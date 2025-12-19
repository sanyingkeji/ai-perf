"""
macOS 系统级全局快捷键实现
使用 CGEventTap 实现全局快捷键监听（兼容 macOS 10.13+）
"""
import platform
import sys
from typing import Optional
import threading

if platform.system() == "Darwin":
    try:
        import Quartz
        from PySide6.QtCore import QObject, Signal, QCoreApplication
        _has_quartz = True
    except ImportError as e:
        print(f"[WARNING] PyObjC not available: {e}", file=sys.stderr)
        Quartz = None
        QObject = object
        _has_quartz = False
else:
    Quartz = None
    QObject = object
    _has_quartz = False


def get_macos_version():
    """获取 macOS 版本号（公开函数，供其他模块使用）"""
    try:
        version_str = platform.mac_ver()[0]  # 例如 "14.7.8"
        if version_str:
            parts = version_str.split('.')
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            return (major, minor)
    except:
        pass
    return (0, 0)

# 内部使用的别名
_get_macos_version = get_macos_version


def check_accessibility_permission() -> Optional[bool]:
    """
    检查 macOS 辅助功能权限（使用 CGEventTap，兼容 macOS 10.13+）
    
    Returns:
        bool 或 None: True=已授权, False=未授权, None=无法确定
    """
    if platform.system() != "Darwin":
        return None
    
    if not _has_quartz or Quartz is None:
        return None
    
    try:
        # 尝试创建一个临时的 CGEventTap 来检查权限
        def dummy_callback(proxy, event_type, event, refcon):
            return event
        
        # 使用 CGEventMaskBit 创建事件掩码（兼容所有版本）
        try:
            # 尝试使用 CGEventMaskBit（macOS 10.13+）
            event_mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        except (AttributeError, TypeError):
            # 如果 CGEventMaskBit 不可用，使用直接数值（兼容旧版本）
            # kCGEventKeyDown = 10, 事件掩码 = 1 << 10 = 0x400
            event_mask = 0x400
        
        event_tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,  # kCGSessionEventTap = 1
            Quartz.kCGHeadInsertEventTap,  # kCGHeadInsertEventTap = 0
            Quartz.kCGEventTapOptionDefault,  # kCGEventTapOptionDefault = 0
            event_mask,  # 按键事件掩码
            dummy_callback,
            None
        )
        
        if event_tap is None:
            # 没有权限或创建失败
            return False
        
        # 有权限，立即释放
        Quartz.CFRelease(event_tap)
        return True
    except Exception:
        # 无法确定
        return None


def open_accessibility_settings() -> bool:
    """
    打开 macOS 辅助功能设置页面（兼容 macOS 10.13+）
    
    Returns:
        bool: 是否成功打开设置页面
    """
    if platform.system() != "Darwin":
        return False
    
    try:
        import subprocess
        macos_version = _get_macos_version()
        
        if macos_version[0] >= 13:  # macOS 13 (Ventura) 及以上
            # 使用新的系统设置 URL
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            ])
        else:  # macOS 12 及以下
            # 使用旧的系统偏好设置 URL
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            ])
        return True
    except Exception as e:
        print(f"无法打开系统设置: {e}", file=sys.stderr)
        # 尝试备用方法
        try:
            import subprocess
            subprocess.Popen(["open", "/System/Library/PreferencePanes/Security.prefPane"])
            return True
        except:
            return False


