#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消息页面
员工端：查看所有收到的历史通知
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView, QTextEdit, QDialog, QStackedWidget
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot
from PySide6.QtGui import QFont, QColor
from datetime import datetime
from utils.api_client import ApiClient, ApiError
from utils.theme_manager import ThemeManager
from widgets.toast import Toast


class _NotificationListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _NotificationListWorker(QRunnable):
    """后台加载通知列表数据"""
    def __init__(self, api_client: ApiClient, limit: int = 30, offset: int = 0):
        super().__init__()
        self.api_client = api_client
        self.limit = limit
        self.offset = offset
        self.signals = _NotificationListWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            response = self.api_client._get("/api/notifications", params={"limit": self.limit, "offset": self.offset})
            if response.get("status") == "success":
                items = response.get("items", [])
                self.signals.finished.emit(items)
            else:
                self.signals.error.emit(response.get("message", "未知错误"))
        except (ApiError, Exception) as e:
            self.signals.error.emit(str(e))


class NotificationDetailDialog(QDialog):
    """通知详情对话框"""
    def __init__(self, parent, notification_data: dict):
        super().__init__(parent)
        self.setWindowTitle("通知详情")
        self.setModal(True)
        self.resize(500, 400)
        self._init_ui(notification_data)
    
    def _init_ui(self, data: dict):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        # 标题
        title_label = QLabel(f"<b>{data.get('title', '')}</b>")
        title_label.setWordWrap(True)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # 副标题（如果有）
        subtitle = data.get("subtitle")
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setWordWrap(True)
            # 根据主题设置颜色
            is_dark = self._detect_theme()
            subtitle_color = "#9AA0A6" if is_dark else "#666"
            subtitle_label.setStyleSheet(f"color: {subtitle_color};")
            layout.addWidget(subtitle_label)
        
        # 分隔线
        from PySide6.QtWidgets import QFrame
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        
        # 内容
        content_label = QLabel("内容：")
        content_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(content_label)
        
        content_text = QTextEdit()
        content_text.setReadOnly(True)
        content_text.setText(data.get("message", ""))
        content_text.setMinimumHeight(150)
        layout.addWidget(content_text)
        
        # 信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        
        created_at = data.get("created_at", "")
        if created_at:
            try:
                if isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    dt = created_at
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = str(created_at)
        else:
            time_str = "未知"
        
        time_label = QLabel(f"<b>接收时间：</b>{time_str}")
        info_layout.addWidget(time_label)
        
        is_read = data.get("is_read", False)
        read_status = "已读" if is_read else "未读"
        read_label = QLabel(f"<b>状态：</b>{read_status}")
        info_layout.addWidget(read_label)
        
        layout.addLayout(info_layout)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False


