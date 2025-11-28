#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期选择器辅助工具：
- 为 QDateEdit 的日历弹出框适配深色模式
"""

from PySide6.QtWidgets import QDateEdit, QCalendarWidget
from PySide6.QtGui import QTextCharFormat, QBrush, QColor
from PySide6.QtCore import Qt

from utils.theme_manager import ThemeManager
from utils.config_manager import ConfigManager


def apply_theme_to_date_edit(date_edit: QDateEdit):
    """
    为 QDateEdit 的日历弹出框适配深色模式
    
    Args:
        date_edit: QDateEdit 实例
    """
    # 立即应用一次（如果日历已创建）
    _apply_theme_to_calendar(date_edit)
    
    # 使用 QTimer 延迟检查，因为日历可能在用户点击后才创建
    from PySide6.QtCore import QTimer
    
    def on_calendar_shown():
        _apply_theme_to_calendar(date_edit)
    
    # 创建一个定时器，在日历可能显示时检查并应用样式
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_calendar_shown)
    
    # 保存原始的 focusInEvent
    original_focus_in = date_edit.focusInEvent
    
    def new_focus_in(event):
        if original_focus_in:
            original_focus_in(event)
        # 延迟应用样式，确保日历已创建
        timer.start(100)
    
    date_edit.focusInEvent = new_focus_in


def _apply_theme_to_calendar(date_edit: QDateEdit):
    """内部函数：为日历组件应用主题样式"""
    # 检测当前主题
    is_dark = _detect_theme()
    
    # 获取日历组件（QDateEdit 内部使用 QCalendarWidget）
    # 注意：QDateEdit 的日历是通过 calendarWidget() 方法获取的
    calendar = date_edit.calendarWidget()
    if calendar is None:
        return
    
    # 设置星期标题样式
    weekdays = [Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday, Qt.Saturday, Qt.Sunday]
    
    if is_dark:
        # 深色模式：星期标题使用浅色文字
        header_format = QTextCharFormat()
        header_format.setForeground(QBrush(QColor("#ffffff")))  # 白色文字
        header_format.setBackground(QBrush(QColor("#2a2a2a")))  # 深灰色背景
        # 设置周末（周六、周日）为红色
        weekend_format = QTextCharFormat()
        weekend_format.setForeground(QBrush(QColor("#e57373")))  # 浅红色文字
        weekend_format.setBackground(QBrush(QColor("#2a2a2a")))  # 深灰色背景
        
        # 应用格式到所有星期标题
        for i, day in enumerate(weekdays):
            if i >= 5:  # 周六、周日（索引5、6）
                calendar.setWeekdayTextFormat(day, weekend_format)
            else:
                calendar.setWeekdayTextFormat(day, header_format)
    else:
        # 浅色模式：星期标题使用默认样式，周末为红色
        header_format = QTextCharFormat()
        header_format.setForeground(QBrush(QColor("#000000")))  # 黑色文字
        weekend_format = QTextCharFormat()
        weekend_format.setForeground(QBrush(QColor("#dc3545")))  # 红色文字
        
        # 应用格式到所有星期标题
        for i, day in enumerate(weekdays):
            if i >= 5:  # 周六、周日（索引5、6）
                calendar.setWeekdayTextFormat(day, weekend_format)
            else:
                calendar.setWeekdayTextFormat(day, header_format)


def _detect_theme() -> bool:
    """检测当前是否为深色模式"""
    try:
        cfg = ConfigManager.load()
        preference = cfg.get("theme", "auto")
        
        if preference == "auto":
            theme = ThemeManager.detect_system_theme()
        else:
            theme = preference  # "light" or "dark"
        
        return theme == "dark"
    except:
        return False

