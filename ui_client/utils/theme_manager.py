import json
import platform
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor

from utils.config_manager import ConfigManager

BASE_DIR = Path(__file__).resolve().parents[1]
THEME_DIR = BASE_DIR / "themes"


class ThemeManager:
    """
    主题管理器：
    - 读取用户配置（auto/light/dark）
    - 自动检测系统主题（macOS/Windows）
    - 应用主题样式（QSS）
    """

    @staticmethod
    def detect_system_theme() -> str:
        """检测系统主题：返回 light/dark"""

        os_name = platform.system()

        # macOS (使用 AppleInterfaceStyle)
        if os_name == "Darwin":
            try:
                import subprocess
                cmd = "defaults read -g AppleInterfaceStyle"
                out = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if b"Dark" in out.stdout:
                    return "dark"
                else:
                    return "light"
            except Exception:
                return "light"

        # Windows 使用调色板检测
        if os_name == "Windows":
            palette = QApplication.palette()
            bg = palette.window().color()
            # 深色系统通常背景更暗
            return "dark" if bg.lightness() < 128 else "light"

        # Linux 默认亮色
        return "light"

    @staticmethod
    def load_qss(theme_name: str):
        """加载 QSS 文件"""

        file_map = {
            "light": THEME_DIR / "theme_light.qss",
            "dark": THEME_DIR / "theme_dark.qss"
        }

        if theme_name not in file_map:
            print("[ThemeManager] unknown theme:", theme_name)
            return

        qss_path = file_map[theme_name]
        if not qss_path.exists():
            print("[ThemeManager] QSS missing:", qss_path)
            return

        with open(qss_path, "r", encoding="utf-8") as f:
            qss = f.read()
            QApplication.instance().setStyleSheet(qss)

    @staticmethod
    def apply_theme():
        """
        按照配置文件 + 系统主题，计算最终主题并应用。
        """

        cfg = ConfigManager.load()
        preference = cfg.get("theme", "auto")

        if preference == "auto":
            theme = ThemeManager.detect_system_theme()
        else:
            theme = preference  # "light" or "dark"

        print(f"[ThemeManager] Final theme = {theme}")
        ThemeManager.load_qss(theme)

    @staticmethod
    def set_theme(theme_name: str):
        """
        被设置页调用，手动覆盖用户主题。
        theme_name 可为：auto / light / dark
        """

        if theme_name not in ("auto", "light", "dark"):
            return

        cfg = ConfigManager.load()
        cfg["theme"] = theme_name
        ConfigManager.save(cfg)

        ThemeManager.apply_theme()
