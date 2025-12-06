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
    注册 Windows 全局热键：Ctrl + Shift + A
    通过 RegisterHotKey 直接注册 Ctrl + Shift + A 组合键。
    """

    def __init__(self, window, callback):
        if platform.system() != "Windows":
            raise RuntimeError("WindowsGlobalHotkey can only be used on Windows.")

        super().__init__()
        self._callback = callback
        self._registered = False
        self._hwnd = int(window.winId())

        # VK_A = 0x41 (65)
        VK_A = 0x41
        MOD_SHIFT = 0x0004
        modifiers = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
        if not user32.RegisterHotKey(self._hwnd, ID_HOTKEY, modifiers, VK_A):
            raise RuntimeError("RegisterHotKey failed for Ctrl+Shift+A.")

        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("QCoreApplication instance is required for global hotkey.")
        app.installNativeEventFilter(self)
        self._registered = True
        print("[AirDrop] ✅ Windows global hotkey registered: Ctrl + Shift + A", file=sys.stderr)

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0

        msg = ctypes.wintypes.MSG.from_address(message.__int__())
        if msg.message == WM_HOTKEY and msg.wParam == ID_HOTKEY:
            try:
                print("[AirDrop] ✅ Hotkey triggered: Ctrl + Shift + A", file=sys.stderr)
                self._callback()
            except Exception as e:
                print(f"[AirDrop] Global hotkey callback error: {e}", file=sys.stderr)
            return True, 0
        return False, 0

    def unregister(self):
        if self._registered:
            user32.UnregisterHotKey(self._hwnd, ID_HOTKEY)
            self._registered = False
            QCoreApplication.instance().removeNativeEventFilter(self)

    def __del__(self):
        self.unregister()

