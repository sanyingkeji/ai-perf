from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QStackedWidget, QLabel, QDialog, QPushButton, QMessageBox, QSystemTrayIcon, QMenu, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QAction
from pathlib import Path
import platform

from windows.history_score_view import HistoryScoreView
from windows.monthly_score_view import MonthlyScoreView
from windows.employee_view import EmployeeView
from windows.settings_view import SettingsView
from windows.etl_log_view import EtlLogView
from windows.ai_log_view import AiLogView
from windows.operation_log_view import OperationLogView
from windows.health_check_view import HealthCheckView
from windows.report_view import ReportView
from windows.update_dialog import UpdateDialog
from utils.config_manager import ConfigManager
from utils.google_login import login_and_get_id_token, GoogleLoginError
from utils.api_client import AdminApiClient, ApiError, AuthError
from widgets.toast import Toast
from widgets.loading_overlay import LoadingOverlay
from datetime import date


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Ai 绩效管理端")
        self.resize(1400, 900)
        
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
            0: False,  # 每日评分
            1: False,  # 月度评分
            2: False,  # 员工列表
            3: False,  # ETL日志
            4: False,  # AI调用日志
            5: False,  # 操作日志
            6: False,  # 健康检查
            7: False,  # 版本管理
            8: False,  # 通知管理
            9: False,  # 日历管理
            10: False,  # 日常运维
            11: False,  # 统计&报表
            12: False,  # 设置
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

        logo = QLabel("Ai Perf\n管理端")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("font-size:18px; font-weight:bold;")
        nav_layout.addWidget(logo)

        self.nav = QListWidget()
        # 菜单项定义（索引对应页面）
        self.menu_items = [
            "每日评分",
            "月度评分",
            "员工列表",
            "ETL日志",
            "AI调用日志",
            "操作日志",
            "健康检查",
            "版本管理",
            "通知管理",
            "日历管理",
            "日常运维",
            "统计&报表",
            "设置",
        ]
        # 初始化时添加所有菜单项
        for item in self.menu_items:
            self.nav.addItem(item)
        self.nav.setFixedWidth(180)
        
        # 菜单权限（登录后从服务器获取）
        # 默认未登录状态，只显示"设置"菜单
        self.menu_permission = {
            "is_admin": False,
            "allowed_menus": ["设置"],
        }
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

        root_layout.addWidget(nav_container)

        # 右侧：页面 stack
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        # 各页面
        from windows.version_view import VersionView
        from windows.notification_view import NotificationView
        from windows.maintenance_view import MaintenanceView
        from windows.workday_view import WorkdayView
        self.history_page = HistoryScoreView()
        self.monthly_score_page = MonthlyScoreView()
        self.employee_page = EmployeeView()
        self.etl_log_page = EtlLogView()
        self.ai_log_page = AiLogView()
        self.operation_log_page = OperationLogView()
        self.health_check_page = HealthCheckView()
        self.version_page = VersionView()
        self.notification_page = NotificationView(self)
        self.workday_page = WorkdayView()
        self.maintenance_page = MaintenanceView(self)
        self.report_page = ReportView()
        self.settings_page = SettingsView()

        self.stack.addWidget(self.history_page)
        self.stack.addWidget(self.monthly_score_page)
        self.stack.addWidget(self.employee_page)
        self.stack.addWidget(self.etl_log_page)
        self.stack.addWidget(self.ai_log_page)
        self.stack.addWidget(self.operation_log_page)
        self.stack.addWidget(self.health_check_page)
        self.stack.addWidget(self.version_page)
        self.stack.addWidget(self.notification_page)
        self.stack.addWidget(self.workday_page)
        self.stack.addWidget(self.maintenance_page)
        self.stack.addWidget(self.report_page)
        self.stack.addWidget(self.settings_page)

        # 全局加载中遮罩
        self.loading_overlay = LoadingOverlay(self)

        # 绑定导航
        self.nav.currentRowChanged.connect(self.switch_page)
        
        # 初始化时，无论是否已登录，都先只显示"设置"菜单
        # 等权限获取完成后再更新菜单显示
        self._update_menu_visibility()
        
        # 检查是否已登录，决定默认显示的页面
        is_logged_in = self._ensure_logged_in()
        if is_logged_in:
            # 已登录，默认选中"设置"页面（索引12），等权限获取完成后再切换到合适的页面
            self.nav.setCurrentRow(12)
        else:
            # 未登录，默认选中"设置"页面（索引12）
            self.nav.setCurrentRow(12)
        
        # 标记：是否已经显示过升级弹窗（防止重复弹窗）
        self._update_dialog_shown = False
        
        # 标记：是否已经有登录弹窗在显示（防止重复弹窗）
        self._login_dialog_shown = False
        
        # 标记：是否正在登录中（防止重复点击登录按钮）
        self._login_in_progress = False
        
        # 标记：是否已经请求过菜单权限（防止重复请求）
        self._menu_permission_requested = False
        
        # 系统托盘（在窗口初始化时设置，确保窗口关闭后图标仍然存在）
        self._tray_icon = None
        self._setup_system_tray()
        
        # macOS: 确保应用在窗口关闭后仍然运行
        import platform
        if platform.system() == "Darwin":
            # 将托盘图标保存为类变量，确保窗口关闭后仍然存在
            if not hasattr(MainWindow, '_app_tray_icon'):
                MainWindow._app_tray_icon = self._tray_icon
        
        # 应用窗口标题栏主题适配
        self._apply_window_title_bar_theme()
        
        # 监听主题变化，动态更新标题栏
        from PySide6.QtCore import QTimer
        self._theme_check_timer = QTimer()
        self._theme_check_timer.timeout.connect(self._check_and_update_title_bar_theme)
        self._theme_check_timer.start(1000)  # 每秒检查一次
        
        # 应用启动时检查版本升级（延迟检查，等待窗口显示）
        QTimer.singleShot(1000, self._check_version_on_startup)
        
        # 应用启动时检查登录状态（延迟检查，等待窗口显示）
        # 如果未登录，会显示登录提示弹窗
        QTimer.singleShot(1500, self._check_login_on_startup)

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
        
        # 设置标志，防止重复弹窗
        self._login_dialog_shown = True
        
        dlg = QDialog(self)
        dlg.setWindowTitle("登录提示")
        dlg.setModal(True)

        layout = QVBoxLayout(dlg)
        label = QLabel("当前未登录，请用管理员邮箱进行谷歌授权登录")
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
            
            # 在后台线程中执行登录
            from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal
            
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
            
            def _on_callback_received():
                """已收到回调，正在登录中"""
                self.show_loading("已成功接收到谷歌回调信息，正在登录中...", closeable=False)
            
            def _on_login_success():
                self.hide_loading()
                self._login_in_progress = False  # 重置登录状态
                result["logged_in"] = True
                Toast.show_message(self, "Google 登录成功")
                # 登录成功后，获取菜单权限并更新菜单显示
                self._load_menu_permission()
            
            def _on_login_error(error_msg: str):
                self.hide_loading()
                self._login_in_progress = False  # 重置登录状态
                # 清理 worker 引用
                if hasattr(self, '_login_worker') and worker in self._login_worker:
                    self._login_worker.remove(worker)
                
                # 如果是权限错误，使用 QMessageBox 显示更详细的提示
                if "无权限" in error_msg or "只有以下管理员邮箱" in error_msg or "权限" in error_msg:
                    from PySide6.QtWidgets import QMessageBox
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle("登录失败 - 无权限")
                    msg_box.setText(f"您的邮箱不是管理员邮箱，无法登录管理端。\n\n{error_msg}\n\n请联系系统管理员将您的邮箱添加到管理员列表。")
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
        
        # 对话框关闭后，重置标志（允许下次再次弹窗）
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
        隐藏"加载中"遮罩。
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

        # 切换到设置页面（index=11）时，刷新登录状态
        if index == 11 and hasattr(self.settings_page, "refresh_login_status"):
            self.settings_page.refresh_login_status()

        # 只对业务 Tab 做一次性自动请求
        # 判定条件：已登录 且 该页面还没有请求过数据
        if index in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11) and not self._page_first_loaded.get(index, False):
            # 未登录：只提示，不标记为已加载，方便登录后再自动触发
            if not self._ensure_logged_in():
                return
            # 已登录，继续执行
            self._page_first_loaded[index] = True

            # 为避免 UI 卡顿，这里的自动请求仍交由各页面内部处理
            if index == 0 and hasattr(self.history_page, "reload_from_api"):
                # 每日评分
                self.history_page.reload_from_api()
            elif index == 1 and hasattr(self.monthly_score_page, "reload_from_api"):
                # 月度评分
                self.monthly_score_page.reload_from_api()
            elif index == 2 and hasattr(self.employee_page, "reload_from_api"):
                # 员工列表
                self.employee_page.reload_from_api()
            elif index == 3 and hasattr(self.etl_log_page, "reload_from_api"):
                # ETL日志
                self.etl_log_page.reload_from_api()
            elif index == 4 and hasattr(self.ai_log_page, "reload_from_api"):
                # AI调用日志
                self.ai_log_page.reload_from_api()
            elif index == 5 and hasattr(self.operation_log_page, "reload_from_api"):
                # 操作日志
                self.operation_log_page.reload_from_api()
            elif index == 6 and hasattr(self.health_check_page, "reload_from_api"):
                # 健康检查
                self.health_check_page.reload_from_api()
            elif index == 7 and hasattr(self.version_page, "reload_from_api"):
                # 版本管理
                self.version_page.reload_from_api()
            elif index == 8 and hasattr(self.notification_page, "reload_from_api"):
                # 通知管理
                self.notification_page.reload_from_api()
            elif index == 9 and hasattr(self.workday_page, "_load_workdays"):
                # 日历管理
                self.workday_page._load_workdays()
            elif index == 10 and hasattr(self.maintenance_page, "reload_from_api"):
                # 日常运维
                self.maintenance_page.reload_from_api()
            elif index == 11 and hasattr(self.report_page, "reload_from_api"):
                # 统计&报表
                self.report_page.reload_from_api()
    
    # -------- 启动时登录检查 --------
    def _check_login_on_startup(self):
        """应用启动时检查登录状态，如果未登录则提示"""
        
        # 确保窗口已显示
        if not self.isVisible():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self._check_login_on_startup)
            return
        
        # 检查是否已登录
        is_logged_in = self._ensure_logged_in()
        if not is_logged_in:
            # 如果未登录，显示登录提示弹窗
            # 使用 force=False，如果已经有弹窗在显示则不会重复弹窗
            result = self.show_login_required_dialog(force=False)
        # 注意：如果已登录，菜单权限已经在 __init__ 中立即请求了，这里不需要重复请求
    
    def _load_menu_permission(self):
        """从服务器获取菜单权限并更新菜单显示（异步执行）"""
        # 标记已请求过菜单权限
        self._menu_permission_requested = True
        # 显示加载状态提示
        self.show_loading("正在获取菜单权限...", closeable=False)
        
        from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal, Slot
        
        class _MenuPermissionWorkerSignals(QObject):
            finished = Signal(dict)  # permission_data
            error = Signal(str)  # error message
        
        class _MenuPermissionWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _MenuPermissionWorkerSignals()
            
            @Slot()
            def run(self):
                try:
                    client = AdminApiClient.from_config()
                    permission_data = client.get_menu_permission()
                    self.signals.finished.emit(permission_data)
                except (AuthError, ApiError) as e:
                    self.signals.error.emit(str(e))
                except Exception as e:
                    self.signals.error.emit(f"获取菜单权限失败：{type(e).__name__}: {e}")
        
        def on_permission_loaded(permission_data: dict):
            """权限加载成功回调"""
            try:
                # 隐藏加载状态
                self.hide_loading()
                
                if permission_data.get("status") == "success":
                    is_admin = permission_data.get("is_admin", False)
                    allowed_menus = permission_data.get("allowed_menus", [])
                    
                    self.menu_permission = {
                        "is_admin": is_admin,
                        "allowed_menus": allowed_menus,
                    }
                    self._update_menu_visibility()
                    
                    # 如果是超级管理员，切换到第一个页面（每日评分）
                    # 如果是普通管理员，切换到第一个允许的菜单项
                    if is_admin:
                        # 超级管理员，切换到每日评分页面（索引0）
                        if 0 < self.nav.count():
                            self.nav.setCurrentRow(0)
                    else:
                        # 普通管理员，切换到第一个允许的菜单项
                        if allowed_menus:
                            first_menu = allowed_menus[0]
                            if first_menu in self.menu_items:
                                menu_index = self.menu_items.index(first_menu)
                                if menu_index < self.nav.count():
                                    self.nav.setCurrentRow(menu_index)
                        else:
                            # 如果没有允许的菜单，保持在"设置"页面
                            self.nav.setCurrentRow(11)
                else:
                    # 如果获取失败，只显示"设置"菜单（未登录状态）
                    self.menu_permission = {
                        "is_admin": False,
                        "allowed_menus": ["设置"],
                    }
                    self._update_menu_visibility()
            except RuntimeError:
                # 对象已被删除，忽略
                pass
            except Exception as e:
                # 记录错误但继续执行
                print(f"[ERROR] on_permission_loaded 异常: {e}")
                import traceback
                traceback.print_exc()
                try:
                    self.hide_loading()
                except:
                    pass
        
        def on_permission_error(error_msg: str):
            """权限加载失败回调"""
            try:
                # 隐藏加载状态
                self.hide_loading()
                # 如果获取失败（如未登录），只显示"设置"菜单
                self.menu_permission = {
                    "is_admin": False,
                    "allowed_menus": ["设置"],
                }
                self._update_menu_visibility()
            except RuntimeError:
                # 对象已被删除，忽略
                pass
            except Exception as e:
                # 记录错误但继续执行
                print(f"[ERROR] on_permission_error 异常: {e}")
                import traceback
                traceback.print_exc()
                try:
                    self.hide_loading()
                except:
                    pass
        
        # 创建并启动后台任务
        worker = _MenuPermissionWorker()
        # 保存 worker 引用，防止被垃圾回收
        if not hasattr(self, '_menu_permission_workers'):
            self._menu_permission_workers = []
        self._menu_permission_workers.append(worker)
        
        worker.signals.finished.connect(on_permission_loaded)
        worker.signals.error.connect(on_permission_error)
        QThreadPool.globalInstance().start(worker)
    
    def _update_menu_visibility(self):
        """根据菜单权限更新菜单项的显示/隐藏"""
        is_admin = self.menu_permission.get("is_admin", False)
        allowed_menus = self.menu_permission.get("allowed_menus", [])
        
        # 获取当前选中的菜单索引
        current_index = self.nav.currentRow()
        
        # 如果是超级管理员，显示所有菜单
        if is_admin:
            for i in range(self.nav.count()):
                item = self.nav.item(i)
                if item:
                    item.setHidden(False)
        else:
            # 普通管理员，只显示允许的菜单
            for i, menu_name in enumerate(self.menu_items):
                item = self.nav.item(i)
                if item:
                    # 如果菜单在允许列表中，显示；否则隐藏
                    item.setHidden(menu_name not in allowed_menus)
        
        # 如果当前选中的菜单项被隐藏了，切换到第一个可见的菜单项
        if current_index >= 0 and current_index < self.nav.count():
            current_item = self.nav.item(current_index)
            if current_item and current_item.isHidden():
                # 找到第一个可见的菜单项
                first_visible_index = -1
                for i in range(self.nav.count()):
                    item = self.nav.item(i)
                    if item and not item.isHidden():
                        first_visible_index = i
                        break
                
                if first_visible_index >= 0:
                    self.nav.setCurrentRow(first_visible_index)
                else:
                    # 如果没有可见的菜单项，默认选中"设置"（索引11）
                    # 确保"设置"菜单始终可见
                    settings_index = 11
                    if settings_index < self.nav.count():
                        settings_item = self.nav.item(settings_index)
                        if settings_item:
                            settings_item.setHidden(False)
                            self.nav.setCurrentRow(settings_index)
                    else:
                        self.nav.setCurrentRow(-1)
        
        # 重新调整菜单高度
        from PySide6.QtCore import QTimer
        def adjust_nav_height():
            """根据可见菜单项数量自动调整菜单高度"""
            visible_count = sum(1 for i in range(self.nav.count()) if not self.nav.item(i).isHidden())
            if visible_count > 0:
                item_height = self.nav.sizeHintForRow(0)
                if item_height <= 0:
                    item_height = 30
                total_height = visible_count * item_height + 4
                self.nav.setFixedHeight(total_height)
            else:
                self.nav.setFixedHeight(30)
        
        QTimer.singleShot(100, adjust_nav_height)
    
    def _check_version_on_startup(self):
        """应用启动时检查版本升级（无需登录）"""
        try:
            cfg = ConfigManager.load()
            client_version = cfg.get("client_version", "1.0.0")
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8880").strip()
        except Exception:
            return
        
        # 后台检查版本（直接使用HTTP请求，不需要登录）
        from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal, Slot
        import httpx
        
        class _VersionCheckWorkerSignals(QObject):
            finished = Signal(dict)  # version_info
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
                    url = f"{self._api_base}/admin/health"
                    params = {"current_version": self._current_version} if self._current_version else None
                    r = httpx.get(url, params=params, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and data.get("status") == "success":
                            health_data = data.get("data")
                            if health_data:
                                version_info = health_data.get("version_info")
                                if version_info:
                                    self.signals.finished.emit(version_info)
                except Exception:
                    # 版本检查失败不影响应用启动
                    pass
        
        worker = _VersionCheckWorker(client_version, api_base)
        worker.signals.finished.connect(self._on_version_update_available)
        QThreadPool.globalInstance().start(worker)
    
    def _on_version_update_available(self, version_info: dict):
        """检测到新版本，显示升级弹窗"""
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
        from utils.resource_path import get_app_icon_path
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
        # macOS: 确保窗口显示在最前面，并恢复 Dock 图标
        import platform
        if platform.system() == "Darwin":
            from PySide6.QtCore import Qt
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
            # 确保窗口获得焦点
            self.setFocus()
            # 恢复 Dock 图标（使用 PyObjC）
            try:
                from AppKit import NSApplication, NSImage
                import os
                app = NSApplication.sharedApplication()
                # 0 = NSApplicationActivationPolicyRegular (显示 Dock 图标)
                app.setActivationPolicy_(0)
                # 重新设置应用图标，确保 Dock 图标正确显示
                app_dir = Path(__file__).parent.parent
                icon_path = app_dir / "resources" / "app_icon.icns"
                if icon_path.exists() and os.path.exists(str(icon_path)):
                    # 使用 NSImage 加载图标并设置
                    icon_image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
                    if icon_image is not None:
                        app.setApplicationIconImage_(icon_image)
                # 激活应用，确保 Dock 图标正确显示
                app.activateIgnoringOtherApps_(True)
            except ImportError as e:
                # PyObjC 不可用
                import sys
                print(f"[WARNING] PyObjC not available, cannot show Dock icon: {e}", file=sys.stderr)
            except Exception as e:
                # 其他错误
                import sys
                print(f"[WARNING] Failed to show Dock icon: {type(e).__name__}: {e}", file=sys.stderr)
    
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
        QApplication.quit()
        import os
        os._exit(0)
    
    def _cleanup_resources(self):
        """清理所有资源"""
        # 停止所有正在运行的登录 worker
        if hasattr(self, '_login_worker') and self._login_worker:
            for worker in self._login_worker[:]:
                if hasattr(worker, 'stop'):
                    worker.stop()
        
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
        
        # 停止线程池中的所有任务
        try:
            from PySide6.QtCore import QThreadPool
            thread_pool = QThreadPool.globalInstance()
            thread_pool.clear()
        except Exception:
            pass
        
        # 清理登录 worker 引用
        if hasattr(self, '_login_worker'):
            self._login_worker.clear()
    
    def showEvent(self, event):
        """窗口显示事件：在窗口显示后立即请求菜单权限（如果已登录）"""
        super().showEvent(event)
        # 如果已登录且还没有请求过菜单权限，立即请求（最优先）
        if not self._menu_permission_requested:
            is_logged_in = self._ensure_logged_in()
            if is_logged_in:
                self._menu_permission_requested = True
                # 使用 QTimer.singleShot(0, ...) 确保在事件循环中执行
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self._load_menu_permission)
    
    def closeEvent(self, event):
        """窗口关闭事件"""
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
            
            # 根据操作系统显示不同的提示文本
            system = platform.system()
            if tray_available:
                if system == "Darwin":
                    # macOS
                    msg_box.setInformativeText(
                        "• 保留在状态栏：窗口关闭后，图标保留在状态栏，右键可打开窗口或退出\n"
                        "• 直接退出：完全退出应用程序\n\n"
                        "此选择将保存，下次关闭时将自动使用该方式。"
                    )
                else:
                    # Windows/Linux
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
                # 根据操作系统显示不同的按钮文本
                if system == "Darwin":
                    btn_tray = msg_box.addButton("保留在状态栏", QMessageBox.AcceptRole)
                else:
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
                QApplication.quit()
                import os
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
                    "Ai 绩效管理端",
                    "应用已最小化到系统托盘",
                    QSystemTrayIcon.Information,
                    2000
                )
        else:
            # 直接退出
            self._cleanup_resources()
            super().closeEvent(event)
            QApplication.quit()
            import os
            os._exit(0)
    
    def _apply_window_title_bar_theme(self):
        """应用窗口标题栏主题样式"""
        from utils.theme_manager import ThemeManager
        from utils.config_manager import ConfigManager
        
        cfg = ConfigManager.load()
        preference = cfg.get("theme", "auto")
        
        if preference == "auto":
            theme = ThemeManager.detect_system_theme()
        else:
            theme = preference
        
        # 根据主题设置窗口样式
        if theme == "dark":
            # 深色主题：标题栏背景和文字颜色
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #202124;
                }
            """)
        else:
            # 浅色主题：标题栏背景和文字颜色
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #F7F9FC;
                }
            """)
    
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

