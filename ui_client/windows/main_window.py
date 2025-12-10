from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QDialog, QPushButton, QSizePolicy,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt, QSize, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
from typing import Optional
import platform
from pathlib import Path

from windows.today_view import TodayView
from windows.history_view import HistoryView
from windows.review_view import ReviewView
from windows.ranking_view import RankingView
from windows.settings_view import SettingsView
from windows.update_dialog import UpdateDialog
from windows.help_center_window import HelpCenterWindow
from windows.data_trend_view import DataTrendView
from utils.config_manager import ConfigManager
from utils.notification import send_notification
from utils.google_login import login_and_get_id_token, GoogleLoginError
from utils.api_client import ApiClient, ApiError, AuthError
from utils.polling_service import get_polling_service
from utils.resource_path import get_app_icon_path
from widgets.toast import Toast
from widgets.loading_overlay import LoadingOverlay
from datetime import date, datetime
import sys


class MainWindow(QMainWindow):
    @staticmethod
    def _log_with_timestamp(message: str):
        """打印带时间戳的日志（精确到毫秒）"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # 精确到毫秒
        print(f"[{timestamp}] {message}", file=sys.stderr)
    
    @staticmethod
    def _get_macos_y_offset(window=None):
        """获取 macOS Y 坐标偏移量（用于补偿系统自动调整）
        
        在 macOS 上，系统可能会自动调整窗口的 Y 坐标（通常是标题栏高度），
        导致 geometry().y() 和 pos().y() 有差值。这个方法动态检测这个偏移量。
        
        Args:
            window: 窗口对象，如果提供则动态检测，否则根据系统版本估算
        
        Returns:
            int: Y 坐标偏移量（像素），非 macOS 系统返回 0
        """
        import platform
        if platform.system() != "Darwin":
            return 0  # Windows/Linux 不需要偏移
        
        # 如果提供了窗口对象，动态检测偏移量
        if window is not None:
            try:
                geo = window.geometry()
                pos = window.pos()
                # 计算差值（通常是标题栏高度）
                offset = geo.y() - pos.y()
                if offset > 0:
                    return offset
            except:
                pass
        
        # 如果动态检测失败，根据 macOS 版本估算
        try:
            import platform as plat
            mac_version = plat.mac_ver()[0]  # 例如 "14.7.8"
            if mac_version:
                major_version = int(mac_version.split('.')[0])
                # macOS 11+ 通常有 28 像素偏移（标题栏高度）
                # macOS 10.13-10.15 可能偏移不同或没有偏移
                if major_version >= 11:
                    return 28
                elif major_version == 10:
                    # macOS 10.13-10.15，可能需要检测，暂时返回 0
                    # 如果实际测试发现有偏移，可以调整
                    return 0
        except:
            pass
        
        return 0  # 默认不偏移
    
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Ai 绩效客户端 —— 让数据说话，让努力被看见")
        self.resize(1200, 780)
        
        # 设置窗口图标（如果应用图标未设置，这里会使用应用图标）
        app_dir = Path(__file__).parent.parent
        icon_paths = [
            app_dir / "resources" / "app_icon.icns",
            app_dir / "resources" / "app_icon.ico",
            app_dir / "resources" / "app_icon.png",
        ]
        for icon_path in icon_paths:
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                break

        # 标记各页面是否已做过"首次自动加载"
        self._page_first_loaded = {
            0: False,  # 今日评分
            1: False,  # 历史评分
            2: False,  # 复评中心（当前无自动拉取，仅预留）
            3: False,  # 排行榜
            4: False,  # 消息
            5: False,  # 我的
            6: False,  # 设置
        }
        # 图文趋势菜单与页面索引（延后赋值）
        self.DATA_TREND_PAGE_INDEX = None
        self.data_trend_url: Optional[str] = None
        self._data_trend_last_loaded_url: Optional[str] = None
        self._last_help_text: Optional[str] = None  # 保存上次的 help_text，用于比较
        self.DEFAULT_DATA_TREND_TEXT = "数据趋势"

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)

        # 左侧：导航栏
        nav_container = QWidget()
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(12, 1, 12, 12)
        nav_layout.setSpacing(12)

        logo = QLabel("Ai Perf")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size:20px; font-weight:bold;")
        nav_layout.addWidget(logo)

        self.nav = QListWidget()
        self.nav.setStyleSheet("""
            QListWidget {
                border-top: 1px solid rgba(0, 0, 0, 0.12);
                border-bottom: 1px solid rgba(0, 0, 0, 0.06);
                outline: none;
            }
            QListWidget::item:focus,
            QListWidget::item:selected:focus,
            QListWidget::item {
                outline: none;
            }
        """)
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.addItem("今日评分")
        self.nav.addItem("历史评分")
        self.nav.addItem("复评中心")
        # 图文趋势：放在复评中心后、排行榜前
        self.data_trend_item = QListWidgetItem(self.DEFAULT_DATA_TREND_TEXT)
        self.data_trend_item.setHidden(True)  # 默认隐藏
        self.nav.addItem(self.data_trend_item)
        self.nav.addItem("排行榜")
        self.nav.addItem("消息")
        self.nav.addItem("我的")
        self.nav.addItem("设置")
        # 自动适配内容宽度
        # 根据内容计算合适的宽度
        max_width = 0
        font_metrics = self.nav.fontMetrics()
        for i in range(self.nav.count()):
            item_text = self.nav.item(i).text()
            text_width = font_metrics.horizontalAdvance(item_text)
            max_width = max(max_width, text_width)
        def recalc_nav_width():
            """根据当前菜单文本长度动态调整导航宽度"""
            font_metrics = self.nav.fontMetrics()
            max_width_local = 0
            for i in range(self.nav.count()):
                item = self.nav.item(i)
                if not item:
                    continue
                text_width = font_metrics.horizontalAdvance(item.text())
                max_width_local = max(max_width_local, text_width)
            content_width_local = max_width_local + 40  # 左右内边距 + 图标空间
            nav_width_local = max(120, content_width_local)
            nav_width_local = min(nav_width_local, 300)
            self.nav.setFixedWidth(nav_width_local)

        # 初始计算宽度
        recalc_nav_width()
        
        # 隐藏 Windows 上的焦点虚线框
        self.nav.setStyleSheet("""
            QListWidget::item:focus {
                outline: none;
            }
            QListWidget::item:selected:focus {
                outline: none;
            }
        """)
        # 设置大小策略，让菜单根据内容自动调整高度，而不是显示滚动条
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        nav_layout.addWidget(self.nav)
        nav_layout.addStretch()  # 恢复 addStretch()，让菜单在顶部，底部留空
        
        # 帮助中心（放在菜单底部，默认隐藏，只有接收到 help_text 时才显示）
        self.help_label = QLabel("帮助中心")
        self.help_label.setAlignment(Qt.AlignCenter)
        self.help_label.setStyleSheet("""
            QLabel {
                padding: 10px 14px;
                border-radius: 4px;
            }
            QLabel:hover {
                background-color: rgba(58, 122, 254, 0.1);
            }
        """)
        # 设置为可点击
        self.help_label.mousePressEvent = self._on_help_center_clicked
        # 默认隐藏，只有接收到 help_text 时才显示
        self.help_label.setVisible(False)
        nav_layout.addWidget(self.help_label)
        
        # 延迟计算菜单高度，确保在窗口显示后准确计算
        from PySide6.QtCore import QTimer
        def adjust_nav_height():
            """根据菜单项数量自动调整菜单高度"""
            visible_indices = [
                i for i in range(self.nav.count())
                if self.nav.item(i) and not self.nav.item(i).isHidden()
            ]
            if not visible_indices:
                self.nav.setFixedHeight(30)
                return

            first_index = visible_indices[0]
            item_height = self.nav.sizeHintForRow(first_index)
            if item_height <= 0:
                item_height = 30  # 默认高度

            total_height = len(visible_indices) * item_height + 4
            self.nav.setFixedHeight(total_height)
        
        # 暴露方法，便于后续动态调整（例如图文趋势菜单的显隐）
        self._adjust_nav_height = adjust_nav_height
        self._recalculate_nav_width = recalc_nav_width
        # 延迟执行，确保窗口已显示
        QTimer.singleShot(100, self._adjust_nav_height)

        root_layout.addWidget(nav_container)

        # 右侧：页面 stack
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        # 各页面
        from windows.notification_view import NotificationView
        from windows.profile_view import ProfileView
        self.today_page = TodayView()
        self.history_page = HistoryView()
        self.review_page = ReviewView()
        self.ranking_page = RankingView()
        self.notification_page = NotificationView(self)
        self.profile_page = ProfileView(self)
        self.settings_page = SettingsView()
        self.data_trend_page = DataTrendView(self)

        self.stack.addWidget(self.today_page)
        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.review_page)
        self.stack.addWidget(self.data_trend_page)
        self.stack.addWidget(self.ranking_page)
        self.stack.addWidget(self.notification_page)
        self.stack.addWidget(self.profile_page)
        self.stack.addWidget(self.settings_page)
        self.DATA_TREND_PAGE_INDEX = self.stack.indexOf(self.data_trend_page)
        self._page_first_loaded[self.DATA_TREND_PAGE_INDEX] = False
        # 启动时强制隐藏图文趋势入口，等待接口决定是否展示
        self._ensure_data_trend_hidden_by_default()

        # 全局加载中遮罩
        self.loading_overlay = LoadingOverlay(self)

        # 绑定导航
        self.nav.currentRowChanged.connect(self.switch_page)
        # 默认选中第一个 Tab，会触发一次 switch_page(0)
        self.nav.setCurrentRow(0)
        
        # 标记：是否已经显示过升级弹窗（防止重复弹窗）
        self._update_dialog_shown = False
        
        # 标记：是否正在显示登录提示（防止重复弹窗）
        self._login_dialog_shown = False
        
        # 标记：是否已经尝试过更新服务配置（防止重复弹出服务更新弹窗）
        # 从配置文件读取，避免每次启动都尝试更新
        try:
            cfg = ConfigManager.load()
            self._service_update_attempted = cfg.get("service_update_attempted", False)
        except Exception:
            self._service_update_attempted = False
        
        # 应用启动时检查版本升级（延迟检查，等待窗口显示）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, self._check_version_on_startup)
        
        # 应用启动时检查登录状态（延迟检查，等待窗口显示）
        QTimer.singleShot(1500, self._check_login_on_startup)
        
        # 启动统一轮询服务（延迟启动，等待登录完成）
        QTimer.singleShot(3000, self._start_polling_service)
        
        # 安装并启用后台通知服务（应用未运行时也能接收通知）
        QTimer.singleShot(2000, self._setup_background_notification_service)
        
        # 启动帮助中心文字动态更新定时器（定期检查 help_text）
        self._help_text_timer = QTimer()
        self._help_text_timer.timeout.connect(self._check_help_text)
        self._help_text_timer.start(30000)  # 每30秒检查一次
        # 立即执行一次
        QTimer.singleShot(2000, self._check_help_text)
        
        # 设置系统托盘（macOS 和 Windows 都支持）
        self._tray_icon = None
        self._setup_system_tray()
        
        # 应用窗口标题栏主题适配（延迟执行，确保窗口已显示）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, self._apply_window_title_bar_theme)
        
        # 监听主题变化，动态更新标题栏
        self._theme_check_timer = QTimer()
        self._theme_check_timer.timeout.connect(self._check_and_update_title_bar_theme)
        self._theme_check_timer.start(1000)  # 每秒检查一次
        
        # 隔空投送窗口
        self._airdrop_window = None
        
        self._global_hotkey = None
        if platform.system() == "Windows":
            from utils.win_hotkey import WindowsGlobalHotkey
            try:
                self._global_hotkey = WindowsGlobalHotkey(self, self._show_airdrop)
            except Exception:
                pass
        elif platform.system() == "Darwin":
            # macOS: 不在启动时自动注册快捷键，让用户在设置页面手动启用
            # 检查用户配置，如果已启用则尝试注册
            try:
                cfg = ConfigManager.load()
                hotkey_enabled = cfg.get("global_hotkey_enabled", False)
                if hotkey_enabled:
                    from utils.mac_hotkey import MacGlobalHotkey, check_accessibility_permission
                    permission = check_accessibility_permission()
                    if permission is True:
                        try:
                            self._global_hotkey = MacGlobalHotkey(self._show_airdrop)
                        except Exception:
                            pass
            except Exception:
                pass

        # 启动应用时自动打开隔空投送窗口，并在1秒后自动隐藏
        QTimer.singleShot(500, self._startup_airdrop_with_autohide)
        
        # macOS: 确保应用在窗口关闭后仍然运行
        if platform.system() == "Darwin":
            # 将托盘图标保存为类变量，确保窗口关闭后仍然存在
            if not hasattr(MainWindow, '_app_tray_icon'):
                MainWindow._app_tray_icon = self._tray_icon


    # -------- 登录状态检查 --------
    def _ensure_logged_in(self) -> bool:
        """
        仅检查本地是否存在有效 session_token，不做 UI 提示。
        主要用于 Tab 首次自动加载时的快速判断。
        """
        try:
            cfg = ConfigManager.load()
        except Exception:
            return False

        token = (cfg.get("session_token") or "").strip()
        return bool(token)


    def show_login_required_dialog(self, force: bool = False) -> bool:
        """
        弹出"当前未登录"提示框，并可直接发起 Google 授权登录。
        
        Args:
            force: 是否强制显示（忽略重复弹窗检查）
        
        返回 True 表示登录成功，False 表示未登录。
        """
        # 如果已经有登录弹窗在显示，且不是强制显示，则不再重复弹窗
        if not force and self._login_dialog_shown:
            return False
        
        dlg = QDialog(self)
        dlg.setWindowTitle("登录提示")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        label = QLabel("当前未登录，请用工作邮箱进行谷歌授权登录")
        label.setWordWrap(True)
        layout.addWidget(label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_login = QPushButton("谷歌授权登录")
        btn_close = QPushButton("关闭")
        btn_row.addWidget(btn_login)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        result = {"logged_in": False, "dialog_closed": False}
        
        # 标记登录弹窗正在显示
        self._login_dialog_shown = True
        
        # 标记：是否正在登录中（防止重复点击登录按钮）
        if not hasattr(self, '_login_in_progress'):
            self._login_in_progress = False

        def _do_login():
            # 防止重复点击
            if self._login_in_progress:
                Toast.show_message(self, "登录正在进行中，请勿重复点击")
                return
            
            # 设置登录进行中标志
            self._login_in_progress = True
            
            # 关闭对话框
            dlg.accept()
            result["dialog_closed"] = True
            
            # 显示"等待登录回调中"遮盖层（可关闭）
            # 在后台线程中执行登录
            from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal
            
            class _LoginWorkerSignals(QObject):
                callback_received = Signal()  # 已收到回调，正在登录中
                finished = Signal()  # 登录成功
                error = Signal(str)  # 登录失败
            
            class _LoginWorker(QRunnable):
                def __init__(self, main_window):
                    super().__init__()
                    self.signals = _LoginWorkerSignals()
                    self._main_window = main_window
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
                        
                        # login_and_get_id_token 内部会：
                        # 1. 打开浏览器（run_local_server）
                        # 2. 等待用户完成授权（阻塞）
                        # 3. 收到回调后，调用 on_callback_received
                        # 4. 然后调用后端接口（网络耗时）
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
            
            worker = _LoginWorker(self)
            
            def _on_cancel_login():
                """用户取消登录"""
                # 停止 worker
                if hasattr(worker, 'stop'):
                    worker.stop()
                # 从列表中移除 worker
                if hasattr(self, '_login_worker') and worker in self._login_worker:
                    self._login_worker.remove(worker)
                # 隐藏加载遮罩
                self.hide_loading()
                # 重置登录状态
                self._login_in_progress = False
                # 强制退出应用（因为 run_local_server 无法中断）
                import os
                os._exit(0)
            
            self.show_loading(
                "等待登录回调中...\n请完成浏览器中的授权操作后回到软件界面",
                closeable=True,
                close_callback=_on_cancel_login
            )
            
            def _on_callback_received():
                """已收到回调，正在登录中（此时关闭按钮应该隐藏，因为正在处理中）"""
                self.show_loading("已成功接收到谷歌回调信息，正在登录中...", closeable=False)
            
            def _on_login_success():
                self.hide_loading()
                self._login_in_progress = False  # 重置登录状态
                result["logged_in"] = True
                Toast.show_message(self, "Google 登录成功")
                # 登录成功后刷新当前页面
                self.refresh_current_page_after_login()
                # 启动轮询服务
                self._start_polling_service()
                # 登录成功后隔1秒打开隔空投送并自动隐藏（与启动时逻辑对齐）
                QTimer.singleShot(1000, self._open_airdrop_after_login)
            
            def _on_login_error(error_msg: str):
                self.hide_loading()
                self._login_in_progress = False  # 重置登录状态
                # 清理 worker 引用
                if hasattr(self, '_login_worker') and worker in self._login_worker:
                    self._login_worker.remove(worker)
                
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
            if not hasattr(self, '_login_worker'):
                self._login_worker = []
            self._login_worker.append(worker)
            
            QThreadPool.globalInstance().start(worker)

        btn_login.clicked.connect(_do_login)
        btn_close.clicked.connect(dlg.reject)

        dlg.exec()
        
        # 登录弹窗已关闭，重置标志
        self._login_dialog_shown = False

        # 如果登录正在进行，等待完成（最多5分钟）
        if result.get("dialog_closed", False) and not result.get("logged_in", False):
            import time
            from PySide6.QtWidgets import QApplication
            timeout = 300  # 最多等待5分钟
            elapsed = 0
            while elapsed < timeout and not result.get("logged_in", False):
                QApplication.processEvents()
                time.sleep(0.1)
                elapsed += 0.1

        return bool(result["logged_in"])

    def refresh_current_page_after_login(self):
        """
        登录成功后：
        1）重置所有需要登录的页面的加载状态（这样切换页面时会自动请求）
        2）刷新当前页面（并标记为已加载，避免重复加载）
        """
        # 重置所有需要登录的页面的加载状态
        # 这样，登录后切换页面时，如果该页面还没请求过，就会自动请求
        for page_index in (0, 1, 2, 3, 4, 5):  # 今日评分、历史评分、复评中心、排行榜、消息、我的
            self._page_first_loaded[page_index] = False
        
        current_index = self.stack.currentIndex()
        
        # 根据当前页面索引刷新对应的页面
        if current_index == 0 and hasattr(self.today_page, "refresh_from_api"):
            # 今日评分
            self.today_page.refresh_from_api(silent=True)
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[0] = True
        elif current_index == 1 and hasattr(self.history_page, "reload_from_api"):
            # 历史评分
            self.history_page.reload_from_api()
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[1] = True
        elif current_index == 2 and hasattr(self.review_page, "reload_from_api"):
            # 复评中心
            self.review_page.reload_from_api()
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[2] = True
        elif current_index == 3 and hasattr(self.ranking_page, "refresh_from_api"):
            # 排行榜
            self.ranking_page.refresh_from_api(silent=True)
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[3] = True
        elif current_index == 4 and hasattr(self.notification_page, "reload_from_api"):
            # 消息
            self.notification_page.reload_from_api()
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[4] = True
        elif current_index == 5 and hasattr(self.profile_page, "reload_from_api"):
            # 我的
            self.profile_page.reload_from_api()
            # 标记为已加载，避免切换时重复加载
            self._page_first_loaded[5] = True

    # -------- 全局 Loading 控制，供子页面调用 --------
    def show_loading(self, message: str = "加载中…", closeable: bool = False, close_callback=None) -> None:
        """
        在主窗口上显示半透明"加载中"遮罩。
        
        Args:
            message: 提示文案
            closeable: 是否显示关闭按钮
            close_callback: 关闭按钮的回调函数
        """
        if self.loading_overlay is None:
            return
        self.loading_overlay.show_message(message, closeable=closeable, close_callback=close_callback)

    def hide_loading(self) -> None:
        """
        隐藏“加载中”遮罩。
        """
        if self.loading_overlay is None:
            return
        self.loading_overlay.hide_safely()

    # -------- Tab 切换 & 首次自动拉取 --------
    def switch_page(self, index: int):
        """
        1）切换页面；
        2）在"已登录"前提下，对每个业务 Tab 做一次"首次自动拉取 API"：
           - 仅首次切到该 Tab 时触发；
           - 若尚未登录，则只提示"不做请求"，等待用户登录后再触发。
        3）切换到设置页面时，刷新登录状态显示。
        """
        self.stack.setCurrentIndex(index)

        # 切换到设置页面（index=6）时，刷新登录状态
        if index == 6 and hasattr(self.settings_page, "refresh_login_status"):
            self.settings_page.refresh_login_status()

        # 只对业务 Tab 做一次性自动请求（排除设置页和图文趋势页）
        # 判定条件：已登录 且 该页面还没有请求过数据
        # 注意：index 3 是排行榜，也需要自动请求
        if index in (0, 1, 2, 3, 4, 5) and index != self.DATA_TREND_PAGE_INDEX and not self._page_first_loaded.get(index, False):
            # 未登录：只提示，不标记为已加载，方便登录后再自动触发
            if not self._ensure_logged_in():
                return

            # 已登录且该页面还没有请求过数据，进行首次自动请求
            self._page_first_loaded[index] = True

            # 为避免 UI 卡顿，这里的自动请求仍交由各页面内部处理
            if index == 0 and hasattr(self.today_page, "refresh_from_api"):
                # 今日评分
                self.today_page.refresh_from_api(silent=True)
            elif index == 1 and hasattr(self.history_page, "reload_from_api"):
                # 历史评分
                self.history_page.reload_from_api()
            elif index == 2 and hasattr(self.review_page, "reload_from_api"):
                # 复评中心
                self.review_page.reload_from_api()
            elif index == 3 and hasattr(self.ranking_page, "refresh_from_api"):
                # 排行榜
                self.ranking_page.refresh_from_api(silent=True)
            elif index == 4 and hasattr(self.notification_page, "reload_from_api"):
                # 消息
                self.notification_page.reload_from_api()
            elif index == 5 and hasattr(self.profile_page, "reload_from_api"):
                # 我的
                self.profile_page.reload_from_api()
        elif index == self.DATA_TREND_PAGE_INDEX:
            # 图文趋势：仅当有有效链接时才加载
            if self.data_trend_url:
                if self._data_trend_last_loaded_url != self.data_trend_url:
                    self.data_trend_page.load_url(self.data_trend_url)
                    self._data_trend_last_loaded_url = self.data_trend_url
                elif not self._page_first_loaded.get(index, False):
                    self.data_trend_page.load_url(self.data_trend_url)
                self._page_first_loaded[index] = True

    # 窗口尺寸变化时，让遮罩自适应
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.loading_overlay is not None:
            self.loading_overlay.resize(self.size())
    
    def _setup_system_tray(self):
        """设置系统托盘"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        # 创建系统托盘图标（不设置为窗口的子对象，确保窗口关闭后图标仍然存在）
        # 在 macOS 上，使用 None 作为父对象，这样窗口关闭时不会影响托盘图标
        import platform
        if platform.system() == "Darwin":
            # macOS: 不设置父对象，确保窗口关闭后图标仍然存在
            # 如果已经有应用级别的托盘图标，复用它
            if hasattr(MainWindow, '_app_tray_icon') and MainWindow._app_tray_icon is not None:
                self._tray_icon = MainWindow._app_tray_icon
            else:
                self._tray_icon = QSystemTrayIcon()
                MainWindow._app_tray_icon = self._tray_icon
        else:
            # Windows/Linux: 可以设置父对象
            self._tray_icon = QSystemTrayIcon(self)
        
        # 设置托盘图标
        icon_path = get_app_icon_path()
        if icon_path and icon_path.exists():
            try:
                icon = QIcon(str(icon_path))
                # 验证图标是否有效
                if icon.isNull():
                    # 图标无效，尝试使用默认图标
                    self._tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
                else:
                    self._tray_icon.setIcon(icon)
            except Exception as e:
                # 加载图标失败，使用默认图标
                import sys
                print(f"加载托盘图标失败: {e}", file=sys.stderr)
                self._tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        else:
            # 如果找不到图标，使用默认图标
            import sys
            print(f"未找到托盘图标文件: {icon_path}", file=sys.stderr)
            self._tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        
        # 创建托盘菜单（macOS 上不设置父对象，确保窗口关闭后菜单仍然可用）
        # 将菜单保存为实例变量，防止被垃圾回收
        import platform
        system = platform.system()
        if system == "Darwin":
            # macOS: 使用应用级别的菜单，确保窗口关闭后仍然可用
            if hasattr(MainWindow, '_app_tray_menu') and MainWindow._app_tray_menu is not None:
                tray_menu = MainWindow._app_tray_menu
                # 清除旧菜单项（如果有）
                tray_menu.clear()
            else:
                tray_menu = QMenu()  # macOS: 不设置父对象
                MainWindow._app_tray_menu = tray_menu
        else:
            tray_menu = QMenu(self)  # Windows/Linux: 可以设置父对象
            self._tray_menu = tray_menu  # 保存为实例变量
        
        # 根据操作系统调整菜单项
        if system == "Darwin":
            # macOS: 显示"回到主界面"和"退出"
            # 注意：菜单项也需要保存为类变量，防止被垃圾回收
            if not hasattr(MainWindow, '_app_tray_actions'):
                MainWindow._app_tray_actions = []
            else:
                # 清除旧的 action 引用
                MainWindow._app_tray_actions.clear()
            
            show_action = QAction("回到主界面")
            show_action.triggered.connect(self._show_window_from_tray)
            tray_menu.addAction(show_action)
            MainWindow._app_tray_actions.append(show_action)  # 保存引用

            tray_menu.addSeparator()
            
            # 加载隔空投送图标并转换为黑色
            from utils.resource_path import get_resource_path
            airdrop_icon_path = get_resource_path("resources/airdrop.png")
            airdrop_icon = None
            if airdrop_icon_path.exists():
                pixmap = QPixmap(str(airdrop_icon_path))
                if not pixmap.isNull():
                    # 缩放图标到合适大小（16x16像素，菜单图标通常较小）
                    scaled_pixmap = pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    # 将图标转换为黑色
                    black_pixmap = self._tint_pixmap_black(scaled_pixmap)
                    airdrop_icon = QIcon(black_pixmap)
            
            airdrop_action = QAction("隔空投送")
            if airdrop_icon:
                airdrop_action.setIcon(airdrop_icon)
            airdrop_action.triggered.connect(self._show_airdrop)
            tray_menu.addAction(airdrop_action)
            MainWindow._app_tray_actions.append(airdrop_action)  # 保存引用
            
            tray_menu.addSeparator()
            
            quit_action = QAction("退出")
            quit_action.triggered.connect(self._quit_application)
            tray_menu.addAction(quit_action)
            MainWindow._app_tray_actions.append(quit_action)  # 保存引用
        else:
            # Windows/Linux: 显示"显示窗口"和"退出"
            show_action = QAction("显示窗口", self)
            show_action.triggered.connect(self.show)
            show_action.triggered.connect(self.raise_)
            show_action.triggered.connect(self.activateWindow)
            tray_menu.addAction(show_action)
            
            # 加载隔空投送图标并转换为黑色
            from utils.resource_path import get_resource_path
            airdrop_icon_path = get_resource_path("resources/airdrop.png")
            airdrop_icon = None
            if airdrop_icon_path.exists():
                pixmap = QPixmap(str(airdrop_icon_path))
                if not pixmap.isNull():
                    # 缩放图标到合适大小（16x16像素，菜单图标通常较小）
                    scaled_pixmap = pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    # 将图标转换为黑色
                    black_pixmap = self._tint_pixmap_black(scaled_pixmap)
                    airdrop_icon = QIcon(black_pixmap)
            
            airdrop_action = QAction("隔空投送", self)
            if airdrop_icon:
                airdrop_action.setIcon(airdrop_icon)
            airdrop_action.triggered.connect(self._show_airdrop)
            tray_menu.addAction(airdrop_action)
            
            tray_menu.addSeparator()
            
            quit_action = QAction("退出", self)
            # 仅隐藏窗口，保持后台服务运行
            quit_action.triggered.connect(self.hide)
            tray_menu.addAction(quit_action)
        
        self._tray_icon.setContextMenu(tray_menu)
        
        # 双击托盘图标显示窗口
        self._tray_icon.activated.connect(self._on_tray_icon_activated)
        
        # 显示托盘图标
        self._tray_icon.show()
    
    def _tint_pixmap_black(self, pixmap: QPixmap) -> QPixmap:
        """将图标转换为黑色"""
        # 创建新的pixmap，使用源pixmap的尺寸
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 使用源pixmap作为mask，然后填充黑色
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, pixmap)
        
        # 使用CompositionMode_SourceIn将颜色改为黑色
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), QColor(0, 0, 0))  # 黑色
        
        painter.end()
        return result
    
    def _restore_dock_icon(self):
        """macOS 恢复 Dock 图标与前置激活"""
        import platform
        if platform.system() != "Darwin":
            return
        try:
            from AppKit import NSApplication, NSImage
            app = NSApplication.sharedApplication()
            # 0 = NSApplicationActivationPolicyRegular，恢复 Dock 图标
            app.setActivationPolicy_(0)
            # 尝试重新设置应用图标（避免变成默认白图标）
            from utils.resource_path import get_resource_path
            candidates = [
                get_resource_path("resources/app_icon.icns"),
                get_resource_path("resources/app_icon.png"),
                get_resource_path("resources/app_icon_512x512.png"),
                get_resource_path("resources/app_icon_256x256.png"),
                get_resource_path("resources/airdrop.png"),
            ]
            for icon_path in candidates:
                if icon_path.exists():
                    ns_image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
                    if ns_image:
                        app.setApplicationIconImage_(ns_image)
                        break
            # 抢占前台，避免需要点击
            app.activateIgnoringOtherApps_(True)
        except Exception:
            pass
    
    def _show_window_from_tray(self):
        """从托盘菜单打开窗口"""
        import platform
        if platform.system() == "Darwin":
            # 恢复 Dock 图标再显示窗口
            self._restore_dock_icon()
        # 确保窗口存在且可见
        if self.isHidden():
            self.show()
        self.raise_()
        self.activateWindow()

    def _notify_airdrop_hint_once(self):
        """首次启动提示如何呼出隔空投送窗口（侧边缘呼出）"""
        try:
            cfg = ConfigManager.load()
        except Exception:
            cfg = {}
        if cfg.get("airdrop_first_run_hint_shown"):
            return
        send_notification(
            title="隔空投送提示",
            message="鼠标移动到侧边缘呼出隔空传送窗口",
            subtitle=None,
            sound=True
        )
        cfg["airdrop_first_run_hint_shown"] = True
        try:
            ConfigManager.save(cfg)
        except Exception:
            pass
    
    def _open_airdrop_after_login(self):
        """
        登录成功后打开隔空投送窗口，1秒后自动隐藏为侧边图标。
        与启动时的逻辑对齐。
        """
        try:
            self._show_airdrop()
        except Exception:
            return

        # 1 秒后自动隐藏到侧边
        def hide_later():
            if self._airdrop_window and hasattr(self._airdrop_window, "_animate_to_icon"):
                try:
                    self._airdrop_window._animate_to_icon()
                except Exception:
                    pass
        QTimer.singleShot(1000, hide_later)

    def _startup_airdrop_with_autohide(self):
        """
        启动时展示隔空投送窗口，1秒后自动隐藏为侧边图标。
        首次启动会发系统通知提示使用方式。
        只在已登录情况下自动打开（未登录时不打开）。
        """
        # 检查是否已登录，未登录时不打开
        if not self._ensure_logged_in():
            return
        
        try:
            self._show_airdrop()
        except Exception:
            return

        # 1 秒后自动隐藏到侧边
        def hide_later():
            if self._airdrop_window and hasattr(self._airdrop_window, "_animate_to_icon"):
                try:
                    self._airdrop_window._animate_to_icon()
                except Exception:
                    pass
        QTimer.singleShot(1000, hide_later)

        # 首次启动提示
        QTimer.singleShot(1200, self._notify_airdrop_hint_once)
    
    def _show_error_dialog(self, title: str, message: str, detailed_text: str = ""):
        """显示可复制错误信息的对话框（仅 Windows/Linux）"""
        if platform.system() == "Darwin":
            # macOS 使用 Toast
            from widgets.toast import Toast
            Toast.show_message(self, message)
            return
        
        # Windows/Linux: 使用可复制的错误对话框
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(300)
        
        layout = QVBoxLayout(dialog)
        
        # 错误消息
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)
        
        # 详细错误信息（可复制）
        if detailed_text:
            detail_label = QLabel("详细错误信息（可复制）：")
            layout.addWidget(detail_label)
            
            text_edit = QTextEdit()
            text_edit.setPlainText(detailed_text)
            text_edit.setReadOnly(True)
            text_edit.setMinimumHeight(150)
            layout.addWidget(text_edit)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        copy_button = QPushButton("复制错误信息")
        copy_button.clicked.connect(lambda: self._copy_to_clipboard(detailed_text or message))
        button_layout.addWidget(copy_button)
        
        ok_button = QPushButton("确定")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        
        layout.addLayout(button_layout)
        dialog.exec()
    
    def _copy_to_clipboard(self, text: str):
        """复制文本到剪贴板"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        from widgets.toast import Toast
        Toast.show_message(self, "已复制到剪贴板")
    
    # eventFilter no longer needed
    
    def _show_airdrop(self):
        """显示隔空投送窗口（从系统托盘菜单、快捷键等调用）"""
        # 检查是否已登录，未登录时提示
        if not self._ensure_logged_in():
            from widgets.toast import Toast
            Toast.show_message(self, "此功能请先登录")
            return
        
        system = platform.system()
        
        # Windows/Linux 调试信息
        try:
            self._show_airdrop_window()
        except Exception as e:
            import traceback
            error_msg = f"显示隔空投送窗口失败: {e}"
            detailed_text = traceback.format_exc()
            
            if system == "Darwin":
                # macOS: 使用 Toast
                print(error_msg, file=sys.stderr)
                from widgets.toast import Toast
                Toast.show_message(self, f"无法打开隔空投送窗口: {str(e)}")
            else:
                # Windows/Linux: 显示可复制的错误对话框
                print(f"[ERROR] {error_msg}\n{detailed_text}", file=sys.stderr)
                self._show_error_dialog(
                    "无法打开隔空投送窗口",
                    f"无法打开隔空投送窗口：{str(e)}",
                    detailed_text
                )
    
    def _show_airdrop_window(self):
        """显示隔空投送窗口（内部方法）"""
        # 检查是否已登录，未登录时提示
        if not self._ensure_logged_in():
            from widgets.toast import Toast
            Toast.show_message(self, "此功能请先登录")
            return
        
        import sys
        import platform
        system = platform.system()
        
        # 检查依赖
        if system == "Windows":
            try:
                import zeroconf
            except ImportError:
                error_msg = "缺少依赖: zeroconf。请运行: pip install zeroconf"
                print(f"[ERROR] {error_msg}", file=sys.stderr)
                if system == "Darwin":
                    from widgets.toast import Toast
                    Toast.show_message(self, "缺少依赖: zeroconf\n请安装: pip install zeroconf")
                else:
                    self._show_error_dialog(
                        "缺少依赖",
                        "缺少依赖: zeroconf",
                        f"请运行以下命令安装：\n\npip install zeroconf\n\n错误信息：{error_msg}"
                    )
                return
        
        try:
            from windows.airdrop_view import AirDropView
        except ImportError as e:
            import traceback
            error_msg = f"导入 AirDropView 失败: {e}"
            detailed_text = traceback.format_exc()
            print(f"[ERROR] {error_msg}\n{detailed_text}", file=sys.stderr)
            if system == "Darwin":
                from widgets.toast import Toast
                Toast.show_message(self, f"无法加载隔空投送模块: {str(e)}")
            else:
                self._show_error_dialog(
                    "无法加载隔空投送模块",
                    f"导入 AirDropView 失败：{str(e)}",
                    detailed_text
                )
            return
        except Exception as e:
            import traceback
            error_msg = f"导入 AirDropView 时发生错误: {e}"
            detailed_text = traceback.format_exc()
            print(f"[ERROR] {error_msg}\n{detailed_text}", file=sys.stderr)
            if system == "Darwin":
                from widgets.toast import Toast
                Toast.show_message(self, f"无法加载隔空投送模块: {str(e)}")
            else:
                self._show_error_dialog(
                    "无法加载隔空投送模块",
                    f"导入时发生错误：{str(e)}",
                    detailed_text
                )
            return
        
        # 如果窗口正在执行显示动画，等待完成或强制重置
        if self._airdrop_window and hasattr(self._airdrop_window, '_is_showing_animation') and self._airdrop_window._is_showing_animation:
            # 等待一下，如果还是在进行，强制重置
            def check_and_reset():
                if self._airdrop_window and hasattr(self._airdrop_window, '_is_showing_animation') and self._airdrop_window._is_showing_animation:
                    self._airdrop_window._is_showing_animation = False
                    # 重新调用显示
                    self._show_airdrop_window()
            QTimer.singleShot(500, check_and_reset)
            return
        
        if self._airdrop_window is None:
            try:
                self._airdrop_window = AirDropView()
                self._airdrop_window.setWindowTitle("隔空投送")
            except Exception as e:
                import traceback
                error_msg = f"创建 AirDropView 窗口失败: {e}"
                detailed_text = traceback.format_exc()
                print(f"[ERROR] {error_msg}\n{detailed_text}", file=sys.stderr)
                if system == "Darwin":
                    from widgets.toast import Toast
                    Toast.show_message(self, f"无法创建隔空投送窗口: {str(e)}")
                else:
                    self._show_error_dialog(
                        "无法创建隔空投送窗口",
                        f"创建窗口失败：{str(e)}",
                        detailed_text
                    )
                return
            # 只保留关闭按钮，禁用最小化和最大化
            # 注意：在macOS上，需要设置窗口属性来禁用最小化和最大化按钮
            # 不包含 WindowMinimizeButtonHint 和 WindowMaximizeButtonHint
            import platform
            if platform.system() == "Windows":
                # Windows: 强制置顶，确保隔空投送窗口始终显示在最上层
                self._airdrop_window.setWindowFlags(
                    Qt.Window |
                    Qt.WindowStaysOnTopHint |
                    Qt.WindowCloseButtonHint
                )
            else:
                # macOS: 使用 WindowStaysOnTopHint
                self._airdrop_window.setWindowFlags(
                    Qt.Window |
                    Qt.WindowStaysOnTopHint |
                    Qt.WindowCloseButtonHint
                )
            # macOS: 设置窗口样式，隐藏最小化和最大化按钮
            if platform.system() == "Darwin":
                try:
                    from AppKit import NSWindow
                    # 延迟执行，确保窗口已创建
                    def set_window_style():
                        try:
                            import sys
                            qwindow = self._airdrop_window.windowHandle()
                            if not qwindow:
                                QTimer.singleShot(100, set_window_style)
                                return
                            
                            # 通过QWindow获取NSWindow
                            ns_view = qwindow.winId()
                            if not ns_view:
                                QTimer.singleShot(100, set_window_style)
                                return
                            
                            import objc
                            from ctypes import c_void_p
                            view = objc.objc_object(c_void_p=c_void_p(int(ns_view)))
                            if not view:
                                QTimer.singleShot(100, set_window_style)
                                return
                            
                            ns_window = view.window()
                            if not ns_window:
                                QTimer.singleShot(100, set_window_style)
                                return
                            
                            # 设置窗口样式：只显示关闭按钮，禁用最小化和缩放
                            # NSWindowStyleMask 常量（正确的值）：
                            # NSWindowStyleMaskTitled = 1
                            # NSWindowStyleMaskClosable = 2
                            # NSWindowStyleMaskMiniaturizable = 4
                            # NSWindowStyleMaskResizable = 8
                            # 获取当前样式掩码
                            current_mask = ns_window.styleMask()
                            
                            # 移除 Miniaturizable (4) 和 Resizable (8)
                            # 保留 Titled (1) 和 Closable (2)
                            style_mask = current_mask & ~4 & ~8  # 移除 Miniaturizable 和 Resizable
                            style_mask = style_mask | 1 | 2  # 确保 Titled 和 Closable 存在
                            
                            ns_window.setStyleMask_(style_mask)
                        except Exception as e:
                            import sys
                            print(f"[WARNING] Failed to set window style: {e}", file=sys.stderr)
                    
                    # 延迟执行，确保窗口已创建
                    QTimer.singleShot(200, set_window_style)
                    QTimer.singleShot(500, set_window_style)
                except Exception as e:
                    import sys
                    print(f"[WARNING] Failed to setup window style: {e}", file=sys.stderr)
            
            # macOS: 禁用最小化和最大化按钮（主要方法）
            if platform.system() == "Darwin":
                def disable_buttons():
                    try:
                        import sys
                        from AppKit import NSWindow, NSWindowButton
                        from PySide6.QtGui import QWindow
                        
                        # 获取QWindow对象
                        qwindow = self._airdrop_window.windowHandle()
                        if not qwindow:
                            QTimer.singleShot(100, disable_buttons)
                            return
                        
                        # 获取NSView
                        ns_view = qwindow.winId()
                        if not ns_view:
                            return
                        
                        # 通过NSView获取NSWindow
                        import objc
                        from ctypes import c_void_p
                        view = objc.objc_object(c_void_p=c_void_p(int(ns_view)))
                        if not view:
                            return
                        
                        # 获取窗口
                        ns_window = view.window()
                        if not ns_window:
                            return
                        
                        # 方法1：通过设置窗口样式掩码来隐藏按钮（更可靠）
                        try:
                            # NSWindowStyleMask 常量（macOS 实际值）：
                            # NSWindowStyleMaskTitled = 1 (标题栏)
                            # NSWindowStyleMaskClosable = 2 (关闭按钮)
                            # NSWindowStyleMaskMiniaturizable = 4 (最小化按钮)
                            # NSWindowStyleMaskResizable = 8 (调整大小，通常包含最大化)
                            # 获取当前样式掩码，只移除 Miniaturizable 和 Resizable，保留其他所有样式
                            current_mask = ns_window.styleMask()
                            
                            # 移除 Miniaturizable (4) 和 Resizable (8)
                            # 保留其他所有样式（包括 Titled=1 和 Closable=2）
                            style_mask = current_mask & ~4  # 移除 Miniaturizable
                            style_mask = style_mask & ~8   # 移除 Resizable
                            
                            # 确保至少保留 Titled 和 Closable（即使当前掩码中没有）
                            style_mask = style_mask | 1  # 确保 Titled
                            style_mask = style_mask | 2  # 确保 Closable
                            
                            ns_window.setStyleMask_(style_mask)
                            
                            # 验证设置是否成功
                            new_mask = ns_window.styleMask()
                            if new_mask != style_mask:
                                print(f"[WARNING] Style mask not set correctly! Expected {style_mask}, got {new_mask}", file=sys.stderr)
                                # 强制设置
                                ns_window.setStyleMask_(style_mask)
                            
                            # 方法2：直接禁用最大化按钮（通过 NSWindowButton）
                            try:
                                # NSWindowButtonZoom = 2 (最大化/全屏按钮)
                                # 直接使用数值 2，因为 NSWindowButton 在 PyObjC 中可能不是枚举
                                zoom_button = ns_window.standardWindowButton_(2)
                                if zoom_button:
                                    zoom_button.setEnabled_(False)  # 禁用最大化按钮
                            except Exception as e_zoom:
                                print(f"[WARNING] Failed to disable zoom button: {e_zoom}", file=sys.stderr)
                            
                            # 禁止双击窗口头部扩大（通过设置 resizeIncrements 来防止调整大小）
                            try:
                                from AppKit import NSSize
                                # 设置一个非常大的增量，这样窗口就无法调整大小
                                huge_size = NSSize(10000, 10000)
                                ns_window.setResizeIncrements_(huge_size)
                            except Exception as e4:
                                print(f"[WARNING] Failed to set resize increments: {e4}", file=sys.stderr)
                            
                            # 额外措施：在 macOS 14+ 上，尝试直接禁止调整大小
                            try:
                                # 通过设置窗口的 contentMinSize 和 contentMaxSize 为相同值来禁止调整大小
                                from AppKit import NSSize
                                fixed_size = NSSize(window_width, window_height)
                                ns_window.setContentMinSize_(fixed_size)
                                ns_window.setContentMaxSize_(fixed_size)
                            except Exception as e5:
                                print(f"[WARNING] Failed to set window fixed size: {e5}", file=sys.stderr)
                        except Exception as e:
                            print(f"[WARNING] Failed to set window style mask: {e}", file=sys.stderr)
                        
                    except Exception as e:
                        import sys
                        import traceback
                        print(f"[WARNING] Failed to disable window buttons: {e}", file=sys.stderr)
                        print(f"[WARNING] Traceback: {traceback.format_exc()}", file=sys.stderr)
                
                # 窗口显示后再禁用按钮（多次重试，确保按钮被彻底隐藏）
                QTimer.singleShot(100, disable_buttons)
                QTimer.singleShot(200, disable_buttons)
                QTimer.singleShot(500, disable_buttons)
                QTimer.singleShot(1000, disable_buttons)
                QTimer.singleShot(2000, disable_buttons)
            # 设置窗口大小和位置
            # 计算合适的窗口大小：能显示22个设备不滚屏
            # 每个设备项高度约100px，加上padding和背景文字区域，总高度约2400px
            # 但考虑到屏幕高度，我们设置一个合理的大小
            # 使用 availableGeometry() 获取可用区域（排除任务栏）
            screen = QApplication.primaryScreen().availableGeometry()
            # 窗口宽度：足够显示设备信息
            window_width = 480
            # 窗口高度：能显示约22个设备（每个设备约100px高度）+ 顶部padding + 底部背景区域
            # 22 * 100 = 2200px，但考虑到屏幕限制，设置为屏幕高度的80%或最大2400px
            # 现在改为一半高度
            max_height = min(int(screen.height() * 0.85), 2400)
            window_height = max(300, max_height // 2)  # 高度减半，至少300px
            
            # 在 macOS / Windows 上，设置窗口为固定大小，防止用户调整大小
            from PySide6.QtCore import QSize
            system_platform = platform.system()
            if system_platform in ("Darwin", "Windows"):
                self._airdrop_window.setFixedSize(window_width, window_height)
                self._airdrop_window._fixed_size = QSize(window_width, window_height)
            else:
                self._airdrop_window.resize(window_width, window_height)
            
            # 默认位置：右侧屏幕边缘垂直居中（基于可用区域）
            x = screen.right() - window_width
            y = screen.top() + (screen.height() - window_height) // 2
            self._airdrop_window.move(x, y)
            
            # 窗口关闭时直接隐藏（不再需要悬浮图标）
            # 监听窗口关闭事件
            self._airdrop_window.destroyed.connect(self._on_airdrop_window_destroyed)
            # 重写关闭事件
            def custom_close_event(event):
                # 不真正关闭，而是隐藏到图标（关闭时也触发边缘隐藏动画）
                event.ignore()
                # 触发窗口隐藏动画（模拟拖到边缘）
                if self._airdrop_window:
                    # 在隐藏动画前，立即保存当前位置（使用pos()的Y坐标，避免系统调整影响）
                    current_geo = self._airdrop_window.geometry()
                    current_pos = self._airdrop_window.pos()
                    # 使用 pos() 的 Y 坐标，因为它是实际窗口位置，geometry() 的 Y 可能包含标题栏等偏移
                    self._airdrop_window._before_hide_rect = QRect(current_pos.x(), current_pos.y(), current_geo.width(), current_geo.height())
                    # 重置拖拽状态
                    if hasattr(self._airdrop_window, '_edge_triggered'):
                        self._airdrop_window._edge_triggered = False
                    self._airdrop_window._animate_to_icon()
            self._airdrop_window.closeEvent = custom_close_event
            
        
        # 判断逻辑：如果窗口已经存在且之前被隐藏过（从边缘恢复），否则是首次打开
        is_restoring = (self._airdrop_window is not None and 
                       hasattr(self._airdrop_window, '_was_hidden_to_icon') and 
                       self._airdrop_window._was_hidden_to_icon)
        
        system = platform.system()
        
        if is_restoring:
            # 从边缘恢复：执行恢复动画
            QTimer.singleShot(50, lambda: self._show_window_after_hidden())
        else:
            # 首次打开：直接显示正常大小的窗口（无动画）
            self._show_window_directly()
    
    def _show_window_directly(self):
        """直接显示窗口（首次打开，无动画）"""
        if not self._airdrop_window:
            return
        
        import sys
        # 计算窗口尺寸和位置
        # 使用 availableGeometry() 获取可用区域（排除任务栏）
        screen = QApplication.primaryScreen().availableGeometry()
        window_width = 480
        max_height = min(int(screen.height() * 0.85), 2400)
        window_height = max(300, max_height // 2)  # 高度减半，至少300px
        x = screen.right() - window_width
        y = screen.top() + (screen.height() - window_height) // 2
        
        # 直接设置窗口大小和位置
        try:
            self._airdrop_window.setGeometry(QRect(x, y, window_width, window_height))
            self._airdrop_window.show()
            self._airdrop_window.setVisible(True)
            self._airdrop_window.raise_()
            self._airdrop_window.activateWindow()
            
            # Windows/Linux 上确保窗口可见（调试信息）
            system = platform.system()
            if system != "Darwin":
                if not self._airdrop_window.isVisible():
                    error_msg = f"窗口显示失败: isVisible()={self._airdrop_window.isVisible()}, geometry={self._airdrop_window.geometry()}, pos={self._airdrop_window.pos()}"
                    print(f"[ERROR] {error_msg}", file=sys.stderr)
                    self._show_error_dialog(
                        "窗口显示失败",
                        "隔空投送窗口无法显示",
                        f"窗口状态信息：\n\nisVisible: {self._airdrop_window.isVisible()}\ngeometry: {self._airdrop_window.geometry()}\npos: {self._airdrop_window.pos()}\nwindowFlags: {self._airdrop_window.windowFlags()}"
                    )
                else:
                    # Windows 特定：再次确保窗口在最前面
                    if system == "Windows":
                        QTimer.singleShot(100, lambda: self._airdrop_window.raise_())
                        QTimer.singleShot(100, lambda: self._airdrop_window.activateWindow())
        except Exception as e:
            import traceback
            system = platform.system()
            error_msg = f"显示窗口失败: {e}"
            detailed_text = traceback.format_exc()
            print(f"[ERROR] {error_msg}\n{detailed_text}", file=sys.stderr)
            if system == "Darwin":
                from widgets.toast import Toast
                Toast.show_message(self, f"无法显示隔空投送窗口: {str(e)}")
            else:
                self._show_error_dialog(
                    "无法显示隔空投送窗口",
                    f"显示窗口失败：{str(e)}",
                    detailed_text
                )
    
    def _show_window_after_hidden(self):
        """从隐藏位置恢复显示窗口（从隐藏位置显示）"""
        if not self._airdrop_window:
            return
        
        import sys
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRect
        
        # 使用 availableGeometry() 获取可用区域（排除任务栏）
        screen = QApplication.primaryScreen().availableGeometry()
        window_width = 480
        max_height = min(int(screen.height() * 0.85), 2400)
        target_width = window_width
        target_height = max(300, max_height // 2)  # 高度减半，至少300px
        
        # 检查是否有保存的隐藏前位置（窗口隐藏前的位置）
        if hasattr(self._airdrop_window, '_before_hide_rect') and self._airdrop_window._before_hide_rect:
            # 从隐藏前的位置恢复：使用隐藏前的位置和大小
            before_hide_rect = self._airdrop_window._before_hide_rect
            target_width = before_hide_rect.width()
            target_height = before_hide_rect.height()
            
            import sys
            
            # 根据隐藏方向决定目标位置
            # 如果从右侧隐藏，窗口右边缘应该接近屏幕右边缘
            # 如果从左侧隐藏，窗口左边缘应该接近屏幕左边缘
            if hasattr(self._airdrop_window, '_hidden_to_left') and self._airdrop_window._hidden_to_left:
                # 从左侧隐藏的，恢复时窗口左边缘接近屏幕左边缘
                target_x = screen.left()
            else:
                # 从右侧隐藏的，恢复时窗口右边缘接近屏幕右边缘
                target_x = screen.right() - target_width
            
            # Y坐标：使用隐藏前的位置（使用pos()保存的Y坐标），但确保在可用区域内
            # 注意：before_hide_rect 中保存的是 pos() 的 Y 坐标，这是实际窗口位置
            # 计算Y坐标的最大值：可用区域底部 - 窗口高度 - macOS Y偏移量
            y_offset = self._get_macos_y_offset(self._airdrop_window)  # 动态检测 macOS Y 坐标偏移量
            max_y = screen.bottom() - target_height - y_offset
            
            target_y = before_hide_rect.y()
            if target_y < screen.top():
                target_y = screen.top()
            elif target_y + target_height > screen.bottom():
                # 当窗口被下边缘挡住时，固定Y坐标为最大值
                target_y = max_y
            
            # 使用动画从隐藏位置滑出显示
            target_rect = QRect(target_x, target_y, target_width, target_height)
            self._airdrop_window._animate_from_icon(target_rect)
            return
        
        # 如果没有隐藏位置（不应该发生，但作为备用）
        # 从右侧屏幕边缘垂直居中显示（基于可用区域）
        target_x = screen.right() - target_width
        target_y = screen.top() + (screen.height() - target_height) // 2
        
        self._airdrop_window.setGeometry(QRect(target_x, target_y, target_width, target_height))
        self._airdrop_window.show()
        self._airdrop_window.setVisible(True)
        self._airdrop_window.raise_()
        self._airdrop_window.activateWindow()
        
        # 重置隐藏标记
        if self._airdrop_window:
            self._airdrop_window._was_hidden_to_icon = False
    
    
    def _on_airdrop_window_destroyed(self):
        """隔空投送窗口被销毁"""
        self._airdrop_window = None
    
    def _on_tray_icon_activated(self, reason):
        """托盘图标激活事件"""
        # macOS: 左键点击显示菜单，双击显示窗口
        # Windows/Linux: 双击显示窗口
        import platform
        if platform.system() == "Darwin":
            if reason == QSystemTrayIcon.Trigger:  # 左键单击
                # macOS 上左键点击应该显示菜单（通过 setContextMenu 自动处理）
                # 右键点击也会触发 Trigger，但会显示 contextMenu
                # 这里不需要额外处理，系统会自动显示菜单
                pass
            elif reason == QSystemTrayIcon.DoubleClick:  # 双击
                self._show_window_from_tray()
        else:
            # Windows/Linux: 双击显示窗口
            if reason == QSystemTrayIcon.DoubleClick:
                self._show_window_from_tray()
    
    def _quit_application(self):
        """退出应用"""
        # 清理资源
        self._cleanup_resources()
        
        # 退出应用：先优雅退出事件循环，稍作延迟再强制退出，确保 mDNS goodbye 包发送
        from PySide6.QtWidgets import QApplication
        import os
        import sys
        QApplication.quit()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: os._exit(0))
    
    def _cleanup_resources(self):
        """清理所有资源"""
        if self._global_hotkey:
            try:
                self._global_hotkey.unregister()
            except Exception:
                pass
            self._global_hotkey = None
        # 停止所有正在运行的登录 worker
        if hasattr(self, '_login_worker') and self._login_worker:
            for worker in self._login_worker[:]:
                if hasattr(worker, 'stop'):
                    worker.stop()
        
        # 停止轮询服务
        try:
            from utils.polling_service import get_polling_service
            polling_service = get_polling_service()
            if polling_service:
                polling_service.stop_polling()
        except Exception:
            pass
        
        # 停止隔空投送服务（注销 mDNS 服务，让其他端知道设备已离线）
        if self._airdrop_window:
            if hasattr(self._airdrop_window, '_transfer_manager') and self._airdrop_window._transfer_manager:
                try:
                    self._airdrop_window._transfer_manager.stop()
                except Exception:
                    pass
            self._airdrop_window.close()
            self._airdrop_window = None
        
        # 注意：悬浮图标不关闭，让它一直存在
        # 如果用户关闭了主窗口，悬浮图标仍然可以显示隔空投送窗口
        
        # 隐藏加载遮罩
        self.hide_loading()
        
        # 停止所有定时器
        try:
            from PySide6.QtCore import QTimer
            for obj in self.findChildren(QTimer):
                if obj.isActive():
                    obj.stop()
        except Exception:
            pass
        
        # 停止线程池中的所有任务（不等待，直接清除）
        try:
            from PySide6.QtCore import QThreadPool
            thread_pool = QThreadPool.globalInstance()
            thread_pool.clear()  # 直接清除所有任务，不等待
        except Exception:
            pass
        
        # 清理登录 worker 引用
        if hasattr(self, '_login_worker'):
            self._login_worker.clear()
        
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 停止帮助中心文字更新定时器
        if hasattr(self, '_help_text_timer'):
            self._help_text_timer.stop()
        system = platform.system()
        if system == "Darwin":
            # macOS: 直接隐藏到状态栏，不询问用户
            if QSystemTrayIcon.isSystemTrayAvailable():
                # 确保托盘图标存在（如果不存在，重新创建）
                if self._tray_icon is None:
                    self._setup_system_tray()
                
                if self._tray_icon is not None:
                    # 直接隐藏到状态栏
                    event.ignore()  # 阻止窗口关闭
                    
                    # 先隐藏 Dock 图标（使用 PyObjC），确保 Dock 图标和主界面同时消失
                    try:
                        from AppKit import NSApplication
                        app = NSApplication.sharedApplication()
                        # 1 = NSApplicationActivationPolicyAccessory (隐藏 Dock 图标)
                        # 注意：必须使用 sharedApplication() 获取应用实例
                        result = app.setActivationPolicy_(1)
                        if result:
                            # 成功隐藏 Dock 图标
                            pass
                        else:
                            # 设置失败，可能是权限问题
                            import sys
                            print("[WARNING] Failed to set activation policy to hide Dock icon", file=sys.stderr)
                    except ImportError as e:
                        # PyObjC 不可用
                        import sys
                        print(f"[WARNING] PyObjC not available, cannot hide Dock icon: {e}", file=sys.stderr)
                        print("[WARNING] In packaged app, PyObjC should be included. Check build_macos.spec hiddenimports.", file=sys.stderr)
                    except Exception as e:
                        # 其他错误
                        import sys
                        print(f"[WARNING] Failed to hide Dock icon: {type(e).__name__}: {e}", file=sys.stderr)
                    
                    # 隐藏窗口（在隐藏 Dock 图标之后）
                    self.hide()
                    
                    # 确保托盘图标仍然显示
                    if not self._tray_icon.isVisible():
                        self._tray_icon.show()
                else:
                    # 托盘图标创建失败，直接退出
                    self._cleanup_resources()
                    super().closeEvent(event)
                    from PySide6.QtWidgets import QApplication
                    QApplication.quit()
                    import os
                    os._exit(0)
            else:
                # 没有系统托盘，直接退出
                self._cleanup_resources()
                super().closeEvent(event)
                from PySide6.QtWidgets import QApplication
                QApplication.quit()
                import os
                os._exit(0)
            return
        
        # Windows/Linux: 检查是否是首次关闭
        try:
            cfg = ConfigManager.load()
            close_behavior = cfg.get("close_behavior", None)  # None表示首次
        except Exception:
            close_behavior = None
        
        # 如果是首次关闭（Windows/Linux），显示选择对话框
        if close_behavior is None:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("关闭应用")
            msg_box.setText("请选择关闭方式：")
            
            # 检查系统托盘是否可用
            tray_available = QSystemTrayIcon.isSystemTrayAvailable() and self._tray_icon is not None
            
            if tray_available:
                msg_box.setInformativeText(
                    "• 退出到托盘：窗口关闭后，应用在系统托盘中继续运行\n"
                    "• 直接退出：完全退出应用程序\n\n"
                    "此选择将保存，下次关闭时将自动使用该方式。"
                )
            else:
                msg_box.setInformativeText(
                    "当前系统不支持系统托盘功能，将直接退出应用程序。\n\n"
                    "此选择将保存，下次关闭时将自动使用该方式。"
                )
            
            # 创建自定义按钮
            btn_tray = None
            if tray_available:
                btn_tray = msg_box.addButton("退出到托盘", QMessageBox.AcceptRole)
            btn_quit = msg_box.addButton("直接退出", QMessageBox.AcceptRole)
            btn_cancel = msg_box.addButton("取消", QMessageBox.RejectRole)
            
            # 设置默认按钮
            if tray_available:
                msg_box.setDefaultButton(btn_tray)
            else:
                msg_box.setDefaultButton(btn_quit)
            
            msg_box.exec()
            
            clicked_button = msg_box.clickedButton()
            
            if clicked_button == btn_cancel:
                # 取消关闭
                event.ignore()
                return
            elif btn_tray and clicked_button == btn_tray:
                # 退出到托盘
                close_behavior = "tray"
            elif clicked_button == btn_quit:
                # 直接退出
                close_behavior = "quit"
            else:
                # 默认取消
                event.ignore()
                return
            
            # 保存用户选择
            try:
                cfg = ConfigManager.load()
            except Exception:
                cfg = {}
            cfg["close_behavior"] = close_behavior
            ConfigManager.save(cfg)
        
        # Windows/Linux 默认隐藏到托盘（静默与 macOS 行为一致）
        if not QSystemTrayIcon.isSystemTrayAvailable() or not self._tray_icon:
            # 托盘不可用则直接退出
            self._cleanup_resources()
            super().closeEvent(event)
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
            import os
            os._exit(0)
        else:
            event.ignore()
            self.hide()
            if not self._tray_icon.isVisible():
                self._tray_icon.show()
            self._tray_icon.showMessage(
                "Ai 绩效客户端",
                "应用已最小化到系统托盘",
                QSystemTrayIcon.Information,
                2000
            )
    
    # -------- 启动时登录检查 --------
    def _check_login_on_startup(self):
        """应用启动时检查登录状态，如果未登录则提示"""
        # 检查是否已登录
        if not ApiClient.is_logged_in():
            # 如果未登录，显示登录提示弹窗
            # 使用 force=False，如果已经有弹窗在显示则不会重复弹窗
            self.show_login_required_dialog(force=False)
    
    # -------- 版本升级检查 --------
    def _check_version_on_startup(self):
        """应用启动时检查版本升级（无需登录）"""
        try:
            cfg = ConfigManager.load()
            client_version = cfg.get("client_version", "1.0.0")
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8000").strip()
        except Exception:
            return
        
        # 后台检查版本（直接使用HTTP请求，不需要登录）
        from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal, Slot
        import httpx
        
        class _VersionCheckWorkerSignals(QObject):
            finished = Signal(dict)  # version_info
            health_data_received = Signal(dict)  # health_data
            error = Signal(str)
        
        class _VersionCheckWorker(QRunnable):
            def __init__(self, current_version: str, api_base: str):
                super().__init__()
                self.signals = _VersionCheckWorkerSignals()
                self._current_version = current_version
                self._api_base = api_base.rstrip("/")
            
            @Slot()
            def run(self):
                try:
                    # 直接使用HTTP请求，不需要登录
                    url = f"{self._api_base}/api/health"
                    params = {"current_version": self._current_version} if self._current_version else None
                    r = httpx.get(url, params=params, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            health_data = data.get("data")
                            if health_data:
                                # 发送整个 health_data，用于检查 help_text
                                self.signals.health_data_received.emit(health_data)
                                # 发送 version_info，用于版本检查
                                version_info = health_data.get("version_info")
                                if version_info:
                                    self.signals.finished.emit(version_info)
                except Exception:
                    # 版本检查失败不影响应用启动
                    pass
        
        worker = _VersionCheckWorker(client_version, api_base)
        worker.signals.finished.connect(self._on_version_update_available)
        worker.signals.health_data_received.connect(self._on_health_data_received)
        QThreadPool.globalInstance().start(worker)
    
    def _check_help_text(self):
        """定期检查 help_text，动态更新帮助中心标签"""
        try:
            cfg = ConfigManager.load()
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8000").strip()
        except Exception:
            # 如果获取配置失败，隐藏标签
            self.help_label.setVisible(False)
            return
        
        # 后台检查 help_text（直接使用HTTP请求，不需要登录）
        from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal, Slot
        import httpx
        
        class _HelpTextCheckWorkerSignals(QObject):
            health_data_received = Signal(dict)  # health_data
            error = Signal(str)
        
        class _HelpTextCheckWorker(QRunnable):
            def __init__(self, api_base: str):
                super().__init__()
                self.signals = _HelpTextCheckWorkerSignals()
                self._api_base = api_base.rstrip("/")
            
            @Slot()
            def run(self):
                try:
                    # 直接使用HTTP请求，不需要登录
                    url = f"{self._api_base}/api/health"
                    r = httpx.get(url, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            health_data = data.get("data")
                            if health_data:
                                self.signals.health_data_received.emit(health_data)
                except Exception:
                    # 检查失败不影响应用，静默处理
                    pass
        
        worker = _HelpTextCheckWorker(api_base)
        worker.signals.health_data_received.connect(self._on_health_data_received)
        QThreadPool.globalInstance().start(worker)
    
    def _on_health_data_received(self, health_data: dict):
        """接收到健康检查数据，更新帮助中心与图文趋势入口"""
        # 优先处理 data_trend（远程图文趋势链接与文案）
        self._update_data_trend_link(
            health_data.get("data_trend"),
            health_data.get("data_trend_text"),
        )

        help_text = health_data.get("help_text")
        # 只有在 help_text 存在、是字符串、且非空时才显示
        # 处理三种情况：不存在、null、空字符串
        help_text_processed = None
        if help_text and isinstance(help_text, str) and help_text.strip():
            help_text_processed = help_text.strip()
        
        # 只有当 help_text 发生变化时才更新
        if help_text_processed != self._last_help_text:
            self._last_help_text = help_text_processed
            if help_text_processed:
                # 更新帮助中心标签的文字并显示
                self.help_label.setText(help_text_processed)
                self.help_label.setVisible(True)
            else:
                # 如果没有 help_text、为 null、或为空字符串，隐藏标签
                self.help_label.setVisible(False)

    def _ensure_data_trend_hidden_by_default(self):
        """默认隐藏图文趋势入口，等待接口返回控制显示与文案"""
        if not hasattr(self, "data_trend_item"):
            return
        # 清空缓存的链接与加载状态，防止默认展示
        self.data_trend_url = None
        self._data_trend_last_loaded_url = None
        self.data_trend_item.setText(self.DEFAULT_DATA_TREND_TEXT)
        self.data_trend_item.setHidden(True)
        # 同步导航尺寸，避免因默认项展示导致的占位
        if hasattr(self, "_adjust_nav_height"):
            self._adjust_nav_height()
        if hasattr(self, "_recalculate_nav_width"):
            self._recalculate_nav_width()

    def _update_data_trend_link(self, data_trend_value, data_trend_text=None):
        """根据 /api/health 返回的 data_trend 相关字段控制菜单显隐与文案"""
        if self.DATA_TREND_PAGE_INDEX is None or not hasattr(self, "data_trend_item"):
            return

        # 处理菜单文案
        text = self.DEFAULT_DATA_TREND_TEXT
        if isinstance(data_trend_text, str) and data_trend_text.strip():
            text = data_trend_text.strip()
        self.data_trend_item.setText(text)
        if hasattr(self, "_recalculate_nav_width"):
            self._recalculate_nav_width()

        # 校验链接格式，仅接受 http/https
        url = ""
        if isinstance(data_trend_value, str):
            candidate = data_trend_value.strip()
            if candidate.lower().startswith(("http://", "https://")):
                url = candidate

        if url:
            # 只有当 URL 发生变化时才更新和加载
            url_changed = (self.data_trend_url != url)
            self.data_trend_url = url
            self.data_trend_item.setHidden(False)
            if hasattr(self, "_adjust_nav_height"):
                self._adjust_nav_height()

            # 如果 URL 没有变化，且已经加载过，则不重新加载（避免刷新）
            if not url_changed and self._data_trend_last_loaded_url == url:
                # URL 没有变化，不需要重新加载
                return
            
            # 如果 URL 变化了，重置已加载标记，这样切换到页面时会加载新 URL
            if url_changed:
                self._data_trend_last_loaded_url = None

            # 如果当前就在该页面或尚未加载过，则立即加载
            should_load_now = (
                self.nav.currentRow() == self.DATA_TREND_PAGE_INDEX
                or not self._page_first_loaded.get(self.DATA_TREND_PAGE_INDEX, False)
            )
            if should_load_now:
                self.data_trend_page.load_url(url)
                self._data_trend_last_loaded_url = url
                self._page_first_loaded[self.DATA_TREND_PAGE_INDEX] = True
        else:
            # 不合法或缺失：收起菜单、重置状态
            self.data_trend_url = None
            self._data_trend_last_loaded_url = None
            self._page_first_loaded[self.DATA_TREND_PAGE_INDEX] = False
            self.data_trend_item.setHidden(True)
            if self.nav.currentRow() == self.DATA_TREND_PAGE_INDEX:
                self.nav.setCurrentRow(0)
            if hasattr(self, "_adjust_nav_height"):
                self._adjust_nav_height()
    
    def _on_version_update_available(self, version_info: dict):
        """检测到新版本，显示升级弹窗（从轮询服务调用）"""
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
        
        # 如果已有弹窗在显示
        if hasattr(self, '_update_dialog') and self._update_dialog and self._update_dialog.isVisible():
            # 强制升级：检查版本是否有变化
            if is_force_update:
                existing_version = self._update_dialog._version_info.get("version", "")
                if existing_version != new_version:
                    # 版本有变化，关闭旧弹窗，显示新弹窗
                    self._update_dialog.close()
                    self._update_dialog.deleteLater()
                    self._update_dialog_shown = False  # 重置标志，允许显示新弹窗
                else:
                    # 版本没变化，不重复显示
                    return
            else:
                # 非强制升级：已有弹窗在显示，不重复显示
                return
        
        # 如果已经显示过升级弹窗（且不是版本更新），不再重复显示
        if self._update_dialog_shown:
            return
        
        try:
            cfg = ConfigManager.load()
            current_version = cfg.get("client_version", "1.0.0")
        except Exception:
            current_version = "1.0.0"
        
        self._update_dialog = UpdateDialog(self, current_version, version_info)
        # 连接弹窗关闭信号，确保标志正确重置
        self._update_dialog.finished.connect(self._on_update_dialog_finished)
        self._update_dialog.show()
        self._update_dialog_shown = True
    
    def _on_update_dialog_finished(self, result: int):
        """弹窗关闭时的处理，重置标志以允许后续版本检查"""
        # 只有当弹窗被关闭（非强制升级）时才重置标志
        # 如果是强制升级，弹窗不应该被关闭，所以这里不需要特殊处理
        if hasattr(self, '_update_dialog') and self._update_dialog:
            # 检查是否是强制升级
            is_force_update = getattr(self._update_dialog, '_is_force_update', True)
            if not is_force_update:
                # 非强制升级：弹窗已关闭，重置标志，允许后续版本检查
                self._update_dialog_shown = False
                # 同时清除轮询服务中的版本缓存，确保新版本能够被检测到
                from utils.polling_service import get_polling_service
                polling_service = get_polling_service()
                if polling_service:
                    polling_service._last_version_info = None
    
    def _setup_background_notification_service(self):
        """设置后台通知服务（应用未运行时也能接收通知）"""
        try:
            import platform
            from utils.system_notification_service import SystemNotificationService
            from utils.config_manager import ConfigManager
            
            # 检查用户是否启用了通知
            config = ConfigManager.load()
            if not config.get("notifications", True):
                return
            
            service = SystemNotificationService()
            system = platform.system()
            
            # macOS 保持静默安装（不影响原有逻辑）
            is_macos = system == "Darwin"
            
            # 检查服务状态
            if not service.is_installed():
                # 如果未安装，尝试安装
                success, msg = service.install(force_reinstall=False)
                if success:
                    # 安装成功后启用
                    enable_success, enable_msg = service.enable()
                    # 只在 Windows 和 Linux 显示提示，macOS 保持静默
                    if not is_macos:
                        from PySide6.QtWidgets import QMessageBox
                        if enable_success:
                            # 显示成功提示（仅在首次安装时）
                            QMessageBox.information(
                                self,
                                "服务安装成功",
                                "后台通知服务已成功安装并启用。\n\n"
                                "即使应用未运行，您也能收到系统通知。",
                                QMessageBox.Ok
                            )
                        else:
                            # 安装成功但启用失败
                            QMessageBox.warning(
                                self,
                                "服务启用失败",
                                f"服务已安装，但启用失败：{enable_msg}\n\n"
                                "您可以在设置中手动启用服务。",
                                QMessageBox.Ok
                            )
                else:
                    # 安装失败，只在 Windows 和 Linux 显示错误提示
                    if not is_macos:
                        from PySide6.QtWidgets import QMessageBox
                        error_msg = msg or "未知错误"
                        # 检查是否是权限问题
                        if "权限" in error_msg or "access denied" in error_msg.lower() or "拒绝访问" in error_msg:
                            QMessageBox.warning(
                                self,
                                "服务安装失败 - 需要管理员权限",
                                f"安装后台通知服务需要管理员权限。\n\n"
                                f"错误信息：{error_msg}\n\n"
                                "请以管理员身份运行应用，或手动在任务计划程序中创建任务。",
                                QMessageBox.Ok
                            )
                        else:
                            QMessageBox.warning(
                                self,
                                "服务安装失败",
                                f"安装后台通知服务失败：{error_msg}\n\n"
                                "您仍可以在应用运行时接收通知。",
                                QMessageBox.Ok
                            )
                    # macOS 静默失败，不显示错误（保持原有逻辑）
            else:
                # 服务已安装，检查配置是否正确（覆盖安装的情况）
                # 只在 Windows 和 Linux 进行配置检查和重新安装
                if not is_macos and not service.is_configuration_valid():
                    # 配置不正确，检查是否已经尝试过更新（防止重复弹出弹窗）
                    if not self._service_update_attempted:
                        # 标记已尝试更新（保存到配置文件，避免每次启动都尝试）
                        self._service_update_attempted = True
                        try:
                            cfg = ConfigManager.load()
                            cfg["service_update_attempted"] = True
                            ConfigManager.save(cfg)
                        except Exception:
                            pass
                        
                        # 重新安装
                        from PySide6.QtWidgets import QMessageBox
                        success, msg = service.install(force_reinstall=True)
                        if success:
                            enable_success, enable_msg = service.enable()
                            if enable_success:
                                # 重新安装后等待一下，让任务计划程序生效
                                import time
                                time.sleep(1)
                                
                                # 再次检查配置是否有效
                                if service.is_configuration_valid():
                                    QMessageBox.information(
                                        self,
                                        "服务更新成功",
                                        "检测到旧版本服务配置，已自动更新为新版本配置。",
                                        QMessageBox.Ok
                                    )
                                else:
                                    # 配置仍然无效，但重新安装成功，认为配置已更新
                                    # 不再重复检查，避免循环
                                    print(f"[WARNING] 服务配置更新后验证失败，但安装成功，不再重复检查")
                            else:
                                QMessageBox.warning(
                                    self,
                                    "服务更新失败",
                                    f"服务配置已更新，但启用失败：{enable_msg}",
                                    QMessageBox.Ok
                                )
                        else:
                            QMessageBox.warning(
                                self,
                                "服务更新失败",
                                f"更新服务配置失败：{msg}\n\n"
                                "建议手动卸载旧服务后重新安装。",
                                QMessageBox.Ok
                            )
                    # 如果已经尝试过更新，不再重复弹出弹窗（避免循环）
                elif not service.is_enabled():
                    # 如果已安装但未启用，启用它
                    enable_success, enable_msg = service.enable()
                    # 只在 Windows 和 Linux 显示失败提示
                    if not is_macos and not enable_success:
                        from PySide6.QtWidgets import QMessageBox
                        QMessageBox.warning(
                            self,
                            "服务启用失败",
                            f"启用后台通知服务失败：{enable_msg}",
                            QMessageBox.Ok
                        )
        except Exception as e:
            # 记录错误但不显示给用户（避免干扰）
            import traceback
            print(f"[ERROR] 设置后台通知服务失败: {e}")
            traceback.print_exc()
    
    def _start_polling_service(self):
        """启动统一轮询服务（检查版本和通知）"""
        try:
            # 检查是否已登录（通知检查需要登录，版本检查不需要）
            is_logged_in = self._ensure_logged_in()
            
            # 创建 API 客户端（如果已登录）
            api_client = None
            if is_logged_in:
                try:
                    api_client = ApiClient.from_config()
                except Exception:
                    pass
            
            # 获取轮询服务并启动
            polling_service = get_polling_service(api_client)
            polling_service.start_polling()
            
            # 连接信号
            polling_service.notification_received.connect(self._on_notification_received)
            polling_service.version_update_available.connect(self._on_version_update_available)
        except Exception:
            # 静默失败，不干扰主程序
            pass
    
    def _on_notification_received(self, notification: dict):
        """收到通知时的处理"""
        # 通知已经在 notification_service 中通过系统通知显示了
        # 这里可以添加额外的UI处理，比如在应用内显示通知列表等
        pass
    
    def _on_notification_clicked(self, notification: dict):
        """通知被点击时的处理"""
        notification_id = notification.get("id")
        if notification_id:
            self.show_notification_detail(notification_id)
    
    def _apply_window_title_bar_theme(self):
        """应用窗口标题栏主题样式"""
        import platform
        from utils.theme_manager import ThemeManager
        from utils.config_manager import ConfigManager
        
        cfg = ConfigManager.load()
        preference = cfg.get("theme", "auto")
        
        if preference == "auto":
            theme = ThemeManager.detect_system_theme()
        else:
            theme = preference
        
        # macOS 特殊处理：尝试使用 PyObjC 设置 NSWindow 外观
        if platform.system() == "Darwin":
            try:
                # 尝试导入 PyObjC
                try:
                    from AppKit import NSAppearance, NSAppearanceNameDarkAqua, NSAppearanceNameAqua
                    from PySide6.QtGui import QGuiApplication
                    
                    # 获取窗口的 QWindow
                    window_handle = self.windowHandle()
                    if window_handle and window_handle.isVisible():
                        # 使用 QWindow 的 winId 获取 NSWindow
                        win_id = window_handle.winId()
                        if win_id:
                            # 通过 objc 访问 NSWindow
                            try:
                                from ctypes import c_void_p
                                import objc
                                from AppKit import NSWindow
                                
                                # 获取 NSWindow 对象
                                # 注意：这需要窗口已经显示
                                # 使用 QWindow 的 nativeInterface 方法
                                ns_window = None
                                if hasattr(window_handle, 'nativeInterface'):
                                    native = window_handle.nativeInterface()
                                    if native:
                                        # 尝试获取 NSWindow
                                        ns_window = native.nativeResourceForWindow("NSWindow", window_handle)
                                        if not ns_window:
                                            # 备用方法：直接通过 winId 转换
                                            ns_window = objc.objc_object(c_void_p=c_void_p(int(win_id)))
                                    else:
                                        # native 为 None，使用备用方法
                                        ns_window = objc.objc_object(c_void_p=c_void_p(int(win_id)))
                                else:
                                    # 直接通过 winId 转换
                                    ns_window = objc.objc_object(c_void_p=c_void_p(int(win_id)))
                                
                                if ns_window and hasattr(ns_window, 'setAppearance_'):
                                    # 根据主题设置外观
                                    if theme == "dark":
                                        appearance = NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua)
                                    else:
                                        appearance = NSAppearance.appearanceNamed_(NSAppearanceNameAqua)
                                    ns_window.setAppearance_(appearance)
                            except (ImportError, AttributeError, Exception) as e:
                                # PyObjC 不可用或方法失败，使用备用方法
                                print(f"[MainWindow] PyObjC method failed: {e}")
                                # 尝试使用 QWindow 的 nativeInterface
                                try:
                                    if hasattr(window_handle, 'nativeInterface'):
                                        native = window_handle.nativeInterface()
                                        if native:
                                            # 尝试获取 NSWindow
                                            ns_window_ref = native.nativeResourceForWindow("NSWindow", window_handle)
                                            if ns_window_ref and hasattr(ns_window_ref, 'setAppearance_'):
                                                if theme == "dark":
                                                    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua)
                                                else:
                                                    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameAqua)
                                                ns_window_ref.setAppearance_(appearance)
                                except Exception as e2:
                                    print(f"[MainWindow] Native interface method failed: {e2}")
                except ImportError:
                    # PyObjC 未安装，跳过
                    print("[MainWindow] PyObjC not available, skipping macOS title bar theme")
            except Exception as e:
                print(f"[MainWindow] macOS title bar theme setup error: {e}")
        
        # 通用方法：设置窗口背景色
        # 注意：在 macOS 上，这不会直接影响标题栏，但会让窗口内容区域与主题一致
        if theme == "dark":
            # 深色主题
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #202124;
                }
            """)
        else:
            # 浅色主题
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #F7F9FC;
                }
            """)
    
    def showEvent(self, event):
        """窗口显示时调用，用于设置 macOS 标题栏主题"""
        super().showEvent(event)
        # macOS: 窗口显示后再次应用标题栏主题
        import platform
        if platform.system() == "Darwin":
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, self._apply_window_title_bar_theme)
    
    def _check_and_update_title_bar_theme(self):
        """检查并更新标题栏主题（用于动态主题切换）"""
        from utils.theme_manager import ThemeManager
        from utils.config_manager import ConfigManager
        
        cfg = ConfigManager.load()
        preference = cfg.get("theme", "auto")
        
        if preference == "auto":
            current_theme = ThemeManager.detect_system_theme()
        else:
            current_theme = preference
        
        # 检查主题是否变化（简单实现，可以优化）
        if not hasattr(self, '_last_theme'):
            self._last_theme = None
        
        if self._last_theme != current_theme:
            self._last_theme = current_theme
            self._apply_window_title_bar_theme()
    
    def _on_help_center_clicked(self, event):
        """帮助中心标签点击事件"""
        if event.button() == Qt.LeftButton:
            self._open_help_center()
    
    def _open_help_center(self):
        """打开帮助中心窗口"""
        try:
            # 创建帮助中心窗口
            help_window = HelpCenterWindow(self)
            help_window.show()
        except Exception as e:
            # 如果QWebEngineView不可用，显示错误提示
            QMessageBox.warning(
                self,
                "错误",
                f"无法打开帮助中心：{e}\n\n请确保已安装 PySide6-QtWebEngine。"
            )
    
    def show_notification_detail(self, notification_id: int = None):
        """显示通知详情（供外部调用，如通知点击）"""
        from PySide6.QtCore import QTimer
        # 切换到消息页面
        self.nav.setCurrentRow(4)  # 消息
        # 如果指定了通知ID，可以高亮显示
        if notification_id and hasattr(self.notification_page, "highlight_notification"):
            QTimer.singleShot(500, lambda: self.notification_page.highlight_notification(notification_id))
