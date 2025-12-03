import sys
import encodings  # 确保 PyInstaller 包含 encodings 模块（修复 ModuleNotFoundError）
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from utils.theme_manager import ThemeManager
from windows.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    
    # macOS: 关闭窗口时不退出应用，保留在状态栏
    # 打开时在 Dock 显示，关闭窗口后隐藏 Dock 图标
    # Windows/Linux: 根据用户配置决定（首次会询问）
    import platform
    if platform.system() == "Darwin":
        # macOS: 不自动退出，由 closeEvent 控制
        app.setQuitOnLastWindowClosed(False)
        # 初始状态：在 Dock 显示（不设置 LSUIElement）
        # 关闭窗口后通过代码动态隐藏 Dock 图标
        from PySide6.QtCore import Qt
        app.setAttribute(Qt.AA_DontShowIconsInMenus, False)  # 确保菜单图标正常显示
    else:
        # Windows/Linux: 默认自动退出，但 closeEvent 可以覆盖
        app.setQuitOnLastWindowClosed(True)

    # 设置应用图标
    app_dir = Path(__file__).parent
    icon_paths = [
        app_dir / "resources" / "app_icon.icns",  # macOS
        app_dir / "resources" / "app_icon.ico",   # Windows
        app_dir / "resources" / "app_icon.png",   # 通用/Linux
    ]
    
    for icon_path in icon_paths:
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break

    # 应用主题（根据 config + 系统）
    ThemeManager.apply_theme()

    win = MainWindow()
    win.show()
    
    # 检查是否有命令行参数
    notification_id = None
    install_background_service = False
    
    for arg in sys.argv:
        if arg.startswith("--notification-id="):
            try:
                notification_id = int(arg.split("=")[1])
            except (ValueError, IndexError):
                pass
        elif arg == "--install-background-service":
            install_background_service = True
    
    # 如果要求安装后台服务，先安装然后退出（不显示窗口）
    if install_background_service:
        try:
            from utils.system_notification_service import SystemNotificationService
            from utils.config_manager import ConfigManager
            
            # 检查用户是否启用了通知
            config = ConfigManager.load()
            if config.get("notifications", True):
                service = SystemNotificationService()
                if not service.is_installed():
                    success, msg = service.install()
                    if success:
                        service.enable()
                elif not service.is_enabled():
                    service.enable()
        except Exception:
            # 静默失败
            pass
        sys.exit(0)
    
    # 如果有通知ID，显示通知详情
    if notification_id:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: win.show_notification_detail(notification_id))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
