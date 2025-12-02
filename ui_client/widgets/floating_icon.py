#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面悬浮图标（类似 macOS 的悬浮球）
尺寸更小，更不抢眼
"""

from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from typing import Optional, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPixmap
from pathlib import Path
import platform


class FloatingIcon(QWidget):
    """桌面悬浮图标（小尺寸）"""
    
    clicked = Signal()  # 点击信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity_value = 0.4  # 默认半透明，不抢眼
        self._setup_ui()
        self._setup_position()
    
    def _get_opacity(self):
        """获取透明度（用于动画）"""
        return self._opacity_value
    
    def _set_opacity(self, value):
        """设置透明度（用于动画）"""
        self._opacity_value = max(0.0, min(1.0, value))
        self.update()
    
    opacity = property(_get_opacity, _set_opacity)
    
    def _setup_ui(self):
        """设置UI"""
        # 设置窗口属性
        self.setWindowFlags(
            Qt.Window |
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.X11BypassWindowManagerHint  # Linux
        )
        
        # 设置窗口大小（更小）
        self.setFixedSize(36, 36)
        
        # 设置背景透明
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 设置鼠标跟踪
        self.setMouseTracking(True)
        
        # 加载图标
        self._load_icon()
    
    def _load_icon(self):
        """加载图标"""
        app_dir = Path(__file__).parent.parent
        icon_paths = [
            app_dir / "resources" / "app_icon.png",
            app_dir / "resources" / "app_icon.icns",
        ]
        
        for icon_path in icon_paths:
            if icon_path.exists():
                pixmap = QPixmap(str(icon_path))
                if not pixmap.isNull():
                    # 缩放图标（更小）
                    scaled_pixmap = pixmap.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._icon_pixmap = scaled_pixmap
                    return
        
        # 如果没有图标，使用默认圆形
        self._icon_pixmap = None
    
    def _setup_position(self):
        """设置初始位置（屏幕右侧边缘）"""
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 8
        y = screen.height() // 2 - self.height() // 2
        self.move(x, y)
    
    def paintEvent(self, event):
        """绘制图标"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 绘制圆形背景（半透明）
        painter.setBrush(QBrush(QColor(0, 0, 0, int(80 * self._opacity_value))))
        painter.setPen(QPen(QColor(255, 255, 255, int(150 * self._opacity_value)), 1))
        painter.drawEllipse(2, 2, 32, 32)
        
        # 绘制图标
        if self._icon_pixmap:
            painter.drawPixmap(4, 4, self._icon_pixmap)
        else:
            # 绘制默认图标（小圆点）
            painter.setBrush(QBrush(QColor(255, 255, 255, int(200 * self._opacity_value))))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(14, 14, 8, 8)
    
    def enterEvent(self, event):
        """鼠标进入"""
        self._opacity_value = 1.0
        self.update()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        """鼠标离开"""
        self._opacity_value = 0.4
        self.update()
        super().leaveEvent(event)
    
    def mousePressEvent(self, event):
        """鼠标按下"""
        if event.button() == Qt.LeftButton:
            self._drag_start_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = False
    
    def mouseMoveEvent(self, event):
        """鼠标移动"""
        if event.buttons() == Qt.LeftButton:
            if not hasattr(self, '_is_dragging'):
                self._is_dragging = False
            
            # 检查是否开始拖拽
            if not self._is_dragging:
                delta = (event.globalPosition().toPoint() - self._drag_start_position - self.frameGeometry().topLeft()).manhattanLength()
                if delta > 5:
                    self._is_dragging = True
            
            if self._is_dragging:
                # 拖拽窗口
                new_pos = event.globalPosition().toPoint() - self._drag_start_position
                self._constrain_to_screen(new_pos)
                self.move(new_pos)
    
    def mouseReleaseEvent(self, event):
        """鼠标释放"""
        if event.button() == Qt.LeftButton:
            if hasattr(self, '_is_dragging') and not self._is_dragging:
                # 点击事件
                self.clicked.emit()
            self._is_dragging = False
    
    def _constrain_to_screen(self, pos: QPoint):
        """限制位置在屏幕内"""
        screen = QApplication.primaryScreen().geometry()
        
        # 自动吸附到边缘
        margin = 10
        if pos.x() + self.width() >= screen.right() - margin:
            pos.setX(screen.right() - self.width())
        elif pos.x() <= screen.left() + margin:
            pos.setX(screen.left())
        
        if pos.y() + self.height() >= screen.bottom() - margin:
            pos.setY(screen.bottom() - self.height())
        elif pos.y() <= screen.top() + margin:
            pos.setY(screen.top())
    
    def showEvent(self, event):
        """显示事件"""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
    
    def animate_show(self, from_pos: Optional[QPoint] = None):
        """动画显示（从指定位置出现）"""
        if from_pos:
            # 从指定位置开始（窗口隐藏的位置）
            self.move(from_pos)
            self._opacity_value = 0.0
        else:
            # 默认淡入
            self._opacity_value = 0.0
        
        self.show()
        self.setVisible(True)  # 确保可见
        self.update()
        
        animation = QPropertyAnimation(self, b"opacity")
        animation.setDuration(300)
        animation.setStartValue(0.0)
        animation.setEndValue(0.4)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start()
    
    def animate_hide(self):
        """动画隐藏"""
        animation = QPropertyAnimation(self, b"opacity")
        animation.setDuration(200)
        animation.setStartValue(self._opacity_value)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.InCubic)
        
        def on_finished():
            self.hide()
            self.setVisible(False)  # 确保隐藏
        
        animation.finished.connect(on_finished)
        animation.start()
