#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知服务
用户端：接收并显示系统通知（运行时和未运行时）
"""

import sys
import platform
from typing import Optional, List, Dict, Any
from datetime import datetime
from PySide6.QtCore import QObject, QTimer, Signal, QThreadPool, QRunnable
from utils.api_client import ApiClient, ApiError
from utils.notification import send_notification
from utils.config_manager import ConfigManager


class NotificationService(QObject):
    """通知服务：定期检查并显示新通知"""
    
    notification_received = Signal(dict)  # 收到新通知时发出信号
    notification_clicked = Signal(dict)  # 通知被点击时发出信号
    
    def __init__(self, api_client: Optional[ApiClient] = None):
        super().__init__()
        self.api_client = api_client
        self.config = ConfigManager.load()
        self._last_check_time: Optional[datetime] = None
        self._checked_notification_ids: set = set()
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._check_notifications)
        self._poll_interval = 60000  # 60秒检查一次
    
    def start_polling(self):
        """开始轮询检查通知"""
        if not self.config.get("notifications", True):
            return
        
        # 立即检查一次
        self._check_notifications()
        
        # 启动定时器
        self._poll_timer.start(self._poll_interval)
    
    def stop_polling(self):
        """停止轮询"""
        self._poll_timer.stop()
    
    def _check_notifications(self):
        """检查新通知（需要登录）"""
        # 首先检查是否已登录，未登录时不发起请求
        if not ApiClient.is_logged_in():
            return
        
        if not self.api_client:
            try:
                self.api_client = ApiClient.from_config()
            except Exception:
                return
        
        if not self.config.get("notifications", True):
            return
        
        # 在后台线程中检查
        worker = _NotificationCheckWorker(self.api_client, self._checked_notification_ids)
        worker.notification_found.connect(self._on_notification_received)
        QThreadPool.globalInstance().start(worker)
    
    def _on_notification_received(self, notification: Dict[str, Any]):
        """收到新通知"""
        notification_id = notification.get("id")
        
        # 检查是否已读（双重保险，虽然API已经过滤了，但这里再检查一次）
        if notification.get("is_read", False):
            return
        
        # 检查是否已经处理过（去重）
        if notification_id in self._checked_notification_ids:
            return
        
        self._checked_notification_ids.add(notification_id)
        
        # 发送系统通知（带点击回调）
        if self.config.get("notifications", True):
            from utils.notification import send_notification
            notification_id = notification.get("id")
            send_notification(
                title=notification.get("title", "系统通知"),
                message=notification.get("message", ""),
                subtitle=notification.get("subtitle"),
                notification_id=notification_id,
                click_callback=lambda: self.notification_clicked.emit(notification)
            )
        
        # 发出信号（供UI使用）
        self.notification_received.emit(notification)
    


class _NotificationCheckWorker(QRunnable):
    """后台检查通知的工作线程"""
    
    notification_found = Signal(dict)
    
    def __init__(self, api_client: ApiClient, checked_ids: set):
        super().__init__()
        self.api_client = api_client
        self.checked_ids = checked_ids
    
    def run(self):
        """执行检查"""
        # 检查 api_client 是否有效
        if not self.api_client:
            return
        
        # 再次检查登录状态（双重检查，确保不会在未登录时发起请求）
        if not ApiClient.is_logged_in():
            return
        
        try:
            response = self.api_client._get("/api/notifications", params={"unread_only": True, "limit": 10})
            
            if response.get("status") == "success":
                items = response.get("items", [])
                for item in items:
                    # 双重检查：确保只处理未读通知
                    if item.get("is_read", False):
                        continue
                    # 检查是否已经处理过（去重）
                    if item.get("id") not in self.checked_ids:
                        self.notification_found.emit(item)
        except Exception:
            # 静默失败，不干扰主程序
            pass


# 全局通知服务实例
_notification_service: Optional[NotificationService] = None


def get_notification_service(api_client: Optional[ApiClient] = None) -> NotificationService:
    """获取全局通知服务实例"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService(api_client)
    return _notification_service

