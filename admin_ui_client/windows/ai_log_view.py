#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI调用审计日志页面：
- 支持按日期范围、用户ID、模型、成功状态筛选
- 显示AI调用详情
- 查看请求和响应JSON
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


class _AiLogWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count
    error = Signal(str)


class _AiLogWorker(QRunnable):
    """后台加载AI日志数据"""
    def __init__(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
        model: Optional[str] = None,
        ok: Optional[bool] = None,
        offset: int = 0,
        limit: int = 50
    ):
        super().__init__()
        self._start_date = start_date
        self._end_date = end_date
        self._user_id = user_id
        self._model = model
        self._ok = ok
        self._offset = offset
        self._limit = limit
        self.signals = _AiLogWorkerSignals()

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
            resp = client.get_ai_run_logs(
                start_date=self._start_date,
                end_date=self._end_date,
                user_id=self._user_id,
                model=self._model,
                ok=self._ok,
                offset=self._offset,
                limit=self._limit
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total_count = len(items)
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载AI日志失败：{e}")


class AiLogView(QWidget):
    def __init__(self):
        super().__init__()
        
        # 分页相关状态
        self._current_offset = 0
        self._page_size = 50
        self._is_loading = False
        self._has_more = True
        self._current_filters = {}  # 保存当前筛选条件

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("AI调用审计日志")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # 筛选区域
        filter_frame = QWidget()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)

        filter_layout.addWidget(QLabel("开始日期："))
        self._start_date_edit = QDateEdit()
        self._start_date_edit.setCalendarPopup(True)
        self._start_date_edit.setDate(QDate.currentDate().addDays(-7))  # 默认最近7天
        self._start_date_edit.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._start_date_edit)
        filter_layout.addWidget(self._start_date_edit)

        filter_layout.addWidget(QLabel("结束日期："))
        self._end_date_edit = QDateEdit()
        self._end_date_edit.setCalendarPopup(True)
        self._end_date_edit.setDate(QDate.currentDate())
        self._end_date_edit.setDisplayFormat("yyyy-MM-dd")
        apply_theme_to_date_edit(self._end_date_edit)
        filter_layout.addWidget(self._end_date_edit)

        filter_layout.addWidget(QLabel("用户ID："))
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("留空显示所有用户")
        filter_layout.addWidget(self._user_id_edit)

        filter_layout.addWidget(QLabel("模型："))
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("留空显示所有模型")
        filter_layout.addWidget(self._model_edit)

        filter_layout.addWidget(QLabel("状态："))
        self._ok_combo = QComboBox()
        self._ok_combo.addItems(["全部", "成功", "失败"])
        self._ok_combo.setCurrentIndex(0)
        filter_layout.addWidget(self._ok_combo)

        btn_filter = QPushButton("筛选")
        btn_filter.clicked.connect(self._on_filter_clicked)
        filter_layout.addWidget(btn_filter)

        btn_clear = QPushButton("清除筛选")
        btn_clear.clicked.connect(self._on_clear_filter)
        filter_layout.addWidget(btn_clear)

        filter_layout.addStretch()
        layout.addWidget(filter_frame)

        # 表格区域
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "日期", "用户ID", "模型", "延迟(ms)", "状态", "创建时间", "操作"
        ])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 日期
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 用户ID
        header.setSectionResizeMode(2, QHeaderView.Stretch)  # 模型 - 使用Stretch填充剩余空间
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 延迟(ms)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 创建时间（需要完整显示）
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 操作
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格大小策略，让它填充可用空间
        from PySide6.QtWidgets import QSizePolicy
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 监听滚动事件，实现无限滚动
        scroll_bar = self._table.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._on_scroll_changed)
        
        layout.addWidget(self._table, 1)  # 设置stretch factor为1，让表格填充剩余空间
        
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
        
        worker = _AiLogWorker(
            start_date=self._current_filters.get("start_date"),
            end_date=self._current_filters.get("end_date"),
            user_id=self._current_filters.get("user_id"),
            model=self._current_filters.get("model"),
            ok=self._current_filters.get("ok"),
            offset=self._current_offset,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_more_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_filter_clicked(self):
        """执行筛选（重置分页）"""
        start_date = self._start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self._end_date_edit.date().toString("yyyy-MM-dd")
        user_id = self._user_id_edit.text().strip() or None
        model = self._model_edit.text().strip() or None
        ok_text = self._ok_combo.currentText()
        ok = None if ok_text == "全部" else (ok_text == "成功")

        # 保存筛选条件
        self._current_filters = {
            "start_date": start_date,
            "end_date": end_date,
            "user_id": user_id,
            "model": model,
            "ok": ok,
        }
        
        # 重置分页状态
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)  # 清空表格

        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载AI日志中...")

        worker = _AiLogWorker(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            model=model,
            ok=ok,
            offset=0,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_clear_filter(self):
        """清除筛选条件"""
        self._start_date_edit.setDate(QDate.currentDate().addDays(-7))
        self._end_date_edit.setDate(QDate.currentDate())
        self._user_id_edit.clear()
        self._model_edit.clear()
        self._ok_combo.setCurrentIndex(0)
        # 清除后自动执行一次筛选
        self._on_filter_clicked()

    def _on_data_loaded(self, items: List[Dict[str, Any]], total_count: int):
        """首次数据加载完成"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._is_loading = False
        self._apply_rows_to_table(items, append=False)
        
        # 更新分页状态
        self._current_offset = len(items)
        self._has_more = len(items) >= self._page_size
        
        if not self._has_more and len(items) > 0:
            self._status_label.setText(f"已加载全部数据（共 {self._current_offset} 条）")
        elif len(items) == 0:
            self._status_label.setText("暂无数据")
        else:
            self._status_label.setText(f"已加载 {self._current_offset} 条，滚动到底部加载更多")

    def _on_more_data_loaded(self, items: List[Dict[str, Any]], total_count: int):
        """加载更多数据完成"""
        self._is_loading = False
        self._apply_rows_to_table(items, append=True)
        
        # 更新分页状态
        self._current_offset += len(items)
        self._has_more = len(items) >= self._page_size
        
        if not self._has_more:
            self._status_label.setText(f"已加载全部数据（共 {self._current_offset} 条）")
        else:
            self._status_label.setText(f"已加载 {self._current_offset} 条，滚动到底部加载更多")

    def _apply_rows_to_table(self, items: List[Dict[str, Any]], append: bool = False):
        """将数据应用到表格"""
        if not append:
            self._table.setRowCount(0)
        
        start_row = self._table.rowCount()
        self._table.setRowCount(start_row + len(items))

        for idx, item in enumerate(items):
            row = start_row + idx
            date_str = str(item.get("date", ""))
            user_id = item.get("user_id", "")
            model = item.get("model", "")
            latency_ms = item.get("latency_ms", 0)
            ok = item.get("ok", False)
            created_at = item.get("created_at", "")

            # 格式化时间
            if created_at:
                if isinstance(created_at, str):
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

            self._table.setItem(row, 0, QTableWidgetItem(date_str))
            self._table.setItem(row, 1, QTableWidgetItem(user_id))
            self._table.setItem(row, 2, QTableWidgetItem(model))
            
            latency_item = QTableWidgetItem(str(latency_ms))
            latency_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 3, latency_item)

            # 状态列：根据成功状态设置颜色
            status_item = QTableWidgetItem("成功" if ok else "失败")
            status_item.setForeground(Qt.green if ok else Qt.red)
            status_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 4, status_item)

            self._table.setItem(row, 5, QTableWidgetItem(created_at))

            # 操作列：查看详情按钮（缩小尺寸，与客户端一致）
            btn_view = QPushButton("查看")
            btn_view.setFixedSize(52, 22)  # 与客户端查看按钮尺寸一致
            btn_view.setStyleSheet("""
                QPushButton {
                    font-size: 9pt;
                    padding: 0px;
                }
            """)
            btn_view.setProperty("item_data", json.dumps(item, ensure_ascii=False, default=str))
            btn_view.clicked.connect(self._on_view_detail_clicked)
            self._table.setCellWidget(row, 6, btn_view)

    def _on_view_detail_clicked(self):
        """查看详情"""
        btn = self.sender()
        if not btn:
            return

        item_data_str = btn.property("item_data") or "{}"
        try:
            item = json.loads(item_data_str)
        except:
            item = {}

        dlg = QDialog(self)
        dlg.setWindowTitle(f"AI调用详情 - {item.get('user_id', '')} - {item.get('date', '')}")
        dlg.resize(1000, 700)

        layout = QVBoxLayout(dlg)

        # Tab标签页
        tab_widget = QTabWidget()

        # Tab 1: 提示信息
        prompt_tab = QTextEdit()
        prompt_tab.setReadOnly(True)
        prompt_tab.setFont(QFont("Consolas", 10))
        prompt_json = item.get("prompt_json", {})
        prompt_tab.setPlainText(json.dumps(prompt_json, ensure_ascii=False, indent=2))
        tab_widget.addTab(prompt_tab, "提示信息")

        # Tab 2: 请求JSON
        request_tab = QTextEdit()
        request_tab.setReadOnly(True)
        request_tab.setFont(QFont("Consolas", 10))
        request_json = item.get("request_json", {})
        request_tab.setPlainText(json.dumps(request_json, ensure_ascii=False, indent=2))
        tab_widget.addTab(request_tab, "请求JSON")

        # Tab 3: 响应JSON
        response_tab = QTextEdit()
        response_tab.setReadOnly(True)
        response_tab.setFont(QFont("Consolas", 10))
        response_json = item.get("response_json", {})
        response_tab.setPlainText(json.dumps(response_json, ensure_ascii=False, indent=2))
        tab_widget.addTab(response_tab, "响应JSON")

        layout.addWidget(tab_widget)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _on_error(self, error: str):
        """处理错误"""
        self._is_loading = False
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        self._status_label.setText(f"加载失败：{error}")
        handle_api_error(self, Exception(error), "加载失败")

    def reload_from_api(self):
        """从API重新加载数据（供主窗口调用）"""
        self._on_filter_clicked()

