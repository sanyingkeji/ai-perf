from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QStackedWidget, QLabel, QDialog, QPushButton, QSizePolicy,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox
)
from PySide6.QtCore import Qt, QSize, QTimer, QPoint
from PySide6.QtGui import QIcon, QAction
import platform
from pathlib import Path

from windows.today_view import TodayView
from windows.history_view import HistoryView
from windows.review_view import ReviewView
from windows.ranking_view import RankingView
from windows.settings_view import SettingsView
from windows.update_dialog import UpdateDialog
from windows.help_center_window import HelpCenterWindow
from utils.config_manager import ConfigManager
from utils.google_login import login_and_get_id_token, GoogleLoginError
from utils.api_client import ApiClient, ApiError, AuthError
from utils.polling_service import get_polling_service
from widgets.toast import Toast
from widgets.loading_overlay import LoadingOverlay
from datetime import date


class MainWindow(QMainWindow):
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

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)

        # 左侧：导航栏
        nav_container = QWidget()
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(12, 12, 12, 12)
        nav_layout.setSpacing(12)

        logo = QLabel("Ai Perf")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size:20px; font-weight:bold;")
        nav_layout.addWidget(logo)

        self.nav = QListWidget()
        self.nav.addItem("今日评分")
        self.nav.addItem("历史评分")
        self.nav.addItem("复评中心")
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
        # 设置宽度：文本宽度 + 左右内边距 + 图标空间（如果有）
        content_width = max_width + 40  # 40px 用于内边距和图标
        nav_width = max(120, content_width)
        nav_width = min(nav_width, 300)  # 最大宽度300
        self.nav.setFixedWidth(nav_width)
        
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
            item_count = self.nav.count()
            if item_count > 0:
                # 获取第一项的高度作为每项高度
                item_height = self.nav.sizeHintForRow(0)
                if item_height <= 0:
                    item_height = 30  # 默认高度
                # 计算总高度：项数 * 每项高度 + 边框（约4px）
                total_height = item_count * item_height + 4
                self.nav.setFixedHeight(total_height)
            else:
                self.nav.setFixedHeight(30)
        
        # 延迟执行，确保窗口已显示
        QTimer.singleShot(100, adjust_nav_height)

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

        self.stack.addWidget(self.today_page)
        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.review_page)
        self.stack.addWidget(self.ranking_page)
        self.stack.addWidget(self.notification_page)
        self.stack.addWidget(self.profile_page)
        self.stack.addWidget(self.settings_page)

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
        
        # 创建悬浮图标（一直存在）
        self._floating_icon = None
        self._airdrop_window = None
        self._setup_floating_icon()
        
        # macOS: 确保应用在窗口关闭后仍然运行
        import platform
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
        """登录成功后刷新当前页面（仅刷新当前页面，不刷新其他页面）"""
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

        # 只对业务 Tab 做一次性自动请求
        # 判定条件：已登录 且 该页面还没有请求过数据
        if index in (0, 1, 2, 3, 4, 5) and not self._page_first_loaded.get(index, False):
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
        app_dir = Path(__file__).parent.parent
        icon_paths = [
            app_dir / "resources" / "app_icon.icns",
            app_dir / "resources" / "app_icon.ico",
            app_dir / "resources" / "app_icon.png",
        ]
        for icon_path in icon_paths:
            if icon_path.exists():
                self._tray_icon.setIcon(QIcon(str(icon_path)))
                break
        
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
            
            airdrop_action = QAction("隔空投送")
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
            
            airdrop_action = QAction("隔空投送", self)
            airdrop_action.triggered.connect(self._show_airdrop)
            tray_menu.addAction(airdrop_action)
            
            tray_menu.addSeparator()
            
            quit_action = QAction("退出", self)
            quit_action.triggered.connect(self._quit_application)
            tray_menu.addAction(quit_action)
        
        self._tray_icon.setContextMenu(tray_menu)
        
        # 双击托盘图标显示窗口
        self._tray_icon.activated.connect(self._on_tray_icon_activated)
        
        # 显示托盘图标
        self._tray_icon.show()
    
    def _show_window_from_tray(self):
        """从托盘菜单打开窗口"""
        # 确保窗口存在且可见
        if self.isHidden():
            self.show()
        self.raise_()
        self.activateWindow()
    
    def _setup_floating_icon(self):
        """设置悬浮图标（一直存在，不随主窗口关闭）"""
        try:
            from widgets.floating_icon import FloatingIcon
            # 使用类变量确保图标独立于主窗口实例
            if not hasattr(MainWindow, '_global_floating_icon'):
                MainWindow._global_floating_icon = FloatingIcon()
                MainWindow._global_floating_icon.clicked.connect(self._show_airdrop_from_icon)
                # 初始状态：显示图标（因为窗口还没创建）
                MainWindow._global_floating_icon.animate_show()
            else:
                # 如果已经存在，连接信号
                MainWindow._global_floating_icon.clicked.connect(self._show_airdrop_from_icon)
            
            self._floating_icon = MainWindow._global_floating_icon
        except Exception as e:
            import sys
            print(f"[WARNING] 创建悬浮图标失败: {e}", file=sys.stderr)
            self._floating_icon = None
    
    def _show_airdrop(self):
        """显示隔空投送窗口（从系统托盘菜单调用）"""
        self._show_airdrop_window()
    
    def _show_airdrop_from_icon(self):
        """从悬浮图标显示窗口"""
        self._show_airdrop_window()
    
    def _show_airdrop_window(self):
        """显示隔空投送窗口（内部方法）"""
        from windows.airdrop_view import AirDropView
        
        if self._airdrop_window is None:
            self._airdrop_window = AirDropView()
            self._airdrop_window.setWindowTitle("隔空投送")
            # 只保留关闭按钮，禁用最小化和最大化
            # 注意：在macOS上，需要设置窗口属性来禁用最小化和最大化按钮
            self._airdrop_window.setWindowFlags(
                Qt.Window |
                Qt.WindowStaysOnTopHint |
                Qt.WindowCloseButtonHint
            )
            # macOS: 禁用最小化和最大化按钮
            if platform.system() == "Darwin":
                try:
                    from AppKit import NSWindow, NSWindowButton
                    # 获取窗口的NSWindow对象
                    win_id = self._airdrop_window.winId()
                    if win_id:
                        # 通过objc访问NSWindow
                        from ctypes import c_void_p
                        import objc
                        ns_window = objc.objc_object(c_void_p=c_void_p(int(win_id)))
                        if ns_window:
                            # 禁用最小化按钮
                            minimize_button = ns_window.standardWindowButton_(NSWindowButton.NSWindowMiniaturizeButton)
                            if minimize_button:
                                minimize_button.setEnabled_(False)
                            # 禁用最大化按钮
                            zoom_button = ns_window.standardWindowButton_(NSWindowButton.NSWindowZoomButton)
                            if zoom_button:
                                zoom_button.setEnabled_(False)
                except Exception:
                    # 如果失败，忽略（不影响功能）
                    pass
            # 设置窗口大小和位置
            self._airdrop_window.resize(420, 600)
            # 居中显示
            screen = QApplication.primaryScreen().geometry()
            window_geometry = self._airdrop_window.frameGeometry()
            window_geometry.moveCenter(screen.center())
            self._airdrop_window.move(window_geometry.topLeft())
            
            # 连接信号：窗口拖到边缘时隐藏并显示图标（传递图标位置）
            self._airdrop_window.should_hide_to_icon.connect(self._hide_airdrop_to_icon)
            # 监听窗口关闭事件
            self._airdrop_window.destroyed.connect(self._on_airdrop_window_destroyed)
            # 重写关闭事件
            def custom_close_event(event):
                # 不真正关闭，而是隐藏到图标（关闭时也触发边缘隐藏动画）
                event.ignore()
                # 停止传输管理器
                if hasattr(self._airdrop_window, '_transfer_manager') and self._airdrop_window._transfer_manager:
                    self._airdrop_window._transfer_manager.stop()
                # 触发窗口隐藏动画（模拟拖到边缘）
                if self._airdrop_window:
                    self._airdrop_window._animate_to_icon()
            self._airdrop_window.closeEvent = custom_close_event
            
        
        # 显示窗口，隐藏图标（确保互斥）
        if self._floating_icon and self._floating_icon.isVisible():
            self._floating_icon.animate_hide()
            # 等待图标隐藏完成后再显示窗口
            QTimer.singleShot(250, lambda: self._show_window_after_icon_hidden())
        else:
            self._show_window_after_icon_hidden()
    
    def _show_window_after_icon_hidden(self):
        """图标隐藏后显示窗口"""
        if self._airdrop_window:
            # 确保图标已完全隐藏（互斥）
            if self._floating_icon:
                self._floating_icon.hide()
                self._floating_icon.setVisible(False)
            # 显示窗口（确保可见）
            self._airdrop_window.show()
            self._airdrop_window.setVisible(True)
            self._airdrop_window.raise_()
            self._airdrop_window.activateWindow()
    
    
    def _hide_airdrop_to_icon(self, icon_target_pos: Optional[QPoint] = None):
        """窗口拖到边缘/关闭，隐藏窗口并显示图标"""
        if not self._airdrop_window:
            return
        
        # 确保窗口已隐藏（互斥）
        if self._airdrop_window.isVisible():
            self._airdrop_window.hide()
            self._airdrop_window.setVisible(False)
        
        # 显示悬浮图标（带动画，确保互斥）
        if self._floating_icon:
            # 延迟一点显示图标，让窗口完全隐藏后再显示
            # 如果指定了目标位置（从窗口隐藏位置），从该位置动画出现
            QTimer.singleShot(100, lambda: self._floating_icon.animate_show(icon_target_pos))
    
    def _on_airdrop_window_destroyed(self):
        """隔空投送窗口被销毁"""
        self._airdrop_window = None
        # 显示悬浮图标（确保互斥）
        if self._floating_icon:
            self._floating_icon.animate_show()
    
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
        
        # 退出应用
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
        import os
        import sys
        # 强制退出，确保进程完全终止
        os._exit(0)
    
    def _cleanup_resources(self):
        """清理所有资源"""
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
        
        # 关闭隔空投送窗口
        if self._airdrop_window:
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
        
        # 根据用户选择执行相应操作（Windows/Linux）
        if close_behavior == "tray":
            # 退出到托盘
            if not QSystemTrayIcon.isSystemTrayAvailable() or not self._tray_icon:
                # 系统托盘不可用，提示用户
                reply = QMessageBox.warning(
                    self,
                    "系统托盘不可用",
                    "当前系统不支持系统托盘功能，将直接退出应用程序。",
                    QMessageBox.Ok
                )
                # 直接退出
                self._cleanup_resources()
                super().closeEvent(event)
                # 确保应用完全退出
                from PySide6.QtWidgets import QApplication
                QApplication.quit()
                import os
                import sys
                # 强制退出，确保进程完全终止
                os._exit(0)
            else:
                # 退出到托盘
                event.ignore()  # 阻止窗口关闭
                self.hide()  # 隐藏窗口
                
                # 确保托盘图标仍然显示
                if self._tray_icon:
                    if not self._tray_icon.isVisible():
                        self._tray_icon.show()
                
                # Windows/Linux: 显示托盘通知
                self._tray_icon.showMessage(
                    "Ai 绩效客户端",
                    "应用已最小化到系统托盘",
                    QSystemTrayIcon.Information,
                    2000
                )
        else:
            # 直接退出
            self._cleanup_resources()
            super().closeEvent(event)
            # 确保应用完全退出
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
            import os
            import sys
            # 强制退出，确保进程完全终止
            os._exit(0)
    
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
        """接收到健康检查数据，检查是否有 help_text 字段，动态更新标签"""
        help_text = health_data.get("help_text")
        # 只有在 help_text 存在、是字符串、且非空时才显示
        # 处理三种情况：不存在、null、空字符串
        if help_text and isinstance(help_text, str) and help_text.strip():
            # 更新帮助中心标签的文字并显示
            self.help_label.setText(help_text.strip())
            self.help_label.setVisible(True)
        else:
            # 如果没有 help_text、为 null、或为空字符串，隐藏标签
            self.help_label.setVisible(False)
    
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
        self._update_dialog.show()
        self._update_dialog_shown = True
    
    def _setup_background_notification_service(self):
        """设置后台通知服务（应用未运行时也能接收通知）"""
        try:
            from utils.system_notification_service import SystemNotificationService
            from utils.config_manager import ConfigManager
            
            # 检查用户是否启用了通知
            config = ConfigManager.load()
            if not config.get("notifications", True):
                return
            
            service = SystemNotificationService()
            
            # 检查服务状态
            if not service.is_installed():
                # 如果未安装，尝试安装
                success, msg = service.install()
                if success:
                    # 安装成功后启用
                    service.enable()
                # 静默失败，不显示错误（避免干扰用户体验）
            elif not service.is_enabled():
                # 如果已安装但未启用，启用它
                service.enable()
        except Exception:
            # 静默失败，不干扰主程序
            pass
    
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
