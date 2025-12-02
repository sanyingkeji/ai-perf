#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔空投送界面（模仿苹果 AirDrop 风格）
支持拖放文件到设备头像进行传输
支持窗口拖拽到边缘自动变成图标
"""

import os
from pathlib import Path
from typing import Optional, Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QScrollArea, QApplication, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, QSize, QTimer, Signal, QMimeData, QPoint, QPropertyAnimation, QEasingCurve, QRect, QEvent
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QBrush, QDragEnterEvent, QDropEvent, QMouseEvent
import httpx
import logging

from utils.lan_transfer.manager import TransferManager
from utils.lan_transfer.discovery import DeviceInfo
from utils.api_client import ApiClient
from widgets.toast import Toast
from widgets.transfer_confirm_dialog import TransferConfirmDialog
from utils.notification import send_notification

logger = logging.getLogger(__name__)


class DeviceItemWidget(QWidget):
    """设备列表项（支持拖放，苹果风格）"""
    
    file_dropped = Signal(Path, DeviceInfo)  # 文件拖放信号
    
    def __init__(self, device: DeviceInfo, parent=None):
        super().__init__(parent)
        self._device = device
        self._setup_ui()
        self.setAcceptDrops(True)
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)
        
        # 头像（可拖放区域，更大）
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(80, 80)
        self.avatar_label.setScaledContents(True)
        self.avatar_label.setAcceptDrops(True)
        self.avatar_label.setStyleSheet("""
            QLabel {
                border: 2px dashed transparent;
                border-radius: 40px;
                background-color: #f5f5f5;
            }
            QLabel:hover {
                border-color: #007AFF;
                background-color: rgba(0, 122, 255, 0.1);
            }
        """)
        self._load_avatar()
        layout.addWidget(self.avatar_label)
        
        # 信息区域
        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        info_layout.setAlignment(Qt.AlignVCenter)
        
        # 用户名
        self.name_label = QLabel(self._device.name)
        self.name_label.setFont(QFont("SF Pro Display", 15, QFont.Normal))
        info_layout.addWidget(self.name_label)
        
        # 设备名（如果有）
        if self._device.device_name:
            device_label = QLabel(self._device.device_name)
            device_label.setStyleSheet("color: #8E8E93; font-size: 13px;")
            info_layout.addWidget(device_label)
        
        layout.addLayout(info_layout)
        layout.addStretch()
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.avatar_label.setStyleSheet("""
                QLabel {
                    border: 2px solid #007AFF;
                    border-radius: 40px;
                    background-color: rgba(0, 122, 255, 0.15);
                }
            """)
    
    def dragLeaveEvent(self, event):
        """拖拽离开事件"""
        self.avatar_label.setStyleSheet("""
            QLabel {
                border: 2px dashed transparent;
                border-radius: 40px;
                background-color: #f5f5f5;
            }
            QLabel:hover {
                border-color: #007AFF;
                background-color: rgba(0, 122, 255, 0.1);
            }
        """)
    
    def dropEvent(self, event: QDropEvent):
        """拖放事件"""
        self.avatar_label.setStyleSheet("""
            QLabel {
                border: 2px dashed transparent;
                border-radius: 40px;
                background-color: #f5f5f5;
            }
            QLabel:hover {
                border-color: #007AFF;
                background-color: rgba(0, 122, 255, 0.1);
            }
        """)
        
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = Path(urls[0].toLocalFile())
                if file_path.is_file():
                    self.file_dropped.emit(file_path, self._device)
        event.acceptProposedAction()
    
    def _load_avatar(self):
        """加载头像"""
        if self._device.avatar_url:
            self._load_avatar_async(self._device.avatar_url)
        else:
            self._set_default_avatar()
    
    def _load_avatar_async(self, url: str):
        """异步加载头像"""
        def load():
            try:
                response = httpx.get(url, timeout=5)
                if response.status_code == 200:
                    pixmap = QPixmap()
                    pixmap.loadFromData(response.content)
                    if not pixmap.isNull():
                        circular_pixmap = self._make_circular(pixmap, 80)
                        self.avatar_label.setPixmap(circular_pixmap)
                        return
            except Exception as e:
                logger.error(f"加载头像失败: {e}")
            self._set_default_avatar()
        
        import threading
        thread = threading.Thread(target=load, daemon=True)
        thread.start()
    
    def _set_default_avatar(self):
        """设置默认头像"""
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor(220, 220, 220))
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(142, 142, 147)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 80, 80)
        
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("SF Pro Display", 32, QFont.Medium))
        first_char = self._device.name[0].upper() if self._device.name else "?"
        painter.drawText(0, 0, 80, 80, Qt.AlignCenter, first_char)
        painter.end()
        
        self.avatar_label.setPixmap(pixmap)
    
    @staticmethod
    def _make_circular(pixmap: QPixmap, size: int) -> QPixmap:
        """将头像转换为圆形"""
        circular = QPixmap(size, size)
        circular.fill(Qt.transparent)
        
        painter = QPainter(circular)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.end()
        
        return circular
    
    @property
    def device(self) -> DeviceInfo:
        return self._device


class AirDropView(QWidget):
    """隔空投送主界面（苹果风格）"""
    
    # 信号：窗口需要隐藏（变成图标），传递图标位置
    should_hide_to_icon = Signal(QPoint)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._transfer_manager: Optional[TransferManager] = None
        self._transferring = False
        self._current_target: Optional[DeviceInfo] = None
        self._pending_requests: Dict[str, dict] = {}  # 待处理的传输请求
        self._setup_ui()
        self._init_transfer_manager()
        self._setup_drag_detection()
    
    def _setup_ui(self):
        """设置UI（苹果风格）"""
        # 设置窗口样式
        self.setStyleSheet("""
            QWidget {
                background-color: #FFFFFF;
            }
            QLabel {
                color: #000000;
            }
        """)
        
        # 使用绝对定位布局，让背景文字在底部
        from PySide6.QtWidgets import QWidget
        main_widget = QWidget()
        main_widget.setStyleSheet("background-color: #FFFFFF;")
        
        # 主内容区域（设备列表）
        content_widget = QWidget(main_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(0)
        
        # 滚动区域（设备列表）
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
        """)
        
        self.devices_list = QListWidget()
        self.devices_list.setSpacing(12)
        self.devices_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
            }
            QListWidget::item {
                background-color: #F5F5F5;
                border-radius: 12px;
                margin: 4px;
            }
            QListWidget::item:hover {
                background-color: #E5E5E5;
            }
        """)
        scroll_area.setWidget(self.devices_list)
        
        content_layout.addWidget(scroll_area, 1)
        
        # 背景文字（水平居中，垂直靠底部）
        self._background_label = QLabel('"隔空投送"可让你与附近的同事立即共享。')
        self._background_label.setAlignment(Qt.AlignCenter)
        self._background_label.setFont(QFont("SF Pro Display", 13))
        self._background_label.setStyleSheet("color: #C0C0C0;")  # 浅灰色，作为背景
        self._background_label.setWordWrap(True)
        self._background_label.setParent(main_widget)
        
        # 传输进度（初始隐藏）
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #E5E5E5;
                height: 6px;
            }
            QProgressBar::chunk {
                background-color: #007AFF;
                border-radius: 4px;
            }
        """)
        content_layout.addWidget(self.progress_bar)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #8E8E93; font-size: 13px;")
        self.status_label.setVisible(False)
        content_layout.addWidget(self.status_label)
        
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(main_widget)
        
        # 保存引用以便后续调整背景文字位置
        self._main_widget = main_widget
        self._content_widget = content_widget
        
        # 重写resizeEvent来调整背景文字位置
        self._update_background_label_position()
    
    def resizeEvent(self, event):
        """窗口大小改变时调整背景文字位置"""
        super().resizeEvent(event)
        self._update_background_label_position()
    
    def _update_background_label_position(self):
        """更新背景文字位置（水平居中，垂直靠底部）"""
        if not hasattr(self, '_background_label'):
            return
        
        # 背景文字位置：水平居中，距离底部60像素
        label_width = 300
        label_height = 40
        x = (self.width() - label_width) // 2
        y = self.height() - 60
        
        self._background_label.setGeometry(x, y, label_width, label_height)
        self._background_label.lower()  # 置于底层，作为背景
    
    def _setup_drag_detection(self):
        """设置拖拽检测（用于检测窗口拖到边缘）"""
        self.setMouseTracking(True)
        self._drag_start_pos = None
        self._drag_window_pos = None
        self._is_dragging = False
        self._edge_triggered = False
    
    def mousePressEvent(self, event: QMouseEvent):
        """鼠标按下"""
        if event.button() == Qt.LeftButton:
            # 检查是否在标题栏区域（系统标题栏区域，约30像素）
            # 或者在整个窗口顶部区域（用于拖拽）
            if event.position().y() <= 30:
                # 记录鼠标按下时的全局位置和窗口位置
                self._drag_start_pos = event.globalPosition().toPoint()
                self._drag_window_pos = self.pos()
                self._is_dragging = False
                self._edge_triggered = False
            else:
                super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """鼠标移动"""
        if event.buttons() == Qt.LeftButton and self._drag_start_pos is not None:
            if not self._is_dragging:
                delta = (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength()
                if delta > 5:
                    self._is_dragging = True
            
            if self._is_dragging:
                # 计算窗口新位置：鼠标移动距离 = 窗口移动距离
                mouse_delta = event.globalPosition().toPoint() - self._drag_start_pos
                new_pos = self._drag_window_pos + mouse_delta
                self.move(new_pos)
                
                # 检查是否拖到屏幕边缘（只要有一个边靠边缘就触发）
                screen = QApplication.primaryScreen().geometry()
                window_rect = self.geometry()
                
                # 检查是否有一个边靠边缘（10像素内）
                margin = 10
                is_at_edge = (
                    window_rect.left() <= screen.left() + margin or
                    window_rect.right() >= screen.right() - margin or
                    window_rect.top() <= screen.top() + margin or
                    window_rect.bottom() >= screen.bottom() - margin
                )
                
                if is_at_edge and not self._edge_triggered:
                    # 触发隐藏到图标（只触发一次）
                    self._edge_triggered = True
                    self._animate_to_icon()  # 立即开始动画
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """鼠标释放"""
        self._drag_start_pos = None
        self._drag_window_pos = None
        self._is_dragging = False
        if self._edge_triggered:
            self._edge_triggered = False
        super().mouseReleaseEvent(event)
    
    def _animate_to_icon(self):
        """动画：窗口渐渐藏入边缘，然后图标从藏住的位置出现"""
        screen = QApplication.primaryScreen().geometry()
        current_rect = self.geometry()
        
        # 确定窗口要隐藏到的边缘位置
        # 根据当前窗口位置，判断应该隐藏到哪个边缘
        left_dist = current_rect.left() - screen.left()
        right_dist = screen.right() - current_rect.right()
        top_dist = current_rect.top() - screen.top()
        bottom_dist = screen.bottom() - current_rect.bottom()
        
        # 找到最近的边缘
        min_dist = min(left_dist, right_dist, top_dist, bottom_dist)
        
        if min_dist == left_dist:
            # 隐藏到左边缘
            target_x = screen.left() - current_rect.width() + 36
            target_y = current_rect.y() + current_rect.height() // 2 - 18
        elif min_dist == right_dist:
            # 隐藏到右边缘
            target_x = screen.right() - 36
            target_y = current_rect.y() + current_rect.height() // 2 - 18
        elif min_dist == top_dist:
            # 隐藏到上边缘
            target_x = current_rect.x() + current_rect.width() // 2 - 18
            target_y = screen.top() - current_rect.height() + 36
        else:
            # 隐藏到下边缘
            target_x = current_rect.x() + current_rect.width() // 2 - 18
            target_y = screen.bottom() - 36
        
        # 图标最终位置（从窗口隐藏位置出现）
        icon_pos = QPoint(target_x, target_y)
        
        # 创建窗口隐藏动画
        window_animation = QPropertyAnimation(self, b"geometry")
        window_animation.setDuration(300)
        window_animation.setStartValue(QRect(current_rect))
        window_animation.setEndValue(QRect(target_x, target_y, 36, 36))
        window_animation.setEasingCurve(QEasingCurve.InOutCubic)
        
        def on_window_animation_finished():
            # 窗口隐藏完成，隐藏窗口（确保互斥）
            self.hide()
            self.setVisible(False)
            # 触发显示图标（传递图标位置）
            self.should_hide_to_icon.emit(icon_pos)
            # 重置标志
            if hasattr(self, '_edge_triggered'):
                self._edge_triggered = False
        
        window_animation.finished.connect(on_window_animation_finished)
        window_animation.start()
    
    def _init_transfer_manager(self):
        """初始化传输管理器"""
        try:
            api_client = ApiClient.from_config()
            user_info = api_client._get("/api/user_info")
            
            if isinstance(user_info, dict) and user_info.get("status") == "success":
                data = user_info.get("data", {})
                user_id = str(data.get("user_id", ""))
                user_name = data.get("name", "Unknown")
                avatar_url = data.get("avatar_url")
                
                self._transfer_manager = TransferManager(
                    user_id=user_id,
                    user_name=user_name,
                    avatar_url=avatar_url
                )
                
                self._transfer_manager.device_added.connect(self._on_device_added)
                self._transfer_manager.device_removed.connect(self._on_device_removed)
                self._transfer_manager.transfer_request_received.connect(self._on_transfer_request_received)
                self._transfer_manager.file_received.connect(self._on_file_received)
                self._transfer_manager.transfer_progress.connect(self._on_transfer_progress)
                self._transfer_manager.transfer_completed.connect(self._on_transfer_completed)
                
                self._transfer_manager.start()
                
                self._refresh_timer = QTimer()
                self._refresh_timer.timeout.connect(self._refresh_devices)
                self._refresh_timer.start(2000)
            else:
                Toast.show_message(self, "无法获取用户信息，请先登录")
        except Exception as e:
            logger.error(f"初始化传输管理器失败: {e}")
            Toast.show_message(self, f"初始化失败: {e}")
    
    def _on_device_added(self, device: DeviceInfo):
        """设备添加"""
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget) and widget.device.user_id == device.user_id:
                return
        
        item = QListWidgetItem()
        item.setSizeHint(QSize(200, 100))
        widget = DeviceItemWidget(device)
        widget.file_dropped.connect(self._on_file_dropped)
        self.devices_list.addItem(item)
        self.devices_list.setItemWidget(item, widget)
    
    def _on_device_removed(self, device_name: str):
        """设备移除"""
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget) and widget.device.name == device_name:
                self.devices_list.takeItem(i)
                break
    
    def _on_file_dropped(self, file_path: Path, device: DeviceInfo):
        """文件拖放到设备头像"""
        if self._transferring:
            Toast.show_message(self, "正在传输中，请稍候...")
            return
        
        if not file_path.exists() or not file_path.is_file():
            Toast.show_message(self, "无效的文件")
            return
        
        self._send_transfer_request(file_path, device)
    
    def _send_transfer_request(self, file_path: Path, device: DeviceInfo):
        """发送传输请求"""
        if not self._transfer_manager:
            return
        
        self._transferring = True
        self._current_target = device
        
        self.status_label.setVisible(True)
        self.status_label.setText(f"正在请求传输到 {device.name}...")
        
        def send_in_thread():
            result = self._transfer_manager.send_transfer_request(file_path, device)
            
            if result["success"]:
                request_id = result["request_id"]
                self._wait_and_transfer(file_path, device, request_id)
            else:
                self._transferring = False
                self.status_label.setVisible(False)
                Toast.show_message(self, f"请求失败: {result['message']}")
        
        import threading
        thread = threading.Thread(target=send_in_thread, daemon=True)
        thread.start()
    
    def _wait_and_transfer(self, file_path: Path, device: DeviceInfo, request_id: str):
        """等待确认后传输"""
        def wait_in_thread():
            result = self._transfer_manager._client.wait_for_confirm(
                request_id=request_id,
                target_ip=device.ip,
                target_port=device.port,
                timeout=30
            )
            
            if result["success"] and result["accepted"]:
                self._transfer_file(file_path, device, request_id)
            else:
                self._transferring = False
                self.status_label.setVisible(False)
                if result.get("accepted") is False:
                    Toast.show_message(self, f"{device.name} 拒绝了传输请求")
                else:
                    Toast.show_message(self, "传输请求超时")
        
        import threading
        thread = threading.Thread(target=wait_in_thread, daemon=True)
        thread.start()
    
    def _transfer_file(self, file_path: Path, device: DeviceInfo, request_id: str):
        """传输文件"""
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText(f"正在发送到 {device.name}...")
        
        self._transfer_manager.send_file_after_confirm(
            file_path=file_path,
            target_device=device,
            request_id=request_id,
            on_progress=self._on_transfer_progress
        )
    
    def _on_transfer_request_received(self, request_id: str, sender_name: str, sender_id: str,
                                     filename: str, file_size: int):
        """收到传输请求"""
        self._pending_requests[request_id] = {
            'sender_name': sender_name,
            'sender_id': sender_id,
            'filename': filename,
            'file_size': file_size
        }
        
        size_str = self._format_file_size(file_size)
        
        def notification_callback():
            if self.parent():
                self.parent().show()
                self.parent().raise_()
                self.parent().activateWindow()
            self._show_confirm_dialog(request_id)
        
        send_notification(
            title="文件传输请求",
            message=f"{sender_name} 想要发送文件给您",
            subtitle=f"{filename} ({size_str})",
            notification_id=hash(request_id),
            click_callback=notification_callback
        )
        
        if self.isVisible():
            QTimer.singleShot(500, lambda: self._show_confirm_dialog(request_id))
    
    def _show_confirm_dialog(self, request_id: str):
        """显示确认对话框"""
        if request_id not in self._pending_requests:
            return
        
        request_info = self._pending_requests[request_id]
        
        dialog = TransferConfirmDialog(
            sender_name=request_info['sender_name'],
            filename=request_info['filename'],
            file_size=request_info['file_size'],
            parent=self
        )
        
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        
        def on_accepted():
            if self._transfer_manager and self._transfer_manager._server:
                request_data = self._transfer_manager._server.get_pending_request(request_id)
                if request_data:
                    sender_ip = request_data.get('sender_ip')
                    sender_port = request_data.get('sender_port', 8765)
                    
                    if sender_ip:
                        result = self._transfer_manager.accept_transfer(
                            request_id, sender_ip, sender_port
                        )
                        if result["success"]:
                            self._transfer_manager._server.confirm_transfer(request_id, True)
                            Toast.show_message(self, "已接受传输请求，等待文件...")
                        else:
                            Toast.show_message(self, f"接受失败: {result['message']}")
                    else:
                        Toast.show_message(self, "无法获取发送端信息")
                else:
                    Toast.show_message(self, "请求已过期")
            
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]
        
        def on_rejected():
            if self._transfer_manager and self._transfer_manager._server:
                request_data = self._transfer_manager._server.get_pending_request(request_id)
                if request_data:
                    sender_ip = request_data.get('sender_ip')
                    sender_port = request_data.get('sender_port', 8765)
                    
                    if sender_ip:
                        result = self._transfer_manager.reject_transfer(
                            request_id, sender_ip, sender_port
                        )
                        if result["success"]:
                            self._transfer_manager._server.confirm_transfer(request_id, False)
                            Toast.show_message(self, "已拒绝传输请求")
            
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]
        
        dialog.accepted.connect(on_accepted)
        dialog.rejected.connect(on_rejected)
    
    def _on_transfer_progress(self, target_name: str, uploaded: int, total: int):
        """传输进度更新"""
        if self._current_target and target_name == self._current_target.name:
            progress = int((uploaded / total) * 100) if total > 0 else 0
            self.progress_bar.setValue(progress)
    
    def _on_transfer_completed(self, target_name: str, success: bool, message: str):
        """传输完成"""
        self._transferring = False
        
        self.progress_bar.setVisible(False)
        self.status_label.setVisible(False)
        
        if success:
            Toast.show_message(self, f"文件已成功发送到 {target_name}")
        else:
            Toast.show_message(self, f"发送失败: {message}")
        
        self._current_target = None
    
    def _on_file_received(self, save_path: Path, file_size: int, original_filename: str):
        """文件接收"""
        size_str = self._format_file_size(file_size)
        Toast.show_message(
            self,
            f"收到文件: {original_filename} ({size_str})\n保存位置: {save_path.parent}",
            duration=5000
        )
    
    def _refresh_devices(self):
        """刷新设备列表"""
        if not self._transfer_manager:
            return
        
        current_devices = {d.user_id for d in self._transfer_manager.get_devices()}
        
        for i in range(self.devices_list.count() - 1, -1, -1):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                if widget.device.user_id not in current_devices:
                    self.devices_list.takeItem(i)
    
    @staticmethod
    def _format_file_size(size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"
    
    def closeEvent(self, event):
        """关闭事件"""
        # 注意：这个closeEvent会被main_window中的custom_close_event重写
        # 所以这里只处理传输管理器的停止
        if self._transfer_manager:
            self._transfer_manager.stop()
        super().closeEvent(event)
