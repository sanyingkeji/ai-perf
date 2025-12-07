import sys
import os
import encodings  # 确保 PyInstaller 包含 encodings 模块（修复 ModuleNotFoundError）
import logging
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from utils.theme_manager import ThemeManager
from utils.config_manager import CONFIG_PATH, ConfigManager
from windows.main_window import MainWindow

# 开发开关：设置 True 或环境变量 AI_PERF_DISABLE_LOGGING=1 关闭日志
DISABLE_LOGGING = os.environ.get("AI_PERF_DISABLE_LOGGING") == "1"


def _get_log_dir() -> Path:
    return CONFIG_PATH.parent / "logs"


def _get_retention_hours() -> int:
    try:
        cfg = ConfigManager.load()
        return int(cfg.get("log_retention_hours", 1)) or 1
    except Exception:
        return 1


def _cleanup_old_logs(log_dir: Path, retention_hours: int) -> None:
    try:
        if retention_hours <= 0:
            return
        deadline = datetime.now() - timedelta(hours=retention_hours)
        for f in log_dir.glob("*.log"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < deadline:
                    f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def main():
    # 本地诊断日志，落盘到用户目录，避免被升级覆盖
    if DISABLE_LOGGING:
        logging.disable(logging.CRITICAL)
        log_file = None
    else:
        try:
            log_dir = _get_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"admin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                handlers=[
                    logging.FileHandler(log_file, encoding='utf-8'),
                    logging.StreamHandler(sys.stderr),
                ],
            )
            logger = logging.getLogger("admin_app")
            logger.info("Admin app start, log file: %s", log_file)

            # 按配置清理旧日志
            retention_hours = _get_retention_hours()
            logger.info("日志保留时长：%s 小时", retention_hours)
            _cleanup_old_logs(log_dir, retention_hours)
        except Exception:
            # 日志初始化失败不阻断启动
            pass

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

