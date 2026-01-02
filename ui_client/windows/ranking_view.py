#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ranking_view.py

排行榜页面：
- 顶部日期选择器（默认显示最近一天的排名）
- 显示前十名（前三名重点突出）
- 显示排名变化（上升/下降，绿色/红色）
- 显示当前用户排名（如果不在前十）
- 如果当前用户在前十，特殊美化显示
"""

from typing import Optional, Any, Dict
from datetime import date, timedelta
from calendar import monthrange

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QFrame, QPushButton, QDateEdit, QScrollArea, QDialog,
    QTextEdit, QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QToolTip
)
from PySide6.QtGui import QFont, QColor, QPalette
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate, QTimer, QEvent

from utils.api_client import ApiClient, ApiError, AuthError
from utils.date_edit_helper import apply_theme_to_date_edit, apply_theme_to_combo_box
from utils.theme_manager import ThemeManager
from utils.config_manager import ConfigManager
from widgets.toast import Toast
from windows.comparison_dialog import ComparisonDialog


class _RankingWorkerSignals(QObject):
    finished = Signal(dict)  # RankingResponse data
    error = Signal(str)


class _RankingWorker(QRunnable):
    """在后台线程中获取排行榜数据"""
    def __init__(self, date_str: Optional[str] = None):
        super().__init__()
        self._date_str = date_str
        self.signals = _RankingWorkerSignals()

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
            resp = client.get_ranking(date_str=self._date_str)
            if isinstance(resp, dict):
                self.signals.finished.emit(resp)
            else:
                self.signals.error.emit("API 返回格式错误")
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"获取排行榜失败：{e}")
            return


class _MonthlyRankingWorker(QRunnable):
    """在后台线程中获取月度排行榜数据"""
    def __init__(self, month_str: Optional[str] = None):
        super().__init__()
        self._month_str = month_str
        self.signals = _RankingWorkerSignals()

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
            resp = client.get_monthly_ranking(month_str=self._month_str)
            if isinstance(resp, dict):
                self.signals.finished.emit(resp)
            else:
                self.signals.error.emit("API 返回格式错误")
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"获取月度排行榜失败：{e}")
            return


class _MonthlyRankExtWorker(QRunnable):
    """在后台线程中获取月度扩展排行榜数据（进步/四维）"""

    def __init__(self, rank_type: str, month_str: Optional[str] = None):
        super().__init__()
        self._rank_type = (rank_type or "").strip()
        self._month_str = month_str
        self.signals = _RankingWorkerSignals()

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
            resp = client.get_monthly_rank_ext(rank_type=self._rank_type, month_str=self._month_str)
            if isinstance(resp, dict):
                self.signals.finished.emit(resp)
            else:
                self.signals.error.emit("API 返回格式错误")
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"获取扩展月度排行榜失败：{e}")
            return


class RankingView(QWidget):
    def __init__(self):
        super().__init__()
        # 检测当前主题
        self._is_dark = self._detect_theme()
        
        # 保存当前数据，用于主题变化时重新渲染
        self._current_data = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        # 去掉整体外边框
        self.setStyleSheet("background-color: transparent;")

        # 标题
        title = QLabel("排行榜")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # TAB切换（日排名/月排名/进步&四维排名）
        self.tab_widget = QTabWidget()
        self.daily_tab = QWidget()
        self.monthly_tab = QWidget()
        self.progress_tab = QWidget()
        self.execution_tab = QWidget()
        self.quality_tab = QWidget()
        self.collaboration_tab = QWidget()
        self.reflection_tab = QWidget()
        self.tab_widget.addTab(self.daily_tab, "日排名")
        self.tab_widget.addTab(self.monthly_tab, "月排名")
        self.tab_widget.addTab(self.progress_tab, "进步排名")
        self.tab_widget.addTab(self.execution_tab, "执行力排名")
        self.tab_widget.addTab(self.quality_tab, "质量排名")
        self.tab_widget.addTab(self.collaboration_tab, "协作排名")
        self.tab_widget.addTab(self.reflection_tab, "思考排名")
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_widget)

        # 日排名TAB的内容
        daily_layout = QVBoxLayout(self.daily_tab)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        daily_layout.setSpacing(16)

        # 日期选择区域（日排名）
        filter_frame = QFrame()
        filter_frame.setFrameShape(QFrame.NoFrame)  # 去掉边框
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)

        date_label = QLabel("日期：")
        date_label.setStyleSheet("background-color: transparent;")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        # 先设置为今天，稍后会更新为最新评分日期
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.dateChanged.connect(self._on_date_changed)
        # 适配深色模式
        apply_theme_to_date_edit(self.date_edit)

        filter_layout.addWidget(date_label)
        filter_layout.addWidget(self.date_edit)
        filter_layout.addStretch()
        
        # 刷新按钮
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self._apply_button_theme(self.refresh_btn)
        filter_layout.addWidget(self.refresh_btn)

        # 去掉外边框
        filter_frame.setStyleSheet("background-color: transparent;")
        daily_layout.addWidget(filter_frame)

        # 月排名TAB的内容
        monthly_layout = QVBoxLayout(self.monthly_tab)
        monthly_layout.setContentsMargins(0, 0, 0, 0)
        monthly_layout.setSpacing(16)

        # 月份选择区域（月排名）- 照搬历史评分页面的样式
        monthly_filter_frame = QFrame()
        monthly_filter_layout = QHBoxLayout(monthly_filter_frame)
        monthly_filter_layout.setContentsMargins(12, 12, 12, 12)
        monthly_filter_layout.setSpacing(8)

        month_label = QLabel("月份：")
        month_label.setStyleSheet("background-color: transparent;")
        self.month_combo = QComboBox()
        self.month_combo.setMinimumWidth(150)
        # 填充月份下拉框（从2025-11到当前月份，对齐管理端的逻辑）
        self._populate_month_combo()
        # 应用主题适配（确保倒三角图标正确显示并支持动态主题切换）
        apply_theme_to_combo_box(self.month_combo)
        
        monthly_filter_layout.addWidget(month_label)
        monthly_filter_layout.addWidget(self.month_combo)
        monthly_filter_layout.addStretch()
        
        # 刷新按钮（确保在浅色模式下可见）
        self.monthly_refresh_btn = QPushButton("刷新")
        self.monthly_refresh_btn.clicked.connect(self._on_monthly_refresh_clicked)
        # 应用按钮主题样式，确保在浅色模式下可见
        self._apply_button_theme(self.monthly_refresh_btn)
        monthly_filter_layout.addWidget(self.monthly_refresh_btn)

        monthly_filter_frame.setProperty("class", "card")
        monthly_layout.addWidget(monthly_filter_frame)
        
        # 连接下拉框改变事件（和历史评分页面一样使用 currentIndexChanged）
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)

        # 进步排名TAB的内容（趋势型）
        progress_layout = QVBoxLayout(self.progress_tab)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(16)

        progress_filter_frame = QFrame()
        progress_filter_layout = QHBoxLayout(progress_filter_frame)
        progress_filter_layout.setContentsMargins(12, 12, 12, 12)
        progress_filter_layout.setSpacing(8)

        progress_month_label = QLabel("月份：")
        progress_month_label.setStyleSheet("background-color: transparent;")
        self.progress_month_combo = QComboBox()
        self.progress_month_combo.setMinimumWidth(150)
        self._populate_month_combo(self.progress_month_combo)
        apply_theme_to_combo_box(self.progress_month_combo)

        progress_filter_layout.addWidget(progress_month_label)
        progress_filter_layout.addWidget(self.progress_month_combo)
        progress_filter_layout.addStretch()

        self.progress_refresh_btn = QPushButton("刷新")
        self.progress_refresh_btn.clicked.connect(
            lambda checked=False: self._on_ext_refresh_clicked("progress", self.progress_month_combo)
        )
        self._apply_button_theme(self.progress_refresh_btn)
        progress_filter_layout.addWidget(self.progress_refresh_btn)

        progress_filter_frame.setProperty("class", "card")
        progress_layout.addWidget(progress_filter_frame)
        self.progress_month_combo.currentIndexChanged.connect(
            lambda idx, t="progress", cb=self.progress_month_combo: self._on_ext_month_changed(idx, t, cb)
        )

        progress_scroll = QScrollArea()
        progress_scroll.setWidgetResizable(True)
        progress_scroll.setFrameShape(QFrame.NoFrame)
        progress_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.progress_content_widget = QWidget()
        self.progress_content_layout = QVBoxLayout(self.progress_content_widget)
        self.progress_content_layout.setContentsMargins(12, 12, 12, 12)
        self.progress_content_layout.setSpacing(12)
        progress_scroll.setWidget(self.progress_content_widget)
        progress_layout.addWidget(progress_scroll)

        # 执行力排名TAB
        execution_layout = QVBoxLayout(self.execution_tab)
        execution_layout.setContentsMargins(0, 0, 0, 0)
        execution_layout.setSpacing(16)

        execution_filter_frame = QFrame()
        execution_filter_layout = QHBoxLayout(execution_filter_frame)
        execution_filter_layout.setContentsMargins(12, 12, 12, 12)
        execution_filter_layout.setSpacing(8)

        execution_month_label = QLabel("月份：")
        execution_month_label.setStyleSheet("background-color: transparent;")
        self.execution_month_combo = QComboBox()
        self.execution_month_combo.setMinimumWidth(150)
        self._populate_month_combo(self.execution_month_combo)
        apply_theme_to_combo_box(self.execution_month_combo)

        execution_filter_layout.addWidget(execution_month_label)
        execution_filter_layout.addWidget(self.execution_month_combo)
        execution_filter_layout.addStretch()

        self.execution_refresh_btn = QPushButton("刷新")
        self.execution_refresh_btn.clicked.connect(
            lambda checked=False: self._on_ext_refresh_clicked("execution", self.execution_month_combo)
        )
        self._apply_button_theme(self.execution_refresh_btn)
        execution_filter_layout.addWidget(self.execution_refresh_btn)

        execution_filter_frame.setProperty("class", "card")
        execution_layout.addWidget(execution_filter_frame)
        self.execution_month_combo.currentIndexChanged.connect(
            lambda idx, t="execution", cb=self.execution_month_combo: self._on_ext_month_changed(idx, t, cb)
        )

        execution_scroll = QScrollArea()
        execution_scroll.setWidgetResizable(True)
        execution_scroll.setFrameShape(QFrame.NoFrame)
        execution_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.execution_content_widget = QWidget()
        self.execution_content_layout = QVBoxLayout(self.execution_content_widget)
        self.execution_content_layout.setContentsMargins(12, 12, 12, 12)
        self.execution_content_layout.setSpacing(12)
        execution_scroll.setWidget(self.execution_content_widget)
        execution_layout.addWidget(execution_scroll)

        # 质量排名TAB
        quality_layout = QVBoxLayout(self.quality_tab)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(16)

        quality_filter_frame = QFrame()
        quality_filter_layout = QHBoxLayout(quality_filter_frame)
        quality_filter_layout.setContentsMargins(12, 12, 12, 12)
        quality_filter_layout.setSpacing(8)

        quality_month_label = QLabel("月份：")
        quality_month_label.setStyleSheet("background-color: transparent;")
        self.quality_month_combo = QComboBox()
        self.quality_month_combo.setMinimumWidth(150)
        self._populate_month_combo(self.quality_month_combo)
        apply_theme_to_combo_box(self.quality_month_combo)

        quality_filter_layout.addWidget(quality_month_label)
        quality_filter_layout.addWidget(self.quality_month_combo)
        quality_filter_layout.addStretch()

        self.quality_refresh_btn = QPushButton("刷新")
        self.quality_refresh_btn.clicked.connect(
            lambda checked=False: self._on_ext_refresh_clicked("quality", self.quality_month_combo)
        )
        self._apply_button_theme(self.quality_refresh_btn)
        quality_filter_layout.addWidget(self.quality_refresh_btn)

        quality_filter_frame.setProperty("class", "card")
        quality_layout.addWidget(quality_filter_frame)
        self.quality_month_combo.currentIndexChanged.connect(
            lambda idx, t="quality", cb=self.quality_month_combo: self._on_ext_month_changed(idx, t, cb)
        )

        quality_scroll = QScrollArea()
        quality_scroll.setWidgetResizable(True)
        quality_scroll.setFrameShape(QFrame.NoFrame)
        quality_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.quality_content_widget = QWidget()
        self.quality_content_layout = QVBoxLayout(self.quality_content_widget)
        self.quality_content_layout.setContentsMargins(12, 12, 12, 12)
        self.quality_content_layout.setSpacing(12)
        quality_scroll.setWidget(self.quality_content_widget)
        quality_layout.addWidget(quality_scroll)

        # 协作排名TAB
        collaboration_layout = QVBoxLayout(self.collaboration_tab)
        collaboration_layout.setContentsMargins(0, 0, 0, 0)
        collaboration_layout.setSpacing(16)

        collaboration_filter_frame = QFrame()
        collaboration_filter_layout = QHBoxLayout(collaboration_filter_frame)
        collaboration_filter_layout.setContentsMargins(12, 12, 12, 12)
        collaboration_filter_layout.setSpacing(8)

        collaboration_month_label = QLabel("月份：")
        collaboration_month_label.setStyleSheet("background-color: transparent;")
        self.collaboration_month_combo = QComboBox()
        self.collaboration_month_combo.setMinimumWidth(150)
        self._populate_month_combo(self.collaboration_month_combo)
        apply_theme_to_combo_box(self.collaboration_month_combo)

        collaboration_filter_layout.addWidget(collaboration_month_label)
        collaboration_filter_layout.addWidget(self.collaboration_month_combo)
        collaboration_filter_layout.addStretch()

        self.collaboration_refresh_btn = QPushButton("刷新")
        self.collaboration_refresh_btn.clicked.connect(
            lambda checked=False: self._on_ext_refresh_clicked("collaboration", self.collaboration_month_combo)
        )
        self._apply_button_theme(self.collaboration_refresh_btn)
        collaboration_filter_layout.addWidget(self.collaboration_refresh_btn)

        collaboration_filter_frame.setProperty("class", "card")
        collaboration_layout.addWidget(collaboration_filter_frame)
        self.collaboration_month_combo.currentIndexChanged.connect(
            lambda idx, t="collaboration", cb=self.collaboration_month_combo: self._on_ext_month_changed(idx, t, cb)
        )

        collaboration_scroll = QScrollArea()
        collaboration_scroll.setWidgetResizable(True)
        collaboration_scroll.setFrameShape(QFrame.NoFrame)
        collaboration_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.collaboration_content_widget = QWidget()
        self.collaboration_content_layout = QVBoxLayout(self.collaboration_content_widget)
        self.collaboration_content_layout.setContentsMargins(12, 12, 12, 12)
        self.collaboration_content_layout.setSpacing(12)
        collaboration_scroll.setWidget(self.collaboration_content_widget)
        collaboration_layout.addWidget(collaboration_scroll)

        # 思考排名TAB
        reflection_layout = QVBoxLayout(self.reflection_tab)
        reflection_layout.setContentsMargins(0, 0, 0, 0)
        reflection_layout.setSpacing(16)

        reflection_filter_frame = QFrame()
        reflection_filter_layout = QHBoxLayout(reflection_filter_frame)
        reflection_filter_layout.setContentsMargins(12, 12, 12, 12)
        reflection_filter_layout.setSpacing(8)

        reflection_month_label = QLabel("月份：")
        reflection_month_label.setStyleSheet("background-color: transparent;")
        self.reflection_month_combo = QComboBox()
        self.reflection_month_combo.setMinimumWidth(150)
        self._populate_month_combo(self.reflection_month_combo)
        apply_theme_to_combo_box(self.reflection_month_combo)

        reflection_filter_layout.addWidget(reflection_month_label)
        reflection_filter_layout.addWidget(self.reflection_month_combo)
        reflection_filter_layout.addStretch()

        self.reflection_refresh_btn = QPushButton("刷新")
        self.reflection_refresh_btn.clicked.connect(
            lambda checked=False: self._on_ext_refresh_clicked("reflection", self.reflection_month_combo)
        )
        self._apply_button_theme(self.reflection_refresh_btn)
        reflection_filter_layout.addWidget(self.reflection_refresh_btn)

        reflection_filter_frame.setProperty("class", "card")
        reflection_layout.addWidget(reflection_filter_frame)
        self.reflection_month_combo.currentIndexChanged.connect(
            lambda idx, t="reflection", cb=self.reflection_month_combo: self._on_ext_month_changed(idx, t, cb)
        )

        reflection_scroll = QScrollArea()
        reflection_scroll.setWidgetResizable(True)
        reflection_scroll.setFrameShape(QFrame.NoFrame)
        reflection_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.reflection_content_widget = QWidget()
        self.reflection_content_layout = QVBoxLayout(self.reflection_content_widget)
        self.reflection_content_layout.setContentsMargins(12, 12, 12, 12)
        self.reflection_content_layout.setSpacing(12)
        reflection_scroll.setWidget(self.reflection_content_widget)
        reflection_layout.addWidget(reflection_scroll)

        # 日排名内容区域（可滚动）
        daily_scroll = QScrollArea()
        daily_scroll.setWidgetResizable(True)
        daily_scroll.setFrameShape(QFrame.NoFrame)
        daily_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.daily_content_widget = QWidget()
        self.daily_content_layout = QVBoxLayout(self.daily_content_widget)
        self.daily_content_layout.setContentsMargins(12, 12, 12, 12)  # 增加内边距
        self.daily_content_layout.setSpacing(12)

        daily_scroll.setWidget(self.daily_content_widget)
        daily_layout.addWidget(daily_scroll)

        # 月排名内容区域（可滚动）
        monthly_scroll = QScrollArea()
        monthly_scroll.setWidgetResizable(True)
        monthly_scroll.setFrameShape(QFrame.NoFrame)
        monthly_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.monthly_content_widget = QWidget()
        self.monthly_content_layout = QVBoxLayout(self.monthly_content_widget)
        self.monthly_content_layout.setContentsMargins(12, 12, 12, 12)  # 增加内边距
        self.monthly_content_layout.setSpacing(12)

        monthly_scroll.setWidget(self.monthly_content_widget)
        monthly_layout.addWidget(monthly_scroll)

        # 保存引用以便后续使用
        self.content_widget = self.daily_content_widget
        self.content_layout = self.daily_content_layout

        # 初始化标志
        self._is_initializing = True
        # 事件过滤器列表（用于管理hover效果）
        self._event_filter_frames = []
        
        # 主题变化检测定时器
        self._theme_check_timer = QTimer(self)
        self._theme_check_timer.timeout.connect(self._check_theme_change)
        self._theme_check_timer.start(500)  # 每500ms检测一次
        
        # 当前TAB索引
        # 0=日排名，1=月排名，2=进步排名，3=执行力排名，4=质量排名，5=协作排名，6=思考排名
        self._current_tab_index = 0
        # 当前“月度类TAB”的月份下拉框引用（用于渲染时同步选择状态）
        self._active_month_combo: Optional[QComboBox] = None
        # 日排名接口在 date=None 时返回的“默认日期”（最后有数据的工作日）
        # 月榜默认月份需要跟随该日期所属月份（而不是跟随本机今天）
        self._default_rank_date: Optional[date] = None
        # 标记最近一次日榜请求是否为“默认日期请求”（date_str=None）
        self._last_daily_request_was_default: bool = False

        # 首次加载时，直接请求日排行榜（不传日期，后端会返回上一个工作日的数据）
        # 延迟到下一事件循环，确保UI完全初始化后再加载
        from PySide6.QtCore import QTimer as SingleShotTimer
        SingleShotTimer.singleShot(100, lambda: self._load_ranking(date_str=None))

    def refresh_from_api(self, silent: bool = False):
        """从API刷新数据（供外部调用，如登录成功后）"""
        # 检查登录状态，未登录时不发起请求
        if not ApiClient.is_logged_in():
            if not silent:
                from widgets.toast import Toast
                Toast.show_message(self, "请先登录")
            return
        
        # 根据当前TAB加载对应的数据
        self._clear_content()
        loading_label = QLabel("加载中…")
        loading_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(loading_label)
        self._is_initializing = True

        if self._current_tab_index == 0:
            # 日排名
            self._load_ranking(date_str=None)
            return

        if self._current_tab_index == 1:
            # 月排名（综合）
            self._load_monthly_ranking(month_str=None)
            return

        # 扩展月榜
        tab_to_type = {
            2: "progress",
            3: "execution",
            4: "quality",
            5: "collaboration",
            6: "reflection",
        }
        rank_type = tab_to_type.get(self._current_tab_index)
        if rank_type:
            self._load_monthly_rank_ext(rank_type=rank_type, month_str=None)
            return
        # 兜底：回到月排名
        self._load_monthly_ranking(month_str=None)

    def _on_tab_changed(self, index: int):
        """TAB切换事件"""
        self._current_tab_index = index
        if index == 0:
            # 切换到日排名
            self.content_widget = self.daily_content_widget
            self.content_layout = self.daily_content_layout
            self._active_month_combo = None
            self._load_ranking(date_str=None)
            return

        if index == 1:
            # 切换到月排名（综合）
            self.content_widget = self.monthly_content_widget
            self.content_layout = self.monthly_content_layout
            self._active_month_combo = self.month_combo
            self._load_monthly_ranking(month_str=None)
            return

        # 扩展月榜（进步/四维）
        tab_map = {
            2: ("progress", self.progress_content_widget, self.progress_content_layout, self.progress_month_combo),
            3: ("execution", self.execution_content_widget, self.execution_content_layout, self.execution_month_combo),
            4: ("quality", self.quality_content_widget, self.quality_content_layout, self.quality_month_combo),
            5: ("collaboration", self.collaboration_content_widget, self.collaboration_content_layout, self.collaboration_month_combo),
            6: ("reflection", self.reflection_content_widget, self.reflection_content_layout, self.reflection_month_combo),
        }
        cfg = tab_map.get(index)
        if cfg:
            rank_type, cw, cl, combo = cfg
            self.content_widget = cw
            self.content_layout = cl
            self._active_month_combo = combo
            self._load_monthly_rank_ext(rank_type=rank_type, month_str=None)
            return

        # 兜底
        self.content_widget = self.monthly_content_widget
        self.content_layout = self.monthly_content_layout
        self._active_month_combo = self.month_combo
        self._load_monthly_ranking(month_str=None)

    def _on_date_changed(self):
        """日期改变时重新加载排行榜"""
        # 如果正在初始化，不触发加载（会在初始化完成后手动调用）
        if self._is_initializing:
            return
        # 用户手动改变日期时，使用日期选择器的值
        selected_date = self.date_edit.date().toPython()
        date_str = selected_date.isoformat()
        self._load_ranking(date_str=date_str)
    
    def _on_refresh_clicked(self):
        """刷新按钮点击事件（日排名）"""
        # 刷新时，不传日期，让后端返回上一个工作日的数据
        self._load_ranking(date_str=None)

    def _populate_month_combo(self, combo: Optional[QComboBox] = None):
        """填充月份下拉框（从2025-11到当前月份，对齐管理端的逻辑）"""
        combo = combo or self.month_combo
        combo.clear()
        
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
            combo.addItem(display_str, month_str)
            
            # 移动到下一个月
            month += 1
            if month > 12:
                month = 1
                year += 1
        
        # 默认选中当前月份（最后一个）
        if combo.count() > 0:
            combo.setCurrentIndex(combo.count() - 1)
    
    def _on_month_changed(self, index: int):
        """月份下拉框改变时重新加载月排名（对齐管理端的逻辑）"""
        # 如果正在初始化，不触发加载
        if self._is_initializing:
            return
        # 用户手动改变月份时，使用下拉框的 itemData（YYYY-MM格式）
        month_str_data = self.month_combo.itemData(index)
        if month_str_data:
            # 拼接 "-01" 转换为 YYYY-MM-DD 格式
            month_str = f"{month_str_data}-01"
        else:
            # 如果没有 itemData，使用 itemText 解析
            month_text = self.month_combo.itemText(index)
            # 从 "YYYY年MM月" 格式解析
            try:
                year_str, month_str_part = month_text.replace("年", "-").replace("月", "").split("-")
                month_str = f"{year_str}-{month_str_part}-01"
            except Exception:
                # 如果解析失败，使用当前月份
                today = date.today()
                month_str = f"{today.year}-{today.month:02d}-01"
        self._load_monthly_ranking(month_str=month_str)

    def _on_monthly_refresh_clicked(self):
        """刷新按钮点击事件（月排名）"""
        # 刷新时，使用当前选择的月份
        current_index = self.month_combo.currentIndex()
        month_str_data = self.month_combo.itemData(current_index)
        if month_str_data:
            # 拼接 "-01" 转换为 YYYY-MM-DD 格式
            month_str = f"{month_str_data}-01"
        else:
            # 如果没有 itemData，使用 itemText 解析
            month_text = self.month_combo.currentText()
            try:
                year_str, month_str_part = month_text.replace("年", "-").replace("月", "").split("-")
                month_str = f"{year_str}-{month_str_part}-01"
            except Exception:
                # 如果解析失败，使用当前月份
                today = date.today()
                month_str = f"{today.year}-{today.month:02d}-01"
        self._load_monthly_ranking(month_str=month_str)

    def _get_month_start_str_from_combo(self, combo: QComboBox, index: Optional[int] = None) -> str:
        """从月份下拉框取 YYYY-MM-DD（月初）"""
        if index is None:
            index = combo.currentIndex()
        month_str_data = combo.itemData(index)
        if month_str_data:
            return f"{month_str_data}-01"
        month_text = combo.itemText(index) or combo.currentText()
        try:
            year_str, month_str_part = month_text.replace("年", "-").replace("月", "").split("-")
            return f"{year_str}-{month_str_part}-01"
        except Exception:
            today = date.today()
            return f"{today.year}-{today.month:02d}-01"

    def _on_ext_month_changed(self, index: int, rank_type: str, combo: QComboBox):
        """扩展月榜：月份变化"""
        if self._is_initializing:
            return
        month_str = self._get_month_start_str_from_combo(combo, index=index)
        self._load_monthly_rank_ext(rank_type=rank_type, month_str=month_str)

    def _on_ext_refresh_clicked(self, rank_type: str, combo: QComboBox):
        """扩展月榜：刷新按钮"""
        month_str = self._get_month_start_str_from_combo(combo, index=None)
        self._load_monthly_rank_ext(rank_type=rank_type, month_str=month_str)

    def _load_ranking(self, date_str: Optional[str] = None):
        """加载日排行榜数据"""
        # 如果 date_str 是 None，表示不传日期参数，后端会返回上一个工作日的数据
        # 如果 date_str 不是 None，使用指定的日期
        self._last_daily_request_was_default = date_str is None
        
        # 显示加载中
        self._clear_content()
        loading_label = QLabel("加载中…")
        loading_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(loading_label)

        # 后台加载
        # 如果 date_str 是 None，不传日期参数给API
        worker = _RankingWorker(date_str=date_str)
        worker.signals.finished.connect(self._on_load_finished)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)

    def _load_monthly_ranking(self, month_str: Optional[str] = None):
        """加载月度排行榜数据"""
        # 如果 month_str 是 None，表示不传月份参数：
        # - 旧逻辑：后端会返回“当前月份”的数据
        # - 新逻辑：默认月份需要跟随“日排名默认日期”所属月份（最后有数据的工作日）
        # 如果 month_str 不是 None，使用指定的月份
        if month_str is None:
            default_month = self._get_default_month_start_str()
            if default_month:
                month_str = default_month
        
        # 显示加载中
        self._clear_content()
        loading_label = QLabel("加载中…")
        loading_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(loading_label)

        # 后台加载
        worker = _MonthlyRankingWorker(month_str=month_str)
        worker.signals.finished.connect(self._on_monthly_load_finished)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)

    def _load_monthly_rank_ext(self, rank_type: str, month_str: Optional[str] = None):
        """加载月度扩展排行榜数据（进步/四维）"""
        if month_str is None:
            default_month = self._get_default_month_start_str()
            if default_month:
                month_str = default_month
        self._clear_content()
        loading_label = QLabel("加载中…")
        loading_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(loading_label)

        worker = _MonthlyRankExtWorker(rank_type=rank_type, month_str=month_str)
        worker.signals.finished.connect(self._on_monthly_ext_load_finished)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)

    def _clear_content(self):
        """清空内容区域"""
        # 先移除事件过滤器，避免访问已删除的对象
        for frame in getattr(self, '_event_filter_frames', []):
            try:
                if frame:
                    frame.removeEventFilter(self)
            except Exception:
                pass
        self._event_filter_frames.clear()
        
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                # 移除事件过滤器
                try:
                    widget.removeEventFilter(self)
                except Exception:
                    pass
                widget.deleteLater()

    def _on_load_finished(self, data: Dict[str, Any]):
        """日排行榜加载完成"""
        # 保存数据，用于主题变化时重新渲染
        self._current_data = data
        # 渲染数据
        self._render_ranking_data(data, is_monthly=False)

    def _on_monthly_load_finished(self, data: Dict[str, Any]):
        """月度排行榜加载完成"""
        # 保存数据，用于主题变化时重新渲染
        self._current_data = data
        # 渲染数据
        self._render_ranking_data(data, is_monthly=True)

    def _on_monthly_ext_load_finished(self, data: Dict[str, Any]):
        """月度扩展排行榜加载完成"""
        self._current_data = data
        self._render_ranking_data(data, is_monthly=True)
    
    def _render_ranking_data(self, data: Dict[str, Any], is_monthly: bool = False):
        """渲染排行榜数据"""
        self._clear_content()
        # 避免仅在分支内赋值导致 UnboundLocalError
        month_str: str = ""
        rank_type: Optional[str] = None

        if data.get("status") != "success":
            error_msg = data.get("message") or "加载失败"
            error_label = QLabel(f"加载失败：{error_msg}")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: red;")
            self.content_layout.addWidget(error_label)
            self._is_initializing = False
            return

        if is_monthly:
            # 月度排行榜
            month_str = data.get("month", "")
            locked = data.get("locked", False)
            rank_type = data.get("rank_type")  # 扩展月榜会带该字段
            rank_type_name = data.get("rank_type_name") or ""
            if month_str:
                try:
                    # 解析月份字符串（可能是 YYYY-MM-DD 或 YYYY-MM 格式）
                    d = date.fromisoformat(month_str)
                    month_key = f"{d.year}-{d.month:02d}"  # YYYY-MM 格式
                    # 使用 blockSignals 临时阻止信号，避免触发加载
                    combo = self._active_month_combo or self.month_combo
                    combo.blockSignals(True)
                    # 通过 itemData 查找匹配的月份
                    index = -1
                    for i in range(combo.count()):
                        if combo.itemData(i) == month_key:
                            index = i
                            break
                    if index >= 0:
                        combo.setCurrentIndex(index)
                    combo.blockSignals(False)
                except Exception:
                    pass

            # 初始化完成
            self._is_initializing = False

            # 显示月份信息
            total_participants = data.get("total_participants", 0)
            locked_text = "（已锁定）" if locked else "（未锁定）"
            prefix = f"{rank_type_name} | " if rank_type_name else ""
            # 四维榜额外展示历史最高（单日最高分）
            history_best_text = ""
            best_user_id = None
            best_user_name = None
            best_date = None
            if rank_type in ("execution", "quality", "collaboration", "reflection"):
                best_score = data.get("history_best_score")
                best_date = data.get("history_best_date")
                best_user_id = data.get("history_best_user_id")
                best_user_name = data.get("history_best_user_name") or best_user_id
                score_str = self._format_history_best_score(best_score)
                if score_str and best_date:
                    # 目标：分数（姓名 日期）
                    name_part = best_user_name or best_user_id or "未知"
                    history_best_text = f" | 历史最高：{score_str}（{name_part} {best_date}）"

            # 信息行：为“历史最高”提供 hover 查看按钮（对齐日榜“对比”交互）
            info_frame = QFrame()
            info_layout = QHBoxLayout(info_frame)
            info_layout.setContentsMargins(0, 0, 0, 0)
            info_layout.setSpacing(4)

            base_text = f"{prefix}月份：{month_str} | 参与排名：{total_participants} 人 {locked_text}"
            month_info = QLabel(base_text)
            month_info.setFont(QFont("Arial", 10))
            if self._is_dark:
                month_info.setStyleSheet("color: #aaaaaa; background-color: transparent;")
            else:
                month_info.setStyleSheet("background-color: transparent;")
            info_layout.addWidget(month_info)

            # 历史最高片段 + 迷你查看按钮（hover显示）
            if history_best_text:
                history_label = QLabel(history_best_text)
                history_label.setFont(QFont("Arial", 10))
                if self._is_dark:
                    history_label.setStyleSheet("color: #aaaaaa; background-color: transparent;")
                else:
                    history_label.setStyleSheet("background-color: transparent;")
                info_layout.addWidget(history_label)

            if (
                rank_type in ("execution", "quality", "collaboration", "reflection")
                and best_user_id
                and best_date
                and history_best_text
            ):
                history_view_btn = QPushButton("查看")
                history_view_btn.setFixedSize(44, 20)
                history_view_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(74, 144, 226, 0.12);
                        color: #4a90e2;
                        border: 1px solid rgba(74, 144, 226, 0.65);
                        border-radius: 10px;
                        font-size: 9pt;
                        padding: 0 6px;
                    }
                    QPushButton:hover {
                        background-color: #4a90e2;
                        color: white;
                        border: 1px solid #4a90e2;
                    }
                """)
                history_view_btn.clicked.connect(
                    lambda checked=False, d=str(best_date), u=str(best_user_id), n=str(best_user_name): self._show_daily_input(d, u, n)
                )
                history_view_btn.setVisible(False)
                info_frame.setProperty("history_view_btn", history_view_btn)
                info_frame.installEventFilter(self)
                self._event_filter_frames.append(info_frame)
                info_layout.addWidget(history_view_btn)

            info_layout.addStretch()
            # 设置 tooltip：根据榜单类型展示不同规则
            lock_rule = (
                "排名锁定规则：<br/>"
                "当月最后一个工作日的排名锁定 = 月评分锁定<br/>"
                + ("排名已锁定（当月最后一个工作日的排名已锁定）" if locked else "否则，未锁定")
            )

            if rank_type == "progress":
                rule = (
                    "进步排名规则：<br/>"
                    "按趋势分（trend_score）降序排列<br/>"
                    "趋势分 = 当月 total_ai 的线性回归斜率（分/自然日） × 有效工作日权重（有效工作日数/当月总工作日数）<br/><br/>"
                )
            elif rank_type == "execution":
                rule = (
                    "执行力排名规则：<br/>"
                    "按执行力月均分降序排列<br/>"
                    "执行力月均 = 当月 eligible=1 工作日的（execution × confidence）平均值<br/><br/>"
                )
            elif rank_type == "quality":
                rule = (
                    "质量排名规则：<br/>"
                    "按质量月均分降序排列<br/>"
                    "质量月均 = 当月 eligible=1 工作日的（quality × confidence）平均值<br/><br/>"
                )
            elif rank_type == "collaboration":
                rule = (
                    "协作排名规则：<br/>"
                    "按协作月均分降序排列<br/>"
                    "协作月均 = 当月 eligible=1 工作日的（collaboration × confidence）平均值<br/><br/>"
                )
            elif rank_type == "reflection":
                rule = (
                    "思考排名规则：<br/>"
                    "按思考月均分降序排列<br/>"
                    "思考月均 = 当月 eligible=1 工作日的（reflection × confidence）平均值<br/><br/>"
                )
            else:
                rule = (
                    "月度排名规则：<br/>"
                    "按最终综合分（final_score）降序排列<br/>"
                    "最终综合分 = 0.7 × 当月AI均分 + 0.2 × 工资贡献率 + 0.1 × 成长率<br/><br/>"
                )
            month_info.setToolTip(rule + lock_rule)
            self.content_layout.addWidget(info_frame)
        else:
            # 日排行榜
            # 从返回数据中获取日期，更新日期选择器
            date_str = data.get("date", "")
            if date_str:
                try:
                    d = date.fromisoformat(date_str)
                    # 仅当本次日榜请求为“默认日期请求”（date_str=None）时，才更新默认日期
                    if self._last_daily_request_was_default:
                        self._default_rank_date = d
                    qdate = QDate(d.year, d.month, d.day)
                    # 使用 blockSignals 临时阻止信号，避免触发加载
                    self.date_edit.blockSignals(True)
                    self.date_edit.setDate(qdate)
                    self.date_edit.blockSignals(False)
                except Exception:
                    pass  # 如果日期解析失败，忽略

            # 初始化完成
            self._is_initializing = False

            # 显示日期信息
            total_participants = data.get("total_participants", 0)
            date_info = QLabel(f"日期：{date_str} | 参与排名：{total_participants} 人")
            date_info.setFont(QFont("Arial", 10))
            if self._is_dark:
                date_info.setStyleSheet("color: #aaaaaa; background-color: transparent;")
            else:
                date_info.setStyleSheet("background-color: transparent;")
            self.content_layout.addWidget(date_info)

        # 显示前十名
        top_10 = data.get("top_10", [])
        if top_10:
            # 月排名（综合）：奖牌/样式规则
            # 文档规则：连续两个月进入前三的人，第二个月仍发奖金，但不占名次（奖牌顺延给后面的人）。
            award_place_by_user_id: Dict[str, int] = {}
            repeat_award_user_ids: set[str] = set()
            if is_monthly and not data.get("rank_type"):
                award_list: list[str] = []
                for it in top_10:
                    if not isinstance(it, dict):
                        continue
                    uid = str(it.get("user_id") or "")
                    if not uid:
                        continue
                    try:
                        cur_rank = int(it.get("rank") or 0)
                    except Exception:
                        cur_rank = 0
                    # 仅“本月前三”才可能触发连续占位规则
                    repeat_top3 = False
                    if cur_rank in (1, 2, 3):
                        rc = it.get("rank_change")
                        if rc is not None:
                            try:
                                prev_rank = int(rc) + int(cur_rank)  # prev_rank - cur_rank = rank_change
                                if prev_rank in (1, 2, 3):
                                    repeat_top3 = True
                            except Exception:
                                repeat_top3 = False
                    if repeat_top3:
                        repeat_award_user_ids.add(uid)
                        continue
                    award_list.append(uid)
                    if len(award_list) >= 3:
                        break
                award_place_by_user_id = {u: idx + 1 for idx, u in enumerate(award_list)}

            # 日排名：计算“我的分数”，用于限制对比按钮（只能对比分高于自己的人）
            my_total_ai: Optional[int] = None
            if not is_monthly:
                # 若 current_user_rank 存在，说明我不在 Top10
                cur_item = data.get("current_user_rank") or {}
                if isinstance(cur_item, dict) and cur_item.get("total_ai") is not None:
                    try:
                        my_total_ai = int(cur_item.get("total_ai"))
                    except Exception:
                        my_total_ai = None
                else:
                    # 我在 Top10：从 top_10 中找 is_current_user
                    try:
                        for it in top_10:
                            if isinstance(it, dict) and it.get("is_current_user"):
                                if it.get("total_ai") is not None:
                                    my_total_ai = int(it.get("total_ai"))
                                break
                    except Exception:
                        my_total_ai = None

            top_10_label = QLabel("🏆 前十名")
            top_10_label.setFont(QFont("Arial", 14, QFont.Bold))
            if self._is_dark:
                top_10_label.setStyleSheet("color: #ffffff; background-color: transparent;")
            else:
                top_10_label.setStyleSheet("background-color: transparent;")
            self.content_layout.addWidget(top_10_label)

            allow_monthly_detail = bool(is_monthly and not data.get("rank_type"))
            # 新增的扩展排名（有 rank_type）不显示奖杯和特殊样式
            show_medal_and_style = bool(not data.get("rank_type"))
            for item in top_10:
                allow_compare = True
                allow_daily_view = False
                if not is_monthly:
                    # 仅对比分高于自己的人
                    try:
                        target_ai = int(item.get("total_ai", 0) or 0)
                        if my_total_ai is not None and target_ai <= my_total_ai:
                            allow_compare = False
                    except Exception:
                        pass
                    # 仅 Top10 开放“查看”
                    allow_daily_view = True

                # 月排名（综合）：奖牌与高亮按“领奖Top3”而不是按名次Top3
                award_place = None
                if is_monthly and not data.get("rank_type"):
                    try:
                        uid = str(item.get("user_id") or "")
                        award_place = award_place_by_user_id.get(uid)
                    except Exception:
                        award_place = None
                is_award_top3 = bool(award_place in (1, 2, 3))
                uid_for_award = str(item.get("user_id") or "")
                is_repeat_awarder = bool(is_monthly and not data.get("rank_type") and uid_for_award in repeat_award_user_ids)
                # 连续获奖者：仍然保留“Top3样式”，但不拿奖牌
                is_top3_style = (
                    (is_award_top3 or is_repeat_awarder) and show_medal_and_style
                ) if (is_monthly and not data.get("rank_type")) else (item.get("rank", 0) <= 3 and show_medal_and_style)

                # 名字后缀：连续获奖者/并列名次（名次顺延导致）
                suffix = ""
                if is_repeat_awarder:
                    suffix = "（连续获奖者，不占位）"
                else:
                    try:
                        cur_rank = int(item.get("rank") or 0)
                    except Exception:
                        cur_rank = 0
                    # 领奖名次小于实际名次，说明名次被“占位”产生顺延：标记并列第x名
                    if is_award_top3 and cur_rank and award_place and award_place < cur_rank:
                        suffix = f"（并列第{award_place}名）"

                rank_item_widget = self._create_rank_item(
                    item,
                    is_top_3=is_top3_style,
                    is_monthly=is_monthly,
                    allow_monthly_detail=allow_monthly_detail,
                    rank_type=(rank_type if is_monthly else None),
                    month_str_for_detail=(str(month_str) if (is_monthly and month_str) else None),
                    allow_compare=allow_compare,
                    allow_daily_view=allow_daily_view,
                    medal_rank=award_place,
                    # 占位者（连续获奖者）也要显示奖牌，但不占位：
                    # - 占位者：award_place 为空，奖牌按其真实名次 rank 展示
                    # - 领奖者：award_place 为 1/2/3，奖牌按并列名次展示
                    show_medal=(is_award_top3 or is_repeat_awarder),
                    name_suffix=suffix,
                )
                self.content_layout.addWidget(rank_item_widget)

        # 显示当前用户排名（如果不在前十）
        current_user_rank = data.get("current_user_rank")
        if current_user_rank:
            self.content_layout.addSpacing(20)
            current_user_label = QLabel("📍 我的排名")
            current_user_label.setFont(QFont("Arial", 14, QFont.Bold))
            if self._is_dark:
                current_user_label.setStyleSheet("color: #ffffff; background-color: transparent;")
            else:
                current_user_label.setStyleSheet("background-color: transparent;")
            self.content_layout.addWidget(current_user_label)

            allow_monthly_detail = bool(is_monthly and not data.get("rank_type"))
            # 新增的扩展排名（有 rank_type）不显示奖杯和特殊样式
            show_medal_and_style = bool(not data.get("rank_type"))
            current_user_widget = self._create_rank_item(
                current_user_rank,
                is_top_3=(current_user_rank.get("rank", 0) <= 3 and show_medal_and_style),
                is_current_user=True,
                is_monthly=is_monthly,
                allow_monthly_detail=allow_monthly_detail,
                rank_type=(rank_type if is_monthly else None),
                month_str_for_detail=(str(month_str) if (is_monthly and month_str) else None),
                allow_compare=False,
                allow_daily_view=False,
            )
            self.content_layout.addWidget(current_user_widget)

        self.content_layout.addStretch()

    def _get_default_month_start_str(self) -> Optional[str]:
        """从“日排名默认日期”（最后有数据的工作日）推导默认月份（月初，YYYY-MM-DD）。"""
        if not self._default_rank_date:
            return None
        return f"{self._default_rank_date.year}-{self._default_rank_date.month:02d}-01"

    @staticmethod
    def _format_history_best_score(val: Any) -> Optional[str]:
        """历史最高分展示：整数去小数，非整数保留 2 位。"""
        if val is None:
            return None
        try:
            f = float(val)
        except Exception:
            return None
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.2f}"

    @staticmethod
    def _apply_tooltip_theme(is_dark: bool) -> None:
        """
        Tooltip 是独立顶层窗口，不会继承 Dialog 的样式表；
        这里用全局 palette 强制适配深浅色，确保 hover 提示可读。
        """
        try:
            pal = QToolTip.palette()
            if is_dark:
                pal.setColor(QPalette.ToolTipBase, QColor("#2a2a2a"))
                pal.setColor(QPalette.ToolTipText, QColor("#E8EAED"))
            else:
                pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
                pal.setColor(QPalette.ToolTipText, QColor("#222222"))
            QToolTip.setPalette(pal)
        except Exception:
            pass

    def _on_load_error(self, message: str):
        """排行榜加载失败"""
        self._clear_content()
        self._is_initializing = False
        
        # 检查是否需要登录
        if any(key in message for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            # 未登录，显示提示
            error_label = QLabel("请先登录以查看排行榜")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: #999; font-size: 12pt; padding: 40px;")
            self.content_layout.addWidget(error_label)
            
            # 弹出登录对话框
            win = self.window()
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        # 登录成功：按当前TAB重新加载
                        self.refresh_from_api(silent=True)
        else:
            # 其他错误
            error_label = QLabel(f"加载失败：{message}")
            error_label.setAlignment(Qt.AlignCenter)
            error_label.setStyleSheet("color: red;")
            self.content_layout.addWidget(error_label)

    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False
    
    def _apply_button_theme(self, button: QPushButton):
        """为按钮应用主题样式"""
        is_dark = self._is_dark
        if is_dark:
            button.setStyleSheet("""
                QPushButton {
                    background-color: #2a2a2a;
                    color: #ffffff;
                    border: 1px solid #404040;
                    border-radius: 4px;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background-color: #3a3a3a;
                    border: 1px solid #555555;
                }
                QPushButton:pressed {
                    background-color: #1a1a1a;
                }
            """)
        else:
            button.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 12px;
                }
                QPushButton:hover {
                    background-color: #f0f0f0;
                    border: 1px solid #999999;
                }
                QPushButton:pressed {
                    background-color: #e0e0e0;
                }
            """)
    
    def _check_theme_change(self):
        """检测主题变化并更新所有UI元素"""
        current_is_dark = self._detect_theme()
        if current_is_dark != self._is_dark:
            # 主题已变化，更新主题状态
            self._is_dark = current_is_dark
            # 重新应用日期选择器的主题
            apply_theme_to_date_edit(self.date_edit)
            # 重新应用月份下拉框的主题（支持动态主题切换）
            apply_theme_to_combo_box(self.month_combo)
            apply_theme_to_combo_box(self.progress_month_combo)
            apply_theme_to_combo_box(self.execution_month_combo)
            apply_theme_to_combo_box(self.quality_month_combo)
            apply_theme_to_combo_box(self.collaboration_month_combo)
            apply_theme_to_combo_box(self.reflection_month_combo)
            # 重新应用按钮主题
            self._apply_button_theme(self.refresh_btn)
            self._apply_button_theme(self.monthly_refresh_btn)
            self._apply_button_theme(self.progress_refresh_btn)
            self._apply_button_theme(self.execution_refresh_btn)
            self._apply_button_theme(self.quality_refresh_btn)
            self._apply_button_theme(self.collaboration_refresh_btn)
            self._apply_button_theme(self.reflection_refresh_btn)
            # 如果有数据，重新渲染排名项
            if self._current_data:
                # 根据当前TAB判断是日排名还是月度类排名
                is_monthly = self._current_tab_index != 0
                self._render_ranking_data(self._current_data, is_monthly=is_monthly)
    
    def showEvent(self, event: QEvent):
        """页面显示时，立即检测并更新主题"""
        super().showEvent(event)
        # 立即检测一次主题变化
        self._check_theme_change()

    def _create_rank_item(
        self,
        item: Dict[str, Any],
        is_top_3: bool = False,
        is_current_user: bool = False,
        is_monthly: bool = False,
        allow_monthly_detail: bool = False,
        rank_type: Optional[str] = None,
        month_str_for_detail: Optional[str] = None,
        allow_compare: bool = True,
        allow_daily_view: bool = False,
        medal_rank: Optional[int] = None,
        show_medal: bool = True,
        name_suffix: str = "",
    ) -> QFrame:
        """创建单个排名项"""
        rank = item.get("rank", 0)
        name = item.get("name") or item.get("user_id", "未知")
        email = item.get("email") or ""
        # 日排名使用 total_ai，月排名使用 final_score
        if is_monthly:
            score_display = item.get("score_display")
            if score_display:
                score_text = str(score_display)
            elif "score" in item:
                score_value = float(item.get("score", 0.0) or 0.0)
                score_text = f"{score_value:.2f} 分"
            else:
                score_value = float(item.get("final_score", 0.0) or 0.0)
                score_text = f"{score_value:.2f} 分"
        else:
            score_value = item.get("total_ai", 0)
            score_text = f"{score_value} 分"
        rank_change = item.get("rank_change")
        is_me = item.get("is_current_user", False) or is_current_user

        # 创建容器
        frame = QFrame()
        frame.setProperty("class", "card")
        
        # 根据主题和类型设置样式
        if self._is_dark:
            if is_top_3:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: #2a2418;
                        border: 2px solid #d4af37;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)
            elif is_me:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: #1a2d3f;
                        border: 2px solid #4a90e2;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)
            else:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: #2a2a2a;
                        border: 1px solid #404040;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)
        else:
            if is_top_3:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: #fff9e6;
                        border: 2px solid #ffd700;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)
            elif is_me:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: #e6f3ff;
                        border: 2px solid #4a90e2;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)
            else:
                frame.setStyleSheet("""
                    QFrame[class="card"] {
                        background-color: white;
                        border: 1px solid #e0e0e0;
                        border-radius: 8px;
                        padding: 6px;
                    }
                """)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)  # 缩小一半：16->8, 12->6
        layout.setSpacing(8)  # 缩小一半：16->8

        # 排名（大号显示）
        rank_label = QLabel(str(rank))
        rank_font = QFont("Arial", 24, QFont.Bold) if is_top_3 else QFont("Arial", 18, QFont.Bold)
        rank_label.setFont(rank_font)
        rank_label.setFixedWidth(60)
        rank_label.setAlignment(Qt.AlignCenter)
        if is_top_3:
            if self._is_dark:
                rank_label.setStyleSheet("color: #f4d03f; background-color: transparent;")
            else:
                rank_label.setStyleSheet("color: #ff6b00; background-color: transparent;")
        elif self._is_dark:
            rank_label.setStyleSheet("color: #ffffff; background-color: transparent;")
        else:
            rank_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(rank_label)

        # 用户信息
        user_info_layout = QVBoxLayout()
        user_info_layout.setSpacing(4)

        # 姓名 + "You" 标识
        name_layout = QHBoxLayout()
        
        # 如果是月排名且是“领奖Top3”，添加奖牌图标
        if is_monthly and is_top_3 and show_medal:
            medal_emoji = ""
            medal_key = medal_rank if medal_rank in (1, 2, 3) else rank
            if medal_key == 1:
                medal_emoji = "🥇"
            elif medal_key == 2:
                medal_emoji = "🥈"
            elif medal_key == 3:
                medal_emoji = "🥉"
            if medal_emoji:
                medal_label = QLabel(medal_emoji)
                medal_label.setFont(QFont("Arial", 14))
                medal_label.setStyleSheet("background-color: transparent;")
                name_layout.addWidget(medal_label)
        
        name_label = QLabel(name)
        name_font = QFont("Arial", 12, QFont.Bold) if is_top_3 or is_me else QFont("Arial", 11)
        name_label.setFont(name_font)
        if self._is_dark:
            name_label.setStyleSheet("color: #ffffff; background-color: transparent;")
        else:
            name_label.setStyleSheet("background-color: transparent;")
        name_layout.addWidget(name_label)

        # 名字后面的提示（弱化样式，作为“提示”而非名字的一部分）
        if name_suffix:
            suffix_label = QLabel(name_suffix)
            suffix_label.setFont(QFont("Arial", 10))
            if self._is_dark:
                suffix_label.setStyleSheet("color: #aaaaaa; font-size:9px; background-color: transparent;")
            else:
                suffix_label.setStyleSheet("color: #666; font-size:10px; background-color: transparent;")
            suffix_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            name_layout.addWidget(suffix_label)

        if is_me:
            you_label = QLabel("👤 You")
            you_label.setStyleSheet("color: #4a90e2; font-weight: bold; background-color: transparent;")
            name_layout.addWidget(you_label)

        name_layout.addStretch()
        user_info_layout.addLayout(name_layout)

        # 邮箱
        if email:
            email_label = QLabel(email)
            if self._is_dark:
                email_label.setStyleSheet("color: #aaaaaa; font-size: 10pt; background-color: transparent;")
            else:
                email_label.setStyleSheet("color: #666; font-size: 10pt; background-color: transparent;")
            user_info_layout.addWidget(email_label)

        layout.addLayout(user_info_layout, 1)

        # 分数（日排名显示 total_ai，月排名显示 final_score）
        score_label = QLabel(score_text)
        score_font = QFont("Arial", 14, QFont.Bold) if is_top_3 else QFont("Arial", 12)
        score_label.setFont(score_font)
        score_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        score_label.setFixedWidth(80)
        if self._is_dark:
            score_label.setStyleSheet("color: #ffffff; background-color: transparent;")
        else:
            score_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(score_label)

        # 排名变化
        change_label = QLabel()
        change_label.setFixedWidth(60)
        change_label.setAlignment(Qt.AlignCenter)
        if isinstance(rank_change, int) and rank_change != 0:
            if rank_change > 0:
                change_label.setText(f"↑ {rank_change}")
                change_label.setStyleSheet("color: #28a745; font-weight: bold; font-size: 11pt; background-color: transparent;")
            else:
                change_label.setText(f"↓ {abs(rank_change)}")
                change_label.setStyleSheet("color: #dc3545; font-weight: bold; font-size: 11pt; background-color: transparent;")
        else:
            # 无变化/不可用：灰色“—”
            change_label.setText("—")
            if self._is_dark:
                change_label.setStyleSheet("color: #aaaaaa; font-weight: bold; font-size: 11pt; background-color: transparent;")
            else:
                change_label.setStyleSheet("color: #999; font-weight: bold; font-size: 11pt; background-color: transparent;")
        layout.addWidget(change_label)

        # 对比按钮（hover显示，当前用户不显示，月排名不显示；且只允许对比分高于自己的人）
        if not is_me and not is_monthly and allow_compare:
            compare_btn = QPushButton("对比")
            compare_btn.setFixedSize(60, 28)
            compare_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4a90e2;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 10pt;
                }
                QPushButton:hover {
                    background-color: #357abd;
                }
            """)
            compare_btn.setProperty("target_user_id", item.get("user_id"))
            compare_btn.setProperty("target_user_name", name)
            compare_btn.clicked.connect(self._on_compare_clicked)
            
            # 默认隐藏，hover时显示
            compare_btn.setVisible(False)
            frame.setProperty("compare_btn", compare_btn)  # 保存按钮引用
            
            # 使用事件过滤器实现hover显示/隐藏
            frame.installEventFilter(self)
            # 保存引用，避免被垃圾回收
            if not hasattr(self, '_event_filter_frames'):
                self._event_filter_frames = []
            self._event_filter_frames.append(frame)
            
            layout.addWidget(compare_btn)

        # 日排名：查看按钮（hover显示，位置/样式对齐“对比”按钮；仅对 Top10 开放）
        if not is_me and not is_monthly and allow_daily_view:
            view_btn = QPushButton("查看")
            view_btn.setFixedSize(60, 28)
            view_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4a90e2;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    font-size: 10pt;
                }
                QPushButton:hover {
                    background-color: #357abd;
                }
            """)
            view_btn.setProperty("target_user_id", item.get("user_id"))
            view_btn.setProperty("target_user_name", name)
            view_btn.clicked.connect(self._on_daily_view_clicked)
            view_btn.setVisible(False)
            frame.setProperty("view_btn", view_btn)
            frame.installEventFilter(self)
            if not hasattr(self, '_event_filter_frames'):
                self._event_filter_frames = []
            self._event_filter_frames.append(frame)
            layout.addWidget(view_btn)

        # 月排名（综合）：查看按钮（hover显示，查看Top10月评明细，用于学习）
        if is_monthly and allow_monthly_detail and (rank_type is None) and month_str_for_detail:
            uid = item.get("user_id")
            if uid:
                view_btn = QPushButton("查看")
                view_btn.setFixedSize(60, 28)
                view_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4a90e2;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        font-size: 10pt;
                    }
                    QPushButton:hover {
                        background-color: #357abd;
                    }
                """)
                view_btn.clicked.connect(
                    lambda checked=False, m=str(month_str_for_detail), u=str(uid), n=str(name): self._show_monthly_user_detail(m, u, n)
                )
                view_btn.setVisible(False)
                frame.setProperty("view_btn", view_btn)
                frame.installEventFilter(self)
                if not hasattr(self, '_event_filter_frames'):
                    self._event_filter_frames = []
                self._event_filter_frames.append(frame)
                layout.addWidget(view_btn)

        # 进步排名：查看按钮（hover 显示，位置/样式对齐“对比”按钮）
        if is_monthly and rank_type == "progress" and month_str_for_detail:
            uid = item.get("user_id")
            if uid:
                view_btn = QPushButton("查看")
                view_btn.setFixedSize(60, 28)
                # 复用“对比”按钮的样式以对齐
                view_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4a90e2;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        font-size: 10pt;
                    }
                    QPushButton:hover {
                        background-color: #357abd;
                    }
                """)
                view_btn.clicked.connect(
                    lambda checked=False, m=month_str_for_detail, u=str(uid), n=str(name): self._show_progress_detail(m, u, n)
                )
                # 默认隐藏，hover 时显示
                view_btn.setVisible(False)
                frame.setProperty("view_btn", view_btn)
                frame.installEventFilter(self)
                if not hasattr(self, '_event_filter_frames'):
                    self._event_filter_frames = []
                self._event_filter_frames.append(frame)
                layout.addWidget(view_btn)

        # 四维榜：查看按钮（hover 显示，展示该用户本月最高 3 条日评分 + 输入数据）
        if (
            is_monthly
            and rank_type in ("execution", "quality", "collaboration", "reflection")
            and month_str_for_detail
            and not is_me
        ):
            uid = item.get("user_id")
            if uid:
                view_btn = QPushButton("查看")
                view_btn.setFixedSize(60, 28)
                view_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4a90e2;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        font-size: 10pt;
                    }
                    QPushButton:hover {
                        background-color: #357abd;
                    }
                """)
                view_btn.clicked.connect(
                    lambda checked=False, m=month_str_for_detail, t=str(rank_type), u=str(uid), n=str(name): self._show_top_daily_dim(m, t, u, n)
                )
                view_btn.setVisible(False)
                frame.setProperty("view_btn", view_btn)
                frame.installEventFilter(self)
                if not hasattr(self, '_event_filter_frames'):
                    self._event_filter_frames = []
                self._event_filter_frames.append(frame)
                layout.addWidget(view_btn)

        return frame
    
    def eventFilter(self, obj, event):
        """事件过滤器：实现hover显示对比按钮"""
        from PySide6.QtCore import QEvent
        try:
            if hasattr(obj, "property"):
                compare_btn = obj.property("compare_btn")
                view_btn = obj.property("view_btn")
                history_view_btn = obj.property("history_view_btn")
                if event.type() == QEvent.Type.Enter:
                    if compare_btn:
                        compare_btn.setVisible(True)
                    if view_btn:
                        view_btn.setVisible(True)
                    if history_view_btn:
                        history_view_btn.setVisible(True)
                elif event.type() == QEvent.Type.Leave:
                    if compare_btn:
                        compare_btn.setVisible(False)
                    if view_btn:
                        view_btn.setVisible(False)
                    if history_view_btn:
                        history_view_btn.setVisible(False)
        except Exception:
            # 忽略事件过滤器中的异常，避免崩溃
            pass
        return super().eventFilter(obj, event)
    
    def _get_current_month_str(self) -> str:
        """获取当前选中的月份字符串（YYYY-MM-DD格式）"""
        current_index = self.month_combo.currentIndex()
        month_str_data = self.month_combo.itemData(current_index)
        if month_str_data:
            # 拼接 "-01" 转换为 YYYY-MM-DD 格式
            return f"{month_str_data}-01"
        else:
            # 如果没有 itemData，使用 itemText 解析
            month_text = self.month_combo.currentText()
            try:
                year_str, month_str_part = month_text.replace("年", "-").replace("月", "").split("-")
                return f"{year_str}-{month_str_part}-01"
            except Exception:
                # 如果解析失败，使用当前月份
                today = date.today()
                return f"{today.year}-{today.month:02d}-01"
    
    def _show_monthly_detail(self, month_str: str):
        """显示月排名明细对话框（先弹窗再请求接口）"""
        dialog = MonthlyDetailDialog(self, month_str)
        dialog.show()  # 先显示对话框
        # 使用 QTimer 延迟加载数据，确保对话框已显示
        QTimer.singleShot(100, dialog._load_data)

    def _show_progress_detail(self, month_str: str, target_user_id: str, target_user_name: str):
        """显示进步排名明细对话框（先弹窗再请求接口）"""
        dialog = ProgressDetailDialog(self, month_str, target_user_id, target_user_name)
        dialog.show()
        QTimer.singleShot(100, dialog.load_data)

    def _show_monthly_user_detail(self, month_str: str, target_user_id: str, target_user_name: str):
        """显示月排名Top10用户的月评明细（用于学习）"""
        dialog = MonthlyUserDetailDialog(self, month_str, target_user_id, target_user_name)
        dialog.show()
        QTimer.singleShot(100, dialog.load_data)

    def _show_daily_input(self, date_str: str, target_user_id: str, target_user_name: str):
        """展示某天某人的原始输入数据（input/daily 快照）"""
        dialog = DailyInputDialog(self, date_str, target_user_id, target_user_name)
        dialog.show()
        QTimer.singleShot(100, dialog.load_data)

    def _show_top_daily_dim(self, month_str: str, rank_type: str, target_user_id: str, target_user_name: str):
        """展示某用户当月最高的 3 条四维日分 + 原始输入数据（用于学习）"""
        dialog = TopDailyDimDialog(self, month_str, rank_type, target_user_id, target_user_name)
        dialog.show()
        QTimer.singleShot(100, dialog.load_data)
    
    def _on_compare_clicked(self):
        """点击对比按钮"""
        btn = self.sender()
        if not btn:
            return
        
        target_user_id = btn.property("target_user_id")
        target_user_name = btn.property("target_user_name") or target_user_id
        
        # 获取当前筛选的日期
        selected_date = self.date_edit.date().toPython()
        date_str = selected_date.isoformat()
        
        # 打开对比对话框
        dialog = ComparisonDialog(self, target_user_id, target_user_name, date_str)
        dialog.exec()

    def _on_daily_view_clicked(self):
        """点击查看按钮（日排名 Top10 学习用）"""
        btn = self.sender()
        if not btn:
            return
        target_user_id = btn.property("target_user_id")
        target_user_name = btn.property("target_user_name") or target_user_id

        # 当前筛选日期
        selected_date = self.date_edit.date().toPython()
        date_str = selected_date.isoformat()

        dialog = Top10DailyViewDialog(self, date_str, str(target_user_id), str(target_user_name))
        dialog.show()
        QTimer.singleShot(100, dialog.load_data)


