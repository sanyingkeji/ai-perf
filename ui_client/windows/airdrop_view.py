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
        self._was_hidden_to_icon = False  # 标记窗口是否被隐藏到图标
        self._setup_ui()
        self._setup_drag_detection()
        # 延迟初始化传输管理器，避免阻塞UI创建
        QTimer.singleShot(0, self._init_transfer_manager)
    
    def changeEvent(self, event):
        """处理窗口状态改变事件，禁止最大化和最小化"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            # 如果窗口被最大化，立即恢复
            if self.isMaximized():
                self.showNormal()
                event.ignore()
                return
            # 如果窗口被最小化，也恢复（因为我们要用隐藏到图标代替）
            if self.isMinimized():
                self.showNormal()
                event.ignore()
                return
        super().changeEvent(event)
    
    def mouseDoubleClickEvent(self, event):
        """禁止双击窗口头部扩大"""
        # 检查是否在标题栏区域（顶部30像素）
        if event.position().y() <= 30:
            # 完全忽略双击事件，不执行默认的扩大操作
            event.ignore()
            # 不调用 super()，完全阻止事件传播
            return
        # 非标题栏区域的双击事件正常处理
        super().mouseDoubleClickEvent(event)
    
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
        
        # 背景区域（水平居中，垂直靠底部）- 包含图标和文字
        self._background_frame = QFrame(main_widget)
        self._background_frame.setStyleSheet("background-color: transparent;")
        background_layout = QVBoxLayout(self._background_frame)
        background_layout.setAlignment(Qt.AlignCenter)
        background_layout.setSpacing(12)
        
        # 信号图标（使用文字模拟，实际可以用图片）
        signal_label = QLabel("📡")
        signal_label.setAlignment(Qt.AlignCenter)
        signal_label.setFont(QFont("SF Pro Display", 48))
        signal_label.setStyleSheet("color: #D0D0D0;")  # 浅灰色图标
        background_layout.addWidget(signal_label)
        
        # 背景文字
        self._background_label = QLabel('"隔空投送"可让你与附近的同事立即共享。')
        self._background_label.setAlignment(Qt.AlignCenter)
        self._background_label.setFont(QFont("SF Pro Display", 13))
        self._background_label.setStyleSheet("color: #808080;")  # 调整为更深的灰色，更易看清
        self._background_label.setWordWrap(True)
        background_layout.addWidget(self._background_label)
        
        self._background_frame.setParent(main_widget)
        
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
        """更新背景区域位置（水平居中，垂直靠底部）"""
        if not hasattr(self, '_background_frame'):
            return
        
        # 背景区域位置：水平居中，距离底部30像素（更靠近底部）
        frame_width = 300
        frame_height = 120
        x = (self.width() - frame_width) // 2
        y = self.height() - frame_height - 30  # 从80改为30，更靠近底部
        
        self._background_frame.setGeometry(x, y, frame_width, frame_height)
        self._background_frame.lower()  # 置于底层，作为背景
    
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
                
                # 限制窗口不能超出屏幕范围
                screen = QApplication.primaryScreen().geometry()
                window_rect = self.geometry()
                window_size = window_rect.size()
                
                # 计算限制后的位置
                constrained_x = max(screen.left(), min(new_pos.x(), screen.right() - window_size.width()))
                constrained_y = max(screen.top(), min(new_pos.y(), screen.bottom() - window_size.height()))
                constrained_pos = QPoint(constrained_x, constrained_y)
                
                self.move(constrained_pos)
                
                # 检查是否拖到屏幕边缘（只要有一个边靠边缘就触发）
                # 使用限制后的位置重新计算窗口矩形
                window_rect = self.geometry()
                
                # 检查是否有一个边靠边缘（20像素内，更宽松的检测）
                margin = 20
                is_at_edge = (
                    window_rect.left() <= screen.left() + margin or
                    window_rect.right() >= screen.right() - margin or
                    window_rect.top() <= screen.top() + margin or
                    window_rect.bottom() >= screen.bottom() - margin
                )
                
                if is_at_edge and not self._edge_triggered:
                    # 触发隐藏到图标（只触发一次）
                    import sys
                    print(f"[DEBUG] Edge detected! window_rect={window_rect}, screen={screen}", file=sys.stderr)
                    self._edge_triggered = True
                    # 停止当前拖拽，开始动画
                    self._is_dragging = False
                    QTimer.singleShot(50, self._animate_to_icon)  # 稍微延迟，确保拖拽结束
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
        import sys
        print(f"[DEBUG] _animate_to_icon called, isVisible={self.isVisible()}", file=sys.stderr)
        
        if not self.isVisible():
            # 如果窗口已经隐藏，直接触发显示图标
            screen = QApplication.primaryScreen().geometry()
            # 默认位置：屏幕右侧边缘
            icon_pos = QPoint(screen.right() - 36, screen.height() // 2 - 18)
            self.should_hide_to_icon.emit(icon_pos)
            return
        
        screen = QApplication.primaryScreen().geometry()
        current_rect = self.geometry()
        
        print(f"[DEBUG] Screen geometry: {screen}", file=sys.stderr)
        print(f"[DEBUG] Current window rect: {current_rect}", file=sys.stderr)
        
        # 确定窗口要隐藏到的边缘位置
        # 只允许隐藏到左右边缘，不允许隐藏到上下边缘
        left_dist = abs(current_rect.left() - screen.left())
        right_dist = abs(screen.right() - current_rect.right())
        
        print(f"[DEBUG] Edge distances: left={left_dist}, right={right_dist}", file=sys.stderr)
        
        # 找到最近的边缘（只考虑左右）
        if left_dist <= right_dist:
            # 隐藏到左边缘
            target_x = screen.left() - 36  # 只露出36像素（图标大小）
            target_y = max(screen.top(), min(screen.bottom() - 36, current_rect.y() + current_rect.height() // 2 - 18))
            print(f"[DEBUG] Hiding to LEFT edge: target=({target_x}, {target_y})", file=sys.stderr)
        else:
            # 隐藏到右边缘
            target_x = screen.right() - 36
            target_y = max(screen.top(), min(screen.bottom() - 36, current_rect.y() + current_rect.height() // 2 - 18))
            print(f"[DEBUG] Hiding to RIGHT edge: target=({target_x}, {target_y})", file=sys.stderr)
        
        # 确保目标位置在屏幕范围内
        target_x = max(screen.left() - 36, min(screen.right(), target_x))
        target_y = max(screen.top(), min(screen.bottom() - 36, target_y))
        
        # 图标最终位置（从窗口隐藏位置出现）
        icon_pos = QPoint(target_x, target_y)
        print(f"[DEBUG] Final icon position: {icon_pos}", file=sys.stderr)
        
        # 创建窗口隐藏动画
        target_rect = QRect(target_x, target_y, 36, 36)
        print(f"[DEBUG] Animation target rect: {target_rect}", file=sys.stderr)
        
        window_animation = QPropertyAnimation(self, b"geometry")
        window_animation.setDuration(300)
        window_animation.setStartValue(QRect(current_rect))
        window_animation.setEndValue(target_rect)
        window_animation.setEasingCurve(QEasingCurve.InOutCubic)
        
        def on_window_animation_finished():
            import sys
            print(f"[DEBUG] ===== Window animation FINISHED callback called =====", file=sys.stderr)
            print(f"[DEBUG] Window isVisible before hide: {self.isVisible()}", file=sys.stderr)
            try:
                # 标记窗口被隐藏到图标（用于后续判断是否从图标恢复）
                self._was_hidden_to_icon = True
                # 窗口隐藏完成，隐藏窗口（确保互斥）
                self.hide()
                self.setVisible(False)
                print(f"[DEBUG] Window isVisible after hide: {self.isVisible()}", file=sys.stderr)
                # 触发显示图标（传递图标位置）
                print(f"[DEBUG] Emitting should_hide_to_icon signal with icon_pos={icon_pos}", file=sys.stderr)
                self.should_hide_to_icon.emit(icon_pos)
                print(f"[DEBUG] Signal emitted successfully", file=sys.stderr)
            except Exception as e:
                print(f"[ERROR] Error in animation finished callback: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc()
            # 重置标志
            if hasattr(self, '_edge_triggered'):
                self._edge_triggered = False
        
        def on_animation_state_changed(new_state, old_state):
            import sys
            from PySide6.QtCore import QAbstractAnimation
            state_names = {QAbstractAnimation.Stopped: "Stopped", 
                          QAbstractAnimation.Running: "Running", 
                          QAbstractAnimation.Paused: "Paused"}
            print(f"[DEBUG] Animation state changed: {state_names.get(old_state, old_state)} -> {state_names.get(new_state, new_state)}", file=sys.stderr)
        
        # 确保连接信号
        window_animation.finished.connect(on_window_animation_finished)
        window_animation.stateChanged.connect(on_animation_state_changed)
        
        print(f"[DEBUG] ===== Starting window animation =====", file=sys.stderr)
        print(f"[DEBUG] From: {current_rect}", file=sys.stderr)
        print(f"[DEBUG] To: {target_rect}", file=sys.stderr)
        print(f"[DEBUG] Animation duration: {window_animation.duration()}ms", file=sys.stderr)
        
        window_animation.start()
        
        print(f"[DEBUG] Animation started, state: {window_animation.state()}", file=sys.stderr)
        
        # 添加一个备用检查：如果动画在预期时间内没有完成，强制触发
        def check_animation_complete():
            from PySide6.QtCore import QAbstractAnimation
            if window_animation.state() == QAbstractAnimation.Stopped:
                print(f"[DEBUG] Animation stopped, checking if finished callback was called", file=sys.stderr)
                if self.isVisible():
                    print(f"[WARNING] Window still visible after animation stopped, forcing hide", file=sys.stderr)
                    on_window_animation_finished()
        
        QTimer.singleShot(350, check_animation_complete)  # 比动画时长稍长一点
    
    def _init_transfer_manager(self):
        """初始化传输管理器（异步执行，避免阻塞UI）"""
        import sys
        print(f"[DEBUG] Starting transfer manager initialization (async)", file=sys.stderr)
        
        def init_in_thread():
            """在后台线程中执行耗时操作"""
            try:
                import sys
                print(f"[DEBUG] Fetching user info from API...", file=sys.stderr)
                api_client = ApiClient.from_config()
                user_info = api_client._get("/api/user_info")
                
                if isinstance(user_info, dict) and user_info.get("status") == "success":
                    data = user_info.get("data", {})
                    user_id = str(data.get("user_id", ""))
                    user_name = data.get("name", "Unknown")
                    avatar_url = data.get("avatar_url")
                    
                    print(f"[DEBUG] User info fetched, creating TransferManager...", file=sys.stderr)
                    
                    # 在主线程中创建 TransferManager（因为需要连接信号）
                    def create_manager():
                        try:
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
                            
                            print(f"[DEBUG] Starting TransferManager...", file=sys.stderr)
                            self._transfer_manager.start()
                            
                            self._refresh_timer = QTimer()
                            self._refresh_timer.timeout.connect(self._refresh_devices)
                            self._refresh_timer.start(2000)
                            
                            print(f"[DEBUG] TransferManager started successfully", file=sys.stderr)
                        except Exception as e:
                            import sys
                            logger.error(f"创建传输管理器失败: {e}")
                            print(f"[ERROR] Failed to create TransferManager: {e}", file=sys.stderr)
                            Toast.show_message(self, f"初始化失败: {e}")
                    
                    # 在主线程中执行创建操作
                    QTimer.singleShot(0, create_manager)
                else:
                    def show_error():
                        Toast.show_message(self, "无法获取用户信息，请先登录")
                    QTimer.singleShot(0, show_error)
            except Exception as e:
                import sys
                logger.error(f"初始化传输管理器失败: {e}")
                print(f"[ERROR] Failed to init transfer manager: {e}", file=sys.stderr)
                def show_error():
                    Toast.show_message(self, f"初始化失败: {e}")
                QTimer.singleShot(0, show_error)
        
        # 在后台线程中执行API调用
        import threading
        thread = threading.Thread(target=init_in_thread, daemon=True)
        thread.start()
    
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
