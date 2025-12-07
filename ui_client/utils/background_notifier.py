#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台通知服务
用于在应用未运行时也能发送通知（通过后台脚本或系统服务）
"""

import sys
import platform
import subprocess
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from utils.config_manager import CONFIG_PATH


class BackgroundNotifier:
    """后台通知服务，可以在应用未运行时发送通知"""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        初始化后台通知服务
        
        Args:
            config_path: 配置文件路径，用于读取通知设置
        """
        base_dir = CONFIG_PATH.parent
        self.config_path = config_path or base_dir / "notifications.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
    
    def send_notification(self, title: str, message: str, 
                         subtitle: Optional[str] = None,
                         action_url: Optional[str] = None) -> bool:
        """
        发送通知（如果应用未运行，会通过后台脚本发送）
        
        Args:
            title: 通知标题
            message: 通知内容
            subtitle: 副标题（仅 macOS）
            action_url: 点击通知后打开的 URL（用于唤醒应用）
        
        Returns:
            bool: 是否发送成功
        """
        # 检查应用是否在运行
        if self._is_app_running():
            # 应用正在运行，使用应用内通知
            from .notification import send_notification
            return send_notification(title, message, subtitle)
        else:
            # 应用未运行，使用后台脚本发送通知
            return self._send_background_notification(title, message, subtitle, action_url)
    
    def _is_app_running(self) -> bool:
        """检查应用是否正在运行"""
        system = platform.system()
        app_name = "Ai Perf Client" if system == "Darwin" else "Ai Perf Client.exe"
        
        try:
            if system == "Darwin":
                # macOS: 使用 pgrep 检查进程
                result = subprocess.run(
                    ["pgrep", "-f", app_name],
                    capture_output=True,
                    timeout=2
                )
                return result.returncode == 0
            elif system == "Windows":
                # Windows: 使用 tasklist 检查进程
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {app_name}"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                return app_name in result.stdout
            else:
                return False
        except Exception:
            return False
    
    def _send_background_notification(self, title: str, message: str,
                                      subtitle: Optional[str] = None,
                                      action_url: Optional[str] = None) -> bool:
        """通过后台脚本发送通知"""
        system = platform.system()
        
        try:
            if system == "Darwin":
                # macOS: 使用 osascript
                script = f'''
                display notification "{message}" with title "{title}"'''
                if subtitle:
                    script += f' subtitle "{subtitle}"'
                script += ' sound name "Glass"'
                
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return True
            elif system == "Windows":
                # Windows: 使用 PowerShell
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
                
                subprocess.Popen(
                    ["powershell", "-Command", ps_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return True
            else:
                return False
        except Exception as e:
            print(f"后台通知发送失败: {e}")
            return False
    
    def save_notification_queue(self, title: str, message: str,
                                subtitle: Optional[str] = None,
                                action_url: Optional[str] = None):
        """
        将通知保存到队列（当应用启动时显示）
        
        Args:
            title: 通知标题
            message: 通知内容
            subtitle: 副标题
            action_url: 操作 URL
        """
        try:
            # 读取现有队列
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    queue = json.load(f)
            else:
                queue = []
            
            # 添加新通知
            queue.append({
                "title": title,
                "message": message,
                "subtitle": subtitle,
                "action_url": action_url,
                "timestamp": datetime.now().isoformat()
            })
            
            # 只保留最近 50 条
            queue = queue[-50:]
            
            # 保存队列
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存通知队列失败: {e}")
    
    def load_notification_queue(self) -> list:
        """加载通知队列"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception:
            return []
    
    def clear_notification_queue(self):
        """清空通知队列"""
        try:
            if self.config_path.exists():
                self.config_path.unlink()
        except Exception:
            pass


# 独立脚本：用于在应用未运行时发送通知
def main():
    """命令行入口：用于后台脚本发送通知"""
    if len(sys.argv) < 3:
        print("用法: python background_notifier.py <title> <message> [subtitle]")
        sys.exit(1)
    
    title = sys.argv[1]
    message = sys.argv[2]
    subtitle = sys.argv[3] if len(sys.argv) > 3 else None
    
    notifier = BackgroundNotifier()
    success = notifier._send_background_notification(title, message, subtitle)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

