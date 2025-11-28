#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日历管理页面：
- 显示工作日日历视图（按月排列）
- 支持单个日期切换工作日/非工作日
- 支持批量设置日期范围
- 支持初始化默认工作日（未来N个月）
- 支持备注说明（如：国庆节调休、春节假期等）
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox,
    QDateEdit, QCheckBox, QLineEdit, QDialog, QFormLayout, QDialogButtonBox,
    QCalendarWidget, QTextEdit, QGridLayout, QScrollArea, QFrame
)
from PySide6.QtGui import QFont, QColor, QBrush, QPainter, QTextCharFormat
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate, QTimer, QEvent

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.theme_manager import ThemeManager
from utils.date_edit_helper import apply_theme_to_date_edit
from widgets.toast import Toast


class _WorkdayListWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _WorkdayListWorker(QRunnable):
    """后台线程：获取工作日列表"""
    def __init__(self, start_date: Optional[date] = None, end_date: Optional[date] = None):
        super().__init__()
        self.signals = _WorkdayListWorkerSignals()
        self._start_date = start_date
        self._end_date = end_date

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
            data = client.get_workdays(start_date=self._start_date, end_date=self._end_date)
            if isinstance(data, dict) and data.get("status") == "success":
                items = data.get("items", [])
                self.signals.finished.emit(items)
            else:
                message = data.get("message", "获取工作日列表失败") if isinstance(data, dict) else "获取工作日列表失败"
                self.signals.error.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"获取工作日列表失败：{e}")


class _WorkdayUpdateWorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)


class _WorkdayUpdateWorker(QRunnable):
    """后台线程：更新工作日"""
    def __init__(self, date_val: date, is_workday: bool, note: Optional[str] = None):
        super().__init__()
        self.signals = _WorkdayUpdateWorkerSignals()
        self._date = date_val
        self._is_workday = is_workday
        self._note = note

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
            data = client.update_workday(self._date, self._is_workday, self._note)
            if isinstance(data, dict) and data.get("status") == "success":
                self.signals.finished.emit()
            else:
                message = data.get("message", "更新工作日失败") if isinstance(data, dict) else "更新工作日失败"
                self.signals.error.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"更新工作日失败：{e}")


class _WorkdayBatchUpdateWorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)


class _WorkdayBatchUpdateWorker(QRunnable):
    """后台线程：批量设置工作日"""
    def __init__(self, start_date: date, end_date: date, is_workday: bool, note: Optional[str] = None):
        super().__init__()
        self.signals = _WorkdayBatchUpdateWorkerSignals()
        self._start_date = start_date
        self._end_date = end_date
        self._is_workday = is_workday
        self._note = note

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
            data = client.batch_update_workdays(self._start_date, self._end_date, self._is_workday, self._note)
            if isinstance(data, dict) and data.get("status") == "success":
                self.signals.finished.emit()
            else:
                message = data.get("message", "批量设置工作日失败") if isinstance(data, dict) else "批量设置工作日失败"
                self.signals.error.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"批量设置工作日失败：{e}")


class _WorkdayInitWorkerSignals(QObject):
    finished = Signal()
    error = Signal(str)


class _WorkdayInitWorker(QRunnable):
    """后台线程：初始化工作日"""
    def __init__(self, months: int):
        super().__init__()
        self.signals = _WorkdayInitWorkerSignals()
        self._months = months

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
            data = client.init_workdays(self._months)
            if isinstance(data, dict) and data.get("status") == "success":
                self.signals.finished.emit()
            else:
                message = data.get("message", "初始化工作日失败") if isinstance(data, dict) else "初始化工作日失败"
                self.signals.error.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"初始化工作日失败：{e}")


