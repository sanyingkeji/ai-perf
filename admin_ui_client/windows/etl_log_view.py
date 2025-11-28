#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETL任务运行日志页面：
- 支持按日期范围、任务名、状态筛选
- 显示ETL任务运行详情
- 查看错误详情（如果有）
"""

import json
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QDialog,
    QTextEdit, QHeaderView, QDateEdit, QLineEdit, QMessageBox,
    QAbstractItemView
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast


class _EtlLogWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count
    error = Signal(str)


class _EtlLogWorker(QRunnable):
    """后台加载ETL日志数据"""
    def __init__(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        job_name: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 50
    ):
        super().__init__()
        self._start_date = start_date
        self._end_date = end_date
        self._job_name = job_name
        self._status = status
        self._offset = offset
        self._limit = limit
        self.signals = _EtlLogWorkerSignals()

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
            resp = client.get_etl_job_runs(
                start_date=self._start_date,
                end_date=self._end_date,
                job_name=self._job_name,
                status=self._status,
                offset=self._offset,
                limit=self._limit
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            total_count = len(items)
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载ETL日志失败：{e}")


class EtlLogView(QWidget):
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

        title = QLabel("ETL任务运行日志")
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

        filter_layout.addWidget(QLabel("任务名："))
        self._job_name_edit = QLineEdit()
        self._job_name_edit.setPlaceholderText("留空显示所有任务")
        filter_layout.addWidget(self._job_name_edit)

        filter_layout.addWidget(QLabel("状态："))
        self._status_combo = QComboBox()
        self._status_combo.addItems(["全部", "success", "partial", "failed"])
        self._status_combo.setCurrentIndex(0)
        filter_layout.addWidget(self._status_combo)

        btn_filter = QPushButton("筛选")
        btn_filter.clicked.connect(self._on_filter_clicked)
        filter_layout.addWidget(btn_filter)

        btn_clear = QPushButton("清除筛选")
        btn_clear.clicked.connect(self._on_clear_filter)
        filter_layout.addWidget(btn_clear)

        filter_layout.addStretch()
        layout.addWidget(filter_frame)

        # 表格区域
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "任务名", "目标日期", "状态", "开始时间", "结束时间", "影响行数", "消息", "操作"
        ])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 任务名
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 目标日期
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 状态
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 开始时间（需要完整显示）
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 结束时间（需要完整显示）
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 影响行数
        header.setSectionResizeMode(6, QHeaderView.Stretch)  # 消息（占剩余空间）
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 操作
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
        
        worker = _EtlLogWorker(
            start_date=self._current_filters.get("start_date"),
            end_date=self._current_filters.get("end_date"),
            job_name=self._current_filters.get("job_name"),
            status=self._current_filters.get("status"),
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
        job_name = self._job_name_edit.text().strip() or None
        status = self._status_combo.currentText()
        status = None if status == "全部" else status

        # 保存筛选条件
        self._current_filters = {
            "start_date": start_date,
            "end_date": end_date,
            "job_name": job_name,
            "status": status,
        }
        
        # 重置分页状态
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)  # 清空表格

        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载ETL日志中...")

        worker = _EtlLogWorker(
            start_date=start_date,
            end_date=end_date,
            job_name=job_name,
            status=status,
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
        self._job_name_edit.clear()
        self._status_combo.setCurrentIndex(0)
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
            job_name = item.get("job_name", "")
            target_date = item.get("target_date", "")
            status = item.get("status", "")
            started_at = item.get("started_at", "")
            finished_at = item.get("finished_at") or ""
            rows_affected = item.get("rows_affected", 0)
            message = item.get("message") or ""
            detail_json = item.get("detail_json")

            # 格式化时间
            if started_at:
                if isinstance(started_at, str):
                    try:
                        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        started_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass
            if finished_at:
                if isinstance(finished_at, str):
                    try:
                        dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                        finished_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

            self._table.setItem(row, 0, QTableWidgetItem(job_name))
            self._table.setItem(row, 1, QTableWidgetItem(str(target_date)))
            
            # 状态列：根据状态设置颜色
            status_item = QTableWidgetItem(status)
            if status == "success":
                status_item.setForeground(Qt.green)
            elif status == "partial":
                status_item.setForeground(Qt.yellow)
            elif status == "failed":
                status_item.setForeground(Qt.red)
            status_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 2, status_item)

            self._table.setItem(row, 3, QTableWidgetItem(started_at))
            self._table.setItem(row, 4, QTableWidgetItem(finished_at))
            
            rows_item = QTableWidgetItem(str(rows_affected))
            rows_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 5, rows_item)
            
            self._table.setItem(row, 6, QTableWidgetItem(message[:100] if message else ""))  # 限制长度

            # 操作列：查看详情按钮（缩小尺寸，与客户端一致）
            if detail_json or message:
                btn_view = QPushButton("查看")
                btn_view.setFixedSize(52, 22)  # 与客户端查看按钮尺寸一致
                btn_view.setStyleSheet("""
                    QPushButton {
                        font-size: 9pt;
                        padding: 0px;
                    }
                """)
                btn_view.setProperty("detail_json", json.dumps(detail_json, ensure_ascii=False) if detail_json else "")
                btn_view.setProperty("message", message)
                btn_view.setProperty("job_name", job_name)
                btn_view.setProperty("target_date", str(target_date))
                btn_view.clicked.connect(self._on_view_detail_clicked)
                self._table.setCellWidget(row, 7, btn_view)

    def _on_view_detail_clicked(self):
        """查看详情"""
        btn = self.sender()
        if not btn:
            return

        detail_json_str = btn.property("detail_json") or ""
        message = btn.property("message") or ""
        job_name = btn.property("job_name") or ""
        target_date = btn.property("target_date") or ""

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{job_name} - {target_date} - 详情")
        dlg.resize(800, 600)

        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))

        content = []
        if message:
            content.append(f"消息：\n{message}\n\n")
        
        if detail_json_str:
            try:
                detail = json.loads(detail_json_str)
                content.append("详细信息：\n")
                content.append(json.dumps(detail, ensure_ascii=False, indent=2))
            except:
                content.append(f"详细信息（原始）：\n{detail_json_str}")

        text.setPlainText("\n".join(content) if content else "无详细信息")
        layout.addWidget(text)

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