class MacGlobalHotkey(QObject):
    """
    注册 macOS 全局热键：Control + A
    使用 CGEventTap 实现全局快捷键监听（兼容 macOS 10.13+）
    
    注意：
    1. 需要用户在系统偏好设置中授予辅助功能权限
       路径：系统偏好设置 > 安全性与隐私 > 隐私 > 辅助功能
    2. 快捷键使用 Control + A
    3. 兼容 macOS 10.13 及以上版本
    """
    
    # 定义信号，用于在主线程中执行回调
    _hotkey_triggered = Signal()
    
    def __init__(self, callback):
        if platform.system() != "Darwin":
            raise RuntimeError("MacGlobalHotkey can only be used on macOS.")
        
        if not _has_quartz or Quartz is None:
            raise RuntimeError("PyObjC is required for MacGlobalHotkey. Please install: pip install pyobjc")
        
        super().__init__()
        self._callback = callback
        self._event_tap = None
        self._run_loop_source = None
        self._run_loop = None
        self._thread = None
        self._registered = False
        self._stop_event = threading.Event()
        
        # 连接信号到回调函数
        self._hotkey_triggered.connect(self._execute_callback)
        
        try:
            self._register_hotkey()
        except Exception:
            raise
    
    def _execute_callback(self):
        """在主线程中执行回调函数（通过信号槽机制调用）"""
        try:
            self._callback()
        except Exception:
            pass
    
    def _event_tap_callback(self, proxy, event_type, event, refcon):
        """CGEventTap 回调函数"""
        try:
            # 检查对象是否仍然有效（防止在销毁过程中访问）
            if not hasattr(self, '_registered') or not self._registered:
                return event
            
            # kCGEventKeyDown = 10
            if event_type != Quartz.kCGEventKeyDown:
                return event
            
            # 获取键码和修饰键
            key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            
            # 检查修饰键
            # kCGEventFlagMaskControl = 0x40000
            has_control = bool(flags & Quartz.kCGEventFlagMaskControl)
            has_command = bool(flags & Quartz.kCGEventFlagMaskCommand)
            has_option = bool(flags & Quartz.kCGEventFlagMaskAlternate)
            has_shift = bool(flags & Quartz.kCGEventFlagMaskShift)
            
            # 检查是否是 Control + A
            # A键的键码是0 (kVK_ANSI_A)
            is_a_key = (key_code == 0)
            
            if has_control and is_a_key:
                # 检查是否有其他修饰键（如Shift、Option、Command）
                # 只允许Control，不允许其他修饰键
                other_flags = flags & ~Quartz.kCGEventFlagMaskControl
                
                # 忽略一些系统标志位（如Caps Lock）
                # 只检查真正重要的修饰键：Shift、Option、Command
                important_modifiers = (
                    Quartz.kCGEventFlagMaskShift |
                    Quartz.kCGEventFlagMaskAlternate |
                    Quartz.kCGEventFlagMaskCommand
                )
                has_other_important_modifiers = bool(other_flags & important_modifiers)
                
                if not has_other_important_modifiers:
                    # 触发回调（在主线程中执行）
                    try:
                        # 再次检查对象是否仍然有效
                        if hasattr(self, '_registered') and self._registered:
                        # 使用信号在主线程中执行回调
                        # 信号会自动在主线程中触发，即使从后台线程发出
                        self._hotkey_triggered.emit()
                    except Exception:
                        pass
            
            # 返回事件，让系统继续处理
            return event
        except Exception:
            return event
    
    def _run_event_tap(self):
        """在后台线程中运行事件监听循环"""
        try:
            # 创建运行循环
            self._run_loop = Quartz.CFRunLoopGetCurrent()
            
            # 将事件tap添加到运行循环
            if self._run_loop_source and self._run_loop:
                Quartz.CFRunLoopAddSource(
                    self._run_loop,
                    self._run_loop_source,
                    Quartz.kCFRunLoopDefaultMode
                )
            
            # 运行循环直到停止
            while not self._stop_event.is_set():
                # 检查对象是否仍然有效
                if not hasattr(self, '_registered') or not self._registered:
                    break
                
                try:
                Quartz.CFRunLoopRunInMode(
                    Quartz.kCFRunLoopDefaultMode,
                    0.1,  # 超时时间
                    False  # returnAfterSourceHandled
                )
                except Exception:
                    # 如果运行循环出错，退出
                    break
        except Exception:
            pass
    
    def _register_hotkey(self):
        """注册全局快捷键监听器（使用 CGEventTap，兼容 macOS 10.13+）"""
        try:
            # 创建事件掩码（兼容不同 macOS 版本）
            try:
                # 尝试使用 CGEventMaskBit（macOS 10.13+）
                event_mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            except (AttributeError, TypeError):
                # 如果 CGEventMaskBit 不可用，使用直接数值（兼容旧版本）
                # kCGEventKeyDown = 10, 事件掩码 = 1 << 10 = 0x400
                event_mask = 0x400
            
            # 创建事件tap
            # kCGSessionEventTap = 1 (监听会话级别的事件)
            # kCGHeadInsertEventTap = 0 (在事件队列头部插入)
            # kCGEventTapOptionDefault = 0
            
            self._event_tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,  # 监听会话级别事件
                Quartz.kCGHeadInsertEventTap,  # 在队列头部插入
                Quartz.kCGEventTapOptionDefault,  # 默认选项
                event_mask,  # 按键事件掩码
                self._event_tap_callback,  # 回调函数
                None  # 用户数据
            )
            
            if self._event_tap is None:
                # 检查是否是权限问题
                macos_version = _get_macos_version()
                if macos_version[0] >= 13:  # macOS 13+
                    error_msg = (
                        "无法创建事件监听器。\n\n"
                        "请在系统设置中授予辅助功能权限：\n"
                        "系统设置 > 隐私与安全性 > 辅助功能\n\n"
                        "找到此应用并勾选以允许使用辅助功能。"
                    )
                else:
                    error_msg = (
                        "无法创建事件监听器。\n\n"
                        "请在系统偏好设置中授予辅助功能权限：\n"
                        "系统偏好设置 > 安全性与隐私 > 隐私 > 辅助功能\n\n"
                        "找到此应用并勾选以允许使用辅助功能。"
                    )
                raise RuntimeError(error_msg)
            
            # 创建运行循环源
            self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
                None,  # allocator
                self._event_tap,
                0  # order
            )
            
            if self._run_loop_source is None:
                raise RuntimeError("Failed to create run loop source for CGEventTap")
            
            # 在后台线程中运行事件监听循环
            self._thread = threading.Thread(target=self._run_event_tap, daemon=True)
            self._thread.start()
            
            self._registered = True
            
        except Exception as e:
            raise RuntimeError(
                f"Failed to register global hotkey: {e}. "
                "Please grant Accessibility permissions in System Preferences > Security & Privacy > Privacy > Accessibility"
            )
    
    def unregister(self):
        """取消注册全局快捷键"""
        if self._registered:
            try:
                # 停止运行循环
                self._stop_event.set()
                
                # 移除运行循环源
                if self._run_loop_source and self._run_loop:
                    Quartz.CFRunLoopRemoveSource(
                        self._run_loop,
                        self._run_loop_source,
                        Quartz.kCFRunLoopDefaultMode
                    )
                
                # 释放事件tap
                if self._event_tap:
                    Quartz.CFRelease(self._event_tap)
                    self._event_tap = None
                
                # 释放运行循环源
                if self._run_loop_source:
                    Quartz.CFRelease(self._run_loop_source)
                    self._run_loop_source = None
                
                # 等待线程结束
                if self._thread and self._thread.is_alive():
                    self._thread.join(timeout=1.0)
                
                self._registered = False
            except Exception:
                pass
    
    def __del__(self):
        """析构函数，确保取消注册"""
        try:
            self.unregister()
        except:
            pass
