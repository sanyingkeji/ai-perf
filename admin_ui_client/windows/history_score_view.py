#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史评分管理页面：
- 支持按日期和员工筛选
- 显示所有员工的历史评分数据
- 查看原始输入数据和输出数据（包含复评）
- 重新拉取指定员工的原始输入数据
"""

import json
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QDialog,
    QTextEdit, QHeaderView, QDateEdit, QLineEdit, QMessageBox,
    QAbstractItemView, QTabWidget
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast


class _HistoryScoreWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count (items on current page)
    error = Signal(str)


class _HistoryScoreWorker(QRunnable):
    """后台加载历史评分数据"""
    def __init__(
        self, 
        date_str: Optional[str] = None, 
        user_id: Optional[str] = None, 
        offset: int = 0,
        limit: int = 50
    ):
        super().__init__()
        self._date_str = date_str
        self._user_id = user_id
        self._offset = offset
        self._limit = limit
        self.signals = _HistoryScoreWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
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
            resp = client.get_history_scores(
                date_str=self._date_str,
                user_id=self._user_id,
                offset=self._offset,
                limit=self._limit
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total_count = len(items)
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载历史评分失败：{e}")


class _DataViewWorkerSignals(QObject):
    finished = Signal(str)  # JSON text
    error = Signal(str)


class _DataViewWorker(QRunnable):
    """后台加载数据（支持多种数据类型）"""
    def __init__(self, client: AdminApiClient, data_type: str, date_str: str, user_id: str):
        super().__init__()
        self._client = client
        self._data_type = data_type  # "input", "output", "review_input", "review_result"
        self._date_str = date_str
        self._user_id = user_id
        self.signals = _DataViewWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self._data_type == "input":
                resp = self._client.get_daily_snapshot(self._date_str, self._user_id)
                data = resp.get("snapshot", {}) if isinstance(resp, dict) else resp
            elif self._data_type == "output":
                resp = self._client.get_daily_output(self._date_str, self._user_id)
                data = resp.get("result", {}) if isinstance(resp, dict) else resp
            elif self._data_type == "review_input":
                resp = self._client.get_review_input(self._date_str, self._user_id)
                data = resp.get("result", {}) if isinstance(resp, dict) else resp
            elif self._data_type == "review_result":
                resp = self._client.get_review_result(self._date_str, self._user_id)
                data = resp.get("result", {}) if isinstance(resp, dict) else resp
            else:
                data = {"error": f"未知的数据类型: {self._data_type}"}
            
            text = json.dumps(data, ensure_ascii=False, indent=2)
            self.signals.finished.emit(text)
        except Exception as e:
            self.signals.error.emit(f"加载数据失败：{e}")


class _RerunEtlWorkerSignals(QObject):
    finished = Signal(str)  # success message
    error = Signal(str)


class _RerunEtlWorker(QRunnable):
    """后台重新拉取数据"""
    def __init__(self, date_str: str, user_id: str):
        super().__init__()
        self._date_str = date_str
        self._user_id = user_id
        self.signals = _RerunEtlWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
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
            resp = client.rerun_etl(self._date_str, self._user_id)
            message = resp.get("message", "操作成功") if isinstance(resp, dict) else "操作成功"
            self.signals.finished.emit(message)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"重新拉取数据失败：{e}")


class AllDataViewDialog(QDialog):
    """所有数据查看对话框（使用Tab标签页）"""
    def __init__(self, parent, date_str: str, user_id: str, user_name: str, is_reviewed: bool = False):
        super().__init__(parent)
        self._date_str = date_str
        self._user_id = user_id
        self._user_name = user_name
        self._is_reviewed = is_reviewed  # 复评状态
        
        title = f"{user_name} ({user_id}) - {date_str}"
        self.setWindowTitle(f"{title} - 数据查看")
        
        self.resize(900, 700)
        
        layout = QVBoxLayout(self)
        
        # Tab标签页
        self._tab_widget = QTabWidget()
        
        # Tab 1: 原始输入数据
        self._input_tab = QTextEdit()
        self._input_tab.setReadOnly(True)
        self._input_tab.setFont(QFont("Consolas", 10))
        self._input_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._input_tab, "原始输入数据")
        
        # Tab 2: AI返回结果
        self._output_tab = QTextEdit()
        self._output_tab.setReadOnly(True)
        self._output_tab.setFont(QFont("Consolas", 10))
        self._output_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._output_tab, "AI返回结果")
        
        # Tab 3: 员工复评内容
        self._review_input_tab = QTextEdit()
        self._review_input_tab.setReadOnly(True)
        self._review_input_tab.setFont(QFont("Consolas", 10))
        self._review_input_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._review_input_tab, "员工复评内容")
        
        # Tab 4: 员工复评结果
        self._review_result_tab = QTextEdit()
        self._review_result_tab.setReadOnly(True)
        self._review_result_tab.setFont(QFont("Consolas", 10))
        self._review_result_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._review_result_tab, "员工复评结果")
        
        layout.addWidget(self._tab_widget)
        
        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)
        
        # 异步加载所有数据
        self._load_all_data()
    
    def _load_all_data(self):
        """异步加载所有Tab的数据"""
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            error_text = "未登录，请先登录"
            self._input_tab.setPlainText(error_text)
            self._output_tab.setPlainText(error_text)
            self._review_input_tab.setPlainText(error_text)
            self._review_result_tab.setPlainText(error_text)
            return
        try:
            client = AdminApiClient.from_config()
        except Exception as e:
            error_text = f"初始化客户端失败：{e}"
            self._input_tab.setPlainText(error_text)
            self._output_tab.setPlainText(error_text)
            self._review_input_tab.setPlainText(error_text)
            self._review_result_tab.setPlainText(error_text)
            return
        
        # 加载原始输入数据
        worker1 = _DataViewWorker(client, "input", self._date_str, self._user_id)
        worker1.signals.finished.connect(self._on_input_loaded)
        worker1.signals.error.connect(lambda err: self._on_tab_error(self._input_tab, err))
        QThreadPool.globalInstance().start(worker1)
        
        # 加载AI返回结果
        worker2 = _DataViewWorker(client, "output", self._date_str, self._user_id)
        worker2.signals.finished.connect(self._on_output_loaded)
        worker2.signals.error.connect(lambda err: self._on_tab_error(self._output_tab, err))
        QThreadPool.globalInstance().start(worker2)
        
        # 根据复评状态决定是否加载复评相关数据
        if self._is_reviewed:
            # 已复评，加载复评内容
            worker3 = _DataViewWorker(client, "review_input", self._date_str, self._user_id)
            worker3.signals.finished.connect(self._on_review_input_loaded)
            worker3.signals.error.connect(lambda err: self._on_tab_error(self._review_input_tab, err))
            QThreadPool.globalInstance().start(worker3)
            
            # 加载员工复评结果
            worker4 = _DataViewWorker(client, "review_result", self._date_str, self._user_id)
            worker4.signals.finished.connect(self._on_review_result_loaded)
            worker4.signals.error.connect(lambda err: self._on_tab_error(self._review_result_tab, err))
            QThreadPool.globalInstance().start(worker4)
        else:
            # 未复评，直接显示提示信息，不请求API
            self._review_input_tab.setPlainText("未提交复评")
            self._review_result_tab.setPlainText("未提交复评")
    
    def _on_input_loaded(self, text: str):
        self._input_tab.setPlainText(text)
    
    def _on_output_loaded(self, text: str):
        self._output_tab.setPlainText(text)
    
    def _on_review_input_loaded(self, text: str):
        self._review_input_tab.setPlainText(text)
    
    def _on_review_result_loaded(self, text: str):
        self._review_result_tab.setPlainText(text)
    
    def _on_tab_error(self, tab: QTextEdit, error: str):
        """处理Tab数据加载错误"""
        error_msg = str(error)
        if error_msg.startswith("HTTP_ERROR_DETAIL:"):
            detail = error_msg.replace("HTTP_ERROR_DETAIL:", "", 1).strip()
            tab.setPlainText(f"错误：{detail}")
        else:
            tab.setPlainText(f"错误：{error}")


class DataViewDialog(QDialog):
    """数据查看对话框（保留用于兼容）"""
    def __init__(self, parent, data_type: str, date_str: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._data_type = data_type
        self._date_str = date_str
        self._user_id = user_id
        
        title = f"{user_name} ({user_id}) - {date_str}"
        if data_type == "input":
            self.setWindowTitle(f"{title} - 原始输入数据")
        else:
            self.setWindowTitle(f"{title} - 输出数据（含复评）")
        
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Consolas", 10))
        self._text_edit.setPlaceholderText("加载中...")
        layout.addWidget(self._text_edit)
        
        btn_layout = QHBoxLayout()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)
        
        # 异步加载数据
        self._load_data()
    
    def _load_data(self):
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            self._text_edit.setPlainText("未登录，请先登录")
            return
        try:
            client = AdminApiClient.from_config()
        except Exception as e:
            self._text_edit.setPlainText(f"初始化客户端失败：{e}")
            return
        
        worker = _DataViewWorker(client, self._data_type, self._date_str, self._user_id)
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_data_loaded(self, text: str):
        self._text_edit.setPlainText(text)
    
    def _on_error(self, error: str):
        # 在对话框中也使用统一的错误处理
        error_msg = str(error)
        if error_msg.startswith("HTTP_ERROR_DETAIL:"):
            detail = error_msg.replace("HTTP_ERROR_DETAIL:", "", 1).strip()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "错误", detail)
            self._text_edit.setPlainText(f"错误：{detail}")
        else:
            self._text_edit.setPlainText(f"错误：{error}")


class HistoryScoreView(QWidget):
    def __init__(self):
        super().__init__()
        
        # 分页相关状态
        self._current_offset = 0
        self._page_size = 50
        self._is_loading = False
        self._has_more = True
        self._current_filters = {}  # 保存当前筛选条件
        
        self._setup_ui()
        self._thread_pool = QThreadPool.globalInstance()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 标题
        title = QLabel("历史评分管理")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(title)
        
        # 筛选区域
        filter_layout = QHBoxLayout()
        
        filter_layout.addWidget(QLabel("日期筛选："))
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._date_edit)
        filter_layout.addWidget(self._date_edit)
        
        filter_layout.addWidget(QLabel("员工ID："))
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("留空显示所有人")
        filter_layout.addWidget(self._user_id_edit)
        
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
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "日期", "员工ID", "姓名", "总分", "执行/质/协/思", "排名", "复评状态", "备注", "操作"
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 监听滚动事件，实现无限滚动
        scroll_bar = self._table.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._on_scroll_changed)
        
        layout.addWidget(self._table)
        
        # 底部状态栏（显示加载状态和"没有更多数据"）
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet("color: #666; padding: 8px;")
        layout.addWidget(self._status_label)
    
    def _on_scroll_changed(self, value: int):
        """滚动事件处理：当滚动到底部时自动加载更多"""
        if self._is_loading or not self._has_more:
            return
        
        scroll_bar = self._table.verticalScrollBar()
        # 当滚动到距离底部50像素以内时，触发加载
        if value >= scroll_bar.maximum() - 50:
            self._load_more()

    def _load_more(self):
        """加载更多数据"""
        if self._is_loading or not self._has_more:
            return
        
        self._is_loading = True
        self._status_label.setText("加载中...")
        
        worker = _HistoryScoreWorker(
            date_str=self._current_filters.get("date_str"),
            user_id=self._current_filters.get("user_id"),
            offset=self._current_offset,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_more_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)

    def _on_filter_clicked(self):
        """执行筛选（重置分页）"""
        date_str = None
        if self._date_edit.date() != QDate.currentDate():
            date_str = self._date_edit.date().toString("yyyy-MM-dd")
        
        user_id = self._user_id_edit.text().strip() or None
        
        # 保存筛选条件
        self._current_filters = {
            "date_str": date_str,
            "user_id": user_id,
        }
        
        # 重置分页状态
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)  # 清空表格
        
        # 显示加载中
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载历史评分数据...")
        
        worker = _HistoryScoreWorker(
            date_str=date_str,
            user_id=user_id,
            offset=0,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
    
    def _on_clear_filter(self):
        """清除筛选条件"""
        self._date_edit.setDate(QDate.currentDate())
        self._user_id_edit.clear()
        # 清除后自动执行一次筛选
        self._on_filter_clicked()
    
    def reload_from_api(self):
        """从API重新加载数据（供主窗口调用，重置分页）"""
        self._on_filter_clicked()
    
    def _on_data_loaded(self, items: List[Dict], total_count: int):
        """首次数据加载完成"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._is_loading = False
        self._apply_rows_to_table(items, append=False)
        
        # 更新分页状态：offset 应该是已加载的数据条数
        self._current_offset = len(items)
        # 如果返回的数据量等于 page_size，说明可能还有更多数据
        self._has_more = len(items) >= self._page_size
        
        if not self._has_more and len(items) > 0:
            self._status_label.setText(f"已加载全部数据（共 {self._current_offset} 条）")
        elif len(items) == 0:
            self._status_label.setText("暂无数据")
        else:
            self._status_label.setText(f"已加载 {self._current_offset} 条，滚动到底部加载更多")

    def _on_more_data_loaded(self, items: List[Dict], total_count: int):
        """加载更多数据完成"""
        self._is_loading = False
        self._apply_rows_to_table(items, append=True)
        
        # 更新分页状态：offset 应该是已加载的数据条数（累加）
        self._current_offset += len(items)
        # 如果返回的数据量小于 page_size，说明没有更多数据了
        self._has_more = len(items) >= self._page_size
        
        if not self._has_more:
            self._status_label.setText(f"已加载全部数据（共 {self._current_offset} 条）")
        else:
            self._status_label.setText(f"已加载 {self._current_offset} 条，滚动到底部加载更多")

    def _apply_rows_to_table(self, items: List[Dict], append: bool = False):
        """将数据应用到表格"""
        if not append:
            self._table.setRowCount(0)
        
        start_row = self._table.rowCount()
        self._table.setRowCount(start_row + len(items))
        
        for idx, item in enumerate(items):
            row = start_row + idx
            
            date_str = str(item.get("date", ""))
            user_id = str(item.get("user_id", ""))
            name = item.get("name") or ""
            total_ai = item.get("total_ai", 0)
            execution = item.get("execution", 0)
            quality = item.get("quality", 0)
            collaboration = item.get("collaboration", 0)
            reflection = item.get("reflection", 0)
            rank = item.get("rank")
            is_reviewed = item.get("is_reviewed", False)
            eligible = item.get("eligible", 1)
            reason = item.get("reason") or ""
            
            dims_text = f"{execution}/{quality}/{collaboration}/{reflection}"
            rank_text = f"第{rank}名" if rank else "未锁定"
            review_status = "已复评" if is_reviewed else "自动评分"
            remark = "参与评优" if eligible == 1 else f"不参与（{reason}）"
            
            self._table.setItem(row, 0, QTableWidgetItem(date_str))
            self._table.setItem(row, 1, QTableWidgetItem(user_id))
            self._table.setItem(row, 2, QTableWidgetItem(name))
            self._table.setItem(row, 3, QTableWidgetItem(str(total_ai)))
            self._table.setItem(row, 4, QTableWidgetItem(dims_text))
            self._table.setItem(row, 5, QTableWidgetItem(rank_text))
            self._table.setItem(row, 6, QTableWidgetItem(review_status))
            self._table.setItem(row, 7, QTableWidgetItem(remark))
            
            # 操作下拉框（合并查看输入和查看输出为"查看"）
            action_combo = QComboBox()
            action_combo.addItems(["选择操作", "查看", "重新拉取"])
            action_combo.setCurrentIndex(0)  # 默认选中"选择操作"
            action_combo.setFixedWidth(100)  # 设置固定宽度，不要撑满
            
            # 存储当前行的数据，用于回调
            action_combo.setProperty("date_str", date_str)
            action_combo.setProperty("user_id", user_id)
            action_combo.setProperty("user_name", name)
            action_combo.setProperty("is_reviewed", is_reviewed)  # 存储复评状态
            
            # 连接信号
            action_combo.currentTextChanged.connect(
                lambda text, combo=action_combo: self._on_action_selected(text, combo)
            )
            
            self._table.setCellWidget(row, 8, action_combo)
    
    def _on_error(self, error: str):
        self._is_loading = False
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        self._status_label.setText(f"加载失败：{error}")
        # 使用统一的错误处理，如果是 detail 错误会用弹出框显示
        handle_api_error(self, Exception(error), "加载失败")
    
    def _on_action_selected(self, text: str, combo: QComboBox):
        """处理操作下拉框选择"""
        if text == "选择操作":
            return  # 忽略默认选项
        
        date_str = combo.property("date_str")
        user_id = combo.property("user_id")
        user_name = combo.property("user_name")
        is_reviewed = combo.property("is_reviewed")  # 获取复评状态
        
        # 重置下拉框到默认选项
        combo.setCurrentIndex(0)
        
        # 根据选择执行相应操作
        if text == "查看":
            self._show_all_data(date_str, user_id, user_name, is_reviewed)
        elif text == "重新拉取":
            self._rerun_etl(date_str, user_id)
    
    def _show_all_data(self, date_str: str, user_id: str, user_name: str, is_reviewed: bool):
        """显示所有数据（使用Tab标签页）"""
        dlg = AllDataViewDialog(self, date_str, user_id, user_name, is_reviewed)
        dlg.exec()
    
    def _rerun_etl(self, date_str: str, user_id: str):
        reply = QMessageBox.question(
            self,
            "确认重新拉取",
            f"确定要重新拉取 {user_id} 在 {date_str} 的原始输入数据吗？\n这可能需要较长时间。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("正在重新拉取数据，请稍候...")
        
        worker = _RerunEtlWorker(date_str, user_id)
        worker.signals.finished.connect(lambda msg: self._on_rerun_success(msg, main_window))
        worker.signals.error.connect(lambda err: self._on_rerun_error(err, main_window))
        self._thread_pool.start(worker)
    
    def _on_rerun_success(self, message: str, main_window):
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        Toast.show_message(self, message)
        # 重新加载数据
        self.reload_from_api()
    
    def _on_rerun_error(self, error: str, main_window):
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        # 使用统一的错误处理，如果是 detail 错误会用弹出框显示
        handle_api_error(self, Exception(error), "重新拉取失败")
    