class BatchUpdateDialog(QDialog):
    """批量设置工作日对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量设置工作日")
        self.setMinimumWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QFormLayout(self)
        
        self._start_date_edit = QDateEdit()
        self._start_date_edit.setCalendarPopup(True)
        self._start_date_edit.setDate(QDate.currentDate())
        apply_theme_to_date_edit(self._start_date_edit)
        layout.addRow("开始日期：", self._start_date_edit)
        
        self._end_date_edit = QDateEdit()
        self._end_date_edit.setCalendarPopup(True)
        self._end_date_edit.setDate(QDate.currentDate().addMonths(1))
        apply_theme_to_date_edit(self._end_date_edit)
        layout.addRow("结束日期：", self._end_date_edit)
        
        self._is_workday_check = QCheckBox("设为工作日")
        self._is_workday_check.setChecked(True)
        layout.addRow("", self._is_workday_check)
        
        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("备注说明（如：国庆节调休、春节假期等）")
        layout.addRow("备注：", self._note_edit)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        # 设置按钮文本为中文
        buttons.button(QDialogButtonBox.Ok).setText("提交")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> Dict[str, Any]:
        start_date = self._start_date_edit.date().toPython()
        end_date = self._end_date_edit.date().toPython()
        return {
            "start_date": start_date,
            "end_date": end_date,
            "is_workday": self._is_workday_check.isChecked(),
            "note": self._note_edit.text().strip() or None,
        }


class WorkdayCalendarWidget(QCalendarWidget):
    """自定义日历组件，支持工作日标记"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workday_map: Dict[date, Dict[str, Any]] = {}
        self._is_dark = False  # 主题标志
        self._other_month_bg = QColor("#f5f5f5")  # 非本月日期背景色
        self._other_month_text = QColor("#cccccc")  # 非本月日期文字色
        self._setup_calendar()

    def _setup_calendar(self):
        """设置日历样式"""
        # 设置周一为第一天
        self.setFirstDayOfWeek(Qt.Monday)
        # 设置网格可见
        self.setGridVisible(True)
        # 设置日期选择模式
        self.setSelectionMode(QCalendarWidget.SingleSelection)
        # 隐藏垂直表头（左侧的周数）
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        # 设置水平表头格式（只显示周一到周六）
        self.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        
        # 检测当前主题
        self._is_dark = self._detect_theme()
        
        # 根据主题设置颜色
        self._update_colors()
        
        # 根据主题设置星期标题样式
        self._update_header_style()
        
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                # 自动模式，检测系统主题
                theme = ThemeManager.detect_system_theme()
            else:
                # 用户手动设置的主题
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            # 如果检测失败，默认返回False（浅色模式）
            return False
    
    def _update_colors(self):
        """根据主题更新颜色"""
        if self._is_dark:
            # 深色模式
            # 设置工作日格式（深色模式下的绿色背景）
            workday_format = QTextCharFormat()
            workday_format.setBackground(QBrush(QColor("#1e4620")))  # 深绿色背景
            workday_format.setForeground(QBrush(QColor("#81c784")))  # 浅绿色文字
            workday_format.setFontWeight(QFont.Normal)
            
            # 设置非工作日格式（深色模式下的红色背景）
            non_workday_format = QTextCharFormat()
            non_workday_format.setBackground(QBrush(QColor("#3d1f1f")))  # 深红色背景
            non_workday_format.setForeground(QBrush(QColor("#e57373")))  # 浅红色文字
            non_workday_format.setFontWeight(QFont.Normal)
            
            # 设置空白格式（数据库中没有的日期）
            empty_format = QTextCharFormat()
            empty_format.setForeground(QBrush(QColor("#666666")))  # 灰色文字
            empty_format.setBackground(QBrush(QColor("#2a2a2a")))  # 深灰色背景
            empty_format.setFontWeight(QFont.Normal)
            
            # 非本月日期颜色
            self._other_month_bg = QColor("#1e1e1e")
            self._other_month_text = QColor("#555555")
        else:
            # 浅色模式
            # 设置工作日格式（绿色背景）
            workday_format = QTextCharFormat()
            workday_format.setBackground(QBrush(QColor("#d4edda")))  # 浅绿色背景
            workday_format.setForeground(QBrush(QColor("#155724")))  # 深绿色文字
            workday_format.setFontWeight(QFont.Normal)
            
            # 设置非工作日格式（红色背景）
            non_workday_format = QTextCharFormat()
            non_workday_format.setBackground(QBrush(QColor("#f8d7da")))  # 浅红色背景
            non_workday_format.setForeground(QBrush(QColor("#721c24")))  # 深红色文字
            non_workday_format.setFontWeight(QFont.Normal)
            
            # 设置空白格式（数据库中没有的日期）
            empty_format = QTextCharFormat()
            empty_format.setForeground(QBrush(QColor("#cccccc")))  # 灰色文字
            empty_format.setBackground(QBrush(QColor("#f5f5f5")))  # 浅灰色背景
            empty_format.setFontWeight(QFont.Normal)
            
            # 非本月日期颜色
            self._other_month_bg = QColor("#f5f5f5")
            self._other_month_text = QColor("#cccccc")
        
        # 存储格式供后续使用
        self._workday_format = workday_format
        self._non_workday_format = non_workday_format
        self._empty_format = empty_format
        
        # 更新样式表，移除选中状态的蓝色背景
        self._update_selection_style()
    
    def _update_selection_style(self):
        """更新选中状态的样式，使其看起来和普通状态一样"""
        # 移除选中状态的蓝色背景和白色文字，使其看起来和普通状态一样
        selection_style = """
            QCalendarWidget QTableView::item:selected {
                background-color: transparent;
                color: inherit;
                border: none;
            }
            QCalendarWidget QTableView::item:hover {
                background-color: transparent;
                color: inherit;
            }
            QCalendarWidget QTableView::item:selected:focus {
                background-color: transparent;
                color: inherit;
                border: none;
                outline: none;
            }
        """
        self.setStyleSheet(selection_style)
    
    def _update_header_style(self):
        """根据主题更新星期标题样式"""
        # 定义星期枚举值列表（周一至周日）
        weekdays = [Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday, Qt.Saturday, Qt.Sunday]
        
        if self._is_dark:
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
                    self.setWeekdayTextFormat(day, weekend_format)
                else:
                    self.setWeekdayTextFormat(day, header_format)
        else:
            # 浅色模式：星期标题使用默认样式，周末为红色
            header_format = QTextCharFormat()
            header_format.setForeground(QBrush(QColor("#000000")))  # 黑色文字
            weekend_format = QTextCharFormat()
            weekend_format.setForeground(QBrush(QColor("#dc3545")))  # 红色文字
            
            # 应用格式到所有星期标题
            for i, day in enumerate(weekdays):
                if i >= 5:  # 周六、周日（索引5、6）
                    self.setWeekdayTextFormat(day, weekend_format)
                else:
                    self.setWeekdayTextFormat(day, header_format)
    
    def update_theme(self):
        """更新主题（当主题切换时调用）"""
        # 重新检测主题
        old_is_dark = self._is_dark
        self._is_dark = self._detect_theme()
        
        # 如果主题发生变化，更新颜色和样式
        if old_is_dark != self._is_dark:
            self._update_colors()
            self._update_header_style()
            # 重新应用日期格式
            self._update_calendar_formatting()
            # 触发重绘
            self.update()
        else:
            # 即使主题没变化，也更新选中状态样式（确保样式正确应用）
            self._update_selection_style()

    def set_workday_map(self, workday_map: Dict[date, Dict[str, Any]]):
        """设置工作日数据"""
        self._workday_map = workday_map
        self._update_calendar_formatting()

    def _update_calendar_formatting(self):
        """更新日历的日期格式（根据工作日状态）"""
        # 获取当前日历显示的年月范围
        min_date = self.minimumDate()
        max_date = self.maximumDate()
        
        # 先清除该月份所有日期的格式（包括默认的1日特殊格式）
        current = min_date
        while current <= max_date:
            # 清除所有默认格式，包括1日的特殊格式
            # 使用空的QTextCharFormat来清除所有默认样式
            default_format = QTextCharFormat()
            # 确保1日也使用相同的默认格式
            if current.day() == 1:
                # 特别处理1日，确保清除其默认格式
                default_format.setForeground(QBrush())  # 清除前景色
                default_format.setBackground(QBrush())  # 清除背景色
                default_format.setFontWeight(QFont.Normal)  # 清除加粗
            self.setDateTextFormat(current, default_format)
            
            # 如果不在数据库中，设置为空白格式
            if current.toPython() not in self._workday_map:
                self.setDateTextFormat(current, self._empty_format)
            current = current.addDays(1)
        
        # 为数据库中的每个日期设置格式
        for date_val, workday_info in self._workday_map.items():
            qdate = QDate(date_val.year, date_val.month, date_val.day)
            if qdate.isValid() and min_date <= qdate <= max_date:
                is_workday = workday_info.get("is_workday", True)
                if is_workday:
                    self.setDateTextFormat(qdate, self._workday_format)
                else:
                    self.setDateTextFormat(qdate, self._non_workday_format)

    def paintCell(self, painter: QPainter, rect, date: QDate):
        """自定义绘制日期单元格"""
        date_val = date.toPython()
        
        # 检查日期是否属于当前显示的月份
        min_date = self.minimumDate()
        max_date = self.maximumDate()
        is_current_month = min_date <= date <= max_date
        
        # 如果是1日，完全自定义绘制，不调用父类（避免父类应用默认的1日样式）
        if date_val.day == 1:
            # 如果不在当前月份，使用统一的灰色样式（非本月日期）
            if not is_current_month:
                # 非本月的1日，使用统一的灰色样式（根据主题）
                painter.fillRect(rect, self._other_month_bg)
                painter.setPen(self._other_month_text)
                font = painter.font()
                font.setWeight(QFont.Normal)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignCenter, str(date_val.day))
                return
            
            # 当前月份的1日，根据数据库数据设置格式
            # 如果数据库中有该日期，使用正确的格式
            if date_val in self._workday_map:
                workday_info = self._workday_map[date_val]
                is_workday = workday_info.get("is_workday", True)
                if is_workday:
                    current_format = self._workday_format
                else:
                    current_format = self._non_workday_format
            else:
                # 不在数据库中，使用空白格式
                current_format = self._empty_format
            
            # 确保1日不加粗
            current_format.setFontWeight(QFont.Normal)
            self.setDateTextFormat(date, current_format)
            
            # 绘制背景
            bg_color = current_format.background().color() if current_format.background().style() != Qt.NoBrush else QColor("#ffffff")
            painter.fillRect(rect, bg_color)
            
            # 绘制文字
            text_color = current_format.foreground().color() if current_format.foreground().style() != Qt.NoBrush else QColor("#000000")
            painter.setPen(text_color)
            painter.setFont(current_format.font())
            painter.drawText(rect, Qt.AlignCenter, str(date_val.day))
            
            # 如果有备注，绘制标记
            if date_val in self._workday_map:
                workday_info = self._workday_map[date_val]
                note = workday_info.get("note", "")
                is_workday = workday_info.get("is_workday", True)
                if note:
                    painter.setPen(QColor("#ffc107") if is_workday else QColor("#dc3545"))
                    painter.setBrush(QBrush(QColor("#ffc107") if is_workday else QColor("#dc3545")))
                    dot_size = 6
                    dot_x = rect.right() - dot_size - 2
                    dot_y = rect.bottom() - dot_size - 2
                    painter.drawEllipse(dot_x, dot_y, dot_size, dot_size)
            return
        
        # 非1日的日期，也需要检查是否属于当前月份
        if not is_current_month:
            # 非本月的日期，使用统一的灰色样式（根据主题）
            painter.fillRect(rect, self._other_month_bg)
            painter.setPen(self._other_month_text)
            font = painter.font()
            font.setWeight(QFont.Normal)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, str(date_val.day))
            return
        
        # 非1日的日期，正常处理
        # 如果数据库中没有该日期，显示为空白或灰色
        if date_val not in self._workday_map:
            # 使用空白格式绘制（完全自定义，不调用父类，避免选中状态）
            bg_color = self._empty_format.background().color() if self._empty_format.background().style() != Qt.NoBrush else (QColor("#2a2a2a") if self._is_dark else QColor("#f5f5f5"))
            text_color = self._empty_format.foreground().color() if self._empty_format.foreground().style() != Qt.NoBrush else (QColor("#666666") if self._is_dark else QColor("#cccccc"))
            painter.fillRect(rect, bg_color)
            painter.setPen(text_color)
            font = painter.font()
            font.setWeight(QFont.Normal)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, str(date_val.day))
            return
        
        # 数据库中有该日期，使用自定义格式绘制（完全自定义，不调用父类，避免选中状态）
        workday_info = self._workday_map[date_val]
        is_workday = workday_info.get("is_workday", True)
        if is_workday:
            current_format = self._workday_format
        else:
            current_format = self._non_workday_format
        
        # 绘制背景（忽略选中状态）
        bg_color = current_format.background().color() if current_format.background().style() != Qt.NoBrush else (QColor("#2a2a2a") if self._is_dark else QColor("#f5f5f5"))
        painter.fillRect(rect, bg_color)
        
        # 绘制文字（忽略选中状态）
        text_color = current_format.foreground().color() if current_format.foreground().style() != Qt.NoBrush else (QColor("#666666") if self._is_dark else QColor("#cccccc"))
        painter.setPen(text_color)
        font = current_format.font()
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, str(date_val.day))
        
        # 如果有备注，绘制标记
        note = workday_info.get("note", "")
        if note:
            # 如果有备注，绘制一个小标记
            painter.setPen(QColor("#ffc107") if is_workday else QColor("#dc3545"))
            painter.setBrush(QBrush(QColor("#ffc107") if is_workday else QColor("#dc3545")))
            # 在右下角绘制小圆点
            dot_size = 6
            dot_x = rect.right() - dot_size - 2
            dot_y = rect.bottom() - dot_size - 2
            painter.drawEllipse(dot_x, dot_y, dot_size, dot_size)


