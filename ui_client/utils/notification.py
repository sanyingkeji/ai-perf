#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台系统通知工具
使用系统原生 API（类似 iOS 的机制）
支持 macOS、Windows 和 Linux
"""

import sys
import platform
from typing import Optional
from pathlib import Path


class SystemNotification:
    """系统通知类，使用系统原生 API"""
    
    @staticmethod
    def _get_default_app_icon() -> Optional[str]:
        """获取应用默认图标路径"""
        try:
            # 尝试从应用资源目录获取图标
            if hasattr(sys, 'frozen') and sys.frozen:
                # 打包后的应用
                if platform.system() == "Darwin":
                    # macOS: 从应用包获取（更健壮：查找上层的 Contents/Resources）
                    exe_path = Path(sys.executable)
                    contents_root = None
                    for parent in exe_path.parents:
                        if parent.name == "Contents":
                            contents_root = parent
                            break
                    if contents_root:
                        icon_path = contents_root / "Resources" / "app_icon.icns"
                        if icon_path.exists():
                            return str(icon_path)
                elif platform.system() == "Windows":
                    # Windows: 从应用目录获取
                    exe_dir = Path(sys.executable).parent
                    icon_path = exe_dir / "app_icon.ico"
                    if icon_path.exists():
                        return str(icon_path)
            else:
                # 开发环境
                project_root = Path(__file__).resolve().parents[1]
                icon_paths = [
                    project_root / "resources" / "app_icon.icns",  # macOS
                    project_root / "resources" / "app_icon.ico",   # Windows
                    project_root / "resources" / "app_icon.png",   # Linux/通用
                ]
                for icon_path in icon_paths:
                    if icon_path.exists():
                        return str(icon_path)
        except Exception:
            pass
        return None
    
    @staticmethod
    def send(title: str, message: str, subtitle: Optional[str] = None, 
             sound: bool = True, timeout: int = 5, icon_path: Optional[str] = None,
             notification_id: Optional[int] = None) -> bool:
        """
        发送系统通知（使用系统原生 API）
        
        Args:
            title: 通知标题
            message: 通知内容
            subtitle: 副标题（仅 macOS）
            sound: 是否播放声音
            timeout: 通知显示时长（秒，仅 macOS，已废弃，由系统控制）
            notification_id: 通知ID（用于点击回调，仅 macOS）
        
        Returns:
            bool: 是否发送成功
        """
        system = platform.system()
        
        if system == "Darwin":  # macOS
            return SystemNotification._send_macos_native(title, message, subtitle, sound, icon_path, notification_id)
        elif system == "Windows":  # Windows
            return SystemNotification._send_windows_native(title, message, sound, icon_path)
        elif system == "Linux":  # Linux
            return SystemNotification._send_linux_native(title, message, sound, icon_path)
        else:
            print(f"不支持的操作系统: {system}")
            return False
    
    @staticmethod
    def _send_macos_native(title: str, message: str, subtitle: Optional[str] = None,
                           sound: bool = True, icon_path: Optional[str] = None,
                           notification_id: Optional[int] = None) -> bool:
        """
        macOS 系统通知（使用 NSUserNotificationCenter / UserNotifications framework）
        类似 iOS 的 UNUserNotificationCenter
        """
        try:
            # 方法1: 尝试使用 UserNotifications framework (macOS 10.14+)
            try:
                from AppKit import NSUserNotification, NSUserNotificationCenter
                
                # 创建通知对象
                notification = NSUserNotification.alloc().init()
                notification.setTitle_(title)
                notification.setInformativeText_(message)
                
                if subtitle:
                    notification.setSubtitle_(subtitle)
                
                if sound:
                    notification.setSoundName_("NSUserNotificationDefaultSoundName")
                
                # 设置图标（如果提供）
                if icon_path:
                    try:
                        from AppKit import NSImage, NSApplication
                        icon_image = NSImage.alloc().initWithContentsOfFile_(icon_path)
                        if icon_image:
                            notification.setContentImage_(icon_image)
                            # 开发模式下没有 bundle，会用 Python 默认图标。直接设置进程图标，确保通知图标一致。
                            try:
                                app = NSApplication.sharedApplication()
                                app.setApplicationIconImage_(icon_image)
                            except Exception:
                                pass
                    except Exception:
                        # 图标设置失败，忽略（使用默认图标）
                        pass
                
                # 设置用户信息（用于点击回调）
                # 将 notification_id 存储到通知的用户信息中，以便点击时识别
                if notification_id is not None:
                    from Foundation import NSDictionary
                    user_info = NSDictionary.dictionaryWithDictionary_({
                        "notification_id": notification_id
                    })
                    notification.setUserInfo_(user_info)
                
                # 设置通知中心代理（如果还没有设置）
                center = NSUserNotificationCenter.defaultUserNotificationCenter()
                if center is None:
                    # macOS 10.14+ 可能返回 None（NSUserNotificationCenter 已被废弃）
                    # 在开发模式下，尝试使用 UserNotifications framework（如果可用）
                    # 如果不可用，回退到 osascript（osascript 不支持点击回调）
                    print("[WARNING] NSUserNotificationCenter 不可用，尝试使用 UserNotifications framework")
                    try:
                        # 尝试使用 UserNotifications framework (macOS 10.14+)
                        from UserNotifications import UNUserNotificationCenter, UNMutableNotificationContent, UNNotificationRequest, UNTimeIntervalNotificationTrigger
                        # 注意：UserNotifications framework 需要应用有正确的 bundle ID
                        # 在开发模式下可能不可用，所以这里只是尝试
                        print("[INFO] UserNotifications framework 可用，但需要应用 bundle ID，开发模式下可能不可用")
                    except ImportError:
                        pass
                    
                    # 如果有点击回调，记录警告
                    if notification_id is not None and hasattr(send_notification, '_callbacks') and send_notification._callbacks.get(notification_id):
                        print(f"[WARNING] 使用 osascript 回退方案，点击回调将无法触发（notification_id={notification_id}）")
                        print("[INFO] 提示：在开发模式下，建议安装 PyObjC 以确保通知点击回调正常工作")
                    
                    return SystemNotification._send_macos_fallback(title, message, subtitle, sound, icon_path)
                
                # 确保设置了 delegate 来处理点击事件
                if not hasattr(SystemNotification, '_delegate_set'):
                    try:
                        from AppKit import NSObject
                        from Foundation import NSObject as FoundationNSObject
                        
                        class NotificationDelegate(FoundationNSObject):
                            """通知代理类，处理通知点击事件"""
                            
                            def userNotificationCenter_didActivateNotification_(self, center, notification):
                                """通知被点击时调用"""
                                try:
                                    print("[DEBUG] 通知被点击，开始处理回调")
                                    user_info = notification.userInfo()
                                    if user_info:
                                        notification_id = user_info.get("notification_id")
                                        print(f"[DEBUG] 通知ID: {notification_id}")
                                        if notification_id is not None:
                                            # 从全局字典中获取回调并执行
                                            if hasattr(send_notification, '_callbacks'):
                                                callback = send_notification._callbacks.get(notification_id)
                                                print(f"[DEBUG] 回调函数: {callback}")
                                                if callback:
                                                    # 在主线程中执行回调
                                                    from PySide6.QtCore import QTimer
                                                    print("[DEBUG] 在主线程中执行回调")
                                                    QTimer.singleShot(0, callback)
                                                else:
                                                    print(f"[WARNING] 未找到通知ID {notification_id} 对应的回调函数")
                                            else:
                                                print("[WARNING] send_notification._callbacks 不存在")
                                    else:
                                        print("[WARNING] 通知没有 userInfo")
                                except Exception as e:
                                    print(f"[WARNING] 处理通知点击失败: {e}")
                                    import traceback
                                    traceback.print_exc()
                            
                            def userNotificationCenter_shouldPresentNotification_(self, center, notification):
                                """是否应该显示通知（返回 True 表示显示）"""
                                return True
                        
                        delegate = NotificationDelegate.alloc().init()
                        center.setDelegate_(delegate)
                        SystemNotification._delegate_set = True
                        print("[DEBUG] 通知代理已设置")
                    except Exception as e:
                        print(f"[WARNING] 设置通知代理失败: {e}")
                        import traceback
                        traceback.print_exc()
                
                center.deliverNotification_(notification)
                
                return True
            except ImportError:
                # 如果 PyObjC 不可用，回退到 osascript（osascript 不支持点击回调）
                return SystemNotification._send_macos_fallback(title, message, subtitle, sound, icon_path)
            except Exception as e:
                print(f"macOS 原生通知 API 调用失败: {e}")
                # 回退到 osascript（osascript 不支持点击回调）
                return SystemNotification._send_macos_fallback(title, message, subtitle, sound, icon_path)
        except Exception as e:
            print(f"macOS 通知发送失败: {e}")
            return False
    
    @staticmethod
    def _send_macos_fallback(title: str, message: str, subtitle: Optional[str] = None,
                            sound: bool = True, icon_path: Optional[str] = None) -> bool:
        """macOS 回退方案：使用 osascript（当 PyObjC 不可用时）"""
        import subprocess
        try:
            script = f'display notification "{message}" with title "{title}"'
            if subtitle:
                script += f' subtitle "{subtitle}"'
            if sound:
                script += ' sound name "Glass"'
            
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            print(f"macOS 回退方案失败: {e}")
            return False
    
    @staticmethod
    def _send_windows_native(title: str, message: str, sound: bool = True, icon_path: Optional[str] = None) -> bool:
        """
        Windows 系统通知（使用 Windows.UI.Notifications API）
        类似 iOS 的 UNUserNotificationCenter
        """
        try:
            # 方法1: 尝试使用 winrt (Windows 10+)
            try:
                import winrt.windows.ui.notifications as notifications
                import winrt.windows.data.xml.dom as dom
                
                # 创建 Toast 通知 XML
                # 如果有图标，添加到 XML 中
                icon_xml = ""
                if icon_path:
                    # Windows Toast 通知需要图标的绝对路径或应用资源路径
                    # 如果是相对路径，转换为绝对路径
                    import os
                    if not os.path.isabs(icon_path):
                        icon_path = os.path.abspath(icon_path)
                    # 转义 XML 特殊字符
                    icon_path_escaped = icon_path.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                    icon_xml = f'<image id="1" src="{icon_path_escaped}" placement="appLogoOverride" hint-crop="circle"/>'
                
                toast_xml = f'''<?xml version="1.0"?>
<toast>
    <visual>
        <binding template="ToastGeneric">
            {icon_xml}
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>'''
                
                # 解析 XML
                xml_doc = dom.XmlDocument()
                xml_doc.load_xml(toast_xml)
                
                # 创建 Toast 通知
                toast = notifications.ToastNotification(xml_doc)
                
                # 获取通知管理器并显示
                notifier = notifications.ToastNotificationManager.create_toast_notifier("Ai Perf Client")
                notifier.show(toast)
                
                return True
            except ImportError:
                # 如果 winrt 不可用，尝试 win10toast
                return SystemNotification._send_windows_fallback(title, message, sound, icon_path)
            except Exception as e:
                print(f"Windows 原生通知 API 调用失败: {e}")
                return SystemNotification._send_windows_fallback(title, message, sound, icon_path)
        except Exception as e:
            print(f"Windows 通知发送失败: {e}")
            return False
    
    @staticmethod
    def _send_windows_fallback(title: str, message: str, sound: bool = True, icon_path: Optional[str] = None) -> bool:
        """Windows 回退方案：使用 win10toast 或 PowerShell"""
        import subprocess
        try:
            # 方法1: 尝试使用 win10toast（如果已安装）
            try:
                from win10toast import ToastNotifier
                toaster = ToastNotifier()
                duration = 5 if sound else 0
                toaster.show_toast(title, message, duration=duration)
                return True
            except ImportError:
                pass
            
            # 方法2: 使用 PowerShell 调用 Windows API
            ps_script = f'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title}</text>
            <text id="2">{message}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Ai Perf Client")
$notifier.Show($toast)
'''
            
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                timeout=10
            )
            
            return result.returncode == 0
        except Exception as e:
            print(f"Windows 回退方案失败: {e}")
            return False
    
    @staticmethod
    def _send_linux_native(title: str, message: str, sound: bool = True, icon_path: Optional[str] = None) -> bool:
        """
        Linux 系统通知（使用 notify-send 命令，基于 D-Bus）
        """
        import subprocess
        try:
            # 方法1: 尝试使用 notify-send（大多数 Linux 发行版都支持）
            try:
                cmd = ["notify-send", title, message]
                
                # 添加图标（如果提供）
                if icon_path:
                    import os
                    if not os.path.isabs(icon_path):
                        icon_path = os.path.abspath(icon_path)
                    if os.path.exists(icon_path):
                        cmd.extend(["--icon", icon_path])
                
                # 添加超时时间（毫秒，默认 5 秒）
                cmd.extend(["--expire-time", "5000"])
                
                # 添加应用名称
                cmd.extend(["--app-name", "Ai Perf Client"])
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.returncode == 0
            except FileNotFoundError:
                # notify-send 不可用，尝试使用 Python 的 plyer 库
                return SystemNotification._send_linux_fallback(title, message, sound, icon_path)
            except Exception as e:
                print(f"Linux notify-send 调用失败: {e}")
                return SystemNotification._send_linux_fallback(title, message, sound, icon_path)
        except Exception as e:
            print(f"Linux 通知发送失败: {e}")
            return False
    
    @staticmethod
    def _send_linux_fallback(title: str, message: str, sound: bool = True, icon_path: Optional[str] = None) -> bool:
        """Linux 回退方案：使用 plyer 库或 dbus-python"""
        try:
            # 方法1: 尝试使用 plyer（跨平台通知库）
            try:
                from plyer import notification
                notification.notify(
                    title=title,
                    message=message,
                    app_name="Ai Perf Client",
                    timeout=5,
                    app_icon=icon_path if icon_path else None
                )
                return True
            except ImportError:
                pass
            
            # 方法2: 尝试使用 dbus-python（需要安装 python3-dbus）
            try:
                import dbus
                
                # 获取 D-Bus 会话总线
                bus = dbus.SessionBus()
                notify_obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
                notify_iface = dbus.Interface(notify_obj, 'org.freedesktop.Notifications')
                
                # 准备参数
                app_name = "Ai Perf Client"
                replaces_id = 0
                app_icon = icon_path if icon_path else ""
                summary = title
                body = message
                actions = []
                hints = {}
                expire_timeout = 5000  # 5秒
                
                # 调用 Notify 方法
                notify_iface.Notify(
                    app_name,
                    replaces_id,
                    app_icon,
                    summary,
                    body,
                    actions,
                    hints,
                    expire_timeout
                )
                return True
            except ImportError:
                pass
            except Exception as e:
                print(f"Linux dbus-python 调用失败: {e}")
            
            # 如果所有方法都失败，返回 False
            print("Linux 通知发送失败：未找到可用的通知方法（需要 notify-send、plyer 或 dbus-python）")
            return False
        except Exception as e:
            print(f"Linux 回退方案失败: {e}")
            return False
    
    @staticmethod
    def check_permission():
        """
        检查通知权限（使用系统原生 API）
        
        Returns:
            bool 或 None: True=已授权, False=未授权, None=无法确定
        """
        system = platform.system()
        
        if system == "Darwin":  # macOS
            try:
                # macOS 10.14+ 使用 UserNotifications framework 检查权限
                # 尝试使用 PyObjC 的 NSUserNotificationCenter 检查
                try:
                    from AppKit import NSUserNotificationCenter
                    center = NSUserNotificationCenter.defaultUserNotificationCenter()
                    if center is None:
                        # macOS 10.14+ NSUserNotificationCenter 可能返回 None
                        # 无法准确检查权限，返回 None 表示无法确定
                        return None
                    
                    # 注意：macOS 10.14+ 需要用户授权，但 NSUserNotificationCenter 无法直接检查授权状态
                    # 这里只是检查通知中心是否可用，无法准确判断权限状态
                    # 返回 True 表示通知中心可用，但不代表一定有权限
                    return True
                except ImportError:
                    # PyObjC 不可用，无法准确检查权限
                    # 返回 None 表示无法确定，让用户主动测试
                    return None  # 返回 None 表示无法确定
            except Exception:
                return None  # 返回 None 表示无法确定
        elif system == "Windows":
            # Windows 10+ 不需要显式权限检查
            return True
        elif system == "Linux":
            # Linux: 检查是否有 notify-send 或 D-Bus
            try:
                import subprocess
                # 检查 notify-send 是否可用
                result = subprocess.run(
                    ["which", "notify-send"],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    return True
                
                # 检查 dbus-python 是否可用
                try:
                    import dbus
                    return True
                except ImportError:
                    pass
                
                # 检查 plyer 是否可用
                try:
                    from plyer import notification
                    return True
                except ImportError:
                    pass
                
                return False
            except Exception:
                return False
        else:
            return True
    
    @staticmethod
    def open_system_settings():
        """
        打开系统通知设置页面
        """
        system = platform.system()
        if system == "Darwin":
            import subprocess
            try:
                # 打开 macOS 系统偏好设置的通知页面
                subprocess.Popen([
                    "open",
                    "x-apple.systempreferences:com.apple.preference.notifications"
                ])
                return True
            except Exception as e:
                print(f"无法打开系统设置: {e}")
                return False
        elif system == "Windows":
            # Windows 10+ 打开通知设置
            import subprocess
            try:
                subprocess.Popen([
                    "ms-settings:notifications"
                ])
                return True
            except Exception:
                # 回退方案：打开 Windows 设置
                try:
                    subprocess.Popen([
                        "start",
                        "ms-settings:notifications"
                    ], shell=True)
                    return True
                except Exception:
                    return False
        elif system == "Linux":
            # Linux: 打开系统通知设置（不同发行版命令不同）
            import subprocess
            try:
                # 尝试常见的设置命令
                commands = [
                    ["gnome-control-center", "notifications"],  # GNOME
                    ["kde5-settings", "notifications"],        # KDE
                    ["xfce4-settings-manager"],                 # XFCE
                ]
                for cmd in commands:
                    try:
                        subprocess.Popen(cmd)
                        return True
                    except FileNotFoundError:
                        continue
                return False
            except Exception:
                return False
        return False
    
    @staticmethod
    def request_permission() -> bool:
        """
        请求通知权限（使用系统原生 API）
        类似 iOS 的 UNUserNotificationCenter.requestAuthorization
        
        Returns:
            bool: 是否获得权限
        """
        system = platform.system()
        
        if system == "Darwin":  # macOS
            # macOS 会在首次发送通知时自动请求权限
            # 这里只是发送一个测试通知来触发权限请求
            try:
                return SystemNotification.send(
                    title="通知权限",
                    message="正在请求通知权限",
                    subtitle="请在系统设置中允许通知"
                )
            except Exception:
                return False
        elif system == "Windows":
            # Windows 10+ 不需要显式请求权限
            return True
        elif system == "Linux":
            # Linux: 通常不需要显式请求权限，直接发送测试通知
            try:
                return SystemNotification.send(
                    title="通知权限",
                    message="正在测试通知功能",
                    subtitle=None
                )
            except Exception:
                return False
        else:
            return False


def send_notification(title: str, message: str, subtitle: Optional[str] = None,
                     sound: bool = True, timeout: int = 5,
                     notification_id: Optional[int] = None,
                     click_callback: Optional[callable] = None,
                     icon_path: Optional[str] = None) -> bool:
    """
    便捷函数：发送系统通知
    
    Args:
        title: 通知标题
        message: 通知内容
        subtitle: 副标题（仅 macOS）
        sound: 是否播放声音
        timeout: 通知显示时长（秒，仅 macOS）
        notification_id: 通知ID（用于点击回调）
        click_callback: 点击回调函数（应用运行时使用）
        icon_path: 图标路径（可选，如果不提供则使用应用默认图标）
                   支持格式：.png, .jpg, .icns (macOS), .ico (Windows)
    
    Returns:
        bool: 是否发送成功
    """
    # 如果有点击回调，保存到全局字典中（供通知点击时调用）
    if click_callback and notification_id:
        if not hasattr(send_notification, '_callbacks'):
            send_notification._callbacks = {}
        send_notification._callbacks[notification_id] = click_callback
        print(f"[DEBUG] 保存通知点击回调: notification_id={notification_id}, callback={click_callback}")
    elif click_callback:
        print(f"[WARNING] 有点击回调但 notification_id 为 None，回调将无法触发")
    elif notification_id:
        print(f"[DEBUG] 通知ID={notification_id}，但没有点击回调")
    
    # 如果没有提供图标路径，尝试使用应用默认图标
    if icon_path is None:
        icon_path = SystemNotification._get_default_app_icon()
    
    return SystemNotification.send(title, message, subtitle, sound, timeout, icon_path, notification_id)


def get_notification_callback(notification_id: int) -> Optional[callable]:
    """获取通知点击回调函数"""
    if hasattr(send_notification, '_callbacks'):
        return send_notification._callbacks.get(notification_id)
    return None


# 测试代码
if __name__ == "__main__":
    # 测试通知
    success = send_notification(
        title="测试通知",
        message="这是一条测试通知消息",
        subtitle="来自 Ai Perf Client"
    )
    print(f"通知发送{'成功' if success else '失败'}")

