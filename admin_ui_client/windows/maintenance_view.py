#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日常运维页面：
所有功能均通过SSH连接远程服务器获取数据，不依赖后端API接口。

- 系统设置（TAB 1）：通过SSH管理服务和定时任务，获取服务器运行状况
- 数据库备份（TAB 2）：通过SSH获取备份文件列表并支持下载
- 日志查看（TAB 3）：通过SSH获取日志文件列表，支持查看和下载
"""

from typing import Dict, Any, List, Optional, Tuple
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
    QTableWidgetItem, QFrame, QTabWidget, QTabBar, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QLineEdit, QCheckBox, QTextEdit, QPlainTextEdit,
    QSplitter, QComboBox, QProgressDialog, QDialog, QListWidget,
    QListWidgetItem, QProgressBar, QMenu, QStyle
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
            # 或者：
            # n/a                          n/a          n/a                         n/a          ai-perf-health-check.timer ai-perf-health-check.service
            if len(lines) > 2:
                # 查找timer行（从第3行开始，跳过标题）
                for line in lines[2:]:
                    if timer_name in line:
                        # 检查是否是n/a的情况
                        if "n/a" in line.lower() and line.lower().count("n/a") >= 3:
                            # 所有时间都是n/a
                            return {
                                "enabled": is_enabled,
                                "last_run": None,
                                "next_run": None,
                            }
                        
                        # 使用正则表达式匹配日期时间格式：Mon 2025-01-13 09:00:00 CST
                        # 匹配格式：星期 日期 时间 时区
                        date_pattern = r'(\w{3}\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w{3})'
                        dates = re.findall(date_pattern, line)
                        
                        if len(dates) >= 2:
                            # 第一个是 next_run，第二个是 last_run
                            next_run = dates[0].strip()
                            last_run = dates[1].strip()
                            return {
                                "enabled": is_enabled,
                                "last_run": last_run,
                                "next_run": next_run,
                            }
                        elif len(dates) == 1:
                            # 只有一个日期，需要判断是next还是last
                            # 根据systemctl list-timers的输出格式，第一列是NEXT，第三列是LAST
                            # 查找日期在行中的位置，以及n/a的位置
                            date_pos = line.find(dates[0])
                            n_a_pos = line.lower().find("n/a")
                            
                            if n_a_pos != -1:
                                if n_a_pos < date_pos:
                                    # n/a在日期之前，说明NEXT是n/a，日期是LAST
                                    return {
                                        "enabled": is_enabled,
                                        "last_run": dates[0].strip(),
                                        "next_run": None,
                                    }
                                else:
                                    # n/a在日期之后，说明LAST是n/a，日期是NEXT
                                    return {
                                        "enabled": is_enabled,
                                        "last_run": None,
                                        "next_run": dates[0].strip(),
                                    }
                            else:
                                # 没有n/a，可能是格式问题，假设第一个是next_run
                                return {
                                    "enabled": is_enabled,
                                    "last_run": None,
                                    "next_run": dates[0].strip(),
                                }
            
            return {"enabled": is_enabled, "last_run": None, "next_run": None}
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"解析timer状态失败: {e}, timer_name: {timer_name}")
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
        
        ssh = None
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
            
            # ⚠️ 备份文件通常较大，不能用 `cat` 走 stdout 拉取（容易阻塞/内存暴涨）
            # 这里改为 SFTP 直连下载，避免“正在下载...”遮罩层一直不消失的问题
            backup_dir = self._backup_dir.rstrip("/")
            filename = self._filename.lstrip("/")
            remote_path = f"{backup_dir}/{filename}"
            result = ssh.download_file(remote_path, self._save_path)
            
            if not result.get("success"):
                error_msg = result.get("error") or "下载备份文件失败"
                self.signals.error.emit(error_msg)
                return
            
            self.signals.finished.emit(self._save_path)
        except Exception as e:
            self.signals.error.emit(f"下载备份文件失败：{e}")
        finally:
            try:
                if ssh:
                    ssh.close()
            except Exception:
                pass


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
            download_btn.clicked.connect(lambda *_, f=filename: self._on_download_clicked(f))
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
        self.cron_jobs_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)  # 操作列固定宽度
        self.cron_jobs_table.setColumnWidth(5, 280)  # 设置操作列宽度（3个按钮 + 间距）
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
                stop_btn.clicked.connect(lambda *_, s=service_name: self._on_control_clicked(s, "stop"))
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
                restart_btn.clicked.connect(lambda *_, s=service_name: self._on_control_clicked(s, "restart"))
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
                start_btn.clicked.connect(lambda *_, s=service_name: self._on_control_clicked(s, "start"))
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
                disable_btn.clicked.connect(lambda *_, s=service_name: self._on_control_clicked(s, "disable"))
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
                enable_btn.clicked.connect(lambda *_, s=service_name: self._on_control_clicked(s, "enable"))
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
                    disable_btn.clicked.connect(lambda *_, j=job_name: self._on_cron_job_control_clicked(j, "disable"))
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
                    enable_btn.clicked.connect(lambda *_, j=job_name: self._on_cron_job_control_clicked(j, "enable"))
                    btn_layout.addWidget(enable_btn)
                
                # 立即执行按钮
                run_now_btn = QPushButton("立即执行")
                run_now_btn.setFixedSize(70, 24)
                run_now_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #007bff;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 9pt;
                    }
                    QPushButton:hover {
                        background-color: #0056b3;
                    }
                """)
                run_now_btn.clicked.connect(lambda *_, j=job_name: self._on_run_timer_now_clicked(j))
                btn_layout.addWidget(run_now_btn)
                
                # 查看命令按钮
                view_cmd_btn = QPushButton("查看命令")
                view_cmd_btn.setFixedSize(70, 24)
                view_cmd_btn.setStyleSheet("""
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
                view_cmd_btn.clicked.connect(lambda *_, j=job_name: self._on_view_timer_command_clicked(j))
                btn_layout.addWidget(view_cmd_btn)
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

    def _on_run_timer_now_clicked(self, timer_name: str):
        """立即执行定时任务对应的 service"""
        # 获取对应的 service 名称（去掉 .timer 后缀，加上 .service）
        service_name = timer_name.replace(".timer", ".service")
        
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
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认执行",
            f"确定要立即执行定时任务 {timer_name} 对应的服务 {service_name} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 由于 systemctl start 是异步的，启动命令会立即返回，不需要等待任务完成
        # 因此不显示遮盖层，避免阻塞用户操作，只显示简短的Toast提示
        Toast.show_message(self, f"正在启动服务 {service_name}...")
        
        # 后台执行（通过SSH）
        def on_finished(job_name):
            # 命令发送成功，任务已在后台开始执行
            self._on_run_timer_now_finished(service_name)
        
        def on_error(job_name, error_msg):
            # 命令发送失败
            self._on_run_timer_now_error(service_name, error_msg)
        
        worker = _CronJobControlWorker(service_name, "start", ssh_config)
        worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(on_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_run_timer_now_finished(self, service_name: str):
        """立即执行完成（命令发送成功）"""
        Toast.show_message(self, f"服务 {service_name} 已开始执行")
    
    def _on_run_timer_now_error(self, service_name: str, error_msg: str):
        """立即执行失败（命令发送失败）"""
        Toast.show_message(self, f"执行服务 {service_name} 失败：{error_msg}")
    
    def _on_view_timer_command_clicked(self, timer_name: str):
        """查看定时任务对应的 service 命令"""
        # 获取对应的 service 名称（去掉 .timer 后缀，加上 .service）
        service_name = timer_name.replace(".timer", ".service")
        
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
        
        # 显示loading
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在获取服务 {service_name} 的命令...")
        
        # 后台获取命令
        class _ViewCommandWorkerSignals(QObject):
            finished = Signal(str, str)  # service_name, command_info
            error = Signal(str, str)  # service_name, error_msg
        
        class _ViewCommandWorker(QRunnable):
            def __init__(self, service_name: str, ssh_config: Dict[str, Any]):
                super().__init__()
                self.signals = _ViewCommandWorkerSignals()
                self._service_name = service_name
                self._ssh_config = ssh_config
            
            @Slot()
            def run(self) -> None:
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
                    
                    # 获取 service 的完整配置（包括 ExecStart 等）
                    result = ssh.execute(f"systemctl cat {self._service_name}", sudo=False)
                    ssh.close()
                    
                    if result["success"]:
                        service_content = result.get("stdout", "")
                        self.signals.finished.emit(self._service_name, service_content)
                    else:
                        # 如果 cat 失败，尝试使用 show 命令获取 ExecStart
                        ssh2 = SSHClient(
                            host=self._ssh_config["host"],
                            port=self._ssh_config.get("port", 22),
                            username=self._ssh_config["username"],
                            password=self._ssh_config.get("password"),
                            key_path=self._ssh_config.get("key_path")
                        )
                        if ssh2.connect():
                            result2 = ssh2.execute(f"systemctl show {self._service_name} -p ExecStart -p ExecStartPre -p ExecStartPost -p ExecStop", sudo=False)
                            ssh2.close()
                            if result2["success"]:
                                self.signals.finished.emit(self._service_name, result2.get("stdout", ""))
                            else:
                                error_msg = result2.get("stderr") or result2.get("error") or "获取命令失败"
                                self.signals.error.emit(self._service_name, error_msg)
                        else:
                            self.signals.error.emit(self._service_name, "SSH连接失败")
                except Exception as e:
                    self.signals.error.emit(self._service_name, f"获取命令失败：{e}")
        
        def on_command_loaded(service_name: str, command_info: str):
            win = self.window()
            hide_loading = getattr(win, "hide_loading", None)
            if callable(hide_loading):
                hide_loading()
            
            # 创建对话框显示命令
            dialog = QDialog(self)
            dialog.setWindowTitle(f"服务命令 - {service_name}")
            dialog.resize(800, 600)
            
            layout = QVBoxLayout(dialog)
            layout.setSpacing(12)
            layout.setContentsMargins(16, 16, 16, 16)
            
            # 标题
            title = QLabel(f"服务：{service_name}")
            title.setFont(QFont("Arial", 12, QFont.Bold))
            layout.addWidget(title)
            
            # 命令内容
            command_text = QTextEdit()
            command_text.setReadOnly(True)
            command_text.setFont(QFont("Monaco", 10) if sys.platform == "darwin" else QFont("Consolas", 10))
            command_text.setPlainText(command_info)
            layout.addWidget(command_text, 1)
            
            # 关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.setFixedWidth(100)
            close_btn.clicked.connect(dialog.close)
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            btn_layout.addWidget(close_btn)
            layout.addLayout(btn_layout)
            
            dialog.exec()
        
        def on_command_error(service_name: str, error_msg: str):
            win = self.window()
            hide_loading = getattr(win, "hide_loading", None)
            if callable(hide_loading):
                hide_loading()
            
            QMessageBox.warning(self, "获取命令失败", f"无法获取服务 {service_name} 的命令：\n{error_msg}")
        
        worker = _ViewCommandWorker(service_name, ssh_config)
        worker.signals.finished.connect(on_command_loaded)
        worker.signals.error.connect(on_command_error)
        QThreadPool.globalInstance().start(worker)

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
        
        # 头部：脚本路径选择和执行按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        
        # 脚本路径标签
        script_label = QLabel("脚本路径：")
        script_label.setFont(QFont("Arial", 10))
        header_layout.addWidget(script_label)
        
        # 脚本路径输入框
        self.script_path_input = QLineEdit()
        self.script_path_input.setText(self._deploy_script_path)
        self.script_path_input.setFont(QFont("Courier New", 10))
        self.script_path_input.setStyleSheet("""
            QLineEdit {
                padding: 4px 8px;
                border: 1px solid #ccc;
                border-radius: 3px;
            }
        """)
        header_layout.addWidget(self.script_path_input, 1)  # stretch factor = 1，填充剩余空间
        
        # 浏览按钮
        browse_btn = QPushButton("浏览")
        browse_btn.setFixedWidth(80)
        browse_btn.setFixedHeight(28)
        browse_btn.clicked.connect(self._on_browse_script)
        header_layout.addWidget(browse_btn)
        
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
                font-family: "Menlo", "Monaco", "Courier New";
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
    
    def _on_browse_script(self):
        """浏览脚本文件"""
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择脚本文件",
            str(project_root),
            "Shell Scripts (*.sh);;All Files (*)"
        )
        
        if file_path:
            self.script_path_input.setText(file_path)
            # 更新工作目录为脚本所在目录
            script_path = Path(file_path)
            self._working_dir = script_path.parent
    
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
        
        # 从输入框获取脚本路径
        script_path = self.script_path_input.text().strip()
        if not script_path:
            QMessageBox.warning(
                self,
                "错误",
                "请输入脚本路径"
            )
            return
        
        # 检查脚本是否存在
        if not os.path.exists(script_path):
            QMessageBox.warning(
                self,
                "脚本不存在",
                f"部署脚本不存在：\n{script_path}"
            )
            return
        
        # 更新工作目录为脚本所在目录
        script_path_obj = Path(script_path)
        self._working_dir = script_path_obj.parent
        
        # 清空输出
        self.output_text.clear()
        
        # 显示头部信息
        header_text = f"$ sh {script_path}\n"
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
        self._process.start("bash", [script_path])
    
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


class EnvConfigTab(QWidget):
    """环境配置 TAB：读取和编辑服务端 /ai-perf/.env 配置文件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._env_content = ""
        self._is_loading = False
        self._is_saving = False
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 头部：刷新和保存按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)
        
        # 文件路径标签
        path_label = QLabel("文件路径：/ai-perf/.env")
        path_label.setFont(QFont("Arial", 10))
        header_layout.addWidget(path_label)
        header_layout.addStretch()
        
        # 刷新按钮
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.setFixedHeight(28)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        header_layout.addWidget(self.refresh_btn)
        
        # 保存按钮
        self.save_btn = QPushButton("保存")
        self.save_btn.setFixedWidth(100)
        self.save_btn.setFixedHeight(28)
        self.save_btn.clicked.connect(self._on_save_clicked)
        header_layout.addWidget(self.save_btn)
        
        layout.addLayout(header_layout)
        
        # 编辑区域（使用与发布TAB相同的样式）
        self.content_text = QTextEdit()
        self.content_text.setReadOnly(False)  # 可编辑
        
        # 设置苹果终端 Basic 主题的字体
        # 优先使用 Menlo，然后是 Monaco，最后是 Courier New
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setFixedPitch(True)
            # 检查字体是否可用
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        if font:
            self.content_text.setFont(font)
        
        # 设置 tab 宽度（4个空格）
        self.content_text.setTabStopDistance(4 * self.content_text.fontMetrics().averageCharWidth())
        
        # 苹果终端 Basic 主题默认样式（完全匹配发布TAB）
        self.content_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #FFFFFF;
                border: none;
                padding: 10px;
                selection-background-color: #0066CC;
                selection-color: #FFFFFF;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12pt;
                line-height: 1.2;
            }
        """)
        
        # 默认文本格式（白色）
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("#FFFFFF"))
        if font:
            self._default_format.setFont(font)
        self.content_text.setPlaceholderText("点击\"刷新\"按钮加载.env配置文件...")
        layout.addWidget(self.content_text, 1)  # stretch factor = 1，填充剩余空间
    
    def _on_refresh_clicked(self):
        """刷新按钮点击事件"""
        if self._is_loading:
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
            QMessageBox.warning(
                self,
                "配置错误",
                "请先配置SSH服务器信息（在系统概览TAB中配置）"
            )
            return
        
        # 显示loading
        self._is_loading = True
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("加载中...")
        self.content_text.setPlaceholderText("正在从服务器加载.env配置文件...")
        
        # 在后台线程中读取文件
        worker = _EnvFileWorker(ssh_config, "read", None)
        worker.signals.finished.connect(self._on_load_finished)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_load_finished(self, content: str):
        """加载完成"""
        self._is_loading = False
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新")
        self._env_content = content
        self.content_text.setPlainText(content)
        self.content_text.setPlaceholderText("")
    
    def _on_load_error(self, error_msg: str):
        """加载失败"""
        self._is_loading = False
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新")
        self.content_text.setPlaceholderText("加载失败，请重试")
        QMessageBox.warning(
            self,
            "加载失败",
            f"无法加载.env配置文件：\n{error_msg}"
        )
    
    def _on_save_clicked(self):
        """保存按钮点击事件"""
        if self._is_saving:
            return
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认保存",
            "确定要保存对.env配置文件的修改吗？\n\n注意：保存后配置将立即生效。",
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
            QMessageBox.warning(
                self,
                "配置错误",
                "请先配置SSH服务器信息（在系统概览TAB中配置）"
            )
            return
        
        # 获取编辑后的内容
        content = self.content_text.toPlainText()
        
        # 显示loading
        self._is_saving = True
        self.save_btn.setEnabled(False)
        self.save_btn.setText("保存中...")
        
        # 在后台线程中保存文件
        worker = _EnvFileWorker(ssh_config, "write", content)
        worker.signals.finished.connect(self._on_save_finished)
        worker.signals.error.connect(self._on_save_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_save_finished(self):
        """保存完成"""
        self._is_saving = False
        self.save_btn.setEnabled(True)
        self.save_btn.setText("保存")
        
        # 保存成功后，自动重启三个HTTP服务
        self._restart_http_services()
    
    def _restart_http_services(self):
        """重启三个HTTP服务以使新配置生效"""
        # 三个HTTP服务名称
        http_services = [
            "ai-perf-api",           # 用户端API服务
            "ai-perf-admin-api",      # 管理端API服务（端口8880）
            "ai-perf-upload",         # 文件上传服务（端口8882）
        ]
        
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
            Toast.show_message(self, ".env配置文件已保存，但无法重启服务（SSH配置未设置）")
            return
        
        # 显示提示
        Toast.show_message(self, ".env配置文件已保存，正在重启HTTP服务...")
        
        # 在后台线程中重启服务
        worker = _RestartServicesWorker(ssh_config, http_services)
        worker.signals.finished.connect(self._on_restart_services_finished)
        worker.signals.error.connect(self._on_restart_services_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_restart_services_finished(self, success_count: int, total_count: int):
        """重启服务完成"""
        if success_count == total_count:
            Toast.show_message(self, f".env配置文件已保存，{total_count}个HTTP服务已重启")
        else:
            Toast.show_message(self, f".env配置文件已保存，{success_count}/{total_count}个HTTP服务重启成功")
    
    def _on_restart_services_error(self, error_msg: str):
        """重启服务失败"""
        Toast.show_message(self, f".env配置文件已保存，但重启服务时出错：{error_msg}")
    
    def _on_save_error(self, error_msg: str):
        """保存失败"""
        self._is_saving = False
        self.save_btn.setEnabled(True)
        self.save_btn.setText("保存")
        QMessageBox.warning(
            self,
            "保存失败",
            f"无法保存.env配置文件：\n{error_msg}"
        )


class _EnvFileWorkerSignals(QObject):
    """环境文件操作信号"""
    finished = Signal(str)  # 读取完成时发送内容
    error = Signal(str)  # 错误时发送错误消息


class _RestartServicesWorkerSignals(QObject):
    """重启服务信号"""
    finished = Signal(int, int)  # 成功数量, 总数量
    error = Signal(str)  # 错误消息


class _EnvFileWorker(QRunnable):
    """后台线程：通过SSH命令读取或写入.env文件"""
    def __init__(self, ssh_config: dict, operation: str, content: Optional[str] = None):
        super().__init__()
        self.signals = _EnvFileWorkerSignals()
        self._ssh_config = ssh_config
        self._operation = operation  # "read" 或 "write"
        self._content = content  # 写入时的内容
    
    @Slot()
    def run(self) -> None:
        try:
            from utils.ssh_client import SSHClient
            
            ssh = SSHClient(**self._ssh_config)
            if not ssh.connect():
                self.signals.error.emit("SSH 连接失败")
                return
            
            remote_path = "/ai-perf/.env"
            
            if self._operation == "read":
                # 使用cat命令读取文件
                result = ssh.execute(f"cat {remote_path}")
                ssh.close()
                
                if not result.get("success"):
                    error_msg = result.get("stderr", result.get("error", "未知错误"))
                    self.signals.error.emit(error_msg)
                    return
                
                content = result.get("stdout", "")
                self.signals.finished.emit(content)
            
            elif self._operation == "write":
                # 使用cat配合heredoc写入文件
                # 将内容进行base64编码，避免特殊字符和换行符问题
                import base64
                
                # 将内容进行base64编码
                content_encoded = base64.b64encode(self._content.encode('utf-8')).decode('ascii')
                
                # 先写入临时文件，然后移动到目标位置（更安全，避免写入过程中文件损坏）
                temp_file = f"/tmp/.env.tmp.{os.getpid()}"
                # 使用base64解码并写入临时文件，然后移动到目标位置
                cmd = f"echo '{content_encoded}' | base64 -d > {temp_file} && mv {temp_file} {remote_path}"
                
                result = ssh.execute(cmd, sudo=True)  # 可能需要sudo权限
                ssh.close()
                
                if not result.get("success"):
                    error_msg = result.get("stderr", result.get("error", "未知错误"))
                    self.signals.error.emit(error_msg)
                    return
                
                self.signals.finished.emit("")
            
        except Exception as e:
            self.signals.error.emit(f"操作失败: {e}")


class _RestartServicesWorker(QRunnable):
    """后台线程：重启多个HTTP服务"""
    def __init__(self, ssh_config: dict, service_names: List[str]):
        super().__init__()
        self.signals = _RestartServicesWorkerSignals()
        self._ssh_config = ssh_config
        self._service_names = service_names
    
    @Slot()
    def run(self) -> None:
        try:
            from utils.ssh_client import SSHClient
            
            ssh = SSHClient(**self._ssh_config)
            if not ssh.connect():
                self.signals.error.emit("SSH 连接失败")
                return
            
            success_count = 0
            total_count = len(self._service_names)
            errors = []
            
            # 依次重启每个服务
            for service_name in self._service_names:
                result = ssh.execute(f"systemctl restart {service_name}", sudo=True)
                if result.get("success"):
                    success_count += 1
                else:
                    error_msg = result.get("stderr", result.get("error", "未知错误"))
                    errors.append(f"{service_name}: {error_msg}")
            
            ssh.close()
            
            if errors:
                error_msg = "; ".join(errors)
                if success_count > 0:
                    # 部分成功
                    self.signals.finished.emit(success_count, total_count)
                else:
                    # 全部失败
                    self.signals.error.emit(error_msg)
            else:
                # 全部成功
                self.signals.finished.emit(success_count, total_count)
            
        except Exception as e:
            self.signals.error.emit(f"重启服务失败: {e}")


class _ServerFileListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _ServerFileListWorker(QRunnable):
    """后台线程：通过SSH获取服务器文件列表"""
    def __init__(self, ssh_config: Dict[str, Any], remote_dir: str):
        super().__init__()
        self.signals = _ServerFileListWorkerSignals()
        self._ssh_config = ssh_config
        self._remote_dir = remote_dir

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
            
            # 列出目录下的文件和文件夹
            result = ssh.list_files(self._remote_dir, recursive=False)
            ssh.close()
            
            if not result["success"]:
                self.signals.error.emit(result.get("error", "获取文件列表失败"))
                return
            
            # 排序：目录在前，文件在后，都按名称排序
            files = result["files"]
            dirs = [f for f in files if f["is_dir"]]
            regular_files = [f for f in files if not f["is_dir"]]
            dirs.sort(key=lambda x: x["name"].lower())
            regular_files.sort(key=lambda x: x["name"].lower())
            
            # 合并：目录在前，文件在后
            sorted_files = dirs + regular_files
            self.signals.finished.emit(sorted_files)
        except Exception as e:
            self.signals.error.emit(f"获取文件列表失败：{e}")


class ServerFileBrowserDialog(QDialog):
    """通过SSH浏览服务器文件的对话框"""
    def __init__(self, parent=None, ssh_config: Dict[str, Any] = None, initial_dir: str = "/ai-perf/scripts"):
        super().__init__(parent)
        self.setWindowTitle("选择服务器脚本文件")
        self.setMinimumSize(600, 500)
        self._ssh_config = ssh_config
        self._current_dir = initial_dir
        self._selected_path = None
        self._thread_pool = QThreadPool()
        self._init_ui()
        self._load_directory(initial_dir)
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # 路径显示和导航
        path_layout = QHBoxLayout()
        path_layout.setSpacing(8)
        
        path_label = QLabel("当前路径:")
        path_label.setFixedWidth(80)
        path_layout.addWidget(path_label)
        
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(False)  # 允许手动输入路径
        self.path_input.setFont(QFont("Courier New", 10))
        self.path_input.returnPressed.connect(self._on_path_entered)  # 按回车键跳转到输入的路径
        path_layout.addWidget(self.path_input, 1)
        
        # 返回上一级按钮
        self.up_btn = QPushButton("↑")
        self.up_btn.setFixedWidth(40)
        self.up_btn.setToolTip("返回上一级目录")
        self.up_btn.clicked.connect(self._on_up_clicked)
        path_layout.addWidget(self.up_btn)
        
        # 刷新按钮
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedWidth(60)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        path_layout.addWidget(self.refresh_btn)
        
        layout.addLayout(path_layout)
        
        # 文件列表
        self.file_list = QListWidget()
        self.file_list.setFont(QFont("Courier New", 10))
        self.file_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.file_list, 1)
        
        # 状态标签
        self.status_label = QLabel("正在加载...")
        self.status_label.setStyleSheet("color: #666; padding: 4px;")
        layout.addWidget(self.status_label)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedWidth(80)
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.select_btn = QPushButton("选择")
        self.select_btn.setFixedWidth(80)
        self.select_btn.clicked.connect(self._on_select_clicked)
        self.select_btn.setEnabled(False)
        button_layout.addWidget(self.select_btn)
        
        layout.addLayout(button_layout)
        
        # 连接文件列表选择事件
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)
    
    def _load_directory(self, remote_dir: str):
        """加载指定目录的文件列表"""
        if not self._ssh_config or not self._ssh_config.get("host"):
            QMessageBox.warning(self, "错误", "请先配置SSH服务器信息（在系统设置TAB中配置）")
            self.reject()
            return
        
        self._current_dir = remote_dir
        self.path_input.setText(remote_dir)
        self.status_label.setText("正在加载...")
        self.file_list.clear()
        self.select_btn.setEnabled(False)
        
        # 创建后台任务
        worker = _ServerFileListWorker(self._ssh_config, remote_dir)
        worker.signals.finished.connect(self._on_files_loaded)
        worker.signals.error.connect(self._on_load_error)
        self._thread_pool.start(worker)
    
    def _on_files_loaded(self, files: List[Dict]):
        """文件列表加载完成"""
        self.file_list.clear()
        
        if not files:
            self.status_label.setText("目录为空")
            return
        
        for file_info in files:
            name = file_info["name"]
            is_dir = file_info["is_dir"]
            
            # 创建列表项
            item = QListWidgetItem(name)
            if is_dir:
                item.setText(f"📁 {name}")
                item.setData(Qt.ItemDataRole.UserRole, {"path": file_info["path"], "is_dir": True})
            else:
                # 只显示 .sh 文件
                if name.endswith(".sh"):
                    item.setText(f"📄 {name}")
                    item.setData(Qt.ItemDataRole.UserRole, {"path": file_info["path"], "is_dir": False})
                else:
                    continue  # 跳过非 .sh 文件
        
        self.status_label.setText(f"共 {self.file_list.count()} 个文件/目录")
    
    def _on_load_error(self, error_msg: str):
        """加载文件列表失败"""
        self.status_label.setText(f"加载失败: {error_msg}")
        QMessageBox.warning(self, "错误", f"无法加载目录：\n{error_msg}")
    
    def _on_item_double_clicked(self, item: QListWidgetItem):
        """双击列表项"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        
        if data["is_dir"]:
            # 如果是目录，进入该目录
            self._load_directory(data["path"])
        else:
            # 如果是文件，直接选择
            self._selected_path = data["path"]
            self.accept()
    
    def _on_selection_changed(self):
        """选择项改变"""
        current_item = self.file_list.currentItem()
        if current_item:
            data = current_item.data(Qt.ItemDataRole.UserRole)
            if data and not data["is_dir"]:
                self.select_btn.setEnabled(True)
            else:
                self.select_btn.setEnabled(False)
        else:
            self.select_btn.setEnabled(False)
    
    def _on_select_clicked(self):
        """选择按钮点击"""
        current_item = self.file_list.currentItem()
        if current_item:
            data = current_item.data(Qt.ItemDataRole.UserRole)
            if data and not data["is_dir"]:
                self._selected_path = data["path"]
                self.accept()
    
    def _on_up_clicked(self):
        """返回上一级目录"""
        if self._current_dir == "/":
            return
        
        # 获取父目录
        parent_dir = str(Path(self._current_dir).parent)
        if parent_dir == ".":
            parent_dir = "/"
        self._load_directory(parent_dir)
    
    def _on_refresh_clicked(self):
        """刷新当前目录"""
        self._load_directory(self._current_dir)
    
    def _on_path_entered(self):
        """用户手动输入路径并按回车"""
        entered_path = self.path_input.text().strip()
        if entered_path:
            # 规范化路径（确保以 / 开头）
            if not entered_path.startswith("/"):
                entered_path = "/" + entered_path
            self._load_directory(entered_path)
    
    def get_selected_path(self) -> Optional[str]:
        """获取选中的文件路径"""
        return self._selected_path


