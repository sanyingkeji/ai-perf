#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知列表加载工作线程
"""

from PySide6.QtCore import QRunnable, QObject, Signal, Slot
from utils.api_client import AdminApiClient, ApiError, AuthError


class _NotificationListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _NotificationListWorker(QRunnable):
    """后台加载通知列表数据"""
    def __init__(self, api_client: AdminApiClient, limit: int = 30, offset: int = 0):
        super().__init__()
        self.api_client = api_client
        self.limit = limit
        self.offset = offset
        self.signals = _NotificationListWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            response = self.api_client._get("/admin/api/notifications", params={"limit": self.limit, "offset": self.offset})
            if response.get("status") == "success":
                items = response.get("items", [])
                self.signals.finished.emit(items)
            else:
                self.signals.error.emit(response.get("message", "未知错误"))
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载通知列表失败：{e}")

