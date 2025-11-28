#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日常运维页面：
所有功能均通过SSH连接远程服务器获取数据，不依赖后端API接口。

- 系统设置（TAB 1）：通过SSH管理服务和定时任务，获取服务器运行状况
- 数据库备份（TAB 2）：通过SSH获取备份文件列表并支持下载
- 日志查看（TAB 3）：通过SSH获取日志文件列表，支持查看和下载
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import re
import subprocess
import os
import sys
import zipfile
import io

# Markdown 渲染库（可选，如果未安装则使用回退实现）
try:
    import markdown
    from markdown.extensions import codehilite, fenced_code, tables, nl2br
    from pygments.formatters import HtmlFormatter
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer_for_filename
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QFrame, QTabWidget, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QLineEdit, QCheckBox, QTextEdit, QPlainTextEdit,
    QSplitter, QComboBox, QProgressDialog, QDialog, QListWidget,
    QListWidgetItem
)
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QTimer, QProcess

from utils.config_manager import ConfigManager
from utils.ssh_client import SSHClient
from utils.theme_manager import ThemeManager
from widgets.toast import Toast
from utils.api_client import AdminApiClient
from utils.version_manager import VersionManager
import httpx
import webbrowser


class _CronJobListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _CronJobListWorker(QRunnable):
    """后台线程：通过SSH获取定时任务列表"""
    def __init__(self, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _CronJobListWorkerSignals()
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit("请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            items = []
            
            # 定义定时任务列表（全部使用 systemd timer）
            cron_jobs = [
                {
                    "name": "ai-perf-daily.timer",
                    "display_name": "每日流水线",
                    "type": "systemd_timer",
                    "schedule": "每天 02:30",
                    "description": "每天凌晨2:30执行前一天的流水线任务",
                },
                {
                    "name": "ai-perf-health-check.timer",
                    "display_name": "健康检查",
                    "type": "systemd_timer",
                    "schedule": "每天 09:00",
                    "description": "每天上午9:00执行健康检查",
                },
                {
                    "name": "ai-perf-backup.timer",
                    "display_name": "数据库备份",
                    "type": "systemd_timer",
                    "schedule": "每天 06:00",
                    "description": "每天凌晨6:00自动备份数据库",
                },
            ]
            
            for job in cron_jobs:
                # 所有任务都使用 systemd timer
                timer_status = self._get_timer_status_via_ssh(ssh, job["name"])
                items.append({
                    "name": job["name"],
                    "display_name": job["display_name"],
                    "type": job["type"],
                    "schedule": job["schedule"],
                    "last_run": timer_status.get("last_run"),
                    "next_run": timer_status.get("next_run"),
                    "enabled": timer_status.get("enabled", False),
                    "description": job["description"],
                })
            
            ssh.close()
            self.signals.finished.emit(items)
        except Exception as e:
            self.signals.error.emit(f"获取定时任务列表失败：{e}")
    
    def _get_timer_status_via_ssh(self, ssh: SSHClient, timer_name: str) -> Dict[str, Any]:
        """通过SSH获取systemd timer状态"""
        try:
            # 先检查timer是否启用
            enabled_result = ssh.execute(f"systemctl is-enabled {timer_name}", sudo=False)
            is_enabled = enabled_result["success"] and enabled_result.get("stdout", "").strip() in ("enabled", "enabled-runtime")
            
            # 执行 systemctl list-timers 命令（使用 --all 显示所有timer，包括未激活的）
            result = ssh.execute(f"systemctl list-timers {timer_name} --no-pager --all", sudo=False)
            
            if not result["success"]:
                return {"enabled": is_enabled, "last_run": None, "next_run": None}
            
            output = result.get("stdout", "").strip()
            if not output:
                return {"enabled": is_enabled, "last_run": None, "next_run": None}
            
            lines = output.split("\n")
            
            # 解析输出（跳过标题行，通常是前两行）
            # systemctl list-timers 输出格式：
            # NEXT                         LEFT          LAST                         PASSED       UNIT                         ACTIVATES
            # Mon 2025-01-13 09:00:00 CST  23h left      Sun 2025-01-12 09:00:00 CST  1h 30min ago ai-perf-health-check.timer ai-perf-health-check.service
            if len(lines) > 2:
                # 查找timer行（从第3行开始，跳过标题）
                for line in lines[2:]:
                    if timer_name in line:
                        # 按空格分割，但日期时间可能包含空格，需要更智能的解析
                            # 使用正则表达式或更精确的解析
                        parts = line.split()
                        if len(parts) >= 4:
                            # 第一列是 NEXT，第二列是 LEFT，第三列是 LAST，第四列是 PASSED
                            # 但日期时间格式可能是 "Mon 2025-01-13 09:00:00 CST"，需要合并
                            # 简单方法：查找日期模式
                            # 匹配日期时间格式：Mon 2025-01-13 09:00:00 CST
                            date_pattern = r'\w{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w{3}'
                            dates = re.findall(date_pattern, line)
                            
                            if len(dates) >= 2:
                                # 第一个是 next_run，第二个是 last_run
                                next_run = dates[0] if dates[0] != "n/a" else None
                                last_run = dates[1] if len(dates) > 1 and dates[1] != "n/a" else None
                                return {
                                    "enabled": is_enabled,
                                    "last_run": last_run,
                                    "next_run": next_run,
                                }
                            elif len(dates) == 1:
                                # 只有一个日期，可能是 next_run（如果 last_run 是 n/a）
                                if "n/a" in line.lower():
                                    # 检查哪个是 n/a
                                    if line.find("n/a") < line.find(dates[0]):
                                        # n/a 在日期之前，说明 last_run 是 n/a，dates[0] 是 next_run
                                        return {
                                            "enabled": is_enabled,
                                            "last_run": None,
                                            "next_run": dates[0],
                                        }
                                    else:
                                        # n/a 在日期之后，说明 next_run 是 n/a，dates[0] 是 last_run
                                        return {
                                            "enabled": is_enabled,
                                            "last_run": dates[0],
                                            "next_run": None,
                                        }
            
            return {"enabled": is_enabled, "last_run": None, "next_run": None}
        except Exception as e:
            return {"enabled": False, "last_run": None, "next_run": None}


class _CronJobControlWorkerSignals(QObject):
    finished = Signal(str)  # 任务名称
    error = Signal(str, str)  # 任务名称, 错误消息


class _CronJobControlWorker(QRunnable):
    """后台线程：通过SSH控制定时任务"""
    def __init__(self, job_name: str, action: str, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _CronJobControlWorkerSignals()
        self._job_name = job_name
        self._action = action
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit(self._job_name, "请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit(self._job_name, "SSH连接失败，请检查配置")
                return
            
            # 执行systemctl命令
            result = ssh.execute(f"systemctl {self._action} {self._job_name}", sudo=True)
            ssh.close()
            
            if result["success"]:
                self.signals.finished.emit(self._job_name)
            else:
                error_msg = result.get("stderr") or result.get("error") or "操作失败"
                self.signals.error.emit(self._job_name, error_msg)
        except Exception as e:
            self.signals.error.emit(self._job_name, f"操作失败：{e}")


class _BackupListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _BackupListWorker(QRunnable):
    """后台线程：通过SSH获取备份列表"""
    def __init__(self, ssh_config: Dict[str, Any], backup_dir: str = "/ai-perf/backups"):
        super().__init__()
        self.signals = _BackupListWorkerSignals()
        self._ssh_config = ssh_config
        self._backup_dir = backup_dir

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit("请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            # 执行 ls -lh 命令获取备份文件列表
            # 格式：-rw-r--r-- 1 root root 1234567 Jan 13 09:00 ai_perf_backup_20250113_090000.sql
            result = ssh.execute(f"ls -lh {self._backup_dir}/*.sql 2>/dev/null | awk '{{print $9, $5, $6, $7, $8}}'", sudo=False)
            
            items = []
            if result["success"]:
                output = result.get("stdout", "").strip()
                if output:
                    for line in output.split("\n"):
                        if line.strip():
                            parts = line.strip().split()
                            if len(parts) >= 5:
                                # 提取文件路径、大小、日期时间
                                filepath = parts[0]
                                filename = Path(filepath).name
                                size_str = parts[1]  # 如 "1.2M" 或 "1234K"
                                date_parts = parts[2:5]  # 月 日 时间 或 月 日 年
                                
                                # 解析文件大小（转换为MB）
                                size_mb = self._parse_size_to_mb(size_str)
                                
                                # 解析日期时间
                                created_at = " ".join(date_parts)
                                
                                items.append({
                                    "filename": filename,
                                    "size_mb": size_mb,
                                    "created_at": created_at,
                                })
            
            # 按文件名排序（最新的在前）
            items.sort(key=lambda x: x["filename"], reverse=True)
            
            ssh.close()
            self.signals.finished.emit(items)
        except Exception as e:
            self.signals.error.emit(f"获取备份列表失败：{e}")
    
    def _parse_size_to_mb(self, size_str: str) -> float:
        """解析大小字符串（如 '1.2M', '1234K'）转换为MB"""
        try:
            size_str = size_str.upper().strip()
            if size_str.endswith("M"):
                return float(size_str[:-1])
            elif size_str.endswith("K"):
                return float(size_str[:-1]) / 1024
            elif size_str.endswith("G"):
                return float(size_str[:-1]) * 1024
            else:
                # 假设是字节
                return float(size_str) / (1024 * 1024)
        except (ValueError, AttributeError):
            return 0.0


class _DownloadBackupWorkerSignals(QObject):
    finished = Signal(str)  # 保存路径
    error = Signal(str)


class _DownloadBackupWorker(QRunnable):
    """后台线程：通过SSH下载备份文件"""
    def __init__(self, filename: str, save_path: str, ssh_config: Dict[str, Any], backup_dir: str = "/ai-perf/backups"):
        super().__init__()
        self.signals = _DownloadBackupWorkerSignals()
        self._filename = filename
        self._save_path = save_path
        self._ssh_config = ssh_config
        self._backup_dir = backup_dir

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit("请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            # 通过SSH读取文件内容
            remote_path = f"{self._backup_dir}/{self._filename}"
            result = ssh.execute(f"cat {remote_path}", sudo=False)
            
            if not result["success"]:
                error_msg = result.get("stderr") or result.get("error") or "读取文件失败"
                ssh.close()
                self.signals.error.emit(error_msg)
                return
            
            # 保存到本地文件
            file_content = result.get("stdout", "")
            if isinstance(file_content, bytes):
                file_content = file_content.decode("utf-8")
            
            with open(self._save_path, "w", encoding="utf-8") as f:
                f.write(file_content)
            
            ssh.close()
            self.signals.finished.emit(self._save_path)
        except Exception as e:
            self.signals.error.emit(f"下载备份文件失败：{e}")


class DatabaseBackupTab(QWidget):
    """数据库备份 TAB"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)  # 完全对齐操作日志页面的TAB内容区域
        layout.setSpacing(6)  # 与操作日志的TAB内容对齐

        # 标题和刷新按钮（第一行，与操作日志的筛选行对齐）
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)  # 与操作日志的筛选行间距对齐
        
        title = QLabel("数据库备份")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # 上次刷新时间标签
        self.last_refresh_label = QLabel("数据刷新于: --")
        self.last_refresh_label.setFont(QFont("Arial", 9))
        # 使用主题颜色，暗色模式下使用更亮的灰色
        from utils.theme_manager import ThemeManager
        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load()
        theme_pref = cfg.get("theme", "auto")
        if theme_pref == "auto":
            current_theme = ThemeManager.detect_system_theme()
        else:
            current_theme = theme_pref
        
        if current_theme == "dark":
            self.last_refresh_label.setStyleSheet("color: #9AA0A6;")  # 暗色模式下的灰色
        else:
            self.last_refresh_label.setStyleSheet("color: #666;")  # 亮色模式下的灰色
        header_layout.addWidget(self.last_refresh_label)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(100)
        refresh_btn.setFixedHeight(28)  # 与操作日志的按钮高度对齐
        refresh_btn.clicked.connect(self.reload_from_api)
        self.refresh_btn = refresh_btn  # 保存引用以便控制状态
        header_layout.addWidget(refresh_btn)
        
        layout.addLayout(header_layout)

        # 说明文字（第二行，与操作日志的筛选行对齐）
        info_label = QLabel("备份文件自动保留最近15天，每天凌晨6点自动备份。")
        info_label.setStyleSheet("color: #666; font-size: 10pt;")
        layout.addWidget(info_label)

        # 备份列表表格
        self.backup_table = QTableWidget()
        self.backup_table.setColumnCount(4)
        self.backup_table.setHorizontalHeaderLabels(["文件名", "大小", "创建时间", "操作"])
        self.backup_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.backup_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.backup_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.backup_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.backup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backup_table.verticalHeader().setVisible(False)
        
        # 设置表格大小策略，让它填充可用空间
        from PySide6.QtWidgets import QSizePolicy
        self.backup_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        layout.addWidget(self.backup_table, 1)  # 设置stretch factor为1，让表格填充剩余空间

    def reload_from_api(self):
        """通过SSH重新加载备份列表"""
        # 如果正在刷新，不重复执行
        if hasattr(self, 'refresh_btn') and not self.refresh_btn.isEnabled():
            return
        
        # 更新刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("正在刷新...")
            self.refresh_btn.setEnabled(False)
        
        # 刷新时间将在加载完成时更新，不在这里更新
        
        # 获取SSH配置（备份功能已改为通过SSH）
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            Toast.show_message(self, "请先配置SSH服务器信息")
            return
        
        # 显示加载状态
        self.backup_table.setRowCount(0)
        loading_item = QTableWidgetItem("加载中...")
        loading_item.setFlags(Qt.NoItemFlags)
        self.backup_table.setRowCount(1)
        self.backup_table.setItem(0, 0, loading_item)
        
        # 后台加载（通过SSH）
        backup_dir = "/ai-perf/backups"  # 默认备份目录
        worker = _BackupListWorker(ssh_config, backup_dir)
        worker.signals.finished.connect(self._on_backup_list_loaded)
        worker.signals.error.connect(self._on_backup_list_error)
        QThreadPool.globalInstance().start(worker)

    def _on_backup_list_loaded(self, items: List[Dict[str, Any]]):
        """备份列表加载完成"""
        # 恢复刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("刷新")
            self.refresh_btn.setEnabled(True)
        
        # 更新刷新时间（刷新完成时）
        from datetime import datetime
        refresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, 'last_refresh_label'):
            self.last_refresh_label.setText(f"数据刷新于: {refresh_time}")
        
        self.backup_table.setRowCount(len(items))
        
        for row, item in enumerate(items):
            # 文件名
            filename_item = QTableWidgetItem(item.get("filename", ""))
            self.backup_table.setItem(row, 0, filename_item)
            
            # 大小
            size_mb = item.get("size_mb", 0)
            size_text = f"{size_mb:.2f} MB"
            size_item = QTableWidgetItem(size_text)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.backup_table.setItem(row, 1, size_item)
            
            # 创建时间
            created_at = item.get("created_at", "")
            try:
                # 解析 ISO 格式时间
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                time_text = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_text = created_at
            time_item = QTableWidgetItem(time_text)
            self.backup_table.setItem(row, 2, time_item)
            
            # 操作按钮
            download_btn = QPushButton("下载")
            download_btn.setFixedSize(80, 28)
            download_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4a90e2;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 10pt;
                }
                QPushButton:hover {
                    background-color: #357abd;
                }
            """)
            filename = item.get("filename", "")
            download_btn.clicked.connect(lambda checked, f=filename: self._on_download_clicked(f))
            self.backup_table.setCellWidget(row, 3, download_btn)

    def _on_backup_list_error(self, error_msg: str):
        """备份列表加载失败"""
        # 恢复刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("刷新")
            self.refresh_btn.setEnabled(True)
        
        self.backup_table.setRowCount(0)
        Toast.show_message(self, f"获取备份列表失败：{error_msg}")

    def _on_download_clicked(self, filename: str):
        """下载按钮点击事件"""
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            Toast.show_message(self, "请先配置SSH服务器信息")
            return
        
        # 选择保存路径
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存备份文件",
            filename,
            "SQL Files (*.sql);;All Files (*)"
        )
        
        if not save_path:
            return  # 用户取消
        
        # 显示加载提示
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在下载 {filename}...")
        
        # 后台下载（通过SSH）
        backup_dir = "/ai-perf/backups"  # 默认备份目录
        worker = _DownloadBackupWorker(filename, save_path, ssh_config, backup_dir)
        worker.signals.finished.connect(self._on_download_finished)
        worker.signals.error.connect(self._on_download_error)
        QThreadPool.globalInstance().start(worker)

    def _on_download_finished(self, save_path: str):
        """下载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        QMessageBox.information(
            self,
            "下载完成",
            f"备份文件已保存到：\n{save_path}"
        )

    def _on_download_error(self, error_msg: str):
        """下载失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"下载失败：{error_msg}")


class _ServerInfoWorkerSignals(QObject):
    finished = Signal(dict)  # {"cpu": {...}, "memory": {...}, "disk": {...}}
    error = Signal(str)


class _ServerInfoWorker(QRunnable):
    """后台线程：通过SSH获取服务器基本信息"""
    def __init__(self, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _ServerInfoWorkerSignals()
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit("请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            result = {}
            
            # 获取CPU使用率（top命令，取1秒平均值）
            cpu_result = ssh.execute("top -bn1 | grep 'Cpu(s)' | sed 's/.*, *\\([0-9.]*\\)%* id.*/\\1/' | awk '{print 100 - $1}'", sudo=False)
            if cpu_result["success"]:
                try:
                    cpu_usage = float(cpu_result["stdout"].strip())
                    result["cpu"] = {"usage": cpu_usage}
                except (ValueError, AttributeError):
                    result["cpu"] = {"usage": None}
            else:
                result["cpu"] = {"usage": None}
            
            # 获取内存使用率
            mem_result = ssh.execute("free | grep Mem | awk '{printf \"%.1f\", ($3/$2) * 100.0}'", sudo=False)
            if mem_result["success"]:
                try:
                    mem_usage = float(mem_result["stdout"].strip())
                    result["memory"] = {"usage": mem_usage}
                except (ValueError, AttributeError):
                    result["memory"] = {"usage": None}
            else:
                result["memory"] = {"usage": None}
            
            # 获取磁盘使用率（根分区）
            disk_result = ssh.execute("df -h / | tail -1 | awk '{print $5}' | sed 's/%//'", sudo=False)
            if disk_result["success"]:
                try:
                    disk_usage = float(disk_result["stdout"].strip())
                    result["disk"] = {"usage": disk_usage}
                except (ValueError, AttributeError):
                    result["disk"] = {"usage": None}
            else:
                result["disk"] = {"usage": None}
            
            # 获取CPU占用排行前三的进程
            cpu_top_result = ssh.execute("ps aux --sort=-%cpu | head -4 | tail -3 | awk '{cpu=$3; $1=$2=$3=$4=$5=$6=$7=$8=$9=$10=$11=\"\"; gsub(/^[ ]+/, \"\"); print $0, cpu}'", sudo=False)
            cpu_top_processes = []
            if cpu_top_result["success"]:
                for line in cpu_top_result["stdout"].strip().split('\n'):
                    if line.strip():
                        parts = line.strip().rsplit(' ', 1)  # 从右边分割，最后一个是CPU使用率
                        if len(parts) >= 2:
                            process_name = parts[0].strip()
                            cpu_percent = parts[-1]
                            try:
                                cpu_top_processes.append({"name": process_name, "usage": float(cpu_percent)})
                            except ValueError:
                                pass
            result["cpu"]["top_processes"] = cpu_top_processes
            
            # 获取内存占用排行前三的进程
            mem_top_result = ssh.execute("ps aux --sort=-%mem | head -4 | tail -3 | awk '{mem=$4; $1=$2=$3=$4=$5=$6=$7=$8=$9=$10=$11=\"\"; gsub(/^[ ]+/, \"\"); print $0, mem}'", sudo=False)
            mem_top_processes = []
            if mem_top_result["success"]:
                for line in mem_top_result["stdout"].strip().split('\n'):
                    if line.strip():
                        parts = line.strip().rsplit(' ', 1)  # 从右边分割，最后一个是内存使用率
                        if len(parts) >= 2:
                            process_name = parts[0].strip()
                            mem_percent = parts[-1]
                            try:
                                mem_top_processes.append({"name": process_name, "usage": float(mem_percent)})
                            except ValueError:
                                pass
            result["memory"]["top_processes"] = mem_top_processes
            
            # 获取磁盘占用排行前三的目录
            disk_top_result = ssh.execute("du -h / 2>/dev/null | sort -rh | head -4 | tail -3 | awk '{print $2, $1}'", sudo=True)
            disk_top_dirs = []
            if disk_top_result["success"]:
                for line in disk_top_result["stdout"].strip().split('\n'):
                    if line.strip():
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            dir_path = parts[0]
                            dir_size = parts[-1]
                            disk_top_dirs.append({"name": dir_path, "size": dir_size})
            result["disk"]["top_dirs"] = disk_top_dirs
            
            # 获取系统负载（load average）
            load_result = ssh.execute("uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//'", sudo=False)
            if load_result["success"]:
                try:
                    load_avg = float(load_result["stdout"].strip())
                    result["load"] = {"average": load_avg}
                except (ValueError, AttributeError):
                    result["load"] = {"average": None}
            else:
                result["load"] = {"average": None}
            
            # 获取系统运行时间
            uptime_result = ssh.execute("uptime -p 2>/dev/null || uptime | awk -F'up' '{print $2}' | awk '{print $1,$2,$3}'", sudo=False)
            if uptime_result["success"]:
                uptime_str = uptime_result["stdout"].strip()
                if uptime_str:
                    result["uptime"] = uptime_str
                else:
                    result["uptime"] = None
            else:
                result["uptime"] = None
            
            ssh.close()
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(f"获取服务器信息失败：{e}")


class _ServiceListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _ServiceListWorker(QRunnable):
    """后台线程：通过SSH获取服务列表"""
    def __init__(self, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _ServiceListWorkerSignals()
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit("请先配置SSH服务器信息")
            return
        
        try:
            # 定义服务列表（不包含定时任务：每日流水线和健康检查已移到定时任务TAB）
            services = [
                {"name": "ai-perf-api", "display_name": "员工端API服务", "description": "员工端API服务器（端口8000）"},
                {"name": "ai-perf-admin-api", "display_name": "管理端API服务", "description": "管理端API服务器（端口8880）"},
                {"name": "ai-perf-upload", "display_name": "文件上传服务", "description": "文件上传API服务器（端口8882）"},
            ]
            
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            items = []
            for svc in services:
                service_name = svc["name"]
                
                # 检查服务状态
                result = ssh.execute(f"systemctl is-active {service_name}", sudo=True)
                is_active = result["success"] and result["stdout"].strip() == "active"
                
                result = ssh.execute(f"systemctl is-enabled {service_name}", sudo=True)
                is_enabled = result["success"] and result["stdout"].strip() in ("enabled", "enabled-runtime")
                
                status = "running" if is_active else "stopped"
                
                # 获取真实运行端口、CPU和内存使用情况
                actual_port = None
                cpu_usage = None
                memory_usage = None
                if is_active:
                    # 获取服务的主进程PID
                    result = ssh.execute(f"systemctl show -p MainPID {service_name}", sudo=True)
                    if result["success"]:
                        main_pid = result["stdout"].strip()
                        if main_pid.startswith("MainPID="):
                            main_pid = main_pid.split("=", 1)[1]
                            if main_pid and main_pid.isdigit() and main_pid != "0":
                                # 使用ss命令查看该进程监听的端口（更现代，比netstat快）
                                result = ssh.execute(f"ss -tlnp 2>/dev/null | grep 'pid={main_pid}' | head -1", sudo=True)
                                if result["success"] and result["stdout"].strip():
                                    # 从ss输出中提取端口号，格式如: LISTEN 0 128 *:8880 *:*
                                    port_match = re.search(r':(\d{4,5})\s', result["stdout"])
                                    if port_match:
                                        actual_port = port_match.group(1)
                                
                                # 获取进程的CPU和内存使用情况
                                # 使用ps命令获取CPU和内存百分比
                                result = ssh.execute(f"ps -p {main_pid} -o %cpu,%mem --no-headers 2>/dev/null", sudo=True)
                                if result["success"] and result["stdout"].strip():
                                    parts = result["stdout"].strip().split()
                                    if len(parts) >= 2:
                                        try:
                                            cpu_usage = float(parts[0].strip())
                                            memory_usage = float(parts[1].strip())
                                        except (ValueError, IndexError):
                                            pass
                                
                                # 如果ps命令失败，尝试使用top命令（需要更复杂的解析）
                                if cpu_usage is None or memory_usage is None:
                                    # 使用top命令获取（但top是交互式的，改用ps aux）
                                    result = ssh.execute(f"ps aux | grep '^[^ ]*[ ]*{main_pid}[ ]' | grep -v grep | head -1", sudo=True)
                                    if result["success"] and result["stdout"].strip():
                                        parts = result["stdout"].strip().split()
                                        if len(parts) >= 11:
                                            try:
                                                cpu_usage = float(parts[2].strip())
                                                memory_usage = float(parts[3].strip())
                                            except (ValueError, IndexError):
                                                pass
                
                # 从原始description中提取服务类型（移除端口信息）
                base_desc = svc["description"].split("（")[0] if "（" in svc["description"] else svc["description"]
                
                items.append({
                    "name": service_name,
                    "display_name": svc["display_name"],
                    "status": status,
                    "enabled": is_enabled,
                    "port": actual_port,  # 端口单独作为字段
                    "cpu": cpu_usage,  # CPU使用率（百分比）
                    "memory": memory_usage,  # 内存使用率（百分比）
                    "description": base_desc,  # description不包含端口信息
                })
            
            ssh.close()
            self.signals.finished.emit(items)
        except Exception as e:
            self.signals.error.emit(f"获取服务列表失败：{e}")


class _ServiceControlWorkerSignals(QObject):
    finished = Signal(str)  # 服务名称
    error = Signal(str, str)  # 服务名称, 错误消息


class _ServiceControlWorker(QRunnable):
    """后台线程：通过SSH控制服务"""
    def __init__(self, service_name: str, action: str, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _ServiceControlWorkerSignals()
        self._service_name = service_name
        self._action = action
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit(self._service_name, "请先配置SSH服务器信息")
            return
        
        try:
            # 创建SSH客户端
            ssh = SSHClient(
                host=self._ssh_config["host"],
                port=self._ssh_config.get("port", 22),
                username=self._ssh_config["username"],
                password=self._ssh_config.get("password"),
                key_path=self._ssh_config.get("key_path")
            )
            
            if not ssh.connect():
                self.signals.error.emit(self._service_name, "SSH连接失败，请检查配置")
                return
            
            # 执行systemctl命令
            result = ssh.execute(f"systemctl {self._action} {self._service_name}", sudo=True)
            ssh.close()
            
            if result["success"]:
                self.signals.finished.emit(self._service_name)
            else:
                error_msg = result.get("stderr") or result.get("error") or "操作失败"
                self.signals.error.emit(self._service_name, error_msg)
        except Exception as e:
            self.signals.error.emit(self._service_name, f"操作失败：{e}")


class SystemSettingsTab(QWidget):
    """系统设置 TAB"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._service_buttons = {}  # 存储服务按钮引用
        self._auto_refresh_timer = None  # 自动刷新定时器（10秒触发一次）
        self._countdown_timer = None  # 倒计时定时器（每秒更新一次）
        self._countdown_seconds = 10  # 倒计时秒数
        self._is_refreshing = False  # 是否正在刷新
        self._auto_refresh_enabled = False  # 是否启用自动刷新
        self._service_list_loaded = False  # 服务列表是否已加载
        self._cron_job_list_loaded = False  # 定时任务列表是否已加载
        self._init_ui()
        # 不再自动加载，由MaintenanceView在首次切换到该TAB时加载

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)  # 完全对齐操作日志页面的TAB内容区域
        layout.setSpacing(4)  # 减少间距，让表格更靠近SSH配置区域
        
        # 设置TAB内容区域的大小策略，让它填充可用空间
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 标题和刷新按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)
        
        title = QLabel("系统概览")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # 上次刷新时间标签
        self.last_refresh_label = QLabel("数据刷新于: --")
        self.last_refresh_label.setFont(QFont("Arial", 9))
        # 使用主题颜色，暗色模式下使用更亮的灰色
        from utils.theme_manager import ThemeManager
        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load()
        theme_pref = cfg.get("theme", "auto")
        if theme_pref == "auto":
            current_theme = ThemeManager.detect_system_theme()
        else:
            current_theme = theme_pref
        
        if current_theme == "dark":
            self.last_refresh_label.setStyleSheet("color: #9AA0A6;")  # 暗色模式下的灰色
        else:
            self.last_refresh_label.setStyleSheet("color: #666;")  # 亮色模式下的灰色
        header_layout.addWidget(self.last_refresh_label)
        
        refresh_btn = QPushButton("手动刷新")
        refresh_btn.setFixedWidth(100)
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self.reload)
        self.refresh_btn = refresh_btn  # 保存引用以便控制状态
        header_layout.addWidget(refresh_btn)
        
        # 自动刷新复选框
        auto_refresh_checkbox = QCheckBox("自动刷新")
        auto_refresh_checkbox.setChecked(False)  # 默认关闭
        # 使用toggled信号更可靠（传递bool值）
        auto_refresh_checkbox.toggled.connect(self._on_auto_refresh_toggled)
        self.auto_refresh_checkbox = auto_refresh_checkbox
        header_layout.addWidget(auto_refresh_checkbox)
        
        layout.addLayout(header_layout)

        # SSH配置区域
        ssh_frame = QFrame()
        ssh_frame.setProperty("class", "card")
        ssh_layout = QVBoxLayout(ssh_frame)
        ssh_layout.setContentsMargins(12, 12, 12, 12)
        ssh_layout.setSpacing(8)
        
        ssh_title = QLabel("SSH服务器配置")
        ssh_title.setFont(QFont("Arial", 12, QFont.Bold))
        ssh_layout.addWidget(ssh_title)
        
        ssh_form_layout = QHBoxLayout()
        
        # 主机（stretch factor = 6）
        host_label = QLabel("主机:")
        self.ssh_host_input = QLineEdit()
        self.ssh_host_input.setPlaceholderText("例如: 192.168.1.100")
        ssh_form_layout.addWidget(host_label)
        ssh_form_layout.addWidget(self.ssh_host_input, 5)
        
        # 端口（stretch factor = 1）
        port_label = QLabel("端口:")
        self.ssh_port_input = QLineEdit()
        self.ssh_port_input.setPlaceholderText("22")
        ssh_form_layout.addWidget(port_label)
        ssh_form_layout.addWidget(self.ssh_port_input, 2)
        
        # 用户名（stretch factor = 2）
        user_label = QLabel("用户名:")
        self.ssh_username_input = QLineEdit()
        self.ssh_username_input.setPlaceholderText("例如: root")
        ssh_form_layout.addWidget(user_label)
        ssh_form_layout.addWidget(self.ssh_username_input, 2)
        
        # 密码（stretch factor = 8，与密钥平分）
        pwd_label = QLabel("密码:")
        self.ssh_password_input = QLineEdit()
        self.ssh_password_input.setPlaceholderText("SSH密码")
        self.ssh_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        ssh_form_layout.addWidget(pwd_label)
        ssh_form_layout.addWidget(self.ssh_password_input, 6)
        
        # 密钥路径（stretch factor = 8，与密码平分）
        key_label = QLabel("密钥路径:")
        self.ssh_key_path_input = QLineEdit()
        self.ssh_key_path_input.setPlaceholderText("可选，SSH密钥文件路径")
        ssh_form_layout.addWidget(key_label)
        ssh_form_layout.addWidget(self.ssh_key_path_input, 10)
        
        # 密钥路径浏览按钮
        browse_key_btn = QPushButton("浏览...")
        browse_key_btn.setFixedWidth(80)
        browse_key_btn.clicked.connect(self._on_browse_key_path)
        ssh_form_layout.addWidget(browse_key_btn)
        
        # 保存按钮
        save_ssh_btn = QPushButton("保存配置")
        save_ssh_btn.setFixedWidth(100)
        save_ssh_btn.clicked.connect(self._save_ssh_config)
        ssh_form_layout.addWidget(save_ssh_btn)
        
        ssh_layout.addLayout(ssh_form_layout)
        layout.addWidget(ssh_frame)
        
        # 加载SSH配置
        self._load_ssh_config()

        # 服务器基本信息区域（上）
        server_info_frame = QFrame()
        server_info_frame.setProperty("class", "card")
        server_info_layout = QVBoxLayout(server_info_frame)
        server_info_layout.setContentsMargins(12, 12, 12, 12)
        server_info_layout.setSpacing(8)
        
        server_info_title = QLabel("服务器运行状况")
        server_info_title.setFont(QFont("Arial", 12, QFont.Bold))
        server_info_layout.addWidget(server_info_title)
        
        # CPU、内存、磁盘、负载、运行时间信息（水平排列）
        server_info_row = QHBoxLayout()
        server_info_row.setSpacing(20)
        
        # CPU使用率
        self.cpu_label = QLabel("CPU: --")
        self.cpu_label.setFont(QFont("Arial", 11))
        server_info_row.addWidget(self.cpu_label)
        
        # 内存使用率
        self.memory_label = QLabel("内存: --")
        self.memory_label.setFont(QFont("Arial", 11))
        server_info_row.addWidget(self.memory_label)
        
        # 磁盘使用率
        self.disk_label = QLabel("磁盘: --")
        self.disk_label.setFont(QFont("Arial", 11))
        server_info_row.addWidget(self.disk_label)
        
        # 系统负载
        self.load_label = QLabel("负载: --")
        self.load_label.setFont(QFont("Arial", 11))
        server_info_row.addWidget(self.load_label)
        
        # 运行时间
        self.uptime_label = QLabel("运行时间: --")
        self.uptime_label.setFont(QFont("Arial", 11))
        server_info_row.addWidget(self.uptime_label)
        
        # Loading状态标签
        self.server_info_loading_label = QLabel("")
        self.server_info_loading_label.setFont(QFont("Arial", 10))
        self.server_info_loading_label.setStyleSheet("color: #666;")
        server_info_row.addWidget(self.server_info_loading_label)
        
        server_info_row.addStretch()
        server_info_layout.addLayout(server_info_row)
        layout.addWidget(server_info_frame)
        
        # 服务列表区域（中）
        services_title = QLabel("服务列表")
        services_title.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(services_title)
        
        # 服务列表表格
        self.service_table = QTableWidget()
        self.service_table.setColumnCount(8)
        self.service_table.setHorizontalHeaderLabels(["服务名称", "状态", "开机自启", "端口", "CPU", "内存", "描述", "操作"])
        self.service_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.service_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.service_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.service_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.service_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.service_table.verticalHeader().setVisible(False)
        self.service_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.service_table.setMinimumHeight(200)
        layout.addWidget(self.service_table, 1)
        
        # 定时任务区域（下）
        cron_jobs_title = QLabel("定时任务")
        cron_jobs_title.setFont(QFont("Arial", 12, QFont.Bold))
        layout.addWidget(cron_jobs_title)
        
        # 定时任务列表表格
        self.cron_jobs_table = QTableWidget()
        self.cron_jobs_table.setColumnCount(6)
        self.cron_jobs_table.setHorizontalHeaderLabels(["任务名称", "类型", "执行计划", "上次执行", "下次执行", "操作"])
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # 执行计划列撑满
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.cron_jobs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cron_jobs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cron_jobs_table.verticalHeader().setVisible(False)
        self.cron_jobs_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.cron_jobs_table.setMinimumHeight(200)
        layout.addWidget(self.cron_jobs_table, 1)
        
    def _on_auto_refresh_toggled(self, checked: bool):
        """自动刷新复选框状态改变（使用toggled信号，传递bool值）"""
        if checked:
            # 开启自动刷新
            self._auto_refresh_enabled = True
            # 禁用手动刷新按钮
            if hasattr(self, 'refresh_btn'):
                self.refresh_btn.setEnabled(False)
            # 开始倒计时（倒计时到0时才刷新）
            self._countdown_seconds = 10
            self._start_countdown()
            # 不立即刷新，等待倒计时到0
        else:
            # 关闭自动刷新
            self._auto_refresh_enabled = False
            # 停止所有定时器
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
            if self._auto_refresh_timer is not None:
                self._auto_refresh_timer.stop()
            # 恢复手动刷新按钮
            if hasattr(self, 'refresh_btn'):
                self.refresh_btn.setText("手动刷新")
                self.refresh_btn.setEnabled(True)
    
    def _start_countdown(self):
        """开始倒计时"""
        if self._countdown_timer is None:
            self._countdown_timer = QTimer()
            self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start(1000)  # 每秒更新一次
        self._update_countdown()  # 立即更新一次
    
    def _update_countdown(self):
        """更新倒计时显示"""
        if not self._auto_refresh_enabled:
            return
        
        if hasattr(self, 'refresh_btn'):
            if self._countdown_seconds > 0:
                self.refresh_btn.setText(f"{self._countdown_seconds}秒后刷新")
                self._countdown_seconds -= 1
            else:
                # 倒计时结束，触发刷新
                self.refresh_btn.setText("0秒后刷新")
                # 停止倒计时定时器
                if self._countdown_timer is not None:
                    self._countdown_timer.stop()
                # 触发刷新
                self._trigger_auto_refresh()
    
    def _trigger_auto_refresh(self):
        """触发自动刷新（仅在未刷新时）"""
        if not self._auto_refresh_enabled:
            return
        
        # 如果正在刷新，不重复触发
        if self._is_refreshing:
            return
        
        # 直接调用reload，让reload方法统一管理_is_refreshing状态
        self.reload()
    
    def reload(self):
        """通过SSH重新加载服务列表和定时任务列表"""
        # 如果正在刷新，不重复执行
        if self._is_refreshing:
            return
        
        # 更新刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            if self._auto_refresh_enabled:
                # 自动刷新时显示"正在刷新..."
                self.refresh_btn.setText("正在刷新...")
            else:
                # 手动刷新时显示"正在刷新..."并禁用按钮
                self.refresh_btn.setText("正在刷新...")
                self.refresh_btn.setEnabled(False)
        
        # 标记为正在刷新，重置加载状态
        self._is_refreshing = True
        self._service_list_loaded = False
        self._cron_job_list_loaded = False
        
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            Toast.show_message(self, "请先配置SSH服务器信息")
            # 恢复按钮状态
            self._is_refreshing = False
            if hasattr(self, 'refresh_btn'):
                if self._auto_refresh_enabled:
                    # 自动刷新时，重新开始倒计时（从10秒开始）
                    self._countdown_seconds = 10
                    self._start_countdown()
                    # 不需要延迟触发，倒计时到0时会自动触发
                else:
                    self.refresh_btn.setText("手动刷新")
                    self.refresh_btn.setEnabled(True)
            return
        
        # 加载服务器基本信息
        self._load_server_info(ssh_config)
        
        # 加载服务列表
        self.service_table.setRowCount(0)
        loading_item = QTableWidgetItem("加载中...")
        loading_item.setFlags(Qt.NoItemFlags)
        self.service_table.setRowCount(1)
        self.service_table.setItem(0, 0, loading_item)
        
        # 后台加载（通过SSH）
        worker = _ServiceListWorker(ssh_config)
        worker.signals.finished.connect(self._on_service_list_loaded)
        worker.signals.error.connect(self._on_service_list_error)
        QThreadPool.globalInstance().start(worker)
        
        # 加载定时任务列表（通过SSH）
        # 获取SSH配置（已在上面获取，这里复用）
        if ssh_config.get("host") and ssh_config.get("username"):
            self.cron_jobs_table.setRowCount(0)
            loading_item2 = QTableWidgetItem("加载中...")
            loading_item2.setFlags(Qt.NoItemFlags)
            self.cron_jobs_table.setRowCount(1)
            self.cron_jobs_table.setItem(0, 0, loading_item2)
            
            worker2 = _CronJobListWorker(ssh_config)
            worker2.signals.finished.connect(self._on_cron_job_list_loaded)
            worker2.signals.error.connect(self._on_cron_job_list_error)
            QThreadPool.globalInstance().start(worker2)
        else:
            self.cron_jobs_table.setRowCount(0)
            error_item = QTableWidgetItem("请先配置SSH服务器信息")
            error_item.setFlags(Qt.NoItemFlags)
            self.cron_jobs_table.setRowCount(1)
            self.cron_jobs_table.setItem(0, 0, error_item)
        
        # 刷新时间将在加载完成时更新，不在这里更新

    def _on_service_list_loaded(self, items: List[Dict[str, Any]]):
        """服务列表加载完成"""
        self._service_list_loaded = True
        self._check_refresh_complete()
        
        self.service_table.setRowCount(len(items))
        self._service_buttons.clear()
        
        for row, item in enumerate(items):
            service_name = item.get("name", "")
            display_name = item.get("display_name", "")
            status = item.get("status", "unknown")
            enabled = item.get("enabled", False)
            port = item.get("port")
            cpu = item.get("cpu")
            memory = item.get("memory")
            description = item.get("description", "")
            
            # 服务名称
            name_item = QTableWidgetItem(display_name)
            self.service_table.setItem(row, 0, name_item)
            
            # 状态
            status_text = {
                "running": "运行中",
                "stopped": "已停止",
                "failed": "失败",
                "unknown": "未知",
            }.get(status, status)
            status_item = QTableWidgetItem(status_text)
            if status == "running":
                status_item.setForeground(QColor("#28a745"))
            elif status == "failed":
                status_item.setForeground(QColor("#dc3545"))
            elif status == "stopped":
                status_item.setForeground(QColor("#6c757d"))
            self.service_table.setItem(row, 1, status_item)
            
            # 开机自启
            enabled_text = "是" if enabled else "否"
            enabled_item = QTableWidgetItem(enabled_text)
            self.service_table.setItem(row, 2, enabled_item)
            
            # 端口
            port_text = str(port) if port else "--"
            port_item = QTableWidgetItem(port_text)
            self.service_table.setItem(row, 3, port_item)
            
            # CPU使用率
            if cpu is not None:
                cpu_text = f"{cpu:.1f}%"
            else:
                cpu_text = "--"
            cpu_item = QTableWidgetItem(cpu_text)
            self.service_table.setItem(row, 4, cpu_item)
            
            # 内存使用率
            if memory is not None:
                memory_text = f"{memory:.1f}%"
            else:
                memory_text = "--"
            memory_item = QTableWidgetItem(memory_text)
            self.service_table.setItem(row, 5, memory_item)
            
            # 描述
            desc_item = QTableWidgetItem(description)
            self.service_table.setItem(row, 6, desc_item)
            
            # 操作按钮
            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(4)
            
            # 根据状态显示不同的按钮
            if status == "running":
                stop_btn = QPushButton("停止")
                stop_btn.setFixedSize(60, 24)
                stop_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #dc3545;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #c82333;
                    }
                """)
                stop_btn.clicked.connect(lambda checked, s=service_name: self._on_control_clicked(s, "stop"))
                btn_layout.addWidget(stop_btn)
                
                restart_btn = QPushButton("重启")
                restart_btn.setFixedSize(60, 24)
                restart_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ffc107;
                        color: #212529;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #e0a800;
                    }
                """)
                restart_btn.clicked.connect(lambda checked, s=service_name: self._on_control_clicked(s, "restart"))
                btn_layout.addWidget(restart_btn)
            else:
                start_btn = QPushButton("启动")
                start_btn.setFixedSize(60, 24)
                start_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #28a745;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #218838;
                    }
                """)
                start_btn.clicked.connect(lambda checked, s=service_name: self._on_control_clicked(s, "start"))
                btn_layout.addWidget(start_btn)
            
            # 开机自启控制按钮
            if enabled:
                disable_btn = QPushButton("禁用自启")
                disable_btn.setFixedSize(70, 24)
                disable_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #6c757d;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #5a6268;
                    }
                """)
                disable_btn.clicked.connect(lambda checked, s=service_name: self._on_control_clicked(s, "disable"))
                btn_layout.addWidget(disable_btn)
            else:
                enable_btn = QPushButton("启用自启")
                enable_btn.setFixedSize(70, 24)
                enable_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #17a2b8;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #138496;
                    }
                """)
                enable_btn.clicked.connect(lambda checked, s=service_name: self._on_control_clicked(s, "enable"))
                btn_layout.addWidget(enable_btn)
            
            btn_layout.addStretch()
            self.service_table.setCellWidget(row, 7, btn_container)
            self._service_buttons[service_name] = btn_container

    def _on_service_list_error(self, error_msg: str):
        """服务列表加载失败"""
        self._service_list_loaded = True  # 即使失败也算加载完成
        self._check_refresh_complete()
        
        self.service_table.setRowCount(0)
        Toast.show_message(self, f"获取服务列表失败：{error_msg}")

    def _on_control_clicked(self, service_name: str, action: str):
        """服务控制按钮点击事件"""
        action_text = {
            "start": "启动",
            "stop": "停止",
            "restart": "重启",
            "enable": "启用自启",
            "disable": "禁用自启",
        }.get(action, action)
        
        # 显示确认对话框
        reply = QMessageBox.question(
            self,
            "确认操作",
            f"确定要{action_text}服务 {service_name} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            Toast.show_message(self, "请先配置SSH服务器信息")
            return
        
        # 显示加载提示
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在{action_text}服务 {service_name}...")
        
        # 后台执行（通过SSH）
        worker = _ServiceControlWorker(service_name, action, ssh_config)
        worker.signals.finished.connect(self._on_control_finished)
        worker.signals.error.connect(self._on_control_error)
        QThreadPool.globalInstance().start(worker)

    def _on_control_finished(self, service_name: str):
        """服务控制完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"服务 {service_name} 操作成功")
        # 重新加载服务列表
        QTimer.singleShot(500, self.reload)  # 延迟500ms，等待服务状态更新

    def _on_control_error(self, service_name: str, error_msg: str):
        """服务控制失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"服务 {service_name} 操作失败：{error_msg}")

    def _on_cron_job_list_loaded(self, items: List[Dict[str, Any]]):
        """定时任务列表加载完成"""
        self._cron_job_list_loaded = True
        self._check_refresh_complete()
        
        self.cron_jobs_table.setRowCount(len(items))
        
        for row, item in enumerate(items):
            job_name = item.get("name", "")
            display_name = item.get("display_name", "")
            job_type = item.get("type", "")
            schedule = item.get("schedule", "")
            last_run = item.get("last_run")
            next_run = item.get("next_run")
            enabled = item.get("enabled", False)
            
            # 任务名称
            name_item = QTableWidgetItem(display_name)
            self.cron_jobs_table.setItem(row, 0, name_item)
            
            # 类型
            type_text = "systemd timer" if job_type == "systemd_timer" else "cron"
            type_item = QTableWidgetItem(type_text)
            self.cron_jobs_table.setItem(row, 1, type_item)
            
            # 执行计划
            schedule_item = QTableWidgetItem(schedule)
            self.cron_jobs_table.setItem(row, 2, schedule_item)
            
            # 上次执行
            last_run_text = last_run if last_run else "--"
            last_run_item = QTableWidgetItem(last_run_text)
            self.cron_jobs_table.setItem(row, 3, last_run_item)
            
            # 下次执行
            next_run_text = next_run if next_run else "--"
            next_run_item = QTableWidgetItem(next_run_text)
            self.cron_jobs_table.setItem(row, 4, next_run_item)
            
            # 操作按钮（仅systemd timer支持控制）
            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(4)
            
            if job_type == "systemd_timer":
                if enabled:
                    disable_btn = QPushButton("禁用")
                    disable_btn.setFixedSize(60, 24)
                    disable_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #dc3545;
                            color: white;
                            border: none;
                            border-radius: 3px;
                            font-size: 9pt;
                        }
                        QPushButton:hover {
                            background-color: #c82333;
                        }
                    """)
                    disable_btn.clicked.connect(lambda checked, j=job_name: self._on_cron_job_control_clicked(j, "disable"))
                    btn_layout.addWidget(disable_btn)
                else:
                    enable_btn = QPushButton("启用")
                    enable_btn.setFixedSize(60, 24)
                    enable_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #28a745;
                            color: white;
                            border: none;
                            border-radius: 3px;
                            font-size: 9pt;
                        }
                        QPushButton:hover {
                            background-color: #218838;
                        }
                    """)
                    enable_btn.clicked.connect(lambda checked, j=job_name: self._on_cron_job_control_clicked(j, "enable"))
                    btn_layout.addWidget(enable_btn)
            else:
                # cron任务不支持控制
                info_label = QLabel("不支持控制")
                info_label.setStyleSheet("color: #999; font-size: 9pt;")
                btn_layout.addWidget(info_label)
            
            btn_layout.addStretch()
            self.cron_jobs_table.setCellWidget(row, 5, btn_container)

    def _on_cron_job_list_error(self, error_msg: str):
        """定时任务列表加载失败"""
        self._cron_job_list_loaded = True  # 即使失败也算加载完成
        self._check_refresh_complete()
        
        self.cron_jobs_table.setRowCount(0)
        Toast.show_message(self, f"获取定时任务列表失败：{error_msg}")
    
    def _check_refresh_complete(self):
        """检查刷新是否完成（服务列表和定时任务列表都加载完成）"""
        if not self._service_list_loaded or not self._cron_job_list_loaded:
            return  # 还没全部完成
        
        # 标记刷新完成
        self._is_refreshing = False
        
        # 更新刷新时间（刷新完成时）
        from datetime import datetime
        refresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, 'last_refresh_label'):
            self.last_refresh_label.setText(f"数据刷新于: {refresh_time}")
        
        # 恢复刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            if self._auto_refresh_enabled:
                # 自动刷新时，重新开始倒计时（从10秒开始）
                self._countdown_seconds = 10
                self._start_countdown()
                # 不需要延迟触发，倒计时到0时会自动触发
            else:
                # 手动刷新时，恢复按钮状态
                self.refresh_btn.setText("手动刷新")
                self.refresh_btn.setEnabled(True)

    def _on_cron_job_control_clicked(self, job_name: str, action: str):
        """定时任务控制按钮点击事件"""
        action_text = {
            "enable": "启用",
            "disable": "禁用",
            "start": "启动",
            "stop": "停止",
        }.get(action, action)
        
        # 显示确认对话框
        reply = QMessageBox.question(
            self,
            "确认操作",
            f"确定要{action_text}定时任务 {job_name} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            Toast.show_message(self, "请先配置SSH服务器信息")
            return
        
        # 显示加载提示
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在{action_text}定时任务 {job_name}...")
        
        # 后台执行（通过SSH）
        worker = _CronJobControlWorker(job_name, action, ssh_config)
        worker.signals.finished.connect(self._on_cron_job_control_finished)
        worker.signals.error.connect(self._on_cron_job_control_error)
        QThreadPool.globalInstance().start(worker)

    def _on_cron_job_control_finished(self, job_name: str):
        """定时任务控制完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"定时任务 {job_name} 操作成功")
        # 重新加载定时任务列表
        QTimer.singleShot(500, self.reload)  # 延迟500ms，等待状态更新

    def _on_cron_job_control_error(self, job_name: str, error_msg: str):
        """定时任务控制失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"定时任务 {job_name} 操作失败：{error_msg}")

    def _load_server_info(self, ssh_config: Dict[str, Any]):
        """加载服务器基本信息"""
        # 显示loading状态
        self.server_info_loading_label.setText("加载中...")
        self.cpu_label.setText("CPU: --")
        self.memory_label.setText("内存: --")
        self.disk_label.setText("磁盘: --")
        self.load_label.setText("负载: --")
        self.uptime_label.setText("运行时间: --")
        
        worker = _ServerInfoWorker(ssh_config)
        worker.signals.finished.connect(self._on_server_info_loaded)
        worker.signals.error.connect(self._on_server_info_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_server_info_loaded(self, info: Dict[str, Any]):
        """服务器信息加载完成"""
        # 清除loading状态
        self.server_info_loading_label.setText("")
        
        # 更新CPU显示
        cpu_info = info.get("cpu", {})
        cpu_usage = cpu_info.get("usage")
        cpu_top_processes = cpu_info.get("top_processes", [])
        if cpu_usage is not None:
            cpu_text = f"CPU: {cpu_usage:.1f}%"
            if cpu_top_processes:
                tooltip_lines = ["占用排行前三的进程："]
                for i, proc in enumerate(cpu_top_processes[:3], 1):
                    tooltip_lines.append(f"{i}. {proc['name']} - {proc['usage']:.1f}%")
                self.cpu_label.setToolTip("\n".join(tooltip_lines))
            else:
                self.cpu_label.setToolTip("")
            self.cpu_label.setText(cpu_text)
        else:
            self.cpu_label.setText("CPU: --")
            self.cpu_label.setToolTip("")
        
        # 更新内存显示
        memory_info = info.get("memory", {})
        memory_usage = memory_info.get("usage")
        memory_top_processes = memory_info.get("top_processes", [])
        if memory_usage is not None:
            memory_text = f"内存: {memory_usage:.1f}%"
            if memory_top_processes:
                tooltip_lines = ["占用排行前三的进程："]
                for i, proc in enumerate(memory_top_processes[:3], 1):
                    tooltip_lines.append(f"{i}. {proc['name']} - {proc['usage']:.1f}%")
                self.memory_label.setToolTip("\n".join(tooltip_lines))
            else:
                self.memory_label.setToolTip("")
            self.memory_label.setText(memory_text)
        else:
            self.memory_label.setText("内存: --")
            self.memory_label.setToolTip("")
        
        # 更新磁盘显示
        disk_info = info.get("disk", {})
        disk_usage = disk_info.get("usage")
        disk_top_dirs = disk_info.get("top_dirs", [])
        if disk_usage is not None:
            disk_text = f"磁盘: {disk_usage:.1f}%"
            if disk_top_dirs:
                tooltip_lines = ["占用排行前三的目录："]
                for i, dir_info in enumerate(disk_top_dirs[:3], 1):
                    tooltip_lines.append(f"{i}. {dir_info['name']} - {dir_info['size']}")
                self.disk_label.setToolTip("\n".join(tooltip_lines))
            else:
                self.disk_label.setToolTip("")
            self.disk_label.setText(disk_text)
        else:
            self.disk_label.setText("磁盘: --")
            self.disk_label.setToolTip("")
        
        # 更新系统负载显示
        load_info = info.get("load", {})
        load_avg = load_info.get("average")
        if load_avg is not None:
            self.load_label.setText(f"负载: {load_avg:.2f}")
            self.load_label.setToolTip(f"系统1分钟平均负载: {load_avg:.2f}")
        else:
            self.load_label.setText("负载: --")
            self.load_label.setToolTip("")
        
        # 更新运行时间显示
        uptime = info.get("uptime")
        if uptime:
            self.uptime_label.setText(f"运行时间: {uptime}")
            self.uptime_label.setToolTip(f"系统运行时间: {uptime}")
        else:
            self.uptime_label.setText("运行时间: --")
            self.uptime_label.setToolTip("")
    
    def _on_server_info_error(self, error_msg: str):
        """服务器信息加载失败"""
        self.server_info_loading_label.setText("")
        self.cpu_label.setText("CPU: --")
        self.memory_label.setText("内存: --")
        self.disk_label.setText("磁盘: --")
        self.load_label.setText("负载: --")
        self.uptime_label.setText("运行时间: --")
        # 不显示错误提示，避免干扰用户

    def _load_ssh_config(self):
        """加载SSH配置"""
        config = ConfigManager.load()
        self.ssh_host_input.setText(config.get("ssh_host", ""))
        self.ssh_port_input.setText(str(config.get("ssh_port", 22)))
        self.ssh_username_input.setText(config.get("ssh_username", ""))
        self.ssh_password_input.setText(config.get("ssh_password", ""))
        self.ssh_key_path_input.setText(config.get("ssh_key_path", ""))
    
    def _on_browse_key_path(self):
        """浏览密钥文件"""
        # 默认从用户主目录的 .ssh 目录开始
        import os
        home_dir = os.path.expanduser("~")
        ssh_dir = os.path.join(home_dir, ".ssh")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择SSH密钥文件",
            ssh_dir if os.path.exists(ssh_dir) else home_dir,
            "All Files (*);;Key Files (*.pem *.key *.pub)"
        )
        
        if file_path:
            self.ssh_key_path_input.setText(file_path)

    def _save_ssh_config(self):
        """保存SSH配置"""
        config = ConfigManager.load()
        config["ssh_host"] = self.ssh_host_input.text().strip()
        try:
            port = int(self.ssh_port_input.text().strip() or "22")
            config["ssh_port"] = port
        except ValueError:
            Toast.show_message(self, "端口必须是数字")
            return
        
        config["ssh_username"] = self.ssh_username_input.text().strip()
        config["ssh_password"] = self.ssh_password_input.text()
        config["ssh_key_path"] = self.ssh_key_path_input.text().strip()
        
        ConfigManager.save(config)
        Toast.show_message(self, "SSH配置已保存")
        # 保存后重新加载服务器信息
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        if ssh_config.get("host") and ssh_config.get("username"):
            self._load_server_info(ssh_config)


