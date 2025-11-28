#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
history_view.py

“历史记录”页面：

- 顶部日期范围下拉框（最近 7 天 / 最近 30 天）；
- 中间表格展示：
    日期 | 总分 | 维度（执行/质/协/思）| 状态 | 备注 | 输入数据(查看按钮)
- 历史记录加载：
    - 使用 QThreadPool + QRunnable 异步请求后端，避免卡 UI；
    - 请求期间通过 MainWindow.show_loading 显示全局“加载中”遮罩；
- “输入数据”列：
    - 每行一个“查看”按钮（缩小尺寸）；
    - 点击后立即弹出对话框，内部文案先显示“加载中…”；
    - 原始 JSON 通过后台线程加载，完成后更新对话框内容，不阻塞弹框打开。
"""

import json
from typing import List, Tuple, Any, Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QFrame, QAbstractItemView,
    QPushButton, QDialog, QTextEdit, QHeaderView, QTabWidget
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot

from utils.api_client import ApiClient, ApiError, AuthError
from utils.config_manager import ConfigManager
from widgets.toast import Toast


class _HistoryWorkerSignals(QObject):
    finished = Signal(list)  # List[Tuple[str, str, str, str, str, str, str, bool, str]]  # 包含姓名列，倒数第二个是is_reviewed，最后一个是user_id
    error = Signal(str)


class _HistoryWorker(QRunnable):
    """
    在后台线程里同步调用 /api/history_scores（已优化：一次性返回所有维度信息，无需额外请求）。
    """
    def __init__(self, limit: int, offset: int = 0, user_id: Optional[str] = None, days: Optional[int] = None):
        super().__init__()
        self._limit = int(limit)
        self._offset = int(offset)
        self._user_id = user_id
        self._days = int(days) if days is not None else None
        self.signals = _HistoryWorkerSignals()

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
            resp = client.get_history_scores(limit=self._limit, offset=self._offset, user_id=self._user_id, days=self._days)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"加载历史记录失败：{e}")
            return

        # api_client.get_history_scores 已经提取了 items 字段，这里 resp 应该是列表
        if isinstance(resp, list):
            items = resp
        elif isinstance(resp, dict):
            # 兼容处理：如果 api_client 没有提取，这里再提取一次
            items = resp.get("items") or []
        else:
            items = []

        rows: List[Tuple[str, str, str, str, str, str]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            date_str = str(item.get("date") or "")
            total = item.get("total_ai")
            conf = item.get("confidence")
            name = item.get("name")  # 姓名（仅组长查看组员时才有）
            item_user_id = item.get("user_id")  # 用户ID（仅组长查看组员时才有）

            # 直接从返回的数据中获取维度信息（已优化：不再需要额外的 API 调用）
            execution = item.get("execution")
            quality = item.get("quality")
            collaboration = item.get("collaboration")
            reflection = item.get("reflection")

            # 格式化维度显示
            dims_text = "{}/{}/{}/{}".format(
                execution if execution is not None else "--",
                quality if quality is not None else "--",
                collaboration if collaboration is not None else "--",
                reflection if reflection is not None else "--",
            )

            # 复评状态：已复评 / 自动评分
            is_reviewed = item.get("is_reviewed")
            if is_reviewed is True:
                review_status_text = "已复评"
            elif is_reviewed is False:
                review_status_text = "自动评分"
            else:
                review_status_text = "--"

            # 排名（单独列）
            rank = item.get("rank")
            if rank is not None:
                rank_text = f"第 {rank} 名"
            else:
                rank_text = "未锁定"

            # 备注：置信度 + 是否参与评优（排名已单独显示，不再放在备注中）
            remark_parts = []
            
            if isinstance(conf, (int, float)):
                remark_parts.append(f"置信度 {conf:.2f}")
            
            eligible = item.get("eligible")
            if eligible is not None:
                if int(eligible) == 1:
                    remark_parts.append("参与评优")
                else:
                    reason = item.get("reason") or "不计入考核"
                    remark_parts.append(f"不参与（{reason}）")
            
            remark_text = " | ".join(remark_parts) if remark_parts else "--"

            rows.append(
                (
                    date_str,
                    name or "",  # 姓名（如果是组长查看组员，否则为空）
                    str(total) if total is not None else "--",
                    dims_text,
                    rank_text,
                    review_status_text,
                    remark_text,
                    is_reviewed if is_reviewed is not None else False,  # 添加is_reviewed状态
                    item_user_id or "",  # 用户ID（用于判断是否是当前用户）
                )
            )

        self.signals.finished.emit(rows)


class _DataViewWorkerSignals(QObject):
    finished = Signal(str)  # pretty json text
    error = Signal(str)


class _DataViewWorker(QRunnable):
    """
    在后台线程中加载某一日的数据，用于"查看"弹窗。
    """
    def __init__(self, date_str: str, data_type: str, user_id: Optional[str] = None):
        super().__init__()
        self._date_str = date_str
        self._data_type = data_type  # "input", "output", "review_input", "review_result"
        self._user_id = user_id  # 可选，用于组长查看组员数据
        self.signals = _DataViewWorkerSignals()

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
            if self._data_type == "input":
                # 原始输入数据
                raw = client.get_daily_snapshot(self._date_str, user_id=self._user_id)
            elif self._data_type == "output":
                # AI返回结果（通过get_daily_score获取）
                raw = client.get_daily_score(self._date_str, user_id=self._user_id)
            elif self._data_type == "review_input":
                # 员工复评内容（暂时不支持user_id，因为review_status接口不支持）
                review_status = client.get_review_status(self._date_str)
                raw = review_status.get("review_input_json") or {}
            elif self._data_type == "review_result":
                # 员工复评结果（暂时不支持user_id，因为review_status接口不支持）
                review_status = client.get_review_status(self._date_str)
                raw = review_status.get("review_result") or {}
            else:
                self.signals.error.emit(f"未知的数据类型：{self._data_type}")
                return

            try:
                pretty = json.dumps(raw, ensure_ascii=False, indent=2)
            except Exception:
                pretty = str(raw)

            self.signals.finished.emit(pretty)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(f"获取数据失败：{e}")
            return
        except Exception as e:
            self.signals.error.emit(f"获取数据失败：{e}")
            return


class AllDataViewDialog(QDialog):
    """所有数据查看对话框（使用Tab标签页）"""
    def __init__(self, parent, date_str: str, is_reviewed: bool = False, user_id: Optional[str] = None):
        super().__init__(parent)
        self._date_str = date_str
        self._is_reviewed = is_reviewed  # 复评状态
        self._user_id = user_id  # 可选，用于组长查看组员数据
        
        self.setWindowTitle(f"{date_str} - 数据查看")
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
        
        # Tab 3: 复评内容
        self._review_input_tab = QTextEdit()
        self._review_input_tab.setReadOnly(True)
        self._review_input_tab.setFont(QFont("Consolas", 10))
        self._review_input_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._review_input_tab, "复评内容")
        
        # Tab 4: 复评结果
        self._review_result_tab = QTextEdit()
        self._review_result_tab.setReadOnly(True)
        self._review_result_tab.setFont(QFont("Consolas", 10))
        self._review_result_tab.setPlaceholderText("加载中...")
        self._tab_widget.addTab(self._review_result_tab, "复评结果")
        
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
        if not ApiClient.is_logged_in():
            error_text = "未登录，请先登录"
            self._input_tab.setPlainText(error_text)
            self._output_tab.setPlainText(error_text)
            self._review_input_tab.setPlainText(error_text)
            self._review_result_tab.setPlainText(error_text)
            return
        
        try:
            client = ApiClient.from_config()
        except Exception as e:
            error_text = f"初始化客户端失败：{e}"
            self._input_tab.setPlainText(error_text)
            self._output_tab.setPlainText(error_text)
            self._review_input_tab.setPlainText(error_text)
            self._review_result_tab.setPlainText(error_text)
            return
        
        # 加载原始输入数据
        worker1 = _DataViewWorker(self._date_str, "input", user_id=self._user_id)
        worker1.signals.finished.connect(self._on_input_loaded)
        worker1.signals.error.connect(lambda err: self._on_tab_error(self._input_tab, err))
        QThreadPool.globalInstance().start(worker1)
        
        # 加载AI返回结果
        worker2 = _DataViewWorker(self._date_str, "output", user_id=self._user_id)
        worker2.signals.finished.connect(self._on_output_loaded)
        worker2.signals.error.connect(lambda err: self._on_tab_error(self._output_tab, err))
        QThreadPool.globalInstance().start(worker2)
        
        # 根据复评状态决定是否加载复评相关数据
        if self._is_reviewed:
            # 已复评，加载复评内容
            worker3 = _DataViewWorker(self._date_str, "review_input", user_id=self._user_id)
            worker3.signals.finished.connect(self._on_review_input_loaded)
            worker3.signals.error.connect(lambda err: self._on_tab_error(self._review_input_tab, err))
            QThreadPool.globalInstance().start(worker3)
            
            # 加载员工复评结果
            worker4 = _DataViewWorker(self._date_str, "review_result", user_id=self._user_id)
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
        tab.setPlainText(f"加载失败：{error_msg}")




class _TeamLeaderInfoWorkerSignals(QObject):
    finished = Signal(dict)  # TeamLeaderInfoResponse
    error = Signal(str)


class _TeamLeaderInfoWorker(QRunnable):
    """在后台线程中获取组长信息和组员列表"""
    def __init__(self):
        super().__init__()
        self.signals = _TeamLeaderInfoWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            client = ApiClient.from_config()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"初始化客户端失败：{e}")
            return

        try:
            resp = client.get_team_leader_info()
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"获取组长信息失败：{e}")


class HistoryView(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("历史记录")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        title.setStyleSheet("background-color: transparent;")
        layout.addWidget(title)

        # 过滤区域
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)

        range_label = QLabel("日期范围：")
        range_label.setStyleSheet("background-color: transparent;")
        self.range_combo = QComboBox()
        self.range_combo.addItems(["最近 7 天", "最近 30 天"])

        filter_layout.addWidget(range_label)
        filter_layout.addWidget(self.range_combo)
        
        # 组员筛选（仅组长可见，初始隐藏）
        self.member_label = QLabel("组员：")
        self.member_label.setStyleSheet("background-color: transparent;")
        self.member_combo = QComboBox()
        self.member_combo.setMinimumWidth(150)
        self.member_combo.addItem("全部", None)  # 第一个选项是"全部"
        self.member_label.setVisible(False)
        self.member_combo.setVisible(False)
        
        filter_layout.addWidget(self.member_label)
        filter_layout.addWidget(self.member_combo)
        filter_layout.addStretch()
        
        # 刷新按钮
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.reload_from_api)
        filter_layout.addWidget(btn_refresh)

        filter_frame.setProperty("class", "card")
        layout.addWidget(filter_frame)
        
        # 初始化：检查是否是组长
        self._is_leader = False
        self._members_map = {}  # {user_id: name}
        # 获取当前用户ID
        cfg = ConfigManager.load()
        self._current_user_id_value = cfg.get("user_id", "")
        
        # 表格区域：先创建表格（默认7列，后续会根据是否是组长更新）
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["日期", "总分", "维度（执行/质/协/思）", "排名", "复评状态", "备注", "操作"]
        )
        
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 日期
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 总分
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 维度
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 排名
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 复评状态
        header.setSectionResizeMode(5, QHeaderView.Stretch)           # 备注（占剩余）
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 操作

        # 表格只读，但允许复制
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        
        # 翻页相关状态
        self._current_offset = 0
        self._page_size = 30  # 每页加载30条
        self._is_loading = False
        self._has_more = True
        self._current_filters = {}  # 保存当前筛选条件

        layout.addWidget(self.table)
        
        # 底部统计信息
        self._stats_label = QLabel("")
        self._stats_label.setAlignment(Qt.AlignRight)
        self._stats_label.setStyleSheet("color: #666; font-size: 11pt; padding: 8px; background-color: transparent;")
        layout.addWidget(self._stats_label)
        
        # 监听滚动事件，实现无限滚动
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        
        # 下拉框变更 → 异步重新加载（不阻塞 UI）
        self.range_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.member_combo.currentIndexChanged.connect(self._on_filter_changed)
        
        # 检查是否是组长（在表格创建后）
        self._check_team_leader()

        # 首次进入页面的"自动加载"由 MainWindow 控制，这里不主动打 API

    # ---- 内部工具 ----
    def _current_limit(self) -> int:
        """
        根据下拉框当前选项，返回对应的天数。
        0 -> 7 天；其他 -> 30 天。
        """
        idx = self.range_combo.currentIndex()
        return 7 if idx == 0 else 30
    
    def _current_user_id(self) -> Optional[str]:
        """获取当前选中的组员ID（如果是组长）"""
        if not self._is_leader:
            return None
        selected = self.member_combo.currentData()
        # 如果选择的是"自己"，返回当前用户的user_id
        if selected == "self":
            return self._current_user_id_value
        return selected
    
    def _update_table_columns(self):
        """根据是否是组长更新表格列"""
        if self._is_leader:
            # 组长：8列（包含姓名列）
            self.table.setColumnCount(8)
            self.table.setHorizontalHeaderLabels(
                ["日期", "姓名", "总分", "维度（执行/质/协/思）", "排名", "复评状态", "备注", "操作"]
            )
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 日期
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 姓名
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 总分
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 维度
            header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 排名
            header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 复评状态
            header.setSectionResizeMode(6, QHeaderView.Stretch)           # 备注（占剩余）
            header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 操作
        else:
            # 普通用户：7列（不包含姓名列）
            self.table.setColumnCount(7)
            self.table.setHorizontalHeaderLabels(
                ["日期", "总分", "维度（执行/质/协/思）", "排名", "复评状态", "备注", "操作"]
            )
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 日期
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 总分
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 维度
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 排名
            header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 复评状态
            header.setSectionResizeMode(5, QHeaderView.Stretch)           # 备注（占剩余）
            header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 操作
    
    def _check_team_leader(self):
        """检查是否是组长，如果是则加载组员列表"""
        worker = _TeamLeaderInfoWorker()
        # 保存 worker 引用，避免被垃圾回收导致信号对象被删除
        if not hasattr(self, '_active_team_leader_worker'):
            self._active_team_leader_worker = None
        self._active_team_leader_worker = worker
        
        worker.signals.finished.connect(self._on_team_leader_info_loaded)
        worker.signals.error.connect(self._on_team_leader_info_error)
        
        # 连接一个清理函数，在完成后移除引用
        def cleanup():
            if hasattr(self, '_active_team_leader_worker') and self._active_team_leader_worker == worker:
                self._active_team_leader_worker = None
        
        worker.signals.finished.connect(cleanup)
        worker.signals.error.connect(cleanup)
        
        QThreadPool.globalInstance().start(worker)
    
    def _on_team_leader_info_loaded(self, info: Dict[str, Any]):
        """组长信息加载完成"""
        is_leader = info.get("is_leader", False)
        self._is_leader = is_leader
        
        if is_leader:
            # 显示组员筛选
            self.member_label.setVisible(True)
            self.member_combo.setVisible(True)
            
            # 加载组员列表
            members = info.get("members", [])
            self._members_map = {}
            self.member_combo.clear()
            self.member_combo.addItem("全部", None)
            # 添加"自己"选项（使用特殊值 "self"）
            self.member_combo.addItem("自己", "self")
            
            for member in members:
                user_id = member.get("user_id", "")
                # 如果组员是当前用户（组长自己），跳过，因为已经添加了"自己"选项
                if user_id == self._current_user_id_value:
                    continue
                name = member.get("name") or user_id
                self._members_map[user_id] = name
                display_text = f"{name} ({user_id})"
                self.member_combo.addItem(display_text, user_id)
        else:
            # 隐藏组员筛选
            self.member_label.setVisible(False)
            self.member_combo.setVisible(False)
        
        # 更新表格列
        self._update_table_columns()
    
    def _on_team_leader_info_error(self, error_msg: str):
        """组长信息加载失败"""
        # 默认不是组长
        self._is_leader = False
        self.member_label.setVisible(False)
        self.member_combo.setVisible(False)
        self._update_table_columns()

    def _apply_rows_to_table(self, rows: List[Tuple[str, str, str, str, str, str, str, bool, str]]) -> None:
        """
        将后台线程返回的 rows 渲染到表格中，同时设置对齐方式。
        rows格式：(日期, 姓名, 总分, 维度, 排名, 复评状态, 备注, is_reviewed, user_id)
        如果是普通用户，姓名列为空字符串，但表格不显示姓名列。
        """
        self.table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            self._apply_single_row_to_table(i, row)
    
    def _apply_single_row_to_table(self, row_index: int, row: Tuple[str, str, str, str, str, str, str, bool, str]) -> None:
        """
        将单行数据渲染到表格的指定行。
        row格式：(日期, 姓名, 总分, 维度, 排名, 复评状态, 备注, is_reviewed, user_id)
        """
        # row格式：(日期, 姓名, 总分, 维度, 排名, 复评状态, 备注, is_reviewed, user_id)
        date_str = row[0]
        name_str = row[1]  # 姓名（仅组长时使用）
        total_str = row[2]
        dims_str = row[3]
        rank_str = row[4]
        review_status_str = row[5]
        remark_str = row[6]
        is_reviewed = row[7] if len(row) > 7 else False
        item_user_id = row[8] if len(row) > 8 else ""  # 用户ID
        
        # 如果是组长查看，且user_id是当前用户，显示"自己"（即使name_str为空也要显示）
        if self._is_leader and item_user_id and item_user_id == self._current_user_id_value:
            name_str = "自己"
        # 如果name_str为空，但需要显示（组长模式），至少显示空字符串（不会留空，因为表格会显示空字符串）
        elif self._is_leader and not name_str:
            name_str = ""  # 保持为空字符串，但确保不会因为None导致错误
        
        # 根据是否是组长决定显示的列
        if self._is_leader:
            # 组长：8列（包含姓名）
            items_data = [
                (0, date_str, Qt.AlignCenter),
                (1, name_str, Qt.AlignCenter),  # 姓名
                (2, total_str, Qt.AlignCenter),
                (3, dims_str, Qt.AlignCenter),
                (4, rank_str, Qt.AlignCenter),
                (5, review_status_str, Qt.AlignCenter),
                (6, remark_str, Qt.AlignLeft),
            ]
            review_status_table_col = 5
            operation_col = 7
        else:
            # 普通用户：7列（不包含姓名）
            items_data = [
                (0, date_str, Qt.AlignCenter),
                (1, total_str, Qt.AlignCenter),
                (2, dims_str, Qt.AlignCenter),
                (3, rank_str, Qt.AlignCenter),
                (4, review_status_str, Qt.AlignCenter),
                (5, remark_str, Qt.AlignLeft),
            ]
            review_status_table_col = 4
            operation_col = 6
        
        # 设置单元格
        for table_col, text, alignment in items_data:
            item = QTableWidgetItem(str(text))
            
            # 复评状态列高亮：已复评显示绿色
            if table_col == review_status_table_col and text == "已复评":
                item.setForeground(Qt.green)
            elif table_col == review_status_table_col and text == "自动评分":
                item.setForeground(Qt.darkGray)
            
            item.setTextAlignment(alignment)
            self.table.setItem(row_index, table_col, item)

        # 操作列按钮
        btn = QPushButton("查看")
        btn.setProperty("date_str", date_str)
        btn.setProperty("is_reviewed", is_reviewed)
        # 存储该行对应的user_id（用于"全部"筛选时，每行对应不同的组员）
        btn.setProperty("row_user_id", item_user_id if item_user_id else "")
        btn.setFixedSize(52, 22)  # 稍小一点，避免视觉上过于抢眼
        btn.setStyleSheet("""
            QPushButton {
                font-size: 9pt;
                padding: 0px;
            }
        """)
        
        btn.clicked.connect(self._on_view_clicked)
        self.table.setCellWidget(row_index, operation_col, btn)

        # 表头对齐也做一下居中美化（视觉更统一）
        header = self.table.horizontalHeader()
        # 根据是否是组长决定哪些列居中
        if self._is_leader:
            # 组长：姓名、总分、维度、排名、复评状态居中
            center_cols = (1, 2, 3, 4, 5)
        else:
            # 普通用户：总分、维度、排名、复评状态居中
            center_cols = (1, 2, 3, 4)
        for col in center_cols:
            header_item = self.table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setTextAlignment(Qt.AlignCenter)

    # ---- 操作按钮点击 ----
    def _on_view_clicked(self) -> None:
        """处理查看按钮点击"""
        btn = self.sender()
        if btn is None:
            return
        
        date_str = btn.property("date_str")
        is_reviewed = btn.property("is_reviewed")
        row_user_id = btn.property("row_user_id")  # 该行对应的user_id
        
        if not date_str:
            return
        
        # 如果是组长查看组员数据，优先使用该行对应的user_id（用于"全部"筛选时）
        # 如果没有行user_id，则使用下拉框选中的user_id
        if self._is_leader:
            user_id = row_user_id if row_user_id else self._current_user_id()
        else:
            user_id = None
        
        self._show_all_data(date_str, is_reviewed, user_id=user_id)
    
    def _show_all_data(self, date_str: str, is_reviewed: bool, user_id: Optional[str] = None) -> None:
        """显示所有数据（使用Tab标签页）"""
        dlg = AllDataViewDialog(self, date_str, is_reviewed, user_id=user_id)
        dlg.exec()

    # ---- 滚动监听和翻页 ----
    def _on_scroll_changed(self, value: int) -> None:
        """监听滚动条变化，当滚动到底部时自动加载更多"""
        scroll_bar = self.table.verticalScrollBar()
        if scroll_bar.maximum() - value < 100:  # 距离底部100像素时触发
            self._load_more()
    
    def _load_more(self) -> None:
        """加载更多数据"""
        if self._is_loading or not self._has_more:
            return
        
        # 如果使用日期范围筛选，不应该有"加载更多"的概念
        # 因为日期范围是固定的，所有数据应该一次性加载
        # 这里保留向后兼容，但实际不应该触发
        self._is_loading = True
        user_id = self._current_user_id()
        limit = self._current_limit()  # 获取日期范围对应的天数
        
        worker = _HistoryWorker(limit=limit, offset=self._current_offset, user_id=user_id, days=limit)
        worker.signals.finished.connect(self._on_more_data_loaded)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_filter_changed(self) -> None:
        """筛选条件变化时重置翻页状态并重新加载"""
        self._current_offset = 0
        self._has_more = True
        self._is_loading = False
        self.table.setRowCount(0)  # 清空表格
        self.reload_from_api()

    # ---- 对外：重新加载（异步） ----
    def reload_from_api(self) -> None:
        """
        1）重置翻页状态；
        2）后台线程异步拉取历史记录；
        3）加载过程中，通过 MainWindow 的 show_loading / hide_loading 显示"加载中"遮罩。
        """
        limit = self._current_limit()  # 获取日期范围对应的天数（7或30）
        user_id = self._current_user_id()  # 如果是组长且选择了组员，传递user_id

        # 重置翻页状态
        self._current_offset = 0
        self._has_more = True
        self._is_loading = True

        # 显示加载中遮罩（如果主窗支持）
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading("加载历史记录中…")

        # 使用日期范围对应的天数，传递days参数而不是limit
        worker = _HistoryWorker(limit=limit, offset=0, user_id=user_id, days=limit)
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_load_error)

        QThreadPool.globalInstance().start(worker)

    # ---- 后台线程回调 ----
    def _on_data_loaded(self, rows: List[Tuple[str, str, str, str, str, str, str, bool, str]]) -> None:
        """首次加载或筛选后的数据加载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        self._is_loading = False
        
        # 判断是否还有更多数据
        if len(rows) >= self._page_size:
            self._has_more = True
            self._current_offset = self._page_size
        else:
            self._has_more = False
            self._current_offset = len(rows)
        
        self._apply_rows_to_table(rows)
        self._update_stats_label(len(rows), self._has_more)
    
    def _on_more_data_loaded(self, rows: List[Tuple[str, str, str, str, str, str, str, bool, str]]) -> None:
        """加载更多数据完成"""
        self._is_loading = False
        
        if len(rows) == 0:
            self._has_more = False
            self._update_stats_label(self.table.rowCount(), False)
            return
        
        # 判断是否还有更多数据
        if len(rows) >= self._page_size:
            self._has_more = True
            self._current_offset += self._page_size
        else:
            self._has_more = False
            self._current_offset += len(rows)
        
        # 追加数据到表格
        current_row_count = self.table.rowCount()
        self.table.setRowCount(current_row_count + len(rows))
        
        for i, row in enumerate(rows):
            self._apply_single_row_to_table(current_row_count + i, row)
        
        self._update_stats_label(self.table.rowCount(), self._has_more)

    def _on_load_error(self, message: str) -> None:
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        self._is_loading = False

        if not message:
            return

        text = str(message)
        # 登录相关错误：弹出登录对话框，成功后自动重载
        if any(key in text for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        # 登录成功后重新加载
                        self.reload_from_api()
                        return
                return  # 如果已经有登录弹窗，直接返回，不显示 Toast

        Toast.show_message(self, text)
    
    def _update_stats_label(self, current_count: int, has_more: bool) -> None:
        """更新底部统计信息"""
        if has_more:
            self._stats_label.setText(f"已显示 {current_count} 条记录，滚动到底部加载更多...")
        else:
            self._stats_label.setText(f"共显示 {current_count} 条记录")
