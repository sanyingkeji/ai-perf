#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loading_overlay.py

全局"加载中"半透明遮罩，用于网络请求期间给出反馈。

设计要点：
- 作为父窗口的子控件存在（SubWindow），不会单独成为一个操作系统窗口；
- 随父窗口一起移动 / 缩放，不会出现"遮罩留在原地"的问题；
- 仅拦截视觉，不抢键盘焦点。
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


class LoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # 子窗口 + 无边框（不是顶层 Tool 窗口）
        # 作为主窗口子控件，随之移动 / 缩放
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 允许鼠标事件透传给下层控件（只做视觉遮罩，不阻塞点击的话可以打开这行）
        # self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.setStyleSheet("background-color: rgba(0, 0, 0, 110);")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 内容容器（标签 + 关闭按钮）
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        self._label = QLabel("加载中…")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: white;")
        self._label.setFont(QFont("Arial", 14, QFont.Bold))
        content_layout.addWidget(self._label, alignment=Qt.AlignCenter)

        # 关闭按钮容器（默认隐藏）
        self._close_btn_container = QHBoxLayout()
        self._close_btn = QPushButton("取消")
        self._close_btn.setFixedWidth(100)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.2);
                color: white;
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-radius: 4px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.3);
            }
        """)
        self._close_btn.clicked.connect(self._on_close_clicked)
        self._close_btn_container.addStretch()
        self._close_btn_container.addWidget(self._close_btn)
        self._close_btn_container.addStretch()
        self._close_btn_container.setContentsMargins(0, 0, 0, 0)
        content_layout.addLayout(self._close_btn_container)
        
        # 关闭按钮默认隐藏
        self._close_btn.setVisible(False)
        
        layout.addStretch()
        layout.addLayout(content_layout)
        layout.addStretch()

        self._close_callback = None
        self.hide()
    
    def _on_close_clicked(self):
        """关闭按钮点击事件"""
        if self._close_callback:
            self._close_callback()
        self.hide_safely()
    
    def set_closeable(self, closeable: bool, callback=None):
        """设置是否可关闭，以及关闭时的回调"""
        self._close_btn.setVisible(closeable)
        self._close_callback = callback

    def set_message(self, text: str) -> None:
        self._label.setText(text or "加载中…")

    def show_message(self, text: str = "加载中…", closeable: bool = False, close_callback=None) -> None:
        """
        显示遮罩并更新提示文案。
        会自动铺满父窗口区域，并保持在最上层。
        
        Args:
            text: 提示文案
            closeable: 是否显示关闭按钮
            close_callback: 关闭按钮的回调函数
        """
        self.set_message(text)
        self.set_closeable(closeable, close_callback)
        if self.parent() is not None:
            # 覆盖整个父窗口区域
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())
        self.raise_()
        self.show()

    def hide_safely(self) -> None:
        """
        防御性隐藏，避免在窗口销毁阶段抛异常。
        """
        try:
            self.hide()
        except RuntimeError:
            # 父窗口已经被销毁，忽略
            pass