class WorkdayView(QWidget):
    """日历管理页面（日历形式）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workday_map: Dict[date, Dict[str, Any]] = {}
        self._thread_pool = QThreadPool.globalInstance()
        self._current_month = date.today().replace(day=1)  # 当前显示的起始月份（月初）
        self._calendar_widgets: List[WorkdayCalendarWidget] = []  # 存储6个月份的日历组件
        self._month_frames: List[QFrame] = []  # 存储6个月份的框架容器
        self._is_dark = self._detect_theme()  # 检测主题
        self._init_ui()
        self._load_workdays()
        
        # 设置主题检测定时器（每500ms检测一次主题变化）
        self._theme_check_timer = QTimer(self)
        self._theme_check_timer.timeout.connect(self._check_theme_change)
        self._theme_check_timer.start(500)  # 每500ms检测一次
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        
        # 月份导航
        self._prev_month_btn = QPushButton("◀ 上6个月")
        self._prev_month_btn.clicked.connect(self._on_prev_six_months)
        toolbar.addWidget(self._prev_month_btn)
        
        self._month_label = QLabel()
        self._update_month_label()
        self._update_month_label_style()
        toolbar.addWidget(self._month_label)
        
        self._next_month_btn = QPushButton("下6个月 ▶")
        self._next_month_btn.clicked.connect(self._on_next_six_months)
        toolbar.addWidget(self._next_month_btn)
        
        toolbar.addStretch()
        
        # 快速跳转
        self._jump_label = QLabel("跳转到：")
        self._update_label_style(self._jump_label)
        toolbar.addWidget(self._jump_label)
        self._jump_date_edit = QDateEdit()
        self._jump_date_edit.setCalendarPopup(True)
        self._jump_date_edit.setDate(QDate.currentDate())
        self._jump_date_edit.dateChanged.connect(self._on_jump_date_changed)
        # 适配深色模式
        apply_theme_to_date_edit(self._jump_date_edit)
        toolbar.addWidget(self._jump_date_edit)
        
        toolbar.addStretch()
        
        # 刷新按钮
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_workdays)
        toolbar.addWidget(refresh_btn)
        
        # 批量设置按钮
        batch_btn = QPushButton("批量设置")
        batch_btn.clicked.connect(self._on_batch_update_clicked)
        toolbar.addWidget(batch_btn)
        
        # 初始化按钮
        init_btn = QPushButton("初始化默认工作日")
        init_btn.clicked.connect(self._on_init_clicked)
        toolbar.addWidget(init_btn)
        
        layout.addLayout(toolbar)

        # 日历视图（6个月，3x2网格）
        calendar_container = QWidget()
        self._calendar_grid = QGridLayout(calendar_container)
        self._calendar_grid.setSpacing(15)
        self._calendar_grid.setContentsMargins(10, 10, 10, 10)
        
        # 创建6个月份的日历（上面3个，下面3个）
        self._refresh_calendars()
        
        layout.addWidget(calendar_container)

        # 图例和说明
        legend_layout = QHBoxLayout()
        self._legend_label = QLabel("图例：")
        self._update_label_style(self._legend_label)
        legend_layout.addWidget(self._legend_label)
        
        self._workday_label = QLabel("工作日")
        legend_layout.addWidget(self._workday_label)
        
        self._non_workday_label = QLabel("非工作日")
        legend_layout.addWidget(self._non_workday_label)
        
        self._note_label = QLabel("有备注")
        legend_layout.addWidget(self._note_label)
        
        legend_layout.addStretch()
        
        self._info_label = QLabel(
            "说明：点击日期可切换工作日/非工作日状态，支持添加备注说明"
        )
        legend_layout.addWidget(self._info_label)
        
        # 更新图例和说明的样式
        self._update_legend_styles()
        
        layout.addLayout(legend_layout)

    def _update_month_label_style(self):
        """更新月份标签样式（根据主题）"""
        text_color = "#ffffff" if self._is_dark else "#000000"
        self._month_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {text_color};")
    
    def _update_label_style(self, label: QLabel):
        """更新标签样式（根据主题）"""
        text_color = "#ffffff" if self._is_dark else "#000000"
        label.setStyleSheet(f"color: {text_color};")
    
    def _update_legend_styles(self):
        """更新图例和说明的样式（根据主题）"""
        if self._is_dark:
            # 深色模式
            self._workday_label.setStyleSheet("background-color: #1e4620; color: #81c784; padding: 4px 8px; border-radius: 4px;")
            self._non_workday_label.setStyleSheet("background-color: #3d1f1f; color: #e57373; padding: 4px 8px; border-radius: 4px;")
            self._note_label.setStyleSheet("background-color: #856404; color: #ffc107; padding: 4px 8px; border-radius: 4px;")
            self._info_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        else:
            # 浅色模式
            self._workday_label.setStyleSheet("background-color: #d4edda; color: #155724; padding: 4px 8px; border-radius: 4px;")
            self._non_workday_label.setStyleSheet("background-color: #f8d7da; color: #721c24; padding: 4px 8px; border-radius: 4px;")
            self._note_label.setStyleSheet("background-color: #ffc107; color: #856404; padding: 4px 8px; border-radius: 4px;")
            self._info_label.setStyleSheet("color: #666; font-size: 12px;")

    def _create_month_calendar(self, month_start: date):
        """创建单个月份的日历，返回 (框架容器, 日历组件)"""
        # 月份标题和日历的容器
        month_frame = QFrame()
        month_frame.setFrameStyle(QFrame.Box)
        # 根据主题设置边框颜色
        border_color = "#555555" if self._is_dark else "#ddd"
        month_frame.setStyleSheet(f"QFrame {{ border: 1px solid {border_color}; border-radius: 4px; }}")
        month_layout = QVBoxLayout(month_frame)
        month_layout.setContentsMargins(8, 8, 8, 8)
        month_layout.setSpacing(4)
        
        # 月份标题
        month_title = QLabel(f"{month_start.year}年{month_start.month}月")
        # 根据主题设置月份标题样式
        bg_color = "#2a2a2a" if self._is_dark else "#f5f5f5"
        text_color = "#ffffff" if self._is_dark else "#000000"
        month_title.setStyleSheet(f"font-size: 14px; font-weight: bold; padding: 4px; background-color: {bg_color}; color: {text_color}; border-radius: 2px;")
        month_title.setAlignment(Qt.AlignCenter)
        month_layout.addWidget(month_title)
        
        # 日历组件
        calendar = WorkdayCalendarWidget()
        calendar.setMinimumDate(QDate(month_start.year, month_start.month, 1))
        
        # 计算该月的最后一天
        if month_start.month == 12:
            last_day = date(month_start.year, 12, 31)
        else:
            last_day = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
        
        calendar.setMaximumDate(QDate(last_day.year, last_day.month, last_day.day))
        calendar.setSelectedDate(QDate(month_start.year, month_start.month, 1))
        
        # 连接点击事件
        calendar.clicked.connect(lambda qdate: self._on_calendar_date_clicked(qdate, calendar))
        
        # 设置工作日数据
        calendar.set_workday_map(self._workday_map)
        
        month_layout.addWidget(calendar)
        
        return month_frame, calendar

    def _update_month_label(self):
        """更新月份标签"""
        # 显示6个月的范围
        end_month = self._get_month_after(self._current_month, 5)  # 当前月份 + 5个月 = 第6个月
        if self._current_month.year == end_month.year:
            month_str = f"{self._current_month.year}年{self._current_month.month}月 - {end_month.month}月"
        else:
            month_str = f"{self._current_month.year}年{self._current_month.month}月 - {end_month.year}年{end_month.month}月"
        self._month_label.setText(month_str)

    def _get_month_after(self, start_month: date, months: int) -> date:
        """获取指定月份之后的第N个月"""
        year = start_month.year
        month = start_month.month + months
        while month > 12:
            month -= 12
            year += 1
        return date(year, month, 1)

    def _on_prev_six_months(self):
        """切换到上6个月"""
        # 往前推6个月
        year = self._current_month.year
        month = self._current_month.month - 6
        while month <= 0:
            month += 12
            year -= 1
        self._current_month = date(year, month, 1)
        self._update_month_label()
        # 刷新日历显示
        self._refresh_calendars()
        self._load_workdays()

    def _on_next_six_months(self):
        """切换到下6个月"""
        self._current_month = self._get_month_after(self._current_month, 6)
        self._update_month_label()
        # 刷新日历显示
        self._refresh_calendars()
        self._load_workdays()

    def _on_jump_date_changed(self, qdate: QDate):
        """跳转到指定日期"""
        date_val = qdate.toPython()
        self._current_month = date_val.replace(day=1)
        self._update_month_label()
        self._load_workdays()

    def _load_workdays(self):
        """加载工作日列表"""
        # 加载6个月的数据
        start_date = self._current_month
        end_month = self._get_month_after(self._current_month, 5)  # 第6个月
        # 计算结束月份的最后一天
        if end_month.month == 12:
            end_date = date(end_month.year, 12, 31)
        else:
            end_date = date(end_month.year, end_month.month + 1, 1) - timedelta(days=1)
        
        worker = _WorkdayListWorker(start_date, end_date)
        worker.signals.finished.connect(self._on_workdays_loaded)
        worker.signals.error.connect(self._on_workdays_error)
        self._thread_pool.start(worker)

    def _on_workdays_loaded(self, items: List[Dict[str, Any]]):
        """工作日列表加载完成"""
        self._workday_map = {}
        for item in items:
            date_val = item["date"]
            if isinstance(date_val, str):
                date_val = datetime.fromisoformat(date_val).date()
            self._workday_map[date_val] = item
        
        # 更新所有日历的显示
        for calendar in self._calendar_widgets:
            calendar.set_workday_map(self._workday_map)
            calendar.update()  # 触发重绘
        
        # 更新切换按钮的启用状态
        self._update_navigation_buttons()

    def _refresh_calendars(self):
        """刷新所有日历的显示（6个月，3x2网格）"""
        # 清除现有日历
        self._calendar_widgets.clear()
        self._month_frames.clear()
        while self._calendar_grid.count():
            child = self._calendar_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # 创建6个月份的日历（上面3个，下面3个）
        current_month = self._current_month
        for i in range(6):
            row = i // 3  # 0或1（上面3个，下面3个）
            col = i % 3   # 0, 1, 2（每行3个）
            month_frame, calendar = self._create_month_calendar(current_month)
            self._calendar_grid.addWidget(month_frame, row, col)
            self._month_frames.append(month_frame)
            self._calendar_widgets.append(calendar)
            # 移动到下一个月
            current_month = self._get_month_after(current_month, 1)

    def _on_workdays_error(self, message: str):
        """工作日列表加载失败"""
        QMessageBox.warning(self, "错误", f"获取工作日列表失败：{message}")
        # 即使加载失败，也尝试更新按钮状态（可能部分数据已加载）
        self._update_navigation_buttons()

    def _update_navigation_buttons(self):
        """更新导航按钮的启用状态"""
        if not hasattr(self, '_prev_month_btn') or not hasattr(self, '_next_month_btn'):
            return
        
        # 允许向前导航（查看历史数据），但至少允许导航到2025-11-01之前
        # 从2025-11-01开始是系统开始日期
        system_start_date = date(2025, 11, 1)
        prev_start_month = self._current_month
        year = prev_start_month.year
        month = prev_start_month.month - 6
        while month <= 0:
            month += 12
            year -= 1
        prev_start = date(year, month, 1)
        
        # 如果向前导航会早于系统开始日期，则禁用
        prev_end = self._get_month_after(prev_start, 5)
        if prev_end.month == 12:
            prev_end_date = date(prev_end.year, 12, 31)
        else:
            prev_end_date = date(prev_end.year, prev_end.month + 1, 1) - timedelta(days=1)
        
        # 如果向前导航的结束日期早于系统开始日期，禁用按钮
        can_go_prev = prev_end_date >= system_start_date
        self._prev_month_btn.setEnabled(can_go_prev)
        
        # 向后导航（查看未来）始终允许，不限制
        # 用户可以查看未来的月份，即使还没有数据
        self._next_month_btn.setEnabled(True)

    def _on_calendar_date_clicked(self, qdate: QDate, calendar: WorkdayCalendarWidget):
        """日历日期被点击"""
        date_val = qdate.toPython()
        if not date_val:
            return
        
        workday_info = self._workday_map.get(date_val, {})
        current_is_workday = workday_info.get("is_workday", True)
        new_is_workday = not current_is_workday
        
        # 创建自定义对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("更新工作日")
        dialog.setMinimumWidth(450)  # 变宽一点
        
        layout = QVBoxLayout(dialog)
        
        # 日期显示
        date_label = QLabel(f"日期：{date_val.strftime('%Y-%m-%d')}")
        layout.addWidget(date_label)
        
        # 状态变化显示（使用箭头）
        current_status = "工作日" if current_is_workday else "非工作日"
        new_status = "工作日" if new_is_workday else "非工作日"
        status_label = QLabel(f"状态：{current_status} --变为--> {new_status}")
        layout.addWidget(status_label)
        
        # 备注输入
        note_label = QLabel("备注说明（可选）：")
        layout.addWidget(note_label)
        note_edit = QLineEdit()
        note_edit.setText(workday_info.get("note", ""))
        layout.addWidget(note_edit)
        
        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("提交")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() != QDialog.Accepted:
            return
        
        # 更新工作日
        note_text = note_edit.text().strip() or None
        worker = _WorkdayUpdateWorker(date_val, new_is_workday, note_text)
        worker.signals.finished.connect(self._load_workdays)
        worker.signals.error.connect(lambda msg: QMessageBox.warning(self, "错误", f"更新工作日失败：{msg}"))
        self._thread_pool.start(worker)

    def _on_batch_update_clicked(self):
        """批量设置工作日"""
        dialog = BatchUpdateDialog(self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            
            # 确认对话框
            reply = QMessageBox.question(
                self,
                "确认",
                f"确定要将 {data['start_date']} 至 {data['end_date']} 的日期"
                f"设置为{'工作日' if data['is_workday'] else '非工作日'}吗？",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                worker = _WorkdayBatchUpdateWorker(
                    data["start_date"],
                    data["end_date"],
                    data["is_workday"],
                    data["note"]
                )
                worker.signals.finished.connect(self._load_workdays)
                worker.signals.error.connect(lambda msg: QMessageBox.warning(self, "错误", f"批量设置失败：{msg}"))
                self._thread_pool.start(worker)

    def _on_init_clicked(self):
        """初始化默认工作日"""
        from PySide6.QtWidgets import QInputDialog
        # 使用 QInputDialog 对象方式，因为静态方法 getInt() 在某些 PySide6 版本中不支持关键字参数
        dialog = QInputDialog(self)
        dialog.setWindowTitle("初始化工作日")
        dialog.setLabelText("初始化未来几个月的工作日？\n（默认规则：周一至周五为工作日，周六、周日为非工作日）")
        dialog.setInputMode(QInputDialog.IntInput)
        dialog.setIntValue(6)
        dialog.setIntMinimum(1)
        dialog.setIntMaximum(99)
        dialog.setIntStep(1)
        
        if dialog.exec() != QInputDialog.Accepted:
            return
        
        months = dialog.intValue()
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认",
            f"确定要初始化未来 {months} 个月的工作日吗？\n"
            f"（已存在的日期不会被覆盖，保留手动设置的值）",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            worker = _WorkdayInitWorker(months)
            worker.signals.finished.connect(self._load_workdays)
            worker.signals.error.connect(lambda msg: QMessageBox.warning(self, "错误", f"初始化失败：{msg}"))
            self._thread_pool.start(worker)
    
    def _check_theme_change(self):
        """检测主题变化并更新所有日历组件"""
        current_is_dark = self._detect_theme()
        if current_is_dark != self._is_dark:
            # 主题已变化，更新所有日历组件
            self._is_dark = current_is_dark
            for calendar in self._calendar_widgets:
                calendar.update_theme()
            # 更新其他UI元素的样式
            self._update_month_label_style()
            self._update_legend_styles()
            # 更新标签样式
            if hasattr(self, '_jump_label'):
                self._update_label_style(self._jump_label)
            if hasattr(self, '_legend_label'):
                self._update_label_style(self._legend_label)
            # 更新月份框架的边框颜色
            for month_frame in self._month_frames:
                border_color = "#555555" if self._is_dark else "#ddd"
                month_frame.setStyleSheet(f"QFrame {{ border: 1px solid {border_color}; border-radius: 4px; }}")
            # 更新月份标题样式
            for i in range(self._calendar_grid.count()):
                item = self._calendar_grid.itemAt(i)
                if item and item.widget():
                    widget = item.widget()
                    if isinstance(widget, QFrame):
                        # 查找月份标题标签
                        for child in widget.findChildren(QLabel):
                            if child.text() and ("年" in child.text() and "月" in child.text()):
                                bg_color = "#2a2a2a" if self._is_dark else "#f5f5f5"
                                text_color = "#ffffff" if self._is_dark else "#000000"
                                child.setStyleSheet(f"font-size: 14px; font-weight: bold; padding: 4px; background-color: {bg_color}; color: {text_color}; border-radius: 2px;")
                                break
    
    def showEvent(self, event: QEvent):
        """页面显示时，立即检测并更新主题"""
        super().showEvent(event)
        # 立即检测一次主题变化
        self._check_theme_change()
