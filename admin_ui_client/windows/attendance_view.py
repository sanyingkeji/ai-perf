#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考勤打卡记录页面（管理端）：
- 从后端 /admin/api/attendance/checkins 读取本地库 raw_attendance_checkin
- 支持按日期范围、user_id、打卡类型筛选
- 支持分页（无限滚动）
- 支持查看单条打卡详情（raw/check_data）
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    QMessageBox,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error


CHECK_TYPE_LABELS: Dict[str, str] = {
    "fp": "指纹",
    "fa": "人脸",
    "pass": "密码",
    "card": "刷卡",
    "app_scan": "APP扫码",
    "gps": "GPS",
    "wifi": "WiFi",
    "out_work": "外勤",
    "reissue": "补卡",
    "flexible": "弹性",
}


def _check_type_text(code: str) -> str:
    c = (code or "").strip()
    if not c:
        return ""
    label = CHECK_TYPE_LABELS.get(c)
    return f"{label}({c})" if label else c


class _AttendanceCheckinWorkerSignals(QObject):
    finished = Signal(list)  # items
    error = Signal(str)


class _AttendanceCheckinWorker(QRunnable):
    def __init__(
        self,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        user_id: Optional[str],
        check_type: Optional[str],
        offset: int,
        limit: int,
    ) -> None:
        super().__init__()
        self._start_date = start_date
        self._end_date = end_date
        self._user_id = user_id
        self._check_type = check_type
        self._offset = offset
        self._limit = limit
        self.signals = _AttendanceCheckinWorkerSignals()

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
            resp = client.get_attendance_checkins(
                start_date=self._start_date,
                end_date=self._end_date,
                user_id=self._user_id,
                check_type=self._check_type,
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
            self.signals.error.emit(f"加载考勤打卡记录失败：{e}")


class _AttendanceCheckinDetailWorkerSignals(QObject):
    finished = Signal(dict)  # item
    error = Signal(str)


class _AttendanceCheckinDetailWorker(QRunnable):
    def __init__(self, checkin_id: int) -> None:
        super().__init__()
        self._checkin_id = int(checkin_id)
        self.signals = _AttendanceCheckinDetailWorkerSignals()

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
            item = client.get_attendance_checkin_detail(self._checkin_id)
            if not isinstance(item, dict):
                item = {}
            self.signals.finished.emit(item)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载详情失败：{e}")


class _AttendanceManualAddWorkerSignals(QObject):
    finished = Signal(dict)  # resp
    error = Signal(str)


class _AttendanceManualAddWorker(QRunnable):
    def __init__(self, items: List[Dict[str, Any]], note: str):
        super().__init__()
        self._items = items
        self._note = note
        self.signals = _AttendanceManualAddWorkerSignals()

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
            resp = client.manual_add_attendance_checkins(items=self._items, rebuild_daily=True, note=self._note or "")
            if not isinstance(resp, dict):
                resp = {"status": "error", "message": "后端返回非JSON对象"}
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"补录失败：{type(e).__name__}: {e}")


class AttendanceManualAddDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("手动补录考勤打卡记录")
        self.resize(900, 620)

        layout = QVBoxLayout(self)

        tip = QLabel(
            "用于补齐“初始化前的历史打卡”等缺口。\n"
            "支持粘贴 CSV/TSV 或 JSON（可直接粘贴得力云 checkin_query 返回的 data 数组）。\n"
            "行格式示例：\n"
            "  u1001,09:20:00,fa\n"
            "  2,2025-12-18 09:20:00,fa\n"
            "说明：第一列既可填内部 user_id（u1001），也可直接填得力云 user_id（数字字符串）。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#444;")
        layout.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("默认日期："))
        self._default_date = QDateEdit()
        self._default_date.setCalendarPopup(True)
        self._default_date.setDate(QDate.currentDate().addDays(-1))
        self._default_date.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._default_date)
        row.addWidget(self._default_date)

        row.addWidget(QLabel("默认类型："))
        self._default_type = QComboBox()
        for code in ["fa", "fp", "pass", "card", "app_scan", "gps", "wifi", "out_work", "reissue", "flexible"]:
            self._default_type.addItem(_check_type_text(code), code)
        self._default_type.setCurrentIndex(0)
        row.addWidget(self._default_type)

        row.addWidget(QLabel("默认终端："))
        self._default_terminal = QLineEdit()
        self._default_terminal.setPlaceholderText("可选，如 DL-D7_0000001；不填则记为 manual")
        row.addWidget(self._default_terminal, 1)

        layout.addLayout(row)

        note_row = QHBoxLayout()
        note_row.addWidget(QLabel("备注："))
        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("可选，例如：补齐 2025-12-18 早上打卡")
        note_row.addWidget(self._note_edit, 1)
        layout.addLayout(note_row)

        self._text = QTextEdit()
        self._text.setPlaceholderText(
            "粘贴数据到这里：\n"
            "1) CSV/TSV：\n"
            "   u1001,09:20:00,fa\n"
            "   u1002,09:28:00\n"
            "   2,2025-12-18 09:20:00,fa\n"
            "2) JSON：\n"
            "   [{\"id\":123,\"user_id\":\"2\",\"check_time\":1503025335,\"check_type\":\"fa\"}, ...]"
        )
        layout.addWidget(self._text, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_submit = QPushButton("提交补录")
        self._btn_close = QPushButton("关闭")
        btn_row.addWidget(self._btn_submit)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        self._btn_close.clicked.connect(self.reject)
        self._btn_submit.clicked.connect(self._on_submit)

    def _parse_items(self) -> List[Dict[str, Any]]:
        txt = (self._text.toPlainText() or "").strip()
        if not txt:
            return []

        # JSON 优先：允许直接粘贴得力云的 data 数组
        if txt.startswith("[") or txt.startswith("{"):
            try:
                obj = json.loads(txt)
            except Exception as e:
                raise ValueError(f"JSON 解析失败：{e}")

            if isinstance(obj, dict):
                # 兼容 {"data": [...]} 或 {"data": {"data": [...]}}
                if isinstance(obj.get("data"), dict) and isinstance(obj["data"].get("data"), list):
                    rows = obj["data"]["data"]
                elif isinstance(obj.get("data"), list):
                    rows = obj["data"]
                else:
                    raise ValueError("JSON 结构不支持：请粘贴数组，或包含 data 的对象")
            elif isinstance(obj, list):
                rows = obj
            else:
                raise ValueError("JSON 顶层必须是数组或对象")

            items: List[Dict[str, Any]] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                user_key = r.get("user_id")
                check_time = r.get("check_time")
                check_type = r.get("check_type")
                if user_key is None or check_time is None or not check_type:
                    continue
                items.append(
                    {
                        "id": r.get("id"),
                        "user_id": str(user_key),
                        "check_type": str(check_type),
                        "check_time_ts": int(float(check_time)),
                        "terminal_id": r.get("terminal_id"),
                        "ext_id": r.get("ext_id"),
                        "check_data": r.get("check_data"),
                        "raw": r,
                    }
                )
            return items

        # 文本行（CSV/TSV）
        default_date = self._default_date.date().toString("yyyy-MM-dd")
        default_type = str(self._default_type.currentData() or "fa")
        default_terminal = (self._default_terminal.text() or "").strip() or "manual"

        items: List[Dict[str, Any]] = []
        for raw_line in txt.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # 允许逗号/Tab 分隔
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
            else:
                parts = [p.strip() for p in line.split(",") if p.strip()]

            if len(parts) < 2:
                continue
            user_key = parts[0]
            t = parts[1]
            ctype = parts[2] if len(parts) >= 3 else default_type
            terminal = parts[3] if len(parts) >= 4 else default_terminal

            payload: Dict[str, Any] = {"user_id": user_key, "check_type": ctype}

            # 时间：epoch / 全量时间 / 仅时分秒
            if t.isdigit() and len(t) >= 10:
                payload["check_time_ts"] = int(t)
            elif "-" in t:
                payload["check_time_local"] = t if len(t.split(":")) >= 3 else f"{t}:00"
            else:
                # 仅时分秒
                tt = t if len(t.split(":")) >= 3 else f"{t}:00"
                payload["check_time_local"] = f"{default_date} {tt}"

            if terminal:
                payload["terminal_id"] = terminal

            items.append(payload)

        return items

    def _on_submit(self):
        try:
            items = self._parse_items()
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return

        if not items:
            QMessageBox.warning(self, "提示", "没有可提交的数据")
            return

        self._btn_submit.setEnabled(False)
        self._btn_close.setEnabled(False)

        note = (self._note_edit.text() or "").strip()

        mw = self.parent()
        if hasattr(mw, "show_loading"):
            mw.show_loading("正在补录并重算日汇总...", closeable=False)

        worker = _AttendanceManualAddWorker(items=items, note=note)

        def _on_ok(resp: Dict[str, Any]):
            try:
                if hasattr(mw, "hide_loading"):
                    mw.hide_loading()
            except Exception:
                pass

            self._btn_submit.setEnabled(True)
            self._btn_close.setEnabled(True)

            if resp.get("status") != "success":
                QMessageBox.warning(self, "补录失败", str(resp.get("message") or resp))
                return

            inserted = int(resp.get("inserted") or 0)
            skipped = int(resp.get("skipped") or 0)
            failed = int(resp.get("failed") or 0)
            rebuilt = int(resp.get("rebuilt_daily_rows") or 0)

            msg = f"补录完成：新增 {inserted} 条，跳过 {skipped} 条，失败 {failed} 条；已重算日汇总 {rebuilt} 行。"
            QMessageBox.information(self, "补录成功", msg)
            self.accept()

        def _on_err(err: str):
            try:
                if hasattr(mw, "hide_loading"):
                    mw.hide_loading()
            except Exception:
                pass
            self._btn_submit.setEnabled(True)
            self._btn_close.setEnabled(True)
            QMessageBox.warning(self, "补录失败", err)

        worker.signals.finished.connect(_on_ok)
        worker.signals.error.connect(_on_err)
        QThreadPool.globalInstance().start(worker)


class AttendanceView(QWidget):
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

        title = QLabel("考勤打卡记录")
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

        fl.addWidget(QLabel("用户ID："))
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("留空显示所有用户")
        fl.addWidget(self._user_id_edit)

        fl.addWidget(QLabel("打卡类型："))
        self._type_combo = QComboBox()
        self._type_combo.addItem("全部", None)
        # 统一用“中文(代码)”显示，但请求仍传 code
        for code in ["fa", "fp", "pass", "card", "app_scan", "gps", "wifi", "out_work", "reissue", "flexible"]:
            self._type_combo.addItem(_check_type_text(code), code)
        self._type_combo.setCurrentIndex(0)
        fl.addWidget(self._type_combo)

        btn_filter = QPushButton("筛选")
        btn_filter.clicked.connect(self._on_filter_clicked)
        fl.addWidget(btn_filter)

        btn_clear = QPushButton("清除筛选")
        btn_clear.clicked.connect(self._on_clear_filter)
        fl.addWidget(btn_clear)

        btn_manual = QPushButton("手动补录")
        btn_manual.clicked.connect(self._on_manual_add_clicked)
        fl.addWidget(btn_manual)

        fl.addStretch()
        layout.addWidget(filter_frame)

        # 表格
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["日期", "时间", "用户ID", "姓名", "得力云ID", "类型", "终端", "操作"])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
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

    def _on_manual_add_clicked(self):
        dlg = AttendanceManualAddDialog(self.window())
        if dlg.exec() == QDialog.Accepted:
            # 补录完成后刷新列表
            self.reload_from_api()

    def _on_scroll_changed(self, value: int):
        if self._is_loading or not self._has_more:
            return
        sb = self._table.verticalScrollBar()
        if value >= sb.maximum() - 50:
            self._load_more()

    def _on_clear_filter(self):
        self._start_date.setDate(QDate.currentDate().addDays(-7))
        self._end_date.setDate(QDate.currentDate())
        self._user_id_edit.clear()
        self._type_combo.setCurrentIndex(0)
        self._on_filter_clicked()

    def _on_filter_clicked(self):
        start_date = self._start_date.date().toString("yyyy-MM-dd")
        end_date = self._end_date.date().toString("yyyy-MM-dd")
        user_id = self._user_id_edit.text().strip() or None
        check_type = self._type_combo.currentData()

        self._current_filters = {
            "start_date": start_date,
            "end_date": end_date,
            "user_id": user_id,
            "check_type": check_type,
        }
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
            mw.show_loading("加载考勤记录中...")

        worker = _AttendanceCheckinWorker(
            start_date=self._current_filters.get("start_date"),
            end_date=self._current_filters.get("end_date"),
            user_id=self._current_filters.get("user_id"),
            check_type=self._current_filters.get("check_type"),
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
            check_date = str(it.get("check_date") or "")
            check_time_local = str(it.get("check_time_local") or "")
            # 兼容 ISO 格式，显示到秒
            if isinstance(check_time_local, str) and "T" in check_time_local:
                try:
                    dt = datetime.fromisoformat(check_time_local.replace("Z", "+00:00"))
                    check_time_local = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            user_id = str(it.get("user_id") or "")
            user_name = str(it.get("user_name") or "")
            delicloud_user_id = str(it.get("delicloud_user_id") or "")
            check_type = str(it.get("check_type") or "")
            terminal_id = str(it.get("terminal_id") or "")

            self._table.setItem(row, 0, QTableWidgetItem(check_date))
            self._table.setItem(row, 1, QTableWidgetItem(check_time_local))
            self._table.setItem(row, 2, QTableWidgetItem(user_id))
            self._table.setItem(row, 3, QTableWidgetItem(user_name))
            self._table.setItem(row, 4, QTableWidgetItem(delicloud_user_id))
            self._table.setItem(row, 5, QTableWidgetItem(_check_type_text(check_type)))
            self._table.setItem(row, 6, QTableWidgetItem(terminal_id))

            btn = QPushButton("查看")
            btn.setFixedSize(52, 22)
            btn.setStyleSheet("QPushButton { font-size: 9pt; padding: 0px; }")
            btn.setProperty("checkin_id", int(it.get("id") or 0))
            btn.clicked.connect(self._on_view_clicked)
            self._table.setCellWidget(row, 7, btn)

    def _on_view_clicked(self):
        btn = self.sender()
        if not btn:
            return
        checkin_id = int(btn.property("checkin_id") or 0)
        if checkin_id <= 0:
            return

        mw = self.window()
        if hasattr(mw, "show_loading"):
            mw.show_loading("加载打卡详情中...")

        worker = _AttendanceCheckinDetailWorker(checkin_id)
        worker.signals.finished.connect(self._show_detail_dialog)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _show_detail_dialog(self, item: Dict[str, Any]):
        mw = self.window()
        if hasattr(mw, "hide_loading"):
            mw.hide_loading()

        dlg = QDialog(self)
        dlg.setWindowTitle(f"打卡详情 - {item.get('user_id') or ''} - {item.get('check_date') or ''}")
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

