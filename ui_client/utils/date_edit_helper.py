#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期选择器辅助工具：
- 为 QDateEdit 的日历弹出框适配深色模式
- 为 QDateEdit 和 QComboBox 本身适配深色模式
"""

from PySide6.QtWidgets import QDateEdit, QCalendarWidget, QComboBox
from PySide6.QtGui import QTextCharFormat, QBrush, QColor
from PySide6.QtCore import Qt

from utils.theme_manager import ThemeManager
from utils.config_manager import ConfigManager


def apply_theme_to_date_edit(date_edit: QDateEdit):
    """
    为 QDateEdit 的日历弹出框和本身适配深色模式
    
    Args:
        date_edit: QDateEdit 实例
    """
    # 应用QDateEdit本身的样式
    _apply_theme_to_widget(date_edit)
    
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


def apply_theme_to_combo_box(combo_box: QComboBox):
    """
    为 QComboBox 适配深色模式
    
    Args:
        combo_box: QComboBox 实例
    """
    _apply_theme_to_widget(combo_box)


def _apply_theme_to_widget(widget):
    """内部函数：为QDateEdit或QComboBox应用主题样式"""
    is_dark = _detect_theme()
    
    if is_dark:
        # 深色模式样式
        widget.setStyleSheet("""
            QDateEdit, QComboBox {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 4px;
            }
            QDateEdit:hover, QComboBox:hover {
                border: 1px solid #555555;
            }
            QDateEdit:focus, QComboBox:focus {
                border: 1px solid #4a90e2;
            }
            QComboBox::drop-down {
                border: none;
                background-color: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #ffffff;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #404040;
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
        """)
    else:
        # 浅色模式样式
        widget.setStyleSheet("""
            QDateEdit, QComboBox {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #cccccc;
                border-radius: 4px;
                padding: 4px;
            }
            QDateEdit:hover, QComboBox:hover {
                border: 1px solid #999999;
            }
            QDateEdit:focus, QComboBox:focus {
                border: 1px solid #4a90e2;
            }
            QComboBox::drop-down {
                border: none;
                background-color: transparent;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #000000;
                margin-right: 5px;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #cccccc;
                selection-background-color: #4a90e2;
                selection-color: #ffffff;
            }
        """)


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

