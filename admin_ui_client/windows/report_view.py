#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报表管理页面：
- 显示报表生成记录列表
- 支持按报表类型筛选
- 支持手动生成报表
- 支持下载报表文件（ZIP压缩）
"""

from datetime import date, datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView,
    QAbstractItemView, QMessageBox, QDialog, QFormLayout,
    QDialogButtonBox, QDateEdit, QLineEdit
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast
from utils.date_edit_helper import apply_theme_to_date_edit

# 导入周数计算工具
import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from jobs.week_calculator import get_week_number, get_week_date_range, get_current_week_number


class _ReportListWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count
    error = Signal(str)


class _ReportListWorker(QRunnable):
    """后台加载报表记录列表"""
    def __init__(
        self,
        report_type: Optional[str] = None,
        offset: int = 0,
        limit: int = 50
    ):
        super().__init__()
        self._report_type = report_type
        self._offset = offset
        self._limit = limit
        self.signals = _ReportListWorkerSignals()

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.get_report_generation_logs(
                report_type=self._report_type,
                offset=self._offset,
                limit=self._limit
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total_count = resp.get("total", 0) if isinstance(resp, dict) else 0
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载报表记录失败：{e}")


class _GenerateReportWorkerSignals(QObject):
    finished = Signal(dict)  # response
    error = Signal(str)


class _GenerateReportWorker(QRunnable):
    """后台生成报表"""
    def __init__(
        self,
        report_type: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        month: Optional[str] = None,
        week_number: Optional[int] = None
    ):
        super().__init__()
        self._report_type = report_type
        self._start_date = start_date
        self._end_date = end_date
        self._month = month
        self._week_number = week_number
        self.signals = _GenerateReportWorkerSignals()

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.generate_report(
                report_type=self._report_type,
                start_date=self._start_date,
                end_date=self._end_date,
                month=self._month,
                week_number=self._week_number
            )
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"生成报表失败：{e}")


class _DownloadReportWorkerSignals(QObject):
    finished = Signal(bytes, str)  # file_content, filename
    error = Signal(str)


class _DownloadReportWorker(QRunnable):
    """后台下载报表文件"""
    def __init__(self, log_id: int):
        super().__init__()
        self._log_id = log_id
        self.signals = _DownloadReportWorkerSignals()

    @Slot()
    def run(self) -> None:
        if not AdminApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = AdminApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            # 先获取记录信息以确定文件名
            logs_resp = client.get_report_generation_logs(limit=1)
            items = logs_resp.get("items", [])
            filename = f"report_{self._log_id}.zip"
            
            # 查找对应的记录
            for item in items:
                if item.get("id") == self._log_id:
                    report_type = item.get("report_type")
                    period_start = item.get("period_start")
                    period_end = item.get("period_end")
                    if report_type == "weekly":
                        filename = f"weekly_{period_start}_{period_end}.zip"
                    else:
                        month_str = period_start[:7] if isinstance(period_start, str) else period_start.strftime("%Y-%m")
                        filename = f"monthly_{month_str}.zip"
                    break
            
            file_content = client.download_report(self._log_id)
            self.signals.finished.emit(file_content, filename)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"下载报表失败：{e}")


class GenerateReportDialog(QDialog):
    """生成报表对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成报表")
        self.setMinimumWidth(400)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        
        # 报表类型
        self._type_combo = QComboBox()
        self._type_combo.addItems(["周报", "月报"])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("报表类型：", self._type_combo)
        
        # 周报：周数选择
        self._week_number_combo = QComboBox()
        # 填充周数选项（当前周及前后各10周）
        current_week = get_current_week_number()
        for week in range(max(6, current_week - 10), current_week + 11):
            week_start, week_end = get_week_date_range(week)
            # 格式：第6周（11-03～11-07）
            week_display = f"第{week}周（{week_start.strftime('%m-%d')}～{week_end.strftime('%m-%d')}）"
            self._week_number_combo.addItem(week_display, week)
        self._week_number_combo.setCurrentIndex(min(10, self._week_number_combo.count() - 1))  # 默认选中当前周
        self._week_number_combo.currentIndexChanged.connect(self._on_week_number_changed)
        form.addRow("周数：", self._week_number_combo)
        
        # 周报日期范围（自动计算，只读显示）
        self._start_date_label = QLabel("")
        self._end_date_label = QLabel("")
        form.addRow("开始日期：", self._start_date_label)
        form.addRow("结束日期：", self._end_date_label)
        
        # 月报月份
        self._month_edit = QComboBox()
        # 填充月份选项（从2025年11月到当前月份）
        current_date = datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        
        # 从2025年11月开始
        start_year = 2025
        start_month = 11
        
        # 生成月份列表
        year = start_year
        month = start_month
        while year < current_year or (year == current_year and month <= current_month):
            month_str = f"{year}-{month:02d}"
            month_display = f"{year}年{month}月"
            self._month_edit.addItem(month_display, month_str)
            month += 1
            if month > 12:
                month = 1
                year += 1
        
        # 默认选中当前月份（最后一个）
        if self._month_edit.count() > 0:
            self._month_edit.setCurrentIndex(self._month_edit.count() - 1)
        form.addRow("月份：", self._month_edit)
        
        # 初始化周数日期显示
        self._on_week_number_changed()
        
        layout.addLayout(form)
        
        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # 初始化显示
        self._on_type_changed()
    
    def _on_type_changed(self):
        """报表类型改变时，显示/隐藏相应字段"""
        is_weekly = self._type_combo.currentIndex() == 0
        self._week_number_combo.setVisible(is_weekly)
        self._start_date_label.setVisible(is_weekly)
        self._end_date_label.setVisible(is_weekly)
        self._month_edit.setVisible(not is_weekly)
    
    def _on_month_changed(self):
        """月份改变时（暂时不需要处理，只是显示）"""
        pass
    
    def _on_week_number_changed(self):
        """周数改变时，自动计算并显示日期范围"""
        week_number = self._week_number_combo.currentData()
        if week_number:
            try:
                week_start, week_end = get_week_date_range(week_number)
                self._start_date_label.setText(week_start.isoformat())
                self._end_date_label.setText(week_end.isoformat())
            except Exception as e:
                self._start_date_label.setText("计算失败")
                self._end_date_label.setText("计算失败")
    
    def get_data(self) -> Dict[str, Any]:
        """获取表单数据"""
        is_weekly = self._type_combo.currentIndex() == 0
        data = {
            "report_type": "weekly" if is_weekly else "monthly"
        }
        if is_weekly:
            week_number = self._week_number_combo.currentData()
            if week_number:
                week_start, week_end = get_week_date_range(week_number)
                data["week_number"] = week_number
                data["start_date"] = week_start.isoformat()
                data["end_date"] = week_end.isoformat()
            else:
                # 如果没有周数，使用日期标签（理论上不会发生）
                data["start_date"] = self._start_date_label.text()
                data["end_date"] = self._end_date_label.text()
        else:
            data["month"] = self._month_edit.currentData()
        return data


