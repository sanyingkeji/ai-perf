#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考勤日汇总页面（管理端）：
- 从后端 /admin/api/attendance/daily 读取本地库 raw_attendance_daily
- 支持按日期范围、user_id 筛选
- 支持分页（无限滚动）
- 支持查看单人单日详情（raw_meta）
"""

import json
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Union

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QDialog,
    QTextEdit,
    QHeaderView,
    QDateEdit,
    QLineEdit,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error


CHECK_TYPE_LABELS: Dict[str, str] = {
    "fp": "指纹",
    "fa": "人脸",
    "gps": "GPS",
    "wifi": "WiFi",
    "out_work": "外勤",
    "reissue": "补卡",
    "flexible": "弹性",
}


def _format_counts_by_type(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return ""
    parts: List[str] = []
    # 固定顺序
    for code in ["fa", "fp", "gps", "wifi", "out_work", "reissue", "flexible"]:
        if code not in counts:
            continue
        try:
            v = int(counts.get(code) or 0)
        except Exception:
            v = 0
        label = CHECK_TYPE_LABELS.get(code, code)
        parts.append(f"{label}:{v}")
    # 兜底：把未知 key 也带上
    for k, v in counts.items():
        if str(k) in CHECK_TYPE_LABELS:
            continue
        if str(k) in {"fa", "fp", "gps", "wifi", "out_work", "reissue", "flexible"}:
            continue
        try:
            vv = int(v or 0)
        except Exception:
            vv = 0
        parts.append(f"{k}:{vv}")
    return " ".join(parts)


def _get_weekday_text(date_val: Union[str, date, Any]) -> str:
    """根据日期字符串或date对象返回星期几的中文显示（如：星期一、星期二）"""
    if not date_val:
        return ""
    try:
        # 尝试解析日期字符串（格式：YYYY-MM-DD）或直接使用date对象
        if isinstance(date_val, str):
            d = datetime.strptime(date_val, "%Y-%m-%d").date()
        elif isinstance(date_val, date):
            d = date_val
        else:
            return ""
        # weekday(): 0=周一, 6=周日
        weekday = d.weekday()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return weekdays[weekday] if 0 <= weekday < 7 else ""
    except Exception:
        return ""


class _AttendanceDailyWorkerSignals(QObject):
    finished = Signal(list)  # items
    error = Signal(str)


class _AttendanceDailyWorker(QRunnable):
    def __init__(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        keyword: Optional[str],
        abnormal: Optional[str],
        offset: int,
        limit: int,
    ) -> None:
        super().__init__()
        self._start_date = start_date
        self._end_date = end_date
        self._keyword = (keyword or "").strip() or None
        self._abnormal = (abnormal or "").strip() or None
        self._offset = offset
        self._limit = limit
        self.signals = _AttendanceDailyWorkerSignals()

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
            resp = client.get_attendance_daily(
                start_date=self._start_date,
                end_date=self._end_date,
                keyword=self._keyword,
                abnormal=self._abnormal,
                offset=self._offset,
                limit=self._limit,
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            if not isinstance(items, list):
                items = []
            self.signals.finished.emit(items)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载考勤日汇总失败：{e}")


class _AttendanceDailyDetailWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class _AttendanceDailyDetailWorker(QRunnable):
    def __init__(self, date_str: str, user_id: str) -> None:
        super().__init__()
        self._date = (date_str or "").strip()
        self._user_id = (user_id or "").strip()
        self.signals = _AttendanceDailyDetailWorkerSignals()

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
            item = client.get_attendance_daily_detail(self._date, self._user_id)
            if not isinstance(item, dict):
                item = {}
            self.signals.finished.emit(item)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载详情失败：{e}")


class AttendanceDailyView(QWidget):
    def __init__(self):
        super().__init__()

        # 分页状态
        self._current_offset = 0
        self._page_size = 50
        self._is_loading = False
        self._has_more = True
        self._current_filters: Dict[str, Any] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("考勤日汇总")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # 筛选区
        filter_frame = QWidget()
        fl = QHBoxLayout(filter_frame)
        fl.setContentsMargins(12, 12, 12, 12)
        fl.setSpacing(8)

        fl.addWidget(QLabel("开始日期："))
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDate(QDate.currentDate().addDays(-7))
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._start_date)
        fl.addWidget(self._start_date)

        fl.addWidget(QLabel("结束日期："))
        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDate(QDate.currentDate())
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        apply_theme_to_date_edit(self._end_date)
        fl.addWidget(self._end_date)

        fl.addWidget(QLabel("姓名/用户ID："))
        self._keyword_edit = QLineEdit()
        self._keyword_edit.setPlaceholderText("留空显示所有用户；可输入姓名或 u1001")
        fl.addWidget(self._keyword_edit)

        fl.addWidget(QLabel("考勤异常："))
        self._abnormal_combo = QComboBox()
        self._abnormal_combo.addItem("全部", None)
        self._abnormal_combo.addItem("迟到早退（>09:30 或 <18:30）", "late_early")
        self._abnormal_combo.addItem("缺卡（<2次）", "missing")
        self._abnormal_combo.addItem("加班（>640分钟）", "overtime")
        self._abnormal_combo.setCurrentIndex(0)
        fl.addWidget(self._abnormal_combo)

        btn_filter = QPushButton("筛选")
        btn_filter.clicked.connect(self._on_filter_clicked)
        fl.addWidget(btn_filter)

        btn_clear = QPushButton("清除筛选")
        btn_clear.clicked.connect(self._on_clear_filter)
        fl.addWidget(btn_clear)

        fl.addStretch()
        layout.addWidget(filter_frame)

        # 表格
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(["日期", "星期", "用户ID", "姓名", "最早打卡", "最晚打卡", "在岗(分钟)", "记录数", "类型统计", "操作"])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        scroll_bar = self._table.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._on_scroll_changed)
        layout.addWidget(self._table, 1)

        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet("color: #666; padding: 8px;")
        layout.addWidget(self._status_label)

    def reload_from_api(self):
        self._on_filter_clicked()

    def _on_scroll_changed(self, value: int):
        if self._is_loading or not self._has_more:
            return
        sb = self._table.verticalScrollBar()
        if value >= sb.maximum() - 50:
            self._load_more()

    def _on_clear_filter(self):
        self._start_date.setDate(QDate.currentDate().addDays(-7))
        self._end_date.setDate(QDate.currentDate())
        self._keyword_edit.clear()
        self._abnormal_combo.setCurrentIndex(0)
        self._on_filter_clicked()

    def _on_filter_clicked(self):
        start_date = self._start_date.date().toString("yyyy-MM-dd")
        end_date = self._end_date.date().toString("yyyy-MM-dd")
        keyword = self._keyword_edit.text().strip() or None
        abnormal = self._abnormal_combo.currentData()

        self._current_filters = {"start_date": start_date, "end_date": end_date, "keyword": keyword, "abnormal": abnormal}
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)
        self._load_more(first=True)

    def _load_more(self, first: bool = False):
        if self._is_loading or not self._has_more:
            return
        self._is_loading = True
        self._status_label.setText("加载中...")

        mw = self.window()
        if first and hasattr(mw, "show_loading"):
            mw.show_loading("加载考勤日汇总中...")

        worker = _AttendanceDailyWorker(
            start_date=self._current_filters.get("start_date"),
            end_date=self._current_filters.get("end_date"),
            keyword=self._current_filters.get("keyword"),
            abnormal=self._current_filters.get("abnormal"),
            offset=self._current_offset,
            limit=self._page_size,
        )
        worker.signals.finished.connect(lambda items: self._on_loaded(items, first=first))
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_loaded(self, items: List[Dict[str, Any]], *, first: bool):
        mw = self.window()
        if first and hasattr(mw, "hide_loading"):
            mw.hide_loading()

        self._is_loading = False
        self._apply_rows(items)

        self._current_offset += len(items)
        self._has_more = len(items) >= self._page_size

        if not items and self._current_offset == 0:
            self._status_label.setText("暂无数据")
        elif not self._has_more:
            self._status_label.setText(f"已加载全部数据（共 {self._current_offset} 条）")
        else:
            self._status_label.setText(f"已加载 {self._current_offset} 条，滚动到底部加载更多")

    def _apply_rows(self, items: List[Dict[str, Any]]):
        start_row = self._table.rowCount()
        self._table.setRowCount(start_row + len(items))

        for idx, it in enumerate(items):
            row = start_row + idx
            d = str(it.get("date") or "")
            user_id = str(it.get("user_id") or "")
            user_name = str(it.get("user_name") or "")

            first_check_time = str(it.get("first_check_time") or "")
            last_check_time = str(it.get("last_check_time") or "")
            # 兼容 ISO 格式，显示到秒
            if isinstance(first_check_time, str) and "T" in first_check_time:
                try:
                    dt = datetime.fromisoformat(first_check_time.replace("Z", "+00:00"))
                    first_check_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            if isinstance(last_check_time, str) and "T" in last_check_time:
                try:
                    dt = datetime.fromisoformat(last_check_time.replace("Z", "+00:00"))
                    last_check_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            presence_minutes = int(it.get("presence_minutes") or 0)
            record_count = int(it.get("record_count") or 0)
            counts_by_type = it.get("counts_by_type") or {}

            # 解释：当 record_count < 2 时，无法用“首末差”估算在岗时长（通常是缺少上下班其中一次、或当前还未下班）。
            # 为避免误解，UI 里直接标注。
            if record_count <= 0:
                presence_text = "—"
                first_check_time_disp = first_check_time
                last_check_time_disp = last_check_time
            elif record_count == 1:
                presence_text = "—（仅1次打卡）"
                first_check_time_disp = f"{first_check_time}（仅1次）" if first_check_time else ""
                last_check_time_disp = f"{last_check_time}（仅1次）" if last_check_time else ""
            else:
                presence_text = str(presence_minutes)
                first_check_time_disp = first_check_time
                last_check_time_disp = last_check_time

            # 计算星期几
            weekday_text = _get_weekday_text(d)

            self._table.setItem(row, 0, QTableWidgetItem(d))
            self._table.setItem(row, 1, QTableWidgetItem(weekday_text))
            self._table.setItem(row, 2, QTableWidgetItem(user_id))
            self._table.setItem(row, 3, QTableWidgetItem(user_name))
            self._table.setItem(row, 4, QTableWidgetItem(first_check_time_disp))
            self._table.setItem(row, 5, QTableWidgetItem(last_check_time_disp))
            self._table.setItem(row, 6, QTableWidgetItem(presence_text))
            self._table.setItem(row, 7, QTableWidgetItem(str(record_count)))
            self._table.setItem(row, 8, QTableWidgetItem(_format_counts_by_type(counts_by_type)))

            btn = QPushButton("查看")
            btn.setFixedSize(52, 22)
            btn.setStyleSheet("QPushButton { font-size: 9pt; padding: 0px; }")
            btn.setProperty("date", d)
            btn.setProperty("user_id", user_id)
            btn.clicked.connect(self._on_view_clicked)
            self._table.setCellWidget(row, 9, btn)

    def _on_view_clicked(self):
        btn = self.sender()
        if not btn:
            return
        d = str(btn.property("date") or "")
        user_id = str(btn.property("user_id") or "")
        if not d or not user_id:
            return

        mw = self.window()
        if hasattr(mw, "show_loading"):
            mw.show_loading("加载日汇总详情中...")

        worker = _AttendanceDailyDetailWorker(d, user_id)
        worker.signals.finished.connect(self._show_detail_dialog)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _show_detail_dialog(self, item: Dict[str, Any]):
        mw = self.window()
        if hasattr(mw, "hide_loading"):
            mw.hide_loading()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"日汇总详情 - {item.get('user_id') or ''} - {item.get('date') or ''}")
        dlg.resize(1000, 700)

        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))
        text.setPlainText(json.dumps(item, ensure_ascii=False, indent=2, default=str))
        layout.addWidget(text)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        bl = QHBoxLayout()
        bl.addStretch()
        bl.addWidget(btn_close)
        layout.addLayout(bl)

        dlg.exec()

    def _on_error(self, msg: str):
        self._is_loading = False
        mw = self.window()
        if hasattr(mw, "hide_loading"):
            mw.hide_loading()
        self._status_label.setText(f"加载失败：{msg}")
        handle_api_error(self, Exception(msg), "加载失败")

