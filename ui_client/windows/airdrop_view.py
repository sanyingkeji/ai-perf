#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔空投送界面（模仿苹果 AirDrop 风格）
支持拖放文件到设备头像进行传输
支持窗口拖拽到边缘自动变成图标
"""

import base64
import contextlib
import imghdr
import os
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, Set
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QMessageBox,
    QApplication, QGraphicsDropShadowEffect,
    QMenu, QFileDialog, QScrollArea
)
from PySide6.QtCore import (
    Qt,
    QSize,
    QTimer,
    Signal,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    QRect,
    QRectF,
    QEvent,
    QMetaObject,
    Q_ARG,
    Slot,
    QUrl,
)
from PySide6.QtGui import (
    QFont,
    QPixmap,
    QPainter,
    QColor,
    QBrush,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QCursor,
    QContextMenuEvent,
    QClipboard,
    QDesktopServices,
    QImage,
)
import httpx
import logging
import sys

from utils.lan_transfer.manager import TransferManager
from utils.lan_transfer.discovery import DeviceInfo
from utils.api_client import ApiClient
from widgets.toast import Toast
from widgets.transfer_confirm_dialog import TransferConfirmDialog
from widgets.clipboard_receive_dialog import ClipboardReceiveDialog
from utils.notification import send_notification

logger = logging.getLogger(__name__)


def _debug_log(message: str):
    """统一的隔空投送调试输出（已禁用）"""
    pass


class CircularProgressAvatar(QLabel):
    """带圆形进度条的头像"""
    
    def __init__(self, avatar_size: int = 64, parent=None):
        super().__init__(parent)
        self._avatar_size = avatar_size
        self._progress = 0  # 0-100
        # 留出进度条空间（上下左右各4像素）
        container_size = avatar_size + 8
        self.setFixedSize(container_size, container_size)
        self.setScaledContents(False)  # 不使用自动缩放，手动控制居中显示
        self.setAcceptDrops(True)
        # 确保布局方向正确
        self.setLayoutDirection(Qt.LeftToRight)
    
    def set_progress(self, progress: int):
        """设置进度（0-100）"""
        self._progress = max(0, min(100, progress))
        self.update()  # 触发重绘
    
    def paintEvent(self, event):
        """绘制头像和进度条"""
        # 手动绘制头像（完全填满容器，内外层尺寸对齐）
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            high_quality_hint = getattr(QPainter, "HighQualityAntialiasing", None)
            if high_quality_hint is not None:
                painter.setRenderHint(high_quality_hint, True)
            
            pixmap = self.pixmap()
            if pixmap and not pixmap.isNull():
                container_size = self.width()
                scaled_pixmap = pixmap.scaled(
                    container_size,
                    container_size,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation
                )
                x = (container_size - scaled_pixmap.width()) // 2
                y = (container_size - scaled_pixmap.height()) // 2
                painter.drawPixmap(x, y, scaled_pixmap)
            
            if self._progress > 0:
                pen_width = 3
                pen = painter.pen()
                pen.setWidth(pen_width)
                pen.setCapStyle(Qt.RoundCap)
                pen.setColor(QColor(0, 122, 255))
                painter.setPen(pen)
                
                arc_rect = QRectF(
                    pen_width / 2,
                    pen_width / 2,
                    self.width() - pen_width,
                    self.height() - pen_width
                )
                start_angle = 90 * 16
                span_angle = -int(self._progress * 360 * 16 / 100)
                painter.drawArc(arc_rect, start_angle, span_angle)
        finally:
            painter.end()


class DeviceItemWidget(QWidget):
    """设备列表项（支持拖放，苹果风格）"""
    
    file_dropped = Signal(Path, DeviceInfo)  # 文件拖放信号
    
    def __init__(self, device: DeviceInfo, parent=None):
        super().__init__(parent)
        self._device = device
        self._progress = 0
        self._setup_ui()
        self.setAcceptDrops(True)
    
    def sizeHint(self):
        """返回基于内容的推荐大小"""
        if hasattr(self, "name_label"):
            name_width = self.name_label.fontMetrics().horizontalAdvance(self.name_label.text())
            name_height = self.name_label.fontMetrics().height()
        else:
            name_width = 0
            name_height = 20
        
        if hasattr(self, "device_label"):
            device_width = self.device_label.fontMetrics().horizontalAdvance(self.device_label.text())
            device_height = self.device_label.fontMetrics().height()
        else:
            device_width = 0
            device_height = 15
        
        content_width = max(72, name_width, device_width)
        width = max(110, content_width + 18)  # 预留左右内边距
        height = 72 + name_height + device_height + 14  # 紧凑但保留余量
        height = max(148, height)
        return QSize(int(width), int(height))
    
    def _setup_ui(self):
        self._avatar_size = 64
        # 确保布局方向是从上到下
        self.setLayoutDirection(Qt.LeftToRight)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(110, 148)
        
        # 使用带进度条的头像组件
        # 注意：CircularProgressAvatar的容器大小是avatar_size+8，所以传入avatar_size即可
        self.avatar_label = CircularProgressAvatar(self._avatar_size, self)
        self.avatar_label.setAcceptDrops(True)
        # 设置头像在中心位置（考虑进度条的空间）
        self.avatar_label.setAlignment(Qt.AlignCenter)
        self.avatar_label.setStyleSheet(f"""
            QLabel {{
                border: none;
                border-radius: {(self._avatar_size + 8) // 2}px;
                background-color: transparent;
            }}
            QLabel:hover {{
                border: none;
                background-color: transparent;
            }}
        """)

        # 确保顺序：名字 -> 头像 -> 设备名（从上到下）
        # 第一步：先加载头像内容，然后添加头像到布局（最上方，第一行）
        self._load_avatar()
        layout.addWidget(self.avatar_label, alignment=Qt.AlignCenter)
        layout.addSpacing(2)  # 头像和名字之间的间距
        
        # 第二步：添加同事名字（中间，第二行）
        self.name_label = QLabel(self._device.name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFont(QFont("SF Pro Display", 12, QFont.Medium))
        self.name_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(self.name_label, alignment=Qt.AlignCenter)
        layout.addSpacing(1)  # 名字和设备名之间的间距
        
        # 第三步：添加设备名（最下方，第三行）
        device_text = self._device.device_name or self._device.ip
        self._default_device_text = device_text
        self.device_label = QLabel(device_text)
        self.device_label.setAlignment(Qt.AlignCenter)
        self.device_label.setWordWrap(True)
        device_font = QFont("SF Pro Display", 9)
        self.device_label.setFont(device_font)
        self._default_device_style = "color: #8E8E93; font-size: 9px; padding-top: 0px; background-color: transparent;"
        self.device_label.setStyleSheet(self._default_device_style)
        layout.addWidget(self.device_label, alignment=Qt.AlignCenter)
        layout.addStretch()

    def set_device_status(self, text: Optional[str], color: Optional[str] = None):
        """更新设备名区域的状态文本"""
        if text:
            color = color or "#8E8E93"
            self.device_label.setText(text)
            self.device_label.setStyleSheet(
                f"color: {color}; font-size: 9px; padding-top: 0px; background-color: transparent;"
            )
        else:
            self.device_label.setText(self._default_device_text)
            self.device_label.setStyleSheet(self._default_device_style)
    
    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        has_clip_text = bool(clipboard.mimeData().hasText())
        image = clipboard.image()
        has_clip_image = image is not None and not image.isNull()
        paste_action = None
        
        if not self.avatar_label.geometry().contains(event.pos()):
            return
        
        if has_clip_text or has_clip_image:
            paste_action = menu.addAction("粘贴并发送")
        browse_action = menu.addAction("浏览...")
        
        action = menu.exec(event.globalPos())
        if paste_action and action == paste_action:
            temp_path = None
            if has_clip_image:
                temp_path = self._create_clipboard_image_temp_file(image)
            else:
                text = clipboard.text().strip()
                if text:
                    temp_path = self._create_clipboard_text_temp_file(text)
            if temp_path:
                self.file_dropped.emit(temp_path, self._device)
        elif action == browse_action:
            file_path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件")
            if file_path:
                self.file_dropped.emit(Path(file_path), self._device)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        self.avatar_label.setStyleSheet("""
            QLabel {
                border: none;
                border-radius: 40px;
                background-color: rgba(0, 122, 255, 0.15);
            }
        """)
    
    def dragLeaveEvent(self, event):
        """拖拽离开事件"""
        self.avatar_label.setStyleSheet(f"""
            QLabel {{
                border: none;
                border-radius: {(self._avatar_size + 8) // 2}px;
                background-color: transparent;
            }}
            QLabel:hover {{
                border: none;
                background-color: transparent;
            }}
        """)
    
    def dropEvent(self, event: QDropEvent):
        """拖放事件"""
        self.avatar_label.setStyleSheet(f"""
            QLabel {{
                border: none;
                border-radius: {(self._avatar_size + 8) // 2}px;
                background-color: transparent;
            }}
            QLabel:hover {{
                border: none;
                background-color: transparent;
            }}
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
                        # 容器大小是avatar_size+8，确保pixmap大小和容器一致
                        container_size = self._avatar_size + 8
                        circular_pixmap = self._make_circular(pixmap, container_size)
                        # 确保在主线程更新UI
                        QMetaObject.invokeMethod(
                            self.avatar_label,
                            "setPixmap",
                            Qt.QueuedConnection,
                            Q_ARG(QPixmap, circular_pixmap)
                        )
                        return
            except Exception as e:
                logger.error(f"加载头像失败: {e}")
            self._set_default_avatar()
        
        import threading
        thread = threading.Thread(target=load, daemon=True)
        thread.start()

    def _create_clipboard_text_temp_file(self, text: str) -> Optional[Path]:
        """将文本（可能包含base64图片）保存到临时文件"""
        if not text:
            return None
        is_image, image_format = self._detect_base64_image(text)
        temp_dir = Path(os.getenv("TEMP", "/tmp"))
        temp_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        if is_image:
            safe_format = (image_format or "png").replace("/", "_")
            filename = f"clipboard_image_{safe_format}-{timestamp}.b64img"
        else:
            filename = f"clipboard_{timestamp}.txt"
        temp_path = temp_dir / filename
        temp_path.write_text(text, encoding='utf-8')
        return temp_path

    def _create_clipboard_image_temp_file(self, image: QImage) -> Optional[Path]:
        """将剪贴板图片保存为临时PNG文件"""
        if image.isNull():
            return None
        temp_dir = Path(os.getenv("TEMP", "/tmp"))
        temp_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        filename = f"clipboard_image_png-{timestamp}.png"
        temp_path = temp_dir / filename
        if image.save(str(temp_path), "PNG"):
            return temp_path
        return None

    @staticmethod
    def _detect_base64_image(text: str) -> Tuple[bool, Optional[str]]:
        """检测文本是否为base64图片"""
        if not text:
            return False, None
        data = text.strip()
        header_format = None
        base64_data = data
        if data.startswith("data:image/") and "," in data:
            header, _, base64_data = data.partition(',')
            try:
                header_format = header.split('/')[1].split(';')[0]
            except IndexError:
                header_format = None
        base64_data = ''.join(base64_data.split())
        try:
            decoded = base64.b64decode(base64_data, validate=True)
        except Exception:
            return False, None
        detected = imghdr.what(None, decoded)
        image_format = detected or header_format
        if not image_format:
            return False, None
        return True, image_format
    
    def _set_default_avatar(self):
        """设置默认头像"""
        # 容器大小是avatar_size+8，pixmap大小要和容器一致，确保内外层尺寸对齐
        container_size = self._avatar_size + 8
        pixmap = QPixmap(container_size, container_size)
        pixmap.fill(Qt.transparent)  # 透明背景
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(142, 142, 147)))
        painter.setPen(Qt.NoPen)
        # 绘制圆形，在容器中心，半径为avatar_size/2
        center = container_size // 2
        radius = self._avatar_size // 2
        painter.drawEllipse(center - radius, center - radius, self._avatar_size, self._avatar_size)
        
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("SF Pro Display", 32, QFont.Medium))
        first_char = self._device.name[0].upper() if self._device.name else "?"
        # 文字绘制在圆形区域内
        painter.drawText(center - radius, center - radius, self._avatar_size, self._avatar_size, Qt.AlignCenter, first_char)
        painter.end()
        
        # 将头像设置为圆形（传入容器大小，确保内外层尺寸对齐）
        circular_pixmap = self._make_circular(pixmap, container_size)
        # 设置pixmap，大小和容器完全一致
        self.avatar_label.setPixmap(circular_pixmap)
    
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
    
    def set_progress(self, progress: int):
        """设置传输进度（0-100）"""
        self._progress = progress
        if hasattr(self.avatar_label, 'set_progress'):
            self.avatar_label.set_progress(progress)
    
    @property
    def device(self) -> DeviceInfo:
        return self._device


