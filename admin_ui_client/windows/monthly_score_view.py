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

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView,
    QLineEdit, QAbstractItemView
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


class MonthlyScoreView(QWidget):
    def __init__(self):
        super().__init__()
        
        self._is_loading = False
        self._current_filters = {}  # 保存当前筛选条件
        self._current_sort_by = "final_score"  # 当前排序字段
        self._current_sort_order = "desc"  # 当前排序方向
        
        # 列索引到排序字段的映射
        self._column_to_sort_field = {
            3: "total_ai_month",  # AI综合均分
            4: "salary_ratio",    # 工资贡献率
            5: "growth_rate",     # 成长率
            6: "final_score",     # 最终综合分
        }
        
        self._setup_ui()
        self._thread_pool = QThreadPool.globalInstance()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 标题
        title = QLabel("月度评分管理")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(title)
        
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
        
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "月份", "员工ID", "姓名", "AI综合均分", "工资贡献率", "成长率", "最终综合分", "有效工作日"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 月份
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 员工ID
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 姓名
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # AI综合均分（可排序）
        header.setSectionResizeMode(4, QHeaderView.Stretch)  # 工资贡献率（可排序）
        header.setSectionResizeMode(5, QHeaderView.Stretch)  # 成长率（可排序）
        header.setSectionResizeMode(6, QHeaderView.Stretch)  # 最终综合分（可排序）
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 有效工作日
        
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
    
    def _populate_month_combo(self):
        """填充月份下拉框（最近12个月）"""
        self._month_combo.clear()
        self._month_combo.addItem("全部", None)
        
        today = date.today()
        for i in range(12):
            # 从当前月份往前推
            month_date = date(today.year, today.month, 1)
            # 往前推 i 个月
            if i > 0:
                if month_date.month <= i:
                    month_date = date(month_date.year - 1, 12 - (i - month_date.month), 1)
                else:
                    month_date = date(month_date.year, month_date.month - i, 1)
            
            month_str = month_date.strftime("%Y-%m")
            display_str = month_date.strftime("%Y年%m月")
            self._month_combo.addItem(display_str, month_str)
    
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
        self._on_filter_clicked()
    
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
            
            # 设置可排序的列（居中显示）
            item_ai = QTableWidgetItem(f"{total_ai_month:.2f}")
            item_ai.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 3, item_ai)
            
            # 工资贡献率：数据库存的是小数（如0.83表示83%），显示时乘以100，显示为整数
            item_salary = QTableWidgetItem(f"{int(round(salary_ratio * 100))}%")
            item_salary.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 4, item_salary)
            
            # 成长率：数据库存的是小数（如0.10表示10%），显示时乘以100，显示为整数
            item_growth = QTableWidgetItem(f"{int(round(growth_rate * 100))}%")
            item_growth.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 5, item_growth)
            
            item_final = QTableWidgetItem(f"{final_score:.2f}")
            item_final.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 6, item_final)
            
            # 设置有效工作日（居中显示）
            item_workday = QTableWidgetItem(str(workday_count))
            item_workday.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(idx, 7, item_workday)
    
    def _on_error(self, error: str):
        self._is_loading = False
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        self._status_label.setText(f"加载失败：{error}")
        # 使用统一的错误处理
        handle_api_error(self, Exception(error), "加载失败")