class NotificationView(QWidget):
    """消息页面"""
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.api = None  # 延迟初始化
        self._init_ui()
    
    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        
        # 标题
        title = QLabel("消息")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # 工具栏
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_notifications)
        toolbar.addWidget(refresh_btn)
        
        mark_all_read_btn = QPushButton("全标为已读")
        mark_all_read_btn.clicked.connect(self._mark_all_as_read)
        toolbar.addWidget(mark_all_read_btn)
        
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        # 使用堆叠布局来切换表格和空状态提示
        self._stacked_widget = QStackedWidget()
        
        # 通知列表表格
        self.notification_table = QTableWidget()
        self.notification_table.setColumnCount(5)
        self.notification_table.setHorizontalHeaderLabels([
            "标题", "内容", "接收时间", "状态", "操作"
        ])
        header = self.notification_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 标题
        header.setSectionResizeMode(1, QHeaderView.Stretch)  # 内容（占剩余空间）
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 接收时间
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 操作
        self.notification_table.setAlternatingRowColors(True)
        self.notification_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.notification_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.notification_table.cellDoubleClicked.connect(self._on_notification_double_clicked)
        
        # 空状态提示标签
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        self._empty_label = QLabel("当前没有任何消息")
        self._empty_label.setAlignment(Qt.AlignCenter)
        # 根据主题设置颜色
        self._update_empty_label_color()
        empty_layout.addWidget(self._empty_label)
        
        # 添加到堆叠布局
        self._stacked_widget.addWidget(self.notification_table)  # 索引 0
        self._stacked_widget.addWidget(empty_widget)  # 索引 1
        self._stacked_widget.setCurrentIndex(0)  # 默认显示表格
        
        # 翻页相关状态
        self._current_offset = 0
        self._page_size = 30  # 每页加载30条
        self._is_loading = False
        self._has_more = True
        
        # 监听滚动事件，实现无限滚动
        self.notification_table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        
        layout.addWidget(self._stacked_widget)
        
        # 底部统计信息
        self._stats_label = QLabel("")
        self._stats_label.setAlignment(Qt.AlignRight)
        # 根据主题设置颜色
        self._update_stats_label_color()
        layout.addWidget(self._stats_label)
    
    def _on_notification_double_clicked(self, row, col):
        """双击通知行，显示详情"""
        item = self.notification_table.item(row, 0)
        if not item:
            return
        
        notification_data = item.data(Qt.ItemDataRole.UserRole + 1)
        if notification_data:
            dialog = NotificationDetailDialog(self, notification_data)
            dialog.exec()
    
    def _load_notifications(self):
        """加载通知列表（首次加载或刷新）"""
        # 初始化 API 客户端
        try:
            if self.api is None:
                self.api = ApiClient.from_config()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法初始化 API 客户端：{str(e)}\n请先登录。")
            return
        
        # 重置翻页状态
        self._current_offset = 0
        self._has_more = True
        self._is_loading = True
        
        # 显示加载
        self.main_window.show_loading("正在加载通知列表...")
        
        # 在后台线程中加载
        worker = _NotificationListWorker(self.api, limit=self._page_size, offset=0)
        worker.signals.finished.connect(self._on_notification_list_loaded)
        worker.signals.error.connect(self._on_notification_list_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_notification_list_loaded(self, items):
        """通知列表加载完成（首次加载）"""
        self.main_window.hide_loading()
        self._is_loading = False
        
        # 判断是否还有更多数据
        if len(items) >= self._page_size:
            self._has_more = True
            self._current_offset = len(items)
        else:
            self._has_more = False
        
        # 清空表格并重新渲染
        self.notification_table.setRowCount(0)
        
        # 如果没有数据，显示空状态提示
        if not items or len(items) == 0:
            self._stacked_widget.setCurrentIndex(1)  # 显示空状态
            self._stats_label.setText("")
        else:
            self._stacked_widget.setCurrentIndex(0)  # 显示表格
        self._apply_items_to_table(items)
        
        # 更新统计信息
        total_count = self.notification_table.rowCount()
        if self._has_more:
            self._stats_label.setText(f"已显示 {total_count} 条通知，滚动加载更多...")
        else:
            self._stats_label.setText(f"共 {total_count} 条通知")
    
    def _apply_items_to_table(self, items):
        """将通知数据渲染到表格"""
        start_row = self.notification_table.rowCount()
        self.notification_table.setRowCount(start_row + len(items))
        
        for idx, item in enumerate(items):
            row = start_row + idx
            
            # 标题（存储完整数据）
            title_item = QTableWidgetItem(item.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole + 1, item)  # 存储完整数据
            self.notification_table.setItem(row, 0, title_item)
            
            # 内容（单行显示，超出显示省略号）
            message = item.get("message", "")
            # 限制显示长度，超出部分用省略号
            if len(message) > 50:
                message = message[:50] + "..."
            content_item = QTableWidgetItem(message)
            content_item.setToolTip(item.get("message", ""))  # 鼠标悬停显示完整内容
            self.notification_table.setItem(row, 1, content_item)
            
            # 接收时间
            created_at = item.get("created_at", "")
            if created_at:
                try:
                    if isinstance(created_at, str):
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    else:
                        dt = created_at
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = str(created_at)
            else:
                time_str = ""
            time_item = QTableWidgetItem(time_str)
            self.notification_table.setItem(row, 2, time_item)
            
            # 状态
            is_read = item.get("is_read", False)
            status = "已读" if is_read else "未读"
            status_item = QTableWidgetItem(status)
            if not is_read:
                # 根据主题设置未读状态颜色
                is_dark = self._detect_theme()
                unread_color = QColor("#5C8CFF") if is_dark else QColor("#3A7AFE")
                status_item.setForeground(unread_color)
            else:
                # 已读状态使用默认文本颜色（由主题控制）
                pass
            self.notification_table.setItem(row, 3, status_item)
            
            # 操作按钮
            view_btn = QPushButton("查看")
            view_btn.setFixedSize(30, 22)  # 设置为历史评分的一半
            view_btn.setStyleSheet("""
                QPushButton {
                    font-size: 9pt;
                    padding: 0px;
                }
            """)
            # 使用 functools.partial 避免 lambda 参数问题
            from functools import partial
            view_btn.clicked.connect(partial(self._view_notification, row))
            self.notification_table.setCellWidget(row, 4, view_btn)
    
    def _view_notification(self, row):
        """查看通知详情"""
        item = self.notification_table.item(row, 0)
        if not item:
            return
        
        notification_data = item.data(Qt.ItemDataRole.UserRole + 1)
        if notification_data:
            dialog = NotificationDetailDialog(self, notification_data)
            dialog.exec()
            
            # 如果未读，标记为已读
            if not notification_data.get("is_read", False):
                self._mark_as_read(notification_data.get("id"))
    
    def _mark_as_read(self, notification_id):
        """标记通知为已读"""
        if not self.api or not notification_id:
            return
        
        try:
            self.api._post(f"/api/notifications/{notification_id}/read", {})
            # 更新当前行的状态显示，不刷新整个列表
            for row in range(self.notification_table.rowCount()):
                item = self.notification_table.item(row, 0)
                if item:
                    data = item.data(Qt.ItemDataRole.UserRole + 1)
                    if data and data.get("id") == notification_id:
                        # 更新数据
                        data["is_read"] = True
                        item.setData(Qt.ItemDataRole.UserRole + 1, data)
                        # 更新状态列显示
                        status_item = self.notification_table.item(row, 3)
                        if status_item:
                            status_item.setText("已读")
                            # 已读状态使用默认文本颜色（由主题控制）
                            # 使用主题的默认文本颜色
                            is_dark = self._detect_theme()
                            default_color = QColor("#E8EAED") if is_dark else QColor("#222")
                            status_item.setForeground(default_color)
                        break
        except Exception as e:
            Toast.show_message(self, f"标记已读失败：{str(e)}")
    
    def _mark_all_as_read(self):
        """标记所有通知为已读"""
        if not self.api:
            try:
                self.api = ApiClient.from_config()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法初始化 API 客户端：{str(e)}\n请先登录。")
                return
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认",
            "确定要将所有未读通知标记为已读吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 显示加载
        self.main_window.show_loading("正在标记所有通知为已读...")
        
        # 在后台线程中执行
        from PySide6.QtCore import QRunnable, QObject, Signal
        
        class _MarkAllReadWorkerSignals(QObject):
            finished = Signal(dict)  # {"count": int, "message": str}
            error = Signal(str)
        
        class _MarkAllReadWorker(QRunnable):
            def __init__(self, api_client: ApiClient):
                super().__init__()
                self.api_client = api_client
                self.signals = _MarkAllReadWorkerSignals()
            
            @Slot()
            def run(self) -> None:
                try:
                    response = self.api_client._post("/api/notifications/mark-all-read", {})
                    if response.get("status") == "success":
                        self.signals.finished.emit(response)
                    else:
                        self.signals.error.emit(response.get("message", "未知错误"))
                except (ApiError, Exception) as e:
                    self.signals.error.emit(str(e))
        
        worker = _MarkAllReadWorker(self.api)
        worker.signals.finished.connect(self._on_mark_all_read_finished)
        worker.signals.error.connect(self._on_mark_all_read_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_mark_all_read_finished(self, response: dict):
        """标记所有已读完成"""
        self.main_window.hide_loading()
        count = response.get("count", 0)
        if count > 0:
            Toast.show_message(self, f"已标记 {count} 条通知为已读")
            # 刷新列表
            self._load_notifications()
        else:
            Toast.show_message(self, "没有未读通知")
    
    def _on_mark_all_read_error(self, error_msg: str):
        """标记所有已读失败"""
        self.main_window.hide_loading()
        QMessageBox.critical(self, "操作失败", f"标记所有通知为已读失败：{error_msg}")
    
    def _on_notification_list_error(self, error_msg):
        """通知列表加载失败"""
        self.main_window.hide_loading()
        self._is_loading = False
        QMessageBox.critical(self, "加载失败", f"加载通知列表失败：{error_msg}")
    
    def _on_scroll_changed(self, value: int):
        """监听滚动条变化，当滚动到底部时自动加载更多"""
        scroll_bar = self.notification_table.verticalScrollBar()
        if scroll_bar.maximum() - value < 100:  # 距离底部100像素时触发
            self._load_more()
    
    def _load_more(self):
        """加载更多数据"""
        if self._is_loading or not self._has_more:
            return
        
        self._is_loading = True
        
        worker = _NotificationListWorker(self.api, limit=self._page_size, offset=self._current_offset)
        worker.signals.finished.connect(self._on_more_data_loaded)
        worker.signals.error.connect(self._on_load_more_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_more_data_loaded(self, items):
        """加载更多数据完成"""
        self._is_loading = False
        
        if not items:
            self._has_more = False
            return
        
        # 判断是否还有更多数据
        if len(items) >= self._page_size:
            self._has_more = True
            self._current_offset += len(items)
        else:
            self._has_more = False
        
        # 追加到表格
        self._apply_items_to_table(items)
        
        # 更新统计信息
        total_count = self.notification_table.rowCount()
        if self._has_more:
            self._stats_label.setText(f"已显示 {total_count} 条通知，滚动加载更多...")
        else:
            self._stats_label.setText(f"共 {total_count} 条通知")
    
    def _on_load_more_error(self, error_msg):
        """加载更多数据失败"""
        self._is_loading = False
        Toast.show_message(self, f"加载更多失败：{error_msg}")
    
    def reload_from_api(self):
        """从 API 重新加载（供外部调用）"""
        # 检查登录状态，未登录时不发起请求
        if not ApiClient.is_logged_in():
            from widgets.toast import Toast
            Toast.show_message(self, "请先登录")
            return
        
        self._load_notifications()
    
    def highlight_notification(self, notification_id: int):
        """高亮显示指定通知（供外部调用）"""
        for row in range(self.notification_table.rowCount()):
            item = self.notification_table.item(row, 0)
            if item:
                data = item.data(Qt.ItemDataRole.UserRole + 1)
                if data and data.get("id") == notification_id:
                    # 选中该行
                    self.notification_table.selectRow(row)
                    # 滚动到该行
                    self.notification_table.scrollToItem(item)
                    # 自动打开详情
                    self._view_notification(row)
                    break
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False
    
    def _update_empty_label_color(self):
        """更新空状态标签颜色"""
        if hasattr(self, '_empty_label'):
            is_dark = self._detect_theme()
            color = "#9AA0A6" if is_dark else "#888"
            current_style = self._empty_label.styleSheet()
            # 保留其他样式，只更新颜色
            new_style = f"color: {color}; font-size: 14pt; padding: 40px;"
            self._empty_label.setStyleSheet(new_style)
    
    def _update_stats_label_color(self):
        """更新统计信息标签颜色"""
        if hasattr(self, '_stats_label'):
            is_dark = self._detect_theme()
            color = "#9AA0A6" if is_dark else "#666"
            current_style = self._stats_label.styleSheet()
            # 保留其他样式，只更新颜色
            new_style = f"color: {color}; font-size: 11pt; padding: 8px;"
            self._stats_label.setStyleSheet(new_style)

