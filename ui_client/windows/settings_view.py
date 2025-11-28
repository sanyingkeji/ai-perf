from datetime import datetime, timezone
from typing import Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QCheckBox, QRadioButton, QHBoxLayout, QPushButton, QFrame, QDialog, QTextEdit
)
from PySide6.QtGui import QFont
from PySide6.QtCore import QTimer, QRunnable, QThreadPool, QObject, Signal, Slot, Qt

from utils.config_manager import ConfigManager
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)

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

        self.rb_auto = QRadioButton("跟随系统")
        self.rb_light = QRadioButton("浅色模式")
        self.rb_dark = QRadioButton("深色模式")

        theme_choice = self.cfg.get("theme", "auto")
        if theme_choice == "light":
            self.rb_light.setChecked(True)
        elif theme_choice == "dark":
            self.rb_dark.setChecked(True)
        else:
            self.rb_auto.setChecked(True)

        # 主题变更时自动保存并应用（使用 lambda 确保只在选中时触发）
        self.rb_auto.toggled.connect(lambda checked: self._auto_save_theme("auto") if checked and not self._is_initializing else None)
        self.rb_light.toggled.connect(lambda checked: self._auto_save_theme("light") if checked and not self._is_initializing else None)
        self.rb_dark.toggled.connect(lambda checked: self._auto_save_theme("dark") if checked and not self._is_initializing else None)

        theme_layout.addWidget(self.rb_auto)
        theme_layout.addWidget(self.rb_light)
        theme_layout.addWidget(self.rb_dark)

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
        
        # 通知权限检查和引导
        notification_permission_row = QHBoxLayout()
        self.notification_permission_label = QLabel("通知权限：")
        self.notification_permission_status = QLabel("检查中...")
        self.notification_permission_btn = QPushButton("打开系统设置")
        self.notification_permission_btn.setFixedWidth(120)
        self.notification_permission_btn.clicked.connect(self._open_notification_settings)
        notification_permission_row.addWidget(self.notification_permission_label)
        notification_permission_row.addWidget(self.notification_permission_status)
        notification_permission_row.addStretch()
        notification_permission_row.addWidget(self.notification_permission_btn)
        behavior_layout.addLayout(notification_permission_row)
        
        # 检查通知权限
        self._check_notification_permission()

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
        refresh_health_btn.setFixedWidth(120)
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

        layout.addStretch(1)
        
        # 初始化完成，允许自动保存
        self._is_initializing = False
        
        # 定时检查后端API服务状态（每3分钟）
        self._api_health_timer = QTimer()
        self._api_health_timer.timeout.connect(self._load_api_health)
        self._api_health_timer.setInterval(3 * 60 * 1000)  # 3分钟 = 180000毫秒
        
        # 立即加载一次，然后启动定时器
        self._load_api_health()
        self._api_health_timer.start()

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
    
    def _check_notification_permission(self):
        """检查通知权限并更新UI"""
        from utils.notification import SystemNotification
        import platform
        
        system = platform.system()
        
        if system == "Darwin":  # macOS
            permission = SystemNotification.check_permission()
            if permission is True:
                self.notification_permission_status.setText("已授权")
                self.notification_permission_status.setStyleSheet("color: green;")
                self.notification_permission_btn.setVisible(False)
            elif permission is False:
                self.notification_permission_status.setText("未授权")
                self.notification_permission_status.setStyleSheet("color: red;")
                self.notification_permission_btn.setVisible(True)
            else:  # None，无法确定
                self.notification_permission_status.setText("未知（请尝试发送测试通知）")
                self.notification_permission_status.setStyleSheet("color: orange;")
                self.notification_permission_btn.setVisible(True)
        elif system == "Windows":
            # Windows 10+ 不需要显式权限
            self.notification_permission_status.setText("已启用（Windows 10+ 无需授权）")
            self.notification_permission_status.setStyleSheet("color: green;")
            self.notification_permission_btn.setVisible(False)
        else:
            self.notification_permission_status.setText("不支持的操作系统")
            self.notification_permission_status.setStyleSheet("color: gray;")
            self.notification_permission_btn.setVisible(False)
    
    def _open_notification_settings(self):
        """打开系统通知设置"""
        from utils.notification import SystemNotification
        from PySide6.QtWidgets import QMessageBox
        import platform
        
        if SystemNotification.open_system_settings():
            system = platform.system()
            if system == "Darwin":
                msg = "已打开系统通知设置页面。\n\n请在系统设置中找到此应用（Ai Perf Client 或 Python），并允许发送通知。\n\n设置完成后，请返回应用，通知权限状态会自动更新。"
            else:
                msg = "已打开系统通知设置页面。\n\n请在系统设置中允许此应用发送通知。"
            
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("打开系统设置")
            msg_box.setText(msg)
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
            
            # 延迟重新检查权限（给用户时间设置）
            QTimer.singleShot(2000, self._check_notification_permission)

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
        """页面显示时启动定时器"""
        super().showEvent(event)
        if hasattr(self, '_api_health_timer'):
            self._api_health_timer.start()

    def hideEvent(self, event):
        """页面隐藏时停止定时器"""
        super().hideEvent(event)
        if hasattr(self, '_api_health_timer'):
            self._api_health_timer.stop()