class DeployTab(QWidget):
    """发布 TAB：执行本地 deploy.sh 脚本"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = False
        self._process = None
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 获取项目根目录（deploy.sh所在目录）
        current_file = Path(__file__).resolve()
        # admin_ui_client/windows/maintenance_view.py -> admin_ui_client -> 项目根目录
        project_root = current_file.parent.parent.parent
        deploy_script = project_root / "deploy.sh"
        self._deploy_script_path = str(deploy_script.resolve())
        self._working_dir = project_root
        
        # 头部：显示脚本路径和执行按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        
        # 显示脚本路径
        script_label = QLabel(f"sh路径+执行：{self._deploy_script_path}")
        script_label.setFont(QFont("Arial", 10))
        script_label.setStyleSheet("color: #666; font-family: 'Courier New', monospace;")
        header_layout.addWidget(script_label)
        header_layout.addStretch()
        
        # 执行按钮
        self.execute_btn = QPushButton("执行")
        self.execute_btn.setFixedWidth(100)
        self.execute_btn.setFixedHeight(28)
        self.execute_btn.clicked.connect(self._on_execute_clicked)
        header_layout.addWidget(self.execute_btn)
        
        layout.addLayout(header_layout)
        
        # 输出区域（苹果终端 Basic 默认样式，支持 ANSI 颜色）
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        
        # 设置苹果终端 Basic 主题的字体
        # 优先使用 Menlo，然后是 Monaco，最后是 Courier New
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setFixedPitch(True)
            # 检查字体是否可用
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        if font:
            self.output_text.setFont(font)
        
        # 设置 tab 宽度（4个空格）
        self.output_text.setTabStopDistance(4 * self.output_text.fontMetrics().averageCharWidth())
        
        # 苹果终端 Basic 主题默认样式（完全匹配）
        self.output_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #FFFFFF;
                border: none;
                padding: 10px;
                selection-background-color: #0066CC;
                selection-color: #FFFFFF;
                font-family: "Menlo", "Monaco", "Courier New", monospace;
                font-size: 12pt;
                line-height: 1.2;
            }
        """)
        
        # 默认文本格式（白色）
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("#FFFFFF"))
        if font:
            self._default_format.setFont(font)
        self.output_text.setPlaceholderText("点击\"执行\"按钮开始执行部署脚本...")
        layout.addWidget(self.output_text, 1)  # stretch factor = 1，填充剩余空间
    
    def _on_execute_clicked(self):
        """执行按钮点击事件"""
        if self._is_running:
            # 如果正在执行，显示停止确认
            reply = QMessageBox.question(
                self,
                "确认停止",
                "部署脚本正在执行中，确定要停止吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._stop_execution()
            return
        
        # 检查脚本是否存在
        if not os.path.exists(self._deploy_script_path):
            QMessageBox.warning(
                self,
                "脚本不存在",
                f"部署脚本不存在：\n{self._deploy_script_path}"
            )
            return
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        header_text = f"$ sh {self._deploy_script_path}\n"
        header_text += "=" * 80 + "\n\n"
        self._append_output(header_text)
        
        # 开始执行
        self._is_running = True
        self.execute_btn.setText("停止")
        self.execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        
        # 使用 QProcess 执行脚本
        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(self._working_dir))
        
        # 连接信号
        self._process.readyReadStandardOutput.connect(self._on_ready_read_output)
        self._process.readyReadStandardError.connect(self._on_ready_read_error)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        
        # 设置环境变量（确保脚本可以正常运行）
        env = self._process.processEnvironment()
        # 保留当前环境变量
        self._process.setProcessEnvironment(env)
        
        # 执行脚本
        self._process.start("bash", [self._deploy_script_path])
    
    def _parse_ansi_color(self, code: str) -> QColor:
        """解析 ANSI 颜色代码"""
        # ANSI 颜色映射（使用苹果终端 Basic 主题的颜色）
        ansi_colors = {
            '30': QColor("#000000"),  # Black
            '31': QColor("#CD3131"),  # Red
            '32': QColor("#0DBC79"),  # Green
            '33': QColor("#E5E510"),  # Yellow
            '34': QColor("#2472C8"),  # Blue
            '35': QColor("#BC3FBC"),  # Magenta
            '36': QColor("#11A8CD"),  # Cyan
            '37': QColor("#E5E5E5"),  # White
            '90': QColor("#767676"),  # Bright Black
            '91': QColor("#F14C4C"),  # Bright Red
            '92': QColor("#23D18B"),  # Bright Green
            '93': QColor("#F5F543"),  # Bright Yellow
            '94': QColor("#3B8EEA"),  # Bright Blue
            '95': QColor("#D670D6"),  # Bright Magenta
            '96': QColor("#29B8DB"),  # Bright Cyan
            '97': QColor("#FFFFFF"),  # Bright White
        }
        return ansi_colors.get(code, QColor("#FFFFFF"))
    
    def _append_output_with_ansi(self, text: str):
        """追加输出文本，支持 ANSI 颜色代码"""
        import re
        
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # 当前文本格式
        current_format = QTextCharFormat(self._default_format)
        
        # 解析 ANSI 转义序列
        # 匹配 \033[...m 或 \x1b[...m 格式
        ansi_pattern = re.compile(r'\033\[([0-9;]+)m|\x1b\[([0-9;]+)m')
        
        last_pos = 0
        for match in ansi_pattern.finditer(text):
            # 插入匹配前的文本
            if match.start() > last_pos:
                plain_text = text[last_pos:match.start()]
                cursor.setCharFormat(current_format)
                cursor.insertText(plain_text)
            
            # 解析 ANSI 代码
            code_str = match.group(1) or match.group(2)
            codes = code_str.split(';')
            
            for code in codes:
                code = code.strip()
                if not code:
                    # 重置格式
                    current_format = QTextCharFormat(self._default_format)
                elif code == '0':
                    # 重置所有格式
                    current_format = QTextCharFormat(self._default_format)
                elif code == '1':
                    # 粗体
                    current_format.setFontWeight(QFont.Weight.Bold)
                elif code == '22':
                    # 取消粗体
                    current_format.setFontWeight(QFont.Weight.Normal)
                elif code in ['30', '31', '32', '33', '34', '35', '36', '37',
                             '90', '91', '92', '93', '94', '95', '96', '97']:
                    # 前景色
                    current_format.setForeground(self._parse_ansi_color(code))
                elif code in ['40', '41', '42', '43', '44', '45', '46', '47',
                             '100', '101', '102', '103', '104', '105', '106', '107']:
                    # 背景色（简化处理，只处理基本颜色）
                    bg_code = str(int(code) - 10)  # 转换为前景色代码
                    if bg_code in ['30', '31', '32', '33', '34', '35', '36', '37',
                                  '90', '91', '92', '93', '94', '95', '96', '97']:
                        bg_color = self._parse_ansi_color(bg_code)
                        # 背景色稍微暗一点
                        bg_color = QColor(
                            max(0, bg_color.red() - 50),
                            max(0, bg_color.green() - 50),
                            max(0, bg_color.blue() - 50)
                        )
                        current_format.setBackground(bg_color)
            
            last_pos = match.end()
        
        # 插入剩余的文本
        if last_pos < len(text):
            plain_text = text[last_pos:]
            cursor.setCharFormat(current_format)
            cursor.insertText(plain_text)
        
        # 自动滚动到底部
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _append_output(self, text: str):
        """追加输出文本（用于头部信息等不需要 ANSI 解析的文本）"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.setCharFormat(self._default_format)
        cursor.insertText(text)
        # 自动滚动到底部
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_ready_read_output(self):
        """读取标准输出"""
        if self._process:
            data = self._process.readAllStandardOutput()
            text = bytes(data).decode('utf-8', errors='replace')
            if text:
                self._append_output_with_ansi(text)
    
    def _on_ready_read_error(self):
        """读取标准错误输出"""
        if self._process:
            data = self._process.readAllStandardError()
            text = bytes(data).decode('utf-8', errors='replace')
            if text:
                self._append_output_with_ansi(text)
    
    def _on_process_finished(self, exit_code: int, exit_status: int):
        """进程执行完成"""
        self._is_running = False
        self.execute_btn.setText("执行")
        self.execute_btn.setStyleSheet("")  # 恢复默认样式
        
        self._append_output("\n" + "=" * 80 + "\n")
        if exit_code == 0:
            self._append_output("[完成] 部署脚本执行成功\n")
        else:
            self._append_output(f"[完成] 部署脚本执行完成，退出码: {exit_code}\n")
        
        # 清理进程
        if self._process:
            self._process.deleteLater()
            self._process = None
    
    def _on_process_error(self, error: QProcess.ProcessError):
        """进程执行错误"""
        self._is_running = False
        self.execute_btn.setText("执行")
        self.execute_btn.setStyleSheet("")  # 恢复默认样式
        
        error_msg = {
            QProcess.ProcessError.FailedToStart: "进程启动失败",
            QProcess.ProcessError.Crashed: "进程崩溃",
            QProcess.ProcessError.Timedout: "进程超时",
            QProcess.ProcessError.WriteError: "写入错误",
            QProcess.ProcessError.ReadError: "读取错误",
            QProcess.ProcessError.UnknownError: "未知错误"
        }.get(error, f"进程错误: {error}")
        
        self._append_output(f"\n[错误] {error_msg}\n")
        
        # 清理进程
        if self._process:
            self._process.deleteLater()
            self._process = None
    
    def _stop_execution(self):
        """停止执行"""
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()
            # 等待进程结束，最多等待2秒
            if not self._process.waitForFinished(2000):
                # 如果2秒后还没结束，强制杀死
                self._process.kill()
                self._process.waitForFinished(1000)
        
        self._is_running = False
        self.execute_btn.setText("执行")
        self.execute_btn.setStyleSheet("")  # 恢复默认样式
        self._append_output("\n[已停止] 用户手动停止执行\n")
        
        # 清理进程
        if self._process:
            self._process.deleteLater()
            self._process = None


class ScriptExecutionTab(QWidget):
    """脚本执行 TAB：通过 SSH 在服务器端 screen 中执行脚本"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = False
        self._ssh_client = None
        self._screen_name = "fromAdminClient"
        self._output_timer = None
        self._search_matches = []  # 存储所有匹配项的位置
        self._current_match_index = -1  # 当前匹配项的索引
        self._search_highlight_format = None  # 高亮格式
        self._current_md_path = None  # 当前打开的MD文件路径
        self._user_manual_style_selected = False  # 用户是否手动选择过样式
        self._current_theme = None  # 当前主题（light/dark）
        self._theme_check_timer = None  # 主题检测定时器
        self._init_ui()
        self._load_readme()
        self._init_theme_detection()
        self._init_theme_detection()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 使用 QSplitter 实现左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：README 文档
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)
        
        # 标题和浏览按钮
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)
        
        readme_label = QLabel("脚本使用说明")
        readme_label.setFont(QFont("Arial", 14, QFont.Bold))
        title_layout.addWidget(readme_label)
        title_layout.addStretch()
        
        # 代码高亮样式选择
        style_label = QLabel("代码样式:")
        style_label.setFont(QFont("Arial", 9))
        title_layout.addWidget(style_label)
        
        self.code_style_combo = QComboBox()
        self.code_style_combo.setFixedWidth(120)
        # 添加常用样式
        self.code_style_combo.addItems([
            "friendly", "monokai", "dracula", "nord", "solarized-light",
            "solarized-dark", "github-dark", "pastie", "xcode", "vs",
            "one-dark", "gruvbox-dark", "gruvbox-light", "default"
        ])
        # 根据当前主题设置默认样式（不触发信号）
        self._update_style_by_theme()
        # 连接信号（用户手动切换时触发）
        self.code_style_combo.currentTextChanged.connect(self._on_user_style_changed)
        title_layout.addWidget(self.code_style_combo)
        
        browse_md_btn = QPushButton("浏览MD文件...")
        browse_md_btn.setFixedWidth(120)
        browse_md_btn.clicked.connect(self._on_browse_md_file)
        title_layout.addWidget(browse_md_btn)
        
        left_layout.addLayout(title_layout)
        
        # 当前文件路径显示
        self.md_path_label = QLabel("文件路径: --")
        self.md_path_label.setFont(QFont("Arial", 9))
        self.md_path_label.setStyleSheet("color: #666; font-family: 'Courier New', monospace;")
        self.md_path_label.setWordWrap(True)
        left_layout.addWidget(self.md_path_label)
        
        # 搜索框
        search_layout = QHBoxLayout()
        search_layout.setSpacing(4)
        
        search_label = QLabel("搜索:")
        search_label.setFixedWidth(50)
        search_layout.addWidget(search_label)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键词进行搜索...")
        self.search_input.returnPressed.connect(self._on_search_next)
        search_layout.addWidget(self.search_input, 1)
        
        search_btn = QPushButton("查找")
        search_btn.setFixedWidth(60)
        search_btn.clicked.connect(self._on_search)
        search_layout.addWidget(search_btn)
        
        prev_btn = QPushButton("↑")
        prev_btn.setFixedWidth(40)
        prev_btn.setToolTip("上一个")
        prev_btn.clicked.connect(self._on_search_prev)
        search_layout.addWidget(prev_btn)
        
        next_btn = QPushButton("↓")
        next_btn.setFixedWidth(40)
        next_btn.setToolTip("下一个")
        next_btn.clicked.connect(self._on_search_next)
        search_layout.addWidget(next_btn)
        
        clear_btn = QPushButton("清除")
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(self._on_clear_search)
        search_layout.addWidget(clear_btn)
        
        left_layout.addLayout(search_layout)
        
        self.readme_text = QTextEdit()
        self.readme_text.setReadOnly(True)
        self.readme_text.setFont(QFont("Arial", 10))
        self.readme_text.setStyleSheet("""
            QTextEdit {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        left_layout.addWidget(self.readme_text, 1)
        
        # 初始化搜索高亮格式
        if not hasattr(self, '_search_highlight_format') or self._search_highlight_format is None:
            self._search_highlight_format = QTextCharFormat()
            self._search_highlight_format.setBackground(QColor("#FFFF00"))  # 黄色背景
            self._search_highlight_format.setForeground(QColor("#000000"))  # 黑色文字
        
        splitter.addWidget(left_widget)
        
        # 右侧：脚本执行界面
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)
        
        # 标题
        title_label = QLabel("脚本执行")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        right_layout.addWidget(title_label)
        
        # Script path 输入框和文件选择按钮
        script_layout = QHBoxLayout()
        script_layout.setSpacing(8)
        
        script_label = QLabel("Script path:")
        script_label.setFixedWidth(100)
        script_layout.addWidget(script_label)
        
        self.script_path_input = QLineEdit()
        self.script_path_input.setPlaceholderText("例如: /ai-perf/scripts/run_daily_pipeline.sh")
        script_layout.addWidget(self.script_path_input, 1)
        
        browse_btn = QPushButton("浏览...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._on_browse_script)
        script_layout.addWidget(browse_btn)
        
        right_layout.addLayout(script_layout)
        
        # Parameters 输入框和 Run 按钮
        params_layout = QHBoxLayout()
        params_layout.setSpacing(8)
        
        params_label = QLabel("Parameters:")
        params_label.setFixedWidth(100)
        params_layout.addWidget(params_label)
        
        self.params_input = QLineEdit()
        self.params_input.setPlaceholderText("例如: 2025-11-10 --steps 1,2,3")
        params_layout.addWidget(self.params_input, 1)
        
        # Run 按钮
        self.run_btn = QPushButton("Run")
        self.run_btn.setFixedWidth(100)
        self.run_btn.setFixedHeight(32)
        self.run_btn.clicked.connect(self._on_run_clicked)
        params_layout.addWidget(self.run_btn)
        
        right_layout.addLayout(params_layout)
        
        # 输出区域（苹果终端风格，支持 ANSI 颜色）
        output_label = QLabel("执行输出:")
        output_label.setFont(QFont("Arial", 10, QFont.Bold))
        right_layout.addWidget(output_label)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        
        # 设置苹果终端 Basic 主题的字体
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setFixedPitch(True)
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        if font:
            self.output_text.setFont(font)
        
        # 设置 tab 宽度
        self.output_text.setTabStopDistance(4 * self.output_text.fontMetrics().averageCharWidth())
        
        # 苹果终端 Basic 主题默认样式（完全匹配发布TAB）
        self.output_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #FFFFFF;
                border: none;
                padding: 10px;
                selection-background-color: #0066CC;
                selection-color: #FFFFFF;
                font-family: "Menlo", "Monaco", "Courier New", monospace;
                font-size: 12pt;
                line-height: 1.2;
            }
        """)
        
        # 默认文本格式
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("#FFFFFF"))
        if font:
            self._default_format.setFont(font)
        
        right_layout.addWidget(self.output_text, 1)
        
        splitter.addWidget(right_widget)
        
        # 设置分割比例（左右7:3）
        splitter.setSizes([700, 300])
        
        layout.addWidget(splitter, 1)
    
    def _load_readme(self):
        """加载 scripts/README.md 文档"""
        try:
            current_file = Path(__file__).resolve()
            # admin_ui_client/windows/maintenance_view.py -> admin_ui_client -> 项目根目录
            project_root = current_file.parent.parent.parent
            readme_path = project_root / "scripts" / "README.md"
            
            if readme_path.exists():
                content = readme_path.read_text(encoding='utf-8')
                html_content = self._markdown_to_html(content)
                # 清空内容并设置HTML（确保完全重置）
                self.readme_text.clear()
                self.readme_text.setHtml(html_content)
                # 滚动到顶部
                cursor = self.readme_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self.readme_text.setTextCursor(cursor)
                self._current_md_path = str(readme_path)
                self.md_path_label.setText(f"文件路径: {self._current_md_path}")
            else:
                self.readme_text.clear()
                self.readme_text.setPlainText(f"README.md 文件不存在: {readme_path}")
                self._current_md_path = None
                self.md_path_label.setText("文件路径: --")
        except Exception as e:
            self.readme_text.clear()
            self.readme_text.setPlainText(f"加载 README.md 失败: {e}")
            self._current_md_path = None
            self.md_path_label.setText("文件路径: --")
    
    def _init_theme_detection(self):
        """初始化主题检测，定期检查主题变化"""
        # 检测当前主题
        self._current_theme = ThemeManager.detect_system_theme()
        
        # 创建定时器，每2秒检查一次主题变化
        self._theme_check_timer = QTimer(self)
        self._theme_check_timer.timeout.connect(self._check_theme_change)
        self._theme_check_timer.start(2000)  # 每2秒检查一次
    
    def _check_theme_change(self):
        """检查主题是否变化，如果变化且用户未手动选择样式，则自动更新"""
        if self._user_manual_style_selected:
            # 用户已手动选择样式，不再自动跟随主题
            return
        
        # 检测当前主题
        current_theme = ThemeManager.detect_system_theme()
        
        if current_theme != self._current_theme:
            # 主题已变化
            self._current_theme = current_theme
            # 更新样式（不触发用户手动选择标志）
            self._update_style_by_theme(silent=True)
            # 重新渲染当前文件
            self._reload_current_md()
    
    def _update_style_by_theme(self, silent=False):
        """根据当前主题更新代码样式"""
        current_theme = ThemeManager.detect_system_theme()
        
        if current_theme == "dark":
            default_style = "github-dark"
        else:
            default_style = "default"  # 浅色模式使用 default
        
        # 临时断开信号，避免触发用户手动选择标志
        if silent:
            self.code_style_combo.blockSignals(True)
        
        self.code_style_combo.setCurrentText(default_style)
        
        if silent:
            self.code_style_combo.blockSignals(False)
    
    def _on_user_style_changed(self, style_name: str):
        """用户手动切换样式时调用"""
        # 标记用户已手动选择样式
        self._user_manual_style_selected = True
        # 重新渲染当前文件
        self._reload_current_md()
    
    def _reload_current_md(self):
        """重新加载并渲染当前打开的 MD 文件"""
        if not MARKDOWN_AVAILABLE:
            return
        
        if self._current_md_path and Path(self._current_md_path).exists():
            try:
                # 重新加载当前文件
                content = Path(self._current_md_path).read_text(encoding='utf-8')
                html_content = self._markdown_to_html(content)
                
                # 强制刷新：先设置空内容，再设置新内容，确保样式更新
                self.readme_text.setPlainText("")  # 先设置为纯文本空内容
                self.readme_text.clear()  # 再清空
                # 立即设置新的 HTML 内容
                self.readme_text.setHtml(html_content)
                
                # 强制更新显示
                self.readme_text.update()
                self.readme_text.repaint()
                
                # 滚动到顶部
                cursor = self.readme_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self.readme_text.setTextCursor(cursor)
                self.readme_text.ensureCursorVisible()
            except Exception as e:
                import traceback
                QMessageBox.warning(self, "错误", f"重新渲染失败: {e}\n{traceback.format_exc()}")
    
    def _on_browse_md_file(self):
        """浏览并加载MD文件"""
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        scripts_dir = project_root / "scripts"
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择Markdown文件",
            str(scripts_dir) if scripts_dir.exists() else str(project_root),
            "Markdown Files (*.md);;All Files (*)"
        )
        
        if file_path:
            try:
                # 先清除搜索和高亮
                self._on_clear_search()
                
                # 读取文件内容
                content = Path(file_path).read_text(encoding='utf-8')
                
                # 转换为HTML
                html_content = self._markdown_to_html(content)
                
                # 清空内容并设置HTML（确保完全重置）
                self.readme_text.clear()
                self.readme_text.setHtml(html_content)
                
                # 滚动到顶部
                cursor = self.readme_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self.readme_text.setTextCursor(cursor)
                
                # 更新路径显示
                self._current_md_path = file_path
                self.md_path_label.setText(f"文件路径: {self._current_md_path}")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载文件失败: {e}")
    
    def _on_search(self):
        """执行搜索"""
        search_text = self.search_input.text().strip()
        if not search_text:
            return
        
        # 清除之前的高亮
        self._clear_highlights()
        
        # 获取纯文本内容进行搜索
        cursor = self.readme_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.readme_text.setTextCursor(cursor)
        
        # 查找所有匹配项
        self._search_matches = []
        search_flags = QTextDocument.FindFlag.FindCaseSensitively  # 默认区分大小写
        if search_text.lower() == search_text:  # 如果输入全小写，则不区分大小写
            search_flags = QTextDocument.FindFlags()
        
        # 使用 QTextDocument.find() 方法进行搜索
        document = self.readme_text.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        
        while True:
            # 使用 QTextDocument.find() 方法
            found_cursor = document.find(search_text, cursor, search_flags)
            if found_cursor.isNull():
                break
            
            # 记录匹配位置
            match_cursor = QTextCursor(found_cursor)
            match_cursor.setPosition(found_cursor.selectionStart())
            match_cursor.setPosition(found_cursor.selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
            
            self._search_matches.append({
                'cursor': match_cursor,
                'position': found_cursor.selectionStart()
            })
            
            # 高亮显示
            match_cursor.setCharFormat(self._search_highlight_format)
            
            # 移动到下一个位置继续搜索
            cursor = found_cursor
            cursor.setPosition(found_cursor.selectionEnd())
        
        if self._search_matches:
            self._current_match_index = 0
            self._highlight_current_match()
        else:
            QMessageBox.information(self, "搜索", f"未找到 \"{search_text}\"")
    
    def _on_search_prev(self):
        """查找上一个"""
        if not self._search_matches:
            self._on_search()
            return
        
        if self._current_match_index > 0:
            self._current_match_index -= 1
        else:
            self._current_match_index = len(self._search_matches) - 1  # 循环到最后一个
        
        self._highlight_current_match()
    
    def _on_search_next(self):
        """查找下一个"""
        if not self._search_matches:
            self._on_search()
            return
        
        if self._current_match_index < len(self._search_matches) - 1:
            self._current_match_index += 1
        else:
            self._current_match_index = 0  # 循环到第一个
        
        self._highlight_current_match()
    
    def _highlight_current_match(self):
        """高亮显示当前匹配项"""
        if not self._search_matches or self._current_match_index < 0:
            return
        
        # 先清除之前的高亮（重新应用搜索高亮）
        for i, match in enumerate(self._search_matches):
            cursor = QTextCursor(match['cursor'])
            if i == self._current_match_index:
                # 当前匹配项使用橙色
                current_format = QTextCharFormat()
                current_format.setBackground(QColor("#FFA500"))  # 橙色背景（当前匹配）
                current_format.setForeground(QColor("#000000"))  # 黑色文字
                cursor.setCharFormat(current_format)
            else:
                # 其他匹配项使用黄色
                cursor.setCharFormat(self._search_highlight_format)
        
        # 移动到当前匹配位置并选中
        match = self._search_matches[self._current_match_index]
        cursor = QTextCursor(match['cursor'])
        self.readme_text.setTextCursor(cursor)
        self.readme_text.ensureCursorVisible()
    
    def _clear_highlights(self):
        """清除所有高亮（仅清除搜索高亮，不重新加载内容）"""
        # 清除搜索匹配记录
        self._search_matches = []
        self._current_match_index = -1
    
    def _on_clear_search(self):
        """清除搜索"""
        self.search_input.clear()
        self._clear_highlights()
    
    def _markdown_to_html(self, markdown_text: str) -> str:
        """使用 markdown 库转换 Markdown 为 HTML（如果可用），否则使用回退实现"""
        if MARKDOWN_AVAILABLE:
            return self._markdown_to_html_with_library(markdown_text)
        else:
            return self._markdown_to_html_fallback(markdown_text)
    
    def _markdown_to_html_with_library(self, markdown_text: str) -> str:
        """使用 markdown + Pygments 库转换"""
        # 获取当前选择的样式
        selected_style = getattr(self, 'code_style_combo', None)
        if selected_style:
            style_name = selected_style.currentText()
        else:
            style_name = 'friendly'  # 默认样式
        
        
        # 获取 formatter（使用内联样式，QTextEdit 支持更好）
        try:
            formatter = HtmlFormatter(style=style_name, noclasses=True)
        except Exception:
            try:
                formatter = HtmlFormatter(style='friendly', noclasses=True)
            except Exception:
                formatter = HtmlFormatter(style='default', noclasses=True)
        
        # 手动处理代码块：先提取，转换其他内容，再替换
        import re
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name
        
        # 先提取所有代码块
        code_blocks = []
        def extract_code_block(match):
            lang = match.group(1) or ''
            code = match.group(2).strip()
            idx = len(code_blocks)
            code_blocks.append((lang, code))
            # 使用 HTML 注释作为占位符，markdown 不会处理它
            return f'<!-- CODE_BLOCK_PLACEHOLDER_{idx} -->'
        
        # 在 markdown 转换前提取代码块
        html_with_placeholders = re.sub(r'```(\w+)?\n(.*?)```', extract_code_block, markdown_text, flags=re.DOTALL)
        
        # 配置 markdown 扩展（不使用 fenced_code，因为我们已经提取了代码块）
        md = markdown.Markdown(extensions=[
            'tables',           # 表格支持
            'nl2br',           # 换行转 <br>
        ])
        
        # 转换其他内容
        html = md.convert(html_with_placeholders)
        md.reset()
        
        # 替换占位符为高亮的代码块
        for idx, (lang, code) in enumerate(code_blocks):
            placeholder = f'<!-- CODE_BLOCK_PLACEHOLDER_{idx} -->'
            
            if placeholder not in html:
                continue
            
            if lang:
                try:
                    lexer = get_lexer_by_name(lang, stripall=True)
                    highlighted = highlight(code, lexer, formatter)
                    # 修复：将 background 改为 background-color，QTextEdit 支持更好
                    # QTextEdit 可能不支持 CSS 简写形式 background，需要明确的 background-color
                    highlighted = highlighted.replace('style="background:', 'style="background-color:')
                    highlighted = highlighted.replace('background: ', 'background-color: ')
                    # 添加语言标签
                    lang_label = f'<div class="code-lang-label">{lang.upper()}</div>'
                    replacement = lang_label + highlighted
                    html = html.replace(placeholder, replacement)
                except Exception as e:
                    # 如果高亮失败，使用转义
                    from html import escape
                    escaped_code = escape(code)
                    lang_label = f'<div class="code-lang-label">{lang.upper()}</div>'
                    html = html.replace(placeholder, f'{lang_label}<div style="background: #f8f8f8; border: 1px solid #ddd; border-radius: 4px; padding: 12px; overflow-x: auto; margin: 1em 0;"><pre><code>{escaped_code}</code></pre></div>')
            else:
                # 没有语言标识
                from html import escape
                escaped_code = escape(code)
                html = html.replace(placeholder, f'<div style="background: #f8f8f8; border: 1px solid #ddd; border-radius: 4px; padding: 12px; overflow-x: auto; margin: 1em 0;"><pre><code>{escaped_code}</code></pre></div>')
        
        # 检查是否还有未替换的占位符
        remaining_placeholders = [f'<!-- CODE_BLOCK_PLACEHOLDER_{i} -->' for i in range(len(code_blocks)) if f'<!-- CODE_BLOCK_PLACEHOLDER_{i} -->' in html]
        if remaining_placeholders:
            pass  # 占位符未替换的情况已处理
        
        # 内联样式模式下，不需要额外的 CSS（样式已经内联在 HTML 中）
        # 包装在美观的容器中，并添加自定义样式
        styled_html = f'''
        <style>
        /* 语言标签样式 */
        .code-block-wrapper {{
            position: relative;
            margin: 1em 0;
        }}
        .code-lang-label {{
            background-color: #e8e8e8;
            color: #666;
            font-size: 0.75em;
            padding: 4px 8px;
            border-bottom: 1px solid #ddd;
            border-radius: 4px 4px 0 0;
            font-family: Monaco, Consolas, monospace;
            text-transform: uppercase;
            display: inline-block;
        }}
        .code-lang-label + .highlight {{
            border-top: none;
            border-radius: 0 0 4px 4px;
        }}
        /* 重要：不要覆盖 .highlight 的背景色，让内联样式生效 */
        .highlight {{
            /* 不设置背景色，让内联样式中的 background 生效 */
            border-radius: 4px;
            padding: 12px;
            overflow-x: auto;
            margin: 1em 0;
        }}
        .highlight pre {{
            margin: 0;
            padding: 0;
            /* 不设置背景，让父元素的背景色显示 */
        }}
        /* 行内代码样式 */
        code {{
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: Monaco, Consolas, monospace;
            font-size: 0.9em;
            color: #c7254e;
        }}
        .highlight code {{
            background: transparent !important;
            padding: 0;
            color: inherit;
        }}
        /* 引用块样式 */
        blockquote {{
            margin: 1.2em 0;
            padding: 1em 1.2em;
            border-left: 4px solid #3498db;
            background: linear-gradient(to right, #f0f7ff 0%, #f8f9fa 100%);
            color: #2c3e50;
            border-radius: 0 6px 6px 0;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        blockquote p {{
            margin: 0.4em 0;
            line-height: 1.7;
            font-style: italic;
            color: #34495e;
        }}
        /* 表格样式 */
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 1em 0;
            border: 1px solid #ddd;
        }}
        table th {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            background-color: #f8f9fa;
            font-weight: bold;
            text-align: left;
        }}
        table td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
        }}
        /* 链接样式 */
        a {{
            color: #3498db;
            text-decoration: none;
            border-bottom: 1px solid #3498db;
        }}
        a:hover {{
            color: #2980b9;
            border-bottom-color: #2980b9;
        }}
        </style>
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; 
                    line-height: 1.6; 
                    color: #333; 
                    max-width: 100%; 
                    padding: 20px; 
                    background-color: #ffffff;">
            {html}
        </div>
        '''
        return styled_html
    
    def _markdown_to_html_fallback(self, markdown_text: str) -> str:
        """回退实现：使用正则表达式转换（当 markdown 库不可用时）"""
        import re
        
        html = markdown_text
        
        # 转义HTML特殊字符（在代码块处理之前）
        def escape_html(text):
            return (text.replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;')
                      .replace("'", '&#39;'))
        
        # 代码块（多行，带语言标识和语法高亮）
        def code_block_replacer(match):
            lang = match.group(1) or ''
            code = match.group(2)
            escaped_code = escape_html(code)
            
            # 语言标签显示
            lang_label = ''
            if lang:
                lang_label = f'<div style="background-color: #e8e8e8; color: #666; font-size: 0.75em; padding: 4px 8px; border-bottom: 1px solid #ddd; border-radius: 4px 4px 0 0; font-family: Monaco, Consolas, monospace; text-transform: uppercase;">{lang}</div>'
            
            # 根据语言设置不同的背景色和边框色
            lang_colors = {
                'python': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#3572A5'},
                'javascript': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#F7DF1E'},
                'js': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#F7DF1E'},
                'bash': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#89e051'},
                'sh': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#89e051'},
                'sql': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#336791'},
                'json': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#000000'},
                'yaml': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#cb171e'},
                'yml': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#cb171e'},
                'html': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#e34c26'},
                'css': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#1572b6'},
                'java': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#ed8b00'},
                'cpp': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#00599c'},
                'c': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#a8b9cc'},
                'go': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#00add8'},
                'rust': {'bg': '#f6f8fa', 'border': '#e1e4e8', 'accent': '#000000'},
            }
            
            color_scheme = lang_colors.get(lang.lower(), {'bg': '#f4f4f4', 'border': '#ddd', 'accent': '#666'})
            
            # 根据是否有语言标签设置不同的样式
            if lang_label:
                border_top = "none"
                border_radius = "0 0 4px 4px"
            else:
                border_top = f"1px solid {color_scheme['border']}"
                border_radius = "4px"
            
            return f'{lang_label}<pre style="background-color: {color_scheme["bg"]}; border: 1px solid {color_scheme["border"]}; border-top: {border_top}; border-radius: {border_radius}; padding: 12px; overflow-x: auto; margin: 1em 0;"><code class="language-{lang}" style="font-family: Monaco, Consolas, monospace; font-size: 0.9em; line-height: 1.5; color: #24292e;">{escaped_code}</code></pre>'
        
        html = re.sub(r'```(\w+)?\n(.*?)```', code_block_replacer, html, flags=re.DOTALL)
        
        # 行内代码
        def inline_code_replacer(match):
            code = match.group(1)
            escaped_code = escape_html(code)
            return f'<code style="background-color: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: Monaco, Consolas, monospace; font-size: 0.9em; color: #c7254e;">{escaped_code}</code>'
        html = re.sub(r'`([^`]+)`', inline_code_replacer, html)
        
        # 标题（支持 H1-H6）
        html = re.sub(r'^###### (.*?)$', r'<h6 style="font-size: 1em; margin-top: 1.5em; margin-bottom: 0.5em; font-weight: bold; color: #333;">\1</h6>', html, flags=re.MULTILINE)
        html = re.sub(r'^##### (.*?)$', r'<h5 style="font-size: 1.1em; margin-top: 1.5em; margin-bottom: 0.5em; font-weight: bold; color: #333;">\1</h5>', html, flags=re.MULTILINE)
        html = re.sub(r'^#### (.*?)$', r'<h4 style="font-size: 1.2em; margin-top: 1.5em; margin-bottom: 0.6em; font-weight: bold; color: #333; border-bottom: 1px solid #eee; padding-bottom: 0.3em;">\1</h4>', html, flags=re.MULTILINE)
        html = re.sub(r'^### (.*?)$', r'<h3 style="font-size: 1.3em; margin-top: 1.8em; margin-bottom: 0.8em; font-weight: bold; color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 0.4em;">\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.*?)$', r'<h2 style="font-size: 1.5em; margin-top: 2em; margin-bottom: 1em; font-weight: bold; color: #2c3e50; border-bottom: 2px solid #34495e; padding-bottom: 0.5em;">\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.*?)$', r'<h1 style="font-size: 2em; margin-top: 2em; margin-bottom: 1em; font-weight: bold; color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.5em;">\1</h1>', html, flags=re.MULTILINE)
        
        # 粗体（必须在斜体之前处理，避免冲突）
        # 处理 **粗体** 和 __粗体__
        html = re.sub(r'\*\*(.+?)\*\*', r'<strong style="font-weight: bold; color: #2c3e50;">\1</strong>', html)
        html = re.sub(r'__(.+?)__', r'<strong style="font-weight: bold; color: #2c3e50;">\1</strong>', html)
        
        # 斜体（使用单词边界，避免匹配文件名中的下划线）
        # 只匹配前后有空格、标点或行首行尾的下划线，且内容不能是纯下划线
        # 处理 *斜体* 和 _斜体_（但不在代码块中）
        html = re.sub(r'(?<![a-zA-Z0-9_])\*(.+?)\*(?![a-zA-Z0-9_*])', r'<em style="font-style: italic;">\1</em>', html)
        # 对于下划线斜体，需要更严格的匹配：前后必须是空格、标点或行首行尾，且内容不能包含空格
        html = re.sub(r'(?<![a-zA-Z0-9_])_(?!_)([^_\s]+?)_(?![a-zA-Z0-9_])(?!_)', r'<em style="font-style: italic;">\1</em>', html)
        
        # 删除线
        html = re.sub(r'~~(.*?)~~', r'<del style="text-decoration: line-through; color: #999;">\1</del>', html)
        
        # 链接（带样式）
        def link_replacer(match):
            text = match.group(1)
            url = match.group(2)
            return f'<a href="{url}" style="color: #3498db; text-decoration: none; border-bottom: 1px solid #3498db;">{text}</a>'
        html = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', link_replacer, html)
        
        # 图片
        html = re.sub(r'!\[([^\]]*)\]\(([^\)]+)\)', r'<img src="\2" alt="\1" style="max-width: 100%; height: auto; margin: 1em 0; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);" />', html)
        
        # 无序列表
        lines = html.split('\n')
        in_list = False
        result_lines = []
        for line in lines:
            if re.match(r'^[\*\-\+] ', line):
                if not in_list:
                    result_lines.append('<ul style="margin: 1em 0; padding-left: 2em; list-style-type: disc;">')
                    in_list = True
                content = re.sub(r'^[\*\-\+] ', '', line)
                result_lines.append(f'<li style="margin: 0.3em 0; line-height: 1.5;">{content}</li>')
            elif re.match(r'^  [\*\-\+] ', line):  # 二级列表
                if not in_list:
                    result_lines.append('<ul style="margin: 1em 0; padding-left: 2em; list-style-type: disc;">')
                    in_list = True
                content = re.sub(r'^  [\*\-\+] ', '', line)
                result_lines.append(f'<li style="margin: 0.5em 0;"><ul style="margin: 0.5em 0; padding-left: 1.5em; list-style-type: circle;"><li style="line-height: 1.6;">{content}</li></ul></li>')
            else:
                if in_list:
                    result_lines.append('</ul>')
                    in_list = False
                result_lines.append(line)
        if in_list:
            result_lines.append('</ul>')
        html = '\n'.join(result_lines)
        
        # 有序列表
        lines = html.split('\n')
        in_olist = False
        result_lines = []
        for line in lines:
            if re.match(r'^\d+\. ', line):
                if not in_olist:
                    result_lines.append('<ol style="margin: 1em 0; padding-left: 2em;">')
                    in_olist = True
                content = re.sub(r'^\d+\. ', '', line)
                result_lines.append(f'<li style="margin: 0.3em 0; line-height: 1.5;">{content}</li>')
            else:
                if in_olist:
                    result_lines.append('</ol>')
                    in_olist = False
                result_lines.append(line)
        if in_olist:
            result_lines.append('</ol>')
        html = '\n'.join(result_lines)
        
        # 引用块（改进样式）
        lines = html.split('\n')
        in_blockquote = False
        result_lines = []
        for line in lines:
            if line.startswith('> '):
                if not in_blockquote:
                    result_lines.append('<blockquote style="margin: 1.2em 0; padding: 1em 1.2em; border-left: 4px solid #3498db; background: linear-gradient(to right, #f0f7ff 0%, #f8f9fa 100%); color: #2c3e50; border-radius: 0 6px 6px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">')
                    in_blockquote = True
                content = line[2:]  # 移除 '> '
                # 处理引用中的格式（粗体、斜体等）
                result_lines.append(f'<p style="margin: 0.4em 0; line-height: 1.7; font-style: italic; color: #34495e;">{content}</p>')
            elif line.strip() == '>':
                # 空引用行，用于段落分隔
                if in_blockquote:
                    result_lines.append('<p style="margin: 0.3em 0;"></p>')
            else:
                if in_blockquote:
                    result_lines.append('</blockquote>')
                    in_blockquote = False
                result_lines.append(line)
        if in_blockquote:
            result_lines.append('</blockquote>')
        html = '\n'.join(result_lines)
        
        # 水平分割线
        html = re.sub(r'^---$', '<hr style="border: none; border-top: 2px solid #eee; margin: 2em 0;" />', html, flags=re.MULTILINE)
        html = re.sub(r'^\*\*\*$', '<hr style="border: none; border-top: 2px solid #eee; margin: 2em 0;" />', html, flags=re.MULTILINE)
        
        # 表格（简单支持）
        def process_table(text):
            lines = text.split('\n')
            in_table = False
            result = []
            for i, line in enumerate(lines):
                if '|' in line and not line.strip().startswith('|--'):
                    if not in_table:
                        result.append('<table style="border-collapse: collapse; width: 100%; margin: 1em 0; border: 1px solid #ddd;">')
                        in_table = True
                    cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                    if i > 0 and '|--' in lines[i-1]:  # 表头
                        result.append('<thead><tr>')
                        for cell in cells:
                            result.append(f'<th style="border: 1px solid #ddd; padding: 8px 12px; background-color: #f8f9fa; font-weight: bold; text-align: left;">{cell}</th>')
                        result.append('</tr></thead><tbody>')
                    else:
                        result.append('<tr>')
                        for cell in cells:
                            result.append(f'<td style="border: 1px solid #ddd; padding: 8px 12px;">{cell}</td>')
                        result.append('</tr>')
                else:
                    if in_table:
                        result.append('</tbody></table>')
                        in_table = False
                    result.append(line)
            if in_table:
                result.append('</tbody></table>')
            return '\n'.join(result)
        
        html = process_table(html)
        
        # 段落处理（先处理段落，再处理换行）
        # 将连续的空行（\n\n或更多）作为段落分隔符
        html = re.sub(r'\n{3,}', '\n\n', html)  # 将3个或更多换行合并为2个
        paragraphs = html.split('\n\n')
        result_paragraphs = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            # 如果已经是HTML标签（标题、列表、代码块等），直接保留
            if re.match(r'^<(h[1-6]|ul|ol|li|pre|blockquote|table|hr)', para):
                result_paragraphs.append(para)
            elif para.startswith('<'):
                # 其他HTML标签也保留
                result_paragraphs.append(para)
            else:
                # 普通文本，包装成段落，段落内的单个换行转换为空格或保留
                # 将段落内的单个换行转换为空格（避免不必要的换行）
                para = re.sub(r'\n+', ' ', para)
                para = f'<p style="margin: 0.8em 0; line-height: 1.6;">{para}</p>'
                result_paragraphs.append(para)
        html = '\n'.join(result_paragraphs)
        
        # 包装在美观的容器中
        return f'<div style="font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', \'Helvetica Neue\', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 100%; padding: 20px; background-color: #ffffff;">{html}</div>'
    
    def _on_browse_script(self):
        """浏览脚本文件"""
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        scripts_dir = project_root / "scripts"
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择脚本文件",
            str(scripts_dir) if scripts_dir.exists() else str(project_root),
            "Shell Scripts (*.sh);;All Files (*)"
        )
        
        if file_path:
            # 转换为服务器路径（假设脚本在服务器的 /ai-perf/scripts/ 目录下）
            rel_path = Path(file_path).relative_to(project_root)
            server_path = f"/ai-perf/{rel_path}"
            self.script_path_input.setText(server_path)
    
    def _on_run_clicked(self):
        """执行脚本按钮点击事件"""
        if self._is_running:
            reply = QMessageBox.question(
                self,
                "确认停止",
                "脚本正在执行中，确定要停止吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._stop_execution()
            return
        
        script_path = self.script_path_input.text().strip()
        if not script_path:
            QMessageBox.warning(self, "错误", "请输入脚本路径")
            return
        
        # 加载 SSH 配置
        from utils.config_manager import ConfigManager
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        if not ssh_config.get("host") or not ssh_config.get("username"):
            QMessageBox.warning(self, "错误", "请先配置 SSH 连接信息（在系统设置中）")
            return
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        params = self.params_input.text().strip()
        cmd = f"{script_path} {params}".strip()
        header_text = f"$ {cmd}\n"
        header_text += "=" * 80 + "\n\n"
        self._append_output(header_text)
        
        # 开始执行
        self._is_running = True
        self.run_btn.setText("停止")
        self.run_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        
        # 在后台线程中执行
        worker = _ScriptExecutionWorker(ssh_config, script_path, params, self._screen_name)
        worker.signals.output.connect(self._append_output_with_ansi)
        worker.signals.finished.connect(self._on_execution_finished)
        worker.signals.error.connect(self._on_execution_error)
        QThreadPool.globalInstance().start(worker)
        
        # 启动定时器，定期读取 screen 输出
        self._output_timer = QTimer()
        self._output_timer.timeout.connect(self._read_screen_output)
        self._output_timer.start(500)  # 每500ms读取一次
    
    def _read_screen_output(self):
        """读取 screen 的输出"""
        if not self._is_running:
            if self._output_timer:
                self._output_timer.stop()
                self._output_timer = None
            return
        
        try:
            from utils.config_manager import ConfigManager
            from utils.ssh_client import SSHClient
            
            config = ConfigManager.load()
            ssh_config = {
                "host": config.get("ssh_host", ""),
                "port": config.get("ssh_port", 22),
                "username": config.get("ssh_username", ""),
                "password": config.get("ssh_password", ""),
                "key_path": config.get("ssh_key_path", ""),
            }
            
            ssh = SSHClient(**ssh_config)
            if ssh.connect():
                # 获取 screen 的输出
                # 使用 hardcopy 命令获取 screen 的内容
                cmd = f"screen -S {self._screen_name} -X hardcopy /tmp/screen_output_{self._screen_name}.txt 2>/dev/null && cat /tmp/screen_output_{self._screen_name}.txt 2>/dev/null || echo ''"
                result = ssh.execute(cmd)
                
                if result.get("stdout"):
                    # 只显示新增的内容（简化处理，实际应该做增量读取）
                    self._append_output_with_ansi(result["stdout"])
                
                ssh.close()
        except Exception as e:
            # 静默处理错误，避免频繁弹窗
            pass
    
    def _append_output_with_ansi(self, text: str):
        """追加输出文本，支持 ANSI 颜色代码"""
        import re
        
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        current_format = QTextCharFormat(self._default_format)
        ansi_pattern = re.compile(r'\033\[([0-9;]+)m|\x1b\[([0-9;]+)m')
        
        last_pos = 0
        for match in ansi_pattern.finditer(text):
            if match.start() > last_pos:
                plain_text = text[last_pos:match.start()]
                cursor.setCharFormat(current_format)
                cursor.insertText(plain_text)
            
            code_str = match.group(1) or match.group(2)
            codes = code_str.split(';')
            
            for code in codes:
                code = code.strip()
                if not code or code == '0':
                    current_format = QTextCharFormat(self._default_format)
                elif code == '1':
                    current_format.setFontWeight(QFont.Weight.Bold)
                elif code == '22':
                    current_format.setFontWeight(QFont.Weight.Normal)
                elif code in ['30', '31', '32', '33', '34', '35', '36', '37',
                             '90', '91', '92', '93', '94', '95', '96', '97']:
                    current_format.setForeground(self._parse_ansi_color(code))
            
            last_pos = match.end()
        
        if last_pos < len(text):
            plain_text = text[last_pos:]
            cursor.setCharFormat(current_format)
            cursor.insertText(plain_text)
        
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _parse_ansi_color(self, code: str) -> QColor:
        """解析 ANSI 颜色代码"""
        ansi_colors = {
            '30': QColor("#000000"), '31': QColor("#CD3131"), '32': QColor("#0DBC79"),
            '33': QColor("#E5E510"), '34': QColor("#2472C8"), '35': QColor("#BC3FBC"),
            '36': QColor("#11A8CD"), '37': QColor("#E5E5E5"),
            '90': QColor("#767676"), '91': QColor("#F14C4C"), '92': QColor("#23D18B"),
            '93': QColor("#F5F543"), '94': QColor("#3B8EEA"), '95': QColor("#D670D6"),
            '96': QColor("#29B8DB"), '97': QColor("#FFFFFF"),
        }
        return ansi_colors.get(code, QColor("#FFFFFF"))
    
    def _append_output(self, text: str):
        """追加输出文本（用于头部信息等）"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.setCharFormat(self._default_format)
        cursor.insertText(text)
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_execution_finished(self):
        """执行完成"""
        self._is_running = False
        self.run_btn.setText("Run")
        self.run_btn.setStyleSheet("")
        
        if self._output_timer:
            self._output_timer.stop()
            self._output_timer = None
        
        self._append_output("\n" + "=" * 80 + "\n")
        self._append_output("[完成] 脚本执行完成\n")
    
    def _on_execution_error(self, error_msg: str):
        """执行错误"""
        self._is_running = False
        self.run_btn.setText("Run")
        self.run_btn.setStyleSheet("")
        
        if self._output_timer:
            self._output_timer.stop()
            self._output_timer = None
        
        self._append_output(f"\n[错误] {error_msg}\n")
    
    def _stop_execution(self):
        """停止执行"""
        self._is_running = False
        self.run_btn.setText("Run")
        self.run_btn.setStyleSheet("")
        
        if self._output_timer:
            self._output_timer.stop()
            self._output_timer = None
        
        self._append_output("\n[已停止] 用户手动停止执行\n")


class _ScriptExecutionWorkerSignals(QObject):
    output = Signal(str)
    finished = Signal()
    error = Signal(str)


class _ScriptExecutionWorker(QRunnable):
    """后台线程：通过 SSH 在 screen 中执行脚本"""
    def __init__(self, ssh_config: dict, script_path: str, params: str, screen_name: str):
        super().__init__()
        self.signals = _ScriptExecutionWorkerSignals()
        self._ssh_config = ssh_config
        self._script_path = script_path
        self._params = params
        self._screen_name = screen_name
    
    @Slot()
    def run(self) -> None:
        try:
            from utils.ssh_client import SSHClient
            
            ssh = SSHClient(**self._ssh_config)
            if not ssh.connect():
                self.signals.error.emit("SSH 连接失败")
                return
            
            # 检查 screen 是否存在，如果不存在则创建
            check_cmd = f"screen -list | grep -q {self._screen_name} || screen -dmS {self._screen_name}"
            check_result = ssh.execute(check_cmd)
            
            if not check_result.get("success"):
                self.signals.error.emit(f"创建 screen 失败: {check_result.get('stderr', '未知错误')}")
                ssh.close()
                return
            
            # 构建执行命令
            cmd = f"{self._script_path} {self._params}".strip()
            
            # 在 screen 中执行命令（使用 stuff 命令发送到 screen）
            # 先发送命令，然后发送回车
            # 转义特殊字符
            cmd_escaped = cmd.replace("'", "'\\''")
            execute_cmd = f"screen -S {self._screen_name} -X stuff '{cmd_escaped}\\n'"
            execute_result = ssh.execute(execute_cmd)
            
            if not execute_result.get("success"):
                self.signals.error.emit(f"执行命令失败: {execute_result.get('stderr', '未知错误')}")
                ssh.close()
                return
            
            # 等待一段时间让命令开始执行
            import time
            time.sleep(1)
            
            # 持续读取输出（简化处理，实际应该更智能）
            # 这里只是启动执行，实际输出由定时器读取
            ssh.close()
            self.signals.finished.emit()
            
        except Exception as e:
            self.signals.error.emit(f"执行脚本失败: {e}")


class MaintenanceView(QWidget):
    """日常运维页面"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 跟踪哪些TAB已经加载过数据
        self._loaded_tabs = set()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)  # 与操作日志对齐
        layout.setSpacing(8)  # 与操作日志对齐

        # 标题
        title = QLabel("日常运维")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # TAB切换：系统设置/数据库备份（完全对齐操作日志页面的结构）
        self.tabs = QTabWidget()
        # 不设置最大高度，让TAB组件填充剩余空间（TAB标签栏会自动调整高度）
        
        # 系统概览 TAB
        self.settings_tab = SystemSettingsTab(self)
        self.tabs.addTab(self.settings_tab, "系统概览")
        
        # 数据库备份 TAB
        self.backup_tab = DatabaseBackupTab(self)
        self.tabs.addTab(self.backup_tab, "数据库备份")
        
        # 日志查看 TAB
        try:
            from windows.log_view_tab import LogViewTab
            self.log_view_tab = LogViewTab(self)
            self.tabs.addTab(self.log_view_tab, "日志查看")
        except ImportError as e:
            # 如果导入失败，记录错误但不影响其他功能
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"导入日志查看TAB失败: {e}")
        
        # 发布 TAB
        self.deploy_tab = DeployTab(self)
        self.tabs.addTab(self.deploy_tab, "发布")
        
        # 脚本执行 TAB
        self.script_execution_tab = ScriptExecutionTab(self)
        self.tabs.addTab(self.script_execution_tab, "脚本执行")
        
        # 版本管理 TAB（从 version_view 导入）
        from windows.version_view import VersionView
        self.version_management_tab = VersionView()
        self.tabs.addTab(self.version_management_tab, "版本管理")
        
        # 打包 TAB
        self.package_tab = PackageTab(self)
        self.tabs.addTab(self.package_tab, "打包")
        
        # 设置TAB组件的大小策略，让它填充剩余空间
        from PySide6.QtWidgets import QSizePolicy
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 监听TAB切换事件，首次切换到某个TAB时自动加载数据
        self.tabs.currentChanged.connect(self._on_tab_changed)
        
        layout.addWidget(self.tabs, 1)  # 设置stretch factor为1，让TAB组件填充剩余空间

        # 默认加载系统设置TAB的数据（索引0）
        QTimer.singleShot(100, lambda: self._load_tab_data(0))

    def _on_tab_changed(self, index: int):
        """TAB切换事件处理，首次切换到某个TAB时自动加载数据"""
        self._load_tab_data(index)
    
    def _load_tab_data(self, index: int):
        """加载指定TAB的数据（仅在首次加载）"""
        # 如果已经加载过，不再重复加载
        if index in self._loaded_tabs:
            return
        
        # 标记为已加载
        self._loaded_tabs.add(index)
        
        # 根据TAB索引加载对应数据
        if index == 0:
            # 系统设置 TAB
            if hasattr(self, "settings_tab") and hasattr(self.settings_tab, "reload"):
                self.settings_tab.reload()
        elif index == 1:
            # 数据库备份 TAB
            if hasattr(self, "backup_tab") and hasattr(self.backup_tab, "reload_from_api"):
                self.backup_tab.reload_from_api()
        elif index == 2:
            # 日志查看 TAB
            if hasattr(self, "log_view_tab") and hasattr(self.log_view_tab, "reload_from_ssh"):
                self.log_view_tab.reload_from_ssh()
        elif index == 3:
            # 发布 TAB（不需要加载数据，用户点击执行按钮时才执行）
            pass
        elif index == 4:
            # 脚本执行 TAB（不需要加载数据，用户点击执行按钮时才执行）
            pass
        elif index == 5:
            # 版本管理 TAB（首次加载时刷新版本信息）
            if hasattr(self, "version_management_tab") and hasattr(self.version_management_tab, "reload"):
                self.version_management_tab.reload()
        elif index == 6:
            # 打包 TAB（首次加载时获取版本列表）
            if hasattr(self, "package_tab") and hasattr(self.package_tab, "reload_versions"):
                self.package_tab.reload_versions()
    
    def reload(self):
        """重新加载数据（供外部调用）"""
        # 如果当前选中的是系统设置 TAB，刷新服务列表和定时任务列表
        if self.tabs.currentIndex() == 0:
            if hasattr(self, "settings_tab") and hasattr(self.settings_tab, "reload"):
                self.settings_tab.reload()
        # 如果当前选中的是数据库备份 TAB，刷新备份列表
        elif self.tabs.currentIndex() == 1:
            if hasattr(self, "backup_tab") and hasattr(self.backup_tab, "reload_from_api"):
                self.backup_tab.reload_from_api()
        # 如果当前选中的是日志查看 TAB，刷新日志列表
        elif self.tabs.currentIndex() == 2:
            if hasattr(self, "log_view_tab") and hasattr(self.log_view_tab, "reload_from_ssh"):
                self.log_view_tab.reload_from_ssh()
        # 如果当前选中的是版本管理 TAB，刷新版本信息
        elif self.tabs.currentIndex() == 5:
            if hasattr(self, "version_management_tab") and hasattr(self.version_management_tab, "reload"):
                self.version_management_tab.reload()
        # 如果当前选中的是打包 TAB，刷新版本列表
        elif self.tabs.currentIndex() == 6:
            if hasattr(self, "package_tab") and hasattr(self.package_tab, "reload_versions"):
                self.package_tab.reload_versions()


