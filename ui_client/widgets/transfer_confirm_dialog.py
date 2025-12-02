#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输确认对话框
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from pathlib import Path


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
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        self.setWindowTitle("文件传输请求")
        self.setModal(True)
        self.resize(400, 200)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        # 标题
        title = QLabel(f"{self._sender_name} 想要发送文件给您")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # 文件信息
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.Box)
        info_frame.setStyleSheet("""
            QFrame {
                border: 1px solid #ddd;
                border-radius: 8px;
                background-color: #f9f9f9;
                padding: 12px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(8)
        
        # 文件名
        filename_label = QLabel(f"文件名：{self._filename}")
        filename_label.setWordWrap(True)
        info_layout.addWidget(filename_label)
        
        # 文件大小
        size_str = self._format_file_size(self._file_size)
        size_label = QLabel(f"大小：{size_str}")
        info_layout.addWidget(size_label)
        
        layout.addWidget(info_frame)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        
        reject_btn = QPushButton("拒绝")
        reject_btn.setFixedHeight(40)
        reject_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #ddd;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
        """)
        reject_btn.clicked.connect(self._on_reject)
        button_layout.addWidget(reject_btn)
        
        accept_btn = QPushButton("接受")
        accept_btn.setFixedHeight(40)
        accept_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: white;
                border: none;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0056CC;
            }
        """)
        accept_btn.clicked.connect(self._on_accept)
        button_layout.addWidget(accept_btn)
        
        layout.addLayout(button_layout)
    
    def _on_accept(self):
        """接受"""
        self.accepted.emit()
        self.accept()
    
    def _on_reject(self):
        """拒绝"""
        self.rejected.emit()
        self.reject()
    
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

