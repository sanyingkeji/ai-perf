#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图文趋势页面：在主窗口右侧内嵌远程网页。
"""

import json

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QMenu
from PySide6.QtCore import Qt, QUrl, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QPainter, QColor, QAction, QContextMenuEvent

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
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

        # 创建进度条（放在顶部）
        self.progress_bar = self._create_progress_bar()
        layout.addWidget(self.progress_bar)
        
        self.web_view = QWebEngineView(self)
        # 使用自定义右键菜单
        self.web_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.web_view.customContextMenuRequested.connect(self._show_context_menu)
        # 连接加载进度信号
        self.web_view.loadProgress.connect(self._on_load_progress)
        self.web_view.loadStarted.connect(self._on_load_started)
        # 加载成功后注入样式与上下文（macOS 亦生效）
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.loadFinished.connect(self._hide_scrollbars)
        self.web_view.loadFinished.connect(self._inject_app_context)
        
        # 设置 UserScript 在页面加载前注入变量
        self._setup_user_script()
        
        # 设置 webview 背景色以适配主题
        self._update_webview_background()
        
        layout.addWidget(self.web_view)

    def _setup_user_script(self):
        """设置 UserScript 在页面加载前注入变量"""
        if not WEBENGINE_AVAILABLE or not self.web_view:
            return
        
        # 重新加载上下文数据
        self._context_data = self._load_context()
        ctx = self._context_data
        ctx_json = json.dumps(ctx)
        
        # 创建 UserScript，在文档开始创建时注入
        script_source = f"""
        (function() {{
            const ctx = {ctx_json};
            // 在页面加载前就设置这些变量
            Object.defineProperty(window, 'appToken', {{
                value: ctx.token,
                writable: true,
                configurable: true
            }});
            Object.defineProperty(window, 'appTheme', {{
                value: ctx.theme,
                writable: true,
                configurable: true
            }});
            Object.defineProperty(window, 'appVersion', {{
                value: ctx.clientVersion,
                writable: true,
                configurable: true
            }});
            Object.defineProperty(window, 'userInfo', {{
                value: {{
                    id: ctx.userId,
                    name: ctx.userName,
                    email: ctx.userEmail
                }},
                writable: true,
                configurable: true
            }});
            Object.defineProperty(window, 'appPreferences', {{
                value: {{
                    autoRefresh: ctx.autoRefresh,
                    notifications: ctx.notifications
                }},
                writable: true,
                configurable: true
            }});
            Object.defineProperty(window, 'appContext', {{
                value: Object.assign({{}}, ctx),
                writable: true,
                configurable: true
            }});
            
            console.log('[UserScript] App context injected before page load:', {{
                theme: ctx.theme,
                hasToken: !!ctx.token
            }});
        }})();
        """
        
        script = QWebEngineScript()
        script.setSourceCode(script_source)
        script.setName("app_context_injector")
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        script.setRunsOnSubFrames(False)
        
        # 直接插入脚本（不清除旧脚本，因为脚本是幂等的）
        # 注意：PySide6 的 QWebEngineScriptCollection API 可能不支持遍历和查找
        # 但直接插入是安全的，因为脚本是幂等的（重复执行结果相同）
        page = self.web_view.page()
        scripts = page.scripts()
        scripts.insert(script)

    def load_url(self, url: str):
        """加载远程链接，确保只接受有效的 http/https 地址。"""
        if not WEBENGINE_AVAILABLE or not self.web_view:
            return
        if not url:
            return

        qurl = QUrl(url)
        if (not qurl.isValid()) or qurl.scheme().lower() not in ("http", "https"):
            return

        # 如果 URL 相同且已经加载过，不做任何操作，避免刷新
        current_url = self.web_view.url()
        if current_url == qurl and current_url.isValid():
            return

        # 在加载前更新 UserScript
        self._setup_user_script()

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

        # 延迟注入，确保DOM已经准备好
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self._do_inject_app_context)
    
    def _do_inject_app_context(self):
        """实际执行注入操作（在页面加载后再次确认和触发事件）"""
        if not self.web_view or not self.web_view.url().isValid():
            return

        # 每次加载重新拉取配置，确保 token / 主题为最新
        self._context_data = self._load_context()
        ctx = self._context_data
        ctx_json = json.dumps(ctx)
        
        # 再次确认变量已设置，并触发事件
        js_code = """
        (() => {{
            const ctx = {ctx_json};
            // 确保变量已设置（UserScript 可能已经设置了，这里再次确认）
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
            
            // 触发主题变更事件，让页面可以响应主题变化
            if (window.onAppThemeChanged) {{
                window.onAppThemeChanged(ctx.theme);
            }}
            
            // 也触发自定义事件
            if (typeof window.dispatchEvent !== 'undefined') {{
                window.dispatchEvent(new CustomEvent('appThemeChanged', {{ detail: {{ theme: ctx.theme }} }}));
            }}
            
            // 如果页面有 initTheme 函数，也调用它
            if (typeof initTheme === 'function') {{
                initTheme();
            }}
            
            console.log('[PostLoad] DataTrend app context confirmed', {{
                hasToken: ctx.token ? '***' : '',
                theme: ctx.theme,
                appTheme: window.appTheme,
                appContextTheme: window.appContext?.theme
            }});
        }})();
        """.replace("{ctx_json}", ctx_json)
        
        # 执行注入
        self.web_view.page().runJavaScript(js_code)
        
        # 延迟后再次注入以确保成功（如果第一次注入时页面还没完全准备好）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._retry_inject_app_context())
    
    def _retry_inject_app_context(self):
        """重试注入（确保主题正确设置）"""
        if not self.web_view or not self.web_view.url().isValid():
            return
        
        self._context_data = self._load_context()
        ctx = self._context_data
        ctx_json = json.dumps(ctx)
        
        # 只注入主题相关的变量，确保主题正确
        js_code = """
        (() => {{
            const ctx = {ctx_json};
            window.appTheme = ctx.theme;
            if (window.appContext) {{
                window.appContext.theme = ctx.theme;
            }}
            
            // 触发主题变更事件
            if (window.onAppThemeChanged) {{
                window.onAppThemeChanged(ctx.theme);
            }}
            
            if (typeof window.dispatchEvent !== 'undefined') {{
                window.dispatchEvent(new CustomEvent('appThemeChanged', {{ detail: {{ theme: ctx.theme }} }}));
            }}
            
            if (typeof initTheme === 'function') {{
                initTheme();
            }}
        }})();
        """.replace("{ctx_json}", ctx_json)
        
        self.web_view.page().runJavaScript(js_code)
    
    def _update_webview_background(self):
        """更新 webview 背景色以适配主题"""
        if not WEBENGINE_AVAILABLE or not self.web_view:
            return
        
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
        except Exception:
            theme = "light"
        
        # 设置页面背景色
        page = self.web_view.page()
        if theme == "dark":
            # 深色主题：使用深色背景
            page.setBackgroundColor(QColor(32, 33, 36))  # #202124
            self.web_view.setStyleSheet("background-color: #202124;")
        else:
            # 浅色主题：使用浅色背景
            page.setBackgroundColor(QColor(247, 249, 252))  # #F7F9FC
            self.web_view.setStyleSheet("background-color: #F7F9FC;")
    
    def update_theme(self):
        """更新 webview 主题（供外部调用）"""
        if not WEBENGINE_AVAILABLE or not self.web_view:
            return
        
        # 更新背景色
        self._update_webview_background()
        
        # 更新 UserScript（下次加载时会使用新主题）
        self._setup_user_script()
        
        # 如果页面已加载，立即更新主题
        if self.web_view.url().isValid():
            # 重新拉取配置，确保主题为最新
            self._context_data = self._load_context()
            ctx = self._context_data
            ctx_json = json.dumps(ctx)
            
            # 先更新变量，再触发事件
            js_code = """
            (() => {{
                const ctx = {ctx_json};
                // 更新所有相关变量
                window.appTheme = ctx.theme;
                if (window.appContext) {{
                    window.appContext.theme = ctx.theme;
                }}
                
                console.log('[UpdateTheme] Variables updated:', {{
                    appTheme: window.appTheme,
                    appContextTheme: window.appContext?.theme
                }});
                
                // 触发主题变更事件（延迟一点确保变量已更新）
                setTimeout(() => {{
                    if (window.onAppThemeChanged) {{
                        window.onAppThemeChanged(ctx.theme);
                    }}
                    
                    if (typeof window.dispatchEvent !== 'undefined') {{
                        window.dispatchEvent(new CustomEvent('appThemeChanged', {{ detail: {{ theme: ctx.theme }} }}));
                    }}
                    
                    // 如果页面有 updateTheme 函数，也调用它
                    if (typeof updateTheme === 'function') {{
                        updateTheme(ctx.theme);
                    }}
                    
                    console.log('[UpdateTheme] DataTrend theme updated and events triggered:', ctx.theme);
                }}, 50);
            }})();
        """.replace("{ctx_json}", ctx_json)
        self.web_view.page().runJavaScript(js_code)

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
            
            # 获取当前主题
            try:
                cfg = ConfigManager.load()
                preference = cfg.get("theme", "auto")
                if preference == "auto":
                    theme = ThemeManager.detect_system_theme()
                else:
                    theme = preference
            except:
                theme = "light"
            
            # 根据主题设置颜色
            if theme == "dark":
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
        from PySide6.QtCore import QTimer
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

