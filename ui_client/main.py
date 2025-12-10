import sys
import os
import encodings  # 确保 PyInstaller 包含 encodings 模块（修复 ModuleNotFoundError）
import traceback
import logging
from pathlib import Path
from datetime import datetime, timedelta
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType, QLoggingCategory, QLockFile, QSharedMemory
from utils.theme_manager import ThemeManager
from utils.config_manager import CONFIG_PATH, ConfigManager
from windows.main_window import MainWindow

# 开发开关：设置为 True 或环境变量 AI_PERF_DISABLE_LOGGING=1 可关闭日志输出
DISABLE_LOGGING = os.environ.get("AI_PERF_DISABLE_LOGGING") == "1"


def _get_log_dir() -> Path:
    """日志目录（随用户配置存放）"""
    return CONFIG_PATH.parent / "logs"


def _cleanup_old_logs(log_dir: Path, retention_hours: int) -> None:
    """删除超过保留时间的旧日志，避免磁盘占用"""
    try:
        if retention_hours <= 0:
            return
        deadline = datetime.now() - timedelta(hours=retention_hours)
        for file in log_dir.glob("*.log"):
            try:
                if datetime.fromtimestamp(file.stat().st_mtime) < deadline:
                    file.unlink()
            except Exception:
                # 忽略单个文件的删除失败
                pass
    except Exception:
        # 清理失败不影响主流程
        pass


def _get_retention_hours() -> int:
    """从配置读取日志保留时长，默认 1 小时"""
    try:
        cfg = ConfigManager.load()
        return int(cfg.get("log_retention_hours", 1)) or 1
    except Exception:
        return 1


def setup_crash_logging():
    """设置崩溃日志记录"""
    log_dir = _get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    # 创建日志文件，文件名包含时间戳
    log_file = log_dir / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    # 创建日志文件，文件名包含时间戳
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stderr)
        ]
    )

    logger = logging.getLogger('crash_logger')
    logger.info("=" * 80)
    logger.info(f"应用启动 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Python 版本: {sys.version}")
    logger.info(f"日志文件: {log_file}")
    logger.info("=" * 80)

    # 按配置清理旧日志
    retention_hours = _get_retention_hours()
    logger.info(f"日志保留时长：{retention_hours} 小时")
    _cleanup_old_logs(log_dir, retention_hours)

    return logger, log_file


def qt_message_handler(msg_type, context, message):
    """Qt 消息处理器，捕获 Qt 的警告和错误"""
    logger = logging.getLogger('qt_logger')
    
    if msg_type == QtMsgType.QtCriticalMsg:
        logger.critical(f"[Qt Critical] {message}")
        logger.critical(f"  File: {context.file}:{context.line}")
        logger.critical(f"  Function: {context.function}")
    elif msg_type == QtMsgType.QtFatalMsg:
        logger.fatal(f"[Qt Fatal] {message}")
        logger.fatal(f"  File: {context.file}:{context.line}")
        logger.fatal(f"  Function: {context.function}")
        # 记录堆栈跟踪
        logger.fatal("Stack trace:")
        for line in traceback.format_stack():
            logger.fatal(line.strip())
    elif msg_type == QtMsgType.QtWarningMsg:
        logger.warning(f"[Qt Warning] {message}")
    elif msg_type == QtMsgType.QtInfoMsg:
        logger.info(f"[Qt Info] {message}")
    elif msg_type == QtMsgType.QtDebugMsg:
        logger.debug(f"[Qt Debug] {message}")


