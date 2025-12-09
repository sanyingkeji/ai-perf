#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台通知服务
用于在应用未运行时检查并发送通知
"""

import sys
import platform
import time
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from ui_client.utils.config_manager import CONFIG_PATH as UI_CONFIG_PATH
except Exception:
    UI_CONFIG_PATH = None

# 添加项目路径
# 判断是否在打包后的应用中
is_frozen = hasattr(sys, 'frozen') and sys.frozen

if is_frozen:
    # 打包后的应用：从脚本路径推导项目根目录
    script_dir = Path(__file__).resolve().parent
    # 尝试找到 ui_client 目录
    if (script_dir.parent / "ui_client").exists():
        project_root = script_dir.parent
        sys.path.insert(0, str(project_root))
        sys.path.insert(0, str(project_root / "ui_client"))
    else:
        # 如果找不到，尝试从可执行文件路径推导
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir.parent / "ui_client").exists():
            project_root = exe_dir.parent
            sys.path.insert(0, str(project_root))
            sys.path.insert(0, str(project_root / "ui_client"))
        else:
            # 最后尝试：假设脚本在 Resources/scripts 下
            project_root = script_dir.parent.parent.parent.parent  # scripts -> Resources -> Contents -> app
            sys.path.insert(0, str(project_root))
            sys.path.insert(0, str(project_root / "ui_client"))
else:
    # 开发环境
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "ui_client"))


def load_config() -> Optional[Dict[str, Any]]:
    """加载配置"""
    try:
        # 判断是否在打包后的应用中
        is_frozen = hasattr(sys, 'frozen') and sys.frozen
        system = platform.system()
        
        # 尝试从多个位置加载配置
        config_paths = []
        
        if is_frozen:
            # 打包后的应用
            if system == "Darwin":
                # macOS: 应用包内或用户配置目录
                if sys.executable.endswith('.app/Contents/MacOS/Ai Perf Client'):
                    app_bundle = Path(sys.executable).parent.parent.parent
                    config_paths.append(app_bundle / "Contents" / "Resources" / "config.json")
                config_paths.append(Path("/Applications/Ai Perf Client.app/Contents/Resources/config.json"))
            elif system == "Windows":
                # Windows: 应用目录或用户配置目录
                exe_dir = Path(sys.executable).parent
                config_paths.append(exe_dir / "config.json")
                config_paths.append(Path.home() / "AppData" / "Local" / "Ai Perf Client" / "config.json")
        else:
            # 开发环境
            config_paths.append(project_root / "ui_client" / "config.json")
        
        # 用户配置目录（优先级最高）
        if UI_CONFIG_PATH:
            config_paths.append(UI_CONFIG_PATH)
        else:
            config_paths.append(Path.home() / ".ai_perf_client" / "config.json")
        
        for config_path in config_paths:
            if config_path and config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
    except Exception:
        pass
    return None


def send_system_notification(title: str, message: str, subtitle: Optional[str] = None, notification_id: Optional[int] = None):
    """发送系统通知（跨平台）"""
    import subprocess
    system = platform.system()
    
    try:
        if system == "Darwin":
            # macOS: 使用 osascript
            # 转义特殊字符
            title_escaped = title.replace('"', '\\"').replace('\\', '\\\\')
            message_escaped = message.replace('"', '\\"').replace('\\', '\\\\')
            script = f'display notification "{message_escaped}" with title "{title_escaped}"'
            if subtitle:
                subtitle_escaped = subtitle.replace('"', '\\"').replace('\\', '\\\\')
                script += f' subtitle "{subtitle_escaped}"'
            script += ' sound name "Glass"'
            
            # 如果指定了通知ID，添加打开应用的命令
            if notification_id:
                # 使用 open 命令打开应用，并传递通知ID参数
                app_path = "/Applications/Ai Perf Client.app"
                subprocess.Popen(
                    ["open", "-a", app_path, "--args", f"--notification-id={notification_id}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
        elif system == "Windows":
            # Windows: 使用 plyer 或 win10toast 或 PowerShell
            notification_sent = False
            
            # 首先尝试使用 plyer（跨平台库）
            try:
                from plyer import notification as plyer_notification
                plyer_notification.notify(
                    title=title,
                    message=message,
                    app_name="Ai Perf Client",
                    timeout=10
                )
                notification_sent = True
            except ImportError:
                pass
            except Exception:
                pass
            
            # 如果 plyer 失败，尝试 win10toast
            if not notification_sent:
                try:
                    from win10toast import ToastNotifier
                    toaster = ToastNotifier()
                    toaster.show_toast(
                        title=title,
                        msg=message,
                        duration=10,
                        threaded=True
                    )
                    notification_sent = True
                except ImportError:
                    pass
                except Exception:
                    pass
            
            # 如果都失败，使用 PowerShell
            if not notification_sent:
                # 转义特殊字符
                title_escaped = title.replace("'", "''").replace('"', '`"')
                message_escaped = message.replace("'", "''").replace('"', '`"')
                
                # 使用 PowerShell 脚本
                ps_script = f'''
                Add-Type -AssemblyName System.Windows.Forms
                $balloon = New-Object System.Windows.Forms.NotifyIcon
                $balloon.Icon = [System.Drawing.SystemIcons]::Information
                $balloon.BalloonTipTitle = "{title_escaped}"
                $balloon.BalloonTipText = "{message_escaped}"
                $balloon.Visible = $true
                $balloon.ShowBalloonTip(10000)
                Start-Sleep -Seconds 1
                $balloon.Dispose()
                '''
                
                subprocess.run(
                    ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                    
    except Exception:
        pass


def check_and_send_notifications():
    """检查并发送通知"""
    config = load_config()
    if not config:
        return
    
    # 检查是否启用了通知
    if not config.get("notifications", True):
        return
    
    # 检查是否已登录
    session_token = config.get("session_token", "").strip()
    if not session_token:
        return
    
    try:
        # 创建 API 客户端
        api_base = config.get("api_base", "").strip()
        if not api_base:
            return
        # 容错：修复误填的协议前缀（例如 "ttps://..."）
        if api_base.startswith("ttps://"):
            api_base = f"h{api_base}"
        if api_base.startswith("://"):
            api_base = f"http{api_base}"
        if not api_base.startswith(("http://", "https://")):
            # 无法识别的协议，直接跳过，避免 httpx 抛 UnsupportedProtocol
            return
        
        # 动态导入，确保路径正确
        try:
            from utils.api_client import ApiClient, ApiError
        except ImportError:
            # 如果导入失败，尝试添加路径
            import sys
            # 尝试从脚本位置推导 ui_client 路径
            script_dir = Path(__file__).resolve().parent
            possible_ui_client_paths = [
                script_dir.parent / "ui_client",  # scripts -> project -> ui_client
                script_dir.parent.parent / "ui_client",  # scripts -> Resources -> Contents -> app -> ui_client
                Path(sys.executable).parent.parent / "ui_client" if hasattr(sys, 'frozen') and sys.frozen else None,
            ]
            for ui_client_path in possible_ui_client_paths:
                if ui_client_path and ui_client_path.exists():
                    sys.path.insert(0, str(ui_client_path.parent))
                    sys.path.insert(0, str(ui_client_path))
                    break
            from utils.api_client import ApiClient, ApiError
        
        api_client = ApiClient(api_base, session_token)
        
        # 获取未读通知
        response = api_client._get("/api/notifications", params={"unread_only": True, "limit": 10})
        
        if response.get("status") == "success":
            items = response.get("items", [])
            
            for item in items:
                notification_id = item.get("id")
                title = item.get("title", "系统通知")
                message = item.get("message", "")
                
                # 发送系统通知（带通知ID，用于点击后打开应用）
                send_system_notification(
                    title=title,
                    message=message,
                    subtitle=item.get("subtitle"),
                    notification_id=notification_id
                )
                
                # 标记为已读
                try:
                    api_client._post(f"/api/notifications/{notification_id}/read", {})
                except Exception:
                    pass
            
    except Exception:
        pass


def main():
    """主函数"""
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--once":
            # 单次执行模式
            check_and_send_notifications()
        else:
            # 持续运行模式（每60秒检查一次）
            while True:
                check_and_send_notifications()
                time.sleep(60)
    except Exception:
        pass


if __name__ == "__main__":
    main()