class ReportView(QWidget):
    def __init__(self):
        super().__init__()
        
        self._is_loading = False
        self._current_report_type = None
        self._thread_pool = QThreadPool.globalInstance()  # 必须在_setup_ui之前初始化
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 标题
        title = QLabel("统计&报表")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(title)
        
        # 筛选和操作区域
        filter_layout = QHBoxLayout()
        
        # 报表类型筛选
        filter_layout.addWidget(QLabel("报表类型："))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["全部", "周报", "月报"])
        self._type_combo.currentIndexChanged.connect(self._on_type_filter_changed)
        filter_layout.addWidget(self._type_combo)
        
        # 周数选择（周报时显示）
        filter_layout.addWidget(QLabel("周数："))
        self._week_filter_combo = QComboBox()
        # 填充周数选项（从第6周到当前周，不显示未来周）
        current_week = get_current_week_number()
        for week in range(6, current_week + 1):  # 从第6周到当前周（包含当前周）
            week_start, week_end = get_week_date_range(week)
            # 格式：第6周（11-03～11-07）
            week_display = f"第{week}周（{week_start.strftime('%m-%d')}～{week_end.strftime('%m-%d')}）"
            self._week_filter_combo.addItem(week_display, week)
        # 默认选中当前周（最后一个）
        if self._week_filter_combo.count() > 0:
            self._week_filter_combo.setCurrentIndex(self._week_filter_combo.count() - 1)
        self._week_filter_combo.currentIndexChanged.connect(self._on_week_filter_changed)
        filter_layout.addWidget(self._week_filter_combo)
        
        # 月份选择（月报时显示）
        filter_layout.addWidget(QLabel("月份："))
        self._month_filter_combo = QComboBox()
        # 填充月份选项（从2025年11月到当前月份）
        current_date = datetime.now()
        current_year = current_date.year
        current_month = current_date.month
        
        # 从2025年11月开始
        start_year = 2025
        start_month = 11
        
        # 生成月份列表
        year = start_year
        month = start_month
        while year < current_year or (year == current_year and month <= current_month):
            month_str = f"{year}-{month:02d}"
            month_display = f"{year}年{month}月"
            self._month_filter_combo.addItem(month_display, month_str)
            month += 1
            if month > 12:
                month = 1
                year += 1
        
        # 默认选中当前月份（最后一个）
        if self._month_filter_combo.count() > 0:
            self._month_filter_combo.setCurrentIndex(self._month_filter_combo.count() - 1)
        filter_layout.addWidget(self._month_filter_combo)
        
        # 生成报表按钮
        btn_generate = QPushButton("生成报表")
        btn_generate.clicked.connect(self._on_generate_clicked)
        filter_layout.addWidget(btn_generate)
        
        # 刷新按钮
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._load_data)
        filter_layout.addWidget(btn_refresh)
        
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(10)
        self._table.setHorizontalHeaderLabels([
            "ID", "报表类型", "周数/月份", "开始日期", "结束日期", "文件大小", "生成方式", "生成者", "生成时间", "操作"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 报表类型
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 周数/月份
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 开始日期
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 结束日期
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 文件大小
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 生成方式
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 生成者
        header.setSectionResizeMode(8, QHeaderView.Stretch)  # 生成时间
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)  # 操作
        
        layout.addWidget(self._table)
        
        # 状态标签
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)
        
        # 初始化筛选条件显示（必须在所有UI元素创建之后）
        self._on_type_filter_changed()
    
    def _on_type_filter_changed(self):
        """报表类型筛选改变"""
        index = self._type_combo.currentIndex()
        if index == 0:
            self._current_report_type = None
            self._week_filter_combo.setVisible(False)
            self._month_filter_combo.setVisible(False)
        elif index == 1:
            self._current_report_type = "weekly"
            self._week_filter_combo.setVisible(True)
            self._month_filter_combo.setVisible(False)
        else:
            self._current_report_type = "monthly"
            self._week_filter_combo.setVisible(False)
            self._month_filter_combo.setVisible(True)
        # 只有在UI完全初始化后才加载数据
        if hasattr(self, '_status_label') and hasattr(self, '_thread_pool'):
            self._load_data()
    
    def _on_week_filter_changed(self):
        """周数筛选改变（暂时不需要处理，只是显示）"""
        pass
    
    def _load_data(self):
        """加载报表记录列表"""
        if self._is_loading:
            return
        
        self._is_loading = True
        self._status_label.setText("加载中...")
        
        worker = _ReportListWorker(
            report_type=self._current_report_type,
            offset=0,
            limit=100
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
    
    def _on_data_loaded(self, items: List[Dict], total_count: int):
        """数据加载完成"""
        self._is_loading = False
        self._apply_rows_to_table(items)
        self._status_label.setText(f"共 {total_count} 条记录")
    
    def _on_error(self, error: str):
        """加载错误"""
        self._is_loading = False
        self._status_label.setText(f"加载失败：{error}")
        handle_api_error(self, Exception(error), "加载报表记录失败")
    
    def _apply_rows_to_table(self, items: List[Dict]):
        """将数据应用到表格"""
        self._table.setRowCount(0)
        self._table.setRowCount(len(items))
        
        for idx, item in enumerate(items):
            row = idx
            
            log_id = item.get("id", 0)
            report_type = item.get("report_type", "")
            period_start = item.get("period_start", "")
            period_end = item.get("period_end", "")
            file_size = item.get("file_size", 0)
            generation_method = item.get("generation_method", "")
            generated_by = item.get("generated_by") or "系统"
            generated_at = item.get("generated_at", "")
            status = item.get("status", "")
            
            # 格式化日期
            if isinstance(period_start, str):
                try:
                    period_start = datetime.fromisoformat(period_start.replace("Z", "+00:00")).date()
                except:
                    pass
            if isinstance(period_end, str):
                try:
                    period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00")).date()
                except:
                    pass
            if isinstance(generated_at, str):
                try:
                    generated_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                except:
                    pass
            
            # 格式化文件大小
            if file_size < 1024:
                file_size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                file_size_str = f"{file_size / 1024:.2f} KB"
            else:
                file_size_str = f"{file_size / (1024 * 1024):.2f} MB"
            
            # 格式化生成时间
            if isinstance(generated_at, datetime):
                generated_at_str = generated_at.strftime("%Y-%m-%d %H:%M:%S")
            else:
                generated_at_str = str(generated_at)
            
            # 报表类型显示
            type_text = "周报" if report_type == "weekly" else "月报"
            
            # 计算周数/月份显示
            period_display = ""
            if report_type == "weekly":
                # 从开始日期计算周数
                if isinstance(period_start, date):
                    try:
                        week_number = get_week_number(period_start)
                        period_display = f"第{week_number}周"
                    except:
                        period_display = "-"
                else:
                    period_display = "-"
            else:
                # 月报显示月份
                if isinstance(period_start, date):
                    period_display = period_start.strftime("%Y-%m")
                elif isinstance(period_start, str):
                    try:
                        period_start_date = datetime.fromisoformat(period_start.replace("Z", "+00:00")).date()
                        period_display = period_start_date.strftime("%Y-%m")
                    except:
                        period_display = "-"
                else:
                    period_display = "-"
            
            # 生成方式显示
            method_text = "自动" if generation_method == "auto" else "手动"
            
            # 状态显示
            status_text = "成功" if status == "success" else "失败"
            
            self._table.setItem(row, 0, QTableWidgetItem(str(log_id)))
            self._table.setItem(row, 1, QTableWidgetItem(type_text))
            self._table.setItem(row, 2, QTableWidgetItem(period_display))
            self._table.setItem(row, 3, QTableWidgetItem(str(period_start)))
            self._table.setItem(row, 4, QTableWidgetItem(str(period_end)))
            self._table.setItem(row, 5, QTableWidgetItem(file_size_str))
            self._table.setItem(row, 6, QTableWidgetItem(method_text))
            self._table.setItem(row, 7, QTableWidgetItem(generated_by))
            self._table.setItem(row, 8, QTableWidgetItem(generated_at_str))
            
            # 操作列：下载按钮
            if status == "success":
                btn_download = QPushButton("下载")
                btn_download.clicked.connect(lambda checked, lid=log_id: self._on_download_clicked(lid))
                self._table.setCellWidget(row, 9, btn_download)
            else:
                self._table.setItem(row, 9, QTableWidgetItem("无法下载"))
    
    def _on_generate_clicked(self):
        """生成报表按钮点击"""
        # 检查筛选条件，如果已指定周数/月份，直接使用筛选条件生成
        report_type_index = self._type_combo.currentIndex()
        
        if report_type_index == 0:
            # 全部，需要弹出对话框选择
            dialog = GenerateReportDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return
            data = dialog.get_data()
        elif report_type_index == 1:
            # 周报，使用筛选条件中的周数
            week_number = self._week_filter_combo.currentData()
            if not week_number:
                QMessageBox.warning(self, "提示", "请先选择周数")
                return
            week_start, week_end = get_week_date_range(week_number)
            data = {
                "report_type": "weekly",
                "week_number": week_number,
                "start_date": week_start.isoformat(),
                "end_date": week_end.isoformat()
            }
        else:
            # 月报，使用筛选条件中的月份
            month = self._month_filter_combo.currentData()
            if not month:
                QMessageBox.warning(self, "提示", "请先选择月份")
                return
            data = {
                "report_type": "monthly",
                "month": month
            }
        
        # 验证数据
        if data["report_type"] == "weekly":
            if not data.get("start_date") or not data.get("end_date"):
                QMessageBox.warning(self, "提示", "周报需要指定开始日期和结束日期")
                return
        else:
            if not data.get("month"):
                QMessageBox.warning(self, "提示", "月报需要指定月份（格式：YYYY-MM）")
                return
        
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("正在生成报表...")
        
        # 后台生成
        worker = _GenerateReportWorker(
            report_type=data["report_type"],
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            month=data.get("month"),
            week_number=data.get("week_number")
        )
        worker.signals.finished.connect(self._on_generate_success)
        worker.signals.error.connect(self._on_generate_error)
        self._thread_pool.start(worker)
    
    def _on_generate_success(self, resp: Dict[str, Any]):
        """生成报表成功"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        if resp.get("status") == "success":
            Toast.show_message(self, "报表生成成功")
            self._load_data()  # 刷新列表
        else:
            message = resp.get("message", "生成失败")
            QMessageBox.warning(self, "生成失败", message)
    
    def _on_generate_error(self, error: str):
        """生成报表失败"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        handle_api_error(self, Exception(error), "生成报表失败")
    
    def _on_download_clicked(self, log_id: int):
        """下载报表按钮点击"""
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("正在下载报表...")
        
        # 后台下载
        worker = _DownloadReportWorker(log_id)
        worker.signals.finished.connect(self._on_download_success)
        worker.signals.error.connect(self._on_download_error)
        self._thread_pool.start(worker)
    
    def _on_download_success(self, file_content: bytes, filename: str):
        """下载成功"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        # 保存文件
        from PySide6.QtWidgets import QFileDialog
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存报表文件",
            filename,
            "ZIP Files (*.zip);;All Files (*)"
        )
        
        if save_path:
            try:
                with open(save_path, "wb") as f:
                    f.write(file_content)
                Toast.show_message(self, f"报表已保存到：{save_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"无法保存文件：{e}")
    
    def _on_download_error(self, error: str):
        """下载失败"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        handle_api_error(self, Exception(error), "下载报表失败")
    
    def reload_from_api(self):
        """从API重新加载数据（供主窗口调用）"""
        self._load_data()

