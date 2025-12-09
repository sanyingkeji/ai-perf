#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
帮助中心窗口：使用 WebView 显示帮助页面
"""

from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QMessageBox, QMenu
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QPainter, QColor, QAction
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
        
        # 创建进度条（放在顶部）
        self.progress_bar = self._create_progress_bar()
        layout.addWidget(self.progress_bar)
        
        # 创建WebView
        self.web_view = QWebEngineView()
        
        # 创建自定义页面，用于注入JS变量
        page = QWebEnginePage(self.web_view)
        self.web_view.setPage(page)
        
        # 使用自定义右键菜单
        self.web_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.web_view.customContextMenuRequested.connect(self._show_context_menu)
        
        # 连接titleChanged信号，更新窗口标题
        self.web_view.titleChanged.connect(self._on_title_changed)
        
        # 连接加载进度信号
        self.web_view.loadProgress.connect(self._on_load_progress)
        self.web_view.loadStarted.connect(self._on_load_started)
        
        # 连接loadFinished信号，在页面加载完成后注入JS
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.loadFinished.connect(self._on_page_loaded)
        
        layout.addWidget(self.web_view)
        
        # 加载URL
        if help_url != "about:blank":
            url = QUrl(help_url)
            self.web_view.setUrl(url)
    
    def _create_progress_bar(self) -> QWidget:
        """创建网页加载进度条"""
        progress_widget = QWidget(self)
        progress_widget.setFixedHeight(3)
        progress_widget.hide()  # 默认隐藏
        
        # 保存进度值（0-100）
        self._progress_value = 0
        
        # 重写 paintEvent 来绘制进度条
        def paintEvent(event):
            painter = QPainter(progress_widget)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # 根据主题设置颜色
            if self._theme == "dark":
                bg_color = QColor(40, 40, 40)  # 深色背景
                progress_color = QColor(96, 165, 250)  # 蓝色进度条
            else:
                bg_color = QColor(240, 240, 240)  # 浅色背景
                progress_color = QColor(96, 165, 250)  # 蓝色进度条
            
            # 绘制背景
            painter.fillRect(0, 0, progress_widget.width(), progress_widget.height(), bg_color)
            
            # 绘制进度条
            if self._progress_value > 0:
                progress_width = int(progress_widget.width() * self._progress_value / 100)
                painter.fillRect(0, 0, progress_width, progress_widget.height(), progress_color)
        
        progress_widget.paintEvent = paintEvent
        return progress_widget
    
    def _on_load_started(self):
        """开始加载时显示进度条"""
        self._progress_value = 0
        self.progress_bar.show()
        self.progress_bar.update()
    
    def _on_load_progress(self, progress: int):
        """加载进度更新"""
        self._progress_value = progress
        self.progress_bar.update()
    
    def _on_load_finished(self, ok: bool):
        """加载完成时隐藏进度条"""
        # 延迟一点再隐藏，让用户看到100%
        if ok:
            self._progress_value = 100
            self.progress_bar.update()
            QTimer.singleShot(300, lambda: self.progress_bar.hide())
        else:
            self.progress_bar.hide()
    
    def _show_context_menu(self, position):
        """显示自定义右键菜单"""
        menu = QMenu(self)
        
        # 后退
        back_action = QAction("后退", self)
        back_action.setEnabled(self.web_view.history().canGoBack())
        back_action.triggered.connect(self.web_view.back)
        menu.addAction(back_action)
        
        # 前进
        forward_action = QAction("前进", self)
        forward_action.setEnabled(self.web_view.history().canGoForward())
        forward_action.triggered.connect(self.web_view.forward)
        menu.addAction(forward_action)
        
        # 分隔线
        menu.addSeparator()
        
        # 刷新
        reload_action = QAction("刷新", self)
        reload_action.triggered.connect(self.web_view.reload)
        menu.addAction(reload_action)
        
        # 分隔线
        menu.addSeparator()
        
        # 复制
        copy_action = QAction("复制", self)
        copy_action.triggered.connect(lambda: self.web_view.page().triggerAction(QWebEnginePage.Copy))
        menu.addAction(copy_action)
        
        # 显示菜单
        menu.exec(self.web_view.mapToGlobal(position))
    
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

