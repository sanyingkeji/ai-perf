#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台系统通知工具
使用系统原生 API（类似 iOS 的机制）
支持 macOS 和 Windows
"""

import sys
import platform
from typing import Optional


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
                    # macOS: 从应用包获取
                    exe_path = Path(sys.executable)
                    if exe_path.parts[-3:] == ('Contents', 'MacOS', 'Ai Perf Client'):
                        app_bundle = exe_path.parent.parent.parent
                        icon_path = app_bundle / "Contents" / "Resources" / "app_icon.icns"
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
                    project_root / "resources" / "app_icon.png",   # 通用
                ]
                for icon_path in icon_paths:
                    if icon_path.exists():
                        return str(icon_path)
        except Exception:
            pass
        return None
    
    @staticmethod
    def send(title: str, message: str, subtitle: Optional[str] = None, 
             sound: bool = True, timeout: int = 5, icon_path: Optional[str] = None) -> bool:
        """
        发送系统通知（使用系统原生 API）
        
        Args:
            title: 通知标题
            message: 通知内容
            subtitle: 副标题（仅 macOS）
            sound: 是否播放声音
            timeout: 通知显示时长（秒，仅 macOS，已废弃，由系统控制）
        
        Returns:
            bool: 是否发送成功
        """
        system = platform.system()
        
        if system == "Darwin":  # macOS
            return SystemNotification._send_macos_native(title, message, subtitle, sound, icon_path)
        elif system == "Windows":  # Windows
            return SystemNotification._send_windows_native(title, message, sound, icon_path)
        else:
            print(f"不支持的操作系统: {system}")
            return False
    
    @staticmethod
    def _send_macos_native(title: str, message: str, subtitle: Optional[str] = None,
                           sound: bool = True, icon_path: Optional[str] = None) -> bool:
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
                        from AppKit import NSImage
                        icon_image = NSImage.alloc().initWithContentsOfFile_(icon_path)
                        if icon_image:
                            notification.setContentImage_(icon_image)
                    except Exception:
                        # 图标设置失败，忽略（使用默认图标）
                        pass
                
                # 设置用户信息（用于点击回调）
                # 注意：NSUserNotification 不支持直接回调，需要通过通知中心代理处理
                # 这里先设置，后续可以通过通知中心代理处理点击事件
                
                # 发送通知（通过系统通知中心）
                center = NSUserNotificationCenter.defaultUserNotificationCenter()
                if center is None:
                    # macOS 10.14+ 可能返回 None（NSUserNotificationCenter 已被废弃）
                    # 回退到 osascript
                    return SystemNotification._send_macos_fallback(title, message, subtitle, sound, icon_path)
                
                center.deliverNotification_(notification)
                
                return True
            except ImportError:
                # 如果 PyObjC 不可用，回退到 osascript
                return SystemNotification._send_macos_fallback(title, message, subtitle, sound, icon_path)
            except Exception as e:
                print(f"macOS 原生通知 API 调用失败: {e}")
                # 回退到 osascript
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
        else:
            return True
    
    @staticmethod
    def open_system_settings():
        """
        打开系统通知设置页面（仅 macOS）
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
    
    # 如果没有提供图标路径，尝试使用应用默认图标
    if icon_path is None:
        icon_path = SystemNotification._get_default_app_icon()
    
    return SystemNotification.send(title, message, subtitle, sound, timeout, icon_path)


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

