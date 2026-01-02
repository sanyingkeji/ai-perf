#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
月度评分管理页面：
- 支持按月份、员工ID、工资贡献率筛选
- 支持按 total_ai_month、salary_ratio、growth_rate、final_score 排序
- 显示所有员工的月度评分数据
"""

from datetime import date, datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView,
    QLineEdit, QAbstractItemView, QMessageBox, QFileDialog,
    QTabWidget, QSpinBox
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast
from utils.date_edit_helper import apply_theme_to_date_edit


class _MonthlyScoreWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count
    error = Signal(str)


class _LockRankWorkerSignals(QObject):
    finished = Signal(str)  # success message
    error = Signal(str)


class _LockRankWorker(QRunnable):
    """后台锁定排名"""
    def __init__(self, month: str):
        super().__init__()
        self._month = month
        self.signals = _LockRankWorkerSignals()
    
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
            resp = client.lock_month_rank(self._month)
            if isinstance(resp, dict) and resp.get("status") == "success":
                message = resp.get("message", "锁定成功")
                self.signals.finished.emit(message)
            else:
                message = resp.get("message", "锁定失败") if isinstance(resp, dict) else "锁定失败"
                self.signals.error.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"锁定排名失败：{e}")


class _ExportMonthlyScoreWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict] - 导出的数据
    error = Signal(str)


class _ExportMonthlyScoreWorker(QRunnable):
    """后台导出月度评分数据"""
    def __init__(
        self, 
        month: Optional[str] = None,
        user_id: Optional[str] = None,
        salary_ratio_filter: Optional[str] = None,
        sort_by: Optional[str] = "final_score",
        sort_order: Optional[str] = "desc"
    ):
        super().__init__()
        self._month = month
        self._user_id = user_id
        self._salary_ratio_filter = salary_ratio_filter
        self._sort_by = sort_by
        self._sort_order = sort_order
        self.signals = _ExportMonthlyScoreWorkerSignals()
    
    @Slot()
    def run(self) -> None:
        try:
            if not AdminApiClient.is_logged_in():
                self.signals.error.emit("需要先登录")
                return
            
            client = AdminApiClient.from_config()
            
            # 获取所有数据（不限制数量）
            resp = client.get_monthly_scores(
                month=self._month,
                user_id=self._user_id,
                salary_ratio_filter=self._salary_ratio_filter,
                sort_by=self._sort_by,
                sort_order=self._sort_order
            )
            
            if not isinstance(resp, dict):
                self.signals.error.emit("API返回格式错误")
                return
            
            if resp.get("status") != "success":
                error_msg = resp.get("message", "获取数据失败")
                self.signals.error.emit(error_msg)
                return
            
            items = resp.get("items", [])
            # 确保items是列表
            if not isinstance(items, list):
                items = []
            
            self.signals.finished.emit(items)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            import traceback
            error_msg = f"导出数据失败：{e}\n{traceback.format_exc()}"
            self.signals.error.emit(error_msg)


class _MonthlyScoreWorker(QRunnable):
    """后台加载月度评分数据"""
    def __init__(
        self, 
        month: Optional[str] = None,
        user_id: Optional[str] = None,
        salary_ratio_filter: Optional[str] = None,
        sort_by: Optional[str] = "final_score",
        sort_order: Optional[str] = "desc"
    ):
        super().__init__()
        self._month = month
        self._user_id = user_id
        self._salary_ratio_filter = salary_ratio_filter
        self._sort_by = sort_by
        self._sort_order = sort_order
        self.signals = _MonthlyScoreWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态
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
            resp = client.get_monthly_scores(
                month=self._month,
                user_id=self._user_id,
                salary_ratio_filter=self._salary_ratio_filter,
                sort_by=self._sort_by,
                sort_order=self._sort_order
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total_count = len(items)
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载月度评分失败：{e}")


class _ProgressStarWorkerSignals(QObject):
    finished = Signal(dict)  # ProgressStarListResponse
    error = Signal(str)


class _ProgressStarWorker(QRunnable):
    """后台加载进步之星候选列表"""

    def __init__(
        self,
        month: Optional[str] = None,
        *,
        limit: int = 50,
        min_workdays: int = 10,
        exclude_top_n: int = 3,
    ):
        super().__init__()
        self._month = month
        self._limit = limit
        self._min_workdays = min_workdays
        self._exclude_top_n = exclude_top_n
        self.signals = _ProgressStarWorkerSignals()

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
            resp = client.get_progress_star(
                month=self._month,
                limit=int(self._limit),
                min_workdays=int(self._min_workdays),
                exclude_top_n=int(self._exclude_top_n),
            )
            if not isinstance(resp, dict):
                self.signals.error.emit("API返回格式错误")
                return

            if resp.get("status") != "success":
                msg = resp.get("message") or "获取进步之星列表失败"
                self.signals.error.emit(str(msg))
                return

            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载进步之星列表失败：{e}")


class MonthlyScoreView(QWidget):
    def __init__(self):
        super().__init__()
        
        self._is_loading = False
        self._current_filters = {}  # 保存当前筛选条件
        self._current_sort_by = "final_score"  # 当前排序字段
        self._current_sort_order = "desc"  # 当前排序方向
        self._user_team_map = {}  # user_id -> team_name 映射
        
        # 列索引到排序字段的映射
        self._column_to_sort_field = {
            4: "final_score",     # 最终综合分
            5: "total_ai_month",  # AI综合均分
            6: "salary_ratio",    # 工资贡献率
            7: "growth_rate",     # 成长率
        }
        
        self._setup_ui()
        self._thread_pool = QThreadPool.globalInstance()
        # 初始化时加载员工数据以获取团队信息
        self._load_employee_data()
    
    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # 标题
        title = QLabel("月度评分管理")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        root_layout.addWidget(title)

        # Tab：月度综合榜 / 进步之星
        self._tabs = QTabWidget()
        self._tab_monthly = QWidget()
        self._tab_progress_star = QWidget()
        self._tabs.addTab(self._tab_monthly, "月度综合榜")
        self._tabs.addTab(self._tab_progress_star, "进步之星")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root_layout.addWidget(self._tabs, 1)

        # ----------------------------------------------------------
        # Tab 1：月度综合榜（原页面内容）
        # ----------------------------------------------------------
        layout = QVBoxLayout(self._tab_monthly)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 筛选区域
        filter_layout = QHBoxLayout()
        
        # 月份选择
        filter_layout.addWidget(QLabel("月份选择："))
        self._month_combo = QComboBox()
        self._month_combo.setEditable(False)
        self._populate_month_combo()
        filter_layout.addWidget(self._month_combo)
        
        # 员工ID
        filter_layout.addWidget(QLabel("员工ID："))
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("留空显示所有人")
        filter_layout.addWidget(self._user_id_edit)
        
        # 工资贡献率筛选
        filter_layout.addWidget(QLabel("工资贡献率："))
        self._salary_ratio_combo = QComboBox()
        self._salary_ratio_combo.addItems(["全部", "大于100%", "等于100%", "小于100%"])
        self._salary_ratio_combo.setCurrentIndex(0)
        filter_layout.addWidget(self._salary_ratio_combo)
        
        btn_filter = QPushButton("筛选")
        btn_filter.clicked.connect(self._on_filter_clicked)
        filter_layout.addWidget(btn_filter)
        
        btn_clear = QPushButton("清除筛选")
        btn_clear.clicked.connect(self._on_clear_filter)
        filter_layout.addWidget(btn_clear)
        
        # 锁定排名按钮（动态显示月份）
        self._lock_rank_btn = QPushButton("锁定排名")
        self._lock_rank_btn.clicked.connect(self._on_lock_rank_clicked)
        # 监听月份选择变化，更新按钮文本
        self._month_combo.currentIndexChanged.connect(self._update_lock_rank_btn_text)
        self._update_lock_rank_btn_text()
        filter_layout.addWidget(self._lock_rank_btn)
        
        # 导出按钮
        btn_export = QPushButton("导出JSON")
        btn_export.clicked.connect(self._on_export_clicked)
        filter_layout.addWidget(btn_export)
        
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "月份", "员工ID", "姓名", "团队", "最终综合分", "AI综合均分", "工资贡献率", "成长率", "有效工作日"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 月份
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 员工ID
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 姓名
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 团队
        header.setSectionResizeMode(4, QHeaderView.Stretch)  # 最终综合分（可排序）
        header.setSectionResizeMode(5, QHeaderView.Stretch)  # AI综合均分（可排序）
        header.setSectionResizeMode(6, QHeaderView.Stretch)  # 工资贡献率（可排序）
        header.setSectionResizeMode(7, QHeaderView.Stretch)  # 成长率（可排序）
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)  # 有效工作日
        
        # 连接列标题点击信号
        header.sectionClicked.connect(self._on_header_clicked)
        
        # 设置列标题可点击样式
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        
        # 初始化排序指示器
        self._update_sort_indicator()
        
        layout.addWidget(self._table)
        
        # 底部状态栏
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet("color: #666; padding: 8px;")
        layout.addWidget(self._status_label)

        # ----------------------------------------------------------
        # Tab 2：进步之星（趋势型）候选列表
        # ----------------------------------------------------------
        ps_layout = QVBoxLayout(self._tab_progress_star)
        ps_layout.setContentsMargins(0, 0, 0, 0)
        ps_layout.setSpacing(12)

        ps_filter_layout = QHBoxLayout()
        ps_filter_layout.addWidget(QLabel("月份："))
        self._ps_month_combo = QComboBox()
        self._ps_month_combo.setEditable(False)
        self._populate_ps_month_combo()
        ps_filter_layout.addWidget(self._ps_month_combo)

        ps_filter_layout.addWidget(QLabel("排除TopN："))
        self._ps_exclude_top_n = QSpinBox()
        self._ps_exclude_top_n.setRange(0, 50)
        self._ps_exclude_top_n.setValue(3)
        ps_filter_layout.addWidget(self._ps_exclude_top_n)

        ps_filter_layout.addWidget(QLabel("最少工作日："))
        self._ps_min_workdays = QSpinBox()
        self._ps_min_workdays.setRange(1, 31)
        self._ps_min_workdays.setValue(10)
        ps_filter_layout.addWidget(self._ps_min_workdays)

        ps_filter_layout.addWidget(QLabel("返回候选数："))
        self._ps_limit = QSpinBox()
        self._ps_limit.setRange(1, 500)
        self._ps_limit.setValue(50)
        ps_filter_layout.addWidget(self._ps_limit)

        self._ps_refresh_btn = QPushButton("刷新")
        self._ps_refresh_btn.clicked.connect(self._on_ps_refresh_clicked)
        ps_filter_layout.addWidget(self._ps_refresh_btn)

        ps_filter_layout.addStretch()
        ps_layout.addLayout(ps_filter_layout)

        self._ps_info_label = QLabel("")
        self._ps_info_label.setStyleSheet("color: #666;")
        ps_layout.addWidget(self._ps_info_label)

        self._ps_table = QTableWidget()
        self._ps_table.setColumnCount(12)
        self._ps_table.setHorizontalHeaderLabels([
            "排名", "员工ID", "姓名", "团队", "趋势分", "斜率(分/日)", "有效工作日",
            "权重", "月初均值", "月末均值", "差值", "R²"
        ])
        self._ps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._ps_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        ps_header = self._ps_table.horizontalHeader()
        ps_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 排名
        ps_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 员工ID
        ps_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 姓名
        ps_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 团队
        ps_header.setSectionResizeMode(4, QHeaderView.Stretch)           # 趋势分
        ps_header.setSectionResizeMode(5, QHeaderView.Stretch)           # 斜率
        ps_header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 有效工作日
        ps_header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 权重
        ps_header.setSectionResizeMode(8, QHeaderView.Stretch)           # 月初均值
        ps_header.setSectionResizeMode(9, QHeaderView.Stretch)           # 月末均值
        ps_header.setSectionResizeMode(10, QHeaderView.Stretch)          # 差值
        ps_header.setSectionResizeMode(11, QHeaderView.ResizeToContents) # R²

        ps_layout.addWidget(self._ps_table)

        self._ps_status_label = QLabel("")
        self._ps_status_label.setAlignment(Qt.AlignCenter)
        self._ps_status_label.setStyleSheet("color: #666; padding: 8px;")
        ps_layout.addWidget(self._ps_status_label)
    
    def _populate_month_combo(self):
        """填充月份下拉框（从2025-11到当前月份）"""
        self._month_combo.clear()
        self._month_combo.addItem("全部", None)
        
        # 从2025年11月开始
        start_year = 2025
        start_month = 11
        
        # 当前月份
        today = date.today()
        current_year = today.year
        current_month = today.month
        
        # 生成月份列表
        year = start_year
        month = start_month
        while year < current_year or (year == current_year and month <= current_month):
            month_str = f"{year}-{month:02d}"
            display_str = f"{year}年{month:02d}月"
            self._month_combo.addItem(display_str, month_str)
            
            # 移动到下一个月
            month += 1
            if month > 12:
                month = 1
                year += 1
        
        # 默认选中当前月份（最后一个）
        if self._month_combo.count() > 1:
            self._month_combo.setCurrentIndex(self._month_combo.count() - 1)

    def _populate_ps_month_combo(self):
        """填充“进步之星”月份下拉框（从2025-11到当前月份，不含“全部”）"""
        self._ps_month_combo.clear()

        start_year = 2025
        start_month = 11

        today = date.today()
        current_year = today.year
        current_month = today.month

        year = start_year
        month = start_month
        while year < current_year or (year == current_year and month <= current_month):
            month_str = f"{year}-{month:02d}"
            display_str = f"{year}年{month:02d}月"
            self._ps_month_combo.addItem(display_str, month_str)
            month += 1
            if month > 12:
                month = 1
                year += 1

        if self._ps_month_combo.count() > 0:
            self._ps_month_combo.setCurrentIndex(self._ps_month_combo.count() - 1)

    def _on_tab_changed(self, index: int):
        """切换 Tab 时触发加载"""
        # 0=月度综合榜，1=进步之星
        if index == 1:
            # 切到“进步之星”时自动加载一次
            self._load_progress_star_with_current_filters()
    
    def _on_header_clicked(self, column: int):
        """列标题点击事件处理"""
        # 只处理可排序的列
        if column not in self._column_to_sort_field:
            return
        
        sort_field = self._column_to_sort_field[column]
        
        # 如果点击的是当前排序列，切换排序方向
        if sort_field == self._current_sort_by:
            self._current_sort_order = "asc" if self._current_sort_order == "desc" else "desc"
        else:
            # 点击新列，默认降序
            self._current_sort_by = sort_field
            self._current_sort_order = "desc"
        
        # 更新排序指示器
        self._update_sort_indicator()
        
        # 重新加载数据
        self._load_data_with_current_filters()
    
    def _update_sort_indicator(self):
        """更新列标题的排序指示器"""
        header = self._table.horizontalHeader()
        
        # 清除所有列的指示器
        for col in range(self._table.columnCount()):
            label = self._table.horizontalHeaderItem(col)
            if label:
                text = label.text()
                # 移除已有的排序指示器
                if " ▲" in text or " ▼" in text:
                    text = text.replace(" ▲", "").replace(" ▼", "")
                    label.setText(text)
        
        # 在当前排序列显示指示器
        for col, sort_field in self._column_to_sort_field.items():
            if sort_field == self._current_sort_by:
                label = self._table.horizontalHeaderItem(col)
                if label:
                    text = label.text()
                    # 移除已有的指示器
                    text = text.replace(" ▲", "").replace(" ▼", "")
                    # 添加新的指示器
                    indicator = " ▲" if self._current_sort_order == "asc" else " ▼"
                    label.setText(text + indicator)
                    break
    
    def _load_data_with_current_filters(self):
        """使用当前筛选条件加载数据"""
        # 获取月份
        month = None
        if self._month_combo.currentIndex() > 0:
            month = self._month_combo.currentData()
        
        # 获取员工ID
        user_id = self._user_id_edit.text().strip() or None
        
        # 获取工资贡献率筛选
        salary_ratio_index = self._salary_ratio_combo.currentIndex()
        salary_ratio_filter = None
        if salary_ratio_index == 1:  # 大于100%
            salary_ratio_filter = "gt100"
        elif salary_ratio_index == 2:  # 等于100%
            salary_ratio_filter = "eq100"
        elif salary_ratio_index == 3:  # 小于100%
            salary_ratio_filter = "lt100"
        
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载月度评分数据...")
        
        worker = _MonthlyScoreWorker(
            month=month,
            user_id=user_id,
            salary_ratio_filter=salary_ratio_filter,
            sort_by=self._current_sort_by,
            sort_order=self._current_sort_order
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
    
    def _on_filter_clicked(self):
        """执行筛选"""
        # 使用当前排序设置加载数据
        self._load_data_with_current_filters()

    # -----------------------------
    # 进步之星（趋势型）Tab
    # -----------------------------

    def _on_ps_refresh_clicked(self):
        """刷新进步之星列表"""
        self._load_progress_star_with_current_filters()

    def _load_progress_star_with_current_filters(self):
        """加载进步之星候选列表（按当前筛选）"""
        # 获取月份（YYYY-MM）
        month = self._ps_month_combo.currentData()
        if not month:
            # 兜底：尝试从显示文本解析
            month_text = self._ps_month_combo.currentText()
            try:
                year_str, month_str_part = month_text.replace("年", "-").replace("月", "").split("-")
                month = f"{year_str}-{month_str_part}"
            except Exception:
                month = None

        limit = int(self._ps_limit.value())
        min_workdays = int(self._ps_min_workdays.value())
        exclude_top_n = int(self._ps_exclude_top_n.value())

        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载进步之星列表...")

        self._ps_status_label.setText("加载中...")
        self._ps_info_label.setText("")

        worker = _ProgressStarWorker(
            month=month,
            limit=limit,
            min_workdays=min_workdays,
            exclude_top_n=exclude_top_n,
        )
        worker.signals.finished.connect(self._on_ps_data_loaded)
        worker.signals.error.connect(self._on_ps_error)
        self._thread_pool.start(worker)

    def _on_ps_data_loaded(self, resp: Dict[str, Any]):
        """进步之星列表加载完成"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()

        ps_status = resp.get("progress_star_status") or ""
        month_str = resp.get("month") or (self._ps_month_combo.currentData() or "")
        excluded = resp.get("excluded_user_ids") or []
        excluded_count = len(excluded) if isinstance(excluded, list) else 0

        # 顶部说明
        extra = f"排除月度综合榜TopN：{self._ps_exclude_top_n.value()}（命中 {excluded_count} 人）"
        self._ps_info_label.setText(f"月份：{month_str} | 进步之星状态：{ps_status} | {extra}")

        items = resp.get("items", [])
        winner_user_id = resp.get("winner_user_id")

        if not isinstance(items, list):
            items = []

        self._apply_ps_rows_to_table(items, winner_user_id=winner_user_id)

        if len(items) == 0:
            msg = resp.get("message") or "暂无数据"
            self._ps_status_label.setText(str(msg))
        else:
            self._ps_status_label.setText(f"共 {len(items)} 条记录")

    def _apply_ps_rows_to_table(self, items: List[Dict[str, Any]], winner_user_id: Optional[str] = None):
        """将进步之星候选列表应用到表格"""
        self._ps_table.setRowCount(0)
        self._ps_table.setRowCount(len(items))

        for row_idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            rank = int(item.get("rank") or (row_idx + 1))
            user_id = str(item.get("user_id") or "")
            name = item.get("name") or ""
            team_name = self._user_team_map.get(user_id, "") or (item.get("team_name") or "")

            trend_score = float(item.get("trend_score") or 0.0)
            slope_per_day = float(item.get("slope_per_day") or 0.0)
            workday_count = int(item.get("workday_count") or 0)
            workday_weight = float(item.get("workday_weight") or 0.0)
            head_avg = float(item.get("head_avg") or 0.0)
            tail_avg = float(item.get("tail_avg") or 0.0)
            delta_tail_head = float(item.get("delta_tail_head") or 0.0)
            r2 = float(item.get("r2") or 0.0)

            # 姓名加奖牌：冠军用🥇，第2/3用🥈🥉
            medal = ""
            if winner_user_id and str(winner_user_id) == user_id:
                medal = "🥇 "
            elif rank == 1:
                medal = "🥇 "
            elif rank == 2:
                medal = "🥈 "
            elif rank == 3:
                medal = "🥉 "
            name_text = f"{medal}{name}" if name else medal.strip()

            self._ps_table.setItem(row_idx, 0, QTableWidgetItem(str(rank)))
            self._ps_table.setItem(row_idx, 1, QTableWidgetItem(user_id))
            self._ps_table.setItem(row_idx, 2, QTableWidgetItem(name_text))
            self._ps_table.setItem(row_idx, 3, QTableWidgetItem(team_name))

            def _set_num(col: int, text: str):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignCenter)
                self._ps_table.setItem(row_idx, col, it)

            _set_num(4, f"{trend_score:.6f}")
            _set_num(5, f"{slope_per_day:.4f}")
            _set_num(6, str(workday_count))
            _set_num(7, f"{workday_weight:.3f}")
            _set_num(8, f"{head_avg:.2f}")
            _set_num(9, f"{tail_avg:.2f}")
            _set_num(10, f"{delta_tail_head:.2f}")
            _set_num(11, f"{r2:.3f}")

    def _on_ps_error(self, error: str):
        """进步之星列表加载失败"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()

        self._ps_status_label.setText(f"加载失败：{error}")
        handle_api_error(self, Exception(error), "加载失败")
    
    def _update_lock_rank_btn_text(self):
        """更新锁定排名按钮的文本"""
        month = None
        if self._month_combo.currentIndex() > 0:
            month = self._month_combo.currentData()
        
        if month:
            # 将 YYYY-MM 格式转换为中文显示
            try:
                month_date = datetime.strptime(month, "%Y-%m").date()
                month_text = month_date.strftime("%Y年%m月")
                self._lock_rank_btn.setText(f"锁定{month_text}排名")
            except:
                self._lock_rank_btn.setText("锁定排名")
        else:
            self._lock_rank_btn.setText("锁定排名")
    
    def _on_clear_filter(self):
        """清除筛选条件"""
        self._month_combo.setCurrentIndex(0)
        self._user_id_edit.clear()
        self._salary_ratio_combo.setCurrentIndex(0)
        # 重置排序为默认值
        self._current_sort_by = "final_score"
        self._current_sort_order = "desc"
        self._update_sort_indicator()
        # 清除后自动执行一次筛选
        self._on_filter_clicked()
    
    def reload_from_api(self):
        """从API重新加载数据（供主窗口调用）"""
        try:
            current_tab = self._tabs.currentIndex()
        except Exception:
            current_tab = 0
        if current_tab == 1:
            self._load_progress_star_with_current_filters()
        else:
            self._on_filter_clicked()
    
    def _load_employee_data(self):
        """加载员工数据以获取团队信息（带缓存）"""
        # 使用与employee_view相同的缓存机制
        from utils.config_manager import CONFIG_PATH

        class _DataCache:
            CACHE_DIR = CONFIG_PATH.parent / "cache"
            CACHE_EXPIRE_HOURS = 24
            
            @classmethod
            def _get_cache_path(cls, cache_key: str) -> Path:
                cls.CACHE_DIR.mkdir(exist_ok=True)
                return cls.CACHE_DIR / f"{cache_key}.json"
            
            @classmethod
            def get(cls, cache_key: str):
                cache_path = cls._get_cache_path(cache_key)
                if not cache_path.exists():
                    return None
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    cached_time_str = cache_data.get('cached_at')
                    if not cached_time_str:
                        return None
                    cached_time = datetime.fromisoformat(cached_time_str)
                    now = datetime.now()
                    if (now - cached_time).total_seconds() > cls.CACHE_EXPIRE_HOURS * 3600:
                        return None
                    return cache_data.get('data')
                except:
                    try:
                        cache_path.unlink()
                    except:
                        pass
                    return None
        
        class _EmployeeDataWorkerSignals(QObject):
            finished = Signal(dict)  # user_id -> team_name 映射
            error = Signal(str)
        
        class _EmployeeDataWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _EmployeeDataWorkerSignals()
            
            @Slot()
            def run(self):
                if not AdminApiClient.is_logged_in():
                    self.signals.finished.emit({})
                    return
                
                try:
                    client = AdminApiClient.from_config()
                    # 先尝试从缓存加载
                    cached_employees = _DataCache.get("employees")
                    if cached_employees is not None:
                        employees = cached_employees
                    else:
                        resp = client.get_employees()
                        employees = resp.get("items", []) if isinstance(resp, dict) else []
                        # 缓存员工数据
                        cache_path = _DataCache._get_cache_path("employees")
                        cache_path.parent.mkdir(exist_ok=True)
                        with open(cache_path, 'w', encoding='utf-8') as f:
                            json.dump({
                                'cached_at': datetime.now().isoformat(),
                                'data': employees
                            }, f, ensure_ascii=False, indent=2)
                    
                    # 建立 user_id -> team_name 映射
                    user_team_map = {}
                    for emp in employees:
                        user_id = str(emp.get("user_id", ""))
                        team_name = emp.get("team_name") or ""
                        if user_id:
                            user_team_map[user_id] = team_name
                    
                    self.signals.finished.emit(user_team_map)
                except Exception as e:
                    self.signals.error.emit(str(e))
        
        worker = _EmployeeDataWorker()
        worker.signals.finished.connect(self._on_employee_data_loaded)
        worker.signals.error.connect(lambda err: None)  # 静默失败，不影响主功能
        self._thread_pool.start(worker)
    
    def _on_employee_data_loaded(self, user_team_map: Dict[str, str]):
        """员工数据加载完成"""
        self._user_team_map = user_team_map
        
        # 如果表格已经有数据，需要更新团队列
        if self._table.rowCount() > 0:
            self._update_team_column()
        if getattr(self, "_ps_table", None) is not None and self._ps_table.rowCount() > 0:
            self._update_ps_team_column()
    
    def _update_team_column(self):
        """更新表格中的团队列（当员工数据加载完成后调用）"""
        for row in range(self._table.rowCount()):
            # 获取该行的员工ID（第1列，索引为1）
            user_id_item = self._table.item(row, 1)
            if user_id_item:
                user_id = user_id_item.text()
                # 获取团队名称
                team_name = self._user_team_map.get(user_id, "")
                # 更新团队列（第3列，索引为3）
                team_item = self._table.item(row, 3)
                if team_item:
                    team_item.setText(team_name)
                else:
                    # 如果团队列还没有item，创建一个
                    self._table.setItem(row, 3, QTableWidgetItem(team_name))

    def _update_ps_team_column(self):
        """更新“进步之星”表格中的团队列（当员工数据加载完成后调用）"""
        for row in range(self._ps_table.rowCount()):
            # 员工ID在第1列（索引1），团队在第3列（索引3）
            user_id_item = self._ps_table.item(row, 1)
            if not user_id_item:
                continue
            user_id = user_id_item.text()
            team_name = self._user_team_map.get(user_id, "")
            team_item = self._ps_table.item(row, 3)
            if team_item:
                # 只在有映射值时覆盖，避免把 API 自带 team_name 覆盖成空
                if team_name:
                    team_item.setText(team_name)
            else:
                self._ps_table.setItem(row, 3, QTableWidgetItem(team_name))
    
    def _on_data_loaded(self, items: List[Dict], total_count: int):
        """数据加载完成"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._is_loading = False
        self._apply_rows_to_table(items)
        
        if len(items) == 0:
            self._status_label.setText("暂无数据")
        else:
            self._status_label.setText(f"共 {total_count} 条记录")
    
    def _apply_rows_to_table(self, items: List[Dict]):
        """将数据应用到表格"""
        # 首先根据最终综合分确定排名（用于显示奖牌）
        # 创建一个字典，key是(user_id, month_str)，value是排名
        # 先统一处理month格式，确保key一致
        def get_month_key(month):
            """统一处理月份格式"""
            if isinstance(month, str):
                try:
                    month_date = datetime.strptime(month, "%Y-%m-%d").date()
                    return month_date.strftime("%Y-%m")
                except:
                    return month
            elif hasattr(month, 'strftime'):
                return month.strftime("%Y-%m")
            else:
                return str(month)
        
        ranked_items = sorted(items, key=lambda x: x.get("final_score", 0.0), reverse=True)
        rank_map = {}
        
        for idx, item in enumerate(ranked_items):
            final_score = item.get("final_score", 0.0)
            user_id = str(item.get("user_id", ""))
            month = item.get("month", "")
            month_key = get_month_key(month)
            
            # 计算排名：如果和上一个分数相同，排名相同；否则排名 = 当前索引 + 1
            if idx == 0:
                # 第一个，排名为1
                current_rank = 1
            else:
                prev_item = ranked_items[idx - 1]
                prev_score = prev_item.get("final_score", 0.0)
                if abs(final_score - prev_score) < 0.01:
                    # 分数相同，使用上一个排名
                    # 从上一个item获取排名
                    prev_user_id = str(prev_item.get("user_id", ""))
                    prev_month = prev_item.get("month", "")
                    prev_month_key = get_month_key(prev_month)
                    current_rank = rank_map.get((prev_user_id, prev_month_key), idx + 1)
                else:
                    # 分数不同，更新排名（排名 = 当前索引 + 1）
                    current_rank = idx + 1
            
            rank_map[(user_id, month_key)] = current_rank
        
        self._table.setRowCount(0)
        self._table.setRowCount(len(items))
        
        for idx, item in enumerate(items):
            month = item.get("month", "")
            month_str = get_month_key(month)
            
            user_id = str(item.get("user_id", ""))
            name = item.get("name") or ""
            # 从员工数据映射中获取团队名称，如果没有则使用API返回的team_name（如果有）
            team_name = self._user_team_map.get(user_id, "") or item.get("team_name", "")
            total_ai_month = item.get("total_ai_month", 0.0)
            salary_ratio = item.get("salary_ratio", 0.0)
            growth_rate = item.get("growth_rate", 0.0)
            final_score = item.get("final_score", 0.0)
            workday_count = item.get("workday_count", 0)
            
            # 获取排名并添加奖牌图标
            rank = rank_map.get((user_id, month_str), 0)
            medal = ""
            if rank == 1:
                medal = "🥇 "  # 金牌 emoji
            elif rank == 2:
                medal = "🥈 "  # 银牌 emoji
            elif rank == 3:
                medal = "🥉 "  # 铜牌 emoji
            
            # 设置月份、员工ID、姓名（左对齐，姓名前加奖牌）
            self._table.setItem(idx, 0, QTableWidgetItem(month_str))
            self._table.setItem(idx, 1, QTableWidgetItem(user_id))
            
            # 创建姓名单元格，设置支持emoji的字体
            name_text = f"{medal}{name}" if name else medal.strip()
            name_item = QTableWidgetItem(name_text)
            
            # 设置支持emoji的字体（跨平台）
            # 使用系统默认字体，通常已经支持emoji
            import platform
            system = platform.system()
            if system == "Darwin":  # macOS
                # macOS系统字体通常支持emoji
                font = QFont("Apple Color Emoji", 12)
            elif system == "Windows":
                font = QFont("Segoe UI Emoji", 12)
            else:  # Linux
                # Linux尝试使用Noto Color Emoji，如果不存在则使用默认字体
                font = QFont("Noto Color Emoji", 12)
                if not font.exactMatch():
                    # 如果字体不存在，使用默认字体（通常也支持emoji）
                    font = QFont()
            
            name_item.setFont(font)
            self._table.setItem(idx, 2, name_item)
            
            # 设置团队（左对齐）
            team_item = QTableWidgetItem(team_name)
            self._table.setItem(idx, 3, team_item)
            
            # 设置可排序的列（居中显示）
            # 最终综合分（列4）
            item_final = QTableWidgetItem(f"{final_score:.2f}")
            item_final.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 4, item_final)
            
            # AI综合均分（列5）
            item_ai = QTableWidgetItem(f"{total_ai_month:.2f}")
            item_ai.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 5, item_ai)
            
            # 工资贡献率：数据库存的是小数（如0.83表示83%），显示时乘以100，显示为整数（列6）
            item_salary = QTableWidgetItem(f"{int(round(salary_ratio * 100))}%")
            item_salary.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 6, item_salary)
            
            # 成长率：数据库存的是小数（如0.10表示10%），显示时乘以100，显示为整数（列7）
            item_growth = QTableWidgetItem(f"{int(round(growth_rate * 100))}%")
            item_growth.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 7, item_growth)
            
            # 设置有效工作日（居中显示）
            item_workday = QTableWidgetItem(str(workday_count))
            item_workday.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 8, item_workday)
    
    def _on_lock_rank_clicked(self):
        """锁定排名按钮点击事件"""
        # 获取当前选择的月份
        month = None
        if self._month_combo.currentIndex() > 0:
            month = self._month_combo.currentData()
        else:
            QMessageBox.warning(self, "提示", "请先选择要锁定排名的月份")
            return
        
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "确认锁定",
            f"确定要锁定 {month} 月的排名吗？\n\n锁定后将无法再提交该月最后一个工作日的复评。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("正在锁定排名...")
        
        # 在后台线程中锁定排名
        worker = _LockRankWorker(month)
        worker.signals.finished.connect(self._on_lock_rank_success)
        worker.signals.error.connect(self._on_lock_rank_error)
        self._thread_pool.start(worker)
    
    def _on_lock_rank_success(self, message: str):
        """锁定排名成功"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        Toast.show_message(self, message)
    
    def _on_lock_rank_error(self, error: str):
        """锁定排名失败"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        handle_api_error(self, Exception(error), "锁定排名失败")
    
    def _on_error(self, error: str):
        self._is_loading = False
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        self._status_label.setText(f"加载失败：{error}")
        # 使用统一的错误处理
        handle_api_error(self, Exception(error), "加载失败")
    
    def _on_export_clicked(self):
        """导出JSON按钮点击事件"""
        # 获取当前筛选条件
        month = None
        if self._month_combo.currentIndex() > 0:
            month = self._month_combo.currentData()
        
        user_id = self._user_id_edit.text().strip() or None
        
        salary_ratio_index = self._salary_ratio_combo.currentIndex()
        salary_ratio_filter = None
        if salary_ratio_index == 1:  # 大于100%
            salary_ratio_filter = "gt100"
        elif salary_ratio_index == 2:  # 等于100%
            salary_ratio_filter = "eq100"
        elif salary_ratio_index == 3:  # 小于100%
            salary_ratio_filter = "lt100"
        
        # 生成默认文件名
        filename = "monthly_scores"
        if month:
            filename = f"monthly_scores_{month}"
        else:
            filename = "monthly_scores_all"
        if user_id:
            filename += f"_{user_id}"
        filename += ".json"
        
        # 选择保存路径
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出月度评分数据",
            filename,
            "JSON Files (*.json);;All Files (*)"
        )
        
        if not save_path:
            return  # 用户取消
        
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("正在导出数据...")
        
        # 保存路径到实例变量，以便在回调中使用
        self._export_save_path = save_path
        
        # 后台导出
        worker = _ExportMonthlyScoreWorker(
            month=month,
            user_id=user_id,
            salary_ratio_filter=salary_ratio_filter,
            sort_by=self._current_sort_by,
            sort_order=self._current_sort_order
        )
        worker.signals.finished.connect(self._on_export_data_ready)
        worker.signals.error.connect(self._on_export_error)
        self._thread_pool.start(worker)
    
    def _on_export_data_ready(self, items: List[Dict]):
        """导出数据准备完成"""
        if not hasattr(self, '_export_save_path'):
            main_window = self.window()
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            QMessageBox.warning(self, "导出失败", "保存路径丢失")
            return
        
        save_path = self._export_save_path
        # 清理临时变量
        if hasattr(self, '_export_save_path'):
            delattr(self, '_export_save_path')
        self._on_export_success(items, save_path)
    
    def _on_export_success(self, items: List[Dict], save_path: str):
        """导出成功"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        try:
            # 构建导出数据
            export_data = {
                "export_time": datetime.now().isoformat(),
                "total_count": len(items),
                "filters": {
                    "month": self._month_combo.currentData() if self._month_combo.currentIndex() > 0 else None,
                    "user_id": self._user_id_edit.text().strip() or None,
                    "salary_ratio_filter": self._salary_ratio_combo.currentText()
                },
                "sort": {
                    "sort_by": self._current_sort_by,
                    "sort_order": self._current_sort_order
                },
                "data": []
            }
            
            # 格式化数据
            for item in items:
                month = item.get("month", "")
                # 统一月份格式
                if isinstance(month, str):
                    try:
                        month_date = datetime.strptime(month, "%Y-%m-%d").date()
                        month_str = month_date.strftime("%Y-%m")
                    except:
                        try:
                            # 尝试其他格式
                            month_date = datetime.strptime(month, "%Y-%m").date()
                            month_str = month_date.strftime("%Y-%m")
                        except:
                            month_str = month
                elif hasattr(month, 'strftime'):
                    month_str = month.strftime("%Y-%m")
                else:
                    month_str = str(month)
                
                export_item = {
                    "month": month_str,
                    "user_id": str(item.get("user_id", "")),
                    "name": item.get("name") or "",
                    "team_name": item.get("team_name") or "",
                    "total_ai_month": float(item.get("total_ai_month", 0.0)),
                    "salary_ratio": float(item.get("salary_ratio", 0.0)),
                    "growth_rate": float(item.get("growth_rate", 0.0)),
                    "final_score": float(item.get("final_score", 0.0)),
                    "workday_count": int(item.get("workday_count", 0))
                }
                export_data["data"].append(export_item)
            
            # 保存JSON文件
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            
            Toast.show_message(self, f"导出成功：{len(items)} 条记录已保存到 {save_path}")
        except Exception as e:
            import traceback
            error_msg = f"保存文件失败：{e}\n{traceback.format_exc()}"
            QMessageBox.critical(self, "导出失败", error_msg)
    
    def _on_export_error(self, error: str):
        """导出失败"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        # 清理临时变量
        if hasattr(self, '_export_save_path'):
            delattr(self, '_export_save_path')
        
        # 显示详细错误信息
        QMessageBox.critical(self, "导出失败", f"导出数据失败：\n{error}")

