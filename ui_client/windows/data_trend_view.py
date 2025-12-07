#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图文趋势页面：在主窗口右侧内嵌远程网页。
"""

import json

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QUrl

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False

from utils.config_manager import ConfigManager
from utils.theme_manager import ThemeManager


class DataTrendView(QWidget):
    """用于承载远程图文趋势页面的视图。"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._context_data = self._load_context()

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
        # 加载成功后注入样式与上下文（macOS 亦生效）
        self.web_view.loadFinished.connect(self._hide_scrollbars)
        self.web_view.loadFinished.connect(self._inject_app_context)
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

    def _hide_scrollbars(self, ok: bool):
        """在页面加载完成后注入 CSS，隐藏浏览器滚动条。"""
        if not ok:
            return
        # 同时处理 html 与 body，确保 macOS 上的浮动滚动条也被禁用
        js = """
            (() => {
                const style = document.createElement('style');
                style.textContent = `
                    /* 保持可滚动，仅隐藏滚动条（含 macOS 浮动条） */
                    html, body {
                        overflow: auto !important;
                        scrollbar-width: none;          /* Firefox */
                        -ms-overflow-style: none;        /* IE/Edge */
                    }
                    html::-webkit-scrollbar,
                    body::-webkit-scrollbar {
                        width: 0 !important;
                        height: 0 !important;
                        display: none !important;        /* WebKit/macOS */
                    }
                `;
                document.head.appendChild(style);
            })();
        """
        self.web_view.page().runJavaScript(js)

    def _inject_app_context(self, ok: bool):
        """页面加载完成后注入用户信息与偏好配置，保持与帮助中心对齐。"""
        if not ok:
            return

        # 每次加载重新拉取配置，确保 token / 主题为最新
        self._context_data = self._load_context()
        ctx = self._context_data
        ctx_json = json.dumps(ctx)
        # 注意：使用 format 时需要对 JS 对象的大括号进行转义
        # JS 模板中的大括号需要成对转义，避免 str.format 误解析
        js_code = """
        (() => {{
            const ctx = {ctx_json};
            // 兼容帮助中心的变量命名
            window.appToken = ctx.token;
            window.appTheme = ctx.theme;
            window.appVersion = ctx.clientVersion;
            window.userInfo = {{
                id: ctx.userId,
                name: ctx.userName,
                email: ctx.userEmail
            }};
            window.appPreferences = {{
                autoRefresh: ctx.autoRefresh,
                notifications: ctx.notifications
            }};
            window.appContext = {{ ...ctx }};
            console.log('DataTrend app context injected', {{
                hasToken: ctx.token ? '***' : '',
                theme: ctx.theme,
                version: ctx.clientVersion,
                autoRefresh: ctx.autoRefresh,
                notifications: ctx.notifications
            }});
        })();
        """.replace("{ctx_json}", ctx_json)
        self.web_view.page().runJavaScript(js_code)

    def _load_context(self) -> dict:
        """读取用户信息与偏好配置，供 WebView 注入使用。"""
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            theme = (
                ThemeManager.detect_system_theme()
                if preference == "auto"
                else preference
            )
            return {
                "token": (cfg.get("session_token") or "").strip(),
                "userId": (cfg.get("user_id") or "").strip(),
                "userName": (cfg.get("user_name") or "").strip(),
                "userEmail": (cfg.get("user_email") or "").strip(),
                "theme": theme,
                "clientVersion": (cfg.get("client_version") or cfg.get("app_version") or "1.0.0").strip(),
                "autoRefresh": bool(cfg.get("auto_refresh", True)),
                "notifications": bool(cfg.get("notifications", True)),
            }
        except Exception:
            return {
                "token": "",
                "userId": "",
                "userName": "",
                "userEmail": "",
                "theme": "light",
                "clientVersion": "1.0.0",
                "autoRefresh": True,
                "notifications": True,
            }

