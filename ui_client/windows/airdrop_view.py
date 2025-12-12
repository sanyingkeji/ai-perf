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
import queue
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, Set
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QMessageBox, QGraphicsOpacityEffect,
    QApplication,
    QMenu, QFileDialog, QScrollArea, QSizePolicy, QSpacerItem
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
    QPen,
    QIcon,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QCursor,
    QContextMenuEvent,
    QClipboard,
    QDesktopServices,
    QImage,
    QGuiApplication,
)
try:
    from shiboken6 import isValid as _qt_is_valid
except Exception:  # pragma: no cover
    _qt_is_valid = None
import httpx
import logging
import sys

from utils.lan_transfer.manager import TransferManager
from utils.lan_transfer.discovery import DeviceInfo
from utils.api_client import ApiClient
from widgets.toast import Toast
from utils.notification import send_notification
from utils.theme_manager import ThemeManager
from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class NonScrollListWidget(QListWidget):
    """禁用自身滚动，交由外层 QScrollArea 接管。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # wheel 事件通常落在 viewport 上，安装事件过滤器以便转发
        if self.viewport():
            self.viewport().installEventFilter(self)

    def _forward_wheel_to_scroll_area(self, event):
        """将滚轮事件转发给最近的 QScrollArea。"""
        from PySide6.QtWidgets import QScrollArea
        parent = self.parent()
        scroll_area = None
        while parent is not None:
            if isinstance(parent, QScrollArea):
                scroll_area = parent
                break
            parent = parent.parent()
        if scroll_area and scroll_area.viewport():
            from PySide6.QtGui import QWheelEvent
            # 构造新的 wheel 事件发给 scroll_area 的 viewport
            forwarded = QWheelEvent(
                scroll_area.viewport().mapFromGlobal(event.globalPosition().toPoint()),
                event.globalPosition(),
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
                event.source()
            )
            from PySide6.QtWidgets import QApplication
            QApplication.sendEvent(scroll_area.viewport(), forwarded)
            return True
        return False

    def wheelEvent(self, event):
        if not self._forward_wheel_to_scroll_area(event):
            event.ignore()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.viewport() and event.type() == QEvent.Type.Wheel:
            if self._forward_wheel_to_scroll_area(event):
                return True
        return super().eventFilter(obj, event)


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
    
    # Windows/Qt6：避免在 Signal 签名里使用非 Qt 元类型（Path/自定义 dataclass），否则在跨线程/排队投递时可能触发原生崩溃
    file_dropped = Signal(object, object)  # (file_path: Path, device: DeviceInfo)
    
    def __init__(self, device: DeviceInfo, parent=None):
        super().__init__(parent)
        self._device = device
        self._progress = 0
        self._parent_airdrop_view = None  # 用于获取主题颜色
        self._is_dark = False  # 主题状态
        self._device_text_max_px = 160  # 设备名单行显示的最大像素宽度（超出中间省略）
        if parent:
            # 向上查找 AirDropView
            widget = parent
            while widget:
                if isinstance(widget, AirDropView):
                    self._parent_airdrop_view = widget
                    self._is_dark = widget._is_dark
                    break
                widget = widget.parent()
        # 后台下载头像后，使用 invokeMethod 在 UI 线程应用（避免从 Python Thread 里 emit Qt signal 导致 Qt6Core.dll 0xc0000005）
        self._pending_avatar_bytes: Optional[bytes] = None
        self._avatar_apply_scheduled: bool = False

        self._setup_ui()
        self.setAcceptDrops(True)
    
    def sizeHint(self) -> QSize:
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
        # 宽度将由 _update_item_widths() 动态设置，这里只计算最小宽度
        # 左右内边距各0px（已改为0），所以最小宽度 = 内容宽度
        w: int = int(max(110, content_width))
        # 调整高度计算：上内边距6px，下内边距4px，间距2+1=3px，头像72px
        h: int = int(6 + 72 + 2 + name_height + 1 + device_height + 4)  # 上边距+头像+间距+名字+间距+设备名+下边距
        h = max(118, h)
        # 使用与构造函数参数名匹配的变量名，并明确类型
        return QSize(w, h)
    
    def _setup_ui(self):
        self._avatar_size = 64
        # 确保布局方向是从上到下
        self.setLayoutDirection(Qt.LeftToRight)
        
        layout = QVBoxLayout(self)
        # 调整内边距，特别是减小底部内边距
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignCenter)

        # 宽度将在窗口大小确定后动态设置（窗口宽度的1/4，考虑滚动条）
        # PySide6 6.5 兼容：分别设置宽度和高度
        self.setMinimumHeight(118)
        
        # 确保 widget 在 item 中水平居中
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

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
        # PySide6 6.5 兼容：使用 setter 方式避免静态检查误报
        name_font = QFont()
        name_font.setFamily("SF Pro Display")
        name_font.setPointSize(12)
        name_font.setWeight(QFont.Weight.Medium)
        self.name_label.setFont(name_font)
        self.name_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(self.name_label, alignment=Qt.AlignCenter)
        layout.addSpacing(1)  # 名字和设备名之间的间距
        
        # 第三步：添加设备名（最下方，第三行）
        device_text = self._device.device_name or self._device.ip
        self._default_device_text = device_text
        self.device_label = QLabel(device_text)
        self.device_label.setAlignment(Qt.AlignCenter)
        # 限制设备名单行显示并中间省略，避免撑高/换行
        self.device_label.setWordWrap(False)
        if self._device_text_max_px:
            fm = self.device_label.fontMetrics()
            elided = fm.elidedText(device_text, Qt.ElideMiddle, self._device_text_max_px)
            self.device_label.setText(elided)
            self.device_label.setMaximumWidth(self._device_text_max_px)
            self.device_label.setMinimumWidth(0)
        device_font = QFont()
        device_font.setFamily("SF Pro Display")
        device_font.setPointSize(9)
        self.device_label.setFont(device_font)
        # 使用主题颜色
        colors = self._get_theme_colors()
        self._default_device_style = f"color: {colors['text_tertiary']}; font-size: 10px; padding-top: 0px; background-color: transparent;"
        self.device_label.setStyleSheet(self._default_device_style)
        layout.addWidget(self.device_label, alignment=Qt.AlignCenter)
        # 移除 addStretch()，减小底部内边距
    
    def _get_theme_colors(self) -> dict:
        """获取主题颜色（从父窗口或使用默认值）"""
        if self._parent_airdrop_view and hasattr(self._parent_airdrop_view, '_get_theme_colors'):
            return self._parent_airdrop_view._get_theme_colors()
        # 默认使用亮色主题
        return {
            "text_primary": "#000000",
            "text_secondary": "#111111",
            "text_tertiary": "#999999",
            "avatar_bg": "#E5E5EA",
        }
    
    def _update_theme_colors(self, colors: dict):
        """更新主题颜色"""
        self.name_label.setStyleSheet(f"background-color: transparent; color: {colors['text_primary']};")
        self._default_device_style = f"color: {colors['text_tertiary']}; font-size: 10px; padding-top: 0px; background-color: transparent;"
        if self.device_label.text() == self._default_device_text:
            self.device_label.setStyleSheet(self._default_device_style)
        # 重新绘制默认头像（如果有）
        if not self.avatar_label.pixmap() or not self.avatar_label.pixmap().isNull():
            # 检查是否是默认头像（通过检查是否有图片URL）
            if not hasattr(self._device, 'avatar_url') or not self._device.avatar_url:
                self._set_default_avatar()

    def set_device_status(self, text: Optional[str], color: Optional[str] = None):
        """更新设备名区域的状态文本"""
        if text:
            if color is None:
                colors = self._get_theme_colors()
                color = colors['text_tertiary']
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
                border-radius: 0;
                background-color: rgba(0, 122, 255, 0.2);
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
        # 先设置默认占位，确保 UI 有内容；异步失败也不在后台线程触碰 UI
        self._set_default_avatar()
        if self._device.avatar_url:
            self._load_avatar_async(self._device.avatar_url)
    
    def _load_avatar_async(self, url: str):
        """
        异步加载头像（后台线程只做网络请求，不创建/绘制 QPixmap）。

        说明：QPixmap/QPainter 不是线程安全的，尤其在 Windows/Qt6 上会引发 0xc0000005。
        """
        def load():
            max_retries = 3
            retry_delay = 1  # 秒

            for attempt in range(max_retries):
                try:
                    response = httpx.get(url, timeout=5)
                    if response.status_code == 200 and response.content:
                        data = bytes(response.content)
                        self._post_avatar_bytes_to_ui(data)
                        return
                except Exception as e:
                    logger.warning(f"加载头像失败 (尝试 {attempt + 1}/{max_retries}): {e}")

                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避

            # 全部失败：保留默认占位，不在后台线程做任何 UI 操作
            return

        thread = threading.Thread(target=load, daemon=True)
        thread.start()

    def _post_avatar_bytes_to_ui(self, data: bytes) -> None:
        """
        后台线程调用：把头像 bytes 投递到 UI 线程应用。

        重要：Win11/Qt6 下避免从 Python Thread 里调用任何 Qt API（包括 invokeMethod/emit signal）。
        这里仅通过父窗口的 Python 队列投递，UI 线程定时 drain 执行。
        """
        try:
            parent_view = getattr(self, "_parent_airdrop_view", None)
            if parent_view and hasattr(parent_view, "_post_to_ui_thread"):
                parent_view._post_to_ui_thread(lambda raw=data: self._apply_avatar_bytes(raw))
        except Exception:
            pass

    def _apply_avatar_bytes(self, raw: bytes) -> None:
        """UI 线程：将头像 bytes 转成 pixmap 并更新头像。"""
        try:
            if not raw:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(raw):
                return
            container_size = getattr(self, "_avatar_size", 64) + 8
            circular_pixmap = self._make_circular(pixmap, container_size)
            self.avatar_label.setPixmap(circular_pixmap)
        except Exception:
            # 静默失败，避免影响主流程
            pass

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
        # 获取主题颜色
        colors = self._get_theme_colors()
        # 解析头像背景色
        bg_color_str = colors.get('avatar_bg', '#E5E5EA')
        if bg_color_str.startswith('#'):
            bg_color = QColor(bg_color_str)
        else:
            bg_color = QColor(142, 142, 147)  # 默认灰色
        
        # 文字颜色：深色主题用白色，亮色主题用深色
        text_color = QColor(255, 255, 255) if self._is_dark else QColor(0, 0, 0)
        
        # 容器大小是avatar_size+8，pixmap大小要和容器一致，确保内外层尺寸对齐
        container_size = self._avatar_size + 8
        pixmap = QPixmap(container_size, container_size)
        pixmap.fill(Qt.transparent)  # 透明背景
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        # 绘制圆形，在容器中心，半径为avatar_size/2
        center = container_size // 2
        radius = self._avatar_size // 2
        painter.drawEllipse(center - radius, center - radius, self._avatar_size, self._avatar_size)
        
        painter.setPen(text_color)
        # PySide6 6.5 兼容：使用 setter 方式避免静态检查误报
        avatar_font = QFont()
        avatar_font.setFamily("SF Pro Display")
        avatar_font.setPointSize(32)
        avatar_font.setWeight(QFont.Weight.Medium)
        painter.setFont(avatar_font)
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


class TrianglePointer(QWidget):
    """倒三角指向头像"""
    
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self._border_color = None  # 边框颜色
        self.setFixedSize(18, 10)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
    
    def set_border_color(self, color: QColor):
        """设置边框颜色"""
        self._border_color = color
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        points = [
            QPoint(0, 0),
            QPoint(w, 0),
            QPoint(w // 2, h),
        ]
        
        # 绘制主体
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._color))
        painter.drawPolygon(points)
        
        # 绘制边框，仅两侧（顶部不描边，贴合气泡主体）
        if self._border_color:
            pen = QPen(self._border_color)
            pen.setWidth(1)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(points[0], points[2])
            painter.drawLine(points[1], points[2])
        painter.end()


class TransferRequestBubble(QWidget):
    """悬浮在头像附近的传输请求气泡（文件/剪贴板共用）"""
    
    accepted = Signal()          # 文件接受 / 剪贴板放入剪贴板
    accepted_open = Signal()     # 文件“接受并打开”
    save_as_file = Signal()      # 剪贴板“另存为TXT”
    rejected = Signal()
    
    def __init__(self, sender_name: str, filename: str, file_size: int, parent=None,
                 is_clipboard: bool = False, is_clipboard_image: bool = False):
        super().__init__(parent)
        self._sender_name = sender_name
        self._filename = filename
        self._file_size = file_size
        self._is_clipboard = is_clipboard
        self._is_clipboard_image = is_clipboard_image
        self._size_locked = False
        self._last_screen_name = None
        self._pointer_visible = True
        self._is_dark = self._detect_theme()
        self._setup_ui()
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False
    
    def _get_theme_colors(self) -> dict:
        """获取主题颜色"""
        if self._is_dark:
            return {
                "bg_primary": "#1C1C1E",
                "text_primary": "#FFFFFF",
                "text_secondary": "#EBEBF5",
                "text_tertiary": "#9a9ab1",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#2C2C2E",
                "button_secondary_border": "#38383A",
                "button_secondary_hover": "#3A3A3C",
                "button_secondary_pressed": "#48484A",
                "bg_alpha": 0.95,
                "border_alpha": 1.0,
                "bg_rgb": "28, 28, 30",
                "border_rgb": "50, 50, 52",
                "link": "#0A84FF",
            }
        else:
            return {
                "bg_primary": "#FFFFFF",
                "text_primary": "#000000",
                "text_secondary": "#111111",
                "text_tertiary": "#8E8E93",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#F2F2F7",
                "button_secondary_border": "#D1D1D6",
                "button_secondary_hover": "#E5E5EA",
                "button_secondary_pressed": "#D8D8DC",
                "bg_alpha": 0.96,
                "border_alpha": 0.1,
                "bg_rgb": "255, 255, 255",
                "border_rgb": "0, 0, 0",
                "link": "#0A84FF",
            }
    
    def _setup_ui(self, colors: dict = None):
        if colors is None:
            colors = self._get_theme_colors()
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        
        container = QFrame()
        container.setObjectName("bubbleFrame")
        container.setStyleSheet(f"""
            QFrame#bubbleFrame {{
                background: rgba({colors['bg_rgb']}, {colors['bg_alpha']});
                border-radius: 12px;
                border: 1px solid rgba({colors['border_rgb']}, {colors['border_alpha']});
            }}
            QLabel {{
                color: {colors['text_primary']};
            }}
        """)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(10)
        container.setLayout(container_layout)
        
        if self._is_clipboard:
            title = QLabel(f"“{self._sender_name}”想向你发送剪贴板内容。")
        else:
            title = QLabel(f"“{self._sender_name}”想向你发送“{self._filename}”。")
        title.setWordWrap(True)
        colors = self._get_theme_colors()
        title.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {colors['text_secondary']}; background-color: transparent;")
        container_layout.addWidget(title)
        
        if self._is_clipboard and self._is_clipboard_image:
            size_label = QLabel("这是一张图片")
        else:
            size_str = self._format_file_size(self._file_size)
            size_label = QLabel(size_str)
        size_label.setStyleSheet(f"font-size: 10px; color: {colors['text_tertiary']}; background-color: transparent;")
        container_layout.addWidget(size_label)
        
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(12)
        
        # 获取父窗口的主题颜色
        colors = self._get_theme_colors()
        
        if self._is_clipboard:
            if self._is_clipboard_image:
                # 剪贴板图片：接受并打开(主按钮蓝色) + 拒绝
                accept_open_btn = QPushButton("接受并打开")
                accept_open_btn.setFixedHeight(30)
                accept_open_btn.setMinimumWidth(90)
                accept_open_btn.setStyleSheet(self._primary_button_style(colors))
                accept_open_btn.clicked.connect(self.accepted_open.emit)
                btn_layout.addWidget(accept_open_btn, 1)
                
                reject_btn = QPushButton("拒绝")
                reject_btn.setFixedHeight(30)
                reject_btn.setMinimumWidth(48)
                reject_btn.setStyleSheet(self._secondary_button_style(colors))
                reject_btn.clicked.connect(lambda: self.rejected.emit())
                btn_layout.addWidget(reject_btn, 1)
            else:
                # 剪贴板文本：放入剪贴板 / 另存为TXT / 拒绝
                save_btn = QPushButton("另存为.txt文件")
                save_btn.setFixedHeight(30)
                save_btn.setMinimumWidth(110)
                save_btn.setStyleSheet(self._secondary_button_style(colors))
                save_btn.clicked.connect(self.save_as_file.emit)
                btn_layout.addWidget(save_btn, 1)
                
                reject_btn = QPushButton("拒绝")
                reject_btn.setFixedHeight(30)
                reject_btn.setMinimumWidth(48)
                reject_btn.setStyleSheet(self._secondary_button_style(colors))
                reject_btn.clicked.connect(lambda: self.rejected.emit())
                btn_layout.addWidget(reject_btn, 1)
                
                accept_btn = QPushButton("放入剪贴板")
                accept_btn.setFixedHeight(30)
                accept_btn.setMinimumWidth(90)
                accept_btn.setStyleSheet(self._primary_button_style(colors))
                accept_btn.clicked.connect(self.accepted.emit)
                btn_layout.addWidget(accept_btn, 1)
        else:
            # 只有当文件可以在当前系统打开时才显示"接受并打开"按钮
            if self._can_open_file():
                accept_open_btn = QPushButton("接受并打开")
                accept_open_btn.setFixedHeight(30)
                accept_open_btn.setMinimumWidth(90)
                accept_open_btn.setStyleSheet(self._secondary_button_style(colors))
                accept_open_btn.clicked.connect(self.accepted_open.emit)
                btn_layout.addWidget(accept_open_btn, 1)
            
            reject_btn = QPushButton("拒绝")
            reject_btn.setFixedHeight(30)
            reject_btn.setMinimumWidth(48)
            reject_btn.setStyleSheet(self._secondary_button_style(colors))
            reject_btn.clicked.connect(lambda: self.rejected.emit())
            btn_layout.addWidget(reject_btn, 1)
            
            accept_btn = QPushButton("接受")
            accept_btn.setFixedHeight(30)
            accept_btn.setMinimumWidth(48)
            accept_btn.setStyleSheet(self._primary_button_style(colors))
            accept_btn.clicked.connect(self.accepted.emit)
            btn_layout.addWidget(accept_btn, 1)
        
        container_layout.addLayout(btn_layout)
        outer_layout.addWidget(container, 0, Qt.AlignTop)
        
        # 指针（独立定位）
        self._container = container
        self._pointer_overlap = 1  # 覆盖气泡边框的像素
        pointer_color = QColor(255, 255, 255, 245) if not self._is_dark else QColor(28, 28, 30, 245)
        border_color = QColor(0, 0, 0, 20) if not self._is_dark else QColor(50, 50, 52, 255)
        self._pointer = TrianglePointer(pointer_color, self)
        self._pointer.set_border_color(border_color)
        
        spacer = QSpacerItem(0, max(0, self._pointer.height() - self._pointer_overlap), QSizePolicy.Minimum, QSizePolicy.Fixed)
        outer_layout.addSpacerItem(spacer)
        QTimer.singleShot(0, self._position_pointer)
    
    def lock_size_for_screen(self, screen):
        """根据屏幕锁定尺寸，切换屏幕后重新计算一次"""
        name = screen.name() if screen else None
        if self._size_locked and name == self._last_screen_name:
            return
        self._last_screen_name = name
        self._size_locked = False
        self.adjustSize()
        self.setFixedSize(self.size())
        self._size_locked = True
    
    def set_pointer_visible(self, visible: bool):
        self._pointer_visible = visible
        self._pointer.setVisible(visible)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_pointer()
    
    def _position_pointer(self):
        """将指针定位到容器底部中央，并覆盖边框"""
        try:
            if not hasattr(self, "_container") or not self._container:
                return
            cgeom = self._container.geometry()
            pw, ph = self._pointer.width(), self._pointer.height()
            overlap = getattr(self, "_pointer_overlap", 1)
            x = cgeom.x() + (cgeom.width() - pw) // 2
            y = cgeom.y() + cgeom.height() - overlap
            self._pointer.move(x, y)
            self._pointer.raise_()
        except Exception:
            pass
    
    def _can_open_file(self) -> bool:
        """判断文件是否可以在当前操作系统上打开（仅考虑系统自带支持）"""
        if "." not in self._filename:
            return False
        
        ext = self._filename.rsplit(".", 1)[-1].lower()
        import platform
        system = platform.system()
        
        # macOS 系统自带支持的文件类型
        if system == "Darwin":
            # 不支持的类型（其他系统的安装包）
            unsupported = {"exe", "msi", "deb", "rpm"}
            if ext in unsupported:
                return False
            
            # 支持的类型
            supported = {
                # 图片
                "jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "bmp", "tiff", "tif",
                # 视频
                "mp4", "mov", "m4v", "avi", "mkv", "webm",
                # 音频
                "mp3", "aac", "m4a", "wav", "flac", "ogg",
                # 文档
                "pdf", "txt", "rtf", "pages", "numbers", "key", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                # 压缩
                "zip", "tar", "gz", "bz2", "xz",
                # 安装包
                "dmg", "pkg", "app"
            }
            return ext in supported
        
        # Windows 系统自带支持的文件类型
        elif system == "Windows":
            # 不支持的类型（其他系统的安装包）
            unsupported = {"dmg", "pkg", "app", "deb", "rpm"}
            if ext in unsupported:
                return False
            
            # 支持的类型
            supported = {
                # 图片
                "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp",
                # 视频
                "mp4", "avi", "wmv", "mov", "mkv", "webm",
                # 音频
                "mp3", "wav", "aac", "m4a", "flac",
                # 文档
                "pdf", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf",
                # 压缩
                "zip",
                # 安装包
                "exe", "msi"
            }
            return ext in supported
        
        # Linux 系统自带支持的文件类型
        elif system == "Linux":
            # 不支持的类型（其他系统的安装包）
            unsupported = {"exe", "msi", "dmg", "pkg", "app"}
            if ext in unsupported:
                return False
            
            # 支持的类型
            supported = {
                # 图片
                "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp",
                # 视频
                "mp4", "avi", "mkv", "webm", "mov",
                # 音频
                "mp3", "ogg", "wav", "flac", "aac",
                # 文档
                "pdf", "txt", "rtf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                # 压缩
                "zip", "tar", "gz", "bz2", "xz",
                # 安装包
                "deb", "rpm"
            }
            return ext in supported
        
        # 其他系统，默认不支持
        return False
    
    @staticmethod
    def _format_file_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"
    
    def _primary_button_style(self, colors: dict = None) -> str:
        """主按钮样式（使用主题颜色）"""
        if colors is None:
            colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_primary_bg']};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {colors['button_primary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {colors['button_primary_pressed']};
            }}
        """
    
    def _secondary_button_style(self, colors: dict = None) -> str:
        """次要按钮样式（使用主题颜色）"""
        if colors is None:
            colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_secondary_bg']};
                border: 1px solid {colors['button_secondary_border']};
                color: {colors['text_secondary']};
                border-radius: 8px;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {colors['button_secondary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {colors['button_secondary_pressed']};
            }}
            QPushButton::text {{
                background-color: transparent;
            }}
        """
    
    def reject(self):
        """兼容对话框的 reject 接口"""
        self.rejected.emit()
        self.close()
    
    def bring_to_front(self):
        """确保气泡在最上层显示"""
        try:
            self.raise_()
        except Exception:
            pass


class ClipboardRequestBubble(QWidget):
    """剪贴板内容请求气泡"""
    
    paste_to_clipboard = Signal()
    save_as_file = Signal()
    rejected = Signal()
    
    def __init__(self, sender_name: str, is_image: bool = False, parent=None):
        super().__init__(parent)
        self._sender_name = sender_name
        self._is_image = is_image
        self._size_locked = False
        self._last_screen_name = None
        self._pointer_visible = True
        self._is_dark = self._detect_theme()
        self._setup_ui()
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _load_discover_scope(self) -> str:
        """读取可被发现范围配置，默认 all"""
        try:
            cfg = ConfigManager.load()
            scope = cfg.get("airdrop_discover_scope", "all")
            if scope not in ("all", "group", "none"):
                scope = "all"
            return scope
        except Exception:
            return "all"

    def _save_discover_scope(self, scope: str):
        try:
            cfg = ConfigManager.load()
            cfg["airdrop_discover_scope"] = scope
            ConfigManager.save(cfg)
        except Exception as e:
            logger.warning(f"保存 discover_scope 配置失败: {e}")
    
    def _get_theme_colors(self) -> dict:
        """获取主题颜色"""
        if self._is_dark:
            return {
                "bg_primary": "#1C1C1E",
                "text_primary": "#FFFFFF",
                "text_secondary": "#EBEBF5",
                "text_tertiary": "#9a9ab1",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#2C2C2E",
                "button_secondary_border": "#38383A",
                "button_secondary_hover": "#3A3A3C",
                "button_secondary_pressed": "#48484A",
                "bg_alpha": 0.95,
                "border_alpha": 1.0,
                "bg_rgb": "28, 28, 30",
                "border_rgb": "50, 50, 52",
                "link": "#0A84FF",
            }
        else:
            return {
                "bg_primary": "#FFFFFF",
                "text_primary": "#000000",
                "text_secondary": "#111111",
                "text_tertiary": "#8E8E93",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#F2F2F7",
                "button_secondary_border": "#D1D1D6",
                "button_secondary_hover": "#E5E5EA",
                "button_secondary_pressed": "#D8D8DC",
                "bg_alpha": 0.96,
                "border_alpha": 0.1,
                "bg_rgb": "255, 255, 255",
                "border_rgb": "0, 0, 0",
                "link": "#0A84FF",
            }
    
    def _primary_button_style(self, colors: dict = None) -> str:
        if colors is None:
            colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_primary_bg']};
                border: none;
                color: {colors['text_primary']};
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {colors['button_primary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {colors['button_primary_pressed']};
            }}
            QPushButton::text {{
                background-color: transparent;
            }}
        """
    
    def _secondary_button_style(self, colors: dict = None) -> str:
        if colors is None:
            colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_secondary_bg']};
                border: 1px solid {colors['button_secondary_border']};
                color: {colors['text_secondary']};
                border-radius: 8px;
                font-weight: 500;
                font-size: 12px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {colors['button_secondary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {colors['button_secondary_pressed']};
            }}
            QPushButton::text {{
                background-color: transparent;
            }}
        """
    
    def _setup_ui(self, colors: dict = None):
        if colors is None:
            colors = self._get_theme_colors()
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        
        container = QFrame()
        container.setObjectName("clipboardBubbleFrame")
        container.setStyleSheet(f"""
            QFrame#clipboardBubbleFrame {{
                background: rgba({colors['bg_rgb']}, {colors['bg_alpha']});
                border-radius: 12px;
                border: 1px solid rgba({colors['border_rgb']}, {colors['border_alpha']});
            }}
            QLabel {{
                color: {colors['text_primary']};
            }}
        """)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(10)
        container.setLayout(container_layout)
        
        content_type = "剪贴板图片" if self._is_image else "剪贴板内容"
        title = QLabel(f"“{self._sender_name}”想向你发送{content_type}。")
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {colors['text_secondary']};")
        container_layout.addWidget(title)
        
        info_text = "是否将图片放入剪贴板？" if self._is_image else "请选择接收方式："
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        info_label.setStyleSheet(f"font-size: 10px; color: {colors['text_tertiary']}; background-color: transparent;")
        container_layout.addWidget(info_label)
        
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(12)
        
        if not self._is_image:
            save_btn = QPushButton("另存为TXT")
            save_btn.setFixedHeight(30)
            save_btn.setMinimumWidth(80)
            save_btn.setStyleSheet(self._secondary_button_style(colors))
            save_btn.clicked.connect(self.save_as_file.emit)
            btn_layout.addWidget(save_btn, 1)
        
        reject_btn = QPushButton("拒绝")
        reject_btn.setFixedHeight(30)
        reject_btn.setMinimumWidth(48)
        reject_btn.setStyleSheet(self._secondary_button_style(colors))
        reject_btn.clicked.connect(lambda: self.rejected.emit())
        btn_layout.addWidget(reject_btn, 1)
        
        accept_btn = QPushButton("放入剪贴板")
        accept_btn.setFixedHeight(30)
        accept_btn.setMinimumWidth(80)
        accept_btn.setStyleSheet(self._primary_button_style(colors))
        accept_btn.clicked.connect(self.paste_to_clipboard.emit)
        btn_layout.addWidget(accept_btn, 1)
        
        container_layout.addLayout(btn_layout)
        outer_layout.addWidget(container, 0, Qt.AlignTop)
        
        self._container = container
        self._pointer_overlap = 1
        pointer_color = QColor(255, 255, 255, 245) if not self._is_dark else QColor(28, 28, 30, 245)
        border_color = QColor(0, 0, 0, 100) if not self._is_dark else QColor(50, 50, 52, 255)
        self._pointer = TrianglePointer(pointer_color, self)
        self._pointer.set_border_color(border_color)
        spacer = QSpacerItem(0, max(0, self._pointer.height() - self._pointer_overlap), QSizePolicy.Minimum, QSizePolicy.Fixed)
        outer_layout.addSpacerItem(spacer)
        QTimer.singleShot(0, self._position_pointer)
    
    def lock_size_for_screen(self, screen):
        name = screen.name() if screen else None
        if self._size_locked and name == self._last_screen_name:
            return
        self._last_screen_name = name
        self._size_locked = False
        self.adjustSize()
        self.setFixedSize(self.size())
        self._size_locked = True
    
    def set_pointer_visible(self, visible: bool):
        self._pointer_visible = visible
        self._pointer.setVisible(visible)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_pointer()
    
    def _position_pointer(self):
        try:
            if not hasattr(self, "_container") or not self._container:
                return
            cgeom = self._container.geometry()
            pw, ph = self._pointer.width(), self._pointer.height()
            overlap = getattr(self, "_pointer_overlap", 1)
            x = cgeom.x() + (cgeom.width() - pw) // 2
            y = cgeom.y() + cgeom.height() - overlap
            self._pointer.move(x, y)
            self._pointer.raise_()
        except Exception:
            pass
    
    def reject(self):
        """兼容到期处理"""
        self.rejected.emit()
        self.close()
    
    def bring_to_front(self):
        try:
            self.raise_()
        except Exception:
            pass
class AirDropView(QWidget):
    """隔空投送主界面（苹果风格）"""
    
    # 信号：窗口需要隐藏（已移除悬浮图标，不再需要）
    # should_hide_to_icon = Signal(QPoint)  # 已移除
    
    # 信号：传输请求结果（用于从后台线程通知主线程）
    # Windows/Qt6：避免 dict 这种非 Qt 元类型作为 Signal 签名，使用 object 更安全
    transfer_request_result = Signal(object, str, str, str, int, str)  # result(dict), file_path, device_name, device_ip, device_port, request_id
    
    def _load_discover_scope(self) -> str:
        """读取可被发现范围配置，默认 all"""
        try:
            cfg = ConfigManager.load()
            scope = cfg.get("airdrop_discover_scope", "all")
            if scope not in ("all", "group", "none"):
                scope = "all"
            return scope
        except Exception:
            return "all"

    def _save_discover_scope(self, scope: str):
        """保存可被发现范围配置"""
        try:
            cfg = ConfigManager.load()
            cfg["airdrop_discover_scope"] = scope
            ConfigManager.save(cfg)
        except Exception as e:
            logger.warning(f"保存 discover_scope 配置失败: {e}")

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
        # 跨线程 UI 任务投递（严禁在 Python Thread 里直接调用 QTimer/操作 Qt，Win11/Qt6 易触发 Qt6Core.dll 0xc0000005）
        self._ui_event_queue: "queue.Queue[callable]" = queue.Queue()
        self._ui_dispatch_enabled: bool = True
        try:
            self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        except Exception:
            pass
        # UI 线程任务调度器：周期性 drain 队列（避免从 Python Thread 里 invokeMethod/emit signal）
        self._ui_dispatch_timer = QTimer(self)
        self._ui_dispatch_timer.setInterval(20)
        self._ui_dispatch_timer.timeout.connect(self._drain_ui_events)
        self._ui_dispatch_timer.start()
        _debug_log("Initializing AirDropView...")
        self._transfer_manager: Optional[TransferManager] = None
        self._transferring = False
        self._current_target: Optional[DeviceInfo] = None
        self._pending_requests: Dict[str, dict] = {}  # 待处理的传输请求
        self._was_hidden_to_icon = False  # 标记窗口是否被隐藏到图标
        self._changing_window_state = False  # 防止 changeEvent 递归的标志
        
        # 排序相关数据
        self._current_user_id: Optional[str] = None  # 当前用户的 user_id
        self._current_user_group_id: Optional[str] = None  # 当前用户的组ID
        self._device_discovery_times: Dict[str, float] = {}  # 设备发现时间 {user_id: timestamp}
        self._device_transfer_times: Dict[str, float] = {}  # 设备传输时间 {user_id: timestamp}
        self._device_group_ids: Dict[str, Optional[str]] = {}  # 设备组ID {user_id: group_id}
        self._discover_scope: str = self._load_discover_scope()  # 可被发现范围 all/group/none
        self._scope_change_thread: Optional[threading.Thread] = None  # 更新可见范围的后台任务
        self._temp_visible_devices: Set[str] = set()  # 临时显示的设备（发送请求方）
        self._temp_device_timers: Dict[str, QTimer] = {}  # 临时设备的定时移除器
        # 发送等待倒计时
        self._wait_countdown_timer: Optional[QTimer] = None
        self._wait_countdown_remaining: int = 0
        self._wait_countdown_device: Optional[DeviceInfo] = None
        # 设备列表最小高度（保证提示区在底部，即使只有一行/无同事）
        self._devices_min_height = None  # 启动后根据初始列表高度动态确定
        # 淡入动画引用，避免被GC
        self._fade_animations: list = []
        # 记录最近一次有效的设备列表可用宽度，隐藏状态下回退使用
        self._last_viewport_width: int = 0
        # 记录最近一次有效的设备列表可用高度，隐藏状态下回退使用
        self._last_viewport_height: int = 0
        # 标记布局是否需要在下次显示时重算
        self._layout_dirty: bool = False
        # 应用退出时统一清理传输管理器（窗口隐藏/关闭不再主动停服务）
        try:
            QApplication.instance().aboutToQuit.connect(self._cleanup_transfer_manager)
        except Exception:
            pass
        
        # 主题相关
        self._is_dark = self._detect_theme()
        self._theme_check_timer = QTimer()
        self._theme_check_timer.timeout.connect(self._check_and_update_theme)
        self._theme_check_timer.start(1000)  # 每秒检查一次主题变化
        
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

    def _post_to_ui_thread(self, fn):
        """从任意线程安全投递到 UI 线程执行。"""
        if not getattr(self, "_ui_dispatch_enabled", True):
            return
        try:
            self._ui_event_queue.put_nowait(fn)
        except Exception as e:
            try:
                logger.error(f"[AirDropView] enqueue ui event failed: {e}", exc_info=True)
            except Exception:
                pass
            return

    @Slot()
    def _drain_ui_events(self):
        """在 UI 线程执行队列中的任务。"""
        while True:
            try:
                fn = self._ui_event_queue.get_nowait()
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"[AirDropView] dequeue ui event failed: {e}", exc_info=True)
                break
            try:
                fn()
            except Exception as e:
                logger.error(f"[AirDropView] ui event execution failed: {e}", exc_info=True)

    @Slot()
    def _on_destroyed(self) -> None:
        self._ui_dispatch_enabled = False
        try:
            if hasattr(self, "_ui_dispatch_timer") and self._ui_dispatch_timer:
                self._ui_dispatch_timer.stop()
        except Exception:
            pass

    def _snapshot_viewport_metrics(self):
        """记录当前设备列表的可用宽度/高度，避免隐藏后视口为0时无法计算布局。"""
        try:
            if hasattr(self, 'devices_list') and self.devices_list.viewport():
                vp = self.devices_list.viewport()
                w = vp.width()
                h = vp.height()
                if w and w > 0:
                    self._last_viewport_width = w
                if h and h > 0:
                    self._last_viewport_height = h
        except Exception:
            pass
    
    def changeEvent(self, event):
        """处理窗口状态改变事件，禁止最大化和最小化"""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            # 防止递归：如果正在处理窗口状态改变，直接返回
            if self._changing_window_state:
                super().changeEvent(event)
                return
            
            # 如果窗口被最大化，立即恢复
            if self.isMaximized():
                self._changing_window_state = True
                try:
                    self.showNormal()
                    event.ignore()
                finally:
                    self._changing_window_state = False
                return
            # 如果窗口被最小化，也恢复（因为我们要用隐藏到图标代替）
            if self.isMinimized():
                self._changing_window_state = True
                try:
                    self.showNormal()
                    event.ignore()
                finally:
                    self._changing_window_state = False
                return
        # 调用父类方法处理其他事件
        super().changeEvent(event)
        
        if event.type() == QEvent.WindowActivate:
            # 窗口被激活时，重新提升所有气泡窗口到最上层
            # 延迟执行，确保窗口状态已更新
            # 注意：只在 macOS 上需要，因为 macOS 点击任何位置都会激活窗口
            import platform
            if platform.system() == "Darwin":
                # 延迟稍长，确保窗口激活事件已经处理完
                QTimer.singleShot(200, self._bring_all_bubbles_to_front)
                # 激活后立即尝试聚焦最早待处理请求，避免必须点击
                QTimer.singleShot(200, self._focus_first_pending_request)
            else:
                # 其他平台也补一刀，防止悬浮窗口被覆盖
                QTimer.singleShot(50, self._focus_first_pending_request)
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
        colors = self._get_theme_colors()
        
        # 设置窗口样式
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {colors['bg_primary']};
            }}
            QLabel {{
                color: {colors['text_primary']};
            }}
            DeviceItemWidget {{
                /* 确保子组件布局方向正确 */
            }}
        """)
        
        # 主内容区域（设备列表 + 提示内容），整体作为滚动内容
        content_widget = QWidget()
        content_widget.setStyleSheet(f"background-color: {colors['bg_primary']};")
        # 设置大小策略，确保能够根据内容自动调整大小
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 10, 0, 0)
        content_layout.setSpacing(0)

        self.devices_list = NonScrollListWidget()
        self.devices_list.setSpacing(1)
        self.devices_list.setSelectionMode(QListWidget.NoSelection)
        self.devices_list.setFocusPolicy(Qt.NoFocus)
        # 禁用QListWidget自己的滚动条，使用外层QScrollArea的滚动条
        self.devices_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.devices_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 设置大小策略，允许根据内容自动调整高度
        self.devices_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # 设置视图模式为IconMode，允许item自由设置大小
        self.devices_list.setViewMode(QListWidget.IconMode)
        # 设置流式布局，横向排列
        self.devices_list.setFlow(QListWidget.LeftToRight)
        # 设置item大小模式为固定
        self.devices_list.setResizeMode(QListWidget.Fixed)
        # 去掉QListWidget本身的边框，但给item添加边框，并设置内边距和居中对齐
        self.devices_list.setStyleSheet(f"""
            QListWidget {{
                border: none;
                background-color: {colors['bg_primary']};
            }}
            QListWidget::item {{
                border: 1px solid {colors['item_border']};
                border-radius: 8px;
                background-color: transparent;
                padding: 0px;
                text-align: center;
            }}
        """)
        content_layout.addWidget(self.devices_list, 0)
        
        # 提示内容区域（作为正常内容，跟随在同事列表后面）
        self._list_to_background_spacing = 12  # 记录列表与提示区的固定间距
        content_layout.addSpacing(self._list_to_background_spacing)
        self._background_frame = QFrame(content_widget)
        self._background_frame.setStyleSheet("background-color: transparent;")
        self._background_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._background_frame.setMinimumHeight(140)
        background_layout = QVBoxLayout(self._background_frame)
        background_layout.setAlignment(Qt.AlignCenter)
        background_layout.setContentsMargins(0, 0, 0, 0)
        background_layout.setSpacing(6)
        
        # 信号图标
        signal_label = QLabel()
        signal_label.setAlignment(Qt.AlignCenter)
        # 加载图标
        from utils.resource_path import get_resource_path
        icon_path = get_resource_path("resources/airdrop.png")
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                # 缩放图标到合适大小（32x32像素，更小）
                scaled_pixmap = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # 根据主题着色图标
                if self._is_dark:
                    tinted_pixmap = self._tint_pixmap(scaled_pixmap, QColor(0, 104, 218))
                else:
                    tinted_pixmap = self._tint_pixmap(scaled_pixmap, QColor(0, 104, 218))
                signal_label.setPixmap(tinted_pixmap)
                signal_label.setStyleSheet(f"color: {colors['signal_icon']};")
            else:
                # 如果加载失败，使用默认emoji
                signal_label.setText("📡")
                signal_label.setStyleSheet(f"color: {colors['signal_icon']}; font-size: 32px;")
        else:
            # 如果文件不存在，使用默认emoji
            signal_label.setText("📡")
            signal_label.setStyleSheet(f"color: {colors['signal_icon']}; font-size: 32px;")
        background_layout.addWidget(signal_label)
        
        # 提示文字
        self._background_label = QLabel('"隔空投送"可让你与附近的同事立即共享。')
        self._background_label.setAlignment(Qt.AlignCenter)
        self._background_label.setStyleSheet(f"color: {colors['text_tertiary']}; font-size: 12px;")
        self._background_label.setWordWrap(False)  # 不换行
        background_layout.addWidget(self._background_label)

        # 允许发现控制（文案占位，功能后续实现）
        discover_row = QHBoxLayout()
        discover_row.setContentsMargins(0, 0, 0, 0)
        discover_row.setSpacing(4)
        discover_row.addStretch()
        link_color = colors.get("link", colors.get("button_primary_bg", "#0A84FF"))
        self._discover_button = QPushButton("允许这些同事发现我：所有人")
        self._discover_button.setCursor(Qt.PointingHandCursor)
        self._discover_button.setFlat(True)
        self._discover_button.setStyleSheet(self._discover_button_style(link_color))
        # 设置箭头图标（与文字同色）
        from utils.resource_path import get_resource_path
        arrow_path = get_resource_path("resources/arrow_down.png")
        if arrow_path.exists():
            arrow_pix = QPixmap(str(arrow_path))
            if not arrow_pix.isNull():
                try:
                    tinted_arrow = self._tint_pixmap(arrow_pix, QColor(link_color))
                except Exception:
                    tinted_arrow = arrow_pix
                self._discover_button.setIcon(QIcon(tinted_arrow))
                self._discover_button.setIconSize(QSize(14, 9))  # 放大箭头尺寸
                self._discover_button.setLayoutDirection(Qt.RightToLeft)  # 让箭头在文字右侧
        self._update_discover_button_label()
        # 用 lambda 包一层，避免 IDE 对 connect 的解析警告
        self._discover_button.clicked.connect(lambda: self._on_discover_clicked())
        discover_row.addWidget(self._discover_button)
        discover_row.addStretch()
        background_layout.addLayout(discover_row)
        
        # 将提示内容添加到布局中，单独一行，居中显示
        content_layout.addWidget(self._background_frame, 0, Qt.AlignHCenter)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(f"color: {colors['text_tertiary']}; font-size: 12px;")
        self.status_label.setVisible(False)
        content_layout.addWidget(self.status_label)
        
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 整个内容放入滚动区域（仅在需要时滚动）
        # 若内容未超出视窗则不应出现滚动行为
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 垂直滚动条默认关闭，后续根据内容高度动态调整
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setWidget(content_widget)
        layout.addWidget(self._scroll_area)
        
        # 通过样式表隐藏滚动条（保持视觉无滚动条）
        self._scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
            }
            QScrollBar:vertical {
                width: 0px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                width: 0px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                width: 0px;
                height: 0px;
            }
            QScrollBar:horizontal {
                height: 0px;
                background: transparent;
            }
            QScrollBar::handle:horizontal {
                height: 0px;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
                height: 0px;
            }
        """)
        
        # 保存引用
        self._content_widget = content_widget
        
        # 记录初始布局距离（未启动服务时提示块相对窗口的位置）
        QTimer.singleShot(0, self._log_initial_layout_metrics)
    
    def _discover_button_style(self, link_color: str) -> str:
        """下拉按钮样式，仿苹果蓝色文本+简洁箭头"""
        return f"""
            QPushButton {{
                color: {link_color};
                font-size: 12px;
                border: 0px;
                background: transparent;
                padding: 2px 4px 2px 0px; /* 给右侧箭头留一点空隙 */
                text-align: left;
            }}
            QPushButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            QPushButton:hover {{
                color: {link_color};
                text-decoration: underline;
            }}
        """
    
    def _update_discover_button_label(self):
        """根据当前 scope 更新按钮文案"""
        scope_label_map = {"all": "所有人", "group": "本部门", "none": "没有人"}
        label = scope_label_map.get(self._discover_scope, "所有人")
        self._discover_button.setText(f"允许这些同事发现我：{label}")
    
    def _apply_discover_scope(self, scope: str, persist: bool = True, update_service: bool = True):
        """应用选择的可见范围，更新 UI / 配置 / 服务"""
        if scope not in ("all", "group", "none"):
            scope = "all"
        self._discover_scope = scope
        self._update_discover_button_label()
        if persist:
            self._save_discover_scope(scope)
        if update_service and self._transfer_manager:
            # 直接在 UI 线程更新：避免从 Python Thread 调用 QObject 方法导致 Win11/Qt6 原生崩溃
            try:
                self._transfer_manager.set_discover_scope(scope)
            except Exception as e:
                logger.warning(f"更新 discover_scope 到 TransferManager 失败: {e}")
    
    def _on_discover_clicked(self):
        """展示自定义下拉（向上弹出），选项暂不改逻辑，仅UI"""
        colors = self._get_theme_colors()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {colors['bg_primary']};
                color: {colors['text_primary']};
                border-radius: 8px;
                border: 1px solid {colors['button_secondary_border']};
            }}
            QMenu::item:selected {{
                background: {colors['button_primary_bg']};
                color: {colors['text_primary']};
            }}
        """)
        options = [("all", "所有人"), ("group", "本部门"), ("none", "没有人")]
        actions = []
        for key, label in options:
            actions.append((menu.addAction(label), key))
    
        # 计算向上弹出的位置
        anchor = self._discover_button.mapToGlobal(QPoint(self._discover_button.width() // 2, 0))
        menu_size = menu.sizeHint()
        pos = QPoint(anchor.x() - menu_size.width() // 2, anchor.y() - menu_size.height())
        chosen = menu.exec(pos)
        if chosen:
            for action, key in actions:
                if action == chosen:
                    # 仅更新广播/配置，不拦截发送，失败交由对方拒绝
                    self._apply_discover_scope(key, persist=True, update_service=True)
                    break

    def _log_initial_layout_metrics(self):
        """打印未启动服务时提示区域的相对位置，便于校准"""
        try:
            if not hasattr(self, '_background_frame') or not hasattr(self, 'devices_list'):
                return
            bg_top = self._background_frame.mapTo(self, QPoint(0, 0)).y()
            list_top = self.devices_list.mapTo(self, QPoint(0, 0)).y()
            bg_h = self._background_frame.height()
            list_h = self.devices_list.height()
            window_h = self.height()
            logger.info(
                f"[AirDropView] initial layout: bg_top={bg_top}, list_top={list_top}, "
                f"bg_height={bg_h}, list_height={list_h}, "
                f"window_height={window_h}"
            )
            # 动态设定设备列表的最小高度，避免提示区上移
            if not self._devices_min_height:
                # 按需求：初始最小高度 = 窗口高度 H - 提示区高度 H1（再减去布局上边距10和间距12）
                base_margins = 10 + 12  # content_top_margin + spacing
                available_h = max(window_h - bg_h - base_margins, 0)
                self._devices_min_height = available_h
                logger.info(f"[AirDropView] devices_min_height set to {self._devices_min_height}")
        except Exception as e:
            logger.debug(f"[AirDropView] log initial layout failed: {e}")
    
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
        
        # 窗口大小改变时，更新所有 item 的宽度和 devices_list 的大小
        if hasattr(self, 'devices_list'):
            QTimer.singleShot(0, self._update_item_widths)
            if self.devices_list.count() > 0:
                QTimer.singleShot(0, self._adjust_devices_list_size)
    
    def showEvent(self, event):
        """窗口显示时更新 item 宽度"""
        super().showEvent(event)
        # 窗口显示后，延迟更新 item 宽度，确保布局已完成
        if hasattr(self, 'devices_list'):
            QTimer.singleShot(50, self._update_item_widths)
            QTimer.singleShot(80, self._adjust_devices_list_size)
        # 更新窗口标题（显示在线人数）
        QTimer.singleShot(50, self._update_window_title)
        # 如果有待处理请求，显示时主动聚焦最早的请求
        if self._pending_requests:
            QTimer.singleShot(50, self._focus_first_pending_request)

    def show(self):
        """确保每次显示（含从隐藏恢复）都强制重算布局。"""
        super().show()
        # 仅在显示时执行布局计算；若之前标记过 dirty，这里统一刷新
        def refresh():
            self._layout_dirty = False
            self._update_item_widths()
            self._adjust_devices_list_size()
            self._update_window_title()
        refresh()
        QTimer.singleShot(10, refresh)
        QTimer.singleShot(10, refresh)
    
    def _tint_pixmap(self, pixmap: QPixmap, color: QColor) -> QPixmap:
        """将图标着色为指定颜色"""
        # 创建新的pixmap，使用源pixmap的尺寸
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 使用源pixmap作为mask，然后填充指定颜色
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, pixmap)
        
        # 使用CompositionMode_SourceIn将颜色改为指定颜色
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), color)
        
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
                # 设置拖动标志并隐藏所有气泡
                if not self._is_dragging:
                    self._is_dragging = True
                    self._hide_all_bubbles()
            
            self._last_window_pos = current_pos
            self._position_unchanged_count = 0  # 重置未变化计数
        else:
            # 窗口位置没有变化
            if self._drag_detected:
                # 如果鼠标左键还在按下，说明还在拖拽中（可能拖到了边缘或暂时停止移动）
                if is_left_button_pressed:
                    # 鼠标还在按下，保持拖动状态，确保气泡保持隐藏
                    if not self._is_dragging:
                        self._is_dragging = True
                        self._hide_all_bubbles()
                    self._position_unchanged_count = 0
                else:
                    # 鼠标已经释放，但需要确认位置确实不再变化（避免误判）
                    self._position_unchanged_count += 1
                    # 只有当位置连续多次（约200ms）没有变化，且鼠标已释放时，才认为拖拽结束
                    if self._position_unchanged_count >= 4:  # 4次 * 50ms = 200ms
                        # 拖动结束，重置标志并重新定位气泡
                        if self._is_dragging:
                            self._is_dragging = False
                            self._drag_detected = False
                            QTimer.singleShot(100, self._reposition_all_bubbles)
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
        
        # 点击窗口时，延迟提升所有气泡窗口（防止被窗口激活覆盖）
        # macOS 上点击任何位置都会激活窗口，Windows 上只有点击标题栏才会激活
        if platform.system() == "Darwin":
            # macOS: 点击任何位置都可能激活窗口，延迟提升气泡
            # 延迟稍长，确保窗口激活事件已经处理完
            QTimer.singleShot(200, self._bring_all_bubbles_to_front)
        else:
            # Windows: 只有点击标题栏才会激活，检查是否在标题栏区域
            y_pos = event.position().y()
            if y_pos <= 50:
                QTimer.singleShot(200, self._bring_all_bubbles_to_front)
        
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
                    self._hide_all_bubbles()
            
            if self._is_dragging:
                # 拖动期间持续隐藏所有气泡（防止被其他逻辑重新显示）
                self._hide_all_bubbles()
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
        was_dragging = self._is_dragging
        self._is_dragging = False
        self._drag_detected = False  # 重置拖动检测标志
        super().mouseReleaseEvent(event)
        if was_dragging:
            # 拖动结束后，延迟一点再重新定位气泡，确保窗口位置已稳定
            QTimer.singleShot(100, self._reposition_all_bubbles)
    
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
                    # 重新定位气泡到头像位置
                    self._reposition_all_bubbles()
                    QTimer.singleShot(0, self._focus_first_pending_request)
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
        
        # 在隐藏前记录一次可用宽高，避免隐藏后为 0
        self._snapshot_viewport_metrics()
        
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
                # 窗口隐藏后，重定位现有气泡到右上角
                self._reposition_all_bubbles()
                
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
                # 窗口隐藏后，重定位现有气泡到右上角
                self._reposition_all_bubbles()
                
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
            """在后台线程中执行耗时操作，带重试机制"""
            import time
            max_retries = 3
            retry_delay = 2  # 秒
            
            for attempt in range(max_retries):
                try:
                    _debug_log(f"Fetching user info for AirDrop... (attempt {attempt + 1}/{max_retries})")
                    api_client = ApiClient.from_config()
                    user_info = api_client._get("/api/user_info")
                    
                    if isinstance(user_info, dict) and user_info.get("status") == "success":
                        data = user_info.get("data", {})
                        user_id = str(data.get("user_id", ""))
                        user_name = data.get("name", "Unknown")
                        avatar_url = data.get("avatar_url")
                        # 获取当前用户的组ID
                        self._current_user_group_id = data.get("group_id") or data.get("team_id")
                        _debug_log(f"User info loaded: id={user_id}, name={user_name}, group_id={self._current_user_group_id}")
                        
                        _debug_log("Queueing _create_transfer_manager on UI thread")
                        self._post_to_ui_thread(lambda uid=user_id, un=user_name, au=(avatar_url or ""): self._createTransferManagerSlot(uid, un, au))
                        return  # 成功，退出重试循环
                    else:
                        _debug_log("User info response invalid, cannot start AirDrop")
                        def show_error():
                            Toast.show_message(self, "无法获取用户信息，请先登录")
                        self._post_to_ui_thread(show_error)
                        return  # 业务错误，不重试
                except Exception as e:
                    logger.warning(f"初始化传输管理器失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    _debug_log(f"init_in_thread exception (attempt {attempt + 1}/{max_retries}): {e}")
                    
                    if attempt < max_retries - 1:
                        # 还有重试机会，等待后重试
                        _debug_log(f"等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        # 所有重试都失败了
                        logger.error(f"初始化传输管理器失败（已重试 {max_retries} 次）: {e}")
                        def show_error():
                            Toast.show_message(self, f"初始化失败: {e}（已重试 {max_retries} 次）")
                        self._post_to_ui_thread(show_error)
        
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
            self._current_user_id = user_id  # 保存当前用户的 user_id
            self._transfer_manager = TransferManager(
                user_id=user_id,
                user_name=user_name,
                avatar_url=avatar_url,
                group_id=self._current_user_group_id,
                discover_scope=self._discover_scope
            )
            
            self._transfer_manager.device_added.connect(self._on_device_added)
            self._transfer_manager.device_removed.connect(self._on_device_removed)
            self._transfer_manager.transfer_request_received.connect(self._on_transfer_request_received)
            self._transfer_manager.file_received.connect(self._on_file_received)
            self._transfer_manager.transfer_progress.connect(self._on_transfer_progress)
            self._transfer_manager.receive_progress.connect(self._on_receive_progress)
            self._transfer_manager.transfer_completed.connect(self._on_transfer_completed)
            
            # 传输请求结果不再使用 Qt Signal 从后台线程回传，统一走 _post_to_ui_thread 直接回到 UI 线程调用

            # 启动 TransferManager：在 UI 线程执行（避免从 Python Thread 调用 QObject 方法导致 Win11/Qt6 原生崩溃）
            def start_manager_attempt(attempt: int = 1, delay_s: int = 0):
                max_retries = 3
                if delay_s and delay_s > 0:
                    QTimer.singleShot(delay_s * 1000, lambda: start_manager_attempt(attempt, 0))
                    return
                try:
                    self._transfer_manager.start()
                    self._on_transfer_manager_started()
                except Exception as exc:
                    logger.warning(f"启动 TransferManager 失败 (尝试 {attempt}/{max_retries}): {exc}")
                    if attempt < max_retries:
                        next_delay = 2 ** attempt  # 2,4,...
                        start_manager_attempt(attempt + 1, next_delay)
                    else:
                        logger.error(f"启动 TransferManager 失败（已重试 {max_retries} 次）: {exc}")
                        Toast.show_message(self, f"初始化失败: {exc}（已重试 {max_retries} 次）")

            QTimer.singleShot(0, lambda: start_manager_attempt(1, 0))
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
    
    def _get_device_unique_id(self, device: DeviceInfo) -> str:
        """获取设备的唯一标识（user_id + ip，支持同一账号多个设备）"""
        return f"{device.user_id}::{device.ip}"

    def _has_device_in_list(self, key: str) -> bool:
        """当前列表中是否已有该设备"""
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                if self._get_device_unique_id(widget.device) == key:
                    return True
        return False

    def _resolve_device_name(self, user_id: str, ip: str, fallback: str) -> str:
        """根据已知设备缓存获取设备名，找不到则用 fallback"""
        try:
            if self._transfer_manager:
                for d in self._transfer_manager.get_devices():
                    if d.user_id == user_id and d.ip == ip:
                        return d.device_name or d.name or fallback
        except Exception:
            pass
        return fallback

    def _is_device_visible(self, device: DeviceInfo) -> bool:
        """
        根据对方的 discover_scope 及组信息决定是否展示。
        本端选择“本部门/没人”仅影响自身广播（通过 discover_scope 传递给对方），不影响本地列表展示。
        """
        device_key = self._get_device_unique_id(device)
        if device_key in self._temp_visible_devices:
            return True  # 有临时展示需求，直接显示
        remote_scope = (getattr(device, "discover_scope", None) or "all").lower()
        device_group = getattr(device, "group_id", None)
        my_group = self._current_user_group_id

        # 对端关闭广播
        if remote_scope == "none":
            return False
        # 对端仅同组可见
        if remote_scope == "group":
            if not my_group or not device_group or device_group != my_group:
                return False
        return True

    def _add_temp_device_for_request(self, device: DeviceInfo, ttl_ms: int = 60000):
        """临时将发送方展示在列表中，超时自动移除"""
        key = self._get_device_unique_id(device)
        # 已在临时集或已在常规列表中，直接返回，避免重复
        if key in self._temp_visible_devices or self._has_device_in_list(key):
            return
        self._temp_visible_devices.add(key)
        self._add_device_widget_at_sorted_position(device)
        # 定时移除
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ttl_ms)
        timer.timeout.connect(lambda k=key: self._remove_temp_device_by_key(k))
        timer.start()
        self._temp_device_timers[key] = timer

    def _remove_temp_device_by_key(self, key: str):
        """移除临时显示的设备（并刷新列表）"""
        if key not in self._temp_visible_devices:
            return
        self._temp_visible_devices.discard(key)
        timer = self._temp_device_timers.pop(key, None)
        if timer:
            timer.stop()
            timer.deleteLater()
        # 删除列表中的对应 item
        for i in range(self.devices_list.count() - 1, -1, -1):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                if self._get_device_unique_id(widget.device) == key:
                    self.devices_list.takeItem(i)
        QTimer.singleShot(0, self._adjust_devices_list_size)

    def _remove_temp_device_by_sender(self, sender_id: str, sender_ip: str):
        key = f"{sender_id or ''}::{sender_ip or ''}"
        self._remove_temp_device_by_key(key)
        QTimer.singleShot(0, self._adjust_devices_list_size)

    # 发送前不再因本端 discover_scope 拦截，保持与苹果一致（发送方可随时发送）
    
    def _update_device_group_cache(self, device: DeviceInfo, reorder_if_changed: bool = False):
        """根据设备自身携带的组信息更新缓存"""
        incoming_group_id = getattr(device, "group_id", None)
        current_group_id = self._device_group_ids.get(device.user_id)
        
        if device.user_id not in self._device_group_ids:
            self._device_group_ids[device.user_id] = incoming_group_id
            return
        
        if incoming_group_id and current_group_id != incoming_group_id:
            self._device_group_ids[device.user_id] = incoming_group_id
            if reorder_if_changed:
                QTimer.singleShot(0, lambda uid=device.user_id: self._reorder_device(uid))
    
    def _on_device_added(self, device: DeviceInfo):
        """设备添加"""
        # 过滤“自己”：同 user_id + 同 IP 不显示（双重兜底，避免定时刷新/回调路径遗漏）
        try:
            if self._current_user_id and device.user_id == self._current_user_id:
                local_ip = getattr(self._transfer_manager, "_local_ip", None) if self._transfer_manager else None
                if local_ip and device.ip == local_ip:
                    _debug_log(f"[UI] Ignoring self device: user_id={device.user_id}, ip={device.ip}")
                    return
        except Exception:
            pass
        if not self._is_device_visible(device):
            _debug_log(f"[UI] Device hidden by discover_scope: {getattr(device, 'discover_scope', None)} user_id={device.user_id}")
            return
        device_unique_id = self._get_device_unique_id(device)
        _debug_log(f"[UI] Device discovered in AirDropView: {device.name} ({device.ip}) user_id={device.user_id}, unique_id={device_unique_id}")
        
        # 使用 user_id + ip 作为唯一标识，支持同一账号多个设备
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                existing_unique_id = self._get_device_unique_id(widget.device)
                if existing_unique_id == device_unique_id:
                    _debug_log(f"[UI] Device already exists: {device_unique_id}, skipping")
                    return  # 相同的设备（user_id + ip）已存在
        
        _debug_log(f"[UI] Adding new device: {device_unique_id}")
        
        # 记录设备发现时间（按 user_id 记录，同一账号的设备共享发现时间）
        import time
        if device.user_id not in self._device_discovery_times:
            self._device_discovery_times[device.user_id] = time.time()
        
        # 记录设备携带的组信息（每个客户端自行广播，无需单独请求接口）
        self._update_device_group_cache(device, reorder_if_changed=True)
        
        # 在正确的位置插入设备（根据排序规则）
        self._add_device_widget_at_sorted_position(device)
    
    def _get_device_sort_key(self, user_id: str) -> tuple:
        """获取设备的排序键"""
        # 1. 优先级：最近传输过的 > 同组的 > 其他组的
        has_transfer = user_id in self._device_transfer_times
        is_same_group = False
        if self._current_user_group_id and user_id in self._device_group_ids:
            device_group_id = self._device_group_ids[user_id]
            is_same_group = (device_group_id == self._current_user_group_id)
        
        # 优先级值：0=最近传输过的，1=同组的，2=其他组的
        if has_transfer:
            priority = 0
            # 最近传输过的按传输时间倒序
            sort_time = self._device_transfer_times[user_id]
        elif is_same_group:
            priority = 1
            # 同组的按发现时间倒序
            sort_time = self._device_discovery_times.get(user_id, 0)
        else:
            priority = 2
            # 其他组的按发现时间倒序
            sort_time = self._device_discovery_times.get(user_id, 0)
        
        # 返回排序键：(优先级, -时间戳) 时间戳取负号实现倒序
        return (priority, -sort_time)
    
    def _find_insert_position(self, device: DeviceInfo) -> int:
        """找到设备应该插入的位置（根据排序规则）"""
        new_key = self._get_device_sort_key(device.user_id)
        
        # 遍历现有items，找到第一个应该在新设备后面的位置
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                existing_key = self._get_device_sort_key(widget.device.user_id)
                # 如果新设备的排序键小于现有设备，应该插入到这里
                if new_key < existing_key:
                    return i
        
        # 如果没有找到合适的位置，插入到最后
        return self.devices_list.count()
    
    def _add_device_widget_at_sorted_position(self, device: DeviceInfo):
        """在排序后的正确位置添加设备卡片"""
        # 如果是同一账号的其他设备，将名称改为"你自己"
        display_device = device
        if self._current_user_id and device.user_id == self._current_user_id:
            # 创建新的 DeviceInfo 对象，将 name 改为"你自己"
            display_device = DeviceInfo(
                name="你自己",
                user_id=device.user_id,
                ip=device.ip,
                port=device.port,
                avatar_url=device.avatar_url,
                device_name=device.device_name,
                group_id=getattr(device, "group_id", None)
            )
        
        item = QListWidgetItem()
        # 先给 AirDropView 作为父对象，便于 DeviceItemWidget 在 __init__ 中正确获取主题/深色模式；
        # setItemWidget 时 Qt 会把其重新挂到 viewport 下。
        widget = DeviceItemWidget(display_device, parent=self)
        widget.file_dropped.connect(self._on_file_dropped)
        # 先设置为透明，插入后再做淡入动画
        opacity = QGraphicsOpacityEffect(widget)
        opacity.setOpacity(0.0)
        widget.setGraphicsEffect(opacity)
        
        # 找到正确的插入位置
        insert_pos = self._find_insert_position(device)
        
        # 在正确位置插入
        self.devices_list.insertItem(insert_pos, item)
        self.devices_list.setItemWidget(item, widget)
        # 设置父窗口引用，用于主题适配
        widget._parent_airdrop_view = self
        # 应用当前主题颜色
        colors = self._get_theme_colors()
        widget._update_theme_colors(colors)
        
        # 根据widget的sizeHint设置item大小，确保头像和文字完全显示
        size_hint = widget.sizeHint()
        if size_hint.isValid():
            item.setSizeHint(size_hint)
            # 确保 item 中的内容水平居中
            item.setTextAlignment(Qt.AlignCenter)
        
        # 更新所有 item 的宽度
        self._update_item_widths()
        # 调整 QListWidget 大小以显示所有内容
        self._adjust_devices_list_size()
        # 更新窗口标题（显示在线人数）
        self._update_window_title()
        # 淡入显示
        self._fade_in_widget(widget)
    
    def _add_device_widget(self, device: DeviceInfo):
        """统一添加设备卡片（保留用于兼容性）"""
        self._add_device_widget_at_sorted_position(device)
    
    def _update_item_widths(self):
        """根据列表数量动态更新所有 item 的宽度"""
        if not hasattr(self, 'devices_list') or self.devices_list.count() == 0:
            return
        # 窗口未显示时跳过计算，标记待刷新
        if not self.isVisible():
            self._layout_dirty = True
            return
        
        # 获取 devices_list 的可用宽度（viewport 宽度，已排除滚动条）
        available_width = self.devices_list.viewport().width()
        if available_width <= 0 and self._last_viewport_width > 0:
            # 窗口隐藏时 viewport 宽度可能为 0，回退到上一次有效值
            available_width = self._last_viewport_width
        if available_width <= 0:
            # 如果宽度还没计算出来，延迟重试
            QTimer.singleShot(10, self._update_item_widths)
            return
        # 记录最近一次有效宽度，供隐藏状态下使用
        self._last_viewport_width = available_width

        # 根据列表数量动态计算每行显示的 item 数量
        total_count = self.devices_list.count()
        if total_count >= 5:
            # 数量 >= 5：每行4个
            items_per_row = 4
        elif total_count == 4:
            # 数量 = 4：每行2个
            items_per_row = 2
        elif total_count == 3:
            # 数量 = 3：每行3个
            items_per_row = 3
        elif total_count == 2:
            # 数量 = 2：每行2个
            items_per_row = 2
        else:  # total_count == 1
            # 数量 = 1：每行1个
            items_per_row = 1
        
        # 计算每个 item 的宽度：可用宽度 / 每行数量 (2*items_per_row 为边框自身所占总宽度)
        item_width = (available_width - 2*items_per_row) // items_per_row
        
        # 更新所有 item 的宽度
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            if item:
                widget = self.devices_list.itemWidget(item)
                if widget:
                    # 获取当前 item 的高度
                    current_size = item.sizeHint()
                    current_height = current_size.height() if current_size.isValid() else 118
                    # 更新 item 的宽度，保持高度不变
                    item.setSizeHint(QSize(item_width, current_height))
                    # 将卡片固定为该行应占的宽度，保证平分并居中
                    widget.setMinimumWidth(item_width)
                    widget.setMaximumWidth(item_width)
                    widget.setFixedWidth(item_width)
        # 确保列表本身宽度与可用宽度对齐，避免剩余空白
        self.devices_list.setMinimumWidth(available_width)
        self.devices_list.setMaximumWidth(available_width)
        # 设定统一网格尺寸，确保 IconMode 平均分配
        self.devices_list.setGridSize(QSize(item_width, current_height if 'current_height' in locals() else 118))
    
    def _adjust_devices_list_size(self):
        """调整 devices_list 的大小以显示所有内容"""
        if self.devices_list.count() == 0:
            min_h = self._devices_min_height or 0
            self.devices_list.setMinimumHeight(min_h)
            self.devices_list.setMaximumHeight(min_h)
            return
        # 窗口未显示时跳过计算，标记待刷新
        if not self.isVisible():
            self._layout_dirty = True
            return
        
        # 获取第一个 item 的大小作为参考
        first_item = self.devices_list.item(0)
        if not first_item:
            return
        
        item_size = first_item.sizeHint()
        if not item_size.isValid():
            return
        
        item_width = item_size.width()
        item_height = item_size.height()
        spacing = self.devices_list.spacing()
        
        # 获取 QListWidget 的可用宽度
        # 需要等待布局完成，所以使用 QTimer 延迟执行
        QTimer.singleShot(0, lambda: self._do_adjust_devices_list_size(item_width, item_height, spacing))
    
    def _do_adjust_devices_list_size(self, item_width: int, item_height: int, spacing: int):
        """实际执行调整大小"""
        # 若窗口未显示，延迟到下次显示再计算，避免隐藏态尺寸为0
        if not self.isVisible():
            self._layout_dirty = True
            return
        if self.devices_list.count() == 0:
            # 无设备时按照 MINH 规则布置，使提示区完整可见且不滚动
            min_h = self._devices_min_height or 0
            viewport_h = 0
            if hasattr(self, "_scroll_area") and self._scroll_area and self._scroll_area.viewport():
                viewport_h = self._scroll_area.viewport().height()
                if viewport_h <= 0 and self._last_viewport_height > 0:
                    viewport_h = self._last_viewport_height
            bg_hint = self._background_frame.sizeHint().height() if hasattr(self, "_background_frame") and self._background_frame else 0
            bg_min = self._background_frame.minimumHeight() if hasattr(self, "_background_frame") and self._background_frame else 0
            bg_h = max(bg_hint, bg_min)
            status_h = self.status_label.sizeHint().height() if hasattr(self, "status_label") and self.status_label and self.status_label.isVisible() else 0
            spacing_h = getattr(self, "_list_to_background_spacing", 0)
            content_top_margin = 10
            content_bottom_margin = 0
            MINH = max(viewport_h - bg_h - status_h - spacing_h - content_top_margin - content_bottom_margin, 0) if viewport_h > 0 else 0
            # 若 MINH 计算为 0（例如服务未启动、高度未知），退回 min_h（默认最小列表高）避免提示被挤
            if MINH == 0 and min_h > 0:
                MINH = min_h

            list_h = max(min_h, MINH)
            self.devices_list.setViewportMargins(0, 0, 0, 0)
            self.devices_list.setMinimumHeight(list_h)
            self.devices_list.setMaximumHeight(list_h)
            # 固定内容总高度，避免滚动且提示区完整
            if hasattr(self, "_scroll_area") and self._scroll_area and self._scroll_area.widget():
                content_h = list_h + spacing_h + bg_h + status_h + content_top_margin + content_bottom_margin
                self._scroll_area.widget().setMinimumHeight(content_h)
                self._scroll_area.widget().setMaximumHeight(content_h)
                from PySide6.QtCore import Qt as _Qt
                self._scroll_area.setVerticalScrollBarPolicy(_Qt.ScrollBarAlwaysOff)
            return
        
        # 获取 QListWidget 的可用宽度（减去滚动条宽度）
        available_width = self.devices_list.viewport().width()
        if available_width <= 0:
            # 如果宽度还没计算出来，延迟重试
            QTimer.singleShot(10, lambda: self._do_adjust_devices_list_size(item_width, item_height, spacing))
            return
        
        # 计算每行能放多少个 item
        items_per_row = max(1, (available_width + spacing) // (item_width + spacing))
        
        # 计算需要多少行
        total_items = self.devices_list.count()
        rows = (total_items + items_per_row - 1) // items_per_row  # 向上取整
        
        # 计算总高度：行数 * (item高度 + 间距) + 一些边距
        total_height = rows * (item_height + spacing) + spacing
        
        # 计算 MINH = viewport高度 - 提示区高度(H1) - 间距/状态高度 - 布局边距
        viewport_h = 0
        if hasattr(self, "_scroll_area") and self._scroll_area and self._scroll_area.viewport():
            viewport_h = self._scroll_area.viewport().height()
            if viewport_h <= 0 and self._last_viewport_height > 0:
                viewport_h = self._last_viewport_height
        bg_hint = self._background_frame.sizeHint().height() if hasattr(self, "_background_frame") and self._background_frame else 0
        bg_min = self._background_frame.minimumHeight() if hasattr(self, "_background_frame") and self._background_frame else 0
        bg_h = max(bg_hint, bg_min)
        status_h = self.status_label.sizeHint().height() if hasattr(self, "status_label") and self.status_label and self.status_label.isVisible() else 0
        spacing_h = getattr(self, "_list_to_background_spacing", 0)
        # content_layout 上下边距
        content_top_margin = 10  # setContentsMargins(0,10,0,0)
        content_bottom_margin = 0
        MINH = max(viewport_h - bg_h - status_h - spacing_h - content_top_margin - content_bottom_margin, 0) if viewport_h > 0 else 0

        # 调试尺寸打印，便于排查布局问题
        # 注意：该方法可能在 UI 刷新/轮询时被频繁触发，避免刷屏：仅当指标变化时输出，且降级为 debug
        try:
            metrics = (
                viewport_h,
                self._last_viewport_height,
                bg_h,
                status_h,
                spacing_h,
                MINH,
                total_height,
                total_items,
                rows,
                available_width,
                self._last_viewport_width,
                content_top_margin,
                content_bottom_margin,
            )
            if getattr(self, "_last_layout_metrics_log", None) != metrics:
                self._last_layout_metrics_log = metrics
                logger.debug(
                    "[AirDropView] layout metrics | vp_h=%s last_vp_h=%s bg_h=%s status_h=%s spacing_h=%s MINH=%s total_h=%s items=%s rows=%s available_w=%s last_vp_w=%s content_top_margin=%s content_bottom_margin=%s",
                    viewport_h, self._last_viewport_height, bg_h, status_h, spacing_h, MINH,
                    total_height, total_items, rows, available_width, self._last_viewport_width,
                    content_top_margin, content_bottom_margin
                )
        except Exception:
            pass

        from PySide6.QtCore import Qt as _Qt
        # 情况1：内容高度 <= MINH，一屏可见且在 MINH 内垂直居中，不滚动
        if MINH > 0 and total_height <= MINH:
            free_space = MINH - total_height
            top_margin = free_space // 2
            bottom_margin = free_space - top_margin
            self.devices_list.setViewportMargins(0, top_margin, 0, bottom_margin)
            self.devices_list.setMinimumHeight(MINH)
            self.devices_list.setMaximumHeight(MINH)
            # 确保内容整体高度不超出 viewport，避免滚动
            if hasattr(self, "_scroll_area") and self._scroll_area and self._scroll_area.widget():
                content_height = MINH + spacing_h + bg_h + status_h + content_top_margin + content_bottom_margin
                self._scroll_area.widget().setMinimumHeight(content_height)
                self._scroll_area.widget().setMaximumHeight(content_height)
            if hasattr(self, "_scroll_area") and self._scroll_area:
                self._scroll_area.setVerticalScrollBarPolicy(_Qt.ScrollBarAlwaysOff)
            return

        # 情况2：内容高度 > MINH（或无法获得 MINH），允许滚动，列表高度按内容
        self.devices_list.setViewportMargins(0, 0, 0, 0)
        self.devices_list.setMinimumHeight(total_height)
        self.devices_list.setMaximumHeight(total_height)
        if hasattr(self, "_scroll_area") and self._scroll_area:
            self._scroll_area.setVerticalScrollBarPolicy(_Qt.ScrollBarAsNeeded)
        # 如果当前不可见，标记下次显示时重算（冗余保护）
        if not self.isVisible():
            self._layout_dirty = True
    
    def _update_window_title(self):
        """更新窗口标题，显示在线设备数量（包括自己的其他设备）"""
        try:
            if not hasattr(self, 'devices_list'):
                return
            
            # 统计设备列表数量（包括"你自己"和其他设备）
            device_count = self.devices_list.count()
            
            # 更新窗口标题
            if device_count > 0:
                title = f"隔空投送({device_count}设备在线)"
            else:
                title = "隔空投送"
            
            self.setWindowTitle(title)
        except Exception:
            # 静默失败，不干扰主程序
            pass

    def _fade_in_widget(self, widget: QWidget, duration: int = 200):
        """淡入显示新加入的设备卡片"""
        try:
            effect = widget.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(widget)
                effect.setOpacity(0.0)
                widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", widget)
            anim.setDuration(duration)
            anim.setStartValue(effect.opacity())
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._fade_animations.append(anim)
            def on_finished():
                try:
                    effect.setOpacity(1.0)
                    if anim in self._fade_animations:
                        self._fade_animations.remove(anim)
                except Exception:
                    pass
            anim.finished.connect(on_finished)
            anim.start()
        except Exception:
            pass
    
    def _on_device_removed(self, user_id: str, ip: str, device_name: str):
        """设备移除（通过 user_id + ip 唯一标识，支持"自己"设备的正确匹配）"""
        _debug_log(f"[UI] Device removed from AirDropView: {device_name} (user_id={user_id}, ip={ip})")
        # 使用 user_id + ip 作为唯一标识来匹配设备，而不是使用 device_name
        # 因为"自己"的设备在 UI 中显示为"你自己"，但传入的 device_name 是原始名称
        device_unique_id = f"{user_id}::{ip}"
        removed = False
        for i in range(self.devices_list.count() - 1, -1, -1):  # 倒序遍历，避免索引问题
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                widget_unique_id = self._get_device_unique_id(widget.device)
                if widget_unique_id == device_unique_id:
                    self.devices_list.takeItem(i)
                    removed = True
                    _debug_log(f"[UI] Removed device: {widget_unique_id}")
        
        if removed:
            # 更新所有 item 的宽度（根据新的设备数量重新计算排列方式）
            self._update_item_widths()
            # 调整 QListWidget 大小
            self._adjust_devices_list_size()
            # 更新窗口标题（显示在线人数）
            self._update_window_title()
    
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
        colors = self._get_theme_colors()
        self._set_device_status(device, "等待中...", colors['status_waiting'])
        self._start_wait_countdown(device, 60)
        
        def send_in_thread():
            try:
                result = self._transfer_manager.send_transfer_request(file_path, device)
                if result.get("success"):
                    request_id = result.get("request_id")
                    self._wait_and_transfer(file_path, device, request_id)
                else:
                    msg = result.get("message", "请求失败")
                    self._post_to_ui_thread(lambda m=msg: self._handle_send_request_failure(device, m))
            except Exception as e:
                self._post_to_ui_thread(lambda m=str(e): self._handle_send_request_failure(device, m))
        
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
            # 不要在 Python Thread 里 emit Qt signal（Win11/Qt6 下可能直接 0xc0000005），统一投递到 UI 线程执行
            self._post_to_ui_thread(
                lambda res=result, fp=str(file_path), dn=device.name, dip=device.ip, dport=device.port, rid=request_id:
                self._on_transfer_request_result_signal(res, fp, dn, dip, dport, rid)
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
        self._stop_wait_countdown()
        if result.get("success") and result.get("accepted"):
            self._set_device_status(device, None)
            self._transfer_file(file_path, device, request_id)
            return
        
        # 处理拒绝或失败的情况
        self._transferring = False
        self.status_label.setVisible(False)
        colors = self._get_theme_colors()
        
        # 如果明确是拒绝（success=True但accepted=False），显示"已拒绝"
        if result.get("success") and not result.get("accepted"):
            self._set_device_status(device, "已拒绝", colors['status_error'])
            message = result.get("message", "已拒绝")
            Toast.show_message(self, f"对方: {message}")
        else:
            # 其他情况（超时、失败等）
            message = result.get("message", "请求失败")
            self._set_device_status(device, message, colors['status_error'])
            Toast.show_message(self, f"传输失败: {message}")
        
        self._current_target = None
    
    def _transfer_file(self, file_path: Path, device: DeviceInfo, request_id: str):
        """传输文件"""
        self.status_label.setVisible(False)
        self._set_device_status(device, None)

        # 重要：不要把 UI 回调（会操作 Qt 控件）传入后台线程执行。
        # TransferManager 内部会安全地把进度通过 Qt signal 投递回 UI 线程（transfer_progress），
        # 这里不再额外传 on_progress，避免 Win11/Qt6 下 Qt6Core.dll 0xc0000005。
        self._transfer_manager.send_file_after_confirm(
            file_path=file_path,
            target_device=device,
            request_id=request_id
        )
    
    def _on_transfer_request_received(self, request_id: str, sender_name: str, sender_id: str,
                                     filename: str, file_size: int, sender_ip: str = "", sender_port: int = 8765):
        """收到传输请求"""
        _debug_log(f"收到传输请求: request_id={request_id}, sender_ip={sender_ip}, sender_port={sender_port}")
        # 临时显示发送方（即便对方不在可见列表），对齐苹果的“临时可见”体验
        if sender_ip:
            device_name_resolved = self._resolve_device_name(sender_id or "", sender_ip, sender_name)
            temp_device = DeviceInfo(
                name=sender_name,
                user_id=sender_id or "",
                ip=sender_ip,
                port=sender_port,
                avatar_url=None,
                device_name=device_name_resolved,
                group_id=None,
                discover_scope="all"  # 临时视为可见
            )
            self._add_temp_device_for_request(temp_device)
        is_clipboard = (
            filename.startswith('clipboard_')
            or filename.startswith('clipboard_image_')
            or filename.startswith('clipboard_img-')
        )
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
            'auto_expired': False,
            'received_at': time.time()
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
        is_clipboard = request_info.get(
            'is_clipboard',
            filename.startswith('clipboard_') or filename.startswith('clipboard_image_') or filename.startswith('clipboard_img-')
        )
        is_clipboard_image = request_info.get('is_clipboard_image', False)
        
        # 预取发送端标识（用于临时可见清理）
        sender_id_local = request_info.get('sender_id', '')
        sender_ip_local = request_info.get('sender_ip', '')
        
        bubble = TransferRequestBubble(
            sender_name=request_info['sender_name'],
            filename=request_info['filename'],
            file_size=request_info['file_size'],
            parent=None,
            is_clipboard=is_clipboard,
            is_clipboard_image=is_clipboard_image
        )
        request_info['dialog'] = bubble
        bubble.destroyed.connect(lambda _=None, rid=request_id: self._on_request_dialog_closed(rid))
        
        def on_accept_clipboard(paste_to_clipboard: bool, open_after: bool = False):
            try:
                if not self._transfer_manager:
                    Toast.show_message(self, "传输服务未初始化")
                    return
                
                if request_id not in self._pending_requests:
                    Toast.show_message(self, "请求不存在，请让发送方重新发送")
                    return
                
                req_local = self._pending_requests[request_id]
                if req_local.get('accepted', False):
                    return
                
                sender_ip = req_local.get('sender_ip', '')
                sender_port = req_local.get('sender_port', 8765)
                if not sender_ip:
                    Toast.show_message(self, "无法获取发送端信息，请让发送方重新发送")
                    return
                
                if self._transfer_manager._server:
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
                                'file_size': req_local.get('file_size', 0)
                            }
                
                req_local['accepted'] = True
                req_local['paste_to_clipboard'] = paste_to_clipboard
                req_local['open_after_accept'] = open_after
                bubble.close()
            except Exception as e:
                Toast.show_message(self, f"接受失败: {e}")
        
        def on_clipboard_rejected():
            auto_expired = False
            if request_id in self._pending_requests:
                auto_expired = self._pending_requests[request_id].get('auto_expired', False)
            if self._transfer_manager and self._transfer_manager._server and not auto_expired:
                self._transfer_manager._server.confirm_transfer(request_id, False)
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]
            # 拒绝后移除临时设备
            self._remove_temp_device_by_sender(sender_id_local, sender_ip_local)
            bubble.close()
        
        if is_clipboard:
            if is_clipboard_image:
                # 图片：接受并打开（主按钮），或拒绝
                bubble.accepted_open.connect(lambda: on_accept_clipboard(False, True))
            else:
                bubble.accepted.connect(lambda: on_accept_clipboard(True, False))  # 放入剪贴板
                bubble.save_as_file.connect(lambda: on_accept_clipboard(False, False))
            bubble.rejected.connect(on_clipboard_rejected)
            self._position_request_bubble(bubble, request_info)
            bubble.show()
            bubble.bring_to_front()
            return
        
        # 普通文件：使用同一气泡（非剪贴板）
        file_bubble = bubble  # 已创建的 bubble，但 is_clipboard=False
        
        def on_accept_common(open_after: bool = False):
            try:
                if not self._transfer_manager:
                    _debug_log("TransferManager 未初始化")
                    Toast.show_message(self, "传输服务未初始化")
                    return
                
                # 直接从UI层的_pending_requests获取请求信息（包含sender_ip和sender_port）
                if request_id not in self._pending_requests:
                    if self._transfer_manager and self._transfer_manager._server:
                        with self._transfer_manager._server._lock:
                            server_request = self._transfer_manager._server._pending_requests.get(request_id)
                            if server_request and server_request.get('status') == 'accepted':
                                return
                    Toast.show_message(self, "请求不存在，请让发送方重新发送")
                    return
                
                # 检查是否已经接受过
                request_info_local = self._pending_requests[request_id]
                if request_info_local.get('accepted', False):
                    return
                
                sender_ip = request_info_local.get('sender_ip', '')
                sender_port = request_info_local.get('sender_port', 8765)
                
                if not sender_ip:
                    Toast.show_message(self, "无法获取发送端信息，请让发送方重新发送")
                    return
                
                if self._transfer_manager._server:
                    with self._transfer_manager._server._lock:
                        if request_id in self._transfer_manager._server._pending_requests:
                            self._transfer_manager._server._pending_requests[request_id]['status'] = 'accepted'
                        else:
                            self._transfer_manager._server._pending_requests[request_id] = {
                                'status': 'accepted',
                                'timestamp': time.time(),
                                'sender_ip': sender_ip,
                                'sender_port': sender_port,
                                'filename': request_info_local.get('filename', 'unknown'),
                                'file_size': request_info_local.get('file_size', 0)
                            }
                
                if request_id in self._pending_requests:
                    self._pending_requests[request_id]['accepted'] = True
                    self._pending_requests[request_id]['open_after_accept'] = open_after
                # 接受后暂不移除，等待文件收完或超时清理
                file_bubble.close()
            except Exception as e:
                import traceback
                traceback.print_exc()
                Toast.show_message(self, f"接受请求失败: {e}")
        
        def on_rejected():
            auto_expired = False
            if request_id in self._pending_requests:
                auto_expired = self._pending_requests[request_id].get('auto_expired', False)
            if self._transfer_manager and self._transfer_manager._server and not auto_expired:
                self._transfer_manager._server.confirm_transfer(request_id, False)
            if request_id in self._pending_requests:
                req_info_local = self._pending_requests[request_id]
                self._remove_temp_device_by_sender(req_info_local.get('sender_id', ""), req_info_local.get('sender_ip', ""))
                del self._pending_requests[request_id]
            file_bubble.close()
        
        file_bubble.accepted.connect(lambda: on_accept_common(False))
        file_bubble.accepted_open.connect(lambda: on_accept_common(True))
        file_bubble.rejected.connect(on_rejected)
        
        self._position_request_bubble(file_bubble, request_info)
        file_bubble.show()
        file_bubble.bring_to_front()
    
    def _cleanup_accepted_request(self, request_id: str):
        """清理已接受的请求（在文件接收完成时调用）"""
        if request_id in self._pending_requests:
            del self._pending_requests[request_id]
            logger.debug(f"已从UI层删除请求: {request_id}")
    
    def _on_transfer_progress(self, target_name: str, uploaded: int, total: int):
        """传输进度更新"""
        if self._current_target and hasattr(self._current_target, 'name') and target_name == self._current_target.name:
            progress = int((uploaded / total) * 100) if total > 0 else 0
            self.status_label.setVisible(False)
            # 更新设备项的头像进度条
            try:
                for i in range(self.devices_list.count()):
                    item = self.devices_list.item(i)
                    if item is None:
                        continue
                    widget = self.devices_list.itemWidget(item)
                    if (isinstance(widget, DeviceItemWidget) and 
                        hasattr(widget, 'device') and widget.device is not None and
                        hasattr(widget.device, 'name') and widget.device.name == target_name):
                        widget.set_progress(progress)
                        break
            except Exception as e:
                logger.error(f"更新传输进度时出错: {e}", exc_info=True)
    
    def _on_transfer_completed(self, target_name: str, success: bool, message: str):
        """传输完成"""
        try:
            logger.info(f"[AirDropView] 传输完成: target_name={target_name}, success={success}, message={message}")
            self._transferring = False
            
            self.status_label.setVisible(False)
            
            # 清除设备项的头像进度条，并记录传输时间
            current_device = self._current_target
            target_user_id = None
            target_device = None
            try:
                for i in range(self.devices_list.count()):
                    item = self.devices_list.item(i)
                    if item is None:
                        continue
                    widget = self.devices_list.itemWidget(item)
                    if (isinstance(widget, DeviceItemWidget) and 
                        hasattr(widget, 'device') and widget.device is not None and
                        hasattr(widget.device, 'name') and widget.device.name == target_name):
                        widget.set_progress(0)
                        widget.set_device_status(None)
                        if hasattr(widget.device, 'user_id'):
                            target_user_id = widget.device.user_id
                        target_device = widget.device
                        break
            except Exception as e:
                logger.error(f"处理传输完成时查找设备失败: {e}", exc_info=True)
            
            if success and target_user_id and target_device:
                try:
                    # 记录传输时间（按 user_id 记录，同一账号的所有设备共享传输时间）
                    import time
                    self._device_transfer_times[target_user_id] = time.time()
                    # 传输成功后延迟重新排序该账号的所有设备（因为排序是基于 user_id 的）
                    # 使用 QTimer.singleShot 延迟执行，避免在 Qt 布局更新过程中操作列表导致崩溃
                    QTimer.singleShot(100, lambda uid=target_user_id: self._reorder_devices_by_user_id(uid))
                    Toast.show_message(self, f"文件已成功发送到 {target_name}")
                except Exception as e:
                    logger.error(f"处理传输成功时出错: {e}", exc_info=True)
                    Toast.show_message(self, f"文件已成功发送到 {target_name}")
            else:
                Toast.show_message(self, f"发送失败: {message}")
            
            self._current_target = None
        except Exception as e:
            logger.error(f"[AirDropView] 传输完成回调发生未捕获异常: {e}", exc_info=True)
            # 确保即使发生异常也不会导致应用退出
            import traceback
            logger.error(f"[AirDropView] 异常堆栈: {traceback.format_exc()}")
    
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
            try:
                for i in range(self.devices_list.count()):
                    item = self.devices_list.item(i)
                    if item is None:
                        continue
                    widget = self.devices_list.itemWidget(item)
                    if (isinstance(widget, DeviceItemWidget) and 
                        hasattr(widget, 'device') and widget.device is not None and
                        hasattr(widget.device, 'user_id') and widget.device.user_id == sender_id):
                        widget.set_progress(progress)
                        break
            except Exception as e:
                logger.error(f"更新接收进度时出错: {e}", exc_info=True)
        else:
            # 如果请求不在_pending_requests中，使用默认值继续更新进度
            progress = int((received / total) * 100) if total > 0 else 0
            self.status_label.setVisible(False)
    
    def _on_file_received(self, save_path: str, file_size: int, original_filename: str):
        """文件接收完成"""
        save_path = Path(save_path)
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
        open_after_accept = False
        for req_id, req_info in self._pending_requests.items():
            if (req_info.get('filename') == original_filename and 
                req_info.get('accepted', False) and
                req_info.get('file_size', 0) == file_size):
                request_ids_to_remove.append(req_id)
                sender_id = req_info.get('sender_id')
                sender_ip = req_info.get('sender_ip', "")
                if sender_id:
                    sender_ids_to_reset.add(sender_id)
                if req_info.get('paste_to_clipboard', False):
                    paste_to_clipboard = True
                if req_info.get('is_clipboard', False):
                    is_clipboard_request = True
                if req_info.get('is_clipboard_image', False):
                    is_clipboard_image = True
                    clipboard_image_format = clipboard_image_format or req_info.get('clipboard_image_format')
                if req_info.get('open_after_accept', False):
                    open_after_accept = True
                # 清理临时可见设备
                key = f"{sender_id or ''}::{sender_ip or ''}"
                self._remove_temp_device_by_key(key)
        
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
        
        # 如果用户选择了"接受并打开"，则打开文件（非剪贴板请求）
        if open_after_accept and not is_clipboard_request:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(save_path)))
                logger.info(f"已打开文件: {save_path}")
            except Exception as e:
                logger.error(f"打开文件失败: {e}")
                Toast.show_message(self, f"无法打开文件: {save_path.name}")
        
        for req_id in request_ids_to_remove:
            del self._pending_requests[req_id]
        
        self._reset_device_progress(sender_ids_to_reset)
    
    def _refresh_devices(self):
        """刷新设备列表"""
        if not self._transfer_manager:
            return
        
        # 使用 user_id + ip 作为唯一标识，支持同一账号多个设备
        current_devices = {self._get_device_unique_id(d) for d in self._transfer_manager.get_devices()}
        _debug_log(f"[UI] _refresh_devices: current_devices={current_devices}, list_count={self.devices_list.count()}")
        
        # 检查是否有新设备需要添加
        existing_device_ids = set()
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                device_unique_id = self._get_device_unique_id(widget.device)
                existing_device_ids.add(device_unique_id)
        
        # 添加新发现的设备
        for device in self._transfer_manager.get_devices():
            device_unique_id = self._get_device_unique_id(device)
            if device_unique_id not in existing_device_ids:
                if not self._is_device_visible(device):
                    _debug_log(f"[UI] Skip device by scope: {device_unique_id}, scope={getattr(device, 'discover_scope', None)}")
                    continue
                _debug_log(f"[UI] _refresh_devices: Adding new device {device_unique_id}")
                # 记录设备发现时间
                import time
                if device.user_id not in self._device_discovery_times:
                    self._device_discovery_times[device.user_id] = time.time()
                # 记录设备携带的组信息（每个客户端自行广播，无需单独请求接口）
                self._update_device_group_cache(device, reorder_if_changed=True)
                # 添加设备
                self._add_device_widget_at_sorted_position(device)
        
        # 移除不存在的设备
        for i in range(self.devices_list.count() - 1, -1, -1):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if isinstance(widget, DeviceItemWidget):
                device_unique_id = self._get_device_unique_id(widget.device)
                if device_unique_id not in current_devices:
                    _debug_log(f"[UI] _refresh_devices: Removing device {device_unique_id}")
                    self.devices_list.takeItem(i)
        
        # 刷新后调整大小
        QTimer.singleShot(0, self._update_item_widths)
        QTimer.singleShot(0, self._adjust_devices_list_size)
    
    def _reorder_devices_by_user_id(self, user_id: str):
        """
        重新排序指定 user_id 的所有设备（传输后需要移动到前面）。

        重要：在 Windows/Qt6 上，QListWidget + setItemWidget 的组合如果通过 takeItem/insertItem
        复用旧的 QWidget，非常容易触发 Qt6Core.dll 0xc0000005（C++ 侧 use-after-free）。
        这里改为“先移除旧项，再按最新排序规则重新创建 widget 插回”，稳定优先。
        """
        if not user_id:
            return

        if not hasattr(self, "devices_list") or self.devices_list is None:
            return

        try:
            rows: list[int] = []
            devices: list[DeviceInfo] = []

            # 收集该账号的所有设备（保持原顺序）
            for i in range(self.devices_list.count()):
                item = self.devices_list.item(i)
                if item is None:
                    continue
                widget = self.devices_list.itemWidget(item)
                if not isinstance(widget, DeviceItemWidget):
                    continue
                try:
                    dev = widget.device
                except RuntimeError:
                    continue
                if getattr(dev, "user_id", None) == user_id:
                    rows.append(i)
                    devices.append(dev)

            if not devices:
                return

            was_updates_enabled = self.devices_list.updatesEnabled()
            try:
                self.devices_list.setUpdatesEnabled(False)

                # 移除旧项（倒序避免索引变化）
                for row in reversed(rows):
                    with contextlib.suppress(Exception):
                        self.devices_list.takeItem(row)

                # 重新插入（内部会根据 _get_device_sort_key 计算正确位置）
                for dev in devices:
                    with contextlib.suppress(Exception):
                        self._add_device_widget_at_sorted_position(dev)
            finally:
                self.devices_list.setUpdatesEnabled(was_updates_enabled)

            # 延迟刷新，避开 paint/layout 进行中窗口
            QTimer.singleShot(0, self._update_item_widths)
            QTimer.singleShot(0, self._adjust_devices_list_size)
            QTimer.singleShot(0, self._update_window_title)
        except Exception as e:
            logger.error(f"重新排序设备失败 (user_id={user_id}): {e}", exc_info=True)
    
    def _reorder_device(self, user_id: str):
        """重新排序单个设备（兼容方法，实际调用 _reorder_devices_by_user_id）"""
        self._reorder_devices_by_user_id(user_id)
    
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

    def _start_wait_countdown(self, device: DeviceInfo, seconds: int = 60):
        """启动“等待中”倒计时并更新设备状态文本"""
        self._stop_wait_countdown()
        self._wait_countdown_device = device
        self._wait_countdown_remaining = seconds
        colors = self._get_theme_colors()
        self._set_device_status(device, f"等待中({self._wait_countdown_remaining})...", colors['status_waiting'])
        
        timer = QTimer(self)
        timer.setInterval(1000)
        
        def tick():
            self._wait_countdown_remaining -= 1
            remaining = max(self._wait_countdown_remaining, 0)
            self._set_device_status(device, f"等待中({remaining})...", colors['status_waiting'])
            if remaining <= 0:
                self._stop_wait_countdown()
                # 超时仍未确认，重置传输状态并提示
                self._transferring = False
                self.status_label.setVisible(False)
                if self._current_target and getattr(self._current_target, "ip", None) == device.ip:
                    self._current_target = None
                timeout_msg = "等待对方响应超时"
                self._set_device_status(device, timeout_msg, colors.get('status_error'))
                Toast.show_message(self, timeout_msg)
        
        timer.timeout.connect(tick)
        timer.start()
        self._wait_countdown_timer = timer

    def _stop_wait_countdown(self):
        """停止倒计时"""
        if self._wait_countdown_timer:
            self._wait_countdown_timer.stop()
            self._wait_countdown_timer.deleteLater()
        self._wait_countdown_timer = None
        self._wait_countdown_device = None
        self._wait_countdown_remaining = 0
    
    def _handle_send_request_failure(self, device: DeviceInfo, message: str):
        """发送请求失败时的统一处理"""
        self._transferring = False
        self.status_label.setVisible(False)
        self._stop_wait_countdown()
        
        # 更友好的错误提示
        friendly = message
        if "actively refused" in message or "actively refused" in message.lower():
            friendly = "对方可能已离线或拒绝连接"
        elif "timeout" in message.lower() or "timed out" in message.lower():
            friendly = "连接超时，对方可能已离线"
        
        try:
            logger.info(f"[AirDrop] send_request_failure: user_id={getattr(device,'user_id',None)}, ip={getattr(device,'ip',None)}, raw='{message}'")
        except Exception:
            pass
        
        colors = self._get_theme_colors()
        self._set_device_status(device, friendly, colors.get('status_error'))
        Toast.show_message(self, friendly)
        
        # 尝试触发一次刷新，更新设备在线状态
        QTimer.singleShot(0, self._refresh_devices)
        
        # 主动告知发现模块该设备不可达，加速离线清理
        try:
            if self._transfer_manager and getattr(self._transfer_manager, "_discovery", None):
                logger.info(f"[AirDrop] mark_unreachable for user_id={device.user_id}, ip={device.ip}")
                self._transfer_manager._discovery.mark_unreachable(device.user_id, device.ip)
        except Exception:
            pass

    def _cleanup_transfer_manager(self, stop_manager: bool = True):
        """统一清理传输管理器资源"""
        self._stop_wait_countdown()
        if stop_manager and self._transfer_manager:
            try:
                self._transfer_manager.stop()
            except Exception:
                pass
    
    def moveEvent(self, event):
        """窗口移动时，重新定位悬浮气泡"""
        super().moveEvent(event)
        self._reposition_all_bubbles()
    
    def resizeEvent(self, event):
        """窗口尺寸变更时，重新定位悬浮气泡"""
        super().resizeEvent(event)
        self._reposition_all_bubbles()
    
    def _find_device_widget_for_request(self, request_info: dict) -> Optional[DeviceItemWidget]:
        """根据请求信息查找对应头像的设备项"""
        sender_id = request_info.get('sender_id') or ""
        sender_name = request_info.get('sender_name') or ""
        sender_ip = request_info.get('sender_ip') or ""
        sender_port = request_info.get('sender_port')
        
        for i in range(self.devices_list.count()):
            item = self.devices_list.item(i)
            widget = self.devices_list.itemWidget(item)
            if not isinstance(widget, DeviceItemWidget):
                continue
            
            # 优先匹配 user_id
            if sender_id and widget.device.user_id == sender_id:
                return widget
            
            # 其次匹配名字
            if sender_name and widget.device.name == sender_name:
                return widget
            
            # 再匹配 IP 和端口
            if sender_ip and widget.device.ip == sender_ip:
                if sender_port is None or widget.device.port == sender_port:
                    return widget
        
        return None
    
    def _scroll_to_device_widget(self, widget: QWidget, margin: int = 24):
        """滚动确保指定设备卡片可见"""
        if not widget or not hasattr(self, "_scroll_area") or not self._scroll_area:
            return
        try:
            self._scroll_area.ensureWidgetVisible(widget, margin, margin)
            return
        except Exception:
            pass
        try:
            vbar = self._scroll_area.verticalScrollBar()
            if vbar:
                target_y = widget.mapTo(self._scroll_area.widget(), QPoint(0, 0)).y()
                vbar.setValue(max(0, target_y - margin))
        except Exception:
            pass
    
    def _focus_first_pending_request(self):
        """
        当窗口重新显示时，将视图滚动到最早的未处理请求并重新显示气泡。
        """
        # 动画未完成时稍后重试，避免定位不准
        if getattr(self, "_is_showing_animation", False):
            QTimer.singleShot(200, self._focus_first_pending_request)
            return
        
        if not self._pending_requests:
            return
        
        # 过滤出仍未处理的请求，按收到时间排序
        candidates = []
        for rid, info in self._pending_requests.items():
            if info.get("accepted") or info.get("auto_expired"):
                continue
            received_at = info.get("received_at", 0)
            candidates.append((received_at, rid))
        if not candidates:
            return
        
        candidates.sort(key=lambda x: x[0])
        target_request_id = candidates[0][1]
        
        def do_focus():
            req_info = self._pending_requests.get(target_request_id)
            if not req_info or req_info.get("accepted") or req_info.get("auto_expired"):
                return
            
            # 确保窗口已激活，避免气泡被遮挡
            try:
                self.raise_()
                self.activateWindow()
            except Exception:
                pass
            
            # 确保对应设备卡片在可视区域
            target_widget = self._find_device_widget_for_request(req_info)
            if target_widget:
                self._scroll_to_device_widget(target_widget)
            
            # 确保气泡存在并可见
            dialog = req_info.get("dialog")
            if not isinstance(dialog, TransferRequestBubble):
                self._show_confirm_dialog(target_request_id)
                dialog = self._pending_requests.get(target_request_id, {}).get("dialog")
            elif not dialog.isVisible():
                dialog.show()
            
            if isinstance(dialog, TransferRequestBubble):
                self._position_request_bubble(dialog, self._pending_requests[target_request_id], retry=True)
                dialog.bring_to_front()
                # 再次提升所有气泡，避免被窗口覆盖
                self._bring_all_bubbles_to_front()
                # 再次激活窗口，确保无需点击即可看到
                try:
                    self.raise_()
                    self.activateWindow()
                except Exception:
                    pass
        
        # 略微延迟，等待布局稳定后再滚动定位
        QTimer.singleShot(120, do_focus)
    
    def _position_request_bubble(self, bubble: TransferRequestBubble, request_info: dict, retry: bool = True):
        """将气泡定位在发送者头像附近；若主窗口隐藏到边缘，则固定到屏幕右上角"""
        margin = 0  # 减小间距，让浮窗更靠近头像
        
        # 如果窗口已隐藏到屏幕边缘或当前不可见，固定到屏幕角落提示
        if self._was_hidden_to_icon or not self.isVisible():
            target_screen = None
            if self._hidden_rect:
                target_screen = QGuiApplication.screenAt(self._hidden_rect.center())
            if not target_screen:
                target_screen = QGuiApplication.primaryScreen()
            if bubble.windowHandle() and target_screen:
                bubble.windowHandle().setScreen(target_screen)
            if target_screen:
                bubble.lock_size_for_screen(target_screen)

            # 平台区分：Windows → 右上角；macOS → 右下角
            try:
                import sys
                is_macos = sys.platform == "darwin"
            except Exception:
                is_macos = False

            # 尺寸可能在未显示前为0，使用 sizeHint 兜底
            w = bubble.width() or bubble.sizeHint().width()
            h = bubble.height() or bubble.sizeHint().height()

            if target_screen:
                geo = target_screen.availableGeometry()
                x = geo.right() - w - margin
                if is_macos:
                    y = geo.bottom() - h - margin
                else:
                    y = geo.top() + margin
            else:
                x = margin
                y = margin

            bubble.move(x, y)
            bubble.set_pointer_visible(False)
            bubble.bring_to_front()
            if retry:
                QTimer.singleShot(120, lambda: self._position_request_bubble(bubble, request_info, retry=False))
            return
        
        # 主窗口可见：贴合头像
        # 拖拽过程中不显示气泡，等拖拽结束（位置稳定）再定位
        if getattr(self, "_is_dragging", False):
            bubble.hide()
            # 拖动期间不设置 retry，避免定时器重新调用导致气泡显示
            # 拖动结束后会通过 _reposition_all_bubbles 重新定位
            return
        else:
            bubble.show()
        
        target_widget = self._find_device_widget_for_request(request_info)
        screen_point = None
        if target_widget and hasattr(target_widget, "avatar_label"):
            avatar = target_widget.avatar_label
            center = avatar.mapToGlobal(avatar.rect().center())
            top = avatar.mapToGlobal(avatar.rect().topLeft()).y()
            x = int(center.x() - bubble.width() / 2)
            y = int(top - bubble.height() - margin)
            screen_point = center
        else:
            center = self.mapToGlobal(self.rect().center())
            x = int(center.x() - bubble.width() / 2)
            y = int(center.y() - bubble.height() / 2)
            screen_point = center
        
        screen = QGuiApplication.screenAt(screen_point) if screen_point else QGuiApplication.primaryScreen()
        if not screen:
            screen = QGuiApplication.primaryScreen()
        if bubble.windowHandle() and screen:
            bubble.windowHandle().setScreen(screen)
        if screen:
            bubble.lock_size_for_screen(screen)
        if screen:
            geo = screen.availableGeometry()
            x = max(geo.left() + 8, min(x, geo.right() - bubble.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - bubble.height() - 8))
        bubble.move(x, y)
        bubble.set_pointer_visible(True)
        bubble.bring_to_front()
        
        # 布局可能尚未完成，稍后再尝试一次以提升准确性，同时避免多次锁尺寸
        if retry:
            QTimer.singleShot(120, lambda: self._position_request_bubble(bubble, request_info, retry=False))
    
    def _reposition_all_bubbles(self):
        """窗口移动/尺寸变化时，重新定位所有悬浮气泡"""
        for req_id, info in self._pending_requests.items():
            dialog = info.get('dialog')
            if isinstance(dialog, TransferRequestBubble):
                self._position_request_bubble(dialog, info, retry=False)
    
    def _hide_all_bubbles(self):
        """临时隐藏所有气泡（拖拽时使用）"""
        for req_id, info in self._pending_requests.items():
            dialog = info.get('dialog')
            if isinstance(dialog, TransferRequestBubble) and dialog.isVisible():
                dialog.hide()
    
    def _bring_all_bubbles_to_front(self):
        """将所有气泡提升到最上层"""
        for req_id, info in self._pending_requests.items():
            dialog = info.get('dialog')
            if isinstance(dialog, TransferRequestBubble) and dialog.isVisible():
                dialog.bring_to_front()

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
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False
    
    def _check_and_update_theme(self):
        """检查主题变化并更新UI"""
        is_dark = self._detect_theme()
        if is_dark != self._is_dark:
            self._is_dark = is_dark
            self._update_theme_colors()
    
    def _get_theme_colors(self) -> dict:
        """获取当前主题的颜色配置"""
        if self._is_dark:
            return {
                "bg_primary": "#1C1C1E",  # 主背景色
                "bg_secondary": "#2C2C2E",  # 次要背景色
                "bg_card": "#2C2C2E",  # 卡片背景色
                "bg_hover": "#3A3A3C",  # 悬停背景色
                "text_primary": "#FFFFFF",  # 主文字色
                "text_secondary": "#EBEBF5",  # 次要文字色
                "text_tertiary": "#9a9ab1",  # 第三级文字色
                "border": "#38383A",  # 边框色
                "border_light": "#48484A",  # 浅边框色
                "button_primary_bg": "#0A84FF",  # 主按钮背景
                "button_primary_hover": "#006FE0",  # 主按钮悬停
                "button_primary_pressed": "#005BB8",  # 主按钮按下
                "button_secondary_bg": "#2C2C2E",  # 次要按钮背景
                "button_secondary_border": "#38383A",  # 次要按钮边框
                "button_secondary_hover": "#3A3A3C",  # 次要按钮悬停
                "button_secondary_pressed": "#48484A",  # 次要按钮按下
                "item_border": "#1C1C1E",  # 列表项边框（深色主题下使用更亮的边框）
                "avatar_bg": "#AAAAAA",  # 头像背景
                "signal_icon": "#FFFFFF",  # 信号图标颜色
                "status_waiting": "#EBEBF599",  # 等待状态颜色
                "status_error": "#FF453A",  # 错误状态颜色
            }
        else:
            return {
                "bg_primary": "#FFFFFF",  # 主背景色
                "bg_secondary": "#F2F2F7",  # 次要背景色
                "bg_card": "#F9F9F9",  # 卡片背景色
                "bg_hover": "#E5E5EA",  # 悬停背景色
                "text_primary": "#000000",  # 主文字色
                "text_secondary": "#111111",  # 次要文字色
                "text_tertiary": "#8E8E93",  # 第三级文字色
                "border": "#D1D1D6",  # 边框色
                "border_light": "#E5E5EA",  # 浅边框色
                "button_primary_bg": "#0A84FF",  # 主按钮背景
                "button_primary_hover": "#006FE0",  # 主按钮悬停
                "button_primary_pressed": "#005BB8",  # 主按钮按下
                "button_secondary_bg": "#F2F2F7",  # 次要按钮背景
                "button_secondary_border": "#D1D1D6",  # 次要按钮边框
                "button_secondary_hover": "#E5E5EA",  # 次要按钮悬停
                "button_secondary_pressed": "#D8D8DC",  # 次要按钮按下
                "item_border": "#ffffff",  # 列表项边框（亮色主题下使用标准边框色）
                "avatar_bg": "#E0E0E0",  # 头像背景
                "signal_icon": "#000000",  # 信号图标颜色
                "status_waiting": "#8E8E93",  # 等待状态颜色
                "status_error": "#FF3B30",  # 错误状态颜色
            }
    
    def _update_theme_colors(self):
        """更新所有UI元素的颜色以适配当前主题"""
        colors = self._get_theme_colors()
        
        # 更新主窗口背景
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {colors['bg_primary']};
            }}
            QLabel {{
                color: {colors['text_primary']};
            }}
        """)
        
        # 更新滚动区域背景
        if hasattr(self, '_scroll_area'):
            scroll_widget = self._scroll_area.widget()
            if scroll_widget:
                scroll_widget.setStyleSheet(f"background-color: {colors['bg_primary']};")
        
        # 更新设备列表样式
        if hasattr(self, 'devices_list'):
            self.devices_list.setStyleSheet(f"""
                QListWidget {{
                    border: none;
                    background-color: {colors['bg_primary']};
                }}
                QListWidget::item {{
                    border: 1px solid {colors['item_border']};
                    border-radius: 8px;
                    background-color: transparent;
                    padding: 0px;
                    text-align: center;
                }}
            """)
        
        # 更新背景标签颜色
        if hasattr(self, '_background_label'):
            self._background_label.setStyleSheet(f"color: {colors['text_tertiary']};")
        
        # 更新状态标签颜色
        if hasattr(self, 'status_label'):
            self.status_label.setStyleSheet(f"color: {colors['text_tertiary']}; font-size: 13px;")
        
        # 更新所有设备项的颜色
        if hasattr(self, 'devices_list'):
            for i in range(self.devices_list.count()):
                item = self.devices_list.item(i)
                widget = self.devices_list.itemWidget(item)
                if isinstance(widget, DeviceItemWidget):
                    widget._is_dark = self._is_dark
                    widget._update_theme_colors(colors)
                    # 重新绘制默认头像（如果有）
                    if hasattr(widget, '_device') and not widget.avatar_label.pixmap():
                        widget._set_default_avatar()
        
        # 更新信号图标颜色
        if hasattr(self, '_background_frame'):
            for child in self._background_frame.findChildren(QLabel):
                if child.pixmap():
                    # 重新着色图标
                    colors = self._get_theme_colors()
                    icon_color = QColor(255, 255, 255) if self._is_dark else QColor(0, 0, 0)
                    # 这里需要重新加载并着色图标，但为了简化，暂时跳过
                    pass
    
    def closeEvent(self, event):
        """关闭事件"""
        # 点击窗口关闭按钮时，仅隐藏窗口，保持传输服务运行
        event.ignore()
        self.hide()