def exception_handler(exc_type, exc_value, exc_traceback):
    """全局异常处理器，捕获所有未处理的异常"""
    if exc_type is KeyboardInterrupt or (isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt)):
        # 忽略键盘中断
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    logger = logging.getLogger('crash_logger')
    logger.critical("=" * 80)
    logger.critical("未捕获的异常！")
    logger.critical("=" * 80)
    logger.critical(f"异常类型: {exc_type.__name__}")
    logger.critical(f"异常信息: {exc_value}")
    logger.critical("堆栈跟踪:")
    
    # 记录完整的堆栈跟踪
    for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
        logger.critical(line.strip())
    
    logger.critical("=" * 80)
    
    # 尝试显示错误对话框（如果 QApplication 已创建）
    try:
        app = QApplication.instance()
        if app is not None:
            error_msg = f"应用发生错误，详细信息已记录到日志文件。\n\n异常类型: {exc_type.__name__}\n异常信息: {exc_value}"
            QMessageBox.critical(None, "应用错误", error_msg)
    except Exception:
        pass  # 如果无法显示对话框，忽略
    
    # 调用默认的异常处理器
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def main():
    # 单实例检查：确保应用只能运行一个实例
    # 使用 QLockFile 在创建 QApplication 之前检查
    import platform
    lock_file_path = CONFIG_PATH.parent / "app_instance.lock"
    lock_file = QLockFile(str(lock_file_path))
    
    # 尝试获取锁（非阻塞，等待最多100毫秒）
    if not lock_file.tryLock(100):
        # 已有实例在运行，先创建临时 QApplication 以显示消息框
        try:
            temp_app = QApplication(sys.argv)
            QMessageBox.warning(
                None,
                "应用已运行",
                "应用已经在运行中，无法启动多个实例。\n\n"
                "如果看不到应用窗口，请检查系统托盘图标。"
            )
            sys.exit(1)
        except Exception:
            # 如果无法创建 QApplication，直接退出
            print("应用已经在运行中，无法启动多个实例。", file=sys.stderr)
            sys.exit(1)
    
    # 设置崩溃日志（可通过 DISABLE_LOGGING 关闭）
    if DISABLE_LOGGING:
        logging.disable(logging.CRITICAL)  # 全局禁用日志
        logger = logging.getLogger('crash_logger')
        log_file = None
    else:
        logger, log_file = setup_crash_logging()
        # 设置全局异常处理器
        sys.excepthook = exception_handler
        # 设置 Qt 消息处理器
        qInstallMessageHandler(qt_message_handler)
    
    try:
        logger.info("初始化 QApplication...")
        app = QApplication(sys.argv)
        logger.info("QApplication 初始化成功")
    except Exception as e:
        logger.critical(f"QApplication 初始化失败: {e}")
        logger.critical(traceback.format_exc())
        # 释放锁
        lock_file.unlock()
        raise
    
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
    try:
        logger.info("应用主题...")
        ThemeManager.apply_theme()
        logger.info("主题应用成功")
    except Exception as e:
        logger.warning(f"应用主题失败: {e}")
        logger.warning(traceback.format_exc())

    try:
        logger.info("创建主窗口...")
        win = MainWindow()
        win.show()
        logger.info("主窗口创建并显示成功")
    except Exception as e:
        logger.critical(f"主窗口创建失败: {e}")
        logger.critical(traceback.format_exc())
        raise
    
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
        try:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, lambda: win.show_notification_detail(notification_id))
        except Exception as e:
            logger.warning(f"显示通知详情失败: {e}")
            logger.warning(traceback.format_exc())

    try:
        logger.info("启动应用主循环...")
        exit_code = app.exec()
        logger.info(f"应用退出，退出码: {exit_code}")
        return exit_code
    except Exception as e:
        logger.critical(f"应用主循环异常: {e}")
        logger.critical(traceback.format_exc())
        raise
    finally:
        # 释放单实例锁
        try:
            if 'lock_file' in locals():
                lock_file.unlock()
        except Exception:
            pass
        if 'logger' in locals():
            logger.info("=" * 80)
            logger.info(f"应用结束 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 80)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # 最后的异常捕获，确保所有错误都被记录
        try:
            logger = logging.getLogger('crash_logger')
        except:
            # 如果日志系统也失败了，至少输出到 stderr
            print(f"致命错误: {e}", file=sys.stderr)
            traceback.print_exc()
        else:
            logger.critical(f"致命错误: {e}")
            logger.critical(traceback.format_exc())
        sys.exit(1)
