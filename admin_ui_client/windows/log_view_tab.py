#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志查看 TAB：通过SSH获取服务器日志文件列表，支持查看和下载
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QMessageBox,
    QTextEdit, QSplitter, QFrame, QSizePolicy, QMenu
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QTimer

from utils.config_manager import ConfigManager
from utils.ssh_client import SSHClient
from widgets.toast import Toast


class _LogListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _LogListWorker(QRunnable):
    """后台线程：通过SSH获取日志文件列表"""
    def __init__(self, ssh_config: Dict[str, Any], log_dir: str = "/ai-perf/logs"):
        super().__init__()
        self.signals = _LogListWorkerSignals()
        self._ssh_config = ssh_config
        self._log_dir = log_dir

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
            
            # 列出日志目录下的所有文件（递归）
            result = ssh.list_files(self._log_dir, recursive=True)
            ssh.close()
            
            if not result["success"]:
                self.signals.error.emit(result.get("error", "获取日志列表失败"))
                return
            
            # 过滤出文件（排除目录），并按修改时间排序
            files = [f for f in result["files"] if not f["is_dir"]]
            files.sort(key=lambda x: x["mtime"], reverse=True)
            
            # 格式化文件信息
            items = []
            for f in files:
                # 计算文件大小（MB）
                size_mb = f["size"] / (1024 * 1024)
                
                # 格式化修改时间
                try:
                    mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    mtime_str = "未知"
                
                items.append({
                    "name": f["name"],
                    "path": f["path"],
                    "rel_path": f["rel_path"],
                    "size": f["size"],
                    "size_mb": size_mb,
                    "mtime": mtime_str,
                })
            
            self.signals.finished.emit(items)
        except Exception as e:
            self.signals.error.emit(f"获取日志列表失败：{e}")


class _LogContentWorkerSignals(QObject):
    finished = Signal(str, str)  # 文件路径, 内容
    error = Signal(str, str)  # 文件路径, 错误消息


class _LogContentWorker(QRunnable):
    """后台线程：通过SSH读取日志文件内容"""
    def __init__(self, file_path: str, ssh_config: Dict[str, Any], max_size: int = 10 * 1024 * 1024):
        super().__init__()
        self.signals = _LogContentWorkerSignals()
        self._file_path = file_path
        self._ssh_config = ssh_config
        self._max_size = max_size

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit(self._file_path, "请先配置SSH服务器信息")
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
                self.signals.error.emit(self._file_path, "SSH连接失败，请检查配置")
                return
            
            # 读取文件内容
            result = ssh.read_file(self._file_path, max_size=self._max_size)
            ssh.close()
            
            if result["success"]:
                self.signals.finished.emit(self._file_path, result["content"])
            else:
                self.signals.error.emit(self._file_path, result.get("error", "读取文件失败"))
        except Exception as e:
            self.signals.error.emit(self._file_path, f"读取文件失败：{e}")


class _LogDownloadWorkerSignals(QObject):
    finished = Signal(str, str)  # 文件路径, 本地保存路径
    error = Signal(str, str)  # 文件路径, 错误消息


class _LogDownloadWorker(QRunnable):
    """后台线程：通过SSH下载日志文件"""
    def __init__(self, file_path: str, local_path: str, ssh_config: Dict[str, Any]):
        super().__init__()
        self.signals = _LogDownloadWorkerSignals()
        self._file_path = file_path
        self._local_path = local_path
        self._ssh_config = ssh_config

    @Slot()
    def run(self) -> None:
        # 检查SSH配置
        if not self._ssh_config.get("host") or not self._ssh_config.get("username"):
            self.signals.error.emit(self._file_path, "请先配置SSH服务器信息")
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
                self.signals.error.emit(self._file_path, "SSH连接失败，请检查配置")
                return
            
            # 下载文件
            result = ssh.download_file(self._file_path, self._local_path)
            ssh.close()
            
            if result["success"]:
                self.signals.finished.emit(self._file_path, self._local_path)
            else:
                self.signals.error.emit(self._file_path, result.get("error", "下载文件失败"))
        except Exception as e:
            self.signals.error.emit(self._file_path, f"下载文件失败：{e}")


