from datetime import datetime, timezone, timedelta
from typing import Dict, Any
import sys

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QCheckBox, QHBoxLayout, QPushButton, QFrame, QDialog, QTextEdit,
    QScrollArea, QApplication, QSpinBox, QFileDialog, QButtonGroup
)
from PySide6.QtGui import QFont
from PySide6.QtCore import QTimer, QRunnable, QThreadPool, QObject, Signal, Slot, Qt
import platform
import zipfile
from pathlib import Path

from utils.config_manager import ConfigManager, CONFIG_PATH
from utils.theme_manager import ThemeManager
from utils.google_login import login_and_get_id_token, GoogleLoginError
from utils.api_client import ApiClient, ApiError, AuthError
from widgets.toast import Toast
from windows.update_dialog import UpdateDialog
from datetime import date


class SettingsView(QWidget):
    def __init__(self):
        super().__init__()
        
        self.cfg = ConfigManager.load()
        # 标志：是否正在初始化（用于防止初始化时触发自动保存）
        self._is_initializing = True
        # 标记：是否已经显示过升级弹窗（防止重复弹窗）
        self._update_dialog_shown = False

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建滚动区域
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 根据平台设置滚动条策略：macOS 隐藏滚动条，其他平台显示
        import platform
        system = platform.system()
        if system == "Darwin":
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # macOS 上通过样式表隐藏滚动条
            self.scroll_area.setStyleSheet("""
                QScrollArea {
                    border: none;
                }
                QScrollBar:vertical {
                    width: 0px;
                    background: transparent;
                }
                QScrollBar::handle:vertical {
                    width: 0px;
                }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical {
                    width: 0px;
                }
            """)
        else:
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建内容widget
        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        
        # 设置滚动区域的内容widget
        self.scroll_area.setWidget(self.content_widget)
        
        # 设置最大高度，与今日评分对齐（使用屏幕可用高度）
        screen = QApplication.primaryScreen()
        if screen:
            screen_height = screen.availableGeometry().height()
            max_height = int(screen_height * 1.0)  # 100%
            self.scroll_area.setMaximumHeight(max_height)
        
        # 将滚动区域添加到主布局
        main_layout.addWidget(self.scroll_area)

        title = QLabel("系统设置")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # --- API & 登录 ---
        api_frame = QFrame()
        api_layout = QVBoxLayout(api_frame)
        api_layout.setSpacing(8)

        api_title = QLabel("后端与登录配置")
        api_title_font = QFont()
        api_title_font.setPointSize(12)
        api_title_font.setBold(True)
        api_title.setFont(api_title_font)
        api_layout.addWidget(api_title)

        # API 地址
        api_row = QHBoxLayout()
        api_label = QLabel("后端 API 地址：")
        self.api_edit = QLineEdit()
        self.api_edit.setPlaceholderText("例如：http://127.0.0.1:8000")
        self.api_edit.setText(self.cfg.get("api_base", ""))
        # API 地址变更时自动保存（延迟500ms，避免频繁保存）
        self._api_save_timer = QTimer()
        self._api_save_timer.setSingleShot(True)
        self._api_save_timer.timeout.connect(self._auto_save_api_base)
        self.api_edit.textChanged.connect(lambda: self._api_save_timer.start(500))
        # 失去焦点或按回车时立即保存并刷新状态
        self.api_edit.editingFinished.connect(self._on_api_base_changed)
        self.api_edit.returnPressed.connect(self._on_api_base_changed)

        api_row.addWidget(api_label)
        api_row.addWidget(self.api_edit)
        api_layout.addLayout(api_row)

        # Google ID Token + 登录按钮
        token_row = QHBoxLayout()
        token_label = QLabel("Google ID Token：")
        self.token_edit = QLineEdit()
        self.token_edit.setReadOnly(True)
        self.token_edit.setPlaceholderText("点击右侧按钮，通过 Google 登录自动获取")
        self.token_edit.setText(self.cfg.get("google_id_token", ""))

        token_row.addWidget(token_label)
        token_row.addWidget(self.token_edit)
        api_layout.addLayout(token_row)
        # 当前登录邮箱
        email_row = QHBoxLayout()
        email_label = QLabel("当前登录邮箱：")
        self.email_value = QLabel(self.cfg.get("user_email", "") or "（未登录）")
        email_row.addWidget(email_label)
        email_row.addWidget(self.email_value)
        email_row.addStretch()
        api_layout.addLayout(email_row)

        # 会话 Token（只读展示）
        session_row = QHBoxLayout()
        session_label = QLabel("会话 Token：")
        self.session_edit = QLineEdit()
        self.session_edit.setReadOnly(True)
        self.session_edit.setText(self.cfg.get("session_token", ""))
        session_row.addWidget(session_label)
        session_row.addWidget(self.session_edit)
        api_layout.addLayout(session_row)

        # 登录 / 退出登录 按钮
        btn_row = QHBoxLayout()
        self.btn_google_login = QPushButton("谷歌授权登录")
        self.btn_google_login.clicked.connect(self.on_google_login_clicked)

        self.btn_google_logout = QPushButton("退出登录")
        self.btn_google_logout.setStyleSheet(
            "QPushButton { background-color: #d9534f; color: white; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #c9302c; }"
        )
        self.btn_google_logout.clicked.connect(self.on_google_logout_clicked)

        # 按钮统一宽度
        self.btn_google_login.setFixedWidth(120)
        self.btn_google_logout.setFixedWidth(120)

        btn_row.addWidget(self.btn_google_login)
        btn_row.addWidget(self.btn_google_logout)
        btn_row.addStretch()
        api_layout.addLayout(btn_row)

        self._refresh_login_buttons()

        layout.addWidget(api_frame)

        # --- 主题 ---
        theme_frame = QFrame()
        theme_layout = QVBoxLayout(theme_frame)
        theme_layout.setSpacing(4)

        theme_title = QLabel("主题")
        theme_title_font = QFont()
        theme_title_font.setPointSize(12)
        theme_title_font.setBold(True)
        theme_title.setFont(theme_title_font)
        theme_layout.addWidget(theme_title)

        # 使用互斥的可切换按钮替代 QRadioButton，规避 macOS 原生样式崩溃
        self.theme_buttons: list[QPushButton] = []
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)

        def _make_theme_button(text: str, value: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("themeValue", value)
            btn.setFixedHeight(24)
            btn.setMinimumWidth(60)
            self.theme_group.addButton(btn)
            self.theme_buttons.append(btn)
            return btn

        btn_auto = _make_theme_button("跟随系统", "auto")
        btn_light = _make_theme_button("浅色模式", "light")
        btn_dark = _make_theme_button("深色模式", "dark")

        theme_choice = self.cfg.get("theme", "auto")
        if theme_choice == "light":
            btn_light.setChecked(True)
        elif theme_choice == "dark":
            btn_dark.setChecked(True)
        else:
            btn_auto.setChecked(True)

        # 主题变更时自动保存并应用
        def _on_theme_clicked(button: QPushButton):
            if self._is_initializing:
                return
            value = button.property("themeValue")
            if value:
                self._auto_save_theme(str(value))

        self.theme_group.buttonClicked.connect(_on_theme_clicked)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        for btn in (btn_auto, btn_light, btn_dark):
            theme_row.addWidget(btn)
        theme_row.addStretch()
        theme_layout.addLayout(theme_row)

        # 初始根据当前/系统主题刷新样式
        self._update_theme_buttons_style(theme_choice)

        layout.addWidget(theme_frame)

        # --- 行为 ---
        behavior_frame = QFrame()
        behavior_layout = QVBoxLayout(behavior_frame)
        behavior_layout.setSpacing(4)

        behavior_title = QLabel("行为")
        behavior_title_font = QFont()
        behavior_title_font.setPointSize(12)
        behavior_title_font.setBold(True)
        behavior_title.setFont(behavior_title_font)
        behavior_layout.addWidget(behavior_title)

        self.chk_auto_refresh = QCheckBox("启动时自动刷新今日评分")
        self.chk_auto_refresh.setChecked(self.cfg.get("auto_refresh", True))
        self.chk_auto_refresh.stateChanged.connect(self._auto_save_auto_refresh)
        behavior_layout.addWidget(self.chk_auto_refresh)

        self.chk_notifications = QCheckBox("允许系统通知")
        self.chk_notifications.setChecked(self.cfg.get("notifications", True))
        self.chk_notifications.stateChanged.connect(self._auto_save_notifications)
        behavior_layout.addWidget(self.chk_notifications)

        # 日志保留时长（小时）
        log_retention_row = QHBoxLayout()
        log_retention_label = QLabel("日志保留时长（小时）：")
        self.spin_log_retention = QSpinBox()
        self.spin_log_retention.setRange(1, 72)  # 最多保留 3 天
        self.spin_log_retention.setValue(int(self.cfg.get("log_retention_hours", 1) or 1))
        self.spin_log_retention.setSuffix(" 小时")
        self.spin_log_retention.valueChanged.connect(self._auto_save_log_retention)
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addWidget(self.spin_log_retention)
        log_retention_row.addStretch()
        behavior_layout.addLayout(log_retention_row)

        # 导出日志按钮
        export_row = QHBoxLayout()
        self.btn_export_logs = QPushButton("导出最近日志")
        # 迷你按钮样式
        self.btn_export_logs.setFixedWidth(90)
        self.btn_export_logs.setFixedHeight(26)
        self.btn_export_logs.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.btn_export_logs.clicked.connect(self._export_logs)
        export_row.addWidget(QLabel("日志导出："))
        export_row.addWidget(self.btn_export_logs)
        export_row.addStretch()
        behavior_layout.addLayout(export_row)
        
        # 全局快捷键启用开关（仅 macOS）
        import platform
        system = platform.system()
        if system == "Darwin":
            self.chk_global_hotkey = QCheckBox("启用全局快捷键")
            self.chk_global_hotkey.setChecked(self.cfg.get("global_hotkey_enabled", False))
            self.chk_global_hotkey.stateChanged.connect(self._auto_save_global_hotkey)
            behavior_layout.addWidget(self.chk_global_hotkey)
        
        # 通知权限检查和引导
        notification_permission_row = QHBoxLayout()
        self.notification_permission_label = QLabel("通知权限：")
        self.notification_permission_status = QLabel("检查中...")
        self.notification_permission_btn = QPushButton("打开系统设置")
        self.notification_permission_btn.setFixedWidth(100)
        self.notification_permission_btn.setFixedHeight(28)
        self.notification_permission_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self.notification_permission_btn.clicked.connect(self._open_notification_settings)
        
        # 添加刷新按钮，让用户可以手动刷新权限状态
        self.notification_permission_refresh_btn = QPushButton("刷新权限")
        self.notification_permission_refresh_btn.setFixedWidth(80)
        self.notification_permission_refresh_btn.setFixedHeight(28)
        self.notification_permission_refresh_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self.notification_permission_refresh_btn.clicked.connect(self._refresh_notification_permission)
        
        notification_permission_row.addWidget(self.notification_permission_label)
        notification_permission_row.addWidget(self.notification_permission_status)
        notification_permission_row.addStretch()
        notification_permission_row.addWidget(self.notification_permission_refresh_btn)
        notification_permission_row.addWidget(self.notification_permission_btn)
        behavior_layout.addLayout(notification_permission_row)
        
        # 添加权限说明提示（当权限未授权时显示）
        self.notification_permission_hint = QLabel("")
        self.notification_permission_hint.setStyleSheet("color: #888; font-size: 11px;")
        self.notification_permission_hint.setWordWrap(True)
        self.notification_permission_hint.setVisible(False)  # 默认隐藏，有内容时再显示
        from PySide6.QtWidgets import QSizePolicy
        self.notification_permission_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.notification_permission_hint.setFixedHeight(0)  # 隐藏时不占用空间
        behavior_layout.addWidget(self.notification_permission_hint)
        
        # 全局快捷键权限检查和引导（仅 macOS）
        if system == "Darwin":
            hotkey_permission_row = QHBoxLayout()
            self.hotkey_permission_label = QLabel("全局快捷键权限：")
            self.hotkey_permission_status = QLabel("检查中...")
            self.hotkey_permission_btn = QPushButton("打开系统设置")
            self.hotkey_permission_btn.setFixedWidth(100)
            self.hotkey_permission_btn.setFixedHeight(28)
            self.hotkey_permission_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            self.hotkey_permission_btn.clicked.connect(self._open_accessibility_settings)
            
            # 添加刷新按钮，让用户可以手动刷新权限状态
            self.hotkey_permission_refresh_btn = QPushButton("刷新权限")
            self.hotkey_permission_refresh_btn.setFixedWidth(80)
            self.hotkey_permission_refresh_btn.setFixedHeight(28)
            self.hotkey_permission_refresh_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            self.hotkey_permission_refresh_btn.clicked.connect(self._refresh_hotkey_permission)
            
            hotkey_permission_row.addWidget(self.hotkey_permission_label)
            hotkey_permission_row.addWidget(self.hotkey_permission_status)
            hotkey_permission_row.addStretch()
            hotkey_permission_row.addWidget(self.hotkey_permission_refresh_btn)
            hotkey_permission_row.addWidget(self.hotkey_permission_btn)
            behavior_layout.addLayout(hotkey_permission_row)
            
            # 添加权限说明提示（当权限未授权时显示）
            self.hotkey_permission_hint = QLabel("")
            self.hotkey_permission_hint.setStyleSheet("color: #888; font-size: 11px;")
            self.hotkey_permission_hint.setWordWrap(True)
            self.hotkey_permission_hint.setVisible(False)  # 默认隐藏，有内容时再显示
            from PySide6.QtWidgets import QSizePolicy
            self.hotkey_permission_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self.hotkey_permission_hint.setFixedHeight(0)  # 隐藏时不占用空间
            behavior_layout.addWidget(self.hotkey_permission_hint)
            
            # 快捷键说明（macOS）
            hotkey_info_label = QLabel("快捷键：Control + A（打开隔空投送）")
            hotkey_info_label.setStyleSheet("color: #666; font-size: 11px;")
            behavior_layout.addWidget(hotkey_info_label)
        elif system == "Windows":
            # 快捷键说明（Windows）
            hotkey_info_label = QLabel("快捷键：Ctrl + Shift + A（打开隔空投送）")
            hotkey_info_label.setStyleSheet("color: #666; font-size: 11px;")
            behavior_layout.addWidget(hotkey_info_label)
        
        # 检查通知权限
        self._check_notification_permission()
        
        # 检查全局快捷键权限和状态（仅 macOS）
        if system == "Darwin":
            self._check_hotkey_permission()
            self._update_hotkey_status()
            # 如果快捷键已启用且权限已授权，尝试注册
            if self.chk_global_hotkey.isChecked():
                self._register_hotkey_if_enabled()
        
        layout.addWidget(behavior_frame)

        # --- 后端API服务状态 ---
        health_frame = QFrame()
        health_layout = QVBoxLayout(health_frame)
        health_layout.setSpacing(8)

        health_title = QLabel("后端API服务状态")
        health_title_font = QFont()
        health_title_font.setPointSize(12)
        health_title_font.setBold(True)
        health_title.setFont(health_title_font)
        health_layout.addWidget(health_title)

        self.health_status_label = QLabel("状态：检查中…")
        self.health_status_label.setFont(QFont("Arial", 10))
        health_layout.addWidget(self.health_status_label)

        self.health_time_label = QLabel("检查时间：--")
        self.health_time_label.setFont(QFont("Arial", 9))
        health_layout.addWidget(self.health_time_label)

        refresh_health_btn = QPushButton("刷新状态")
        refresh_health_btn.setFixedWidth(100)
        refresh_health_btn.setFixedHeight(28)
        refresh_health_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        refresh_health_btn.clicked.connect(self._load_api_health)
        health_layout.addWidget(refresh_health_btn)

        layout.addWidget(health_frame)

        # --- 版本信息 ---
        version_frame = QFrame()
        version_layout = QVBoxLayout(version_frame)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.setSpacing(4)

        version_label = QLabel("版本信息")
        version_label_font = QFont()
        version_label_font.setPointSize(12)
        version_label_font.setBold(True)
        version_label.setFont(version_label_font)
        version_layout.addWidget(version_label)

        # 从配置文件读取版本号
        client_version = self.cfg.get("client_version", "1.0.0")
        version_text = QLabel(f"Ai 绩效客户端 v{client_version}")
        version_text.setFont(QFont("Arial", 10))
        version_text.setStyleSheet("color: #666;")
        version_layout.addWidget(version_text)
        
        self._client_version = client_version

        layout.addWidget(version_frame)

        # 移除 addStretch，让内容自然填充
        
        # 初始化完成，允许自动保存
        self._is_initializing = False
        
        # 定时检查后端API服务状态（每3分钟）
        self._api_health_timer = QTimer()
        self._api_health_timer.timeout.connect(self._load_api_health)
        self._api_health_timer.setInterval(3 * 60 * 1000)  # 3分钟 = 180000毫秒
        
        # 立即加载一次，然后启动定时器
        self._load_api_health()
        self._api_health_timer.start()
    
    def showEvent(self, event):
        """页面显示时自动刷新权限状态"""
        super().showEvent(event)
        import platform
        
        # 检查对象是否仍然有效（防止页面切换时对象已被销毁）
        if not hasattr(self, 'notification_permission_status') or not self.notification_permission_status:
            return
        
        # 重新检查通知权限状态（用户可能从系统设置返回）
        try:
            self._check_notification_permission()
        except RuntimeError as e:
            # 对象已被销毁，忽略错误
            print(f"[Settings] showEvent: notification permission check failed: {e}", file=sys.stderr)
        
        if platform.system() == "Darwin":
            # 检查对象是否仍然有效
            if not hasattr(self, 'hotkey_permission_status') or not self.hotkey_permission_status:
                return
            
            # 重新检查快捷键权限状态（用户可能从系统设置返回）
            try:
                self._check_hotkey_permission()
                # 如果快捷键已启用，尝试注册
                if hasattr(self, 'chk_global_hotkey') and self.chk_global_hotkey and self.chk_global_hotkey.isChecked():
                    self._register_hotkey_if_enabled()
            except RuntimeError as e:
                # 对象已被销毁，忽略错误
                print(f"[Settings] showEvent: hotkey permission check failed: {e}", file=sys.stderr)

    # --------- 槽函数 ---------
    def on_google_login_clicked(self):
        """在设置页发起 Google 登录流程（对齐登录弹窗的流程）。"""
        # 防止重复点击
        if not hasattr(self, '_login_in_progress'):
            self._login_in_progress = False
        
        if self._login_in_progress:
            Toast.show_message(self, "登录正在进行中，请勿重复点击")
            return
        
        # 设置登录进行中标志
        self._login_in_progress = True
        
        main_window = self.window()
        
        # 在后台线程中执行登录
        class _LoginWorkerSignals(QObject):
            callback_received = Signal()  # 已收到回调，正在登录中
            finished = Signal()  # 登录成功
            error = Signal(str)  # 登录失败
        
        class _LoginWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _LoginWorkerSignals()
                self._should_stop = False
            
            def stop(self):
                """标记为应该停止"""
                self._should_stop = True
            
            def run(self):
                try:
                    from utils.google_login import login_and_get_id_token
                    
                    # 检查是否应该停止
                    if self._should_stop:
                        return
                    
                    # 定义回调函数：在收到 Google 回调后、调用后端接口前调用
                    def on_callback_received():
                        # 检查是否应该停止
                        if self._should_stop:
                            return
                        # 通过信号通知 UI 线程更新状态
                        self.signals.callback_received.emit()
                    
                    login_and_get_id_token(callback_received_callback=on_callback_received)
                    
                    # 检查是否应该停止
                    if self._should_stop:
                        return
                    
                    self.signals.finished.emit()
                except GoogleLoginError as e:
                    if not self._should_stop:
                        self.signals.error.emit(str(e))
                except Exception as e:
                    if not self._should_stop:
                        self.signals.error.emit(f"登录异常：{e}")
        
        worker = _LoginWorker()
        
        # 显示"等待登录回调中"遮盖层（可关闭）
        def _on_cancel_login():
            """用户取消登录"""
            # 停止 worker
            if hasattr(worker, 'stop'):
                worker.stop()
            # 从列表中移除 worker
            if hasattr(self, "_login_workers") and worker in self._login_workers:
                self._login_workers.remove(worker)
            # 隐藏加载遮罩
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            # 重置登录状态
            self._login_in_progress = False
            # 强制退出应用（因为 run_local_server 无法中断）
            import os
            os._exit(0)
        
        if hasattr(main_window, "show_loading"):
            main_window.show_loading(
                "等待登录回调中...\n请完成浏览器中的授权操作后回到软件界面",
                closeable=True,
                close_callback=_on_cancel_login
            )
        
        def _on_callback_received():
            """已收到回调，正在登录中"""
            if hasattr(main_window, "show_loading"):
                main_window.show_loading("已成功接收到谷歌回调信息，正在登录中...", closeable=False)
        
        def _on_login_success():
            """登录成功回调"""
            self._login_in_progress = False  # 重置登录状态
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            
            # login_and_get_id_token 内部已调用后端 /auth/google_login 并刷新了配置
            try:
                self.cfg = ConfigManager.load()
            except Exception:
                self.cfg = {}

            # 只读展示 ID Token（调试用）
            self.token_edit.setText(self.cfg.get("google_id_token", ""))

            # 更新邮箱显示
            if hasattr(self, "email_value"):
                self.email_value.setText(self.cfg.get("user_email", "") or "（未登录）")

            # 展示 session_token（只读）
            if hasattr(self, "session_edit"):
                self.session_edit.setText(self.cfg.get("session_token", ""))

            # 更新按钮显隐
            self._refresh_login_buttons()

            Toast.show_message(self, "Google 登录成功")
            
            # 通知主窗口刷新当前页面（如果当前页面需要登录才能加载数据）
            if hasattr(main_window, "refresh_current_page_after_login"):
                main_window.refresh_current_page_after_login()
        
        def _on_login_error(error_msg: str):
            """登录失败回调"""
            self._login_in_progress = False  # 重置登录状态
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            
            # 清理 worker 引用
            if hasattr(self, "_login_workers") and worker in self._login_workers:
                self._login_workers.remove(worker)
            
            # 如果是权限错误，使用 QMessageBox 显示更详细的提示
            if "无权限" in error_msg or "权限" in error_msg:
                from PySide6.QtWidgets import QMessageBox
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("登录失败 - 无权限")
                msg_box.setText(f"您的邮箱没有访问权限。\n\n{error_msg}\n\n请联系管理员添加您的邮箱到系统白名单。")
                msg_box.setIcon(QMessageBox.Icon.Warning)
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.exec()  # 使用 exec() 而不是 show()，确保对话框关闭后继续执行
            else:
                # 其他错误使用 Toast 显示
                Toast.show_message(self, f"Google 登录失败：{error_msg}")
        
        worker.signals.callback_received.connect(_on_callback_received)
        worker.signals.finished.connect(_on_login_success)
        worker.signals.error.connect(_on_login_error)
        
        # 保存worker引用，防止被垃圾回收
        if not hasattr(self, "_login_workers"):
            self._login_workers = []
        self._login_workers.append(worker)
        
        QThreadPool.globalInstance().start(worker)

    def on_google_logout_clicked(self):
        """清除本地登录状态，相当于退出登录。"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}

        self.cfg["google_id_token"] = ""
        self.cfg["session_token"] = ""
        self.cfg["user_id"] = ""
        self.cfg["user_name"] = ""
        self.cfg["user_email"] = ""
        ConfigManager.save(self.cfg)

        self.token_edit.setText("")
        if hasattr(self, "email_value"):
            self.email_value.setText("（未登录）")
        if hasattr(self, "session_edit"):
            self.session_edit.setText("")

        # 停止隔空投送服务（注销 mDNS 服务，让其他端知道设备已离线）
        try:
            main_window = self.window()
            if main_window and hasattr(main_window, '_airdrop_window') and main_window._airdrop_window:
                if hasattr(main_window._airdrop_window, '_transfer_manager') and main_window._airdrop_window._transfer_manager:
                    main_window._airdrop_window._transfer_manager.stop()
        except Exception:
            pass

        self._refresh_login_buttons()
        Toast.show_message(self, "已退出登录")

    def _refresh_login_buttons(self):
        """根据是否存在 session_token 切换"谷歌授权登录 / 退出登录"按钮显示。"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        logged_in = bool(self.cfg.get("session_token"))

        if hasattr(self, "btn_google_login"):
            self.btn_google_login.setVisible(not logged_in)
        if hasattr(self, "btn_google_logout"):
            self.btn_google_logout.setVisible(logged_in)

    def refresh_login_status(self):
        """刷新登录状态显示（当从其他页面登录成功后调用）"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        
        # 更新邮箱显示
        if hasattr(self, "email_value"):
            self.email_value.setText(self.cfg.get("user_email", "") or "（未登录）")
        
        # 更新 ID Token 显示
        if hasattr(self, "token_edit"):
            self.token_edit.setText(self.cfg.get("google_id_token", ""))
        
        # 更新 session_token 显示
        if hasattr(self, "session_edit"):
            self.session_edit.setText(self.cfg.get("session_token", ""))
        
        # 更新按钮显隐
        self._refresh_login_buttons()

    def _auto_save_api_base(self):
        """自动保存 API 地址"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["api_base"] = self.api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_api_base_changed(self):
        """API地址改变时（失去焦点或按回车）立即保存并刷新状态"""
        # 停止定时器（如果正在运行）
        if self._api_save_timer.isActive():
            self._api_save_timer.stop()
        
        # 立即保存
        self._auto_save_api_base()
        
        # 刷新登录状态（因为API地址改变后，需要重新检查登录状态）
        self.refresh_login_status()

    def _auto_save_theme(self, theme: str):
        """自动保存主题设置并立即应用"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["theme"] = theme
        ConfigManager.save(self.cfg)
        ThemeManager.apply_theme()
        # 同步主题按钮的配色
        self._update_theme_buttons_style(theme)

    def _resolve_effective_theme(self, pref: str) -> str:
        """根据用户偏好与系统，得出实际主题（light/dark）。"""
        if pref == "auto":
            try:
                return ThemeManager.detect_system_theme()
            except Exception:
                return "light"
        return pref if pref in ("light", "dark") else "light"

    def _update_theme_buttons_style(self, pref: str):
        """根据当前主题调整按钮配色，保证暗色/亮色都可读。"""
        effective = self._resolve_effective_theme(pref)
        if effective == "dark":
            bg = "#2d2d2d"
            bg_hover = "#383838"
            border = "#555"
            checked_bg = "#3d7bfd"
            checked_border = "#3d7bfd"
            text = "#e8e8e8"
            checked_text = "#ffffff"
        else:
            bg = "#f6f6f6"
            bg_hover = "#f0f0f0"
            border = "#c7c7c7"
            checked_bg = "#0078d4"
            checked_border = "#0078d4"
            text = "#222"
            checked_text = "#ffffff"

        style = (
            "QPushButton {"
            f"  border: 1px solid {border};"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            f"  background: {bg};"
            f"  color: {text};"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            f"  background: {bg_hover};"
            "}"
            "QPushButton:checked {"
            f"  background: {checked_bg};"
            f"  color: {checked_text};"
            f"  border-color: {checked_border};"
            "}"
        )

        for btn in getattr(self, "theme_buttons", []):
            btn.setStyleSheet(style)

    def _auto_save_auto_refresh(self, state: int):
        """自动保存自动刷新设置"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["auto_refresh"] = (state == 2)  # 2 表示选中状态
        ConfigManager.save(self.cfg)

    def _auto_save_notifications(self, state: int):
        """自动保存通知设置"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["notifications"] = (state == 2)  # 2 表示选中状态
        ConfigManager.save(self.cfg)
        
        # 如果启用了通知，检查权限
        if state == 2:
            self._check_notification_permission()

    def _auto_save_log_retention(self, value: int):
        """自动保存日志保留时长（小时）"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        hours = max(1, int(value))
        self.cfg["log_retention_hours"] = hours
        ConfigManager.save(self.cfg)
        # 立即按新策略清理旧日志
        try:
            log_dir = Path(CONFIG_PATH.parent / "logs")
            if log_dir.exists():
                deadline = datetime.now() - timedelta(hours=hours)
                for f in log_dir.glob("*.log"):
                    try:
                        if datetime.fromtimestamp(f.stat().st_mtime) < deadline:
                            f.unlink()
                    except Exception:
                        pass
        except Exception:
            pass
        Toast.show_message(self, f"日志将保留最近 {hours} 小时")

    def _export_logs(self):
        """导出当前保留范围内的日志为 zip"""
        log_dir = Path(CONFIG_PATH.parent / "logs")
        if not log_dir.exists():
            Toast.show_message(self, "暂无日志可导出")
            return

        retention_hours = int(self.cfg.get("log_retention_hours", 1) or 1)
        deadline = datetime.now(timezone.utc).astimezone() - timedelta(hours=retention_hours)

        # 收集符合保留时长的日志
        log_files = []
        for f in log_dir.glob("*.log"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).astimezone()
                if mtime >= deadline:
                    log_files.append(f)
            except Exception:
                pass

        if not log_files:
            Toast.show_message(self, "暂无符合保留时长的日志")
            return

        default_name = Path.home() / f"ai-perf-logs-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志",
            str(default_name),
            "Zip 文件 (*.zip)"
        )
        if not file_path:
            return

        try:
            with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for f in log_files:
                    zf.write(f, arcname=f.name)
            Toast.show_message(self, "日志导出成功")
        except Exception as e:
            Toast.show_message(self, f"导出失败：{e}")
            print(f"[Settings] Export logs failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    def _auto_save_global_hotkey(self, state: int):
        """自动保存全局快捷键设置"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        enabled = (state == 2)  # 2 表示选中状态
        self.cfg["global_hotkey_enabled"] = enabled
        ConfigManager.save(self.cfg)
        
        # 更新快捷键状态
        self._check_hotkey_permission()
        self._update_hotkey_status()
        
        # 如果启用了快捷键，尝试注册；如果禁用了，取消注册
        if enabled:
            self._register_hotkey_if_enabled()
        else:
            self._unregister_hotkey_if_disabled()
    
    def _check_notification_permission(self):
        """检查通知权限并更新UI"""
        # 检查对象是否仍然有效（防止页面切换时对象已被销毁）
        if not hasattr(self, 'notification_permission_status') or not self.notification_permission_status:
            return
        
        from utils.notification import SystemNotification
        import platform
        
        system = platform.system()
        
        try:
            if system == "Darwin":  # macOS
                permission = SystemNotification.check_permission()
                if permission is True:
                    self.notification_permission_status.setText("已授权")
                    self.notification_permission_status.setStyleSheet("color: green;")
                    if hasattr(self, 'notification_permission_btn'):
                        self.notification_permission_btn.setVisible(False)
                    if hasattr(self, 'notification_permission_hint'):
                        self.notification_permission_hint.setText("")  # 清空提示
                        self.notification_permission_hint.setVisible(False)  # 隐藏提示
                        self.notification_permission_hint.setFixedHeight(0)  # 不占用空间
                elif permission is False:
                    self.notification_permission_status.setText("未授权")
                    self.notification_permission_status.setStyleSheet("color: red;")
                    if hasattr(self, 'notification_permission_btn'):
                        self.notification_permission_btn.setVisible(True)  # 始终显示，让用户可以重新开启
                    # 显示明确的提示信息
                    if hasattr(self, 'notification_permission_hint'):
                        self.notification_permission_hint.setText(
                            "💡 如果之前拒绝了权限，请点击「打开系统设置」按钮，"
                            "在系统设置中找到此应用并勾选以允许发送通知。"
                        )
                        self.notification_permission_hint.setVisible(True)  # 显示提示
                        self.notification_permission_hint.setMaximumHeight(16777215)  # 恢复最大高度
                else:  # None，无法确定
                    self.notification_permission_status.setText("未知（请尝试发送测试通知）")
                    self.notification_permission_status.setStyleSheet("color: orange;")
                    if hasattr(self, 'notification_permission_btn'):
                        self.notification_permission_btn.setVisible(True)
                    if hasattr(self, 'notification_permission_hint'):
                        self.notification_permission_hint.setText(
                            "💡 无法确定权限状态，请点击「打开系统设置」检查并授权。"
                        )
                        self.notification_permission_hint.setVisible(True)  # 显示提示
                        self.notification_permission_hint.setMaximumHeight(16777215)  # 恢复最大高度
            elif system == "Windows":
                # Windows 10+ 不需要显式权限
                self.notification_permission_status.setText("已启用（Windows 10+ 无需授权）")
                self.notification_permission_status.setStyleSheet("color: green;")
                if hasattr(self, 'notification_permission_btn'):
                    self.notification_permission_btn.setVisible(False)
                if hasattr(self, 'notification_permission_hint'):
                    self.notification_permission_hint.setText("")  # 清空提示
                    self.notification_permission_hint.setVisible(False)  # 隐藏提示
                    self.notification_permission_hint.setFixedHeight(0)  # 不占用空间
            else:
                self.notification_permission_status.setText("不支持的操作系统")
                self.notification_permission_status.setStyleSheet("color: gray;")
                if hasattr(self, 'notification_permission_btn'):
                    self.notification_permission_btn.setVisible(False)
                if hasattr(self, 'notification_permission_hint'):
                    self.notification_permission_hint.setText("")  # 清空提示
                    self.notification_permission_hint.setVisible(False)  # 隐藏提示
                    self.notification_permission_hint.setFixedHeight(0)  # 不占用空间
        except RuntimeError as e:
            # 对象已被销毁，忽略错误
            print(f"[Settings] _check_notification_permission: RuntimeError: {e}", file=sys.stderr)
    
    def _refresh_notification_permission(self):
        """手动刷新通知权限状态"""
        # 重新检查权限
        self._check_notification_permission()
        # 显示提示
        Toast.show_message(self, "权限状态已刷新")
    
    def _open_notification_settings(self):
        """打开系统通知设置"""
        from utils.notification import SystemNotification
        from PySide6.QtWidgets import QMessageBox
        import platform
        
        if SystemNotification.open_system_settings():
            system = platform.system()
            if system == "Darwin":
                msg = (
                    "已打开系统设置页面。\n\n"
                    "请在系统设置中找到此应用（Ai Perf Client 或 Python），"
                    "并允许发送通知。\n\n"
                    "路径：系统设置 > 通知\n\n"
                    "💡 如果之前拒绝了权限，现在可以在这里重新开启。\n\n"
                    "设置完成后，请返回应用并点击「刷新」按钮，"
                    "或等待自动更新（约2秒后）。"
                )
            else:
                msg = (
                    "已打开系统通知设置页面。\n\n"
                    "请在系统设置中允许此应用发送通知。\n\n"
                    "💡 如果之前拒绝了权限，现在可以在这里重新开启。\n\n"
                    "设置完成后，请返回应用并点击「刷新」按钮，"
                    "或等待自动更新（约2秒后）。"
                )
            
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("打开系统设置")
            msg_box.setText(msg)
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            
            # 延迟重新检查权限（给用户时间设置）
            def check_and_notify():
                """检查权限并显示提示"""
                self._check_notification_permission()
                Toast.show_message(self, "权限状态已更新")
            
            QTimer.singleShot(2000, check_and_notify)
    
    def _check_hotkey_permission(self):
        """检查全局快捷键权限并更新UI（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        # 检查对象是否仍然有效（防止页面切换时对象已被销毁）
        if not hasattr(self, 'hotkey_permission_status') or not self.hotkey_permission_status:
            return
        
        try:
            from utils.mac_hotkey import check_accessibility_permission
            permission = check_accessibility_permission()
            
            if permission is True:
                self.hotkey_permission_status.setText("已授权")
                self.hotkey_permission_status.setStyleSheet("color: green;")
                if hasattr(self, 'hotkey_permission_btn'):
                    self.hotkey_permission_btn.setVisible(False)
                if hasattr(self, 'hotkey_permission_hint'):
                    self.hotkey_permission_hint.setText("")  # 清空提示
                    self.hotkey_permission_hint.setVisible(False)  # 隐藏提示
                    self.hotkey_permission_hint.setFixedHeight(0)  # 不占用空间
            elif permission is False:
                self.hotkey_permission_status.setText("未授权")
                self.hotkey_permission_status.setStyleSheet("color: red;")
                if hasattr(self, 'hotkey_permission_btn'):
                    self.hotkey_permission_btn.setVisible(True)  # 始终显示，让用户可以重新开启
                # 显示明确的提示信息
                if hasattr(self, 'hotkey_permission_hint'):
                    self.hotkey_permission_hint.setText(
                        "💡 如果之前拒绝了权限，请点击「打开系统设置」按钮，"
                        "在系统设置中找到此应用并勾选以允许使用辅助功能。"
                    )
                    self.hotkey_permission_hint.setVisible(True)  # 显示提示
                    self.hotkey_permission_hint.setMaximumHeight(16777215)  # 恢复最大高度
            else:  # None，无法确定
                self.hotkey_permission_status.setText("未知")
                self.hotkey_permission_status.setStyleSheet("color: orange;")
                if hasattr(self, 'hotkey_permission_btn'):
                    self.hotkey_permission_btn.setVisible(True)
                if hasattr(self, 'hotkey_permission_hint'):
                    self.hotkey_permission_hint.setText(
                        "💡 无法确定权限状态，请点击「打开系统设置」检查并授权。"
                    )
                    self.hotkey_permission_hint.setVisible(True)  # 显示提示
                    self.hotkey_permission_hint.setMaximumHeight(16777215)  # 恢复最大高度
        except RuntimeError as e:
            # 对象已被销毁，忽略错误
            print(f"[Settings] _check_hotkey_permission: RuntimeError: {e}", file=sys.stderr)
        except Exception as e:
            if hasattr(self, 'hotkey_permission_status'):
                self.hotkey_permission_status.setText("检查失败")
                self.hotkey_permission_status.setStyleSheet("color: red;")
            if hasattr(self, 'hotkey_permission_btn'):
                self.hotkey_permission_btn.setVisible(True)
            if hasattr(self, 'hotkey_permission_hint'):
                self.hotkey_permission_hint.setText(
                    "💡 权限检查失败，请点击「打开系统设置」手动检查权限状态。"
                )
            print(f"[Settings] Failed to check hotkey permission: {e}", file=sys.stderr)
    
    def _refresh_hotkey_permission(self):
        """手动刷新权限状态（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        # 重新检查权限
        self._check_hotkey_permission()
        # 如果快捷键已启用，尝试注册
        self._register_hotkey_if_enabled()
        # 显示提示
        Toast.show_message(self, "权限状态已刷新")
    
    def _update_hotkey_status(self):
        """更新快捷键启用状态显示（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        if not hasattr(self, 'chk_global_hotkey'):
            return
        
        # 检查是否已启用
        enabled = self.chk_global_hotkey.isChecked()
        
        # 如果启用但权限未授权，显示提示
        if enabled:
            try:
                from utils.mac_hotkey import check_accessibility_permission
                permission = check_accessibility_permission()
                if permission is False:
                    # 启用但未授权，提示用户
                    self.hotkey_permission_status.setText("未授权（需要授权才能使用）")
                    self.hotkey_permission_status.setStyleSheet("color: red;")
                    self.hotkey_permission_btn.setVisible(True)  # 确保按钮可见
                    self.hotkey_permission_hint.setText(
                        "💡 快捷键已启用但权限未授权。请点击「打开系统设置」授权后，"
                        "快捷键将自动生效。如果之前拒绝了权限，现在可以重新开启。"
                    )
                    self.hotkey_permission_hint.setVisible(True)  # 显示提示
                    self.hotkey_permission_hint.setMaximumHeight(16777215)  # 恢复最大高度
            except:
                pass
    
    def _register_hotkey_if_enabled(self):
        """如果快捷键已启用，尝试注册（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        try:
            cfg = ConfigManager.load()
            enabled = cfg.get("global_hotkey_enabled", False)
            if not enabled:
                return
            
            from utils.mac_hotkey import MacGlobalHotkey, check_accessibility_permission
            permission = check_accessibility_permission()
            
            if permission is True:
                # 获取主窗口并注册快捷键
                main_window = self.window()
                if main_window and hasattr(main_window, '_show_airdrop'):
                    # 如果已经注册过，先取消注册
                    if hasattr(main_window, '_global_hotkey') and main_window._global_hotkey:
                        try:
                            main_window._global_hotkey.unregister()
                            main_window._global_hotkey = None
                        except:
                            pass
                    
                    # 注册新的快捷键
                    try:
                        main_window._global_hotkey = MacGlobalHotkey(main_window._show_airdrop)
                        # Toast.show_message(self, "全局快捷键已启用")
                    except Exception as e:
                        Toast.show_message(self, f"启用快捷键失败：{e}\n请检查辅助功能权限")
            else:
                Toast.show_message(self, "请先授予辅助功能权限")
        except Exception as e:
            print(f"[Settings] Error registering hotkey: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    def _unregister_hotkey_if_disabled(self):
        """如果快捷键已禁用，取消注册（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        try:
            # 获取主窗口并取消注册快捷键
            main_window = self.window()
            if main_window and hasattr(main_window, '_global_hotkey') and main_window._global_hotkey:
                try:
                    main_window._global_hotkey.unregister()
                    main_window._global_hotkey = None
                    Toast.show_message(self, "全局快捷键已禁用")
                except Exception:
                    pass
        except Exception:
            pass
    
    def _open_accessibility_settings(self):
        """打开系统辅助功能设置（仅 macOS）"""
        import platform
        if platform.system() != "Darwin":
            return
        
        try:
            from utils.mac_hotkey import open_accessibility_settings, get_macos_version
            from PySide6.QtWidgets import QMessageBox
            
            if open_accessibility_settings():
                # 根据 macOS 版本显示不同的提示信息
                macos_version = get_macos_version()
                if macos_version[0] >= 13:  # macOS 13 (Ventura) 及以上
                    msg = (
                        "已打开系统设置页面。\n\n"
                        "请在系统设置中找到此应用（Ai Perf Client 或 Python），"
                        "并勾选以允许使用辅助功能。\n\n"
                        "路径：系统设置 > 隐私与安全性 > 辅助功能\n\n"
                        "💡 如果之前拒绝了权限，现在可以在这里重新开启。\n\n"
                        "设置完成后，请返回应用并点击「刷新」按钮，"
                        "或等待自动更新（约2秒后）。"
                    )
                else:  # macOS 12 及以下
                    msg = (
                        "已打开系统偏好设置页面。\n\n"
                        "请在系统偏好设置中找到此应用（Ai Perf Client 或 Python），"
                        "并勾选以允许使用辅助功能。\n\n"
                        "路径：系统偏好设置 > 安全性与隐私 > 隐私 > 辅助功能\n\n"
                        "💡 如果之前拒绝了权限，现在可以在这里重新开启。\n\n"
                        "设置完成后，请返回应用并点击「刷新」按钮，"
                        "或等待自动更新（约2秒后）。"
                    )
                
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("打开系统设置")
                msg_box.setText(msg)
                msg_box.setIcon(QMessageBox.Icon.Information)
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.exec()
                
                # 延迟重新检查权限并注册快捷键（给用户时间设置）
                def check_and_register():
                    """检查权限并注册快捷键"""
                    self._check_hotkey_permission()
                    # 如果快捷键已启用，尝试注册
                    self._register_hotkey_if_enabled()
                    # 显示提示
                    Toast.show_message(self, "权限状态已更新")
                
                # 延迟检查，给用户时间完成设置
                QTimer.singleShot(2000, check_and_register)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("错误")
            msg_box.setText(f"无法打开系统设置：{e}")
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.exec()

    def _load_api_health(self):
        """加载后端API服务状态和版本信息"""
        worker = _ApiHealthWorker(current_version=self._client_version)
        worker.signals.finished.connect(self._on_api_health_loaded)
        worker.signals.error.connect(self._on_api_health_error)
        QThreadPool.globalInstance().start(worker)

    def _on_api_health_loaded(self, health_data: Dict[str, Any]):
        """后端API服务状态加载完成"""
        status = health_data.get('status', 'unknown')
        check_time = health_data.get('time', '')
        
        # 状态文本和颜色
        if status == 'ok':
            status_text = '正常'
            self.health_status_label.setText(f"状态：{status_text}")
            self.health_status_label.setStyleSheet("color: green;")
        else:
            status_text = '异常'
            self.health_status_label.setText(f"状态：{status_text}")
            self.health_status_label.setStyleSheet("color: red; font-weight: bold;")
        
        # 设置检查时间（API返回的是本地时间，直接格式化）
        if check_time:
            if isinstance(check_time, str):
                try:
                    # 尝试解析ISO格式
                    if 'Z' in check_time or '+' in check_time:
                        # 带时区信息，需要转换
                        dt = datetime.fromisoformat(check_time.replace('Z', '+00:00'))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        local_time = dt.astimezone()
                        time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # 无时区信息，假设是本地时间，直接格式化
                        dt = datetime.fromisoformat(check_time)
                        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    time_str = str(check_time)
            elif isinstance(check_time, datetime):
                if check_time.tzinfo is None:
                    time_str = check_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    local_time = check_time.astimezone()
                    time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
            else:
                time_str = str(check_time)
            self.health_time_label.setText(f"检查时间：{time_str}")
        else:
            self.health_time_label.setText("检查时间：--")
        
        # 检查版本升级
        version_info = health_data.get("version_info")
        if version_info:
            # 有新版本需要升级，显示强制升级弹窗
            self._show_update_dialog(version_info)

    def _on_api_health_error(self, message: str):
        """后端API服务状态加载失败"""
        self.health_status_label.setText("状态：检查失败")
        self.health_status_label.setStyleSheet("color: red; font-weight: bold;")
        self.health_time_label.setText("检查时间：--")
    
    def _show_update_dialog(self, version_info: dict):
        """显示版本升级弹窗"""
        is_force_update = version_info.get("is_force_update", True)
        new_version = version_info.get("version", "")
        
        # 非强制升级：检查今天是否已经关闭过弹窗
        if not is_force_update:
            try:
                cfg = ConfigManager.load()
                dismissed_date = cfg.get("update_dialog_dismissed_date", "")
                if dismissed_date == date.today().isoformat():
                    # 今天已经关闭过，不再显示
                    return
            except Exception:
                pass
        
        # 检查主窗口是否已经有升级弹窗在显示
        main_window = self.window()
        existing_dialog = None
        if hasattr(main_window, '_update_dialog') and main_window._update_dialog and main_window._update_dialog.isVisible():
            existing_dialog = main_window._update_dialog
        elif hasattr(self, '_update_dialog') and self._update_dialog and self._update_dialog.isVisible():
            existing_dialog = self._update_dialog
        
        # 如果已有弹窗在显示
        if existing_dialog:
            # 强制升级：检查版本是否有变化
            if is_force_update:
                existing_version = existing_dialog._version_info.get("version", "")
                if existing_version != new_version:
                    # 版本有变化，关闭旧弹窗，显示新弹窗
                    existing_dialog.close()
                    existing_dialog.deleteLater()
                else:
                    # 版本没变化，不重复显示
                    return
            else:
                # 非强制升级：已有弹窗在显示，不重复显示
                return
        
        # 如果已经显示过升级弹窗（且不是版本更新），不再重复显示
        if self._update_dialog_shown:
            return
        
        # 检查主窗口是否已经显示过升级弹窗（避免重复）
        if hasattr(main_window, '_update_dialog_shown') and main_window._update_dialog_shown:
            return
        
        self._update_dialog = UpdateDialog(self, self._client_version, version_info)
        self._update_dialog.show()
        self._update_dialog_shown = True
        
        # 同时标记主窗口，避免其他地方重复显示
        if hasattr(main_window, '_update_dialog_shown'):
            main_window._update_dialog_shown = True


class _ApiHealthWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class _ApiHealthWorker(QRunnable):
    """后台线程：获取后端API服务状态和版本信息（无需登录）"""
    def __init__(self, current_version: str):
        super().__init__()
        self.signals = _ApiHealthWorkerSignals()
        self._current_version = current_version

    @Slot()
    def run(self) -> None:
        try:
            import httpx
            from utils.config_manager import ConfigManager
            
            cfg = ConfigManager.load()
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8000").strip()
            
            # 直接使用HTTP请求，不需要登录
            url = f"{api_base}/api/health"
            params = {"current_version": self._current_version} if self._current_version else None
            r = httpx.get(url, params=params, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    health_data = data.get("data")
                    if health_data:
                        self.signals.finished.emit(health_data)
                    else:
                        self.signals.error.emit("无法获取后端API服务状态")
                else:
                    self.signals.error.emit("无法获取后端API服务状态")
            else:
                self.signals.error.emit(f"获取后端API服务状态失败：HTTP {r.status_code}")
        except Exception as e:
            self.signals.error.emit(f"获取后端API服务状态失败：{e}")

    def showEvent(self, event):
        """页面显示时启动定时器并重新检查权限"""
        super().showEvent(event)
        if hasattr(self, '_api_health_timer'):
            self._api_health_timer.start()
        
        # 重新检查权限（用户可能从系统设置返回）
        self._check_notification_permission()
        import platform
        if platform.system() == "Darwin":
            self._check_hotkey_permission()
            # 如果快捷键已启用，尝试重新注册
            self._register_hotkey_if_enabled()

    def hideEvent(self, event):
        """页面隐藏时停止定时器"""
        super().hideEvent(event)
        if hasattr(self, '_api_health_timer'):
            self._api_health_timer.stop()