class MonthlyDetailDialog(QDialog):
    """月排名明细对话框"""
    def __init__(self, parent, month_str: str):
        super().__init__(parent)
        self._month_str = month_str
        self.setWindowTitle(f"月排名明细 - {month_str}")
        self.resize(600, 400)
        
        # 检测当前主题
        self._is_dark = self._detect_theme()
        # 应用对话框背景色
        self._apply_dialog_theme()
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # 月份信息
        month_label = QLabel(f"月份：{month_str}")
        month_label.setFont(QFont("Arial", 14, QFont.Bold))
        self._apply_label_theme(month_label)
        layout.addWidget(month_label)
        
        # 信息显示区域
        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(12)
        
        # 创建标签显示各项数据
        self.ai_score_label = QLabel("AI综合评分：--")
        self.salary_ratio_label = QLabel("工资贡献率：--")
        self.growth_rate_label = QLabel("成长率：--")
        self.final_score_label = QLabel("最终综合分：--")
        self.working_days_label = QLabel("有效工作日：--")
        
        # 设置字体和主题
        for label in [self.ai_score_label, self.salary_ratio_label, self.growth_rate_label, 
                      self.final_score_label, self.working_days_label]:
            label.setFont(QFont("Arial", 12))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            self._apply_label_theme(label)
            info_layout.addWidget(label)
        
        layout.addWidget(info_frame)
        layout.addStretch()
        
        # 不立即加载数据，等待对话框显示后再加载（在 _show_monthly_detail 中调用）
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False
    
    def _apply_dialog_theme(self):
        """应用对话框背景色"""
        if self._is_dark:
            self.setStyleSheet("""
                QDialog {
                    background-color: #202124;
                }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog {
                    background-color: #F7F9FC;
                }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
    
    def _apply_label_theme(self, label: QLabel):
        """应用标签文字颜色"""
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")
    
    def _load_data(self):
        """加载月度汇总数据"""
        try:
            client = ApiClient.from_config()
            # 使用专门的月度明细接口，查询 ai_score_monthly 表
            data = client.get_monthly_detail(month_str=self._month_str)
            
            if data.get("status") != "success":
                error_msg = data.get("message") or "加载失败"
                self._show_error(f"加载失败：{error_msg}")
                return
            
            # 提取数据
            total_ai_month = data.get("total_ai_month", 0.0)
            salary_ratio = data.get("salary_ratio", 0.0)  # 数据库存的是小数
            growth_rate = data.get("growth_rate", 0.0)  # 数据库存的是小数
            final_score = data.get("final_score", 0.0)
            working_days = data.get("working_days", 0)  # 从接口直接获取有效工作日数
            
            # 格式化显示
            # 工资贡献率：乘以100，格式化为整数，加%
            # salary_ratio 范围是 0-2，如 1.0 表示 100%，0.83 表示 83%
            salary_ratio_percent = int(round(salary_ratio * 100))
            salary_ratio_display = f"{salary_ratio_percent}%"
            
            # 成长率：乘以100，格式化为整数，加%
            # growth_rate 是小数形式，如 0.10 表示 10%，-0.05 表示 -5%
            growth_rate_percent = int(round(growth_rate * 100))
            # 如果是正数，显示 + 号；如果是负数，已经有 - 号
            if growth_rate_percent > 0:
                growth_rate_display = f"+{growth_rate_percent}%"
            else:
                growth_rate_display = f"{growth_rate_percent}%"
            
            # 更新标签
            self.ai_score_label.setText(f"AI综合评分：{total_ai_month:.2f}")
            self.salary_ratio_label.setText(f"工资贡献率：{salary_ratio_display}")
            self.growth_rate_label.setText(f"成长率：{growth_rate_display}")
            self.final_score_label.setText(f"最终综合分：{final_score:.2f}")
            self.working_days_label.setText(f"有效工作日：{working_days} 天")
            
        except Exception as e:
            self._show_error(f"加载失败：{e}")
    
    def _show_error(self, message: str):
        """显示错误信息"""
        error_label = QLabel(message)
        # 根据主题设置错误信息颜色
        if self._is_dark:
            error_label.setStyleSheet("color: #ff6b6b; background-color: transparent;")
        else:
            error_label.setStyleSheet("color: red; background-color: transparent;")
        main_layout = self.layout()
        if main_layout:
            main_layout.addWidget(error_label)


class ProgressDetailDialog(QDialog):
    """进步排名明细对话框（趋势分组成指标）"""

    def __init__(self, parent, month_str: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._month_str = month_str
        self._user_id = user_id
        self._user_name = user_name
        self.setWindowTitle(f"进步排名明细 - {user_name}")
        self.resize(640, 420)

        # 检测主题
        self._is_dark = self._detect_theme()
        # tooltip 适配（全局）
        RankingView._apply_tooltip_theme(self._is_dark)
        self._apply_dialog_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"用户：{user_name}（{user_id}）\n月份：{month_str}")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self._apply_label_theme(title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)

        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(10)

        # 字段标签
        self.trend_score_label = QLabel("趋势分：--")
        self.slope_label = QLabel("斜率(分/日)：--")
        self.workday_label = QLabel("有效工作日：--")
        self.weight_label = QLabel("权重：--")
        self.head_avg_label = QLabel("月初均值：--")
        self.tail_avg_label = QLabel("月末均值：--")
        self.delta_label = QLabel("差值：--")
        self.r2_label = QLabel("R²：--")

        # tooltip 定义说明（鼠标移上去显示）
        self.trend_score_label.setToolTip(
            "趋势分定义：<br/>"
            "趋势分 = 斜率(分/日) × 有效工作日权重<br/>"
            "用于衡量“当月 total_ai 随时间的增长趋势”，并对有效工作日数不足进行折扣。"
        )
        self.slope_label.setToolTip(
            "斜率定义：<br/>"
            "对当月 eligible=1 的每日 total_ai 做一元线性回归：y = a + b·x<br/>"
            "其中 x=距月初的自然日数，b 即斜率（分/日）。"
        )
        self.workday_label.setToolTip(
            "有效工作日定义：<br/>"
            "当月 eligible=1 且有数据的天数（用于回归与均值计算）。"
        )
        self.weight_label.setToolTip(
            "权重定义：<br/>"
            "有效工作日权重 = 有效工作日数 / 当月总工作日数（范围 0~1）<br/>"
            "当月总工作日优先取 workday 表；若缺失则按周一~周五兜底。"
        )
        self.head_avg_label.setToolTip(
            "月初均值定义：<br/>"
            "取当月最早的 w 个有效工作日 total_ai 平均值，w=min(5, 有效工作日数)。"
        )
        self.tail_avg_label.setToolTip(
            "月末均值定义：<br/>"
            "取当月最晚的 w 个有效工作日 total_ai 平均值，w=min(5, 有效工作日数)。"
        )
        self.delta_label.setToolTip(
            "差值定义：<br/>"
            "差值 = 月末均值 - 月初均值（基于 w=min(5, 有效工作日数) 的窗口）。"
        )
        self.r2_label.setToolTip(
            "R² 定义：<br/>"
            "线性回归的拟合优度（0~1）。越接近 1 表示 total_ai 的变化越接近线性趋势。"
        )

        for label in [
            self.trend_score_label,
            self.slope_label,
            self.workday_label,
            self.weight_label,
            self.head_avg_label,
            self.tail_avg_label,
            self.delta_label,
            self.r2_label,
        ]:
            label.setFont(QFont("Arial", 11))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            self._apply_label_theme(label)
            info_layout.addWidget(label)

        layout.addWidget(info_frame)
        layout.addStretch()

    def _detect_theme(self) -> bool:
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _apply_dialog_theme(self):
        if self._is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #202124; }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F7F9FC; }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)

    def _apply_label_theme(self, label: QLabel):
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")

    def load_data(self):
        """加载进步排名明细数据（由外部在弹窗显示后调用）"""
        try:
            client = ApiClient.from_config()
            data = client.get_progress_rank_detail(month_str=self._month_str, user_id=self._user_id)
            if not isinstance(data, dict) or data.get("status") != "success":
                msg = (data.get("message") if isinstance(data, dict) else None) or "加载失败"
                self._show_error(f"加载失败：{msg}")
                return

            def _fmt_float(v: Any, ndigits: int = 4) -> str:
                try:
                    if v is None:
                        return "--"
                    return f"{float(v):.{ndigits}f}"
                except Exception:
                    return "--"

            def _fmt_int(v: Any) -> str:
                try:
                    if v is None:
                        return "--"
                    return str(int(v))
                except Exception:
                    return "--"

            trend_score = data.get("trend_score")
            slope = data.get("slope_per_day")
            workday_count = data.get("workday_count")
            month_total = data.get("month_total_workdays")
            weight = data.get("workday_weight")
            head_avg = data.get("head_avg")
            tail_avg = data.get("tail_avg")
            delta = data.get("delta_tail_head")
            r2 = data.get("r2")

            self.trend_score_label.setText(f"趋势分：{_fmt_float(trend_score, ndigits=4)}")
            self.slope_label.setText(f"斜率(分/日)：{_fmt_float(slope, ndigits=4)}")
            if month_total is not None:
                self.workday_label.setText(f"有效工作日：{_fmt_int(workday_count)}（当月总工作日：{_fmt_int(month_total)}）")
            else:
                self.workday_label.setText(f"有效工作日：{_fmt_int(workday_count)}")
            self.weight_label.setText(f"权重：{_fmt_float(weight, ndigits=4)}")
            self.head_avg_label.setText(f"月初均值：{_fmt_float(head_avg, ndigits=2)}")
            self.tail_avg_label.setText(f"月末均值：{_fmt_float(tail_avg, ndigits=2)}")
            self.delta_label.setText(f"差值：{_fmt_float(delta, ndigits=2)}")
            self.r2_label.setText(f"R²：{_fmt_float(r2, ndigits=4)}")
        except Exception as e:
            self._show_error(f"加载失败：{e}")

    def _show_error(self, message: str):
        error_label = QLabel(message)
        if self._is_dark:
            error_label.setStyleSheet("color: #ff6b6b; background-color: transparent;")
        else:
            error_label.setStyleSheet("color: red; background-color: transparent;")
        main_layout = self.layout()
        if main_layout:
            main_layout.addWidget(error_label)


class DailyInputDialog(QDialog):
    """查看某日某人的原始输入数据（input/daily snapshot）"""

    def __init__(self, parent, date_str: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._date_str = date_str
        self._user_id = user_id
        self._user_name = user_name
        self.setWindowTitle(f"原始输入数据 - {user_name}")
        self.resize(760, 520)

        self._is_dark = self._detect_theme()
        self._apply_dialog_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"用户：{user_name}（{user_id}）\n日期：{date_str}")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self._apply_label_theme(title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("加载中…")
        layout.addWidget(self.text, 1)

    def _detect_theme(self) -> bool:
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _apply_dialog_theme(self):
        if self._is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #202124; }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F7F9FC; }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)

    def _apply_label_theme(self, label: QLabel):
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")

    def load_data(self):
        try:
            import json

            client = ApiClient.from_config()
            snapshot = client.get_daily_snapshot(date_str=self._date_str, user_id=self._user_id)
            if not snapshot:
                self.text.setPlainText("暂无输入数据。")
                return
            self.text.setPlainText(json.dumps(snapshot, ensure_ascii=False, indent=2))
        except Exception as e:
            self.text.setPlainText(f"加载失败：{e}")


class TopDailyDimDialog(QDialog):
    """四维榜学习：该用户本月最高三条日评分 + 原始输入数据（tab）"""

    def __init__(self, parent, month_str: str, rank_type: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._month_str = month_str
        self._rank_type = (rank_type or "").strip()
        self._user_id = user_id
        self._user_name = user_name
        self.setWindowTitle(f"学习 - {user_name}")
        self.resize(820, 560)

        self._is_dark = self._detect_theme()
        self._apply_dialog_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"用户：{user_name}（{user_id}）\n月份：{month_str}")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self._apply_label_theme(title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._loading = QLabel("加载中…")
        self._loading.setAlignment(Qt.AlignCenter)
        self._apply_label_theme(self._loading)
        layout.addWidget(self._loading)

    def _detect_theme(self) -> bool:
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _apply_dialog_theme(self):
        if self._is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #202124; }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F7F9FC; }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)

    def _apply_label_theme(self, label: QLabel):
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")

    def load_data(self):
        try:
            import json

            client = ApiClient.from_config()
            resp = client.get_top_daily_dim_scores(
                rank_type=self._rank_type,
                month_str=self._month_str,
                user_id=self._user_id,
                limit=3,
            )
            if not isinstance(resp, dict) or resp.get("status") != "success":
                msg = (resp.get("message") if isinstance(resp, dict) else None) or "加载失败"
                self._loading.setText(f"加载失败：{msg}")
                return

            items = resp.get("items") or []
            self._loading.setVisible(False)
            self.tabs.clear()

            if not items:
                empty = QTextEdit()
                empty.setReadOnly(True)
                empty.setPlainText("本月暂无可用的日评分数据。")
                self.tabs.addTab(empty, "无数据")
                return

            for it in items:
                d = it.get("date")
                score_display = it.get("score_display") or "--"
                tab_title = f"{score_display}分 {d}"

                snapshot = it.get("snapshot") or {}
                editor = QTextEdit()
                editor.setReadOnly(True)
                editor.setPlainText(json.dumps(snapshot, ensure_ascii=False, indent=2))
                self.tabs.addTab(editor, tab_title)
        except Exception as e:
            self._loading.setText(f"加载失败：{e}")


class Top10DailyViewDialog(QDialog):
    """日排名 Top10 查看：原始输入 / AI返回 / 复评内容 / 复评结果（4-tab）"""

    def __init__(self, parent, date_str: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._date_str = date_str
        self._user_id = user_id
        self._user_name = user_name
        self.setWindowTitle(f"查看 - {user_name}")
        self.resize(920, 620)

        self._is_dark = self._detect_theme()
        self._apply_dialog_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"用户：{user_name}（{user_id}）\n日期：{date_str}")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self._apply_label_theme(title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._loading = QLabel("加载中…")
        self._loading.setAlignment(Qt.AlignCenter)
        self._apply_label_theme(self._loading)
        layout.addWidget(self._loading)

    def _detect_theme(self) -> bool:
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _apply_dialog_theme(self):
        if self._is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #202124; }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F7F9FC; }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)

    def _apply_label_theme(self, label: QLabel):
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")

    def load_data(self):
        try:
            import json

            client = ApiClient.from_config()
            resp = client.get_top10_user_daily_view(date_str=self._date_str, user_id=self._user_id)
            if not isinstance(resp, dict) or resp.get("status") != "success":
                msg = (resp.get("message") if isinstance(resp, dict) else None) or "加载失败"
                self._loading.setText(f"加载失败：{msg}")
                return

            self._loading.setVisible(False)
            self.tabs.clear()

            def _add_tab(title: str, obj: Any):
                editor = QTextEdit()
                editor.setReadOnly(True)
                editor.setPlainText(json.dumps(obj or {}, ensure_ascii=False, indent=2))
                self.tabs.addTab(editor, title)

            _add_tab("原始输入数据", resp.get("input_snapshot"))
            _add_tab("AI返回结果", resp.get("ai_output"))
            _add_tab("复评内容", resp.get("review_input"))
            _add_tab("复评结果", resp.get("review_result"))
        except Exception as e:
            self._loading.setText(f"加载失败：{e}")


class MonthlyUserDetailDialog(QDialog):
    """月排名Top10：月评明细（用于学习）"""

    def __init__(self, parent, month_str: str, user_id: str, user_name: str):
        super().__init__(parent)
        self._month_str = month_str
        self._user_id = user_id
        self._user_name = user_name
        self.setWindowTitle(f"月评明细 - {user_name}")
        self.resize(640, 420)

        self._is_dark = self._detect_theme()
        # tooltip 适配（全局）
        RankingView._apply_tooltip_theme(self._is_dark)
        self._apply_dialog_theme()

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = QLabel(f"用户：{user_name}（{user_id}）\n月份：{month_str}")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self._apply_label_theme(title)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)

        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(10)

        self.name_label = QLabel("姓名：--")
        self.team_label = QLabel("团队：--")
        self.final_score_label = QLabel("最终综合分：--")
        self.ai_score_label = QLabel("AI综合分：--")
        self.salary_ratio_label = QLabel("工资贡献率：--")
        self.growth_rate_label = QLabel("成长率：--")
        self.working_days_label = QLabel("有效工作日：--")

        # 参数说明 tooltip（对齐“进步之星”风格：看得懂、可学习）
        self.final_score_label.setToolTip(
            "最终综合分定义：<br/>"
            "最终综合分 = 0.7 × 当月AI均分 + 0.2 × 工资贡献率 + 0.1 × 成长率"
        )
        self.ai_score_label.setToolTip(
            "AI综合分定义：<br/>"
            "当月 eligible=1 工作日的 total_ai 平均值（来自 ai_score_monthly.total_ai_month）。"
        )
        self.salary_ratio_label.setToolTip(
            "工资贡献率定义：<br/>"
            "衡量相对工资水平的贡献比（数据库为小数形式，如 1.0 表示 100%）。"
        )
        self.growth_rate_label.setToolTip(
            "成长率定义：<br/>"
            "当月成长率（数据库为小数形式，如 0.10 表示 +10%，-0.05 表示 -5%）。"
        )
        self.working_days_label.setToolTip(
            "有效工作日定义：<br/>"
            "当月该用户 eligible=1 且有数据的天数（来自 ai_score_daily 统计）。"
        )

        for label in [
            self.name_label,
            self.team_label,
            self.final_score_label,
            self.ai_score_label,
            self.salary_ratio_label,
            self.growth_rate_label,
            self.working_days_label,
        ]:
            label.setFont(QFont("Arial", 11))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            self._apply_label_theme(label)
            info_layout.addWidget(label)

        layout.addWidget(info_frame)
        layout.addStretch()

        self._loading = QLabel("加载中…")
        self._loading.setAlignment(Qt.AlignCenter)
        self._apply_label_theme(self._loading)
        layout.addWidget(self._loading)

    def _detect_theme(self) -> bool:
        try:
            from utils.config_manager import ConfigManager
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference
            return theme == "dark"
        except Exception:
            return False

    def _apply_dialog_theme(self):
        if self._is_dark:
            self.setStyleSheet("""
                QDialog { background-color: #202124; }
                QToolTip {
                    background-color: #2a2a2a;
                    color: #E8EAED;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog { background-color: #F7F9FC; }
                QToolTip {
                    background-color: #ffffff;
                    color: #222222;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 6px 8px;
                    font-size: 10pt;
                }
            """)

    def _apply_label_theme(self, label: QLabel):
        if self._is_dark:
            label.setStyleSheet("color: #E8EAED; background-color: transparent;")
        else:
            label.setStyleSheet("color: #222; background-color: transparent;")

    def load_data(self):
        try:
            client = ApiClient.from_config()
            data = client.get_top10_user_monthly_detail(month_str=self._month_str, user_id=self._user_id)
            if not isinstance(data, dict) or data.get("status") != "success":
                msg = (data.get("message") if isinstance(data, dict) else None) or "加载失败"
                self._loading.setText(f"加载失败：{msg}")
                return

            self._loading.setVisible(False)

            name = data.get("name") or self._user_name
            team_name = data.get("team_name") or "--"
            final_score = float(data.get("final_score") or 0.0)
            ai_score = float(data.get("total_ai_month") or 0.0)
            salary_ratio = float(data.get("salary_ratio") or 0.0)
            growth_rate = float(data.get("growth_rate") or 0.0)
            working_days = int(data.get("working_days") or 0)

            salary_ratio_percent = int(round(salary_ratio * 100))
            salary_ratio_display = f"{salary_ratio_percent}%"

            growth_rate_percent = int(round(growth_rate * 100))
            if growth_rate_percent > 0:
                growth_rate_display = f"+{growth_rate_percent}%"
            else:
                growth_rate_display = f"{growth_rate_percent}%"

            self.name_label.setText(f"姓名：{name}")
            self.team_label.setText(f"团队：{team_name}")
            self.final_score_label.setText(f"最终综合分：{final_score:.2f}")
            self.ai_score_label.setText(f"AI综合分：{ai_score:.2f}")
            self.salary_ratio_label.setText(f"工资贡献率：{salary_ratio_display}")
            self.growth_rate_label.setText(f"成长率：{growth_rate_display}")
            self.working_days_label.setText(f"有效工作日：{working_days} 天")
        except Exception as e:
            self._loading.setText(f"加载失败：{e}")
