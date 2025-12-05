#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剪贴板内容接收对话框
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class ClipboardReceiveDialog(QDialog):
    """剪贴板内容接收对话框"""
    
    paste_to_clipboard = Signal()  # 放入剪贴板信号（或接受并打开）
    save_as_file = Signal()  # 另存为文件信号（仅文本时）
    
    def __init__(self, sender_name: str, is_image: bool = False, parent=None):
        """
        初始化对话框
        
        Args:
            sender_name: 发送者名称
        """
        super().__init__(parent)
        self._sender_name = sender_name
        self._is_image = is_image
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        self.setWindowTitle("剪贴板内容接收")
        self.setModal(True)
        self.resize(400, 180)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        # 标题
        content_type_text = "剪贴板图片" if self._is_image else "剪贴板内容"
        title = QLabel(f"接收到来自 {self._sender_name} 的{content_type_text}")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        
        # 提示信息
        info_text = "要放入剪贴板吗？" if self._is_image else "是否放入剪贴板？"
        info_label = QLabel(info_text)
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 13px;")
        layout.addWidget(info_label)
        
        layout.addStretch()
        
        if self._is_image:
            button_layout = QHBoxLayout()
            button_layout.setSpacing(12)
            
            accept_btn = QPushButton("接受并打开")
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
            accept_btn.clicked.connect(self._on_paste)
            button_layout.addWidget(accept_btn)
            
            reject_btn = QPushButton("拒绝")
            reject_btn.setFixedHeight(40)
            reject_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0f0f0;
                    border: 1px solid #ddd;
                    border-radius: 6px;
                    color: #000000;
                }
                QPushButton:hover {
                    background-color: #e0e0e0;
                    color: #000000;
                }
            """)
            reject_btn.clicked.connect(self.reject)
            button_layout.addWidget(reject_btn)
            
            layout.addLayout(button_layout)
        else:
            button_layout = QHBoxLayout()
            button_layout.setSpacing(12)
            
            save_btn = QPushButton("另存为txt文件")
            save_btn.setFixedHeight(40)
            save_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0f0f0;
                    border: 1px solid #ddd;
                    border-radius: 6px;
                    color: #000000;
                }
                QPushButton:hover {
                    background-color: #e0e0e0;
                    color: #000000;
                }
            """)
            save_btn.clicked.connect(self._on_save)
            button_layout.addWidget(save_btn)
            
            paste_btn = QPushButton("是")
            paste_btn.setFixedHeight(40)
            paste_btn.setStyleSheet("""
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
            paste_btn.clicked.connect(self._on_paste)
            button_layout.addWidget(paste_btn)
            
            layout.addLayout(button_layout)
    
    def _on_paste(self):
        """放入剪贴板"""
        self.paste_to_clipboard.emit()
        self.accept()
    
    def _on_save(self):
        """另存为文件"""
        self.save_as_file.emit()
        self.accept()