# ==================== 打包 TAB ====================

class _VersionListWorkerSignals(QObject):
    """版本列表加载信号"""
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _VersionListWorker(QRunnable):
    """后台线程：从 GitHub Releases 获取版本列表"""
    def __init__(self, repo_owner: str = None, repo_name: str = None):
        super().__init__()
        self.signals = _VersionListWorkerSignals()
        self._repo_owner = repo_owner
        self._repo_name = repo_name

    @Slot()
    def run(self) -> None:
        try:
            # 从配置读取打包专用的 GitHub API 信息和仓库信息（与第三方平台配置完全独立）
            cfg = ConfigManager.load()
            api_url = cfg.get("packaging_github_api_url", "https://api.github.com").rstrip("/")
            api_key = cfg.get("packaging_github_api_key", "")
            
            # 从配置读取仓库信息，如果没有则使用默认值
            repo_owner = self._repo_owner or cfg.get("packaging_github_repo_owner", "sanyingkeji")
            repo_name = self._repo_name or cfg.get("packaging_github_repo_name", "ai-perf")
            
            # 私有仓库必须使用 API Key
            if not api_key:
                self.signals.error.emit("请先在设置页面 → 打包配置 中配置 GitHub API Key（私有仓库需要授权）")
                return
            
            # 构建 GitHub API URL
            url = f"{api_url}/repos/{repo_owner}/{repo_name}/releases"
            
            # 构建请求头
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "aiperf-admin-client/1.0",
                "Authorization": f"token {api_key}"
            }
            
            # 调用 GitHub API
            response = httpx.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                releases = response.json()
                # 转换为统一的格式
                items = []
                for release in releases:
                    # 解析 assets
                    assets = release.get("assets", [])
                    download_urls = {}
                    for asset in assets:
                        asset_name = asset.get("name", "")
                        asset_url = asset.get("browser_download_url", "")
                        # 根据文件名判断平台
                        if any(ext in asset_name.lower() for ext in [".dmg", ".pkg"]):
                            platform = "darwin"
                        elif any(ext in asset_name.lower() for ext in [".exe", ".msi"]):
                            platform = "windows"
                        elif any(ext in asset_name.lower() for ext in [".deb", ".rpm"]):
                            platform = "linux"
                        else:
                            continue
                        
                        if platform not in download_urls:
                            download_urls[platform] = []
                        download_urls[platform].append({
                            "name": asset_name,
                            "url": asset_url,
                            "size": asset.get("size", 0),
                        })
                    
                    items.append({
                        "id": release.get("id"),
                        "version": release.get("tag_name", "").lstrip("v"),  # 移除 v 前缀
                        "tag_name": release.get("tag_name", ""),
                        "name": release.get("name", ""),
                        "body": release.get("body", ""),
                        "published_at": release.get("published_at", ""),
                        "prerelease": release.get("prerelease", False),
                        "draft": release.get("draft", False),
                        "download_urls": download_urls,
                        "assets": assets,
                    })
                
                self.signals.finished.emit(items)
            elif response.status_code == 404:
                self.signals.error.emit(f"仓库不存在：{repo_owner}/{repo_name}，或没有访问权限（请检查 API Key）")
            elif response.status_code == 401:
                self.signals.error.emit("GitHub API 认证失败，请检查 GitHub API Key 是否正确")
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", f"HTTP {response.status_code}")
                except:
                    error_msg = f"HTTP {response.status_code}: {response.text[:100]}"
                self.signals.error.emit(f"获取 GitHub Releases 失败：{error_msg}")
        except httpx.TimeoutException:
            self.signals.error.emit("请求超时，请检查网络连接")
        except Exception as e:
            self.signals.error.emit(f"加载版本列表失败：{type(e).__name__}: {e}")