class _ReadScreenOutputWorkerSignals(QObject):
    output = Signal(str)  # 新的输出内容
    length_updated = Signal(int)  # 更新后的总长度


class _ReadScreenOutputWorker(QRunnable):
    """后台线程：读取screen输出"""
    def __init__(self, ssh_client, screen_name: str, last_length: int):
        super().__init__()
        self.signals = _ReadScreenOutputWorkerSignals()
        self._ssh_client = ssh_client
        self._screen_name = screen_name
        self._last_length = last_length
    
    @Slot()
    def run(self) -> None:
        try:
            # 获取 screen 的输出
            cmd = f"screen -S {self._screen_name} -X hardcopy /tmp/screen_output_{self._screen_name}.txt 2>/dev/null && cat /tmp/screen_output_{self._screen_name}.txt 2>/dev/null || echo ''"
            result = self._ssh_client.execute(cmd)
            
            if result.get("stdout"):
                full_output = result["stdout"]
                # 只返回新增的内容（增量读取）
                if len(full_output) > self._last_length:
                    new_output = full_output[self._last_length:]
                    self.signals.output.emit(new_output)
                    self.signals.length_updated.emit(len(full_output))
                else:
                    # 没有新内容，也要通知更新长度（重置标志）
                    self.signals.length_updated.emit(self._last_length)
            else:
                # 没有输出，也要通知更新长度（重置标志）
                self.signals.length_updated.emit(self._last_length)
        except Exception as e:
            # 发生错误时也要重置标志
            self.signals.length_updated.emit(self._last_length)


class _SendCommandWorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)


class _SendCommandWorker(QRunnable):
    """后台线程：发送命令到screen"""
    def __init__(self, ssh_client, screen_name: str, cmd: str):
        super().__init__()
        self.signals = _SendCommandWorkerSignals()
        self._ssh_client = ssh_client
        self._screen_name = screen_name
        self._cmd = cmd
    
    @Slot()
    def run(self) -> None:
        try:
            # 转义特殊字符
            cmd_escaped = self._cmd.replace("'", "'\\''")
            # 发送命令到 screen（使用 stuff 命令）
            execute_cmd = f"screen -S {self._screen_name} -X stuff '{cmd_escaped}\\n'"
            result = self._ssh_client.execute(execute_cmd)
            
            if not result.get("success"):
                self.signals.error.emit(f"发送命令失败: {result.get('stderr', '未知错误')}")
            else:
                self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(f"发送命令失败: {e}")


class _InitSSHWorkerSignals(QObject):
    connected = Signal(object)  # SSH客户端对象
    content_loaded = Signal(str)  # screen内容
    error = Signal(str)  # 错误消息


class _InitSSHWorker(QRunnable):
    """后台线程：初始化SSH连接并加载screen内容"""
    def __init__(self, ssh_config: Dict[str, Any], screen_name: str):
        super().__init__()
        self.signals = _InitSSHWorkerSignals()
        self._ssh_config = ssh_config
        self._screen_name = screen_name
    
    @Slot()
    def run(self) -> None:
        import logging
        # 临时禁用paramiko的详细日志，避免控制台输出大量错误信息
        paramiko_logger = logging.getLogger("paramiko")
        original_level = paramiko_logger.level
        paramiko_logger.setLevel(logging.ERROR)  # 只显示ERROR级别以上的日志
        
        try:
            from utils.ssh_client import SSHClient
            
            # 创建并连接SSH（使用较少的重试次数，避免长时间阻塞和大量日志）
            ssh = SSHClient(**self._ssh_config)
            # 只重试1次，延迟1秒，避免产生大量错误日志
            if not ssh.connect(max_retries=1, retry_delay=1.0):
                self.signals.error.emit("SSH连接失败，请检查配置")
                return
            
            # 确保 screen 存在
            check_cmd = f"screen -list | grep -q {self._screen_name} || screen -dmS {self._screen_name}"
            result = ssh.execute(check_cmd)
            
            if not result.get("success"):
                ssh.close()
                self.signals.error.emit(f"创建 screen 失败: {result.get('stderr', '未知错误')}")
                return
            
            # 读取screen内容
            cmd = f"screen -S {self._screen_name} -X hardcopy /tmp/screen_output_{self._screen_name}.txt 2>/dev/null && cat /tmp/screen_output_{self._screen_name}.txt 2>/dev/null || echo ''"
            result = ssh.execute(cmd)
            
            content = ""
            if result.get("stdout"):
                content = result["stdout"]
            
            # 发送SSH客户端和内容（总是发送，即使内容为空）
            self.signals.connected.emit(ssh)
            self.signals.content_loaded.emit(content if content else "")
        except Exception as e:
            # 简化错误消息，避免输出详细的堆栈信息
            error_msg = str(e)
            if "Error reading SSH protocol banner" in error_msg:
                error_msg = "SSH连接失败：无法读取SSH协议横幅，请检查网络连接和SSH配置"
            elif "SSHException" in error_msg:
                error_msg = "SSH连接失败，请检查SSH服务器地址、端口和认证信息"
            self.signals.error.emit(error_msg)
        finally:
            # 恢复原始日志级别
            paramiko_logger.setLevel(original_level)


