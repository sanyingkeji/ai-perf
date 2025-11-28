#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知管理页面
管理端：发送系统通知
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QComboBox, QMessageBox, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QAbstractItemView
)
from PySide6.QtCore import Qt, QTimer, QRunnable, QThreadPool, QObject, Signal, Slot
from PySide6.QtGui import QFont
from datetime import datetime
from utils.api_client import AdminApiClient, ApiError
from widgets.toast import Toast
from widgets.loading_overlay import LoadingOverlay
from windows._notification_worker import _NotificationListWorker


class NotificationView(QWidget):
    """通知管理页面"""
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.api = None  # 延迟初始化，在需要时创建
        self._init_ui()
    
    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        
        # 标题
        title = QLabel("通知管理")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # 使用 Tab 切换发送和列表
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_send_tab(), "发送通知")
        self.tabs.addTab(self._create_list_tab(), "通知列表")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
    
    def _create_send_tab(self):
        """创建发送通知标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        
        # 说明文字
        desc = QLabel("向所有用户或指定用户/团队发送系统通知。通知会在用户端显示（无论应用是否运行）。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; padding: 8px 0;")
        layout.addWidget(desc)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        
        # 表单区域
        form_layout = QVBoxLayout()
        form_layout.setSpacing(12)
        
        # 通知标题
        title_layout = QHBoxLayout()
        title_label = QLabel("通知标题：")
        title_label.setMinimumWidth(100)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("例如：系统维护通知")
        title_layout.addWidget(title_label)
        title_layout.addWidget(self.title_input)
        form_layout.addLayout(title_layout)
        
        # 通知内容
        message_layout = QVBoxLayout()
        message_label = QLabel("通知内容：")
        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("输入通知的详细内容...")
        self.message_input.setMinimumHeight(120)
        message_layout.addWidget(message_label)
        message_layout.addWidget(self.message_input)
        form_layout.addLayout(message_layout)
        
        # 副标题（可选）
        subtitle_layout = QHBoxLayout()
        subtitle_label = QLabel("副标题（可选）：")
        subtitle_label.setMinimumWidth(100)
        self.subtitle_input = QLineEdit()
        self.subtitle_input.setPlaceholderText("例如：预计耗时30分钟")
        subtitle_layout.addWidget(subtitle_label)
        subtitle_layout.addWidget(self.subtitle_input)
        form_layout.addLayout(subtitle_layout)
        
        # 目标类型
        target_layout = QHBoxLayout()
        target_label = QLabel("发送目标：")
        target_label.setMinimumWidth(100)
        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(["所有用户", "指定用户", "指定团队"])
        self.target_id_input = QLineEdit()
        self.target_id_input.setPlaceholderText("输入用户ID或团队ID")
        self.target_id_input.setEnabled(False)
        self.target_type_combo.currentIndexChanged.connect(self._on_target_type_changed)
        target_layout.addWidget(target_label)
        target_layout.addWidget(self.target_type_combo)
        target_layout.addWidget(QLabel("ID："))
        target_layout.addWidget(self.target_id_input)
        form_layout.addLayout(target_layout)
        
        layout.addLayout(form_layout)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.send_btn = QPushButton("发送通知")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056CC;
            }
            QPushButton:pressed {
                background-color: #004499;
            }
        """)
        self.send_btn.clicked.connect(self._on_send_clicked)
        button_layout.addWidget(self.send_btn)
        
        self.test_btn = QPushButton("测试通知")
        self.test_btn.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #28A745;
            }
            QPushButton:pressed {
                background-color: #1E7E34;
            }
        """)
        self.test_btn.clicked.connect(self._on_test_clicked)
        button_layout.addWidget(self.test_btn)
        
        layout.addLayout(button_layout)
        layout.addStretch()
        
        return tab
    
    def _create_list_tab(self):
        """创建通知列表标签页"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        # 工具栏
        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_notification_list)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        # 通知列表表格
        self.notification_table = QTableWidget()
        self.notification_table.setColumnCount(7)
        self.notification_table.setHorizontalHeaderLabels([
            "ID", "标题", "发送目标", "发送者", "发送时间", "已读数量", "状态"
        ])
        header = self.notification_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 标题
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 发送目标
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 发送者
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 发送时间
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 已读数量
        header.setSectionResizeMode(6, QHeaderView.Stretch)  # 状态（占剩余空间）
        self.notification_table.setAlternatingRowColors(True)
        self.notification_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.notification_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.notification_table.cellDoubleClicked.connect(self._on_notification_double_clicked)
        
        # 翻页相关状态
        self._current_offset = 0
        self._page_size = 30  # 每页加载30条
        self._is_loading = False
        self._has_more = True
        
        # 监听滚动事件，实现无限滚动
        self.notification_table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        
        layout.addWidget(self.notification_table)
        
        # 底部统计信息
        self._stats_label = QLabel("")
        self._stats_label.setAlignment(Qt.AlignRight)
        self._stats_label.setStyleSheet("color: #666; font-size: 11pt; padding: 8px;")
        layout.addWidget(self._stats_label)
        
        return tab
    
    def _on_notification_double_clicked(self, row, col):
        """双击通知行，显示详情"""
        # 从存储的数据中获取完整信息
        notification_id_item = self.notification_table.item(row, 0)
        if not notification_id_item:
            return
        
        notification_data = notification_id_item.data(Qt.ItemDataRole.UserRole + 1)  # 存储完整数据
        if not notification_data:
            # 如果没有存储完整数据，从表格中获取
            title_item = self.notification_table.item(row, 1)
            target_item = self.notification_table.item(row, 2)
            sender_item = self.notification_table.item(row, 3)
            time_item = self.notification_table.item(row, 4)
            read_count_item = self.notification_table.item(row, 5)
            
            title = title_item.text() if title_item else ""
            target = target_item.text() if target_item else ""
            sender = sender_item.text() if sender_item else ""
            time_str = time_item.text() if time_item else ""
            read_count = read_count_item.text() if read_count_item else "0"
            message = ""
            subtitle = ""
        else:
            title = notification_data.get("title", "")
            message = notification_data.get("message", "")
            subtitle = notification_data.get("subtitle", "")
            target_type = notification_data.get("target_type", "")
            target_id = notification_data.get("target_id", "")
            if target_type == "all":
                target = "所有用户"
            elif target_type == "user":
                target = f"用户: {target_id}"
            elif target_type == "team":
                target = f"团队: {target_id}"
            else:
                target = target_type
            sender = notification_data.get("sender_admin_email", "")
            created_at = notification_data.get("created_at", "")
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
            read_count = str(notification_data.get("read_count", 0))
        
        # 显示详情对话框
        msg = QMessageBox(self)
        msg.setWindowTitle("通知详情")
        detail_text = f"<b>标题：</b>{title}<br><br>"
        if subtitle:
            detail_text += f"<b>副标题：</b>{subtitle}<br><br>"
        if message:
            # 将换行符转换为 HTML 换行
            message_html = message.replace("\n", "<br>")
            detail_text += f"<b>内容：</b><br>{message_html}<br><br>"
        detail_text += f"<b>发送目标：</b>{target}<br><br>"
        detail_text += f"<b>发送者：</b>{sender}<br><br>"
        detail_text += f"<b>发送时间：</b>{time_str}<br><br>"
        detail_text += f"<b>已读数量：</b>{read_count}"
        msg.setText(detail_text)
        msg.exec()
    
    def _load_notification_list(self):
        """加载通知列表（首次加载或刷新）"""
        # 初始化 API 客户端
        try:
            if self.api is None:
                self.api = AdminApiClient.from_config()
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
            
            # ID（同时存储完整数据）
            id_item = QTableWidgetItem(str(item.get("id", "")))
            id_item.setData(Qt.ItemDataRole.UserRole, item.get("id"))
            id_item.setData(Qt.ItemDataRole.UserRole + 1, item)  # 存储完整数据供详情使用
            self.notification_table.setItem(row, 0, id_item)
            
            # 标题
            title_item = QTableWidgetItem(item.get("title", ""))
            self.notification_table.setItem(row, 1, title_item)
            
            # 发送目标
            target_type = item.get("target_type", "")
            target_id = item.get("target_id", "")
            if target_type == "all":
                target_text = "所有用户"
            elif target_type == "user":
                target_text = f"用户: {target_id}"
            elif target_type == "team":
                target_text = f"团队: {target_id}"
            else:
                target_text = target_type
            target_item = QTableWidgetItem(target_text)
            self.notification_table.setItem(row, 2, target_item)
            
            # 发送者
            sender_item = QTableWidgetItem(item.get("sender_admin_email", ""))
            self.notification_table.setItem(row, 3, sender_item)
            
            # 发送时间
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
            self.notification_table.setItem(row, 4, time_item)
            
            # 已读数量
            read_count = item.get("read_count", 0)
            read_item = QTableWidgetItem(str(read_count))
            self.notification_table.setItem(row, 5, read_item)
            
            # 状态
            expires_at = item.get("expires_at")
            if expires_at:
                try:
                    if isinstance(expires_at, str):
                        exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    else:
                        exp_dt = expires_at
                    if exp_dt < datetime.now():
                        status = "已过期"
                    else:
                        status = "有效"
                except:
                    status = "有效"
            else:
                status = "永久有效"
            status_item = QTableWidgetItem(status)
            self.notification_table.setItem(row, 6, status_item)
    
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
    
    def _on_tab_changed(self, index):
        """标签页切换"""
        if index == 1:  # 切换到通知列表标签页
            self._load_notification_list()
    
    def reload_from_api(self):
        """从 API 重新加载（供外部调用）"""
        if self.tabs.currentIndex() == 1:  # 如果在列表标签页
            self._load_notification_list()
    
    def _on_target_type_changed(self, index):
        """目标类型改变"""
        # 0=所有用户, 1=指定用户, 2=指定团队
        self.target_id_input.setEnabled(index != 0)
        if index == 0:
            self.target_id_input.clear()
    
    def _on_test_clicked(self):
        """测试通知（发送给自己）"""
        # 填充测试数据
        self.title_input.setText("测试通知")
        self.message_input.setText("这是一条测试通知，用于验证通知功能是否正常工作。")
        self.subtitle_input.setText("来自管理端测试")
        self.target_type_combo.setCurrentIndex(0)  # 所有用户
        self.target_id_input.clear()
        
        # 自动发送
        self._on_send_clicked()
    
    def _on_send_clicked(self):
        """发送通知"""
        # 验证输入
        title = self.title_input.text().strip()
        message = self.message_input.toPlainText().strip()
        
        if not title:
            QMessageBox.warning(self, "输入错误", "请输入通知标题")
            self.title_input.setFocus()
            return
        
        if not message:
            QMessageBox.warning(self, "输入错误", "请输入通知内容")
            self.message_input.setFocus()
            return
        
        # 获取目标类型和ID
        target_index = self.target_type_combo.currentIndex()
        target_type_map = {
            0: "all",
            1: "user",
            2: "team"
        }
        target_type = target_type_map[target_index]
        target_id = self.target_id_input.text().strip() if target_index != 0 else None
        
        if target_type != "all" and not target_id:
            QMessageBox.warning(self, "输入错误", f"发送给{self.target_type_combo.currentText()}时必须提供ID")
            self.target_id_input.setFocus()
            return
        
        # 初始化 API 客户端
        try:
            if self.api is None:
                self.api = AdminApiClient.from_config()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法初始化 API 客户端：{str(e)}\n请先登录。")
            return
        
        # 显示加载
        self.main_window.show_loading("正在发送通知...")
        
        # 发送请求
        try:
            data = {
                "title": title,
                "message": message,
                "subtitle": self.subtitle_input.text().strip() or None,
                "target_type": target_type,
                "target_id": target_id,
            }
            
            response = self.api._post("/admin/api/notifications", data)
            
            self.main_window.hide_loading()
            
            if response.get("status") == "success":
                Toast.show_message(self, "通知发送成功！")
                # 清空表单
                self.title_input.clear()
                self.message_input.clear()
                self.subtitle_input.clear()
                self.target_type_combo.setCurrentIndex(0)
                self.target_id_input.clear()
                # 如果当前在通知列表标签页，自动刷新列表
                if self.tabs.currentIndex() == 1:
                    self._load_notification_list()
                # 否则切换到通知列表标签页
                else:
                    self.tabs.setCurrentIndex(1)
                    QTimer.singleShot(100, self._load_notification_list)
            else:
                QMessageBox.warning(
                    self, "发送失败",
                    response.get("message", "未知错误")
                )
        except ApiError as e:
            self.main_window.hide_loading()
            QMessageBox.critical(self, "发送失败", f"API错误：{str(e)}")
        except Exception as e:
            self.main_window.hide_loading()
            QMessageBox.critical(self, "发送失败", f"错误：{str(e)}")

