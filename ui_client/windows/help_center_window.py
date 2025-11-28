#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
帮助中心窗口：使用 WebView 显示帮助页面
"""

from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QMessageBox
from PySide6.QtCore import Qt, QUrl, QTimer
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False

from utils.api_client import ApiClient
from utils.config_manager import ConfigManager
from utils.theme_manager import ThemeManager


class HelpCenterWindow(QMainWindow):
    """帮助中心窗口"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        if not WEBENGINE_AVAILABLE:
            QMessageBox.warning(
                self,
                "错误",
                "无法打开帮助中心：缺少 PySide6-QtWebEngine 模块。\n\n请安装：pip install PySide6-QtWebEngine"
            )
            return
        
        # 获取配置信息
        try:
            cfg = ConfigManager.load()
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "").strip()
            session_token = (cfg.get("session_token") or "").strip()
            
            # 获取主题模式
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            # 获取app版本（从主窗口标题或配置中获取）
            app_version = cfg.get("app_version", "1.0")
            
        except Exception as e:
            # 如果获取配置失败，使用默认值
            api_base = ""
            session_token = ""
            theme = "light"
            app_version = "1.0"
        
        # 构建帮助中心URL
        if api_base:
            help_url = f"{api_base.rstrip('/')}/help"
        else:
            help_url = "about:blank"
        
        # 保存变量供后续使用
        self._session_token = session_token
        self._theme = theme
        self._app_version = app_version
        
        # 设置窗口标题（稍后会从网页title更新）
        self.setWindowTitle("帮助中心")
        
        # 设置窗口大小与主窗口一致
        if parent:
            self.resize(parent.size())
        else:
            self.resize(1200, 780)
        
        # 创建中央widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建布局
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 创建WebView
        self.web_view = QWebEngineView()
        
        # 创建自定义页面，用于注入JS变量
        page = QWebEnginePage(self.web_view)
        self.web_view.setPage(page)
        
        # 连接titleChanged信号，更新窗口标题
        self.web_view.titleChanged.connect(self._on_title_changed)
        
        # 连接loadFinished信号，在页面加载完成后注入JS
        self.web_view.loadFinished.connect(self._on_page_loaded)
        
        layout.addWidget(self.web_view)
        
        # 加载URL
        if help_url != "about:blank":
            url = QUrl(help_url)
            self.web_view.setUrl(url)
    
    def _on_title_changed(self, title: str):
        """网页标题改变时更新窗口标题"""
        if title:
            self.setWindowTitle(title)
    
    def _on_page_loaded(self, ok: bool):
        """页面加载完成时注入JS变量"""
        if ok:
            self._inject_js_variables()
    
    def _inject_js_variables(self):
        """注入JS变量到页面"""
        # 转义特殊字符，防止JS注入攻击
        import json
        token_escaped = json.dumps(self._session_token)
        theme_escaped = json.dumps(self._theme)
        version_escaped = json.dumps(self._app_version)
        
        js_code = f"""
        (function() {{
            // 注入变量到window对象
            window.appToken = {token_escaped};
            window.appTheme = {theme_escaped};
            window.appVersion = {version_escaped};
            
            console.log('App variables injected:', {{
                token: window.appToken ? '***' : '',
                theme: window.appTheme,
                version: window.appVersion
            }});
        }})();
        """
        
        # 执行JS代码
        self.web_view.page().runJavaScript(js_code)

