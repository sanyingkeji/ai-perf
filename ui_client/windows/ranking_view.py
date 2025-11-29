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
    QTextEdit, QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView
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

        # TAB切换（日排名/月排名）
        self.tab_widget = QTabWidget()
        self.daily_tab = QWidget()
        self.monthly_tab = QWidget()
        self.tab_widget.addTab(self.daily_tab, "日排名")
        self.tab_widget.addTab(self.monthly_tab, "月排名")
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
        # 生成从 2025-11 到 2027-11 的所有月份选项
        months = []
        for year in range(2025, 2028):  # 2025, 2026, 2027
            start_month = 11 if year == 2025 else 1
            end_month = 11 if year == 2027 else 12
            for month in range(start_month, end_month + 1):
                months.append(f"{year}-{month:02d}")
        self.month_combo.addItems(months)
        # 设置当前月份为默认选中
        from datetime import date as date_class
        today = date_class.today()
        current_month_str = f"{today.year}-{today.month:02d}"
        if current_month_str in months:
            index = months.index(current_month_str)
            self.month_combo.setCurrentIndex(index)
        else:
            # 如果当前月份不在范围内，选择最后一个
            self.month_combo.setCurrentIndex(len(months) - 1)
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
        
        # 当前TAB索引（0=日排名，1=月排名）
        self._current_tab_index = 0

        # 首次加载时，直接请求日排行榜（不传日期，后端会返回上一个工作日的数据）
        # 延迟到下一事件循环，确保UI完全初始化后再加载
        from PySide6.QtCore import QTimer as SingleShotTimer
        SingleShotTimer.singleShot(100, lambda: self._load_ranking(date_str=None))

    def refresh_from_api(self, silent: bool = False):
        """从API刷新数据（供外部调用，如登录成功后）"""
        # 根据当前TAB加载对应的数据
        if self._current_tab_index == 0:
            # 日排名
            self._clear_content()
            loading_label = QLabel("加载中…")
            loading_label.setAlignment(Qt.AlignCenter)
            self.content_layout.addWidget(loading_label)
            self._is_initializing = True
            self._load_ranking(date_str=None)
        else:
            # 月排名
            self._clear_content()
            loading_label = QLabel("加载中…")
            loading_label.setAlignment(Qt.AlignCenter)
            self.content_layout.addWidget(loading_label)
            self._is_initializing = True
            self._load_monthly_ranking(month_str=None)

    def _on_tab_changed(self, index: int):
        """TAB切换事件"""
        self._current_tab_index = index
        if index == 0:
            # 切换到日排名
            self.content_widget = self.daily_content_widget
            self.content_layout = self.daily_content_layout
            # 加载日排名数据
            self._load_ranking(date_str=None)
        else:
            # 切换到月排名
            self.content_widget = self.monthly_content_widget
            self.content_layout = self.monthly_content_layout
            # 加载月排名数据
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

    def _on_month_changed(self, index: int):
        """月份下拉框改变时重新加载月排名（和历史评分页面一样的实现方式）"""
        # 如果正在初始化，不触发加载
        if self._is_initializing:
            return
        # 用户手动改变月份时，使用下拉框的值
        month_text = self.month_combo.itemText(index)
        month_str = f"{month_text}-01"
        self._load_monthly_ranking(month_str=month_str)

    def _on_monthly_refresh_clicked(self):
        """刷新按钮点击事件（月排名）"""
        # 刷新时，使用当前选择的月份
        month_text = self.month_combo.currentText()
        month_str = f"{month_text}-01"
        self._load_monthly_ranking(month_str=month_str)

    def _load_ranking(self, date_str: Optional[str] = None):
        """加载日排行榜数据"""
        # 如果 date_str 是 None，表示不传日期参数，后端会返回上一个工作日的数据
        # 如果 date_str 不是 None，使用指定的日期
        
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
        # 如果 month_str 是 None，表示不传月份参数，后端会返回当前月份的数据
        # 如果 month_str 不是 None，使用指定的月份
        
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
    
    def _render_ranking_data(self, data: Dict[str, Any], is_monthly: bool = False):
        """渲染排行榜数据"""
        self._clear_content()

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
            if month_str:
                try:
                    d = date.fromisoformat(month_str)
                    month_text = f"{d.year}-{d.month:02d}"
                    # 使用 blockSignals 临时阻止信号，避免触发加载
                    self.month_combo.blockSignals(True)
                    index = self.month_combo.findText(month_text)
                    if index >= 0:
                        self.month_combo.setCurrentIndex(index)
                    self.month_combo.blockSignals(False)
                except Exception:
                    pass

            # 初始化完成
            self._is_initializing = False

            # 显示月份信息
            total_participants = data.get("total_participants", 0)
            locked_text = "（已锁定）" if locked else "（未锁定）"
            month_info = QLabel(f"月份：{month_str} | 参与排名：{total_participants} 人 {locked_text}")
            month_info.setFont(QFont("Arial", 10))
            if self._is_dark:
                month_info.setStyleSheet("color: #aaaaaa; background-color: transparent;")
            else:
                month_info.setStyleSheet("background-color: transparent;")
            # 设置tooltip（未锁定时显示详细规则）
            if not locked:
                month_info.setToolTip(
                    "月度排名规则：<br/>"
                    "按最终综合分（final_score）降序排列<br/>"
                    "最终综合分 = 0.7 × 当月AI均分 + 0.2 × 工资贡献率 + 0.1 × 成长率<br/><br/>"
                    "排名锁定规则：<br/>"
                    "当月最后一个工作日的排名锁定 = 月评分锁定<br/>"
                    "否则，未锁定"
                )
            else:
                month_info.setToolTip(
                    "月度排名规则：<br/>"
                    "按最终综合分（final_score）降序排列<br/>"
                    "最终综合分 = 0.7 × 当月AI均分 + 0.2 × 工资贡献率 + 0.1 × 成长率<br/><br/>"
                    "排名已锁定（当月最后一个工作日的排名已锁定）"
                )
            self.content_layout.addWidget(month_info)
        else:
            # 日排行榜
            # 从返回数据中获取日期，更新日期选择器
            date_str = data.get("date", "")
            if date_str:
                try:
                    d = date.fromisoformat(date_str)
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
            top_10_label = QLabel("🏆 前十名")
            top_10_label.setFont(QFont("Arial", 14, QFont.Bold))
            if self._is_dark:
                top_10_label.setStyleSheet("color: #ffffff; background-color: transparent;")
            else:
                top_10_label.setStyleSheet("background-color: transparent;")
            self.content_layout.addWidget(top_10_label)

            for item in top_10:
                rank_item_widget = self._create_rank_item(item, is_top_3=(item["rank"] <= 3), is_monthly=is_monthly)
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

            current_user_widget = self._create_rank_item(current_user_rank, is_current_user=True, is_monthly=is_monthly)
            self.content_layout.addWidget(current_user_widget)

        self.content_layout.addStretch()

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
                        # 登录成功，重新加载（不传日期，获取上一个工作日的数据）
                        self._load_ranking(date_str=None)
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
            # 重新应用按钮主题
            self._apply_button_theme(self.refresh_btn)
            self._apply_button_theme(self.monthly_refresh_btn)
            # 如果有数据，重新渲染排名项
            if self._current_data:
                # 根据当前TAB判断是日排名还是月排名
                is_monthly = self._current_tab_index == 1
                self._render_ranking_data(self._current_data, is_monthly=is_monthly)
    
    def showEvent(self, event: QEvent):
        """页面显示时，立即检测并更新主题"""
        super().showEvent(event)
        # 立即检测一次主题变化
        self._check_theme_change()

    def _create_rank_item(self, item: Dict[str, Any], is_top_3: bool = False, is_current_user: bool = False, is_monthly: bool = False) -> QFrame:
        """创建单个排名项"""
        rank = item.get("rank", 0)
        name = item.get("name") or item.get("user_id", "未知")
        email = item.get("email") or ""
        # 日排名使用 total_ai，月排名使用 final_score
        if is_monthly:
            score_value = item.get("final_score", 0.0)
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
        
        # 如果是月排名且是前三名，添加奖牌图标
        if is_monthly and is_top_3:
            medal_emoji = ""
            if rank == 1:
                medal_emoji = "🥇"
            elif rank == 2:
                medal_emoji = "🥈"
            elif rank == 3:
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

        if is_me:
            you_label = QLabel("👤 You")
            you_label.setStyleSheet("color: #4a90e2; font-weight: bold; background-color: transparent;")
            name_layout.addWidget(you_label)
            
            # 月排名时，添加查看明细按钮
            if is_monthly:
                view_btn = QPushButton("查看")
                view_btn.setFixedSize(60, 28)
                # 获取当前月份字符串
                month_str = self._get_current_month_str()
                view_btn.clicked.connect(lambda checked, m=month_str: self._show_monthly_detail(m))
                self._apply_button_theme(view_btn)
                name_layout.addWidget(view_btn)

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
        if rank_change is not None and rank_change != 0:
            change_label = QLabel()
            if rank_change > 0:
                change_label.setText(f"↑ {rank_change}")
                change_label.setStyleSheet("color: #28a745; font-weight: bold; font-size: 11pt; background-color: transparent;")
            else:
                change_label.setText(f"↓ {abs(rank_change)}")
                change_label.setStyleSheet("color: #dc3545; font-weight: bold; font-size: 11pt; background-color: transparent;")
            change_label.setFixedWidth(60)
            change_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(change_label)
        else:
            # 占位，保持对齐
            spacer = QLabel("")
            spacer.setFixedWidth(60)
            spacer.setStyleSheet("background-color: transparent;")
            layout.addWidget(spacer)

        # 对比按钮（hover显示，当前用户不显示，月排名不显示）
        if not is_me and not is_monthly:
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

        return frame
    
    def eventFilter(self, obj, event):
        """事件过滤器：实现hover显示对比按钮"""
        from PySide6.QtCore import QEvent
        try:
            if hasattr(obj, "property") and obj.property("compare_btn"):
                compare_btn = obj.property("compare_btn")
                if compare_btn and event.type() == QEvent.Type.Enter:
                    compare_btn.setVisible(True)
                elif compare_btn and event.type() == QEvent.Type.Leave:
                    compare_btn.setVisible(False)
        except Exception:
            # 忽略事件过滤器中的异常，避免崩溃
            pass
        return super().eventFilter(obj, event)
    
    def _get_current_month_str(self) -> str:
        """获取当前选中的月份字符串（YYYY-MM-DD格式）"""
        month_text = self.month_combo.currentText()
        return f"{month_text}-01"
    
    def _show_monthly_detail(self, month_str: str):
        """显示月排名明细对话框（先弹窗再请求接口）"""
        dialog = MonthlyDetailDialog(self, month_str)
        dialog.show()  # 先显示对话框
        # 使用 QTimer 延迟加载数据，确保对话框已显示
        QTimer.singleShot(100, dialog._load_data)
    
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
            """)
        else:
            self.setStyleSheet("""
                QDialog {
                    background-color: #F7F9FC;
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

