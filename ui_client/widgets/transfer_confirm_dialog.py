#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输确认对话框
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSizePolicy,
    QSpacerItem,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from pathlib import Path
import sys
import platform

from utils.theme_manager import ThemeManager
from utils.config_manager import ConfigManager


class TransferConfirmDialog(QDialog):
    """文件传输确认对话框"""
    
    accepted = Signal()  # 接受信号
    rejected = Signal()  # 拒绝信号
    
    def __init__(self, sender_name: str, filename: str, file_size: int, parent=None):
        """
        初始化确认对话框
        
        Args:
            sender_name: 发送者名称
            filename: 文件名
            file_size: 文件大小（字节）
        """
        super().__init__(parent)
        self._sender_name = sender_name
        self._filename = filename
        self._file_size = file_size
        self._open_after_accept = False  # 区分"接受并打开"与"接受"
        self._is_dark = self._detect_theme()
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        colors = self._get_theme_colors()
        self.setWindowTitle("文件传输请求")
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {colors['bg_primary']};
                border-radius: 12px;
            }}
            QLabel {{
                color: {colors['text_primary']};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        
        # 头部区域：头像 + 文案
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)
        
        avatar = self._create_avatar_label(self._sender_name)
        header_layout.addWidget(avatar, 0, Qt.AlignLeft | Qt.AlignTop)
        
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        
        colors = self._get_theme_colors()
        title = QLabel(f"{self._sender_name} 想向你发送")
        title.setFont(QFont("PingFang SC", 13, QFont.Medium))
        title.setStyleSheet(f"color: {colors['text_secondary']};")
        title_box.addWidget(title)
        
        filename_label = QLabel(self._filename)
        filename_label.setWordWrap(True)
        filename_label.setStyleSheet(f"color: {colors['text_secondary']}; font-size: 12px;")
        title_box.addWidget(filename_label)
        
        size_str = self._format_file_size(self._file_size)
        size_label = QLabel(size_str)
        size_label.setStyleSheet(f"color: {colors['text_tertiary']}; font-size: 11px;")
        title_box.addWidget(size_label)
        
        header_layout.addLayout(title_box)
        header_layout.addItem(QSpacerItem(10, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))
        layout.addLayout(header_layout)
        
        # 文件信息卡片
        colors = self._get_theme_colors()
        info_frame = QFrame()
        info_frame.setStyleSheet(f"""
            QFrame {{
                border: 1px solid {colors['border']};
                border-radius: 10px;
                background-color: {colors['bg_card']};
            }}
        """)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(10)
        
        badge = QLabel(self._file_ext_badge())
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignCenter)
        colors = self._get_theme_colors()
        badge.setStyleSheet(f"""
            QLabel {{
                background-color: {colors['avatar_bg']};
                color: {colors['text_secondary']};
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
        """)
        info_layout.addWidget(badge, 0, Qt.AlignTop)
        
        file_text_box = QVBoxLayout()
        file_text_box.setSpacing(4)
        
        colors = self._get_theme_colors()
        file_title = QLabel(self._filename)
        file_title.setWordWrap(True)
        file_title.setStyleSheet(f"color: {colors['text_secondary']}; font-size: 12px;")
        file_text_box.addWidget(file_title)
        
        size_desc = QLabel(f"{size_str} · 即将接收")
        size_desc.setStyleSheet(f"color: {colors['text_tertiary']}; font-size: 10px;")
        file_text_box.addWidget(size_desc)
        
        info_layout.addLayout(file_text_box)
        layout.addWidget(info_frame)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 只有当文件可以在当前系统打开时才显示"接受并打开"按钮
        if self._can_open_file():
            accept_open_btn = QPushButton("接受并打开")
            accept_open_btn.setFixedHeight(40)
            accept_open_btn.setStyleSheet(self._secondary_button_style())
            accept_open_btn.clicked.connect(self._on_accept_and_open)
            button_layout.addWidget(accept_open_btn, 1)
        
        reject_btn = QPushButton("拒绝")
        reject_btn.setFixedHeight(40)
        reject_btn.setMinimumWidth(80)
        reject_btn.setStyleSheet(self._secondary_button_style())
        reject_btn.clicked.connect(self._on_reject)
        button_layout.addWidget(reject_btn, 1)
        
        accept_btn = QPushButton("接受")
        accept_btn.setFixedHeight(40)
        accept_btn.setMinimumWidth(80)
        accept_btn.setStyleSheet(self._primary_button_style())
        accept_btn.clicked.connect(self._on_accept)
        button_layout.addWidget(accept_btn, 1)
        
        layout.addLayout(button_layout)
    
    def _primary_button_style(self) -> str:
        colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_primary_bg']};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: {colors['button_primary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {colors['button_primary_pressed']};
            }}
        """
    
    def _secondary_button_style(self) -> str:
        colors = self._get_theme_colors()
        return f"""
            QPushButton {{
                background-color: {colors['button_secondary_bg']};
                border: 1px solid {colors['button_secondary_border']};
                color: {colors['text_secondary']};
                border-radius: 8px;
                font-weight: 500;
                padding: 8px 16px;
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
    
    def _create_avatar_label(self, sender_name: str) -> QLabel:
        """创建带首字母的头像标签"""
        colors = self._get_theme_colors()
        avatar = QLabel(sender_name[:1] if sender_name else "?")
        avatar.setFixedSize(48, 48)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(f"""
            QLabel {{
                background-color: {colors['avatar_bg']};
                color: {colors['text_secondary']};
                border-radius: 24px;
                font-size: 18px;
                font-weight: 700;
            }}
        """)
        return avatar
    
    def _file_ext_badge(self) -> str:
        """获取文件扩展名徽标文本"""
        if "." in self._filename:
            ext = self._filename.rsplit(".", 1)[-1]
            return ext.upper() if ext else "FILE"
        return "FILE"
    
    def _on_accept(self):
        """接受"""
        self.accepted.emit()  # 先发出自定义信号
        self.accept()  # 关闭对话框
    
    def _on_accept_and_open(self):
        """接受并打开"""
        self._open_after_accept = True
        self._on_accept()
    
    def _on_reject(self):
        """拒绝"""
        self.rejected.emit()
        self.reject()
    
    @property
    def open_after_accept(self) -> bool:
        """是否选择了"接受并打开" """
        return self._open_after_accept
    
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
                "bg_card": "#2C2C2E",
                "text_primary": "#FFFFFF",
                "text_secondary": "#EBEBF5",
                "text_tertiary": "#EBEBF599",
                "border": "#38383A",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#2C2C2E",
                "button_secondary_border": "#38383A",
                "button_secondary_hover": "#3A3A3C",
                "button_secondary_pressed": "#48484A",
                "avatar_bg": "#2C2C2E",
            }
        else:
            return {
                "bg_primary": "#FFFFFF",
                "bg_card": "#F9F9F9",
                "text_primary": "#000000",
                "text_secondary": "#111111",
                "text_tertiary": "#8E8E93",
                "border": "#D1D1D6",
                "button_primary_bg": "#0A84FF",
                "button_primary_hover": "#006FE0",
                "button_primary_pressed": "#005BB8",
                "button_secondary_bg": "#F2F2F7",
                "button_secondary_border": "#D1D1D6",
                "button_secondary_hover": "#E5E5EA",
                "button_secondary_pressed": "#D8D8DC",
                "avatar_bg": "#E5E5EA",
            }
    
    def _can_open_file(self) -> bool:
        """判断文件是否可以在当前操作系统上打开（仅考虑系统自带支持）"""
        if "." not in self._filename:
            return False
        
        ext = self._filename.rsplit(".", 1)[-1].lower()
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
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"