class _UploadFileWorkerSignals(QObject):
    """文件上传信号"""
    finished = Signal(str)  # download_url
    error = Signal(str)
    progress = Signal(int, int)  # 已上传字节数, 总字节数


class _UploadFileWorker(QRunnable):
    """后台线程：上传文件"""
    def __init__(self, file_path: str, platform: str, version: str, upload_api_url: str):
        super().__init__()
        self.signals = _UploadFileWorkerSignals()
        self._file_path = file_path
        self._platform = platform
        self._version = version
        self._upload_api_url = upload_api_url
        self._should_stop = False
    
    def stop(self):
        """停止上传"""
        self._should_stop = True
    
    @Slot()
    def run(self) -> None:
        try:
            if self._should_stop:
                return
            
            # 读取文件并上传
            with open(self._file_path, "rb") as f:
                files = {"file": (os.path.basename(self._file_path), f, "application/octet-stream")}
                data = {
                    "platform": self._platform,
                    "version": self._version
                }
                
                # 使用httpx上传（multipart/form-data）
                response = httpx.post(
                    self._upload_api_url,
                    files=files,
                    data=data,
                    timeout=300.0
                )
                
                if self._should_stop:
                    return
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == "success":
                        download_url = result.get("url")
                        if download_url:
                            self.signals.finished.emit(download_url)
                        else:
                            self.signals.error.emit("上传成功但未返回URL")
                    else:
                        error_msg = result.get("message", "上传失败")
                        self.signals.error.emit(error_msg)
                else:
                    try:
                        error_text = response.text
                    except:
                        error_text = "无法读取错误信息"
                    error_msg = f"HTTP {response.status_code}: {error_text}"
                    self.signals.error.emit(error_msg)
        
        except httpx.TimeoutException:
            self.signals.error.emit("上传超时，请检查网络连接或文件大小")
        except Exception as e:
            self.signals.error.emit(f"上传失败：{type(e).__name__}: {e}")


