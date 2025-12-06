#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图文趋势页面：在主窗口右侧内嵌远程网页。
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QUrl

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False


class DataTrendView(QWidget):
    """用于承载远程图文趋势页面的视图。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if not WEBENGINE_AVAILABLE:
            # 环境缺少 WebEngine，给予友好提示
            self.web_view = None
            placeholder = QLabel("当前环境缺少 PySide6-QtWebEngine，无法打开图文趋势页面。")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setWordWrap(True)
            placeholder.setStyleSheet("padding: 24px; color: #666;")
            layout.addWidget(placeholder)
            return

        self.web_view = QWebEngineView(self)
        # 关闭右键菜单，避免影响整体视觉
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.web_view)

    def load_url(self, url: str):
        """加载远程链接，确保只接受有效的 http/https 地址。"""
        if not WEBENGINE_AVAILABLE or not self.web_view:
            return
        if not url:
            return

        qurl = QUrl(url)
        if (not qurl.isValid()) or qurl.scheme().lower() not in ("http", "https"):
            return

        # 避免重复刷新相同链接
        if self.web_view.url() == qurl:
            self.web_view.reload()
            return

        self.web_view.setUrl(qurl)

