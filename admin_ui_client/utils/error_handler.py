#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
错误处理工具：统一处理 API 错误，对于包含 detail 的错误用弹出框显示
"""

from PySide6.QtWidgets import QMessageBox, QWidget
from utils.api_client import ApiError, AuthError


def handle_api_error(parent: QWidget, error: Exception, default_title: str = "错误") -> None:
    """
    统一处理 API 错误：
    - 如果错误消息以 "HTTP_ERROR_DETAIL:" 开头，提取 detail 并用 QMessageBox 显示
    - 否则用 Toast 显示（需要从 widgets.toast 导入）
    """
    error_msg = str(error)
    
    # 检查是否是包含 detail 的 HTTP 错误
    if error_msg.startswith("HTTP_ERROR_DETAIL:"):
        detail = error_msg.replace("HTTP_ERROR_DETAIL:", "", 1).strip()
        QMessageBox.warning(parent, default_title, detail)
        return
    
    # 其他错误用 Toast 显示（如果可用）
    try:
        from widgets.toast import Toast
        Toast.show_message(parent, error_msg)
    except ImportError:
        # 如果 Toast 不可用，也用 QMessageBox 显示
        QMessageBox.warning(parent, default_title, error_msg)