class PackageTab(QWidget):
    """打包 TAB：Git Push 和版本管理"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = False
        self._process = None
        self._upload_worker = None
        self._upload_progress = None
        self._progress_timer = None
        self._progress_value = 0
        self._versions = []  # 存储版本列表
        self._push_step = None  # 跟踪 git push 的执行步骤：'check', 'add', 'commit', 'push'
        self._git_status_output = ""  # 存储 git status 的输出
        
        # 获取项目根目录
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        self._project_root = str(project_root.resolve())
        
        # 使用 VersionManager 统一管理版本号
        self._version_manager = VersionManager(Path(self._project_root))
        
        # 获取当前版本号
        self._current_version = self._version_manager.get_current_version() or "1.0.1"
        
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 获取项目根目录
        current_file = Path(__file__).resolve()
        # admin_ui_client/windows/maintenance_view.py -> admin_ui_client -> 项目根目录
        project_root = current_file.parent.parent.parent
        self._project_root = str(project_root.resolve())
        
        # 头部：显示本地目录地址和 git push 按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        
        # 显示本地目录地址
        dir_label = QLabel(f"本地目录：{self._project_root}")
        dir_label.setFont(QFont("Arial", 10))
        dir_label.setStyleSheet("color: #666; font-family: 'Courier New', monospace;")
        header_layout.addWidget(dir_label)
        header_layout.addStretch()
        
        # git push 按钮
        self.push_btn = QPushButton("git push")
        self.push_btn.setFixedWidth(120)
        self.push_btn.setFixedHeight(28)
        self.push_btn.clicked.connect(self._on_push_clicked)
        header_layout.addWidget(self.push_btn)
        
        # Release 按钮（动态获取版本号，使用 VersionManager）
        self.release_btn = QPushButton(f"Release V{self._current_version}")
        self.release_btn.setFixedWidth(180)
        self.release_btn.setFixedHeight(28)
        self.release_btn.clicked.connect(self._on_release_clicked)
        header_layout.addWidget(self.release_btn)
        
        # 版本管理按钮（统一管理版本号）
        version_mgmt_btn = QPushButton("版本管理")
        version_mgmt_btn.setFixedWidth(100)
        version_mgmt_btn.setFixedHeight(28)
        version_mgmt_btn.setToolTip("统一管理所有客户端的版本号")
        version_mgmt_btn.clicked.connect(self._on_version_management_clicked)
        header_layout.addWidget(version_mgmt_btn)
        
        # Check Actions 按钮
        self.actions_btn = QPushButton("Check Actions")
        self.actions_btn.setFixedWidth(150)
        self.actions_btn.setFixedHeight(28)
        self.actions_btn.clicked.connect(self._on_check_actions_clicked)
        header_layout.addWidget(self.actions_btn)
        
        layout.addLayout(header_layout)
        
        # 使用 QSplitter 实现左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：版本列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)
        
        # 标题和刷新按钮
        title_layout = QHBoxLayout()
        title_label = QLabel("版本列表")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        
        refresh_versions_btn = QPushButton("刷新")
        refresh_versions_btn.setFixedWidth(80)
        refresh_versions_btn.clicked.connect(self.reload_versions)
        title_layout.addWidget(refresh_versions_btn)
        
        left_layout.addLayout(title_layout)
        
        # 版本列表表格
        self.version_table = QTableWidget()
        self.version_table.setColumnCount(5)
        self.version_table.setHorizontalHeaderLabels(["版本号", "发布时间", "Assets", "状态", "操作"])
        self.version_table.horizontalHeader().setStretchLastSection(True)
        self.version_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.version_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.version_table.setAlternatingRowColors(True)
        self.version_table.setShowGrid(True)
        
        # 设置列宽
        header = self.version_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 版本号
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 发布时间
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Assets
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(4, QHeaderView.Stretch)  # 操作
        
        left_layout.addWidget(self.version_table, 1)
        
        splitter.addWidget(left_widget)
        
        # 右侧：命令行输出
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)
        
        # 标题
        output_title = QLabel("Git Push 输出")
        output_title.setFont(QFont("Arial", 14, QFont.Bold))
        right_layout.addWidget(output_title)
        
        # 输出区域（继承发布/脚本执行的样式）
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        
        # 设置苹果终端 Basic 主题的字体
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setFixedPitch(True)
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        if font:
            self.output_text.setFont(font)
        
        # 设置 tab 宽度
        self.output_text.setTabStopDistance(4 * self.output_text.fontMetrics().averageCharWidth())
        
        # 苹果终端 Basic 主题默认样式（完全匹配发布TAB）
        self.output_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #FFFFFF;
                border: none;
                padding: 10px;
                selection-background-color: #0066CC;
                selection-color: #FFFFFF;
                font-family: "Menlo", "Monaco", "Courier New", monospace;
                font-size: 12pt;
                line-height: 1.2;
            }
        """)
        
        # 默认文本格式
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("#FFFFFF"))
        if font:
            self._default_format.setFont(font)
        self.output_text.setPlaceholderText("点击\"git push\"按钮开始推送...")
        right_layout.addWidget(self.output_text, 1)
        
        splitter.addWidget(right_widget)
        
        # 设置分割比例（左侧30%，右侧70%）
        splitter.setSizes([300, 700])
        
        layout.addWidget(splitter, 1)
    
    def reload_versions(self):
        """重新加载版本列表（从 GitHub Releases）"""
        # 检查是否配置了打包专用的 GitHub API Key（私有仓库需要）
        cfg = ConfigManager.load()
        api_key = cfg.get("packaging_github_api_key", "")
        if not api_key:
            QMessageBox.warning(
                self,
                "需要配置",
                "私有仓库需要 GitHub API Key 才能访问。\n\n"
                "请在 设置 → 打包配置 中配置 GitHub API Key。"
            )
            return
        
        # 从配置读取仓库信息（可选，默认使用 sanyingkeji/ai-perf）
        repo_owner = cfg.get("packaging_github_repo_owner", "sanyingkeji")
        repo_name = cfg.get("packaging_github_repo_name", "ai-perf")
        
        worker = _VersionListWorker(repo_owner=repo_owner, repo_name=repo_name)
        worker.signals.finished.connect(self._on_versions_loaded)
        worker.signals.error.connect(self._show_versions_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_versions_loaded(self, items: List[Dict[str, Any]]):
        """版本列表加载完成"""
        self._versions = items
        self._update_version_table()
        # 恢复刷新按钮状态
        self.refresh_versions_btn.setEnabled(True)
        self.refresh_versions_btn.setText("🔄 刷新")
    
    def _show_versions_error(self, error_msg: str):
        """显示版本列表加载错误"""
        QMessageBox.warning(self, "错误", f"加载版本列表失败：{error_msg}")
        self._versions = []
        self._update_version_table()
        # 恢复刷新按钮状态
        self.refresh_versions_btn.setEnabled(True)
        self.refresh_versions_btn.setText("🔄 刷新")
    
    def _update_version_table(self):
        """更新版本列表表格"""
        self.version_table.setRowCount(len(self._versions))
        
        for row, release_data in enumerate(self._versions):
            # 版本号
            tag_name = release_data.get("tag_name", "")
            version = release_data.get("version", tag_name)
            version_item = QTableWidgetItem(version)
            self.version_table.setItem(row, 0, version_item)
            
            # 发布时间
            published_at = release_data.get("published_at", "")
            if published_at:
                try:
                    # 解析 ISO 8601 格式的时间
                    from datetime import datetime
                    dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                    # 格式化为本地时间
                    published_str = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    published_str = published_at[:10]  # 只显示日期部分
            else:
                published_str = "未发布"
            published_item = QTableWidgetItem(published_str)
            self.version_table.setItem(row, 1, published_item)
            
            # Assets 数量
            assets = release_data.get("assets", [])
            download_urls = release_data.get("download_urls", {})
            assets_info = []
            if download_urls.get("darwin"):
                assets_info.append(f"macOS({len(download_urls['darwin'])})")
            if download_urls.get("windows"):
                assets_info.append(f"Windows({len(download_urls['windows'])})")
            if download_urls.get("linux"):
                assets_info.append(f"Linux({len(download_urls['linux'])})")
            assets_text = ", ".join(assets_info) if assets_info else f"{len(assets)} 个文件"
            assets_item = QTableWidgetItem(assets_text)
            self.version_table.setItem(row, 2, assets_item)
            
            # 状态
            is_prerelease = release_data.get("prerelease", False)
            is_draft = release_data.get("draft", False)
            if is_draft:
                status_text = "草稿"
            elif is_prerelease:
                status_text = "预发布"
            else:
                status_text = "正式版"
            status_item = QTableWidgetItem(status_text)
            self.version_table.setItem(row, 3, status_item)
            
            # 操作按钮（上传）
            upload_btn = QPushButton("上传")
            upload_btn.setFixedWidth(60)
            upload_btn.setFixedHeight(28)
            # 存储版本数据到按钮的属性中
            upload_btn.setProperty("version_data", release_data)
            upload_btn.clicked.connect(self._on_upload_clicked)
            self.version_table.setCellWidget(row, 4, upload_btn)
    
    def _on_upload_clicked(self):
        """上传按钮点击事件"""
        btn = self.sender()
        if not btn:
            return
        
        version_data = btn.property("version_data")
        if not version_data:
            return
        
        # 使用 tag_name 或 version（tag_name 可能包含 v 前缀）
        version = version_data.get("tag_name", version_data.get("version", ""))
        if not version:
            QMessageBox.warning(self, "错误", "版本号为空")
            return
        
        # 移除 v 前缀（如果有）
        version = version.lstrip("v")
        
        # 选择文件
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择安装包文件（版本：{version}）",
            "",
            "所有文件 (*.*);;DMG文件 (*.dmg);;EXE文件 (*.exe);;MSI文件 (*.msi);;DEB文件 (*.deb);;RPM文件 (*.rpm);;ZIP文件 (*.zip);;TAR文件 (*.tar.gz)"
        )
        
        if not file_path:
            return
        
        # 根据文件扩展名判断平台
        file_ext = os.path.splitext(file_path)[1].lower()
        platform_map = {
            ".dmg": "darwin",
            ".pkg": "darwin",
            ".exe": "windows",
            ".msi": "windows",
            ".deb": "linux",
            ".rpm": "linux",
        }
        platform = platform_map.get(file_ext, "unknown")
        
        if platform == "unknown":
            QMessageBox.warning(self, "错误", f"无法识别文件类型：{file_ext}")
            return
        
        # 获取文件大小
        try:
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            file_name = os.path.basename(file_path)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法读取文件信息：{e}")
            return
        
        # 创建进度对话框
        self._upload_progress = QProgressDialog(
            f"正在上传文件：{file_name}\n大小：{file_size_mb:.2f} MB",
            "取消",
            0,
            100,
            self
        )
        self._upload_progress.setWindowTitle("文件上传")
        self._upload_progress.setWindowModality(Qt.WindowModal)
        self._upload_progress.setMinimumDuration(0)
        self._upload_progress.setValue(0)
        self._upload_progress.setAutoClose(False)
        self._upload_progress.setAutoReset(False)
        
        # 从配置读取上传API地址
        cfg = ConfigManager.load()
        upload_api_url = cfg.get("upload_api_url", "http://127.0.0.1:8882/api/upload")
        
        # 创建上传Worker
        self._upload_worker = _UploadFileWorker(file_path, platform, version, upload_api_url)
        
        # 连接信号
        self._upload_worker.signals.finished.connect(self._on_upload_finished)
        self._upload_worker.signals.error.connect(self._on_upload_error)
        self._upload_progress.canceled.connect(self._on_upload_canceled)
        
        # 启动上传
        QThreadPool.globalInstance().start(self._upload_worker)
        
        # 启动进度更新定时器
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._update_upload_progress)
        self._progress_timer.start(100)
        self._progress_value = 0
    
    def _update_upload_progress(self):
        """更新上传进度（模拟）"""
        if self._upload_progress and not self._upload_progress.wasCanceled():
            if self._progress_value < 90:
                self._progress_value += 2
                self._upload_progress.setValue(self._progress_value)
            else:
                self._progress_timer.stop()
    
    def _on_upload_finished(self, download_url: str):
        """上传完成"""
        if self._progress_timer:
            self._progress_timer.stop()
        
        if self._upload_progress:
            self._upload_progress.setValue(100)
            self._upload_progress.close()
            self._upload_progress = None
        
        QMessageBox.information(self, "上传成功", f"文件上传成功！\n\n下载URL：\n{download_url}")
        
        self._upload_worker = None
        # 重新加载版本列表
        self.reload_versions()
    
    def _on_upload_error(self, error_msg: str):
        """上传错误"""
        if self._progress_timer:
            self._progress_timer.stop()
        
        if self._upload_progress:
            self._upload_progress.close()
            self._upload_progress = None
        
        QMessageBox.warning(self, "上传失败", f"文件上传失败：{error_msg}")
        
        self._upload_worker = None
    
    def _on_upload_canceled(self):
        """上传取消"""
        if self._upload_worker:
            self._upload_worker.stop()
        
        if self._progress_timer:
            self._progress_timer.stop()
        
        if self._upload_progress:
            self._upload_progress.close()
            self._upload_progress = None
        
        self._upload_worker = None
    
    def _on_push_clicked(self):
        """git push 按钮点击事件"""
        if self._is_running:
            # 如果正在执行，显示停止确认
            reply = QMessageBox.question(
                self,
                "确认停止",
                "Git push 正在执行中，确定要停止吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._stop_push()
            return
        
        # 检查是否在 Git 仓库中
        git_dir = os.path.join(self._project_root, ".git")
        if not os.path.exists(git_dir):
            QMessageBox.warning(
                self,
                "错误",
                f"当前目录不是 Git 仓库：\n{self._project_root}"
            )
            return
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        header_text = f"$ cd {self._project_root}\n"
        header_text += "=" * 80 + "\n\n"
        self._append_output(header_text)
        
        # 开始执行
        self._is_running = True
        self.push_btn.setText("停止")
        self.push_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        self.release_btn.setEnabled(False)
        self.actions_btn.setEnabled(False)
        
        # 使用 QProcess 执行 git 命令
        self._process = QProcess(self)
        self._process.setWorkingDirectory(self._project_root)
        
        # 连接信号
        self._process.readyReadStandardOutput.connect(self._on_ready_read_output)
        self._process.readyReadStandardError.connect(self._on_ready_read_error)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        
        # 第一步：检查是否有未提交的更改
        self._push_step = 'check'
        self._append_output("$ git status --porcelain\n")
        self._process.start("git", ["status", "--porcelain"])
    
    def _stop_push(self):
        """停止 git push"""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.kill()
            self._process.waitForFinished(3000)
        self._is_running = False
        self.push_btn.setText("git push")
        self.push_btn.setStyleSheet("")
        self.release_btn.setEnabled(True)
        self.actions_btn.setEnabled(True)
        self._push_step = None
        self._git_status_output = ""
        self._append_output("\n[已停止] 用户手动停止执行\n")
    
    def _on_push_clicked_old(self):
        """git push 按钮点击事件（旧版本，已废弃）"""
        if self._is_running:
            # 如果正在执行，显示停止确认
            reply = QMessageBox.question(
                self,
                "确认停止",
                "Git push 正在执行中，确定要停止吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._stop_push()
            return
        
        # 检查是否在 Git 仓库中
        git_dir = os.path.join(self._project_root, ".git")
        if not os.path.exists(git_dir):
            QMessageBox.warning(
                self,
                "错误",
                f"当前目录不是 Git 仓库：\n{self._project_root}"
            )
            return
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        header_text = f"$ cd {self._project_root}\n"
        header_text += f"$ git push\n"
        header_text += "=" * 80 + "\n\n"
        self._append_output(header_text)
        
        # 开始执行
        self._is_running = True
        self.push_btn.setText("停止")
        self.push_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        
        # 使用 QProcess 执行 git push
        self._process = QProcess(self)
        self._process.setWorkingDirectory(self._project_root)
        
        # 连接信号
        self._process.readyReadStandardOutput.connect(self._on_ready_read_output)
        self._process.readyReadStandardError.connect(self._on_ready_read_error)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        
        # 执行 git push
        self._process.start("git", ["push"])
    
    def _stop_push(self):
        """停止 git push"""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.kill()
            self._process.waitForFinished(3000)
        self._is_running = False
        self.push_btn.setText("git push")
        self.push_btn.setStyleSheet("")
        self._append_output("\n[已停止] 用户手动停止执行\n")
    
    def _parse_ansi_color(self, code: str) -> QColor:
        """解析 ANSI 颜色代码"""
        ansi_colors = {
            '30': QColor("#000000"),  # Black
            '31': QColor("#CD3131"),  # Red
            '32': QColor("#0DBC79"),  # Green
            '33': QColor("#E5E510"),  # Yellow
            '34': QColor("#2472C8"),  # Blue
            '35': QColor("#BC3FBC"),  # Magenta
            '36': QColor("#11A8CD"),  # Cyan
            '37': QColor("#E5E5E5"),  # White
            '90': QColor("#767676"),  # Bright Black
            '91': QColor("#F14C4C"),  # Bright Red
            '92': QColor("#23D18B"),  # Bright Green
            '93': QColor("#F5F543"),  # Bright Yellow
            '94': QColor("#3B8EEA"),  # Bright Blue
            '95': QColor("#D670D6"),  # Bright Magenta
            '96': QColor("#29B8DB"),  # Bright Cyan
            '97': QColor("#FFFFFF"),  # Bright White
        }
        return ansi_colors.get(code, QColor("#FFFFFF"))
    
    def _append_output_with_ansi(self, text: str):
        """追加输出文本，支持 ANSI 颜色代码"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        current_format = QTextCharFormat(self._default_format)
        
        # 解析 ANSI 转义序列
        ansi_pattern = re.compile(r'\033\[([0-9;]+)m|\x1b\[([0-9;]+)m')
        
        last_pos = 0
        for match in ansi_pattern.finditer(text):
            if match.start() > last_pos:
                plain_text = text[last_pos:match.start()]
                cursor.setCharFormat(current_format)
                cursor.insertText(plain_text)
            
            code_str = match.group(1) or match.group(2)
            codes = code_str.split(';')
            
            for code in codes:
                code = code.strip()
                if not code or code == '0':
                    current_format = QTextCharFormat(self._default_format)
                elif code == '1':
                    current_format.setFontWeight(QFont.Weight.Bold)
                elif code == '22':
                    current_format.setFontWeight(QFont.Weight.Normal)
                elif code in ['30', '31', '32', '33', '34', '35', '36', '37',
                             '90', '91', '92', '93', '94', '95', '96', '97']:
                    current_format.setForeground(self._parse_ansi_color(code))
                elif code in ['40', '41', '42', '43', '44', '45', '46', '47',
                             '100', '101', '102', '103', '104', '105', '106', '107']:
                    bg_code = str(int(code) - 10)
                    if bg_code in ['30', '31', '32', '33', '34', '35', '36', '37',
                                  '90', '91', '92', '93', '94', '95', '96', '97']:
                        bg_color = self._parse_ansi_color(bg_code)
                        bg_color = QColor(
                            max(0, bg_color.red() - 50),
                            max(0, bg_color.green() - 50),
                            max(0, bg_color.blue() - 50)
                        )
                        current_format.setBackground(bg_color)
            
            last_pos = match.end()
        
        if last_pos < len(text):
            plain_text = text[last_pos:]
            cursor.setCharFormat(current_format)
            cursor.insertText(plain_text)
        
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _append_output(self, text: str):
        """追加输出文本"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.setCharFormat(self._default_format)
        cursor.insertText(text)
        
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_ready_read_output(self):
        """读取标准输出"""
        if self._process:
            data = self._process.readAllStandardOutput()
            if data:
                try:
                    text = data.data().decode('utf-8', errors='replace')
                    # 如果是检查状态步骤，保存输出用于判断
                    if self._push_step == 'check':
                        self._git_status_output += text
                    self._append_output_with_ansi(text)
                except Exception:
                    text = data.data().decode('latin-1', errors='replace')
                    # 如果是检查状态步骤，保存输出用于判断
                    if self._push_step == 'check':
                        self._git_status_output += text
                    self._append_output_with_ansi(text)
    
    def _on_ready_read_error(self):
        """读取标准错误"""
        if self._process:
            data = self._process.readAllStandardError()
            if data:
                try:
                    text = data.data().decode('utf-8', errors='replace')
                    self._append_output_with_ansi(text)
                except Exception:
                    text = data.data().decode('latin-1', errors='replace')
                    self._append_output_with_ansi(text)
    
    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        """进程执行完成"""
        if exit_code != 0 and self._push_step:
            # 如果命令执行失败，停止整个流程
            self._is_running = False
            self.push_btn.setText("git push")
            self.push_btn.setStyleSheet("")
            self.release_btn.setEnabled(True)
            self.actions_btn.setEnabled(True)
            self._append_output("\n" + "=" * 80 + "\n")
            self._append_output(f"[错误] 命令执行失败，退出码: {exit_code}\n")
            self._push_step = None
            self._git_status_output = ""
            self._process = None
            return
        
        # 根据当前步骤执行下一步
        if self._push_step == 'check':
            # 检查是否有未提交的更改
            has_changes = bool(self._git_status_output.strip())
            if has_changes:
                # 有更改，执行 git add
                self._push_step = 'add'
                self._append_output("\n$ git add .\n")
                self._process.start("git", ["add", "."])
            else:
                # 没有更改，直接执行 git push
                self._push_step = 'push'
                self._append_output("\n$ git push\n")
                self._process.start("git", ["push"])
        
        elif self._push_step == 'add':
            # git add 完成，执行 git commit
            self._push_step = 'commit'
            self._append_output("\n$ git commit -m \"更新代码\"\n")
            self._process.start("git", ["commit", "-m", "更新代码"])
        
        elif self._push_step == 'commit':
            # git commit 完成，执行 git push
            self._push_step = 'push'
            self._append_output("\n$ git push\n")
            self._process.start("git", ["push"])
        
        elif self._push_step == 'push':
            # git push 完成，整个流程结束
            self._is_running = False
            self.push_btn.setText("git push")
            self.push_btn.setStyleSheet("")
            self.release_btn.setEnabled(True)
            self.actions_btn.setEnabled(True)
            self._append_output("\n" + "=" * 80 + "\n")
            self._append_output("[完成] Git push 执行成功\n")
            self._push_step = None
            self._git_status_output = ""
            self._process = None
        
        else:
            # 未知步骤，直接结束
            self._is_running = False
            self.push_btn.setText("git push")
            self.push_btn.setStyleSheet("")
            self.release_btn.setEnabled(True)
            self.actions_btn.setEnabled(True)
            self._append_output("\n" + "=" * 80 + "\n")
            self._append_output("[完成] 执行完成\n")
            self._push_step = None
            self._git_status_output = ""
            self._process = None
    
    def _on_process_error(self, error: QProcess.ProcessError):
        """进程执行错误"""
        self._is_running = False
        self.push_btn.setText("git push")
        self.push_btn.setStyleSheet("")
        self.release_btn.setEnabled(True)
        self.actions_btn.setEnabled(True)
        
        error_msg = {
            QProcess.ProcessError.FailedToStart: "进程启动失败",
            QProcess.ProcessError.Crashed: "进程崩溃",
            QProcess.ProcessError.Timedout: "进程超时",
            QProcess.ProcessError.WriteError: "写入错误",
            QProcess.ProcessError.ReadError: "读取错误",
            QProcess.ProcessError.UnknownError: "未知错误"
        }.get(error, "未知错误")
        
        self._append_output(f"\n[错误] {error_msg}\n")
        
        self._process = None
    
    def _on_release_clicked(self):
        """Release 按钮点击事件：创建 tag 并触发构建"""
        if self._is_running:
            QMessageBox.warning(self, "警告", "已有操作正在执行中，请等待完成")
            return
        
        # 确认操作
        reply = QMessageBox.question(
            self,
            "确认发布",
            f"确定要创建 Release V{self._current_version} 吗？\n\n"
            f"这将执行以下操作：\n"
            f"1. 创建 git tag: v{self._current_version}\n"
            f"2. 推送 tag 到远程仓库\n"
            f"3. 触发 GitHub Actions 自动构建",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        header_text = f"$ cd {self._project_root}\n"
        header_text += f"$ git tag -d v{self._current_version}  # 删除本地 tag（如果存在）\n"
        header_text += f"$ git push origin :refs/tags/v{self._current_version}  # 删除远程 tag（如果存在）\n"
        header_text += f"$ git tag -a v{self._current_version} -m \"Release version {self._current_version}\"\n"
        header_text += f"$ git push origin v{self._current_version}\n"
        header_text += "=" * 80 + "\n\n"
        self._append_output(header_text)
        
        # 开始执行
        self._is_running = True
        self.release_btn.setText("执行中...")
        self.release_btn.setEnabled(False)
        self.push_btn.setEnabled(False)
        self.actions_btn.setEnabled(False)
        
        # 步骤 1: 删除本地 tag（如果存在）
        self._append_output(f"[步骤 1/4] 删除本地 tag（如果存在）: v{self._current_version}\n")
        delete_local_tag_process = QProcess(self)
        delete_local_tag_process.setWorkingDirectory(self._project_root)
        delete_local_tag_process.readyReadStandardOutput.connect(
            lambda: self._append_output_with_ansi(delete_local_tag_process.readAllStandardOutput().data().decode('utf-8', errors='replace'))
        )
        delete_local_tag_process.readyReadStandardError.connect(
            lambda: self._append_output_with_ansi(delete_local_tag_process.readAllStandardError().data().decode('utf-8', errors='replace'))
        )
        
        def on_delete_local_tag_finished(exit_code, exit_status):
            # 无论成功或失败都继续（tag 可能不存在）
            if exit_code == 0:
                self._append_output(f"[完成] 本地 tag 已删除\n\n")
            else:
                self._append_output(f"[提示] 本地 tag 不存在或已删除（继续执行）\n\n")
            
            # 步骤 2: 删除远程 tag（如果存在）
            self._append_output(f"[步骤 2/4] 删除远程 tag（如果存在）: v{self._current_version}\n")
            delete_remote_tag_process = QProcess(self)
            delete_remote_tag_process.setWorkingDirectory(self._project_root)
            delete_remote_tag_process.readyReadStandardOutput.connect(
                lambda: self._append_output_with_ansi(delete_remote_tag_process.readAllStandardOutput().data().decode('utf-8', errors='replace'))
            )
            delete_remote_tag_process.readyReadStandardError.connect(
                lambda: self._append_output_with_ansi(delete_remote_tag_process.readAllStandardError().data().decode('utf-8', errors='replace'))
            )
            
            def on_delete_remote_tag_finished(exit_code, exit_status):
                # 无论成功或失败都继续（tag 可能不存在）
                if exit_code == 0:
                    self._append_output(f"[完成] 远程 tag 已删除\n\n")
                else:
                    self._append_output(f"[提示] 远程 tag 不存在或已删除（继续执行）\n\n")
                
                # 步骤 3: 创建新的 tag
                self._append_output(f"[步骤 3/4] 创建新的 git tag: v{self._current_version}\n")
                tag_process = QProcess(self)
                tag_process.setWorkingDirectory(self._project_root)
                tag_process.readyReadStandardOutput.connect(
                    lambda: self._append_output_with_ansi(tag_process.readAllStandardOutput().data().decode('utf-8', errors='replace'))
                )
                tag_process.readyReadStandardError.connect(
                    lambda: self._append_output_with_ansi(tag_process.readAllStandardError().data().decode('utf-8', errors='replace'))
                )
                
                def on_tag_finished(exit_code, exit_status):
                    if exit_code == 0:
                        self._append_output(f"[完成] Git tag 创建成功\n\n")
                        # 步骤 4: 推送 tag
                        self._append_output(f"[步骤 4/4] 推送 tag 到远程仓库\n")
                        push_tag_process = QProcess(self)
                        push_tag_process.setWorkingDirectory(self._project_root)
                        push_tag_process.readyReadStandardOutput.connect(
                            lambda: self._append_output_with_ansi(push_tag_process.readAllStandardOutput().data().decode('utf-8', errors='replace'))
                        )
                        push_tag_process.readyReadStandardError.connect(
                            lambda: self._append_output_with_ansi(push_tag_process.readAllStandardError().data().decode('utf-8', errors='replace'))
                        )
                        
                        def on_push_tag_finished(exit_code, exit_status):
                            self._is_running = False
                            # 更新版本号显示（从 VersionManager 重新读取）
                            self._current_version = self._version_manager.get_current_version() or "1.0.1"
                            self._update_release_button_text()
                            self.release_btn.setEnabled(True)
                            self.push_btn.setEnabled(True)
                            self.actions_btn.setEnabled(True)
                            
                            self._append_output("\n" + "=" * 80 + "\n")
                            if exit_code == 0:
                                self._append_output(f"[完成] Tag 推送成功，GitHub Actions 将自动开始构建\n")
                                QMessageBox.information(
                                    self,
                                    "发布成功",
                                    f"Release V{self._current_version} 已创建并推送！\n\n"
                                    f"GitHub Actions 将自动开始构建。\n"
                                    f"你可以点击 \"Check Actions\" 按钮查看构建进度。"
                                )
                                # 自动刷新版本列表
                                self.reload_versions()
                            else:
                                self._append_output(f"[错误] Tag 推送失败，退出码: {exit_code}\n")
                                QMessageBox.warning(self, "错误", f"Tag 推送失败，退出码: {exit_code}")
                        
                        push_tag_process.finished.connect(on_push_tag_finished)
                        push_tag_process.start("git", ["push", "origin", f"v{self._current_version}"])
                    else:
                        self._is_running = False
                        # 更新版本号显示
                        self._current_version = self._version_manager.get_current_version() or "1.0.1"
                        self._update_release_button_text()
                        self.release_btn.setEnabled(True)
                        self.push_btn.setEnabled(True)
                        self.actions_btn.setEnabled(True)
                        self._append_output(f"\n[错误] Git tag 创建失败，退出码: {exit_code}\n")
                        QMessageBox.warning(self, "错误", f"Git tag 创建失败，退出码: {exit_code}")
                
                tag_process.finished.connect(on_tag_finished)
                tag_process.start("git", ["tag", "-a", f"v{self._current_version}", "-m", f"Release version {self._current_version}"])
            
            delete_remote_tag_process.finished.connect(on_delete_remote_tag_finished)
            delete_remote_tag_process.start("git", ["push", "origin", f":refs/tags/v{self._current_version}"])
        
        delete_local_tag_process.finished.connect(on_delete_local_tag_finished)
        delete_local_tag_process.start("git", ["tag", "-d", f"v{self._current_version}"])
    
    def _on_check_actions_clicked(self):
        """Check Actions 按钮点击事件：显示 workflows 运行状态"""
        # 从配置读取打包专用的 GitHub 信息
        cfg = ConfigManager.load()
        api_url = cfg.get("packaging_github_api_url", "https://api.github.com").rstrip("/")
        api_key = cfg.get("packaging_github_api_key", "")
        repo_owner = cfg.get("packaging_github_repo_owner", "sanyingkeji")
        repo_name = cfg.get("packaging_github_repo_name", "ai-perf")
        
        if not api_key:
            QMessageBox.warning(
                self,
                "需要配置",
                "请先在 设置 → 打包配置 中配置 GitHub API Key。"
            )
            return
        
        # 获取 GitHub 仓库信息（用于浏览器链接）
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=self._project_root,
                capture_output=True,
                text=True,
                check=True
            )
            remote_url = result.stdout.strip()
            
            if "github.com" in remote_url:
                if remote_url.startswith("git@"):
                    repo_part = remote_url.split(":")[1].replace(".git", "")
                else:
                    repo_part = remote_url.split("github.com/")[1].replace(".git", "")
                
                parsed_owner, parsed_name = repo_part.split("/", 1)
                github_url = f"https://github.com/{parsed_owner}/{parsed_name}"
                actions_url = f"{github_url}/actions"
            else:
                github_url = f"https://github.com/{repo_owner}/{repo_name}"
                actions_url = f"{github_url}/actions"
        except Exception:
            github_url = f"https://github.com/{repo_owner}/{repo_name}"
            actions_url = f"{github_url}/actions"
        
        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("GitHub Actions")
        dialog.resize(900, 600)
        
        # 存储 worker 引用，防止被垃圾回收，并在对话框关闭时清理
        dialog._workers = []
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # 标题和刷新按钮
        header_layout = QHBoxLayout()
        title = QLabel("All Workflows")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(80)
        header_layout.addWidget(refresh_btn)
        layout.addLayout(header_layout)
        
        # 工作流运行状态表格
        table = QTableWidget()
        table.setColumnCount(6)  # 增加一列用于操作按钮
        table.setHorizontalHeaderLabels(["工作流", "状态", "触发事件", "分支/Tag", "时间", "操作"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        # 时间列设置为固定宽度，确保完整显示
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.setColumnWidth(4, 280)  # 设置时间列最小宽度，确保完整显示
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        table.setColumnWidth(5, 100)  # 操作列宽度
        # 启用交替行颜色（已在主题文件中配置适配暗色模式的颜色）
        table.setAlternatingRowColors(True)
        # 设置表格不可选中、不可编辑
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table, 1)
        
        # 加载状态标签
        status_label = QLabel("正在加载...")
        status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(status_label)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        # 打开浏览器按钮
        open_browser_btn = QPushButton("在浏览器中打开")
        open_browser_btn.setFixedWidth(150)
        open_browser_btn.clicked.connect(lambda: webbrowser.open(actions_url))
        btn_layout.addWidget(open_browser_btn)
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
        
        # 加载工作流运行数据
        def load_workflow_runs():
            # 注意：对话框可能还没显示，但不影响加载数据
            # 在对话框显示后，数据会自动填充到表格中
            status_label.setText("正在加载工作流运行状态...")
            table.setRowCount(0)
            
            class _WorkflowRunsWorkerSignals(QObject):
                finished = Signal(list)
                error = Signal(str)
            
            class _WorkflowRunsWorker(QRunnable):
                def __init__(self):
                    super().__init__()
                    self.signals = _WorkflowRunsWorkerSignals()
                    self._dialog = dialog  # 保存对话框引用
                
                @Slot()
                def run(self):
                    try:
                        # 检查对话框是否仍然存在
                        if not hasattr(self._dialog, 'isVisible') or not self._dialog.isVisible():
                            return
                        
                        # 获取工作流运行列表
                        url = f"{api_url}/repos/{repo_owner}/{repo_name}/actions/runs"
                        headers = {
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "aiperf-admin-client/1.0",
                            "Authorization": f"token {api_key}"
                        }
                        
                        response = httpx.get(url, headers=headers, params={"per_page": 20}, timeout=30)
                        
                        # 再次检查对话框是否仍然存在
                        if not hasattr(self._dialog, 'isVisible') or not self._dialog.isVisible():
                            return
                        
                        if response.status_code == 200:
                            data = response.json()
                            runs = data.get("workflow_runs", [])
                            # 再次检查对话框是否仍然存在
                            try:
                                if self._dialog.isVisible():
                                    self.signals.finished.emit(runs)
                            except RuntimeError:
                                # 对话框已被删除，不发送信号
                                return
                        elif response.status_code == 401:
                            try:
                                if self._dialog.isVisible():
                                    self.signals.error.emit("GitHub API 认证失败，请检查 API Key")
                            except RuntimeError:
                                return
                        elif response.status_code == 404:
                            try:
                                if self._dialog.isVisible():
                                    self.signals.error.emit(f"仓库不存在或无权访问：{repo_owner}/{repo_name}")
                            except RuntimeError:
                                return
                        else:
                            try:
                                if self._dialog.isVisible():
                                    self.signals.error.emit(f"获取工作流运行失败：HTTP {response.status_code}")
                            except RuntimeError:
                                return
                    except Exception as e:
                        # 检查对话框是否仍然存在
                        try:
                            if hasattr(self._dialog, 'isVisible') and self._dialog.isVisible():
                                self.signals.error.emit(f"获取工作流运行失败：{e}")
                        except RuntimeError:
                            # 对话框已被删除，不发送信号
                            pass
            
            def on_loaded(runs):
                # 检查对话框是否仍然存在
                try:
                    if not dialog.isVisible():
                        return
                except RuntimeError:
                    # 对话框已被删除
                    return
                
                try:
                    table.setRowCount(len(runs))
                    
                    for row, run in enumerate(runs):
                        # 工作流名称
                        workflow_name = run.get("name", "Unknown")
                        name_item = QTableWidgetItem(workflow_name)
                        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
                        table.setItem(row, 0, name_item)
                        
                        # 状态
                        status = run.get("status", "unknown")
                        conclusion = run.get("conclusion", "")
                        
                        if status == "completed":
                            if conclusion == "success":
                                status_text = "✓ 成功"
                                status_color = QColor("#0DBC79")
                            elif conclusion == "failure":
                                status_text = "✗ 失败"
                                status_color = QColor("#CD3131")
                            elif conclusion == "cancelled":
                                status_text = "⊘ 已取消"
                                status_color = QColor("#767676")
                            else:
                                status_text = f"完成 ({conclusion})"
                                status_color = QColor("#E5E510")
                        elif status == "in_progress":
                            status_text = "⟳ 运行中"
                            status_color = QColor("#2472C8")
                        elif status == "queued":
                            status_text = "⏳ 排队中"
                            status_color = QColor("#E5E510")
                        else:
                            status_text = status
                            status_color = QColor("#767676")
                        
                        status_item = QTableWidgetItem(status_text)
                        status_item.setForeground(status_color)
                        status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
                        table.setItem(row, 1, status_item)
                        
                        # 触发事件
                        event = run.get("event", "unknown")
                        event_item = QTableWidgetItem(event)
                        event_item.setFlags(event_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
                        table.setItem(row, 2, event_item)
                        
                        # 分支/Tag
                        head_branch = run.get("head_branch", "")
                        head_branch_item = QTableWidgetItem(head_branch or "N/A")
                        head_branch_item.setFlags(head_branch_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
                        table.setItem(row, 3, head_branch_item)
                        
                        # 时间
                        created_at = run.get("created_at", "")
                        updated_at = run.get("updated_at", "")
                        if created_at:
                            try:
                                from datetime import datetime
                                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                                
                                # 如果已完成，显示运行时长
                                if status == "completed" and updated_at:
                                    end_dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                                    duration = end_dt - dt
                                    total_seconds = int(duration.total_seconds())
                                    hours = total_seconds // 3600
                                    minutes = (total_seconds % 3600) // 60
                                    seconds = total_seconds % 60
                                    
                                    if hours > 0:
                                        duration_str = f"{hours}小时{minutes}分钟{seconds}秒"
                                    elif minutes > 0:
                                        duration_str = f"{minutes}分钟{seconds}秒"
                                    else:
                                        duration_str = f"{seconds}秒"
                                    
                                    time_str += f" (运行 {duration_str})"
                            except:
                                time_str = created_at[:19] if len(created_at) >= 19 else created_at
                        else:
                            time_str = "N/A"
                        
                        time_item = QTableWidgetItem(time_str)
                        time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
                        table.setItem(row, 4, time_item)
                        
                        # 操作按钮（查看日志和 Re-run）
                        run_id = run.get("id")
                        run_url = run.get("html_url", "")
                        workflow_id = run.get("workflow_id")
                        
                        # 创建按钮容器
                        btn_widget = QWidget()
                        btn_layout = QHBoxLayout(btn_widget)
                        btn_layout.setContentsMargins(2, 2, 2, 2)
                        btn_layout.setSpacing(4)
                        
                        # 查看日志按钮（mini 按钮样式，与健康检查一致）
                        view_logs_btn = QPushButton("查看日志")
                        view_logs_btn.setFixedSize(70, 22)  # 宽度稍大以适应"查看日志"文本
                        view_logs_btn.setStyleSheet("font-size: 9pt; padding: 0px;")
                        view_logs_btn.clicked.connect(lambda checked, rid=run_id, rurl=run_url: self._view_workflow_logs(rid, rurl, api_url, api_key, repo_owner, repo_name))
                        btn_layout.addWidget(view_logs_btn)
                        
                        # Re-run 按钮（只有已完成的工作流才能重新运行）
                        if status == "completed":
                            rerun_btn = QPushButton("Re-run")
                            rerun_btn.setFixedSize(60, 22)  # mini 按钮样式，与健康检查一致
                            rerun_btn.setStyleSheet("font-size: 9pt; padding: 0px;")
                            rerun_btn.clicked.connect(lambda checked, rid=run_id, wid=workflow_id: self._rerun_workflow(rid, wid, api_url, api_key, repo_owner, repo_name, load_workflow_runs))
                            btn_layout.addWidget(rerun_btn)
                        
                        btn_layout.addStretch()
                        table.setCellWidget(row, 5, btn_widget)
                    
                    try:
                        status_label.setText(f"已加载 {len(runs)} 个工作流运行")
                    except RuntimeError:
                        # 对话框已被删除，忽略
                        pass
                except RuntimeError:
                    # 对话框已被删除，忽略
                    return
            
            def on_error(error_msg):
                # 检查对话框是否仍然存在
                try:
                    if not dialog.isVisible():
                        return
                    status_label.setText(f"错误: {error_msg}")
                    QMessageBox.warning(dialog, "错误", error_msg)
                except RuntimeError:
                    # 对话框已被删除，忽略
                    pass
            
            worker = _WorkflowRunsWorker()
            worker.signals.finished.connect(on_loaded)
            worker.signals.error.connect(on_error)
            # 保存 worker 引用，防止被垃圾回收
            dialog._workers.append(worker)
            QThreadPool.globalInstance().start(worker)
        
        # 刷新按钮
        refresh_btn.clicked.connect(load_workflow_runs)
        
        # 对话框关闭时清理 worker
        def on_dialog_finished():
            # 断开所有 worker 的信号连接
            for worker in dialog._workers:
                try:
                    if hasattr(worker, 'signals'):
                        worker.signals.finished.disconnect()
                        worker.signals.error.disconnect()
                except:
                    pass
            dialog._workers.clear()
        
        dialog.finished.connect(on_dialog_finished)
        
        # 在对话框显示后立即加载数据
        # 使用 QTimer.singleShot 确保对话框完全显示后再加载
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, load_workflow_runs)
        
        dialog.exec()
    
    def _update_release_button_text(self):
        """更新 Release 按钮文本（使用 VersionManager 获取最新版本号）"""
        if not hasattr(self, 'release_btn'):
            return
        self._current_version = self._version_manager.get_current_version() or "1.0.1"
        self.release_btn.setText(f"Release V{self._current_version}")
    
    def _on_version_management_clicked(self):
        """版本管理按钮点击事件：打开版本号统一管理对话框"""
        from windows.version_management_dialog import VersionManagementDialog
        
        dialog = VersionManagementDialog(self, self._version_manager)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 如果版本号已更新，刷新 Release 按钮文本
            self._update_release_button_text()
            QMessageBox.information(
                self,
                "版本更新成功",
                "版本号已统一更新到所有相关文件。\n\n"
                "请重新运行打包流程以使用新版本号。"
            )
    
    def _view_workflow_logs(self, run_id: int, run_url: str, api_url: str, api_key: str, repo_owner: str, repo_name: str):
        """查看工作流运行的日志"""
        # 创建日志查看对话框
        log_dialog = QDialog(self)
        log_dialog.setWindowTitle(f"工作流运行日志 - #{run_id}")
        log_dialog.resize(1000, 700)
        
        layout = QVBoxLayout(log_dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # 标题和按钮
        header_layout = QHBoxLayout()
        title = QLabel(f"工作流运行 #{run_id} 日志")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # 在浏览器中打开按钮
        open_browser_btn = QPushButton("在浏览器中打开")
        open_browser_btn.setFixedWidth(150)
        open_browser_btn.clicked.connect(lambda: webbrowser.open(run_url))
        header_layout.addWidget(open_browser_btn)
        
        # 刷新按钮
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(80)
        header_layout.addWidget(refresh_btn)
        
        layout.addLayout(header_layout)
        
        # 日志显示区域
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_text.setFont(QFont("Monaco", 10) if sys.platform == "darwin" else QFont("Consolas", 10))
        log_text.setPlaceholderText("正在加载日志...")
        layout.addWidget(log_text, 1)
        
        # 状态标签
        status_label = QLabel("正在加载日志...")
        status_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(status_label)
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(log_dialog.close)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        def load_logs():
            status_label.setText("正在下载日志...")
            log_text.clear()
            log_text.setPlaceholderText("正在下载日志...")
            
            class _LogsWorkerSignals(QObject):
                finished = Signal(str)
                error = Signal(str)
            
            class _LogsWorker(QRunnable):
                def __init__(self):
                    super().__init__()
                    self.signals = _LogsWorkerSignals()
                
                @Slot()
                def run(self):
                    try:
                        # 获取日志下载 URL
                        logs_url = f"{api_url}/repos/{repo_owner}/{repo_name}/actions/runs/{run_id}/logs"
                        headers = {
                            "Accept": "application/vnd.github+json",
                            "User-Agent": "aiperf-admin-client/1.0",
                            "Authorization": f"token {api_key}"
                        }
                        
                        # 下载日志 ZIP 文件（GitHub API 会返回重定向）
                        response = httpx.get(logs_url, headers=headers, follow_redirects=True, timeout=60)
                        
                        if response.status_code == 404:
                            self.signals.error.emit("日志不存在或已过期（GitHub 日志通常保留 90 天）")
                            return
                        elif response.status_code != 200:
                            self.signals.error.emit(f"下载日志失败：HTTP {response.status_code}")
                            return
                        
                        # 检查是否是 ZIP 文件
                        content_type = response.headers.get("content-type", "")
                        if "zip" not in content_type.lower() and not response.content.startswith(b"PK"):
                            # 如果不是 ZIP，可能是纯文本日志
                            log_content = response.text
                            self.signals.finished.emit(log_content)
                            return
                        
                        # 解压 ZIP 文件
                        zip_data = io.BytesIO(response.content)
                        with zipfile.ZipFile(zip_data, 'r') as zip_ref:
                            # 获取所有文件列表
                            file_list = zip_ref.namelist()
                            
                            # 按文件名排序（通常是按 job 名称和时间）
                            file_list.sort()
                            
                            # 合并所有日志文件
                            all_logs = []
                            for file_name in file_list:
                                if file_name.endswith('.txt') or not '.' in file_name.split('/')[-1]:
                                    # 读取日志文件内容
                                    try:
                                        content = zip_ref.read(file_name).decode('utf-8', errors='replace')
                                        # 添加文件头
                                        all_logs.append(f"\n{'='*80}\n")
                                        all_logs.append(f"文件: {file_name}\n")
                                        all_logs.append(f"{'='*80}\n\n")
                                        all_logs.append(content)
                                        if not content.endswith('\n'):
                                            all_logs.append('\n')
                                    except Exception as e:
                                        all_logs.append(f"\n{'='*80}\n")
                                        all_logs.append(f"文件: {file_name} (读取失败: {e})\n")
                                        all_logs.append(f"{'='*80}\n\n")
                            
                            if all_logs:
                                log_content = ''.join(all_logs)
                            else:
                                log_content = "日志文件为空或格式不正确"
                            
                            self.signals.finished.emit(log_content)
                    
                    except httpx.HTTPError as e:
                        self.signals.error.emit(f"网络错误：{e}")
                    except zipfile.BadZipFile:
                        # 如果不是 ZIP 文件，尝试作为文本处理
                        try:
                            if 'response' in locals():
                                log_content = response.text
                                self.signals.finished.emit(log_content)
                            else:
                                self.signals.error.emit("日志格式错误：无法解析 ZIP 文件")
                        except:
                            self.signals.error.emit("日志格式错误：无法解析 ZIP 文件")
                    except Exception as e:
                        self.signals.error.emit(f"加载日志失败：{e}")
            
            def on_logs_loaded(log_content):
                log_text.setPlainText(log_content)
                status_label.setText(f"日志已加载（{len(log_content)} 字符）")
                # 滚动到底部（显示最新的日志）
                cursor = log_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                log_text.setTextCursor(cursor)
            
            def on_logs_error(error_msg):
                status_label.setText(f"错误: {error_msg}")
                log_text.setPlainText(f"错误: {error_msg}\n\n请检查：\n1. GitHub API Key 是否有足够权限\n2. 日志是否已过期（GitHub 日志通常保留 90 天）\n3. 网络连接是否正常")
                QMessageBox.warning(log_dialog, "加载日志失败", error_msg)
            
            worker = _LogsWorker()
            worker.signals.finished.connect(on_logs_loaded)
            worker.signals.error.connect(on_logs_error)
            QThreadPool.globalInstance().start(worker)
        
        # 刷新按钮
        refresh_btn.clicked.connect(load_logs)
        
        # 初始加载
        load_logs()
        
        log_dialog.exec()
    
    def _rerun_workflow(self, run_id: int, workflow_id: int, api_url: str, api_key: str, repo_owner: str, repo_name: str, refresh_callback=None):
        """重新运行工作流"""
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认重新运行",
            f"确定要重新运行工作流运行 #{run_id} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 显示进度提示
        progress = QProgressDialog("正在重新运行工作流...", "取消", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)  # 不允许取消
        progress.show()
        
        class _RerunWorkerSignals(QObject):
            finished = Signal(bool, str)  # success, message
        
        class _RerunWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _RerunWorkerSignals()
            
            @Slot()
            def run(self):
                try:
                    # 调用 GitHub API 重新运行工作流
                    rerun_url = f"{api_url}/repos/{repo_owner}/{repo_name}/actions/runs/{run_id}/rerun"
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                        "Authorization": f"token {api_key}"
                    }
                    
                    # POST 请求重新运行
                    response = httpx.post(rerun_url, headers=headers, timeout=30)
                    
                    if response.status_code == 201:
                        self.signals.finished.emit(True, "工作流已成功重新运行")
                    elif response.status_code == 403:
                        self.signals.finished.emit(False, "权限不足，无法重新运行工作流。请检查 API Key 权限。")
                    elif response.status_code == 409:
                        self.signals.finished.emit(False, "工作流正在运行中，无法重新运行。")
                    else:
                        error_msg = f"重新运行失败：HTTP {response.status_code}"
                        try:
                            error_data = response.json()
                            if "message" in error_data:
                                error_msg += f" - {error_data['message']}"
                        except:
                            pass
                        self.signals.finished.emit(False, error_msg)
                except httpx.HTTPError as e:
                    self.signals.finished.emit(False, f"网络错误：{e}")
                except Exception as e:
                    self.signals.finished.emit(False, f"重新运行失败：{e}")
        
        def on_rerun_finished(success: bool, message: str):
            progress.close()
            if success:
                QMessageBox.information(self, "成功", message)
                # 刷新工作流列表
                if refresh_callback:
                    refresh_callback()
            else:
                QMessageBox.warning(self, "失败", message)
        
        worker = _RerunWorker()
        worker.signals.finished.connect(on_rerun_finished)
        QThreadPool.globalInstance().start(worker)

