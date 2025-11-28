#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一轮询服务
同时检查版本更新和通知
"""

from typing import Optional, Dict, Any
from datetime import datetime
from PySide6.QtCore import QObject, QTimer, Signal, QThreadPool, QRunnable
from utils.api_client import ApiClient, ApiError
from utils.config_manager import ConfigManager
from utils.notification import send_notification


class PollingService(QObject):
    """统一轮询服务：检查版本更新和通知"""
    
    # 信号
    notification_received = Signal(dict)  # 收到新通知
    notification_clicked = Signal(dict)  # 通知被点击
    version_update_available = Signal(dict)  # 有新版本
    
    def __init__(self, api_client: Optional[ApiClient] = None):
        super().__init__()
        self.api_client = api_client
        self.config = ConfigManager.load()
        
        # 通知相关
        self._checked_notification_ids: set = set()
        self._notification_poll_interval = 60000  # 60秒检查一次通知
        self._notification_timer = QTimer()
        self._notification_timer.timeout.connect(self._check_notifications)
        
        # 版本检查相关
        self._last_version_check_time: Optional[datetime] = None
        self._version_check_interval = 300000  # 5分钟检查一次版本
        self._version_timer = QTimer()
        self._version_timer.timeout.connect(self._check_version)
        self._last_version_info: Optional[dict] = None
    
    def start_polling(self):
        """开始轮询"""
        # 启动通知轮询
        if self.config.get("notifications", True):
            self._check_notifications()  # 立即检查一次
            self._notification_timer.start(self._notification_poll_interval)
        
        # 启动版本检查（延迟启动，避免启动时重复检查）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(60000, self._check_version)  # 1分钟后首次检查
        self._version_timer.start(self._version_check_interval)
    
    def stop_polling(self):
        """停止轮询"""
        self._notification_timer.stop()
        self._version_timer.stop()
    
    def _check_notifications(self):
        """检查新通知"""
        if not self.api_client:
            try:
                self.api_client = ApiClient.from_config()
            except Exception:
                return
        
        if not self.config.get("notifications", True):
            return
        
        # 在后台线程中检查
        worker = _NotificationCheckWorker(self.api_client, self._checked_notification_ids)
        worker.signals.notification_found.connect(self._on_notification_received)
        QThreadPool.globalInstance().start(worker)
    
    def _check_version(self):
        """检查版本更新"""
        try:
            cfg = ConfigManager.load()
            client_version = cfg.get("client_version", "1.0.0")
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8000").strip()
        except Exception:
            return
        
        # 在后台线程中检查
        worker = _VersionCheckWorker(client_version, api_base)
        worker.signals.version_found.connect(self._on_version_update_available)
        QThreadPool.globalInstance().start(worker)
    
    def _on_notification_received(self, notification: Dict[str, Any]):
        """收到新通知"""
        notification_id = notification.get("id")
        
        # 检查是否已读
        if notification.get("is_read", False):
            return
        
        # 检查是否已经处理过（去重）
        if notification_id in self._checked_notification_ids:
            return
        
        self._checked_notification_ids.add(notification_id)
        
        # 发送系统通知
        if self.config.get("notifications", True):
            send_notification(
                title=notification.get("title", "系统通知"),
                message=notification.get("message", ""),
                subtitle=notification.get("subtitle"),
                notification_id=notification_id,
                click_callback=lambda: self.notification_clicked.emit(notification)
            )
        
        # 发出信号
        self.notification_received.emit(notification)
    
    def _on_version_update_available(self, version_info: dict):
        """检测到新版本"""
        # 检查版本是否有变化
        new_version = version_info.get("version", "")
        if self._last_version_info and self._last_version_info.get("version") == new_version:
            return  # 版本没变化，不重复通知
        
        self._last_version_info = version_info
        self.version_update_available.emit(version_info)


class _NotificationCheckWorkerSignals(QObject):
    """通知检查工作线程的信号"""
    notification_found = Signal(dict)


class _NotificationCheckWorker(QRunnable):
    """后台检查通知的工作线程"""
    
    def __init__(self, api_client: ApiClient, checked_ids: set):
        super().__init__()
        self.api_client = api_client
        self.checked_ids = checked_ids
        self.signals = _NotificationCheckWorkerSignals()
    
    def run(self):
        """执行检查"""
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
                        self.signals.notification_found.emit(item)
        except Exception:
            # 静默失败，不干扰主程序
            pass


class _VersionCheckWorkerSignals(QObject):
    """版本检查工作线程的信号"""
    version_found = Signal(dict)


class _VersionCheckWorker(QRunnable):
    """后台检查版本的工作线程"""
    
    def __init__(self, current_version: str, api_base: str):
        super().__init__()
        self._current_version = current_version
        self._api_base = api_base.rstrip("/")
        self.signals = _VersionCheckWorkerSignals()
    
    def run(self):
        """执行检查"""
        try:
            import httpx
            url = f"{self._api_base}/api/health"
            params = {"current_version": self._current_version} if self._current_version else None
            r = httpx.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    health_data = data.get("data")
                    if health_data:
                        version_info = health_data.get("version_info")
                        if version_info:
                            self.signals.version_found.emit(version_info)
        except Exception:
            # 静默失败，不干扰主程序
            pass


# 全局轮询服务实例
_polling_service: Optional[PollingService] = None


def get_polling_service(api_client: Optional[ApiClient] = None) -> PollingService:
    """获取全局轮询服务实例"""
    global _polling_service
    if _polling_service is None:
        _polling_service = PollingService(api_client)
    return _polling_service