class LogViewTab(QWidget):
    """日志查看 TAB"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        # 不再自动加载，由MaintenanceView在首次切换到该TAB时加载

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 标题和刷新按钮
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)
        
        title = QLabel("日志查看")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        # 上次刷新时间标签
        self.last_refresh_label = QLabel("数据刷新于: --")
        self.last_refresh_label.setFont(QFont("Arial", 9))
        self.last_refresh_label.setStyleSheet("color: #666;")
        header_layout.addWidget(self.last_refresh_label)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(100)
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self.reload_from_ssh)
        self.refresh_btn = refresh_btn  # 保存引用以便控制状态
        header_layout.addWidget(refresh_btn)
        
        layout.addLayout(header_layout)

        # 说明文字
        info_label = QLabel("通过SSH获取服务器日志文件列表，支持查看和下载。文件超过10MB时请下载后查看。")
        info_label.setStyleSheet("color: #666; font-size: 10pt;")
        layout.addWidget(info_label)

        # 使用分割器：左侧日志列表，右侧日志内容
        splitter = QSplitter(Qt.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 左侧：日志列表表格
        log_list_widget = QWidget()
        log_list_layout = QVBoxLayout(log_list_widget)
        log_list_layout.setContentsMargins(0, 0, 0, 0)
        log_list_layout.setSpacing(0)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(4)
        self.log_table.setHorizontalHeaderLabels(["文件名", "路径", "大小", "修改时间"])
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 双击行查看日志
        self.log_table.itemDoubleClicked.connect(self._on_log_item_double_clicked)
        # 启用右键菜单
        self.log_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_table.customContextMenuRequested.connect(self._on_log_table_context_menu)
        log_list_layout.addWidget(self.log_table)

        splitter.addWidget(log_list_widget)

        # 右侧：日志内容查看器
        log_content_widget = QWidget()
        log_content_layout = QVBoxLayout(log_content_widget)
        log_content_layout.setContentsMargins(0, 0, 0, 0)
        log_content_layout.setSpacing(6)

        # 内容区域标题栏
        content_header = QHBoxLayout()
        content_title = QLabel("日志内容")
        content_title.setFont(QFont("Arial", 12, QFont.Bold))
        content_header.addWidget(content_title)
        content_header.addStretch()
        
        self.download_btn = QPushButton("下载")
        self.download_btn.setFixedWidth(80)
        self.download_btn.setFixedHeight(28)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download_clicked)
        content_header.addWidget(self.download_btn)
        
        log_content_layout.addLayout(content_header)

        # 日志内容显示
        self.log_content = QTextEdit()
        self.log_content.setReadOnly(True)
        self.log_content.setFont(QFont("Consolas", 10))
        self.log_content.setPlaceholderText("双击左侧日志文件查看内容")
        self.log_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_content_layout.addWidget(self.log_content)

        splitter.addWidget(log_content_widget)

        # 设置分割器比例（左侧40%，右侧60%）
        splitter.setSizes([400, 600])

        layout.addWidget(splitter, 1)

        # 当前选中的文件路径
        self._current_file_path = None

    def reload_from_ssh(self):
        """通过SSH重新加载日志列表"""
        # 如果正在刷新，不重复执行
        if hasattr(self, 'refresh_btn') and not self.refresh_btn.isEnabled():
            return
        
        # 更新刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("正在刷新...")
            self.refresh_btn.setEnabled(False)
        
        # 刷新时间将在加载完成时更新，不在这里更新
        
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
            Toast.show_message(self, "请先配置SSH服务器信息（在系统设置TAB中配置）")
            # 恢复按钮状态
            if hasattr(self, 'refresh_btn'):
                self.refresh_btn.setText("刷新")
                self.refresh_btn.setEnabled(True)
            return
        
        # 显示加载状态
        self.log_table.setRowCount(0)
        loading_item = QTableWidgetItem("加载中...")
        loading_item.setFlags(Qt.NoItemFlags)
        self.log_table.setRowCount(1)
        self.log_table.setItem(0, 0, loading_item)
        
        # 后台加载（通过SSH）
        worker = _LogListWorker(ssh_config)
        worker.signals.finished.connect(self._on_log_list_loaded)
        worker.signals.error.connect(self._on_log_list_error)
        QThreadPool.globalInstance().start(worker)

    def _on_log_list_loaded(self, items: List[Dict[str, Any]]):
        """日志列表加载完成"""
        # 恢复刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("刷新")
            self.refresh_btn.setEnabled(True)
        
        # 更新刷新时间（刷新完成时）
        from datetime import datetime
        refresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(self, 'last_refresh_label'):
            self.last_refresh_label.setText(f"数据刷新于: {refresh_time}")
        
        self.log_table.setRowCount(len(items))
        
        for row, item in enumerate(items):
            # 文件名
            name_item = QTableWidgetItem(item.get("name", ""))
            self.log_table.setItem(row, 0, name_item)
            
            # 路径（相对路径）
            rel_path = item.get("rel_path", item.get("path", ""))
            path_item = QTableWidgetItem(rel_path)
            self.log_table.setItem(row, 1, path_item)
            
            # 大小
            size_mb = item.get("size_mb", 0)
            if size_mb < 1:
                size_text = f"{item.get('size', 0) / 1024:.2f} KB"
            else:
                size_text = f"{size_mb:.2f} MB"
            size_item = QTableWidgetItem(size_text)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.log_table.setItem(row, 2, size_item)
            
            # 修改时间
            mtime = item.get("mtime", "")
            time_item = QTableWidgetItem(mtime)
            self.log_table.setItem(row, 3, time_item)
            
            # 存储完整路径到item的data中
            full_path = item.get("path", "")
            name_item.setData(Qt.UserRole, full_path)

    def _on_log_list_error(self, error_msg: str):
        """日志列表加载失败"""
        # 恢复刷新按钮状态
        if hasattr(self, 'refresh_btn'):
            self.refresh_btn.setText("刷新")
            self.refresh_btn.setEnabled(True)
        
        self.log_table.setRowCount(0)
        Toast.show_message(self, f"获取日志列表失败：{error_msg}")

    def _on_log_item_double_clicked(self, item: QTableWidgetItem):
        """双击日志项，查看日志内容"""
        # 获取完整路径
        file_path = item.data(Qt.UserRole)
        if not file_path:
            # 如果没有存储路径，尝试从第一列获取
            row = item.row()
            path_item = self.log_table.item(row, 1)
            if path_item:
                # 构建完整路径
                rel_path = path_item.text()
                file_path = f"/ai-perf/logs/{rel_path}"
        
        if not file_path:
            Toast.show_message(self, "无法获取文件路径")
            return
        
        self._current_file_path = file_path
        
        # 显示加载状态
        self.log_content.clear()
        self.log_content.setPlaceholderText("正在加载日志内容...")
        self.download_btn.setEnabled(False)
        
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        # 显示加载提示
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在读取日志文件...")
        
        # 后台读取文件内容
        worker = _LogContentWorker(file_path, ssh_config)
        worker.signals.finished.connect(self._on_log_content_loaded)
        worker.signals.error.connect(self._on_log_content_error)
        QThreadPool.globalInstance().start(worker)

    def _on_log_content_loaded(self, file_path: str, content: str):
        """日志内容加载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        self.log_content.setPlainText(content)
        # 滚动到底部（显示最新的日志内容）
        cursor = self.log_content.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_content.setTextCursor(cursor)
        self.log_content.ensureCursorVisible()
        
        self.download_btn.setEnabled(True)
        self._current_file_path = file_path

    def _on_log_content_error(self, file_path: str, error_msg: str):
        """日志内容加载失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        self.log_content.clear()
        self.log_content.setPlaceholderText(f"加载失败：{error_msg}")
        self.download_btn.setEnabled(True)  # 即使加载失败，也允许下载
        self._current_file_path = file_path

    def _on_download_clicked(self):
        """下载按钮点击事件（从右侧内容区域的下载按钮调用）"""
        if not self._current_file_path:
            Toast.show_message(self, "请先选择要下载的日志文件")
            return
        
        self._download_log_file(self._current_file_path)

    def _on_download_finished(self, file_path: str, save_path: str):
        """下载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        QMessageBox.information(
            self,
            "下载完成",
            f"日志文件已保存到：\n{save_path}"
        )

    def _on_download_error(self, file_path: str, error_msg: str):
        """下载失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        Toast.show_message(self, f"下载失败：{error_msg}")
    
    def _on_log_table_context_menu(self, position):
        """日志表格右键菜单"""
        item = self.log_table.itemAt(position)
        if not item:
            return
        
        row = item.row()
        # 获取文件路径
        name_item = self.log_table.item(row, 0)
        if not name_item:
            return
        
        file_path = name_item.data(Qt.UserRole)
        if not file_path:
            # 如果没有存储路径，尝试从第二列获取
            path_item = self.log_table.item(row, 1)
            if path_item:
                rel_path = path_item.text()
                file_path = f"/ai-perf/logs/{rel_path}"
        
        if not file_path:
            return
        
        # 创建右键菜单
        menu = QMenu(self)
        
        # 下载选项
        download_action = menu.addAction("下载")
        download_action.triggered.connect(lambda: self._download_log_file(file_path))
        
        # 显示菜单
        menu.exec_(self.log_table.viewport().mapToGlobal(position))
    
    def _download_log_file(self, file_path: str):
        """下载日志文件（从右键菜单或按钮调用）"""
        if not file_path:
            Toast.show_message(self, "请先选择要下载的日志文件")
            return
        
        # 获取文件名
        file_name = Path(file_path).name
        
        # 选择保存路径
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存日志文件",
            file_name,
            "Log Files (*.log);;All Files (*)"
        )
        
        if not save_path:
            return  # 用户取消
        
        # 显示加载提示
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading(f"正在下载 {file_name}...")
        
        # 获取SSH配置
        config = ConfigManager.load()
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "port": config.get("ssh_port", 22),
            "username": config.get("ssh_username", ""),
            "password": config.get("ssh_password", ""),
            "key_path": config.get("ssh_key_path", ""),
        }
        
        # 后台下载
        worker = _LogDownloadWorker(file_path, save_path, ssh_config)
        worker.signals.finished.connect(self._on_download_finished)
        worker.signals.error.connect(self._on_download_error)
        QThreadPool.globalInstance().start(worker)

