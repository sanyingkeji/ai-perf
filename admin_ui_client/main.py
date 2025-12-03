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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

