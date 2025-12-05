import ctypes
from ctypes import wintypes
import platform
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


if platform.system() == "Windows":
    user32 = ctypes.windll.user32

    WM_HOTKEY = 0x0312
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    VK_D = 0x44
    VK_A = 0x41
    VK_CONTROL = 0x11
    ID_HOTKEY = 0xA1D  # arbitrary unique id


class WindowsGlobalHotkey(QAbstractNativeEventFilter):
    """
    注册 Windows 全局热键：Ctrl + A + D（按住 Ctrl+A 后按 D）
    通过 RegisterHotKey 捕获 Ctrl + D，并结合 GetAsyncKeyState 检查 A 是否按下。
    """

    def __init__(self, window, callback):
        if platform.system() != "Windows":
            raise RuntimeError("WindowsGlobalHotkey can only be used on Windows.")

        super().__init__()
        self._callback = callback
        self._registered = False
        self._hwnd = int(window.winId())

        modifiers = MOD_CONTROL | MOD_NOREPEAT
        if not user32.RegisterHotKey(self._hwnd, ID_HOTKEY, modifiers, VK_D):
            raise RuntimeError("RegisterHotKey failed for Ctrl+D.")

        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("QCoreApplication instance is required for global hotkey.")
        app.installNativeEventFilter(self)
        self._registered = True

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0

        msg = ctypes.wintypes.MSG.from_address(message.__int__())
        if msg.message == WM_HOTKEY and msg.wParam == ID_HOTKEY:
            if self._ctrl_a_pressed():
                try:
                    self._callback()
                except Exception as e:
                    print(f"[AirDrop] Global hotkey callback error: {e}", file=sys.stderr)
                return True, 0
        return False, 0

    @staticmethod
    def _ctrl_a_pressed():
        """检查 Ctrl 和 A 是否同时按下。"""
        state_ctrl = user32.GetAsyncKeyState(ctypes.c_int(VK_CONTROL))
        state_a = user32.GetAsyncKeyState(ctypes.c_int(VK_A))
        return (state_ctrl & 0x8000) and (state_a & 0x8000)

    def unregister(self):
        if self._registered:
            user32.UnregisterHotKey(self._hwnd, ID_HOTKEY)
            self._registered = False
            QCoreApplication.instance().removeNativeEventFilter(self)

    def __del__(self):
        self.unregister()

