#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
team_member_history_view.py

组长查看组员历史评分页面：
- 显示该组所有成员的历史评分
- 包含员工姓名、邮箱等信息
- 支持查看详细数据
"""

import json
from typing import List, Tuple, Any, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QFrame, QAbstractItemView,
    QPushButton, QDialog, QTextEdit, QHeaderView, QTabWidget
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot

from utils.api_client import ApiClient, ApiError, AuthError
from widgets.toast import Toast


class _TeamMemberHistoryWorkerSignals(QObject):
    finished = Signal(dict)  # Dict with is_leader, team_name, items
    error = Signal(str)


class _TeamMemberHistoryWorker(QRunnable):
    """在后台线程里调用 /api/team_member_history_scores"""
    def __init__(self, limit: int):
        super().__init__()
        self._limit = int(limit)
        self.signals = _TeamMemberHistoryWorkerSignals()

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not ApiClient.is_logged_in():
            self.signals.error.emit("需要先登录")
            return
        
        try:
            client = ApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.get_team_member_history_scores(limit=self._limit)
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载组员历史记录失败：{e}")


class TeamMemberHistoryView(QWidget):
    """组长查看组员历史评分页面"""
    def __init__(self):
        super().__init__()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        # 标题
        title = QLabel("组员历史评分")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)
        
        # 团队信息提示
        self._team_info_label = QLabel("")
        self._team_info_label.setStyleSheet("color: #666; font-size: 12pt;")
        layout.addWidget(self._team_info_label)
        
        # 筛选区域
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)
        
        limit_label = QLabel("显示范围：")
        self._limit_combo = QComboBox()
        self._limit_combo.addItems(["最近 7 天", "最近 30 天", "最近 90 天"])
        self._limit_combo.setCurrentIndex(1)  # 默认30天
        self._limit_combo.currentIndexChanged.connect(self._on_limit_changed)
        
        filter_layout.addWidget(limit_label)
        filter_layout.addWidget(self._limit_combo)
        filter_layout.addStretch()
        
        filter_frame.setProperty("class", "card")
        layout.addWidget(filter_frame)
        
        # 表格
        self._table = QTableWidget()
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels([
            "日期", "员工ID", "姓名", "邮箱", "总分", "执行", "质量", "协作", "思考", "排名", "操作"
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self._table)
        
        # 线程池
        self._thread_pool = QThreadPool.globalInstance()
        
        # 初始加载
        self._load_data()
    
    def _on_limit_changed(self):
        """范围改变时重新加载"""
        self._load_data()
    
    def _load_data(self):
        """加载组员历史评分数据"""
        limit_map = {0: 7, 1: 30, 2: 90}
        limit = limit_map.get(self._limit_combo.currentIndex(), 30)
        
        # 显示加载中
        self._table.setRowCount(0)
        loading_label = QLabel("加载中…")
        loading_label.setAlignment(Qt.AlignCenter)
        self._table.setCellWidget(0, 0, loading_label)
        
        worker = _TeamMemberHistoryWorker(limit=limit)
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        self._thread_pool.start(worker)
    
    def _on_data_loaded(self, resp: Dict[str, Any]):
        """数据加载完成"""
        self._table.setRowCount(0)
        
        status = resp.get("status", "error")
        is_leader = resp.get("is_leader", False)
        team_name = resp.get("team_name")
        items = resp.get("items", [])
        message = resp.get("message")
        
        if status == "error" or not is_leader:
            error_msg = message or "您不是组长，无权查看组员历史评分"
            self._team_info_label.setText(f"⚠️ {error_msg}")
            self._team_info_label.setStyleSheet("color: #d32f2f; font-size: 12pt;")
            Toast.show_message(self, error_msg)
            return
        
        # 显示团队信息
        if team_name:
            self._team_info_label.setText(f"团队：{team_name}")
            self._team_info_label.setStyleSheet("color: #4a90e2; font-size: 12pt; font-weight: bold;")
        else:
            self._team_info_label.setText("")
        
        # 填充表格
        for item in items:
            row = self._table.rowCount()
            self._table.insertRow(row)
            
            date_str = str(item.get("date", ""))
            user_id = str(item.get("user_id", ""))
            name = item.get("name") or ""
            email = item.get("email") or ""
            total_ai = item.get("total_ai", 0)
            execution = item.get("execution")
            quality = item.get("quality")
            collaboration = item.get("collaboration")
            reflection = item.get("reflection")
            rank = item.get("rank")
            
            # 设置单元格
            items_data = [
                (0, date_str, Qt.AlignCenter),
                (1, user_id, Qt.AlignCenter),
                (2, name, Qt.AlignCenter),
                (3, email, Qt.AlignLeft),
                (4, str(total_ai), Qt.AlignCenter),
                (5, str(execution) if execution is not None else "-", Qt.AlignCenter),
                (6, str(quality) if quality is not None else "-", Qt.AlignCenter),
                (7, str(collaboration) if collaboration is not None else "-", Qt.AlignCenter),
                (8, str(reflection) if reflection is not None else "-", Qt.AlignCenter),
                (9, str(rank) if rank is not None else "-", Qt.AlignCenter),
            ]
            
            for col, text, alignment in items_data:
                cell_item = QTableWidgetItem(text)
                cell_item.setTextAlignment(alignment)
                self._table.setItem(row, col, cell_item)
            
            # 操作按钮
            view_btn = QPushButton("查看")
            view_btn.setFixedWidth(60)
            view_btn.clicked.connect(
                lambda checked, d=date_str, uid=user_id: self._on_view_clicked(d, uid)
            )
            self._table.setCellWidget(row, 10, view_btn)
    
    def _on_error(self, error_msg: str):
        """加载失败"""
        self._table.setRowCount(0)
        self._team_info_label.setText(f"⚠️ {error_msg}")
        self._team_info_label.setStyleSheet("color: #d32f2f; font-size: 12pt;")
        Toast.show_message(self, error_msg)
    
    def _on_view_clicked(self, date_str: str, user_id: str):
        """查看详情"""
        dialog = _DataViewDialog(self, date_str, user_id)
        dialog.exec_()


class _DataViewDialog(QDialog):
    """数据查看对话框（显示原始输入、AI输出、复评内容、复评结果）"""
    def __init__(self, parent, date_str: str, user_id: str):
        super().__init__(parent)
        self._date_str = date_str
        self._user_id = user_id
        
        self.setWindowTitle(f"查看数据 - {user_id} - {date_str}")
        self.resize(900, 700)
        
        layout = QVBoxLayout(self)
        
        # Tab 页面
        tabs = QTabWidget()
        
        # 原始输入数据
        self._input_tab = QTextEdit()
        self._input_tab.setReadOnly(True)
        self._input_tab.setFont(QFont("Consolas", 10))
        tabs.addTab(self._input_tab, "原始输入数据")
        
        # AI返回结果
        self._output_tab = QTextEdit()
        self._output_tab.setReadOnly(True)
        self._output_tab.setFont(QFont("Consolas", 10))
        tabs.addTab(self._output_tab, "AI返回结果")
        
        # 复评内容
        self._review_input_tab = QTextEdit()
        self._review_input_tab.setReadOnly(True)
        self._review_input_tab.setFont(QFont("Consolas", 10))
        tabs.addTab(self._review_input_tab, "复评内容")
        
        # 复评结果
        self._review_result_tab = QTextEdit()
        self._review_result_tab.setReadOnly(True)
        self._review_result_tab.setFont(QFont("Consolas", 10))
        tabs.addTab(self._review_result_tab, "复评结果")
        
        layout.addWidget(tabs)
        
        # 关闭按钮
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        
        # 加载数据
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        # 检查登录状态（版本升级除外）
        if not ApiClient.is_logged_in():
            self._input_tab.setText("未登录，请先登录")
            return
        
        try:
            client = ApiClient.from_config()
        except Exception as e:
            self._input_tab.setText(f"加载失败：{e}")
            return
        
        # 加载原始输入数据
        try:
            snapshot = client.get_daily_snapshot(date_str=self._date_str, user_id=self._user_id)
            if isinstance(snapshot, dict) and snapshot.get("status") == "success":
                data = snapshot.get("snapshot", {})
                self._input_tab.setText(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                self._input_tab.setText("暂无数据")
        except Exception as e:
            self._input_tab.setText(f"加载失败：{e}")
        
        # 加载AI输出结果
        try:
            score = client.get_daily_score(date_str=self._date_str, user_id=self._user_id)
            if score:
                self._output_tab.setText(json.dumps(score, ensure_ascii=False, indent=2))
            else:
                self._output_tab.setText("暂无数据")
        except Exception as e:
            self._output_tab.setText(f"加载失败：{e}")
        
        # 加载复评数据（如果有）
        try:
            review_status = client.get_review_status(date_str=self._date_str)
            if isinstance(review_status, dict) and review_status.get("is_reviewed"):
                review_input = review_status.get("review_input_json")
                review_result = review_status.get("review_result")
                
                if review_input:
                    self._review_input_tab.setText(json.dumps(review_input, ensure_ascii=False, indent=2))
                else:
                    self._review_input_tab.setText("暂无数据")
                
                if review_result:
                    self._review_result_tab.setText(json.dumps(review_result, ensure_ascii=False, indent=2))
                else:
                    self._review_result_tab.setText("暂无数据")
            else:
                self._review_input_tab.setText("未提交复评")
                self._review_result_tab.setText("未提交复评")
        except Exception as e:
            self._review_input_tab.setText(f"加载失败：{e}")
            self._review_result_tab.setText(f"加载失败：{e}")