class ScriptExecutionTab(QWidget):
    """脚本执行 TAB：通过 SSH 在服务器端 screen 中执行脚本"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = False
        self._ssh_client = None
        self._screen_name = "fromAdminClient"
        self._output_timer = None
        self._last_output_length = 0  # 上次读取的输出长度，用于增量读取
        self._is_reading_output = False  # 是否正在读取输出（防止重复执行）
        self._is_initializing_ssh = False  # 是否正在初始化SSH（防止重复初始化）
        self._ssh_init_failed = False  # SSH初始化是否已失败（避免重复尝试）
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
        # 延迟初始化SSH连接（在UI加载完成后，使用后台线程避免卡顿）
        QTimer.singleShot(2000, self._init_ssh_connection)
    
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
        title_label = QLabel("命令行执行")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        right_layout.addWidget(title_label)
        
        # 命令行输入框（类似终端提示符）
        command_layout = QHBoxLayout()
        command_layout.setSpacing(8)
        
        # 提示符标签
        prompt_label = QLabel("$")
        prompt_label.setFont(QFont("Menlo", 12, QFont.Bold))
        prompt_label.setStyleSheet("color: #00FF00;")  # 绿色提示符
        prompt_label.setFixedWidth(20)
        command_layout.addWidget(prompt_label)
        
        # 命令行输入框
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("输入命令并按回车执行，例如: /ai-perf/scripts/run_daily_pipeline.sh 2025-11-10")
        self.command_input.setFont(QFont("Menlo", 12))
        self.command_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E1E1E;
                color: #FFFFFF;
                border: 1px solid #3C3C3C;
                border-radius: 3px;
                padding: 6px;
            }
            QLineEdit:focus {
                border: 1px solid #007ACC;
            }
        """)
        self.command_input.returnPressed.connect(self._on_command_entered)
        command_layout.addWidget(self.command_input, 1)
        
        # 清空输出按钮
        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(60)
        clear_btn.setFixedHeight(32)
        clear_btn.clicked.connect(self._on_clear_output)
        command_layout.addWidget(clear_btn)
        
        right_layout.addLayout(command_layout)
        
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
                font-family: "Menlo", "Monaco", "Courier New";
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
    
    def _on_command_entered(self):
        """命令行输入回车执行"""
        cmd = self.command_input.text().strip()
        if not cmd:
            return
        
        # 显示命令提示符和命令
        self._append_output(f"$ {cmd}\n")
        
        # 清空输入框
        self.command_input.clear()
        
        # 确保SSH连接和screen已初始化
        if not self._ensure_ssh_connected():
            return
        
        # 发送命令到 screen
        self._send_command_to_screen(cmd)
        
        # 启动定时器读取输出（如果还没启动）
        if not self._output_timer:
            self._output_timer = QTimer()
            self._output_timer.timeout.connect(self._read_screen_output)
            self._output_timer.start(500)  # 每500ms读取一次
    
    def _on_clear_output(self):
        """清空输出"""
        self.output_text.clear()
        self._last_output_length = 0
    
    def _ensure_ssh_connected(self) -> bool:
        """确保SSH连接和screen已初始化"""
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
            return False
        
        # 如果SSH客户端未连接，则连接
        if not self._ssh_client:
            from utils.ssh_client import SSHClient
            self._ssh_client = SSHClient(**ssh_config)
            if not self._ssh_client.connect():
                QMessageBox.warning(self, "错误", "SSH 连接失败，请检查配置")
                self._ssh_client = None
                return False
        
        # 确保 screen 存在
        check_cmd = f"screen -list | grep -q {self._screen_name} || screen -dmS {self._screen_name}"
        result = self._ssh_client.execute(check_cmd)
        
        if not result.get("success"):
            QMessageBox.warning(self, "错误", f"创建 screen 失败: {result.get('stderr', '未知错误')}")
            return False
        
        return True
    
    def _send_command_to_screen(self, cmd: str):
        """发送命令到 screen（在后台线程中执行）"""
        if not self._ssh_client:
            return
        
        # 在后台线程中发送命令，避免阻塞UI
        worker = _SendCommandWorker(self._ssh_client, self._screen_name, cmd)
        worker.signals.finished.connect(self._on_command_sent)
        worker.signals.error.connect(self._on_command_send_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_command_sent(self):
        """命令发送成功"""
        pass  # 命令已发送，等待输出
    
    def _on_command_send_error(self, error_msg: str):
        """命令发送失败"""
        self._append_output(f"错误: {error_msg}\n")
    
    def _load_screen_content(self):
        """加载screen的当前内容并显示"""
        if not self._ssh_client:
            return
        
        try:
            # 获取 screen 的输出
            cmd = f"screen -S {self._screen_name} -X hardcopy /tmp/screen_output_{self._screen_name}.txt 2>/dev/null && cat /tmp/screen_output_{self._screen_name}.txt 2>/dev/null || echo ''"
            result = self._ssh_client.execute(cmd)
            
            if result.get("stdout"):
                full_output = result["stdout"]
                # 显示全部内容
                if full_output.strip():
                    self._append_output_with_ansi(full_output)
                    self._last_output_length = len(full_output)
                else:
                    # screen为空，显示提示信息
                    self._append_output("Screen 'fromAdminClient' 已连接，等待命令输入...\n")
                    self._append_output("提示：输入命令并按回车执行，例如: /ai-perf/scripts/run_daily_pipeline.sh 2025-11-10\n\n")
        except Exception as e:
            # 静默处理错误
            pass
    
    def _read_screen_output(self):
        """读取 screen 的输出（增量读取）- 触发后台线程读取"""
        if not self._ssh_client or self._is_reading_output:
            return
        
        # 设置标志，防止重复执行
        self._is_reading_output = True
        
        # 在后台线程中读取输出，避免阻塞UI
        worker = _ReadScreenOutputWorker(self._ssh_client, self._screen_name, self._last_output_length)
        worker.signals.output.connect(self._on_new_output_received)
        worker.signals.length_updated.connect(self._on_output_length_updated)
        QThreadPool.globalInstance().start(worker)
    
    def _on_new_output_received(self, new_output: str):
        """接收到新的输出内容"""
        if new_output:
            self._append_output_with_ansi(new_output)
        # 重置标志，允许下次读取
        self._is_reading_output = False
    
    def _on_output_length_updated(self, new_length: int):
        """更新输出长度"""
        self._last_output_length = new_length
        # 重置标志，允许下次读取
        self._is_reading_output = False
    
    def _append_output(self, text: str):
        """追加输出文本（纯文本）"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.setCharFormat(self._default_format)
        cursor.insertText(text)
        self.output_text.setTextCursor(cursor)
        # 自动滚动到底部
        scrollbar = self.output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _init_ssh_connection(self):
        """初始化SSH连接并显示screen内容（在后台线程中执行，避免UI卡顿）"""
        # 防止重复初始化
        if self._is_initializing_ssh or self._ssh_init_failed or self._ssh_client:
            return
        
        # 获取SSH配置
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
            # 没有配置SSH，静默失败
            self._ssh_init_failed = True
            self._append_output("提示：未配置SSH服务器信息，请在系统设置TAB中配置SSH连接信息\n")
            self._append_output("配置后，输入命令时会自动连接。\n\n")
            return
        
        # 设置初始化标志
        self._is_initializing_ssh = True
        
        # 在后台线程中初始化SSH连接
        worker = _InitSSHWorker(ssh_config, self._screen_name)
        worker.signals.connected.connect(self._on_ssh_connected)
        worker.signals.content_loaded.connect(self._on_screen_content_loaded)
        worker.signals.error.connect(self._on_ssh_init_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_ssh_connected(self, ssh_client):
        """SSH连接成功回调"""
        self._ssh_client = ssh_client
        self._is_initializing_ssh = False
        self._ssh_init_failed = False
    
    def _on_screen_content_loaded(self, content: str):
        """Screen内容加载完成回调"""
        # 显示内容（即使为空也显示提示信息）
        if content and content.strip():
            self._append_output_with_ansi(content)
            self._last_output_length = len(content)
        else:
            # screen为空，显示提示信息
            self._append_output("Screen 'fromAdminClient' 已连接，等待命令输入...\n")
            self._append_output("提示：输入命令并按回车执行，例如: /ai-perf/scripts/run_daily_pipeline.sh 2025-11-10\n\n")
            self._last_output_length = 0
        
        # 启动定时器，持续读取screen输出（确保总是启动，实现实时更新）
        if not self._output_timer:
            self._output_timer = QTimer()
            self._output_timer.timeout.connect(self._read_screen_output)
            self._output_timer.start(500)  # 每500ms读取一次
        elif not self._output_timer.isActive():
            # 如果定时器存在但未激活，重新启动
            self._output_timer.start(500)
    
    def _on_ssh_init_error(self, error_msg: str):
        """SSH初始化错误回调（静默处理，不弹窗）"""
        self._is_initializing_ssh = False
        self._ssh_init_failed = True
        # 显示友好的错误提示，但不弹窗
        self._append_output(f"SSH连接失败: {error_msg}\n")
        self._append_output("提示：请检查SSH配置（在系统设置TAB中），或稍后手动输入命令时会自动重试连接。\n\n")
    
    def __del__(self):
        """析构函数，关闭SSH连接"""
        if self._ssh_client:
            try:
                self._ssh_client.close()
            except:
                pass
            self._ssh_client = None
    
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
        
        # .env配置 TAB
        self.env_config_tab = EnvConfigTab(self)
        self.tabs.addTab(self.env_config_tab, ".env配置")
        
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
            # .env配置 TAB（不需要加载数据，用户点击刷新按钮时才加载）
            pass
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
                        asset_name_lower = asset_name.lower()
                        
                        # 根据文件名判断平台
                        # macOS: .dmg, .pkg, .app.zip, 或包含 .app 的 .zip 文件
                        is_macos = (
                            ".dmg" in asset_name_lower or
                            ".pkg" in asset_name_lower or
                            ".app.zip" in asset_name_lower or
                            (asset_name_lower.endswith(".zip") and ".app" in asset_name_lower) or
                            (asset_name_lower.endswith(".app") and asset_name_lower.endswith(".zip"))
                        )
                        
                        if is_macos:
                            platform = "darwin"
                        elif any(ext in asset_name_lower for ext in [".exe", ".msi"]):
                            platform = "windows"
                        elif any(ext in asset_name_lower for ext in [".deb", ".rpm"]):
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
    chunk_progress = Signal(int, int, int)  # 当前分片索引, 总分片数, 当前分片进度(0-100)


class _DownloadSingleAssetWorkerSignals(QObject):
    """下载单个 Asset Worker 信号"""
    finished = Signal(str)  # 保存的文件路径
    error = Signal(str)
    progress = Signal(int, int)  # downloaded_bytes, total_bytes


class _DownloadSingleAssetWorker(QRunnable):
    """后台线程：下载单个 GitHub Release asset"""
    def __init__(self, download_url: str, save_path: str):
        super().__init__()
        self.signals = _DownloadSingleAssetWorkerSignals()
        self._download_url = download_url
        self._save_path = Path(save_path)
        self._canceled = False
    
    def cancel(self):
        """取消下载"""
        self._canceled = True
    
    @Slot()
    def run(self) -> None:
        try:
            if self._canceled:
                self.signals.error.emit("下载已取消")
                return
            
            # 流式下载文件以支持进度更新
            with httpx.stream("GET", self._download_url, timeout=300.0, follow_redirects=True) as response:
                if response.status_code != 200:
                    self.signals.error.emit(f"下载失败: HTTP {response.status_code}")
                    return
                
                # 获取文件总大小
                total_bytes = int(response.headers.get("content-length", 0))
                
                # 确保保存目录存在
                self._save_path.parent.mkdir(parents=True, exist_ok=True)
                
                downloaded_bytes = 0
                with open(self._save_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        if self._canceled:
                            self.signals.error.emit("下载已取消")
                            return
                        
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        
                        # 发送进度更新
                        if total_bytes > 0:
                            # 如果已经达到或超过总大小，强制显示100%
                            if downloaded_bytes >= total_bytes:
                                self.signals.progress.emit(total_bytes, total_bytes)
                            else:
                                # 计算百分比，如果已经达到99%或更高，也强制显示100%（防止卡在99%）
                                percent = (downloaded_bytes / total_bytes) * 100
                                if percent >= 99.0:
                                    self.signals.progress.emit(total_bytes, total_bytes)
                                else:
                                    self.signals.progress.emit(downloaded_bytes, total_bytes)
                
                # 循环结束后，确保发送100%进度更新（防止卡在99%）
                if total_bytes > 0:
                    # 文件已经下载完成，强制发送100%
                    self.signals.progress.emit(total_bytes, total_bytes)
                else:
                    # 如果没有content-length，使用实际下载的字节数
                    if downloaded_bytes > 0:
                        self.signals.progress.emit(downloaded_bytes, downloaded_bytes)
                
                self.signals.finished.emit(str(self._save_path))
        except Exception as e:
            self.signals.error.emit(f"下载失败: {e}")


class _DownloadAssetsWorkerSignals(QObject):
    """下载 Assets Worker 信号"""
    finished = Signal(list)  # List[str] - 保存的文件路径列表
    error = Signal(str)
    progress = Signal(int, int)  # current, total


class _DownloadAssetsWorker(QRunnable):
    """后台线程：下载 GitHub Release 的 assets"""
    def __init__(self, assets: List[Dict[str, Any]], save_dir: str, tag_name: str):
        super().__init__()
        self.signals = _DownloadAssetsWorkerSignals()
        self._assets = assets
        self._save_dir = Path(save_dir)
        self._tag_name = tag_name
        self._canceled = False
    
    def cancel(self):
        """取消下载"""
        self._canceled = True
    
    @Slot()
    def run(self) -> None:
        try:
            saved_files = []
            total = len(self._assets)
            
            for i, asset in enumerate(self._assets):
                if self._canceled:
                    self.signals.error.emit("下载已取消")
                    return
                
                asset_name = asset.get("name", "")
                download_url = asset.get("browser_download_url", "")
                
                if not download_url:
                    continue
                
                # 保存文件
                save_path = self._save_dir / asset_name
                
                try:
                    # 下载文件
                    response = httpx.get(download_url, timeout=300.0, follow_redirects=True)
                    if response.status_code == 200:
                        with open(save_path, "wb") as f:
                            f.write(response.content)
                        saved_files.append(str(save_path))
                    else:
                        self.signals.error.emit(f"下载 {asset_name} 失败: HTTP {response.status_code}")
                        continue
                except Exception as e:
                    self.signals.error.emit(f"下载 {asset_name} 失败: {e}")
                    continue
                
                # 更新进度
                self.signals.progress.emit(i + 1, total)
            
            if not self._canceled:
                self.signals.finished.emit(saved_files)
        
        except Exception as e:
            self.signals.error.emit(f"下载失败：{type(e).__name__}: {e}")


class _UploadFileWorker(QRunnable):
    """后台线程：上传文件（支持断点续传）"""
    def __init__(self, file_path: str, platform: str, version: str, upload_api_url: str, chunk_size: int = 10 * 1024 * 1024):
        super().__init__()
        self.signals = _UploadFileWorkerSignals()
        self._file_path = Path(file_path)
        self._platform = platform
        self._version = version
        self._upload_api_url = upload_api_url
        self._chunk_size = chunk_size  # 分片大小，默认10MB
        self._should_stop = False
        self._upload_id = None
        self._total_chunks = 0
        self._uploaded_chunks = set()
        
        # 进度文件路径（用于断点续传）
        self._progress_file = self._file_path.parent / f".{self._file_path.name}.upload_progress"
    
    def stop(self):
        """停止上传"""
        self._should_stop = True
    
    def _load_progress(self) -> Optional[Dict]:
        """加载上传进度"""
        if self._progress_file.exists():
            try:
                import json
                with open(self._progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return None
    
    def _save_progress(self, upload_id: str, uploaded_chunks: set):
        """保存上传进度"""
        try:
            import json
            file_size = self._file_path.stat().st_size if self._file_path.exists() else 0
            progress_data = {
                "upload_id": upload_id,
                "uploaded_chunks": list(uploaded_chunks),
                "file_path": str(self._file_path),
                "platform": self._platform,
                "version": self._version,
                "file_size": file_size  # 保存文件大小用于验证
            }
            with open(self._progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f)
        except Exception:
            pass  # 忽略保存错误
    
    def _clear_progress(self):
        """清除上传进度"""
        try:
            if self._progress_file.exists():
                self._progress_file.unlink()
        except Exception:
            pass
    
    def _upload_chunk_with_retry(self, chunk_index: int, chunk_data: bytes, max_retries: int = 5) -> Tuple[bool, Optional[str]]:
        """
        上传分片（带重试机制）
        
        Args:
            chunk_index: 分片索引
            chunk_data: 分片数据
            max_retries: 最大重试次数
        
        Returns:
            (success: bool, error_msg: Optional[str])
        """
        import time
        
        chunk_files = {
            "chunk": (f"chunk_{chunk_index}", chunk_data, "application/octet-stream")
        }
        chunk_data_form = {
            "upload_id": self._upload_id,
            "chunk_index": chunk_index
        }
        
        last_error = None
        for attempt in range(max_retries):
            if self._should_stop:
                return False, "上传已取消"
            
            try:
                chunk_response = httpx.post(
                    f"{self._upload_api_url}/chunk",
                    files=chunk_files,
                    data=chunk_data_form,
                    timeout=300.0
                )
                
                if chunk_response.status_code == 200:
                    chunk_result = chunk_response.json()
                    if chunk_result.get("status") == "success":
                        return True, None
                    else:
                        # 服务器返回错误，但这不是网络错误，不重试
                        error_msg = chunk_result.get('message', '未知错误')
                        return False, f"上传分片 {chunk_index} 失败：{error_msg}"
                else:
                    # HTTP错误，尝试解析错误信息
                    error_msg = f"上传分片 {chunk_index} 失败：HTTP {chunk_response.status_code}"
                    try:
                        error_result = chunk_response.json()
                        if "message" in error_result:
                            error_msg = f"上传分片 {chunk_index} 失败：{error_result['message']}"
                    except:
                        pass
                    
                    # 4xx错误通常不应该重试（客户端错误），5xx错误可以重试
                    if 400 <= chunk_response.status_code < 500:
                        return False, error_msg
                    
                    last_error = error_msg
                    
            except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as e:
                # 网络错误，可以重试
                last_error = f"网络错误：{type(e).__name__}: {e}"
                if attempt < max_retries - 1:
                    # 指数退避：1秒, 2秒, 4秒, 8秒, 16秒
                    wait_time = min(2 ** attempt, 16)
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                # 其他异常，不重试
                return False, f"上传分片 {chunk_index} 失败：{type(e).__name__}: {e}"
        
        # 所有重试都失败了
        return False, f"上传分片 {chunk_index} 失败（已重试 {max_retries} 次）：{last_error}"
    
    def _init_upload_with_retry(self, file_size: int, max_retries: int = 3) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        初始化上传（带重试机制）
        
        Args:
            file_size: 文件大小
            max_retries: 最大重试次数
        
        Returns:
            (success: bool, error_msg: Optional[str], result: Optional[Dict])
        """
        import time
        
        init_data = {
            "filename": self._file_path.name,
            "platform": self._platform,
            "version": self._version,
            "total_size": file_size
        }
        
        last_error = None
        for attempt in range(max_retries):
            if self._should_stop:
                return False, "上传已取消", None
            
            try:
                init_response = httpx.post(
                    f"{self._upload_api_url}/init",
                    data=init_data,
                    timeout=30.0
                )
                
                if init_response.status_code == 200:
                    init_result = init_response.json()
                    if init_result.get("status") == "success":
                        return True, None, init_result
                    else:
                        # 服务器返回错误，不重试
                        error_msg = init_result.get('message', '未知错误')
                        return False, f"初始化上传失败：{error_msg}", None
                else:
                    # HTTP错误
                    error_msg = f"初始化上传失败：HTTP {init_response.status_code}"
                    # 4xx错误通常不应该重试，5xx错误可以重试
                    if 400 <= init_response.status_code < 500:
                        return False, error_msg, None
                    last_error = error_msg
                    
            except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as e:
                # 网络错误，可以重试
                last_error = f"网络错误：{type(e).__name__}: {e}"
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 8)
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                # 其他异常，不重试
                return False, f"初始化上传失败：{type(e).__name__}: {e}", None
        
        # 所有重试都失败了
        return False, f"初始化上传失败（已重试 {max_retries} 次）：{last_error}", None
    
    def _complete_upload_with_retry(self, max_retries: int = 3) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        完成上传（带重试机制）
        
        Args:
            max_retries: 最大重试次数
        
        Returns:
            (success: bool, error_msg: Optional[str], result: Optional[Dict])
        """
        import time
        
        complete_data = {
            "upload_id": self._upload_id
        }
        
        last_error = None
        for attempt in range(max_retries):
            if self._should_stop:
                return False, "上传已取消", None
            
            try:
                complete_response = httpx.post(
                    f"{self._upload_api_url}/complete",
                    data=complete_data,
                    timeout=300.0
                )
                
                if complete_response.status_code == 200:
                    complete_result = complete_response.json()
                    if complete_result.get("status") == "success":
                        return True, None, complete_result
                    else:
                        # 服务器返回错误，不重试
                        error_msg = complete_result.get('message', '未知错误')
                        return False, f"完成上传失败：{error_msg}", None
                else:
                    # HTTP错误
                    error_msg = f"完成上传失败：HTTP {complete_response.status_code}"
                    # 4xx错误通常不应该重试，5xx错误可以重试
                    if 400 <= complete_response.status_code < 500:
                        return False, error_msg, None
                    last_error = error_msg
                    
            except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as e:
                # 网络错误，可以重试
                last_error = f"网络错误：{type(e).__name__}: {e}"
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 8)
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                # 其他异常，不重试
                return False, f"完成上传失败：{type(e).__name__}: {e}", None
        
        # 所有重试都失败了
        return False, f"完成上传失败（已重试 {max_retries} 次）：{last_error}", None
    
    @Slot()
    def run(self) -> None:
        try:
            if self._should_stop:
                return
            
            # 检查文件是否存在
            if not self._file_path.exists():
                self.signals.error.emit(f"文件不存在：{self._file_path}")
                return
            
            file_size = self._file_path.stat().st_size
            if file_size == 0:
                self.signals.error.emit("文件为空")
                return
            
            # 尝试加载之前的进度
            progress_data = self._load_progress()
            if progress_data and progress_data.get("upload_id"):
                # 验证文件路径、大小、平台和版本是否匹配（防止文件被修改）
                saved_file_path = progress_data.get("file_path")
                saved_platform = progress_data.get("platform")
                saved_version = progress_data.get("version")
                saved_file_size = progress_data.get("file_size", 0)
                
                # 如果文件路径、大小、平台或版本不匹配，清除进度并重新开始
                if (saved_file_path != str(self._file_path) or 
                    saved_platform != self._platform or 
                    saved_version != self._version or
                    saved_file_size != file_size):
                    self._clear_progress()
                    progress_data = None
                else:
                    # 恢复上传会话
                    self._upload_id = progress_data["upload_id"]
                    self._uploaded_chunks = set(progress_data.get("uploaded_chunks", []))
                    # 查询服务器上的进度
                    try:
                        progress_response = httpx.get(
                            f"{self._upload_api_url}/progress",
                            params={"upload_id": self._upload_id},
                            timeout=10.0
                        )
                        if progress_response.status_code == 200:
                            progress_result = progress_response.json()
                            if progress_result.get("status") == "success":
                                server_chunks = set(progress_result.get("uploaded_chunks", []))
                                # 合并本地和服务器进度
                                self._uploaded_chunks = self._uploaded_chunks.union(server_chunks)
                                self._total_chunks = progress_result.get("total_chunks", 0)
                            else:
                                # 服务器会话已过期或无效，清除本地进度并重新初始化
                                self._clear_progress()
                                self._upload_id = None
                                self._uploaded_chunks = set()
                        else:
                            # 服务器返回错误，清除本地进度并重新初始化
                            self._clear_progress()
                            self._upload_id = None
                            self._uploaded_chunks = set()
                    except Exception:
                        # 查询失败，清除本地进度并重新初始化（避免使用过期的会话）
                        self._clear_progress()
                        self._upload_id = None
                        self._uploaded_chunks = set()
            
            # 如果没有上传ID，初始化上传
            if not self._upload_id:
                # 初始化上传（带重试机制）
                success, error_msg, init_result = self._init_upload_with_retry(file_size)
                if not success:
                    self.signals.error.emit(error_msg)
                    return
                
                self._upload_id = init_result["upload_id"]
                self._total_chunks = init_result["total_chunks"]
                self._chunk_size = init_result.get("chunk_size", self._chunk_size)
            
            # 计算总分片数（如果还没有）
            if self._total_chunks == 0:
                self._total_chunks = (file_size + self._chunk_size - 1) // self._chunk_size
            
            # 上传所有分片
            with open(self._file_path, "rb") as f:
                uploaded_size = 0
                
                for chunk_index in range(self._total_chunks):
                    if self._should_stop:
                        return
                    
                    # 跳过已上传的分片
                    if chunk_index in self._uploaded_chunks:
                        # 计算已上传的字节数
                        if chunk_index < self._total_chunks - 1:
                            uploaded_size += self._chunk_size
                        else:
                            # 最后一个分片
                            uploaded_size += file_size - (self._total_chunks - 1) * self._chunk_size
                        # 发送进度更新
                        self.signals.progress.emit(uploaded_size, file_size)
                        self.signals.chunk_progress.emit(chunk_index + 1, self._total_chunks, 100)
                        continue
                    
                    # 读取分片数据
                    f.seek(chunk_index * self._chunk_size)
                    chunk_data = f.read(self._chunk_size)
                    
                    if not chunk_data:
                        break
                    
                    # 上传分片（带重试机制）
                    success, error_msg = self._upload_chunk_with_retry(chunk_index, chunk_data)
                    
                    if self._should_stop:
                        return
                    
                    if not success:
                        self.signals.error.emit(error_msg)
                        return
                    
                    # 记录已上传的分片
                    self._uploaded_chunks.add(chunk_index)
                    uploaded_size += len(chunk_data)
                    
                    # 保存进度
                    self._save_progress(self._upload_id, self._uploaded_chunks)
                    
                    # 发送进度更新
                    self.signals.progress.emit(uploaded_size, file_size)
                    self.signals.chunk_progress.emit(chunk_index + 1, self._total_chunks, 100)
            
            # 所有分片上传完成，通知服务器合并
            if self._should_stop:
                return
            
            # 完成上传（带重试机制）
            success, error_msg, complete_result = self._complete_upload_with_retry()
            if not success:
                self.signals.error.emit(error_msg)
                return
            
            # 清除进度文件
            self._clear_progress()
            
            # 发送完成信号
            download_url = complete_result.get("url")
            if download_url:
                self.signals.finished.emit(download_url)
            else:
                self.signals.error.emit("上传成功但未返回URL")
        
        except httpx.TimeoutException:
            self.signals.error.emit("上传超时，请检查网络连接。可以稍后继续上传（断点续传）")
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
        self._upload_progress_timer = None
        self._upload_progress_value = 0
        self._versions = []  # 存储版本列表
        self._push_step = None  # 跟踪 git push 的执行步骤：'check', 'add', 'commit', 'push'
        self._git_status_output = ""  # 存储 git status 的输出
        self._download_progress = None  # 下载进度对话框
        self._sign_process = None  # 签名脚本进程（保留用于向后兼容）
        self._sign_tabs = {}  # 存储签名任务TAB：{tab_name: {"widget": QTextEdit, "process": QProcess}}
        self._download_tabs = {}  # 存储下载任务TAB：{tab_key: {"widget": QTextEdit, "full_name": str, "save_path": Path}}
        self._upload_tabs = {}  # 存储上传任务TAB：{tab_key: {"widget": QTextEdit, "full_name": str}}
        self._download_progress_label = None  # 右上角下载进度显示标签

        # 获取项目根目录
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        self._project_root = str(project_root.resolve())
        self._sign_script_default = str(Path(self._project_root) / "scripts" / "sign_and_notarize_from_github.py")
        self._sign_script_path = self._sign_script_default
        
        # 使用 VersionManager 统一管理版本号
        self._version_manager = VersionManager(Path(self._project_root))
        
        # 获取当前版本号
        self._current_version = self._version_manager.get_current_version() or "0.0.0"
        
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
        
        # 签名脚本路径显示（仅展示文字，不可编辑）
        self.sign_script_label = QLabel()
        self.sign_script_label.setFont(QFont("Arial", 10))
        # 统一配色，避免使用 QSS cursor 属性造成警告
        self.sign_script_label.setStyleSheet("color: #666; font-family: 'Courier New', monospace;")
        self.sign_script_label.setCursor(Qt.PointingHandCursor)
        self._update_sign_script_label()
        header_layout.addWidget(self.sign_script_label)

        sign_browse_btn = QPushButton("选择签名脚本")
        sign_browse_btn.setFixedWidth(120)
        sign_browse_btn.setFixedHeight(32)
        sign_browse_btn.clicked.connect(self._on_browse_sign_script)
        header_layout.addWidget(sign_browse_btn)

        header_layout.addStretch()
        
        # git push 按钮
        self.push_btn = QPushButton("git push")
        self.push_btn.setFixedWidth(100)
        self.push_btn.setFixedHeight(32)
        self.push_btn.clicked.connect(self._on_push_clicked)
        header_layout.addWidget(self.push_btn)
        
        # Release 按钮（动态获取版本号，使用 VersionManager）
        self.release_btn = QPushButton(f"Release V{self._current_version}")
        self.release_btn.setFixedWidth(140)
        self.release_btn.setFixedHeight(32)
        self.release_btn.clicked.connect(self._on_release_clicked)
        header_layout.addWidget(self.release_btn)
        
        # 版本管理按钮（统一管理版本号）
        version_mgmt_btn = QPushButton("版本号管理")
        version_mgmt_btn.setFixedWidth(100)
        version_mgmt_btn.setFixedHeight(32)
        version_mgmt_btn.setToolTip("统一管理所有客户端的版本号")
        version_mgmt_btn.clicked.connect(self._on_version_management_clicked)
        header_layout.addWidget(version_mgmt_btn)
        
        # Check Actions 按钮
        self.actions_btn = QPushButton("Check Actions")
        self.actions_btn.setFixedWidth(130)
        self.actions_btn.setFixedHeight(32)
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
        title_label = QLabel("Asset List")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        
        # 刷新按钮（更明显的样式）
        self.refresh_versions_btn = QPushButton("🔄 刷新")
        self.refresh_versions_btn.setFixedWidth(90)
        self.refresh_versions_btn.setFixedHeight(28)
        self.refresh_versions_btn.setToolTip("刷新 GitHub Releases 版本列表")
        self.refresh_versions_btn.clicked.connect(self.reload_versions)
        title_layout.addWidget(self.refresh_versions_btn)
        
        left_layout.addLayout(title_layout)
        
        # Assets 列表表格（以 Assets 为维度显示）
        self.version_table = QTableWidget()
        self.version_table.setColumnCount(5)
        self.version_table.setHorizontalHeaderLabels(["Asset 名称", "版本号", "平台", "大小", "状态"])
        self.version_table.horizontalHeader().setStretchLastSection(True)
        self.version_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.version_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.version_table.setAlternatingRowColors(True)
        self.version_table.setShowGrid(True)
        
        # 设置列宽
        header = self.version_table.horizontalHeader()
        header.setMinimumSectionSize(180)  # 设置所有列的最小宽度为 180（主要用于 Asset 名称列）
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # Asset 名称：自适应宽度，显示完全，最小宽度 180
        header.setSectionResizeMode(1, QHeaderView.Fixed)  # 版本号：固定宽度
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # 平台：固定宽度
        header.setSectionResizeMode(3, QHeaderView.Fixed)  # 大小：固定宽度
        header.setSectionResizeMode(4, QHeaderView.Fixed)  # 状态：固定宽度
        
        # 设置固定列的宽度
        self.version_table.setColumnWidth(0, 180)  # Asset 名称：初始宽度 180（Stretch 模式下会自适应扩展，但不会小于 180）
        self.version_table.setColumnWidth(1, 100)  # 版本号
        self.version_table.setColumnWidth(2, 80)   # 平台
        self.version_table.setColumnWidth(3, 100)  # 大小
        self.version_table.setColumnWidth(4, 60)   # 状态（缩窄以给 Asset 名称列更多空间）
        
        # 启用右键菜单
        self.version_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.version_table.customContextMenuRequested.connect(self._on_version_table_context_menu)
        
        left_layout.addWidget(self.version_table, 1)
        
        splitter.addWidget(left_widget)
        
        # 右侧：命令行输出（使用TAB结构）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(0)

        # 使用 QTabWidget 管理多个输出窗口
        self.output_tabs = QTabWidget()
        self.output_tabs.setTabsClosable(True)  # 允许关闭TAB
        self.output_tabs.tabCloseRequested.connect(self._close_output_tab)  # 关闭TAB时的处理
        
        # 设置 TAB 标签左对齐
        tab_bar = self.output_tabs.tabBar()
        # 当空间不足时允许文字截断（使用省略号），但保持最小宽度确保可读性
        tab_bar.setElideMode(Qt.ElideRight)  # 右侧截断，显示省略号
        tab_bar.setUsesScrollButtons(True)  # 启用滚动按钮，当tab太多时可以滚动查看
        tab_bar.setExpanding(False)  # 禁用扩展，确保标签从左边开始排列
        tab_bar.setDocumentMode(False)  # 禁用文档模式，确保标签正常显示
        tab_bar.setMovable(False)  # 禁用标签移动

        # -----------------------------
        # 修复 macOS 下 TAB 与输出区域之间的“间隙”（你观察到需要 -9px）
        #
        # 现象：
        # - 在 macOS（含 Intel + 14+）的某些 Qt style 下
        #   QStyle.PM_TabBarBaseOverlap / PM_TabBarBaseHeight 可能返回 0
        # - 但视觉上 TabBar 底部仍然会留出一段“底座高度”（常见 8~9px），导致 tab 与 pane 有缝
        #
        # 策略：
        # - 先用最小值消除 1px 边框缝
        # - 再在布局完成后，通过 tabBar 的实际尺寸/sizeHint 估算底座高度，动态把 tab 的 margin-bottom 调整为 -N
        #   （等价于你手动设置 margin-bottom:-9px，但不写死）
        # -----------------------------
        pane_top_px = -1
        tab_margin_bottom_px = -1

        # 使用样式表设置 TAB 样式（匹配终端黑色背景）
        # 统一圆角，确保 TAB 内容区域和输出区域一致
        output_tabs_style = """
            QTabWidget::pane {
                border: 1px solid #3A3A3C;
                background-color: #000000;
                border-radius: 0px;
                border-top: 1px solid #3A3A3C;
                padding: 0px;
                margin: 0px;
                top: __PANE_TOP_PX__px;
            }
            QTabBar {
                alignment: left;
                margin: 0px;
                margin-bottom: 0px;
                padding: 0px;
                spacing: 0px;
            }
            /* 滚动按钮样式（当tab太多时显示） */
            QTabBar::scroller {
                width: 20px;
            }
            QTabBar QAbstractButton {
                background-color: #2A2B2D;
                border: 1px solid #3A3A3C;
                border-radius: 3px;
                color: #FFFFFF;
                min-width: 20px;
                max-width: 20px;
            }
            QTabBar QAbstractButton:hover {
                background-color: #333436;
            }
            QTabBar::tab {
                background-color: #2A2B2D;
                color: #FFFFFF;
                border: 1px solid #3A3A3C;
                border-bottom: 1px solid #3A3A3C;
                padding: 6px 32px 6px 16px;
                margin-right: 2px;
                margin-bottom: __TAB_MARGIN_BOTTOM_PX__px;
                font-size: 12px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                min-width: 120px;
                min-height: 28px;
            }
            QTabBar::tab:selected {
                background-color: #000000;
                color: #FFFFFF;
                border-bottom: 1px solid #000000;
            }
            QTabBar::tab:hover:!selected {
                background-color: #333436;
                color: #FFFFFF;
            }
            QTabBar::close-button {
                subcontrol-position: right;
                subcontrol-origin: padding;
                width: 16px;
                height: 16px;
                border: none;
                background: transparent;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEyIDRMMTAgMTJNMTAgNEwxMiAxMiIgc3Ryb2tlPSIjQUFBQUFBIiBzdHJva2Utd2lkdGg9IjEuNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPgo=);
            }
            QTabBar::close-button:hover {
                background-color: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEyIDRMMTAgMTJNMTAgNEwxMiAxMiIgc3Ryb2tlPSIjRkZGRkZGIiBzdHJva2Utd2lkdGg9IjEuNSIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+Cjwvc3ZnPgo=);
            }
            /* 隐藏第一个 TAB（Github 输出）的关闭按钮 */
            QTabBar::tab:first-child::close-button {
                width: 0px;
                height: 0px;
                image: none;
            }
            /* 第一个 TAB（无关闭按钮）使用更小的右侧padding */
            QTabBar::tab:first-child {
                padding-right: 16px;
            }
        """
        def _apply_output_tabs_style(pane_top: int, tab_margin_bottom: int):
            """应用 output_tabs 的样式（支持动态替换 pane/top 与 tab/margin-bottom）。"""
            self.output_tabs.setStyleSheet(
                output_tabs_style
                .replace("__PANE_TOP_PX__", str(pane_top))
                .replace("__TAB_MARGIN_BOTTOM_PX__", str(tab_margin_bottom))
            )

        # 初始应用（先保证基本对齐，避免闪一下）
        _apply_output_tabs_style(pane_top_px, tab_margin_bottom_px)
        
        # 创建默认的 "Github 输出" TAB（不可关闭）
        self.output_text = self._create_output_text_edit()
        self.output_text.setPlaceholderText("点击\"git push\"按钮开始推送...")
        github_output_index = self.output_tabs.addTab(self.output_text, "Github 输出")

        # 布局完成后再校准一次 tab 的 margin-bottom，动态消除底座高度造成的缝隙
        def _auto_fix_output_tabs_gap():
            try:
                tb = self.output_tabs.tabBar()
                if tb.count() <= 0:
                    return
                idx = tb.currentIndex()
                if idx < 0:
                    idx = 0

                # 估算“底座高度”：
                # - 有些 style 的 pixelMetric 返回 0，但 tabBar 的整体高度仍会比 tab 本身高出一截
                # - 这截高度通常就是你看到的 9px 左右
                bar_h = tb.height()
                if bar_h <= 0:
                    bar_h = tb.sizeHint().height()

                tab_h = 0
                try:
                    rect = tb.tabRect(idx)
                    tab_h = rect.height()
                except Exception:
                    tab_h = 0
                if tab_h <= 0:
                    try:
                        tab_h = tb.tabSizeHint(idx).height()
                    except Exception:
                        tab_h = 0

                base_gap_1 = max(0, bar_h - tab_h)

                # 再用纯 sizeHint 的方式兜底（避免未 show 时 height 为 0）
                try:
                    base_gap_2 = max(0, tb.sizeHint().height() - tb.tabSizeHint(idx).height())
                except Exception:
                    base_gap_2 = 0

                base_gap = max(base_gap_1, base_gap_2)

                # gap 很小时不用动（保留 -1 用于消边框缝）
                if base_gap <= 1:
                    return

                # 核心：用负 margin-bottom 抵消底座高度（等价于手动 -9px）
                _apply_output_tabs_style(-1, -int(base_gap))
            except Exception:
                # 任何异常都不影响主流程
                return

        QTimer.singleShot(0, _auto_fix_output_tabs_gap)
        
        # 确保 TAB 文字可见：显式设置文字和颜色
        tab_bar = self.output_tabs.tabBar()
        # 确保文字正确设置（防止样式表覆盖）
        tab_bar.setTabText(github_output_index, "Github 输出")
        # 确保 TAB 有足够宽度显示文字
        tab_bar.setTabData(github_output_index, None)  # 清除可能的数据
        
        # Github 输出 TAB 不显示关闭按钮（通过隐藏关闭按钮）
        # 注意：需要在添加 TAB 后立即设置，否则可能不生效
        QTimer.singleShot(0, lambda: self._hide_git_push_close_button(github_output_index))
        
        # 强制设置 QTabBar 的布局为左对齐（macOS 兼容）
        # 在 macOS 上，QTabBar 默认可能居中显示，需要通过多种方式强制左对齐
        def _force_tab_alignment():
            # 确保所有标签从左边开始排列
            for i in range(tab_bar.count()):
                tab_bar.setTabButton(i, QTabBar.LeftSide, None)  # 确保没有左侧按钮
            # 在 macOS 上，通过设置 QTabBar 的布局来强制左对齐
            # 使用样式表设置左对齐（如果样式表不生效，则通过布局控制）
            current_style = tab_bar.styleSheet()
            if "alignment: left" not in current_style:
                tab_bar.setStyleSheet(current_style + """
                    QTabBar {
                        alignment: left;
                    }
                """)
            # 强制更新布局，确保文字完整显示
            tab_bar.updateGeometry()
            self.output_tabs.updateGeometry()
        # 延迟执行，确保所有 TAB 都已添加
        QTimer.singleShot(10, _force_tab_alignment)
        
        # 初始化默认文本格式（用于Git Push输出）
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setFixedPitch(True)
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        self._default_format = QTextCharFormat()
        self._default_format.setForeground(QColor("#FFFFFF"))
        if font:
            self._default_format.setFont(font)
        
        right_layout.addWidget(self.output_tabs, 1)
        
        splitter.addWidget(right_widget)
        
        # 设置分割比例（左侧35%，右侧65%）
        splitter.setSizes([350, 650])
        
        layout.addWidget(splitter, 1)

        # 创建右上角下载进度显示标签（初始隐藏）
        self._download_progress_label = QLabel(self)
        self._download_progress_label.setAlignment(Qt.AlignCenter)
        self._download_progress_label.setFont(QFont("Arial", 10))
        self._download_progress_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 200);
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
            }
        """)
        self._download_progress_label.hide()
        self._download_progress_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # 不阻挡鼠标事件
    
    def _hide_git_push_close_button(self, index: int):
        """隐藏 Github 输出 TAB 的关闭按钮"""
        tab_bar = self.output_tabs.tabBar()
        close_button = tab_bar.tabButton(index, QTabBar.RightSide)
        if close_button:
            close_button.hide()
            close_button.setParent(None)

    def _create_output_text_edit(self) -> QTextEdit:
        """创建输出文本编辑器（统一的样式）"""
        output_text = QTextEdit()
        output_text.setReadOnly(True)
        
        # 设置苹果终端 Basic 主题的字体
        font_families = ["Menlo", "Monaco", "Courier New"]
        font_size = 12
        font = None
        for font_family in font_families:
            font = QFont(font_family, font_size)
            font.setFixedPitch(True)
            if QFont(font_family).exactMatch() or font_family == "Courier New":
                break
        
        if font:
            output_text.setFont(font)
        
        # 设置 tab 宽度
        output_text.setTabStopDistance(4 * output_text.fontMetrics().averageCharWidth())
        
        # 苹果终端 Basic 主题默认样式（完全匹配发布TAB）
        # 统一圆角为 0，确保与 TAB 内容区域一致
        output_text.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #FFFFFF;
                border: none;
                padding: 10px;
                selection-background-color: #0066CC;
                selection-color: #FFFFFF;
                font-family: "Menlo", "Monaco", "Courier New";
                font-size: 12pt;
                line-height: 1.2;
                border-radius: 0px;
            }
        """)
        
        return output_text
    
    def _update_sign_script_label(self):
        """更新签名脚本路径显示"""
        display_path = self._sign_script_path
        # 长路径做截断显示，完整路径在提示中
        if len(display_path) > 40:
            display_path = display_path[:12] + "..." + display_path[-15:]
        self.sign_script_label.setText(f"签名脚本：{display_path}")
        self.sign_script_label.setToolTip(self._sign_script_path)

    def _on_browse_sign_script(self):
        """浏览选择签名/打包脚本"""
        current_path = Path(self._sign_script_path).expanduser()
        if not current_path.is_absolute():
            current_path = Path(self._project_root) / current_path
        browse_dir = current_path.parent if current_path.exists() else Path(self._project_root) / "scripts"

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择签名脚本",
            str(browse_dir),
            "Python Files (*.py);;All Files (*)"
        )

        if file_path:
            self._sign_script_path = file_path
            self._update_sign_script_label()
    
    def resizeEvent(self, event):
        """窗口大小改变时，更新进度标签位置"""
        super().resizeEvent(event)
        if self._download_progress_label and self._download_progress_label.isVisible():
            parent_w = self.width()
            margin = 20
            x = parent_w - self._download_progress_label.width() - margin
            y = margin
            self._download_progress_label.move(x, y)
    
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
        """更新 Assets 列表表格（以 Assets 为维度显示）"""
        # 收集所有 assets，每个 asset 一行
        asset_rows = []
        for release_data in self._versions:
            tag_name = release_data.get("tag_name", "")
            version = release_data.get("version", tag_name)
            assets = release_data.get("assets", [])
            is_prerelease = release_data.get("prerelease", False)
            is_draft = release_data.get("draft", False)
            
            # 状态
            if is_draft:
                status_text = "草稿"
            elif is_prerelease:
                status_text = "预发布"
            else:
                status_text = "正式版"
            
            # 遍历所有 assets，为每个 asset 创建一行
            for asset in assets:
                asset_name = asset.get("name", "")
                asset_url = asset.get("browser_download_url", "")
                asset_size = asset.get("size", 0)
                asset_name_lower = asset_name.lower()
                
                # 判断平台
                platform = "未知"
                if (
                    ".dmg" in asset_name_lower or
                    ".pkg" in asset_name_lower or
                    ".app.zip" in asset_name_lower or
                    (asset_name_lower.endswith(".zip") and ".app" in asset_name_lower) or
                    (asset_name_lower.endswith(".app") and asset_name_lower.endswith(".zip"))
                ):
                    platform = "macOS"
                elif any(ext in asset_name_lower for ext in [".exe", ".msi"]):
                    platform = "Windows"
                elif any(ext in asset_name_lower for ext in [".deb", ".rpm"]):
                    platform = "Linux"
                else:
                    # 跳过无法识别平台的文件
                    continue
                
                # 格式化文件大小
                if asset_size > 0:
                    if asset_size < 1024:
                        size_text = f"{asset_size} B"
                    elif asset_size < 1024 * 1024:
                        size_text = f"{asset_size / 1024:.2f} KB"
                    elif asset_size < 1024 * 1024 * 1024:
                        size_text = f"{asset_size / (1024 * 1024):.2f} MB"
                    else:
                        size_text = f"{asset_size / (1024 * 1024 * 1024):.2f} GB"
                else:
                    size_text = "未知"
                
                asset_rows.append({
                    "asset_name": asset_name,
                    "asset_url": asset_url,
                    "asset_size": asset_size,
                    "size_text": size_text,
                    "version": version,
                    "tag_name": tag_name,
                    "platform": platform,
                    "status": status_text,
                    "release_data": release_data,  # 保存完整的版本数据，用于右键菜单
                })
        
        # 设置表格行数
        self.version_table.setRowCount(len(asset_rows))
        
        # 填充表格
        for row, asset_data in enumerate(asset_rows):
            # Asset 名称
            asset_name_item = QTableWidgetItem(asset_data["asset_name"])
            self.version_table.setItem(row, 0, asset_name_item)
            
            # 版本号
            version_item = QTableWidgetItem(asset_data["version"])
            self.version_table.setItem(row, 1, version_item)
            
            # 平台
            platform_item = QTableWidgetItem(asset_data["platform"])
            self.version_table.setItem(row, 2, platform_item)
            
            # 大小
            size_item = QTableWidgetItem(asset_data["size_text"])
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.version_table.setItem(row, 3, size_item)
            
            # 状态
            status_item = QTableWidgetItem(asset_data["status"])
            self.version_table.setItem(row, 4, status_item)
            
            # 存储完整的 asset 和 release 数据到第一列，用于右键菜单
            asset_name_item.setData(Qt.UserRole, asset_data)
        
        # 确保状态列始终保持固定宽度，为 Asset 名称列预留空间
        header = self.version_table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.version_table.setColumnWidth(4, 60)
        
        # 确保 Asset 名称列保持最小宽度 180
        current_width = self.version_table.columnWidth(0)
        if current_width < 180:
            self.version_table.setColumnWidth(0, 180)
    
    def _on_version_table_context_menu(self, position):
        """Assets 列表右键菜单"""
        item = self.version_table.itemAt(position)
        if not item:
            return
        
        row = item.row()
        # 获取 asset 数据
        asset_name_item = self.version_table.item(row, 0)
        if not asset_name_item:
            return
        
        asset_data = asset_name_item.data(Qt.UserRole)
        if not asset_data:
            return
        
        # 从 asset_data 中获取 release_data
        version_data = asset_data.get("release_data")
        if not version_data:
            return
        
        # 检查当前 asset 是否是 macOS 平台
        platform = asset_data.get("platform", "")
        has_macos = (platform == "macOS")
        
        # 创建右键菜单
        menu = QMenu(self)
        
        # 仅下载选项（只下载选中的这一行）
        download_action = menu.addAction("仅下载")
        download_action.triggered.connect(lambda: self._on_download_only(asset_data))
        
        # 下载并同步到服务器选项
        upload_action = menu.addAction("下载并同步到服务器")
        upload_action.triggered.connect(lambda: self._on_upload_to_server(asset_data))
        
        # 下载并签名选项（仅 macOS 版本显示）
        if has_macos:
            sign_action = menu.addAction("下载并签名")
            sign_action.triggered.connect(lambda: self._on_download_and_sign(asset_data))
            
            # 上传已签名包到服务器选项（仅 macOS 版本显示）
            upload_signed_action = menu.addAction("上传已签名包到服务器")
            upload_signed_action.triggered.connect(lambda: self._on_upload_signed_package(asset_data))
        
        # 显示菜单
        menu.exec_(self.version_table.viewport().mapToGlobal(position))

    def _infer_macos_arch_from_asset_name(self, asset_name_lower: str) -> Optional[str]:
        """从文件名中推断 macOS 架构（arm64 / intel），推断失败返回 None。"""
        if not asset_name_lower:
            return None

        # 优先匹配更明确的后缀/关键字
        if (
            "-arm64" in asset_name_lower
            or asset_name_lower.endswith("-arm64.app.zip")
            or asset_name_lower.endswith("-arm64.zip")
            or "arm64" in asset_name_lower
        ):
            return "arm64"

        if (
            "-intel" in asset_name_lower
            or asset_name_lower.endswith("-intel.app.zip")
            or asset_name_lower.endswith("-intel.zip")
            or "intel" in asset_name_lower
            or "x86" in asset_name_lower
            or "x86_64" in asset_name_lower
        ):
            return "intel"

        return None

    def _prompt_macos_arch(self, asset_name: str) -> Optional[str]:
        """
        当无法从文件名识别架构时，让用户手动选择 macOS 架构。

        返回：
        - "arm64" / "intel"
        - 用户取消则返回 None
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("选择架构")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tip = QLabel(
            "无法从文件名中识别架构类型，请手动选择。\n\n"
            f"文件名：{asset_name}"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        combo = QComboBox()
        combo.addItem("Apple Silicon（arm64）", "arm64")
        combo.addItem("Intel（x86_64）", "intel")
        layout.addWidget(combo)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        dlg.resize(460, 200)
        if dlg.exec() == QDialog.Accepted:
            return combo.currentData()
        return None
    
    def _on_download_only(self, asset_data: Dict[str, Any]):
        """仅下载：下载选中的单个 asset 到本地（输出到TAB）"""
        # 获取 asset 信息
        asset_name = asset_data.get("asset_name", "")
        asset_url = asset_data.get("asset_url", "")
        platform = asset_data.get("platform", "")
        
        if not asset_url:
            QMessageBox.warning(self, "错误", "该文件没有下载链接")
            return
        
        # 确定客户端类型（从文件名中提取）
        asset_name_lower = asset_name.lower()
        client_type = None
        if "employee" in asset_name_lower or "client" in asset_name_lower or "ai.perf.client" in asset_name_lower or "ai-perf-client" in asset_name_lower:
            client_type = "employee"
        elif "admin" in asset_name_lower or "ai-perf-admin" in asset_name_lower or "ai.perf.admin" in asset_name_lower:
            client_type = "admin"
        
        if not client_type:
            QMessageBox.warning(self, "错误", "无法从文件名中识别客户端类型")
            return
        
        # 确定架构（macOS 需要）
        arch = None
        if platform == "macOS":
            arch = self._infer_macos_arch_from_asset_name(asset_name_lower)
            if not arch:
                arch = self._prompt_macos_arch(asset_name)
                if not arch:
                    return
        
        # 构建下载路径：dist/from_github/{client_type}/{arch或操作系统}/
        # 对于 macOS: dist/from_github/{client_type}/{arch}/
        # 对于 Windows/Linux: dist/from_github/{client_type}/{platform}/
        # 员工端使用 ui_client，管理端使用 admin_ui_client（与签名脚本保持一致）
        client_dir = Path(self._project_root) / ("ui_client" if client_type == "employee" else "admin_ui_client")
        if platform == "macOS" and arch:
            download_dir = client_dir / "dist" / "from_github" / client_type / arch
        else:
            # Windows 或 Linux
            platform_lower = platform.lower()
            download_dir = client_dir / "dist" / "from_github" / client_type / platform_lower
        
        download_dir.mkdir(parents=True, exist_ok=True)
        save_path = download_dir / asset_name
        
        # 检查文件是否已存在且完整
        if save_path.exists() and save_path.is_file():
            file_size = save_path.stat().st_size
            if file_size > 0:
                # 尝试获取远程文件大小进行对比
                try:
                    import httpx
                    head_response = httpx.head(asset_url, timeout=10.0, follow_redirects=True)
                    remote_size = int(head_response.headers.get("content-length", 0))
                    if remote_size > 0 and file_size == remote_size:
                        # 文件已存在且大小匹配，跳过下载
                        tab_name = asset_name
                        if len(tab_name) > 30:
                            tab_name = tab_name[:27] + "..."
                        self._create_download_tab(tab_name, asset_name, save_path, skip_download=True)
                        return
                except Exception:
                    pass  # 如果无法获取远程大小，继续下载
        
        # 创建下载 TAB
        tab_name = asset_name
        if len(tab_name) > 30:
            tab_name = tab_name[:27] + "..."
        self._create_download_tab(tab_name, asset_name, save_path, asset_url)
    
    def _create_download_tab(self, tab_name: str, full_asset_name: str, save_path: Path, asset_url: str = None, skip_download: bool = False):
        """创建下载任务TAB并开始下载"""
        # 创建新的输出TAB
        output_text = self._create_output_text_edit()
        if skip_download:
            output_text.setPlaceholderText(f"文件已存在，跳过下载：{full_asset_name}...")
        else:
            output_text.setPlaceholderText(f"正在下载文件：{full_asset_name}...")
        
        # 添加到TAB
        tab_index = self.output_tabs.addTab(output_text, tab_name)
        self.output_tabs.setCurrentIndex(tab_index)  # 切换到新TAB
        
        # 存储TAB信息
        download_tab_key = f"download_{tab_name}"
        if not hasattr(self, '_download_tabs'):
            self._download_tabs = {}
        self._download_tabs[download_tab_key] = {
            "widget": output_text,
            "full_name": full_asset_name,
            "save_path": save_path
        }
        
        # 输出初始信息
        self._append_output_to_widget(output_text, "=" * 50 + "\n")
        self._append_output_to_widget(output_text, f"下载文件：{full_asset_name}\n")
        self._append_output_to_widget(output_text, f"保存路径：{save_path}\n")
        self._append_output_to_widget(output_text, "=" * 50 + "\n\n")
        
        if skip_download:
            # 文件已存在，跳过下载
            file_size = save_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            self._append_output_to_widget(output_text, f"✓ 文件已存在，跳过下载\n")
            self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n")
            self._append_output_to_widget(output_text, f"  文件路径: {save_path}\n")
            return
        
        if not asset_url:
            self._append_output_to_widget(output_text, "✗ 错误：没有下载链接\n", is_error=True)
            return
        
        # 创建下载 Worker
        worker = _DownloadSingleAssetWorker(asset_url, str(save_path))
        worker.signals.finished.connect(
            lambda path: self._on_download_tab_finished(download_tab_key, path)
        )
        worker.signals.error.connect(
            lambda msg: self._on_download_tab_error(download_tab_key, msg)
        )
        worker.signals.progress.connect(
            lambda downloaded, total: self._on_download_tab_progress(download_tab_key, downloaded, total)
        )
        QThreadPool.globalInstance().start(worker)
    
    def _on_download_tab_finished(self, tab_key: str, save_path: str):
        """下载TAB完成"""
        if tab_key not in self._download_tabs:
            return
        
        tab_info = self._download_tabs[tab_key]
        output_text = tab_info["widget"]
        
        try:
            file_size = Path(save_path).stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            self._append_output_to_widget(output_text, f"\n✓ 下载完成\n")
            self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n")
            self._append_output_to_widget(output_text, f"  文件路径: {save_path}\n")
        except Exception as e:
            self._append_output_to_widget(output_text, f"\n✓ 下载完成\n")
            self._append_output_to_widget(output_text, f"  文件路径: {save_path}\n")
    
    def _on_download_tab_error(self, tab_key: str, error_msg: str):
        """下载TAB错误"""
        if tab_key not in self._download_tabs:
            return
        
        tab_info = self._download_tabs[tab_key]
        output_text = tab_info["widget"]
        self._append_output_to_widget(output_text, f"\n✗ 下载失败: {error_msg}\n", is_error=True)
    
    def _on_download_tab_progress(self, tab_key: str, downloaded_bytes: int, total_bytes: int):
        """下载TAB进度更新"""
        if tab_key not in self._download_tabs:
            return
        
        tab_info = self._download_tabs[tab_key]
        output_text = tab_info["widget"]
        
        if total_bytes > 0:
            percent = (downloaded_bytes / total_bytes) * 100
            downloaded_mb = downloaded_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            # 更新最后一行进度信息
            cursor = output_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            progress_text = f"  进度: {percent:.1f}% ({downloaded_mb:.2f}/{total_mb:.2f} MB)\r"
            cursor.insertText(progress_text)
            output_text.setTextCursor(cursor)
            
            # 自动滚动到底部
            scrollbar = output_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def _on_download_single_finished(self, save_path: str, asset_name: str):
        """单个文件下载完成"""
        # 先强制显示100%进度，然后显示完成提示
        # 获取文件实际大小作为总大小
        try:
            file_size = Path(save_path).stat().st_size
            if file_size > 0:
                self._show_download_progress(asset_name, file_size, file_size)
                # 短暂延迟后显示完成提示
                QTimer.singleShot(200, lambda: self._show_download_progress(asset_name, completed=True))
            else:
                self._show_download_progress(asset_name, completed=True)
        except Exception:
            self._show_download_progress(asset_name, completed=True)
        
        QTimer.singleShot(1500, self._hide_download_progress)  # 1.5秒后自动隐藏
    
    def _on_download_single_error(self, error_msg: str, asset_name: str):
        """单个文件下载失败"""
        # 显示错误提示，然后自动消失
        self._show_download_progress(asset_name, error=True, error_msg=error_msg)
        QTimer.singleShot(2000, self._hide_download_progress)  # 2秒后自动隐藏
    
    def _on_download_single_progress(self, asset_name: str, downloaded_bytes: int, total_bytes: int):
        """单个文件下载进度更新"""
        self._show_download_progress(asset_name, downloaded_bytes, total_bytes)
    
    def _show_download_progress(self, filename: str, downloaded_bytes: int = 0, total_bytes: int = 0, error: bool = False, completed: bool = False, error_msg: str = ""):
        """显示右上角下载进度"""
        if not self._download_progress_label:
            return
        
        if error:
            text = f"下载失败: {filename}"
            if error_msg:
                # 截断过长的错误消息
                if len(error_msg) > 30:
                    error_msg = error_msg[:27] + "..."
                text = f"{filename}\n{error_msg}"
        elif completed:
            text = f"下载完成: {filename}"
        elif total_bytes > 0:
            # 计算百分比，确保不会超过100%
            if downloaded_bytes >= total_bytes:
                percent = 100
                downloaded_bytes = total_bytes  # 确保不超过总大小
            else:
                percent = int((downloaded_bytes / total_bytes) * 100)
            # 格式化文件大小
            downloaded_mb = downloaded_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            text = f"{filename}\n{downloaded_mb:.1f}MB / {total_mb:.1f}MB ({percent}%)"
        else:
            text = f"正在下载: {filename}\n准备中..."
        
        self._download_progress_label.setText(text)
        self._download_progress_label.adjustSize()
        
        # 定位到右上角
        parent_w = self.width()
        margin = 20
        x = parent_w - self._download_progress_label.width() - margin
        y = margin
        self._download_progress_label.move(x, y)
        self._download_progress_label.show()
        self._download_progress_label.raise_()  # 确保在最上层
    
    def _hide_download_progress(self):
        """隐藏下载进度"""
        if self._download_progress_label:
            self._download_progress_label.hide()
    
    def _on_download_and_sign(self, asset_data: Dict[str, Any]):
        """下载并签名：执行 sign_and_notarize_from_github.py 脚本"""
        # 从asset_data中获取信息
        asset_name = asset_data.get("asset_name", "")
        version_data = asset_data.get("release_data", {})
        tag_name = version_data.get("tag_name", "")
        
        if not tag_name:
            QMessageBox.warning(self, "错误", "版本号为空")
            return
        
        # 从asset名称中提取客户端类型和架构
        asset_name_lower = asset_name.lower()
        client_type = None
        arch = None
        
        # 检查是否包含employee或client（employee端命名可能是 Ai.Perf.Client.app-intel.zip）
        if "employee" in asset_name_lower or "client" in asset_name_lower or "ai.perf.client" in asset_name_lower or "ai-perf-client" in asset_name_lower:
            client_type = "employee"
        # 检查是否包含admin
        elif "admin" in asset_name_lower or "ai-perf-admin" in asset_name_lower or "ai.perf.admin" in asset_name_lower:
            client_type = "admin"
        
        if not client_type:
            QMessageBox.warning(
                self,
                "错误",
                f"无法从文件名中识别客户端类型：{asset_name}\n\n"
                "文件名应包含 'employee'、'client' 或 'admin' 关键字。\n"
                "employee端命名示例: Ai.Perf.Client.app-intel.zip\n"
                "admin端命名示例: Ai.Perf.Admin.app-intel.zip"
            )
            return
        
        # 从文件名中提取架构
        arch = self._infer_macos_arch_from_asset_name(asset_name_lower)
        
        if not arch:
            arch = self._prompt_macos_arch(asset_name)
            if not arch:
                return
        
        # 获取下载URL
        asset_url = asset_data.get("asset_url", "")
        if not asset_url:
            QMessageBox.warning(
                self,
                "错误",
                "无法获取下载地址"
            )
            return
        
        # 从配置读取仓库信息（用于脚本参数，但实际不会使用，因为会使用--download-url）
        cfg = ConfigManager.load()
        repo_owner = cfg.get("packaging_github_repo_owner", "sanyingkeji")
        repo_name = cfg.get("packaging_github_repo_name", "ai-perf")
        api_key = cfg.get("packaging_github_api_key", "")
        
        if not api_key:
            QMessageBox.warning(
                self,
                "需要配置",
                "私有仓库需要 GitHub API Key 才能下载。\n\n"
                "请在 设置 → 打包配置 中配置 GitHub API Key。"
            )
            return
        
        # 获取脚本路径（可自定义）
        script_path_str = self._sign_script_path or self._sign_script_default
        script_path = Path(script_path_str).expanduser()
        if not script_path.is_absolute():
            script_path = Path(self._project_root) / script_path
        if not script_path.exists():
            QMessageBox.warning(
                self,
                "错误",
                f"找不到脚本文件：\n{script_path}"
            )
            return
        
        # 构建命令（使用--download-url和--arch参数，跳过从GitHub获取assets的步骤）
        python_cmd = sys.executable
        cmd = [
            python_cmd,
            str(script_path),
            client_type,
            tag_name,
            repo_owner,
            repo_name,
            api_key,
            "--download-url", asset_url,
            "--arch", arch
        ]
        
        # 显示确认对话框
        reply = QMessageBox.question(
            self,
            "确认执行",
            f"将执行以下操作：\n\n"
            f"1. 下载文件并自动签名和公证\n"
            f"2. 打包成 DMG 和 PKG\n"
            f"3. 保存到对应目录\n\n"
            f"文件：{asset_name}\n"
            f"客户端类型：{client_type}\n"
            f"架构：{arch}\n\n"
            f"是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 执行脚本（在后台线程中执行，并显示输出）
        # 使用asset名称作为TAB名称（简化显示）
        tab_name = asset_name
        if len(tab_name) > 30:
            tab_name = tab_name[:27] + "..."
        self._execute_sign_script(cmd, tab_name, asset_name)
    
    def _on_upload_signed_package(self, asset_data: Dict[str, Any]):
        """上传已签名包到服务器：查找本地已签名的 dmg 包并上传（仅 macOS，仅 dmg，不传 pkg）"""
        # 获取 asset 信息
        asset_name = asset_data.get("asset_name", "")
        platform = asset_data.get("platform", "")
        version = asset_data.get("version", "")
        
        # 检查平台
        if platform != "macOS":
            QMessageBox.warning(self, "错误", "此功能仅支持 macOS 平台")
            return
        
        if not version:
            QMessageBox.warning(self, "错误", "无法获取版本号")
            return
        
        # 从asset名称中提取客户端类型和架构（和"下载并签名"的逻辑一样）
        asset_name_lower = asset_name.lower()
        client_type = None
        arch = None
        
        # 检查是否包含employee或client
        if "employee" in asset_name_lower or "client" in asset_name_lower or "ai.perf.client" in asset_name_lower or "ai-perf-client" in asset_name_lower:
            client_type = "employee"
        # 检查是否包含admin
        elif "admin" in asset_name_lower or "ai-perf-admin" in asset_name_lower or "ai.perf.admin" in asset_name_lower:
            client_type = "admin"
        
        if not client_type:
            QMessageBox.warning(
                self,
                "错误",
                f"无法从文件名中识别客户端类型：{asset_name}\n\n"
                f"文件名应包含 'employee'、'client' 或 'admin' 关键字。"
            )
            return
        
        # 从文件名中提取架构
        arch = self._infer_macos_arch_from_asset_name(asset_name_lower)
        
        if not arch:
            arch = self._prompt_macos_arch(asset_name)
            if not arch:
                return
        
        # 构建已签名包的路径：{client_dir}/dist/from_github/{client_type}/{arch}/
        client_dir = Path(self._project_root) / ("ui_client" if client_type == "employee" else "admin_ui_client")
        signed_package_dir = client_dir / "dist" / "from_github" / client_type / arch
        
        if not signed_package_dir.exists():
            QMessageBox.warning(
                self,
                "错误",
                f"找不到已签名包目录：\n{signed_package_dir}\n\n请先执行'下载并签名'功能生成已签名包。"
            )
            return
        
        # 查找 dmg 文件（不找 pkg）
        dmg_files = list(signed_package_dir.glob("*.dmg"))
        
        if not dmg_files:
            QMessageBox.warning(
                self,
                "错误",
                f"在目录中未找到已签名的 dmg 文件：\n{signed_package_dir}\n\n请先执行'下载并签名'功能生成已签名包。"
            )
            return
        
        # 如果有多个 dmg 文件，选择最新的
        if len(dmg_files) > 1:
            # 按修改时间排序，选择最新的
            dmg_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        dmg_path = dmg_files[0]
        dmg_name = dmg_path.name
        
        # 将平台名称转换为上传接口需要的格式
        upload_platform = "darwin"  # macOS 对应 darwin
        
        # 创建上传TAB
        tab_name = f"上传已签名包 {dmg_name}"
        if len(tab_name) > 30:
            tab_name = tab_name[:27] + "..."
        
        # 创建新的输出TAB
        output_text = self._create_output_text_edit()
        output_text.setPlaceholderText(f"正在上传已签名包到服务器：{dmg_name}...")
        
        # 添加到TAB
        tab_index = self.output_tabs.addTab(output_text, tab_name)
        self.output_tabs.setCurrentIndex(tab_index)  # 切换到新TAB
        
        # 存储TAB信息
        upload_tab_key = f"upload_signed_{tab_name}"
        if not hasattr(self, '_upload_tabs'):
            self._upload_tabs = {}
        self._upload_tabs[upload_tab_key] = {
            "widget": output_text,
            "full_name": dmg_name,
            "save_path": dmg_path,
            "upload_platform": upload_platform,
            "version": version,
            "download_dir": signed_package_dir
        }
        
        # 输出初始信息
        self._append_output_to_widget(output_text, "=" * 50 + "\n")
        self._append_output_to_widget(output_text, f"上传已签名包到服务器：{dmg_name}\n")
        self._append_output_to_widget(output_text, f"版本：{version}\n")
        self._append_output_to_widget(output_text, f"平台：{upload_platform}\n")
        self._append_output_to_widget(output_text, f"文件路径：{dmg_path}\n")
        self._append_output_to_widget(output_text, "=" * 50 + "\n\n")
        
        # 直接开始上传（不需要下载）
        self._append_output_to_widget(output_text, "开始上传到服务器...\n")
        self._start_upload_to_server(upload_tab_key, str(dmg_path))
    
    def _execute_sign_script(self, cmd: List[str], tab_name: str, full_asset_name: str):
        """执行签名脚本并显示输出（在独立的TAB中）"""
        # 创建新的输出TAB
        output_text = self._create_output_text_edit()
        output_text.setPlaceholderText(f"正在执行签名和公证任务：{full_asset_name}...")
        
        # 添加到TAB
        tab_index = self.output_tabs.addTab(output_text, tab_name)
        self.output_tabs.setCurrentIndex(tab_index)  # 切换到新TAB
        
        # 创建进程
        sign_process = QProcess(self)
        
        # 存储TAB和进程的映射关系
        self._sign_tabs[tab_name] = {
            "widget": output_text,
            "process": sign_process,
            "full_name": full_asset_name
        }
        
        # 连接信号（使用lambda捕获tab_name）
        sign_process.readyReadStandardOutput.connect(
            lambda: self._append_output_to_tab(tab_name, sign_process.readAllStandardOutput().data().decode('utf-8', errors='replace'))
        )
        sign_process.readyReadStandardError.connect(
            lambda: self._append_output_to_tab(tab_name, sign_process.readAllStandardError().data().decode('utf-8', errors='replace'), is_error=True)
        )
        sign_process.finished.connect(
            lambda exit_code, exit_status: self._on_sign_script_finished(tab_name, exit_code, exit_status)
        )
        
        # 输出初始信息
        self._append_output_to_tab(tab_name, "=" * 50)
        self._append_output_to_tab(tab_name, f"执行签名和公证脚本...")
        self._append_output_to_tab(tab_name, f"文件：{full_asset_name}")
        self._append_output_to_tab(tab_name, f"命令: {' '.join(cmd)}")
        self._append_output_to_tab(tab_name, "=" * 50)
        self._append_output_to_tab(tab_name, "")
        
        # 启动进程
        sign_process.start(cmd[0], cmd[1:])
        
        if not sign_process.waitForStarted(5000):
            QMessageBox.warning(self, "错误", "无法启动签名脚本")
            # 移除失败的TAB
            self._close_sign_tab(tab_name)
            return
    
    def _append_output_to_tab(self, tab_name: str, text: str, is_error: bool = False):
        """向指定TAB输出文本"""
        if tab_name not in self._sign_tabs:
            return
        
        output_text = self._sign_tabs[tab_name]["widget"]
        self._append_output_to_widget(output_text, text, is_error)
    
    def _append_output_to_widget(self, widget: QTextEdit, text: str, is_error: bool = False):
        """向指定的QTextEdit输出文本"""
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if is_error:
            # 错误信息使用红色
            error_format = QTextCharFormat()
            error_format.setForeground(QColor("#FF6B6B"))
            cursor.setCharFormat(error_format)
        else:
            # 正常输出使用默认格式
            default_format = QTextCharFormat()
            default_format.setForeground(QColor("#FFFFFF"))
            cursor.setCharFormat(default_format)
        
        cursor.insertText(text)
        widget.setTextCursor(cursor)
        
        # 自动滚动到底部
        scrollbar = widget.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _close_output_tab(self, index: int):
        """关闭输出TAB"""
        tab_name = self.output_tabs.tabText(index)
        
        # Github 输出 TAB 不能被关闭
        if tab_name == "Github 输出":
            return
        
        # 如果是签名任务TAB，需要停止进程
        if tab_name in self._sign_tabs:
            self._close_sign_tab(tab_name)
        # 如果是上传任务TAB，需要停止worker
        elif hasattr(self, '_upload_tabs'):
            # 查找匹配的上传TAB
            for tab_key, tab_info in list(self._upload_tabs.items()):
                if tab_info.get("widget") == self.output_tabs.widget(index):
                    # 停止worker（如果还在运行）
                    if "worker" in tab_info:
                        worker = tab_info["worker"]
                        if hasattr(worker, 'stop'):
                            worker.stop()
                    # 移除TAB
                    widget = tab_info["widget"]
                    self.output_tabs.removeTab(index)
                    if widget:
                        widget.deleteLater()
                    # 清理资源
                    del self._upload_tabs[tab_key]
                    return
        # 如果是下载任务TAB，直接移除
        elif hasattr(self, '_download_tabs'):
            # 查找匹配的下载TAB
            for tab_key, tab_info in list(self._download_tabs.items()):
                if tab_info.get("widget") == self.output_tabs.widget(index):
                    # 移除TAB
                    widget = tab_info["widget"]
                    self.output_tabs.removeTab(index)
                    if widget:
                        widget.deleteLater()
                    # 清理资源
                    del self._download_tabs[tab_key]
                    return
        
        # 普通TAB，直接移除
        widget = self.output_tabs.widget(index)
        self.output_tabs.removeTab(index)
        if widget:
            widget.deleteLater()
    
    def _close_sign_tab(self, tab_name: str):
        """关闭签名任务TAB（停止进程并移除TAB）"""
        if tab_name not in self._sign_tabs:
            return
        
        tab_info = self._sign_tabs[tab_name]
        process = tab_info["process"]
        widget = tab_info["widget"]
        
        # 停止进程（如果还在运行）
        if process.state() != QProcess.NotRunning:
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(1000)
        
        # 移除TAB
        for i in range(self.output_tabs.count()):
            if self.output_tabs.tabText(i) == tab_name:
                self.output_tabs.removeTab(i)
                break
        
        # 清理资源
        widget.deleteLater()
        process.deleteLater()
        del self._sign_tabs[tab_name]
    
    def _on_sign_script_finished(self, tab_name: str, exit_code: int, exit_status: int):
        """签名脚本执行完成"""
        if tab_name not in self._sign_tabs:
            return
        
        self._append_output_to_tab(tab_name, "")
        self._append_output_to_tab(tab_name, "=" * 50)
        
        full_asset_name = self._sign_tabs[tab_name]["full_name"]
        
        if exit_code == 0:
            self._append_output_to_tab(tab_name, "✓ 签名和公证完成")
            QMessageBox.information(
                self,
                "完成",
                f"签名和公证流程已完成！\n\n"
                f"文件：{full_asset_name}\n\n"
                f"请查看输出日志了解详细信息。"
            )
        else:
            self._append_output_to_tab(tab_name, f"✗ 签名和公证失败（退出码：{exit_code}）")
            QMessageBox.warning(
                self,
                "失败",
                f"签名和公证流程失败（退出码：{exit_code}）\n\n"
                f"文件：{full_asset_name}\n\n"
                f"请查看输出日志了解错误详情。"
            )
        self._append_output_to_tab(tab_name, "=" * 50)
    
    def _on_upload_to_server(self, asset_data: Dict[str, Any]):
        """下载并同步到服务器：先下载文件，然后上传到服务器（输出到TAB）"""
        # 获取 asset 信息
        asset_name = asset_data.get("asset_name", "")
        asset_url = asset_data.get("asset_url", "")
        platform = asset_data.get("platform", "")
        version = asset_data.get("version", "")
        
        if not asset_url:
            QMessageBox.warning(self, "错误", "该文件没有下载链接")
            return
        
        if not version:
            QMessageBox.warning(self, "错误", "无法获取版本号")
            return
        
        # 确定客户端类型（从文件名中提取）
        asset_name_lower = asset_name.lower()
        client_type = None
        if "employee" in asset_name_lower or "client" in asset_name_lower or "ai.perf.client" in asset_name_lower or "ai-perf-client" in asset_name_lower:
            client_type = "employee"
        elif "admin" in asset_name_lower or "ai-perf-admin" in asset_name_lower or "ai.perf.admin" in asset_name_lower:
            client_type = "admin"
        
        if not client_type:
            QMessageBox.warning(self, "错误", "无法从文件名中识别客户端类型")
            return
        
        # 确定架构（macOS 需要）
        arch = None
        if platform == "macOS":
            arch = self._infer_macos_arch_from_asset_name(asset_name_lower)
            if not arch:
                arch = self._prompt_macos_arch(asset_name)
                if not arch:
                    return
        
        # 将平台名称转换为上传接口需要的格式
        platform_map = {
            "macOS": "darwin",
            "Windows": "windows",
            "Linux": "linux"
        }
        upload_platform = platform_map.get(platform, "darwin")
        
        # 构建下载路径：dist/from_github/{client_type}/{arch或操作系统}/
        # 对于 macOS: dist/from_github/{client_type}/{arch}/
        # 对于 Windows/Linux: dist/from_github/{client_type}/{platform}/
        # 员工端使用 ui_client，管理端使用 admin_ui_client（与签名脚本保持一致）
        client_dir = Path(self._project_root) / ("ui_client" if client_type == "employee" else "admin_ui_client")
        if platform == "macOS" and arch:
            download_dir = client_dir / "dist" / "from_github" / client_type / arch
        else:
            # Windows 或 Linux
            platform_lower = platform.lower()
            download_dir = client_dir / "dist" / "from_github" / client_type / platform_lower
        
        download_dir.mkdir(parents=True, exist_ok=True)
        save_path = download_dir / asset_name
        
        # 创建上传TAB
        tab_name = f"上传 {asset_name}"
        if len(tab_name) > 30:
            tab_name = tab_name[:27] + "..."
        self._create_upload_tab(tab_name, asset_name, save_path, asset_url, upload_platform, version, download_dir)
    
    def _create_upload_tab(self, tab_name: str, full_asset_name: str, save_path: Path, asset_url: str, upload_platform: str, version: str, download_dir: Path):
        """创建上传任务TAB并开始下载"""
        # 创建新的输出TAB
        output_text = self._create_output_text_edit()
        output_text.setPlaceholderText(f"正在上传文件到服务器：{full_asset_name}...")
        
        # 添加到TAB
        tab_index = self.output_tabs.addTab(output_text, tab_name)
        self.output_tabs.setCurrentIndex(tab_index)  # 切换到新TAB
        
        # 存储TAB信息
        upload_tab_key = f"upload_{tab_name}"
        if not hasattr(self, '_upload_tabs'):
            self._upload_tabs = {}
        self._upload_tabs[upload_tab_key] = {
            "widget": output_text,
            "full_name": full_asset_name,
            "save_path": save_path,
            "upload_platform": upload_platform,
            "version": version,
            "download_dir": download_dir
        }
        
        # 输出初始信息
        self._append_output_to_widget(output_text, "=" * 50 + "\n")
        self._append_output_to_widget(output_text, f"上传文件到服务器：{full_asset_name}\n")
        self._append_output_to_widget(output_text, f"版本：{version}\n")
        self._append_output_to_widget(output_text, f"平台：{upload_platform}\n")
        self._append_output_to_widget(output_text, "=" * 50 + "\n\n")
        
        # 检查文件是否已存在且完整
        if save_path.exists() and save_path.is_file():
            file_size = save_path.stat().st_size
            if file_size > 0:
                # 尝试获取远程文件大小进行对比
                try:
                    import httpx
                    head_response = httpx.head(asset_url, timeout=10.0, follow_redirects=True)
                    remote_size = int(head_response.headers.get("content-length", 0))
                    if remote_size > 0 and file_size == remote_size:
                        # 文件已存在且大小匹配，跳过下载，直接上传
                        file_size_mb = file_size / (1024 * 1024)
                        self._append_output_to_widget(output_text, f"✓ 文件已存在，跳过下载\n")
                        self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n")
                        self._append_output_to_widget(output_text, f"  文件路径: {save_path}\n\n")
                        self._append_output_to_widget(output_text, "开始上传到服务器...\n")
                        self._start_upload_to_server(upload_tab_key, str(save_path))
                        return
                except Exception as e:
                    self._append_output_to_widget(output_text, f"  无法验证远程文件大小，将重新下载: {e}\n\n")
        
        # 开始下载
        self._append_output_to_widget(output_text, f"步骤 1/2: 下载文件\n")
        self._append_output_to_widget(output_text, f"  下载路径: {save_path}\n\n")
        
        # 创建下载 Worker
        worker = _DownloadSingleAssetWorker(asset_url, str(save_path))
        worker.signals.finished.connect(
            lambda path: self._on_upload_download_finished(upload_tab_key, path)
        )
        worker.signals.error.connect(
            lambda msg: self._on_upload_download_error(upload_tab_key, msg)
        )
        worker.signals.progress.connect(
            lambda downloaded, total: self._on_upload_download_progress(upload_tab_key, downloaded, total)
        )
        QThreadPool.globalInstance().start(worker)
    
    def _on_upload_download_finished(self, tab_key: str, save_path: str):
        """上传任务中的下载完成"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        try:
            file_size = Path(save_path).stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            self._append_output_to_widget(output_text, f"\n✓ 下载完成\n")
            self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n\n")
            self._append_output_to_widget(output_text, "步骤 2/2: 上传到服务器...\n")
            self._start_upload_to_server(tab_key, save_path)
        except Exception as e:
            self._append_output_to_widget(output_text, f"\n✓ 下载完成\n")
            self._append_output_to_widget(output_text, f"  文件路径: {save_path}\n\n")
            self._append_output_to_widget(output_text, "步骤 2/2: 上传到服务器...\n")
            self._start_upload_to_server(tab_key, save_path)
    
    def _on_upload_download_error(self, tab_key: str, error_msg: str):
        """上传任务中的下载错误"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        self._append_output_to_widget(output_text, f"\n✗ 下载失败: {error_msg}\n", is_error=True)
    
    def _on_upload_download_progress(self, tab_key: str, downloaded_bytes: int, total_bytes: int):
        """上传任务中的下载进度更新"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        if total_bytes > 0:
            percent = (downloaded_bytes / total_bytes) * 100
            downloaded_mb = downloaded_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            # 更新最后一行进度信息
            cursor = output_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            progress_text = f"  进度: {percent:.1f}% ({downloaded_mb:.2f}/{total_mb:.2f} MB)\r"
            cursor.insertText(progress_text)
            output_text.setTextCursor(cursor)
            
            # 自动滚动到底部
            scrollbar = output_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def _start_upload_to_server(self, tab_key: str, file_path: str):
        """开始上传到服务器"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        upload_platform = tab_info["upload_platform"]
        version = tab_info["version"]
        
        # 检查文件是否存在
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            self._append_output_to_widget(output_text, f"\n✗ 错误：文件不存在: {file_path}\n", is_error=True)
            return
        
        # 检查文件大小（通常服务器限制为 100MB，这里设置为 90MB 作为警告阈值）
        file_size = file_path_obj.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        max_size_mb = 1024  # 服务器限制（MB）
        
        if file_size_mb > max_size_mb:
            self._append_output_to_widget(output_text, f"\n✗ 错误：文件过大，无法上传\n", is_error=True)
            self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n", is_error=True)
            self._append_output_to_widget(output_text, f"  服务器限制: {max_size_mb} MB\n", is_error=True)
            self._append_output_to_widget(output_text, f"  建议：请使用其他方式上传大文件\n", is_error=True)
            QMessageBox.warning(
                self,
                "文件过大",
                f"文件大小 ({file_size_mb:.2f} MB) 超过服务器限制 ({max_size_mb} MB)，无法上传。\n\n"
                f"请使用其他方式上传大文件。"
            )
            return
        
        # 从配置读取上传API地址
        cfg = ConfigManager.load()
        upload_api_url = cfg.get("upload_api_url", "http://127.0.0.1:8882/api/upload")
        
        if not upload_api_url:
            self._append_output_to_widget(output_text, f"\n✗ 错误：未配置上传API地址\n", is_error=True)
            return
        
        self._append_output_to_widget(output_text, f"  上传API: {upload_api_url}\n")
        self._append_output_to_widget(output_text, f"  平台: {upload_platform}\n")
        self._append_output_to_widget(output_text, f"  版本: {version}\n")
        self._append_output_to_widget(output_text, f"  文件大小: {file_size_mb:.2f} MB\n\n")
        
        # 创建上传Worker（使用断点续传）
        upload_worker = _UploadFileWorker(file_path, upload_platform, version, upload_api_url)
        
        # 连接信号
        upload_worker.signals.finished.connect(
            lambda url: self._on_upload_to_server_finished(tab_key, url)
        )
        upload_worker.signals.error.connect(
            lambda msg: self._on_upload_to_server_error(tab_key, msg)
        )
        upload_worker.signals.progress.connect(
            lambda uploaded, total: self._on_upload_progress(tab_key, uploaded, total)
        )
        upload_worker.signals.chunk_progress.connect(
            lambda current_chunk, total_chunks, chunk_percent: self._on_upload_chunk_progress(tab_key, current_chunk, total_chunks, chunk_percent)
        )
        
        # 存储worker引用
        tab_info["worker"] = upload_worker
        
        # 启动上传
        if not hasattr(self, '_thread_pool'):
            self._thread_pool = QThreadPool()
        self._thread_pool.start(upload_worker)
    
    def _on_upload_progress(self, tab_key: str, uploaded_bytes: int, total_bytes: int):
        """上传进度更新（总体进度）"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        if total_bytes > 0:
            percent = (uploaded_bytes / total_bytes) * 100
            uploaded_mb = uploaded_bytes / (1024 * 1024)
            total_mb = total_bytes / (1024 * 1024)
            
            # 更新最后一行进度信息
            cursor = output_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            progress_text = f"  总进度: {percent:.1f}% ({uploaded_mb:.2f}/{total_mb:.2f} MB)\r"
            cursor.insertText(progress_text)
            output_text.setTextCursor(cursor)
            
            # 自动滚动到底部
            scrollbar = output_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def _on_upload_chunk_progress(self, tab_key: str, current_chunk: int, total_chunks: int, chunk_percent: int):
        """上传分片进度更新"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        # 更新分片进度信息（在总进度下方）
        cursor = output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # 如果当前行是总进度，先换行
        cursor.movePosition(QTextCursor.StartOfLine)
        line_text = cursor.block().text()
        if "总进度" in line_text:
            cursor.movePosition(QTextCursor.End)
            cursor.insertText("\n")
        
        # 更新分片进度
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        chunk_progress_text = f"  分片进度: {current_chunk}/{total_chunks} ({chunk_percent}%)\r"
        cursor.insertText(chunk_progress_text)
        output_text.setTextCursor(cursor)
        
        # 自动滚动到底部
        scrollbar = output_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_upload_to_server_finished(self, tab_key: str, download_url: str):
        """上传到服务器完成"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        # 清除进度行
        cursor = output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText("\n")
        
        # 复制URL到剪贴板
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(download_url)
        
        self._append_output_to_widget(output_text, f"\n✓ 上传成功\n")
        self._append_output_to_widget(output_text, f"  下载URL: {download_url}\n")
        self._append_output_to_widget(output_text, f"  URL已复制到剪贴板\n")
        
        # 显示成功消息
        QMessageBox.information(
            self,
            "上传成功",
            f"文件上传成功！\n\n"
            f"下载URL：\n{download_url}\n\n"
            f"URL已复制到剪贴板，可以直接粘贴到版本管理中。"
        )
        
        # 清理worker引用
        if "worker" in tab_info:
            del tab_info["worker"]
    
    def _on_upload_to_server_error(self, tab_key: str, error_msg: str):
        """上传到服务器错误"""
        if tab_key not in self._upload_tabs:
            return
        
        tab_info = self._upload_tabs[tab_key]
        output_text = tab_info["widget"]
        
        # 清除进度行
        cursor = output_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.movePosition(QTextCursor.StartOfLine, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText("\n")
        
        self._append_output_to_widget(output_text, f"\n✗ 上传失败: {error_msg}\n", is_error=True)
        
        # 如果错误信息包含"断点续传"，提示用户可以继续上传
        if "断点续传" in error_msg or "可以稍后继续" in error_msg:
            self._append_output_to_widget(output_text, f"  提示：可以重新上传，系统会自动从上次中断处继续\n", is_error=False)
        
        QMessageBox.warning(self, "上传失败", f"文件上传失败：{error_msg}")
        
        # 清理worker引用
        if "worker" in tab_info:
            del tab_info["worker"]
    
    def _on_download_finished(self, saved_files: List[str]):
        """下载完成"""
        if self._download_progress:
            self._download_progress.close()
            self._download_progress = None
        
        file_list = "\n".join([f"  • {f}" for f in saved_files[:10]])
        if len(saved_files) > 10:
            file_list += f"\n  ... 还有 {len(saved_files) - 10} 个文件"
        
        QMessageBox.information(
            self,
            "下载完成",
            f"已下载 {len(saved_files)} 个文件：\n\n{file_list}"
        )
    
    def _on_download_error(self, error_msg: str):
        """下载错误"""
        if self._download_progress:
            self._download_progress.close()
            self._download_progress = None
        
        QMessageBox.warning(self, "下载失败", f"下载失败：{error_msg}")
    
    def _on_download_progress(self, current: int, total: int):
        """下载进度更新"""
        if self._download_progress:
            self._download_progress.setValue(current)
            self._download_progress.setLabelText(f"正在下载文件 {current}/{total}...")
    
    def _on_upload_clicked(self):
        """上传按钮点击事件（保留此方法以兼容旧代码，但不再使用）"""
        # 此方法已不再使用，保留以避免错误
        pass
        
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
    
    def _append_output(self, text: str, is_error: bool = False):
        """向默认的Git Push TAB输出文本（保持向后兼容）"""
        cursor = self.output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # 如果是错误，使用红色格式
        if is_error:
            error_format = QTextCharFormat(self._default_format)
            error_format.setForeground(QColor("#FF6B6B"))
            cursor.setCharFormat(error_format)
        else:
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
            self._append_output("\n$ git commit -m \"update some code\"\n")
            self._process.start("git", ["commit", "-m", "update some code"])
        
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
                            self._current_version = self._version_manager.get_current_version() or "0.0.0"
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
                        self._current_version = self._version_manager.get_current_version() or "0.0.0"
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
                                from datetime import datetime, timezone
                                # 解析 UTC 时间
                                dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                # 如果时区信息为空，设置为 UTC
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                # 转换为本地时间
                                local_dt = dt.astimezone()
                                time_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                                
                                # 如果已完成，显示运行时长
                                if status == "completed" and updated_at:
                                    end_dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                                    if end_dt.tzinfo is None:
                                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                                    # 计算时长（使用 UTC 时间计算，避免时区转换问题）
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
                            except Exception as e:
                                # 如果解析失败，使用原始字符串
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
                        view_logs_btn = QPushButton("下载日志")
                        view_logs_btn.setFixedSize(70, 22)  # 宽度稍大以适应"查看日志"文本
                        view_logs_btn.setStyleSheet("font-size: 9pt; padding: 0px;")
                        view_logs_btn.clicked.connect(lambda *_, rid=run_id, rurl=run_url, parent_dlg=dialog: self._view_workflow_logs(rid, rurl, api_url, api_key, repo_owner, repo_name, parent_dlg))
                        btn_layout.addWidget(view_logs_btn)
                        
                        # Cancel-run 按钮（只有正在运行的工作流才能取消）
                        if status == "in_progress":
                            cancel_btn = QPushButton("Cancel-run")
                            cancel_btn.setFixedSize(75, 22)  # mini 按钮样式，与健康检查一致
                            cancel_btn.setStyleSheet("""
                                QPushButton {
                                    background-color: #dc3545;
                                    color: white;
                                    border: none;
                                    border-radius: 3px;
                                    font-size: 9pt;
                                    padding: 0px;
                                }
                                QPushButton:hover {
                                    background-color: #c82333;
                                }
                            """)
                            cancel_btn.clicked.connect(lambda *_, rid=run_id: self._cancel_workflow(rid, api_url, api_key, repo_owner, repo_name, load_workflow_runs, parent=dialog))
                            btn_layout.addWidget(cancel_btn)
                        
                        # Re-run 按钮（只有已完成的工作流才能重新运行）
                        if status == "completed":
                            rerun_btn = QPushButton("Re-run")
                            rerun_btn.setFixedSize(60, 22)  # mini 按钮样式，与健康检查一致
                            rerun_btn.setStyleSheet("font-size: 9pt; padding: 0px;")
                            rerun_btn.clicked.connect(lambda *_, rid=run_id, wid=workflow_id: self._rerun_workflow(rid, wid, api_url, api_key, repo_owner, repo_name, load_workflow_runs, parent=dialog))
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
        self._current_version = self._version_manager.get_current_version() or "0.0.0"
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
    
    def _view_workflow_logs(self, run_id: int, run_url: str, api_url: str, api_key: str, repo_owner: str, repo_name: str, parent_dialog=None):
        """下载工作流运行的日志到本地"""
        # 让用户选择保存位置
        default_filename = f"workflow_run_{run_id}_logs.zip"
        # 使用传入的对话框作为父窗口，如果没有则使用 self
        file_dialog_parent = parent_dialog if parent_dialog else self
        file_path, _ = QFileDialog.getSaveFileName(
            file_dialog_parent,
            "保存工作流日志",
            default_filename,
            "ZIP 文件 (*.zip);;文本文件 (*.txt);;所有文件 (*)"
        )
        
        if not file_path:
            # 用户取消了保存
            return
        
        # 创建自定义对话框来显示进度和调试信息
        progress_parent = parent_dialog if parent_dialog else self
        progress_dialog = QDialog(progress_parent)
        progress_dialog.setWindowTitle("下载日志")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.resize(600, 300)
        
        progress_layout = QVBoxLayout(progress_dialog)
        progress_layout.setSpacing(12)
        progress_layout.setContentsMargins(16, 16, 16, 16)
        
        # 进度条
        progress_label = QLabel("正在下载日志...")
        progress_label.setFont(QFont("Arial", 11))
        progress_layout.addWidget(progress_label)
        
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 0)  # 不确定进度
        progress_layout.addWidget(progress_bar)
        
        # 创建调试信息标签
        debug_label = QLabel("")
        debug_label.setWordWrap(True)
        debug_label.setStyleSheet("color: #666; font-size: 9pt; font-family: monospace; background-color: #f5f5f5; padding: 8px; border: 1px solid #ddd; border-radius: 4px;")
        debug_label.setMaximumHeight(150)
        debug_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        debug_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        progress_layout.addWidget(debug_label)
        
        progress_dialog.show()
        
        # 保存 worker 和 signals 对象的引用，防止被垃圾回收
        # 使用列表来保存引用，确保在方法作用域内可访问
        worker_refs = []  # 用于保存 worker 和 signals 对象
        
        class _LogsDownloadWorkerSignals(QObject):
            finished = Signal(str, bytes)  # file_path, file_data
            error = Signal(str)
            debug = Signal(str)  # 调试信息
            
            def __init__(self):
                super().__init__()
                # 确保信号在主线程中处理
                self.setObjectName("_LogsDownloadWorkerSignals")
        
        class _LogsDownloadWorker(QRunnable):
            def __init__(self, save_path: str, refs_list):
                super().__init__()
                self.save_path = save_path
                self.signals = _LogsDownloadWorkerSignals()
                # 保存 signals 对象的引用（通过参数传递 refs_list）
                refs_list.append(self.signals)
            
            @Slot()
            def run(self):
                try:
                    # 获取日志下载 URL
                    logs_url = f"{api_url}/repos/{repo_owner}/{repo_name}/actions/runs/{run_id}/logs"
                    
                    # 打印调试信息
                    debug_info = f"日志下载 URL: {logs_url}\n"
                    debug_info += f"API URL: {api_url}\n"
                    debug_info += f"仓库: {repo_owner}/{repo_name}\n"
                    debug_info += f"运行 ID: {run_id}\n"
                    debug_info += f"API Key 长度: {len(api_key) if api_key else 0} 字符\n"
                    print(f"[DEBUG] {debug_info}")  # 控制台输出
                    self.signals.debug.emit(debug_info)  # UI 输出
                    
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                        "Authorization": f"token {api_key}"
                    }
                    
                    # 下载日志 ZIP 文件（GitHub API 会返回重定向）
                    print(f"[DEBUG] 开始下载日志，URL: {logs_url}")
                    self.signals.debug.emit("开始下载日志...")
                    
                    # 使用流式下载，支持大文件和进度反馈
                    file_data = None
                    content_type = ""
                    with httpx.stream("GET", logs_url, headers=headers, follow_redirects=True, timeout=300.0) as response:
                        # 打印响应信息
                        content_type = response.headers.get("content-type", "")
                        response_info = f"响应状态码: {response.status_code}\n"
                        response_info += f"响应内容类型: {content_type}\n"
                        content_length = response.headers.get("content-length")
                        if content_length:
                            response_info += f"响应大小: {int(content_length):,} 字节 ({int(content_length) / (1024*1024):.2f} MB)\n"
                        else:
                            response_info += "响应大小: 未知（流式下载）\n"
                        response_info += f"最终 URL: {response.url}\n"
                        print(f"[DEBUG] {response_info}")
                        self.signals.debug.emit(response_info)
                        
                        if response.status_code == 404:
                            self.signals.error.emit("日志不存在或已过期（GitHub 日志通常保留 90 天）")
                            return
                        elif response.status_code != 200:
                            self.signals.error.emit(f"下载日志失败：HTTP {response.status_code}")
                            return
                        
                        # 流式读取内容
                        print(f"[DEBUG] 开始流式读取内容...")
                        self.signals.debug.emit("正在下载...")
                        content_chunks = []
                        total_size = 0
                        chunk_size = 8192  # 8KB chunks
                        
                        try:
                            for chunk in response.iter_bytes(chunk_size):
                                content_chunks.append(chunk)
                                total_size += len(chunk)
                                # 每下载 1MB 更新一次进度
                                if total_size % (1024 * 1024) < chunk_size:
                                    progress_info = f"已下载: {total_size:,} 字节 ({total_size / (1024*1024):.2f} MB)"
                                    print(f"[DEBUG] {progress_info}")
                                    self.signals.debug.emit(progress_info)
                        except Exception as e:
                            error_msg = f"下载过程中出错: {e}"
                            print(f"[DEBUG] {error_msg}")
                            self.signals.error.emit(error_msg)
                            return
                        
                        # 合并所有块
                        print(f"[DEBUG] 合并下载内容，总大小: {total_size:,} 字节")
                        self.signals.debug.emit(f"下载完成，总大小: {total_size:,} 字节 ({total_size / (1024*1024):.2f} MB)")
                        file_data = b''.join(content_chunks)
                    
                    # 检查是否是 ZIP 文件
                    is_zip = "zip" in content_type.lower() or (file_data and file_data.startswith(b"PK"))
                    
                    if is_zip:
                        # 如果是 ZIP 文件，直接保存
                        if self.save_path.endswith('.zip'):
                            # 用户选择了 ZIP 格式，直接保存
                            print(f"[DEBUG] 保存 ZIP 文件，大小: {len(file_data):,} 字节")
                            self.signals.debug.emit(f"保存 ZIP 文件...")
                            print(f"[DEBUG] 准备发送 finished 信号，save_path: {self.save_path}, file_data 大小: {len(file_data)}")
                            try:
                                self.signals.finished.emit(self.save_path, file_data)
                                print(f"[DEBUG] finished 信号已发送")
                            except Exception as e:
                                print(f"[DEBUG] 发送 finished 信号失败: {e}")
                                import traceback
                                traceback.print_exc()
                        else:
                            # 用户选择了文本格式，需要解压并合并
                            print(f"[DEBUG] 解压 ZIP 文件，大小: {len(file_data):,} 字节")
                            self.signals.debug.emit("解压 ZIP 文件...")
                            zip_data = io.BytesIO(file_data)
                            with zipfile.ZipFile(zip_data, 'r') as zip_ref:
                                file_list = zip_ref.namelist()
                                file_list.sort()
                                
                                # 合并所有日志文件
                                all_logs = []
                                for file_name in file_list:
                                    if file_name.endswith('.txt') or not '.' in file_name.split('/')[-1]:
                                        try:
                                            content = zip_ref.read(file_name).decode('utf-8', errors='replace')
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
                                    self.signals.finished.emit(self.save_path, log_content.encode('utf-8'))
                                else:
                                    self.signals.error.emit("日志文件为空或格式不正确")
                    else:
                        # 如果不是 ZIP，作为文本处理
                        print(f"[DEBUG] 处理文本内容，大小: {len(file_data):,} 字节")
                        self.signals.debug.emit("处理文本内容...")
                        try:
                            log_content = file_data.decode('utf-8')
                        except UnicodeDecodeError:
                            log_content = file_data.decode('utf-8', errors='replace')
                        self.signals.finished.emit(self.save_path, log_content.encode('utf-8'))
                
                except httpx.HTTPError as e:
                    self.signals.error.emit(f"网络错误：{e}")
                except zipfile.BadZipFile:
                    self.signals.error.emit("日志格式错误：无法解析 ZIP 文件")
                except Exception as e:
                    self.signals.error.emit(f"下载日志失败：{e}")
        
        def on_download_finished(save_path: str, file_data: bytes):
            print(f"[DEBUG] on_download_finished 被调用，文件路径: {save_path}, 数据大小: {len(file_data)} 字节")
            
            # 检查对话框是否仍然有效
            try:
                if not progress_dialog or not progress_dialog.isVisible():
                    print("[DEBUG] 对话框已关闭，跳过处理")
                    return
            except RuntimeError:
                print("[DEBUG] 对话框已被删除，跳过处理")
                return
            except Exception as e:
                print(f"[DEBUG] 检查对话框时出错: {e}")
                return
            
            # 先保存文件，再关闭对话框
            try:
                print("[DEBUG] 开始保存文件...")
                # 保存文件
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                
                file_size = len(file_data)
                size_mb = file_size / (1024 * 1024)
                size_str = f"{size_mb:.2f} MB" if size_mb >= 1 else f"{file_size / 1024:.2f} KB"
                
                print(f"[DEBUG] 文件保存成功，大小: {size_str}")
            except Exception as e:
                print(f"[DEBUG] 保存文件时出错: {e}")
                import traceback
                traceback.print_exc()
                try:
                    if progress_dialog and progress_dialog.isVisible():
                        progress_dialog.close()
                except:
                    pass
                try:
                    msg_parent = parent_dialog if parent_dialog else self
                    QMessageBox.critical(msg_parent, "保存失败", f"保存文件失败：{e}")
                except:
                    pass
                return
            
            # 关闭进度对话框（无论是否可见都尝试关闭）
            try:
                print("[DEBUG] 关闭进度对话框...")
                if progress_dialog and progress_dialog.isVisible():
                    progress_dialog.close()
                print("[DEBUG] 进度对话框已关闭")
            except RuntimeError as e:
                print(f"[DEBUG] 关闭对话框时出错（可能已关闭）: {e}")
            except Exception as e:
                print(f"[DEBUG] 关闭对话框时出错: {e}")
            
            # 显示完成消息
            try:
                msg_parent = parent_dialog if parent_dialog else self
                print(f"[DEBUG] 显示完成消息框，父窗口: {msg_parent}")
                QMessageBox.information(
                    msg_parent,
                    "下载完成",
                    f"日志已成功下载到：\n{save_path}\n\n文件大小：{size_str}"
                )
                print("[DEBUG] 完成消息框已显示")
            except Exception as e:
                print(f"[DEBUG] 显示消息框时出错: {e}")
                import traceback
                traceback.print_exc()
        
        def on_download_error(error_msg: str):
            try:
                if not progress_dialog or not progress_dialog.isVisible():
                    return
            except RuntimeError:
                return
            except Exception:
                return
            
            try:
                if progress_dialog:
                    progress_dialog.close()
                # 使用传入的对话框作为父窗口，如果没有则使用 self
                msg_parent = parent_dialog if parent_dialog else self
                QMessageBox.warning(
                    msg_parent,
                    "下载失败",
                    f"{error_msg}\n\n请检查：\n1. GitHub API Key 是否有足够权限\n2. 日志是否已过期（GitHub 日志通常保留 90 天）\n3. 网络连接是否正常"
                )
            except RuntimeError:
                pass
            except Exception as e:
                print(f"[DEBUG] on_download_error 异常: {e}")
        
        def on_debug_info(debug_msg: str):
            """显示调试信息"""
            try:
                if not progress_dialog or not progress_dialog.isVisible():
                    return
            except RuntimeError:
                return
            except Exception:
                return
            
            try:
                if not debug_label:
                    return
                # 更新调试标签
                current_text = debug_label.text()
                if current_text:
                    debug_label.setText(current_text + "\n" + debug_msg)
                else:
                    debug_label.setText(debug_msg)
                # 滚动到底部（通过设置对齐方式）
                debug_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            except RuntimeError:
                pass
            except Exception as e:
                print(f"[DEBUG] on_debug_info 异常: {e}")
        
        worker = _LogsDownloadWorker(file_path, worker_refs)
        # 保存 worker 引用
        worker_refs.append(worker)
        
        print(f"[DEBUG] 连接信号...")
        worker.signals.finished.connect(on_download_finished)
        worker.signals.error.connect(on_download_error)
        worker.signals.debug.connect(on_debug_info)
        print(f"[DEBUG] 信号已连接，启动 worker...")
        QThreadPool.globalInstance().start(worker)
        print(f"[DEBUG] Worker 已启动")
        
        # 在对话框关闭时断开信号连接并清理引用
        def on_dialog_finished():
            try:
                # 断开所有信号连接
                if worker and worker.signals:
                    try:
                        worker.signals.finished.disconnect()
                    except:
                        pass
                    try:
                        worker.signals.error.disconnect()
                    except:
                        pass
                    try:
                        worker.signals.debug.disconnect()
                    except:
                        pass
            except Exception as e:
                print(f"[DEBUG] 断开信号连接时出错: {e}")
            # 清理引用（允许垃圾回收）
            worker_refs.clear()
        
        # 连接对话框的 finished 信号
        progress_dialog.finished.connect(on_dialog_finished)
    
    def _rerun_workflow(self, run_id: int, workflow_id: int, api_url: str, api_key: str, repo_owner: str, repo_name: str, refresh_callback=None, parent=None):
        """重新运行工作流"""
        # 使用传入的父窗口，如果没有则使用 self
        parent_widget = parent if parent else self
        
        # 确认对话框
        reply = QMessageBox.question(
            parent_widget,
            "确认重新运行",
            f"确定要重新运行工作流运行 #{run_id} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 显示进度提示
        progress = QProgressDialog("正在重新运行工作流...", "取消", 0, 0, parent_widget)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)  # 不允许取消
        progress.setMinimumDuration(0)  # 立即显示
        progress.setAutoClose(True)  # 完成后自动关闭
        progress.setAutoReset(True)  # 完成后自动重置
        progress.setValue(0)
        progress.show()
        
        class _RerunWorkerSignals(QObject):
            finished = Signal(bool, str)  # success, message
        
        class _RerunWorker(QRunnable):
            def __init__(self, api_url, api_key, repo_owner, repo_name, run_id):
                super().__init__()
                self.signals = _RerunWorkerSignals()
                self.api_url = api_url
                self.api_key = api_key
                self.repo_owner = repo_owner
                self.repo_name = repo_name
                self.run_id = run_id
            
            @Slot()
            def run(self):
                try:
                    # 调用 GitHub API 重新运行工作流
                    rerun_url = f"{self.api_url}/repos/{self.repo_owner}/{self.repo_name}/actions/runs/{self.run_id}/rerun"
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                        "Authorization": f"token {self.api_key}"
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
                except httpx.TimeoutException as e:
                    self.signals.finished.emit(False, f"请求超时：{e}")
                except httpx.HTTPError as e:
                    self.signals.finished.emit(False, f"网络错误：{e}")
                except Exception as e:
                    # 确保所有异常都会发送信号
                    error_msg = f"重新运行失败：{type(e).__name__}: {str(e)}"
                    self.signals.finished.emit(False, error_msg)
        
        def on_rerun_finished(success: bool, message: str):
            # 关闭进度条
            try:
                # 对于不确定进度的对话框，设置为完成状态
                if hasattr(progress, 'setValue') and hasattr(progress, 'maximum'):
                    max_val = progress.maximum()
                    if max_val == 0:
                        # 不确定进度，设置为 1 表示完成
                        progress.setValue(1)
                    else:
                        progress.setValue(max_val)
                if hasattr(progress, 'reset'):
                    progress.reset()
                if hasattr(progress, 'hide'):
                    progress.hide()
                if hasattr(progress, 'close'):
                    progress.close()
            except RuntimeError:
                # progress 已被删除
                pass
            except Exception as e:
                # 其他异常，尝试强制关闭
                try:
                    if hasattr(progress, 'close'):
                        progress.close()
                except:
                    pass
            
            try:
                if not hasattr(parent_widget, 'isVisible') or not parent_widget.isVisible():
                    return
            except RuntimeError:
                # parent_widget 已被删除
                return
            
            try:
                if success:
                    QMessageBox.information(parent_widget, "成功", message)
                    # 刷新工作流列表
                    if refresh_callback:
                        try:
                            refresh_callback()
                        except RuntimeError:
                            # 回调中的对象已被删除
                            pass
                else:
                    QMessageBox.warning(parent_widget, "失败", message)
            except RuntimeError:
                # 对象已被删除
                pass
        
        worker = _RerunWorker(api_url, api_key, repo_owner, repo_name, run_id)
        worker.signals.finished.connect(on_rerun_finished)
        # 保存 worker 引用，防止被垃圾回收
        if not hasattr(parent_widget, '_rerun_workers'):
            parent_widget._rerun_workers = []
        parent_widget._rerun_workers.append(worker)
        QThreadPool.globalInstance().start(worker)
    
    def _cancel_workflow(self, run_id: int, api_url: str, api_key: str, repo_owner: str, repo_name: str, refresh_callback=None, parent=None):
        """取消正在运行的工作流"""
        # 使用传入的父窗口，如果没有则使用 self
        parent_widget = parent if parent else self
        
        # 确认对话框
        reply = QMessageBox.question(
            parent_widget,
            "确认取消",
            f"确定要取消工作流运行 #{run_id} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 显示进度提示
        progress = QProgressDialog("正在取消工作流...", "取消", 0, 0, parent_widget)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)  # 不允许取消
        progress.setMinimumDuration(0)  # 立即显示
        progress.setAutoClose(True)  # 完成后自动关闭
        progress.setAutoReset(True)  # 完成后自动重置
        progress.setValue(0)
        progress.show()
        
        class _CancelWorkerSignals(QObject):
            finished = Signal(bool, str)  # success, message
        
        class _CancelWorker(QRunnable):
            def __init__(self, api_url, api_key, repo_owner, repo_name, run_id):
                super().__init__()
                self.signals = _CancelWorkerSignals()
                self.api_url = api_url
                self.api_key = api_key
                self.repo_owner = repo_owner
                self.repo_name = repo_name
                self.run_id = run_id
            
            @Slot()
            def run(self):
                try:
                    cancel_url = f"{self.api_url}/repos/{self.repo_owner}/{self.repo_name}/actions/runs/{self.run_id}/cancel"
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aiperf-admin-client/1.0",
                        "Authorization": f"token {self.api_key}"
                    }
                    
                    # POST 请求取消运行
                    response = httpx.post(cancel_url, headers=headers, timeout=30)
                    
                    if response.status_code == 202:
                        self.signals.finished.emit(True, "工作流已成功取消")
                    elif response.status_code == 403:
                        self.signals.finished.emit(False, "权限不足，无法取消工作流。请检查 API Key 权限。")
                    elif response.status_code == 409:
                        self.signals.finished.emit(False, "工作流已完成或已取消，无法再次取消。")
                    else:
                        error_msg = f"取消失败：HTTP {response.status_code}"
                        try:
                            error_data = response.json()
                            if "message" in error_data:
                                error_msg += f" - {error_data['message']}"
                        except:
                            pass
                        self.signals.finished.emit(False, error_msg)
                except httpx.TimeoutException as e:
                    self.signals.finished.emit(False, f"请求超时：{e}")
                except httpx.HTTPError as e:
                    self.signals.finished.emit(False, f"网络错误：{e}")
                except Exception as e:
                    # 确保所有异常都会发送信号
                    import traceback
                    error_msg = f"取消失败：{type(e).__name__}: {str(e)}"
                    self.signals.finished.emit(False, error_msg)
        
        def on_cancel_finished(success: bool, message: str):
            # 关闭进度条
            try:
                # 对于不确定进度的对话框，设置为完成状态
                if hasattr(progress, 'setValue') and hasattr(progress, 'maximum'):
                    max_val = progress.maximum()
                    if max_val == 0:
                        # 不确定进度，设置为 1 表示完成
                        progress.setValue(1)
                    else:
                        progress.setValue(max_val)
                if hasattr(progress, 'reset'):
                    progress.reset()
                if hasattr(progress, 'hide'):
                    progress.hide()
                if hasattr(progress, 'close'):
                    progress.close()
            except RuntimeError:
                # progress 已被删除
                pass
            except Exception as e:
                # 其他异常，尝试强制关闭
                try:
                    if hasattr(progress, 'close'):
                        progress.close()
                except:
                    pass
            
            try:
                if not hasattr(parent_widget, 'isVisible') or not parent_widget.isVisible():
                    return
            except RuntimeError:
                # parent_widget 已被删除
                return
            
            try:
                if success:
                    QMessageBox.information(parent_widget, "成功", message)
                    # 刷新工作流列表
                    if refresh_callback:
                        try:
                            refresh_callback()
                        except RuntimeError:
                            # 回调中的对象已被删除
                            pass
                else:
                    QMessageBox.warning(parent_widget, "失败", message)
            except RuntimeError:
                # 对象已被删除
                pass
        
        worker = _CancelWorker(api_url, api_key, repo_owner, repo_name, run_id)
        worker.signals.finished.connect(on_cancel_finished)
        # 保存 worker 引用，防止被垃圾回收
        if not hasattr(parent_widget, '_cancel_workers'):
            parent_widget._cancel_workers = []
        parent_widget._cancel_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