class AirDropView(QWidget):
    """隔空投送主界面（苹果风格）"""
    
    # 信号：窗口需要隐藏（已移除悬浮图标，不再需要）
    # should_hide_to_icon = Signal(QPoint)  # 已移除
    
    # 信号：传输请求结果（用于从后台线程通知主线程）
    transfer_request_result = Signal(dict, str, str, str, int, str)  # result, file_path, device_name, device_ip, device_port, request_id
    
    @staticmethod
    def _log_with_timestamp(message: str):
        """打印带时间戳的日志（精确到毫秒）"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # 精确到毫秒
        print(f"[{timestamp}] {message}", file=sys.stderr)
    
    @staticmethod
    def _get_macos_y_offset(window=None):
        """获取 macOS Y 坐标偏移量（用于补偿系统自动调整）
        
        在 macOS 上，系统可能会自动调整窗口的 Y 坐标（通常是标题栏高度），
        导致 geometry().y() 和 pos().y() 有差值。这个方法动态检测这个偏移量。
        
        Args:
            window: 窗口对象，如果提供则动态检测，否则根据系统版本估算
        
        Returns:
            int: Y 坐标偏移量（像素），非 macOS 系统返回 0
        """
        import platform
        if platform.system() != "Darwin":
            return 0  # Windows/Linux 不需要偏移
        
        # 如果提供了窗口对象，动态检测偏移量
        if window is not None:
            try:
                geo = window.geometry()
                pos = window.pos()
                # 计算差值（通常是标题栏高度）
                offset = geo.y() - pos.y()
                if offset > 0:
                    return offset
            except:
                pass
        
        # 如果动态检测失败，根据 macOS 版本估算
        try:
            import platform as plat
            mac_version = plat.mac_ver()[0]  # 例如 "14.7.8"
            if mac_version:
                major_version = int(mac_version.split('.')[0])
                # macOS 11+ 通常有 28 像素偏移（标题栏高度）
                # macOS 10.13-10.15 可能偏移不同或没有偏移
                if major_version >= 11:
                    return 28
                elif major_version == 10:
                    # macOS 10.13-10.15，可能需要检测，暂时返回 0
                    # 如果实际测试发现有偏移，可以调整
                    return 0
        except:
            pass
        
        return 0  # 默认不偏移
    
    def __init__(self, parent=None):
        super().__init__(parent)
        _debug_log("Initializing AirDropView...")
        self._transfer_manager: Optional[TransferManager] = None
        self._transferring = False
        self._current_target: Optional[DeviceInfo] = None
        self._pending_requests: Dict[str, dict] = {}  # 待处理的传输请求
        self._was_hidden_to_icon = False  # 标记窗口是否被隐藏到图标
        
        try:
            self._setup_ui()
            self._setup_drag_detection()
            # 延迟初始化传输管理器，避免阻塞UI创建
            _debug_log("Scheduling transfer manager initialization...")
            QTimer.singleShot(0, self._init_transfer_manager)
        except Exception as e:
            import traceback
            error_msg = f"AirDropView 初始化失败: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            print(f"[ERROR] {error_msg}", file=sys.stderr)
            # 即使初始化失败，也创建一个基本的窗口，避免完全无法显示
            raise
    
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
        # 检查是否在标题栏区域（顶部50像素，macOS标题栏可能更高）
        if event.position().y() <= 50:
            # 完全忽略双击事件，不执行默认的扩大操作
            event.ignore()
            # 不调用 super()，完全阻止事件传播
            return
        # 非标题栏区域的双击事件也禁止（防止任何双击放大）
        event.ignore()
        return
    
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
            DeviceItemWidget {
                /* 确保子组件布局方向正确 */
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
        self.devices_list.setSelectionMode(QListWidget.NoSelection)
        self.devices_list.setFocusPolicy(Qt.NoFocus)
        # 设置视图模式为IconMode，允许item自由设置大小
        self.devices_list.setViewMode(QListWidget.IconMode)
        # 设置流式布局，横向排列
        self.devices_list.setFlow(QListWidget.LeftToRight)
        # 设置item大小模式为固定
        self.devices_list.setResizeMode(QListWidget.Fixed)
        self.devices_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
            }
            QListWidget::item {
                background-color: transparent;
                border: none;
                margin: 6px;
            }
            QListWidget::item:hover {
                background-color: transparent;
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
        
        # 信号图标
        signal_label = QLabel()
        signal_label.setAlignment(Qt.AlignCenter)
        # 加载图标
        app_dir = Path(__file__).parent.parent
        icon_path = app_dir / "resources" / "airdrop.png"
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                # 缩放图标到合适大小（32x32像素，更小）
                scaled_pixmap = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # 将图标转换为黑色
                black_pixmap = self._tint_pixmap_black(scaled_pixmap)
                signal_label.setPixmap(black_pixmap)
                signal_label.setStyleSheet("color: #0969da;")
            else:
                # 如果加载失败，使用默认emoji
                signal_label.setText("📡")
                signal_label.setFont(QFont("SF Pro Display", 32))
                signal_label.setStyleSheet("color: #000000;")
        else:
            # 如果文件不存在，使用默认emoji
            signal_label.setText("📡")
            signal_label.setFont(QFont("SF Pro Display", 32))
            signal_label.setStyleSheet("color: #000000;")
        background_layout.addWidget(signal_label)
        
        # 背景文字
        self._background_label = QLabel('"隔空投送"可让你与附近的同事立即共享。')
        self._background_label.setAlignment(Qt.AlignCenter)
        self._background_label.setFont(QFont("SF Pro Display", 13))
        self._background_label.setStyleSheet("color: #808080;")  # 调整为更深的灰色，更易看清
        self._background_label.setWordWrap(True)
        background_layout.addWidget(self._background_label)
        
        self._background_frame.setParent(main_widget)
        
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
        """窗口大小改变时调整背景文字位置，并禁止窗口大小改变"""
        # 如果窗口大小被改变，立即恢复为原始大小
        if hasattr(self, '_fixed_size') and self._fixed_size:
            current_size = self.size()
            if current_size != self._fixed_size:
                # 窗口大小被改变，立即恢复
                self.setFixedSize(self._fixed_size)
                return
        
        super().resizeEvent(event)
        self._update_background_label_position()
    
    def _tint_pixmap_black(self, pixmap: QPixmap) -> QPixmap:
        """将图标转换为黑色"""
        # 创建新的pixmap，使用源pixmap的尺寸
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 使用源pixmap作为mask，然后填充黑色
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, pixmap)
        
        # 使用CompositionMode_SourceIn将颜色改为黑色
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), QColor(9, 105, 218))  # 黑色
        
        painter.end()
        return result
    
    def _check_mouse_near_hidden_area(self):
        """检查鼠标是否在屏幕边缘（如果窗口已隐藏）"""
        # 如果正在执行显示动画，不检测鼠标
        if self._is_showing_animation:
            return
        
        if not self._was_hidden_to_icon or not self._hidden_rect:
            return
        
        if self.isVisible():
            # 窗口已显示，不需要检测
            return
        
        # 获取全局鼠标位置
        mouse_pos = QCursor.pos()
        # 使用 availableGeometry() 获取可用区域（排除任务栏）
        screen = QApplication.primaryScreen().availableGeometry()
        
        # 边缘检测区域：
        # X坐标：只在屏幕最边缘（鼠标无法再移动）时触发，macOS不允许鼠标完全消失
        # 注意：macOS上鼠标可能位于屏幕边缘之外（如右边缘时可能在1792，而屏幕右边缘是1791）
        # Y坐标：只检测窗口高度范围内，不扩展
        edge_margin = 1  # 边缘检测范围（像素），只检测最边缘的1像素
        margin_y = 0  # Y坐标不扩展，只检测窗口高度范围内
        
        hidden_y = self._hidden_rect.y()
        hidden_height = self._hidden_rect.height()
        
        # 根据隐藏方向决定检测哪一边缘
        # 如果从左侧隐藏，检测屏幕左边缘
        # 如果从右侧隐藏，检测屏幕右边缘（包括屏幕外，因为macOS不允许鼠标完全消失）
        if hasattr(self, '_hidden_to_left') and self._hidden_to_left:
            # 从左侧隐藏，检测屏幕左边缘
            # 只检测屏幕最左边缘（鼠标无法再左移）
            detect_left = screen.left()
            detect_right = screen.left() + edge_margin
        else:
            # 从右侧隐藏，检测屏幕右边缘
            # 检测范围包括屏幕右边缘和屏幕外（因为macOS不允许鼠标完全消失，鼠标可能在屏幕外）
            # 例如：屏幕右边缘是1791，鼠标可能在1792（屏幕外）
            detect_left = screen.right() - edge_margin
            detect_right = screen.right() + edge_margin  # 扩大到屏幕外，允许检测屏幕外的鼠标位置
        
        # Y坐标范围：只检测窗口高度范围内，不扩展
        detect_top = hidden_y
        detect_bottom = hidden_y + hidden_height
        
        # 检查鼠标是否在边缘检测区域内
        is_in_x_range = detect_left <= mouse_pos.x() <= detect_right
        is_in_y_range = detect_top <= mouse_pos.y() <= detect_bottom
        
        if is_in_x_range and is_in_y_range:
            # 鼠标完全在屏幕边缘上，显示窗口
            # 通知主窗口显示
            if hasattr(self, 'window') and self.window():
                # 通过主窗口显示
                from windows.main_window import MainWindow
                # 查找主窗口实例
                for widget in QApplication.allWidgets():
                    if isinstance(widget, MainWindow):
                        widget._show_airdrop_window()
                        break
    
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
        self._hidden_rect = None  # 窗口隐藏后的位置（用于鼠标检测）
        self._last_window_pos = self.pos()  # 记录上次窗口位置
        self._drag_detected = False  # 是否检测到拖拽
        self._position_unchanged_count = 0  # 位置未变化的连续次数
        self._is_showing_animation = False  # 是否正在执行显示动画
        
        # 启动窗口位置跟踪定时器（用于检测拖拽，特别是 macOS 系统标题栏拖拽）
        self._position_track_timer = QTimer()
        self._position_track_timer.timeout.connect(self._check_window_dragging)
        self._position_track_timer.start(50)  # 每50ms检查一次
        
        # 启动全局鼠标跟踪定时器（用于检测鼠标是否在隐藏区域）
        self._mouse_track_timer = QTimer()
        self._mouse_track_timer.timeout.connect(self._check_mouse_near_hidden_area)
        self._mouse_track_timer.start(100)  # 每100ms检查一次
    
    def _check_window_dragging(self):
        """通过窗口位置变化检测拖拽（用于 macOS 系统标题栏拖拽）"""
        import sys
        import platform
        from PySide6.QtGui import QCursor
        from PySide6.QtCore import Qt
        
        # 如果正在执行显示动画，不检测拖拽和隐藏逻辑
        if self._is_showing_animation:
            return
        
        current_pos = self.pos()
        # 检查鼠标左键是否还在按下（通过全局鼠标按钮状态）
        mouse_buttons = QApplication.mouseButtons()
        is_left_button_pressed = (mouse_buttons & Qt.LeftButton) == Qt.LeftButton
        
        if current_pos != self._last_window_pos and self.isVisible():
            # 窗口位置改变了，可能正在被拖拽
            if not self._drag_detected:
                # 首次检测到位置变化，认为是开始拖拽
                self._drag_detected = True
                self._position_unchanged_count = 0
            
            self._last_window_pos = current_pos
            self._position_unchanged_count = 0  # 重置未变化计数
        else:
            # 窗口位置没有变化
            if self._drag_detected:
                # 如果鼠标左键还在按下，说明还在拖拽中（可能拖到了边缘或暂时停止移动）
                if is_left_button_pressed:
                    # 鼠标还在按下，不认为拖拽结束
                    self._position_unchanged_count = 0
                else:
                    # 鼠标已经释放，但需要确认位置确实不再变化（避免误判）
                    self._position_unchanged_count += 1
                    # 只有当位置连续多次（约200ms）没有变化，且鼠标已释放时，才认为拖拽结束
                    if self._position_unchanged_count >= 4:  # 4次 * 50ms = 200ms
                        # 检查窗口是否超出屏幕（使用可用区域，排除任务栏）
                        screen = QApplication.primaryScreen().availableGeometry()
                        window_rect = self.geometry()
                        
                        # 只要窗口超出屏幕就应该隐藏（不是完全在屏幕外，而是有任何部分超出）
                        is_left_outside = window_rect.left() < screen.left()  # 窗口左边缘超出屏幕左边缘
                        is_right_outside = window_rect.right() > screen.right()  # 窗口右边缘超出屏幕右边缘
                        should_hide = is_left_outside or is_right_outside
                        
                        
                        if should_hide:
                            # 窗口左右超出屏幕，立即保存当前位置（在系统调整之前）
                            # 保存隐藏前的位置（用于恢复时显示）
                            # 注意：只有在_before_hide_rect未设置时才保存，避免覆盖之前保存的正确位置
                            if not hasattr(self, '_before_hide_rect') or self._before_hide_rect is None:
                                current_geo = self.geometry()
                                current_pos = self.pos()
                                # 使用 pos() 的 Y 坐标，因为它是实际窗口位置，geometry() 的 Y 可能包含标题栏等偏移
                                # 但保持使用 geometry() 的宽度和高度
                                self._before_hide_rect = QRect(current_pos.x(), current_pos.y(), current_geo.width(), current_geo.height())
                            # 如果已经保存过位置，使用之前保存的位置，不覆盖
                            # 触发隐藏动画
                            QTimer.singleShot(50, self._animate_to_icon)
                        
                        self._drag_detected = False
                        self._position_unchanged_count = 0
            else:
                self._position_unchanged_count = 0
    
    def mousePressEvent(self, event: QMouseEvent):
        """鼠标按下"""
        import sys
        import platform
        
        # 添加调试日志
        y_pos = event.position().y()
        is_title_bar = y_pos <= 50  # macOS 标题栏可能更高，扩大到50像素
        
        if event.button() == Qt.LeftButton:
            # macOS 上，系统标题栏可能会拦截事件，所以我们需要检测整个窗口顶部区域
            # 或者检测是否在窗口的标题栏区域（包括系统标题栏）
            if is_title_bar:
                # 记录鼠标按下时的全局位置和窗口位置
                self._drag_start_pos = event.globalPosition().toPoint()
                self._drag_window_pos = self.pos()
                self._is_dragging = False
                self._edge_triggered = False
            else:
                super().mousePressEvent(event)
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
                
                # 允许窗口超出屏幕范围（不限制）
                self.move(new_pos)
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """鼠标释放"""
        import sys
        
        if self._drag_start_pos is not None:
            # 检查窗口是否超出屏幕（使用可用区域，排除任务栏）
            screen = QApplication.primaryScreen().availableGeometry()
            window_rect = self.geometry()
            
            # 只要窗口超出屏幕就应该隐藏（不是完全在屏幕外，而是有任何部分超出）
            is_left_outside = window_rect.left() < screen.left()  # 窗口左边缘超出屏幕左边缘
            is_right_outside = window_rect.right() > screen.right()  # 窗口右边缘超出屏幕右边缘
            should_hide = is_left_outside or is_right_outside
            
            # 释放拖拽时打印日志（只要按下过标题栏就打印，不管是否真正移动了）
            
            if self._is_dragging and should_hide:
                # 窗口左右超出屏幕，立即保存当前位置（在系统调整之前）
                # 保存隐藏前的位置（用于恢复时显示）
                # 注意：只有在_before_hide_rect未设置时才保存，避免覆盖之前保存的正确位置
                if not hasattr(self, '_before_hide_rect') or self._before_hide_rect is None:
                    current_geo = self.geometry()
                    current_pos = self.pos()
                    # 使用 pos() 的 Y 坐标，因为它是实际窗口位置，geometry() 的 Y 可能包含标题栏等偏移
                    # 但保持使用 geometry() 的宽度和高度
                    self._before_hide_rect = QRect(current_pos.x(), current_pos.y(), current_geo.width(), current_geo.height())
                # 如果已经保存过位置，使用之前保存的位置，不覆盖
                # 触发隐藏动画
                QTimer.singleShot(50, self._animate_to_icon)
        
        self._drag_start_pos = None
        self._drag_window_pos = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)
    
    def _animate_from_icon(self, target_rect: QRect):
        """动画：窗口从隐藏位置滑出显示（与隐藏动画对应）"""
        import sys
        
        # 如果已经在执行显示动画，直接返回，防止重复调用
        if self._is_showing_animation:
            return
        
        # 使用 availableGeometry() 获取可用区域（排除任务栏）
        screen = QApplication.primaryScreen().availableGeometry()
        window_width = target_rect.width()
        window_height = target_rect.height()
        
        # 确定窗口从哪个边缘滑出
        # 根据隐藏方向决定从哪个方向滑出
        # 注意：起始位置应该与隐藏位置一致（保留1像素可见）
        visible_pixel = 1  # 保留1像素可见
        
        # 计算Y坐标的最大值：可用区域底部 - 窗口高度 - macOS Y偏移量
        # 当窗口被下边缘挡住时，固定Y坐标为这个最大值
        # 动画出现时的Y坐标和动画隐藏时的Y坐标都应该使用这个值
        y_offset = self._get_macos_y_offset(self)  # 动态检测 macOS Y 坐标偏移量
        max_y = screen.bottom() - window_height - y_offset
        
        # 检查目标Y坐标是否会导致窗口下边缘超出可用区域
        # 如果会超出，使用Y坐标的最大值（这样显示和隐藏动画的Y坐标就一致了）
        target_y = target_rect.y()
        if target_y + window_height > screen.bottom():
            target_y = max_y  # 使用Y坐标的最大值
        
        if hasattr(self, '_hidden_to_left') and self._hidden_to_left:
            # 从左侧滑出：窗口从屏幕左侧外滑入（保留1像素可见的位置）
            start_x = screen.left() - window_width + visible_pixel
            start_y = target_y  # 使用调整后的Y坐标（与隐藏动画一致）
        else:
            # 从右侧滑出：窗口从屏幕右侧外滑入（保留1像素可见的位置）
            start_x = screen.right() - visible_pixel
            start_y = target_y  # 使用调整后的Y坐标（与隐藏动画一致）
        
        # 更新 target_rect 的 Y 坐标，确保使用调整后的值（当窗口被下边缘挡住时，使用Y坐标的最大值）
        target_rect = QRect(target_rect.x(), target_y, target_rect.width(), target_rect.height())
        
        # 先设置窗口在隐藏位置（屏幕外）
        start_rect = QRect(start_x, start_y, window_width, window_height)
        
        
        # 立即标记正在执行显示动画，防止重复调用和位置检测
        self._is_showing_animation = True
        
        # 如果窗口已经显示，先隐藏它
        if self.isVisible():
            self.hide()
        
        # 设置窗口在起始位置（屏幕外）
        # 在显示动画开始前，强制设置窗口位置，防止系统调整
        self.setGeometry(start_rect)
        self.move(start_rect.x(), start_rect.y())
        
        # 使用 QTimer.singleShot 延迟一下，确保窗口位置设置完成
        # 延迟时间稍微长一点，确保窗口位置不会被其他代码立即修改
        def start_animation():
            # 再次检查，防止在延迟期间被重复调用
            if not self._is_showing_animation:
                return
            
            # 在显示前，多次强制设置窗口到起始位置
            self.setGeometry(start_rect)
            self.move(start_rect.x(), start_rect.y())
            self.resize(start_rect.width(), start_rect.height())
            
            # 显示窗口（此时窗口在屏幕外的起始位置）
            self.show()
            
            # 显示后立即再次设置位置（防止系统自动调整）
            self.setGeometry(start_rect)
            self.move(start_rect.x(), start_rect.y())
            
            self.raise_()
            self.activateWindow()
            
            # 延迟检查位置并开始动画
            QTimer.singleShot(30, lambda: check_and_start_animation())
        
        def check_and_start_animation():
            """检查位置并开始动画"""
            if not self._is_showing_animation:
                return
            
            # 再次强制设置位置
            self.setGeometry(start_rect)
            self.move(start_rect.x(), start_rect.y())
            
            current_geo = self.geometry()
            if abs(current_geo.x() - start_rect.x()) > 5 or abs(current_geo.y() - start_rect.y()) > 5:
                import sys
                # 使用实际位置作为起始位置，而不是期望位置
                actual_start_rect = QRect(current_geo.x(), current_geo.y(), start_rect.width(), start_rect.height())
                really_start_animation(actual_start_rect)
                return
            
            # 开始动画
            really_start_animation(start_rect)
        
        def really_start_animation(actual_start_rect: QRect):
            """真正开始动画，使用实际起始位置"""
            import sys
            nonlocal target_rect  # 声明 target_rect 是外部作用域的变量
            nonlocal target_y  # 声明 target_y 是外部作用域的变量（在回调函数中使用）
            
            # 在显示动画开始时，检查Y坐标是否已被系统调整
            actual_pos_before_animation = self.pos()
            actual_geo_before_animation = self.geometry()
            
            # 如果Y坐标已被系统调整，强制调整回保存的原始Y坐标
            # 因为系统在显示窗口时会自动调整Y坐标，我们需要强制使用保存的原始Y坐标
            if hasattr(self, '_before_hide_rect') and self._before_hide_rect:
                saved_y = self._before_hide_rect.y()
                if abs(actual_geo_before_animation.y() - saved_y) > 5:
                    # 强制调整窗口Y坐标到保存的原始位置
                    self.move(actual_geo_before_animation.x(), saved_y)
                    # 重新获取位置
                    actual_geo_before_animation = self.geometry()
                    actual_pos_before_animation = self.pos()
                    # 使用保存的原始Y坐标作为目标，而不是系统调整后的Y坐标
                    target_rect = QRect(target_rect.x(), saved_y, target_rect.width(), target_rect.height())
            
            
            # 确保窗口在起始位置和大小
            self.setGeometry(actual_start_rect)
            
            # 在 macOS 上，使用 pos 属性动画可能更可靠
            # 先确保窗口大小正确
            if actual_start_rect.width() != target_rect.width() or actual_start_rect.height() != target_rect.height():
                self.resize(target_rect.width(), target_rect.height())
            
            # 使用实际位置作为起始位置（可能被系统调整过）
            actual_start_pos = self.pos()
            actual_start_x = actual_start_pos.x()
            actual_start_y = actual_start_pos.y()
            
            # 使用保存的原始Y坐标作为目标Y坐标（避免累积偏移）
            # _before_hide_rect 中保存的是隐藏前的原始位置
            original_y = target_rect.y()  # 目标位置已经是从 _before_hide_rect 计算出来的，使用它
            
            # 计算Y坐标的最大值：可用区域底部 - 窗口高度 - macOS Y偏移量
            # 当窗口被下边缘挡住时，固定Y坐标为这个最大值
            # 动画出现时的Y坐标和动画隐藏时的Y坐标都应该使用这个值
            # 使用 availableGeometry() 获取可用区域（排除任务栏）
            screen = QApplication.primaryScreen().availableGeometry()
            y_offset = self._get_macos_y_offset(self)  # 动态检测 macOS Y 坐标偏移量
            max_y = screen.bottom() - actual_start_rect.height() - y_offset
            
            # 检查原始Y坐标是否会导致窗口下边缘超出可用区域
            # 如果会超出，使用Y坐标的最大值（这样显示和隐藏动画的Y坐标就一致了）
            if original_y + actual_start_rect.height() > screen.bottom():
                target_y = max_y  # 使用Y坐标的最大值
            else:
                target_y = original_y
            
            # 更新 target_rect 的 Y 坐标，确保使用调整后的值（在回调函数中也会使用这个值）
            target_rect = QRect(target_rect.x(), target_y, target_rect.width(), target_rect.height())
            
            # 使用 pos 属性动画窗口位置（而不是 geometry）
            # 只动画X坐标，Y坐标保持实际值（接受系统调整，避免累积偏移）
            from PySide6.QtCore import QPropertyAnimation, QPoint
            pos_animation = QPropertyAnimation(self, b"pos")
            pos_animation.setDuration(300)  # 动画时间300ms
            # 起始位置使用实际位置，目标位置X使用目标值，Y使用实际值（避免累积偏移）
            pos_animation.setStartValue(QPoint(actual_start_x, actual_start_y))
            pos_animation.setEndValue(QPoint(target_rect.x(), target_y))  # Y坐标保持实际值
            pos_animation.setEasingCurve(QEasingCurve.InOutCubic)
            
            # 添加动画值变化监听，用于调试
            def on_value_changed(value):
                current_pos = self.pos()
                import sys
                if hasattr(on_value_changed, '_last_log_time'):
                    import time
                    now = time.time()
                    if now - on_value_changed._last_log_time > 0.1:  # 每100ms打印一次
                        on_value_changed._last_log_time = now
                else:
                    import time
                    on_value_changed._last_log_time = time.time()
            
            pos_animation.valueChanged.connect(on_value_changed)
            
            # 保存动画对象，防止被垃圾回收
            self._current_pos_animation = pos_animation
            
            def on_window_animation_finished():
                try:
                    # 确保窗口位置正确（防止动画完成后位置不对）
                    final_pos = self.pos()
                    final_rect = self.geometry()
                    import sys
                    
                    # 检查X坐标是否匹配
                    if abs(final_rect.x() - target_rect.x()) > 5:
                        # 只移动X坐标，保持当前Y坐标
                        self.move(target_rect.x(), final_rect.y())
                    
                    # 强制调整Y坐标到目标位置（使用调整后的 target_y，如果超出下边缘则使用 max_y）
                    # 确保动画完成后窗口位置与目标位置一致
                    if abs(final_rect.y() - target_y) > 5:
                        import sys
                        # 强制调整窗口Y坐标到目标位置（使用调整后的 target_y）
                        self.move(final_rect.x(), target_y)
                        # 重新获取位置确认
                        final_rect = self.geometry()
                        final_pos = self.pos()
                    
                    # 清理动画对象
                    if hasattr(self, '_current_pos_animation'):
                        del self._current_pos_animation
                    
                    # 重置隐藏标记
                    self._was_hidden_to_icon = False
                    if hasattr(self, '_hidden_rect'):
                        self._hidden_rect = None
                    # 清除_before_hide_rect，这样下次拖拽时可以重新保存正确的位置
                    # 如果不清除，下次拖拽时可能会使用旧的（可能被系统调整过的）位置
                    if hasattr(self, '_before_hide_rect'):
                        self._before_hide_rect = None
                    # 标记显示动画完成，允许位置检测
                    self._is_showing_animation = False
                except Exception as e:
                    import sys
                    import traceback
                    traceback.print_exc()
                    # 确保即使出错也重置标志
                    self._is_showing_animation = False
            
            # 确保连接信号
            pos_animation.finished.connect(on_window_animation_finished)
            pos_animation.start()
            
            # 添加超时保护：如果动画在1200ms后还没完成，强制完成（动画时间1000ms + 200ms缓冲）
            def timeout_handler():
                if self._is_showing_animation:
                    import sys
                    # 停止动画
                    if hasattr(self, '_current_pos_animation') and self._current_pos_animation:
                        self._current_pos_animation.stop()
                    on_window_animation_finished()
            QTimer.singleShot(500, timeout_handler)  # 动画时间300ms + 200ms缓冲
        
        # 延迟一下，确保窗口位置设置完成
        QTimer.singleShot(50, start_animation)
    
    def _animate_to_icon(self):
        """动画：窗口滑动藏入屏幕边缘（不缩放，保持窗口大小）"""
        # 如果正在执行显示动画，不允许隐藏
        if self._is_showing_animation:
            import sys
            return
        
        if not self.isVisible():
            # 如果窗口已经隐藏，直接隐藏
            self._was_hidden_to_icon = True
            self.hide()
            self.setVisible(False)
            return
        
        # 在隐藏动画开始前，立即获取窗口位置
        pos_before_animation = self.pos()
        rect_before_animation = self.geometry()
        
        # 使用 availableGeometry() 获取可用区域（排除任务栏）
        screen = QApplication.primaryScreen().availableGeometry()
        current_rect = self.geometry()
        window_width = current_rect.width()
        window_height = current_rect.height()
        
        # 使用已保存的隐藏前位置（在决定隐藏时已保存，避免被系统调整）
        # 如果没有保存，则使用当前位置（兼容旧代码）
        if not hasattr(self, '_before_hide_rect') or self._before_hide_rect is None:
            self._before_hide_rect = QRect(current_rect)
        
        # 强制使用保存的隐藏前位置的Y坐标（用户释放拖拽时的原始位置）
        # 即使系统调整了当前窗口的Y坐标，我们也使用保存的原始Y坐标，确保一致性
        original_y = self._before_hide_rect.y()
        import sys
        
        # 如果当前Y坐标与保存的Y坐标不一致，说明在保存后又被系统调整了
        # 这种情况下，我们强制使用保存的Y坐标，并立即调整窗口位置
        if abs(current_rect.y() - original_y) > 5:
            # 立即调整窗口Y坐标到保存的位置，确保动画从正确的位置开始
            self.move(current_rect.x(), original_y)
            # 重新获取位置（可能被系统再次调整，但我们已经尽力了）
            current_rect = self.geometry()
        
        # 确定窗口要隐藏到的边缘位置
        # 只允许隐藏到左右边缘，不允许隐藏到上下边缘
        left_dist = abs(current_rect.left() - screen.left())
        right_dist = abs(screen.right() - current_rect.right())
        
        # 找到最近的边缘（只考虑左右）
        # 注意：保留1像素可见，避免macOS系统自动调整位置
        visible_pixel = 1  # 保留1像素可见
        
        # 计算Y坐标的最大值：可用区域底部 - 窗口高度 - macOS Y偏移量
        # 当窗口被下边缘挡住时，固定Y坐标为这个最大值
        # 动画出现时的Y坐标和动画隐藏时的Y坐标都应该使用这个值
        y_offset = self._get_macos_y_offset(self)  # 动态检测 macOS Y 坐标偏移量
        max_y = screen.bottom() - window_height - y_offset  # Y坐标的最大值
        
        # 检查原始Y坐标是否会导致窗口下边缘超出可用区域
        # 如果会超出，使用Y坐标的最大值（这样显示和隐藏动画的Y坐标就一致了）
        if original_y + window_height > screen.bottom():
            target_y = max_y  # 使用Y坐标的最大值
        else:
            target_y = original_y  # 使用保存的原始Y坐标
        
        if left_dist <= right_dist:
            # 隐藏到左边缘：窗口几乎完全滑出屏幕左侧，但保留1像素可见
            target_x = screen.left() - window_width + visible_pixel
            # 保存隐藏方向，用于恢复时从正确方向滑出
            self._hidden_to_left = True
        else:
            # 隐藏到右边缘：窗口几乎完全滑出屏幕右侧，但保留1像素可见
            target_x = screen.right() - visible_pixel
            # 保存隐藏方向，用于恢复时从正确方向滑出
            self._hidden_to_left = False
        
        # 创建窗口隐藏动画（只改变位置，不改变大小）
        target_rect = QRect(target_x, target_y, window_width, window_height)
        
        # 在 macOS 上，使用 pos 属性动画可能更可靠
        from PySide6.QtCore import QPoint
        pos_animation = QPropertyAnimation(self, b"pos")
        pos_animation.setDuration(300)  # 动画时间300ms
        # 使用保存的原始位置作为起始位置（使用 pos() 的当前 X，但使用保存的原始 Y）
        current_pos = self.pos()
        pos_animation.setStartValue(QPoint(current_pos.x(), original_y))  # 强制使用保存的原始 Y 坐标
        pos_animation.setEndValue(QPoint(target_x, target_y))  # 使用调整后的 target_y（可能已调整以避免下边缘超出）
        pos_animation.setEasingCurve(QEasingCurve.InOutCubic)
        
        # 保存动画对象，防止被垃圾回收
        self._current_hide_pos_animation = pos_animation
        
        # 在动画完成前50ms提前隐藏窗口，避免系统在动画完成后调整位置
        animation_duration = 300  # 动画持续时间（毫秒）
        hide_before_finish = 50  # 提前隐藏的时间（毫秒）
        hide_time = animation_duration - hide_before_finish
        
        def hide_window_early():
            """在动画完成前提前隐藏窗口"""
            try:
                # 强制设置窗口位置到目标位置（使用调整后的 target_y）
                self.setGeometry(target_x, target_y, window_width, window_height)
                self.move(target_x, target_y)
                pos_before_hide = self.pos()
                rect_before_hide = self.geometry()
                
                # 标记窗口被隐藏（用于后续判断是否从边缘恢复）
                self._was_hidden_to_icon = True
                # 保存隐藏位置（用于鼠标检测）
                self._hidden_rect = target_rect
                
                # 真正隐藏窗口，这样系统不会调整位置
                self.hide()
                self.setVisible(False)
                
                # 在窗口隐藏后，重新启用位置检测（因为窗口已隐藏，不会触发循环）
                if hasattr(self, '_position_track_timer'):
                    self._position_track_timer.start(50)
            except Exception as e:
                import traceback
                traceback.print_exc()
        
        # 在动画完成前50ms隐藏窗口
        QTimer.singleShot(hide_time, hide_window_early)
        
        def on_window_animation_finished():
            try:
                # 在隐藏动画完成后，立即获取窗口位置（此时窗口应该已经隐藏）
                if self.isVisible():
                    # 如果窗口仍然可见，说明提前隐藏没有生效，在这里隐藏
                    pos_after_animation = self.pos()
                    rect_after_animation = self.geometry()
                    
                # 强制设置窗口位置到目标位置（使用调整后的 target_y）
                self.setGeometry(target_x, target_y, window_width, window_height)
                self.move(target_x, target_y)
                
                # 标记窗口被隐藏
                self._was_hidden_to_icon = True
                self._hidden_rect = target_rect
                
                # 隐藏窗口
                self.hide()
                self.setVisible(False)
                
                # 重新启用位置检测
                if hasattr(self, '_position_track_timer'):
                    self._position_track_timer.start(50)
                # 窗口已经隐藏
                
                # 清理动画对象
                if hasattr(self, '_current_hide_pos_animation'):
                    del self._current_hide_pos_animation
            except Exception as e:
                import traceback
                traceback.print_exc()
            # 重置标志
            if hasattr(self, '_edge_triggered'):
                self._edge_triggered = False
        
        # 确保连接信号
        pos_animation.finished.connect(on_window_animation_finished)
        pos_animation.start()  # 比动画时长稍长一点
    
    def _init_transfer_manager(self):
        """初始化传输管理器（异步执行，避免阻塞UI）"""
        _debug_log("_init_transfer_manager called")
        def init_in_thread():
            """在后台线程中执行耗时操作"""
            try:
                _debug_log("Fetching user info for AirDrop...")
                api_client = ApiClient.from_config()
                user_info = api_client._get("/api/user_info")
                
                if isinstance(user_info, dict) and user_info.get("status") == "success":
                    data = user_info.get("data", {})
                    user_id = str(data.get("user_id", ""))
                    user_name = data.get("name", "Unknown")
                    avatar_url = data.get("avatar_url")
                    _debug_log(f"User info loaded: id={user_id}, name={user_name}")
                    
                    _debug_log("Queueing _create_transfer_manager on UI thread")
                    QMetaObject.invokeMethod(
                        self,
                        "_createTransferManagerSlot",
                        Qt.QueuedConnection,
                        Q_ARG(str, user_id),
                        Q_ARG(str, user_name),
                        Q_ARG(str, avatar_url or "")
                    )
                else:
                    _debug_log("User info response invalid, cannot start AirDrop")
                    def show_error():
                        Toast.show_message(self, "无法获取用户信息，请先登录")
                    QTimer.singleShot(0, show_error)
            except Exception as e:
                import sys
                logger.error(f"初始化传输管理器失败: {e}")
                _debug_log(f"init_in_thread exception: {e}")
                def show_error():
                    Toast.show_message(self, f"初始化失败: {e}")
                QTimer.singleShot(0, show_error)
        
        # 在后台线程中执行API调用
        import threading
        thread = threading.Thread(target=init_in_thread, daemon=True)
        thread.start()
    
    @Slot(str, str, str)
    def _createTransferManagerSlot(self, user_id: str, user_name: str, avatar_url: str):
        self._create_transfer_manager(user_id, user_name, avatar_url or None)

    def _create_transfer_manager(self, user_id: str, user_name: str, avatar_url: Optional[str]):
        """创建 TransferManager，并在后台启动服务，避免阻塞 UI"""
        try:
            _debug_log(f"Creating TransferManager instance (queued) for {user_id}")
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
            self._transfer_manager.receive_progress.connect(self._on_receive_progress)
            self._transfer_manager.transfer_completed.connect(self._on_transfer_completed)
            
            # 连接传输请求结果信号
            self.transfer_request_result.connect(self._on_transfer_request_result_signal)
            
            def start_manager():
                try:
                    self._transfer_manager.start()
                    QTimer.singleShot(0, self._on_transfer_manager_started)
                except Exception as exc:
                    logger.error(f"启动 TransferManager 失败: {exc}")
                    _debug_log(f"TransferManager.start() failed: {exc}")
                    QTimer.singleShot(0, lambda: Toast.show_message(self, f"初始化失败: {exc}"))
            
            threading.Thread(target=start_manager, daemon=True).start()
        except Exception as e:
            logger.error(f"创建传输管理器失败: {e}")
            _debug_log(f"_create_transfer_manager failed: {e}")
            Toast.show_message(self, f"初始化失败: {e}")

    def _on_transfer_manager_started(self):
        """TransferManager 启动完成后在主线程回调"""
        _debug_log("TransferManager.start() invoked from AirDropView")
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh_devices)
        self._refresh_timer.start(2000)
        _debug_log("AirDrop device refresh timer started (2s)")
    
    def _on_device_added(self, device: DeviceInfo):
        """设备添加"""
        _debug_log(f"[UI] Device discovered in AirDropView: {device.name} ({device.ip}) user_id={device.user_id}")
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget) and widget.device.user_id == device.user_id:
                return
        
        item = QListWidgetItem()
        widget = DeviceItemWidget(device)
        widget.file_dropped.connect(self._on_file_dropped)
        self.devices_list.addItem(item)
        self.devices_list.setItemWidget(item, widget)
        
        # 根据widget的sizeHint设置item大小，确保头像和文字完全显示
        size_hint = widget.sizeHint()
        if size_hint.isValid():
            item.setSizeHint(size_hint)
    
    def _on_device_removed(self, device_name: str):
        """设备移除"""
        _debug_log(f"[UI] Device removed from AirDropView: {device_name}")
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
        self._set_device_status(device, "等待中...", "#8E8E93")
        
        def send_in_thread():
            result = self._transfer_manager.send_transfer_request(file_path, device)
            
            if result["success"]:
                request_id = result["request_id"]
                self._wait_and_transfer(file_path, device, request_id)
            else:
                self._transferring = False
                self.status_label.setVisible(False)
                self._set_device_status(device, None)
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
                timeout=60
            )
            
            # 使用信号通知主线程（信号会自动在主线程中执行）
            self.transfer_request_result.emit(
                result,
                str(file_path),
                device.name,
                device.ip,
                device.port,
                request_id
            )
        
        import threading
        thread = threading.Thread(target=wait_in_thread, daemon=True)
        thread.start()
    
    def _on_transfer_request_result_signal(self, result: dict, file_path_str: str, device_name: str,
                                           device_ip: str, device_port: int, request_id: str):
        """处理传输请求结果信号（在主线程中执行）"""
        file_path = Path(file_path_str)
        # 重新构建 DeviceInfo
        device = DeviceInfo(
            name=device_name,
            user_id="",  # 不需要
            ip=device_ip,
            port=device_port
        )
        self._handle_transfer_request_result(result, file_path, device, request_id)

    def _handle_transfer_request_result(self, result: dict, file_path: Path, device: DeviceInfo, request_id: str):
        """在主线程处理传输请求结果"""
        if result.get("success") and result.get("accepted"):
            self._set_device_status(device, None)
            self._transfer_file(file_path, device, request_id)
            return
        
        self._transferring = False
        self.status_label.setVisible(False)
        self._set_device_status(device, "已拒绝", "#FF3B30")
        self._current_target = None
        if result.get("accepted") is False:
            Toast.show_message(self, f"{device.name} 拒绝了传输请求")
        else:
            Toast.show_message(self, "传输请求超时")
    
    def _transfer_file(self, file_path: Path, device: DeviceInfo, request_id: str):
        """传输文件"""
        self.status_label.setVisible(False)
        self._set_device_status(device, None)
        
        # 创建一个适配器函数，将 (uploaded, total) 转换为 (target_name, uploaded, total)
        def progress_adapter(uploaded: int, total: int):
            self._on_transfer_progress(device.name, uploaded, total)
        
        self._transfer_manager.send_file_after_confirm(
            file_path=file_path,
            target_device=device,
            request_id=request_id,
            on_progress=progress_adapter
        )
    
    def _on_transfer_request_received(self, request_id: str, sender_name: str, sender_id: str,
                                     filename: str, file_size: int, sender_ip: str = "", sender_port: int = 8765):
        """收到传输请求"""
        _debug_log(f"收到传输请求: request_id={request_id}, sender_ip={sender_ip}, sender_port={sender_port}")
        is_clipboard = filename.startswith('clipboard_') or filename.startswith('clipboard_image_')
        is_clipboard_image = self._is_clipboard_image_filename(filename)
        clipboard_image_format = self._extract_clipboard_image_format(filename) if is_clipboard_image else None
        is_clipboard_image_base64 = filename.endswith('.b64img')
        self._pending_requests[request_id] = {
            'sender_name': sender_name,
            'sender_id': sender_id,
            'filename': filename,
            'file_size': file_size,
            'sender_ip': sender_ip,
            'sender_port': sender_port,
            'accepted': False,
            'paste_to_clipboard': False,
            'is_clipboard': is_clipboard,
            'is_clipboard_image': is_clipboard_image,
            'clipboard_image_format': clipboard_image_format,
            'clipboard_image_base64': is_clipboard_image and is_clipboard_image_base64,
            'dialog': None,
            'auto_expired': False
        }
        self._schedule_request_expiration(request_id)
        
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
        filename = request_info['filename']
        
        # 检测是否是剪贴板内容（通过文件名判断）
        is_clipboard = request_info.get('is_clipboard', filename.startswith('clipboard_'))
        is_clipboard_image = request_info.get('is_clipboard_image', False)
        
        if is_clipboard:
            # 使用剪贴板接收对话框
            dialog = ClipboardReceiveDialog(
                sender_name=request_info['sender_name'],
                is_image=is_clipboard_image,
                parent=self
            )
            request_info['dialog'] = dialog
            dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            dialog.finished.connect(lambda _=None, rid=request_id: self._on_request_dialog_closed(rid))
            
            # 保存request_id和filename到对话框，以便后续使用
            dialog._request_id = request_id
            dialog._filename = filename
            
            def on_paste_to_clipboard():
                # 标记为已接受，等待文件接收
                if request_id in self._pending_requests:
                    self._pending_requests[request_id]['accepted'] = True
                    self._pending_requests[request_id]['paste_to_clipboard'] = True  # 标记为需要放入剪贴板
                
                # 更新服务器端状态
                if self._transfer_manager and self._transfer_manager._server:
                    sender_ip = request_info.get('sender_ip', '')
                    sender_port = request_info.get('sender_port', 8765)
                    if sender_ip and self._transfer_manager._server:
                        with self._transfer_manager._server._lock:
                            if request_id in self._transfer_manager._server._pending_requests:
                                self._transfer_manager._server._pending_requests[request_id]['status'] = 'accepted'
                            else:
                                self._transfer_manager._server._pending_requests[request_id] = {
                                    'status': 'accepted',
                                    'timestamp': time.time(),
                                    'sender_ip': sender_ip,
                                    'sender_port': sender_port,
                                    'filename': filename,
                                    'file_size': request_info.get('file_size', 0)
                                }
            
            def on_save_as_file():
                # 标记为已接受，等待文件接收
                if request_id in self._pending_requests:
                    self._pending_requests[request_id]['accepted'] = True
                    self._pending_requests[request_id]['paste_to_clipboard'] = False  # 标记为需要保存为文件
                
                # 更新服务器端状态
                if self._transfer_manager and self._transfer_manager._server:
                    sender_ip = request_info.get('sender_ip', '')
                    sender_port = request_info.get('sender_port', 8765)
                    if sender_ip and self._transfer_manager._server:
                        with self._transfer_manager._server._lock:
                            if request_id in self._transfer_manager._server._pending_requests:
                                self._transfer_manager._server._pending_requests[request_id]['status'] = 'accepted'
                            else:
                                self._transfer_manager._server._pending_requests[request_id] = {
                                    'status': 'accepted',
                                    'timestamp': time.time(),
                                    'sender_ip': sender_ip,
                                    'sender_port': sender_port,
                                    'filename': filename,
                                    'file_size': request_info.get('file_size', 0)
                                }
            
            def on_clipboard_rejected():
                auto_expired = False
                if request_id in self._pending_requests:
                    auto_expired = self._pending_requests[request_id].get('auto_expired', False)
                if self._transfer_manager and self._transfer_manager._server and not auto_expired:
                    self._transfer_manager._server.confirm_transfer(request_id, False)
                if request_id in self._pending_requests:
                    del self._pending_requests[request_id]
                if not auto_expired:
                    Toast.show_message(self, "已拒绝传输请求")
            
            dialog.paste_to_clipboard.connect(on_paste_to_clipboard)
            if not is_clipboard_image:
                dialog.save_as_file.connect(on_save_as_file)
            dialog.rejected.connect(on_clipboard_rejected)
            return
        
        # 普通文件传输对话框
        dialog = TransferConfirmDialog(
            sender_name=request_info['sender_name'],
            filename=request_info['filename'],
            file_size=request_info['file_size'],
            parent=self
        )
        request_info['dialog'] = dialog
        
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.finished.connect(lambda _=None, rid=request_id: self._on_request_dialog_closed(rid))
        
        def on_accepted():
            try:
                if not self._transfer_manager:
                    _debug_log(f"TransferManager 未初始化")
                    Toast.show_message(self, "传输服务未初始化")
                    return
                
                # 直接从UI层的_pending_requests获取请求信息（包含sender_ip和sender_port）
                if request_id not in self._pending_requests:
                    # 检查是否已经接受过（可能已经被延迟删除）
                    # 尝试从服务器端获取信息
                    if self._transfer_manager and self._transfer_manager._server:
                        with self._transfer_manager._server._lock:
                            server_request = self._transfer_manager._server._pending_requests.get(request_id)
                            if server_request and server_request.get('status') == 'accepted':
                                # 请求已经被接受，正在传输中，不显示提示
                                return
                    Toast.show_message(self, "请求不存在，请让发送方重新发送")
                    return
                
                # 检查是否已经接受过
                request_info = self._pending_requests[request_id]
                if request_info.get('accepted', False):
                    # 已接受，不显示提示
                    return
                
                request_info = self._pending_requests[request_id]
                sender_ip = request_info.get('sender_ip', '')
                sender_port = request_info.get('sender_port', 8765)
                
                if not sender_ip:
                    Toast.show_message(self, "无法获取发送端信息，请让发送方重新发送")
                    return
                
                # 先尝试在服务器端确认请求状态（如果服务器端还有这个请求）
                # 注意：即使服务器端没有请求，我们仍然可以通知发送端接受
                if self._transfer_manager._server:
                    # 检查服务器端是否有这个请求
                    with self._transfer_manager._server._lock:
                        if request_id in self._transfer_manager._server._pending_requests:
                            self._transfer_manager._server._pending_requests[request_id]['status'] = 'accepted'
                        else:
                            # 尝试在服务器端重新创建请求记录（用于后续文件上传）
                            self._transfer_manager._server._pending_requests[request_id] = {
                                'status': 'accepted',
                                'timestamp': time.time(),
                                'sender_ip': sender_ip,
                                'sender_port': sender_port,
                                'filename': request_info.get('filename', 'unknown'),
                                'file_size': request_info.get('file_size', 0)
                            }
                
                # 注意：不需要调用 accept_transfer 通知发送端
                # 因为发送端已经在轮询接收端的 /transfer_status 接口
                # 接收端只需要更新自己的状态为 accepted，发送端轮询时就能看到 accepted 状态
                # 不显示"已接受"提示，直接开始传输
                
                # 接受后，标记为已接受，但不要删除请求
                # 请求将在文件接收完成时（_on_file_received）删除
                # 这样可以确保进度更新时能找到请求信息
                if request_id in self._pending_requests:
                    self._pending_requests[request_id]['accepted'] = True
            except Exception as e:
                import traceback
                traceback.print_exc()
                Toast.show_message(self, f"接受请求失败: {e}")
        
        def on_rejected():
            # 更新服务器端的请求状态为 rejected
            # 发送端会通过轮询 /transfer_status 接口来获取这个状态
            # 不需要调用 reject_transfer 向发送端发送通知，因为发送端没有服务器来接收
            auto_expired = False
            if request_id in self._pending_requests:
                auto_expired = self._pending_requests[request_id].get('auto_expired', False)
            if self._transfer_manager and self._transfer_manager._server and not auto_expired:
                self._transfer_manager._server.confirm_transfer(request_id, False)
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]
            if not auto_expired:
                Toast.show_message(self, "已拒绝传输请求")
        
        # 注意：TransferConfirmDialog 定义了自定义的 accepted/rejected 信号
        # 需要直接连接，而不是使用 QDialog 的 accepted/rejected 信号
        dialog.accepted.connect(on_accepted)  # 这是自定义信号
        dialog.rejected.connect(on_rejected)  # 这是自定义信号
    
    def _cleanup_accepted_request(self, request_id: str):
        """清理已接受的请求（在文件接收完成时调用）"""
        if request_id in self._pending_requests:
            del self._pending_requests[request_id]
            logger.debug(f"已从UI层删除请求: {request_id}")
    
    def _on_transfer_progress(self, target_name: str, uploaded: int, total: int):
        """传输进度更新"""
        if self._current_target and target_name == self._current_target.name:
            progress = int((uploaded / total) * 100) if total > 0 else 0
            self.status_label.setVisible(False)
            # 更新设备项的头像进度条
            for i in range(self.devices_list.count()):
                item = self.devices_list.item(i)
                widget = self.devices_list.itemWidget(item)
                if isinstance(widget, DeviceItemWidget) and widget.device.name == target_name:
                    widget.set_progress(progress)
                    break
    
    def _on_transfer_completed(self, target_name: str, success: bool, message: str):
        """传输完成"""
        self._transferring = False
        
        self.status_label.setVisible(False)
        
        # 清除设备项的头像进度条
        current_device = self._current_target
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget) and widget.device.name == target_name:
                widget.set_progress(0)
                widget.set_device_status(None)
                break
        
        if success:
            Toast.show_message(self, f"文件已成功发送到 {target_name}")
        else:
            Toast.show_message(self, f"发送失败: {message}")
        
        self._current_target = None
    
    def _on_receive_progress(self, request_id: str, received: int, total: int):
        """接收进度更新"""
        # 从待处理请求中获取发送者信息
        if request_id in self._pending_requests:
            sender_name = self._pending_requests[request_id].get('sender_name', '未知')
            filename = self._pending_requests[request_id].get('filename', '未知文件')
            
            progress = int((received / total) * 100) if total > 0 else 0
            self.status_label.setVisible(False)
            
            # 更新设备项的头像进度条（如果有对应的设备）
            sender_id = self._pending_requests[request_id].get('sender_id', '')
            for i in range(self.devices_list.count()):
                item = self.devices_list.item(i)
                widget = self.devices_list.itemWidget(item)
                if isinstance(widget, DeviceItemWidget) and widget.device.user_id == sender_id:
                    widget.set_progress(progress)
                    break
        else:
            # 如果请求不在_pending_requests中，使用默认值继续更新进度
            progress = int((received / total) * 100) if total > 0 else 0
            self.status_label.setVisible(False)
    
    def _on_file_received(self, save_path: Path, file_size: int, original_filename: str):
        """文件接收完成"""
        # 隐藏状态
        self.status_label.setVisible(False)
        
        # 清理所有已接受的请求（通过匹配文件名和文件大小）
        # 因为_on_file_received没有request_id，需要通过文件名和大小匹配
        request_ids_to_remove = []
        sender_ids_to_reset = set()
        paste_to_clipboard = False
        is_clipboard_request = False
        is_clipboard_image = False
        clipboard_image_format = None
        for req_id, req_info in self._pending_requests.items():
            if (req_info.get('filename') == original_filename and 
                req_info.get('accepted', False) and
                req_info.get('file_size', 0) == file_size):
                request_ids_to_remove.append(req_id)
                sender_id = req_info.get('sender_id')
                if sender_id:
                    sender_ids_to_reset.add(sender_id)
                if req_info.get('paste_to_clipboard', False):
                    paste_to_clipboard = True
                if req_info.get('is_clipboard', False):
                    is_clipboard_request = True
                if req_info.get('is_clipboard_image', False):
                    is_clipboard_image = True
                    clipboard_image_format = clipboard_image_format or req_info.get('clipboard_image_format')
        
        message_shown = False
        clipboard_image_base64 = clipboard_image_format is not None and original_filename.endswith('.b64img')
        if is_clipboard_request:
            if is_clipboard_image and not clipboard_image_base64:
                if paste_to_clipboard:
                    image = QImage(str(save_path))
                    if not image.isNull():
                        QApplication.clipboard().setImage(image)
                        Toast.show_message(self, "图片已复制到剪贴板")
                        message_shown = True
                        with contextlib.suppress(OSError):
                            save_path.unlink()
                else:
                    try:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(save_path)))
                    except Exception:
                        pass
                    Toast.show_message(self, f"图片已保存并打开: {save_path.name}")
                    message_shown = True
            else:
                try:
                    content = save_path.read_text(encoding='utf-8')
                except Exception as e:
                    logger.error(f"读取剪贴板文件失败: {e}")
                    Toast.show_message(self, f"无法读取剪贴板内容，已保存为: {save_path.name}")
                    message_shown = True
                    content = ""
                else:
                    if paste_to_clipboard:
                        if is_clipboard_image:
                            if self._copy_image_to_clipboard_from_base64(content, clipboard_image_format):
                                Toast.show_message(self, "图片已复制到剪贴板")
                                message_shown = True
                                with contextlib.suppress(OSError):
                                    save_path.unlink()
                            else:
                                Toast.show_message(self, "图片解析失败，已保存为文本文件")
                                message_shown = True
                        else:
                            clipboard = QApplication.clipboard()
                            clipboard.setText(content)
                            with contextlib.suppress(OSError):
                                save_path.unlink()
                            Toast.show_message(self, "文本已复制到剪贴板")
                            message_shown = True
                    else:
                        if is_clipboard_image:
                            image_path = self._save_image_from_base64(content, clipboard_image_format, save_path.parent)
                            if image_path:
                                try:
                                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(image_path)))
                                except Exception:
                                    pass
                                with contextlib.suppress(OSError):
                                    save_path.unlink()
                                Toast.show_message(self, f"图片已保存并打开: {image_path.name}")
                                message_shown = True
                            else:
                                Toast.show_message(self, f"图片解析失败，原文件保存在: {save_path}")
                                message_shown = True
                        else:
                            size_str = self._format_file_size(file_size)
                            Toast.show_message(
                                self,
                                f"收到剪贴板文本: {original_filename} ({size_str})\n已保存到: {save_path.parent}"
                            )
                            message_shown = True
        
        if not message_shown:
            size_str = self._format_file_size(file_size)
            Toast.show_message(
                self,
                f"收到文件: {original_filename} ({size_str})\n保存位置: {save_path.parent}"
            )
        
        for req_id in request_ids_to_remove:
            del self._pending_requests[req_id]
        
        self._reset_device_progress(sender_ids_to_reset)
    
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
    
    def _reset_device_progress(self, user_ids: Optional[Set[str]] = None):
        """根据 user_id 重置设备头像进度"""
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if not isinstance(widget, DeviceItemWidget):
                continue
            if not user_ids or widget.device.user_id in user_ids:
                widget.set_progress(0)
                if user_ids:
                    widget.set_device_status(None)

    def _set_device_status(self, device: Optional[DeviceInfo], text: Optional[str], color: Optional[str] = None):
        """更新指定设备的状态文本"""
        if not device:
            return
        target_id = getattr(device, "user_id", "") or ""
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if not isinstance(widget, DeviceItemWidget):
                continue
            same_device = False
            if target_id and widget.device.user_id == target_id:
                same_device = True
            elif widget.device.ip == device.ip and widget.device.name == device.name:
                same_device = True
            if same_device:
                widget.set_device_status(text, color)
                break

    def _schedule_request_expiration(self, request_id: str):
        """超过1分钟未接受自动移除请求"""
        def expire():
            request = self._pending_requests.get(request_id)
            if not request or request.get('accepted'):
                return
            request['auto_expired'] = True
            dialog = request.get('dialog')
            if dialog:
                dialog.reject()
            else:
                if self._transfer_manager and self._transfer_manager._server:
                    self._transfer_manager._server.confirm_transfer(request_id, False)
                self._pending_requests.pop(request_id, None)
        QTimer.singleShot(60_000, expire)

    def _on_request_dialog_closed(self, request_id: str):
        """对话框关闭时清理引用"""
        if request_id in self._pending_requests:
            self._pending_requests[request_id].pop('dialog', None)

    @staticmethod
    def _is_clipboard_image_filename(filename: str) -> bool:
        name = Path(filename).name
        return name.startswith("clipboard_image_") or name.startswith("clipboard_img-")

    @staticmethod
    def _extract_clipboard_image_format(filename: str) -> Optional[str]:
        name = Path(filename).name
        prefix_modern = "clipboard_image_"
        prefix_legacy = "clipboard_img-"
        remainder = None
        if name.startswith(prefix_modern):
            remainder = name[len(prefix_modern):]
        elif name.startswith(prefix_legacy):
            remainder = name[len(prefix_legacy):]
        else:
            return None
        if '-' in remainder:
            format_part = remainder.split('-', 1)[0]
            if format_part:
                return format_part
        # fallback: use file suffix
        suffix = Path(name).suffix.lstrip('.')
        return suffix or None

    @staticmethod
    def _parse_base64_image_text(text: str, suggested_format: Optional[str] = None) -> Tuple[Optional[bytes], Optional[str]]:
        if not text:
            return None, None
        data = text.strip()
        base64_data = data
        image_format = suggested_format
        if data.startswith("data:image/") and "," in data:
            header, _, base64_data = data.partition(',')
            try:
                image_format = header.split('/')[1].split(';')[0]
            except IndexError:
                pass
        base64_data = ''.join(base64_data.split())
        try:
            decoded = base64.b64decode(base64_data, validate=True)
        except Exception:
            return None, None
        detected = imghdr.what(None, decoded)
        if detected:
            image_format = detected
        return decoded, image_format

    def _copy_image_to_clipboard_from_base64(self, text: str, suggested_format: Optional[str]) -> bool:
        data, _ = self._parse_base64_image_text(text, suggested_format)
        if not data:
            return False
        image = QImage()
        if not image.loadFromData(data):
            return False
        clipboard = QApplication.clipboard()
        clipboard.setImage(image)
        return True

    def _save_image_from_base64(self, text: str, suggested_format: Optional[str], target_dir: Path) -> Optional[Path]:
        data, image_format = self._parse_base64_image_text(text, suggested_format)
        if not data:
            return None
        ext = image_format or 'png'
        timestamp = int(time.time())
        file_path = target_dir / f"clipboard_image_{timestamp}.{ext}"
        try:
            with open(file_path, 'wb') as f:
                f.write(data)
        except Exception as e:
            logger.error(f"保存base64图片失败: {e}")
            return None
        return file_path
    
    def closeEvent(self, event):
        """关闭事件"""
        # 注意：这个closeEvent会被main_window中的custom_close_event重写
        # 所以这里只处理传输管理器的停止
        if self._transfer_manager:
            self._transfer_manager.stop()
        super().closeEvent(event)
