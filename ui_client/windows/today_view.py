#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
today_view.py

“今日概览”页面：

- 顶部显示日期（YYYY-MM-DD 星期X）；
- 总分 + 置信度 + 今日参考排名（暂时写死“统计中”占位）；
- 四个维度卡片：执行力 / 产出质量 / 协作 / 思考；
- 额外信息：
    - 缺失维度 / 缺失指标说明（来自 ai_score_daily.missing_dims）；
    - 模型给出的改进建议（ai_score_daily.recommendations）；
    - 是否参与评优/统计（ai_score_daily.eligible + reason）；
- 所有网络请求均在后台线程中执行，避免阻塞 UI。
"""

from datetime import date
from typing import Any, Dict, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGridLayout, QFrame, QProgressBar, QStyleOptionProgressBar, QScrollArea, QApplication, QMenu
)
from PySide6.QtGui import QFont, QPainter, QColor, QClipboard, QAction
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QRectF, QTimer, QEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QStyle, QStylePainter
import platform

from utils.api_client import ApiClient, ApiError, AuthError
from utils.theme_manager import ThemeManager
from widgets.toast import Toast


class _SvgIconLabel(QLabel):
    """自定义 QLabel 用于绘制 SVG 图标"""
    def __init__(self, svg_path: str, color: str, parent=None):
        super().__init__(parent)
        self.svg_path = svg_path
        self.color = color
        self.setStyleSheet("background-color: transparent;")
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 设置颜色
        color = QColor()
        if self.color.startswith("rgb("):
            # 解析 rgb(96, 165, 250) 格式
            rgb_str = self.color.replace("rgb(", "").replace(")", "")
            parts = [int(x.strip()) for x in rgb_str.split(",")]
            color.setRgb(parts[0], parts[1], parts[2])
        else:
            color.setNamedColor(self.color)
        
        # 创建 SVG 内容
        svg_content = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color.name()}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="{self.svg_path}"/>
        </svg>'''
        
        # 使用 QSvgRenderer 渲染
        renderer = QSvgRenderer(svg_content.encode('utf-8'))
        if renderer.isValid():
            rect = self.rect()
            renderer.render(painter, QRectF(rect))
        
        painter.end()


class _CustomProgressBar(QProgressBar):
    """自定义进度条，文本靠右跟随进度位置，只显示当前值"""
    def __init__(self, parent=None, is_dark: bool = False):
        super().__init__(parent)
        self.setTextVisible(False)  # 禁用默认文本显示
        self._is_dark = is_dark
    
    def set_dark_mode(self, is_dark: bool):
        """设置深色模式"""
        self._is_dark = is_dark
        self.update()
    
    def paintEvent(self, event):
        # 先绘制默认的进度条
        super().paintEvent(event)
        
        # 绘制自定义文本（只显示当前值，靠右跟随进度）
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 获取当前值
        value = self.value()
        max_value = self.maximum()
        
        if max_value > 0 and value > 0:
            # 计算进度百分比
            progress = value / max_value
            # 文本位置：进度条的右侧（跟随进度位置），稍微靠左一点避免超出
            text_x = int(self.width() * progress) - 25
            text_x = max(8, min(text_x, self.width() - 30))  # 确保在范围内，留出边距
        else:
            text_x = 8  # 无进度时靠左显示
        
        # 设置文本颜色和字体（根据深色模式调整）
        text_color = QColor("#E8EAED") if self._is_dark else QColor("#2c3e50")
        painter.setPen(text_color)
        font = QFont("Arial", 11, QFont.Bold)
        painter.setFont(font)
        
        # 绘制文本（只显示当前值）
        text = str(value) if value > 0 else "--"
        painter.drawText(text_x, 0, self.width() - text_x - 5, self.height(), 
                        Qt.AlignLeft | Qt.AlignVCenter, text)
        
        painter.end()


class _TodayWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class _TodayWorker(QRunnable):
    """
    后台线程里同步调用 /api/latest_score，获取最新的评分记录。
    """
    def __init__(self):
        super().__init__()
        self.signals = _TodayWorkerSignals()

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
            score = client.get_latest_score()
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"拉取最新评分失败：{e}")
            return

        if not isinstance(score, dict):
            self.signals.error.emit("暂无评分记录。")
            return

        self.signals.finished.emit(score)


class TodayView(QWidget):
    def __init__(self):
        super().__init__()

        # 检测当前主题
        self._is_dark = self._detect_theme()
        
        # 保存维度卡片引用，用于主题更新
        self._dim_cards = []

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建滚动区域
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # 根据平台设置滚动条策略：macOS 隐藏滚动条，其他平台显示
        system = platform.system()
        if system == "Darwin":
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # macOS 上通过样式表隐藏滚动条
            scroll_area.setStyleSheet("""
                QScrollArea {
                    border: none;
                }
                QScrollBar:vertical {
                    width: 0px;
                    background: transparent;
                }
                QScrollBar::handle:vertical {
                    width: 0px;
                }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical {
                    width: 0px;
                }
            """)
        else:
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建内容widget
        self._content_widget = QWidget()
        layout = QVBoxLayout(self._content_widget)
        layout.setContentsMargins(12, 12, 12, 12)  # 内边距减半：24 -> 12
        layout.setSpacing(8)  # 间距减半：16 -> 8
        
        # 设置滚动区域的内容widget
        scroll_area.setWidget(self._content_widget)
        
        # 设置最大高度为屏幕高度的100%，默认跟随内容自动高度
        screen = QApplication.primaryScreen()
        if screen:
            screen_height = screen.availableGeometry().height()
            max_height = int(screen_height * 1.0)  # 100%
            scroll_area.setMaximumHeight(max_height)
        
        # 将滚动区域添加到主布局
        main_layout.addWidget(scroll_area)

        title = QLabel("评分概览")
        title.setFont(QFont("Arial", 22, QFont.Bold))
        title_color = "#E8EAED" if self._is_dark else "#2c3e50"
        title.setStyleSheet(f"background-color: transparent; color: {title_color}; margin-bottom: 4px;")
        layout.addWidget(title)

        # 日期：你的上个工作日 xxxx-xx-xx 星期X 的数据：
        self.date_label = QLabel("你的上个工作日：加载中…")
        self.date_label.setFont(QFont("Arial", 12))
        date_color = "#9AA0A6" if self._is_dark else "#7f8c8d"
        self.date_label.setStyleSheet(f"background-color: transparent; color: {date_color};")
        layout.addWidget(self.date_label)

        # 顶部总分卡片
        score_card = QFrame()
        score_layout = QVBoxLayout(score_card)
        score_layout.setContentsMargins(10, 10, 10, 10)  # 内边距减半：20 -> 10
        score_layout.setSpacing(4)  # 间距减半：8 -> 4

        # 总分标签（整行显示 tooltip）
        self.score_label = QLabel("总分：--")
        self.score_label.setFont(QFont("Arial", 32, QFont.Bold))
        score_color = "#E8EAED" if self._is_dark else "#2c3e50"
        self.score_label.setStyleSheet(f"background-color: transparent; color: {score_color};")
        # 设置 tooltip，使用 HTML 格式，第二行不折行
        tooltip_text = (
            "总分计算公式：<br/>"
            "总分&nbsp;=&nbsp;(执行力&nbsp;+&nbsp;产出质量&nbsp;+&nbsp;协作&nbsp;+&nbsp;思考)&nbsp;×&nbsp;置信度"
        )
        self.score_label.setToolTip(tooltip_text)
        
        score_layout.addWidget(self.score_label)

        self.conf_label = QLabel("置信度：--")
        self.conf_label.setFont(QFont("Arial", 13))
        conf_color = "#9AA0A6" if self._is_dark else "#7f8c8d"
        self.conf_label.setStyleSheet(f"background-color: transparent; color: {conf_color};")
        # 设置置信度 tooltip
        conf_tooltip = (
            "置信度说明：<br/>"
            "反映AI对当日评分的可靠程度（0~1）<br/>"
            "依据数据完整性、文本质量、可解释性综合评估<br/>"
            "数据完整、文本清晰：0.9-1.0<br/>"
            "少量缺失：0.7-0.85<br/>"
            "关键数据缺失：0.5-0.7"
        )
        self.conf_label.setToolTip(conf_tooltip)

        # 排名和排名变化使用水平布局
        rank_container = QFrame()
        rank_container.setStyleSheet("background-color: transparent;")
        rank_layout = QHBoxLayout(rank_container)
        rank_layout.setContentsMargins(0, 0, 0, 0)
        rank_layout.setSpacing(8)

        self.rank_label = QLabel("排名：--")
        self.rank_label.setFont(QFont("Arial", 12))
        self.rank_label.setStyleSheet("background-color: transparent;")
        # 设置排名 tooltip
        rank_tooltip = (
            "排名规则：<br/>"
            "先按总分降序排列<br/>"
            "总分相同时，按排名变化降序排列（上升最快的在前）"
        )
        self.rank_label.setToolTip(rank_tooltip)

        # 排名变化标签（初始隐藏）
        self.rank_change_label = QLabel("")
        self.rank_change_label.setFont(QFont("Arial", 11, QFont.Bold))
        self.rank_change_label.setFixedWidth(60)
        self.rank_change_label.setAlignment(Qt.AlignCenter)
        self.rank_change_label.setStyleSheet("background-color: transparent;")
        self.rank_change_label.hide()

        rank_layout.addWidget(self.rank_label)
        rank_layout.addWidget(self.rank_change_label)
        rank_layout.addStretch()

        # 月度排名（在日排名下方）
        monthly_rank_container = QFrame()
        monthly_rank_container.setStyleSheet("background-color: transparent;")
        monthly_rank_layout = QHBoxLayout(monthly_rank_container)
        monthly_rank_layout.setContentsMargins(0, 0, 0, 0)
        monthly_rank_layout.setSpacing(8)

        self.monthly_rank_label = QLabel("本月排名（未锁定）：--")
        self.monthly_rank_label.setFont(QFont("Arial", 12))
        self.monthly_rank_label.setStyleSheet("background-color: transparent;")
        # 设置月度排名 tooltip
        monthly_rank_tooltip = (
            "月度排名规则：<br/>"
            "按最终综合分（final_score）降序排列<br/>"
            "最终综合分 = 0.7 × 当月AI均分 + 0.2 × 工资贡献率 + 0.1 × 成长率<br/><br/>"
            "排名锁定规则：<br/>"
            "当月最后一个工作日的排名锁定 = 月评分锁定<br/>"
            "否则，未锁定"
        )
        self.monthly_rank_label.setToolTip(monthly_rank_tooltip)

        # 月度排名变化标签（初始隐藏）
        self.monthly_rank_change_label = QLabel("")
        self.monthly_rank_change_label.setFont(QFont("Arial", 11, QFont.Bold))
        self.monthly_rank_change_label.setFixedWidth(60)
        self.monthly_rank_change_label.setAlignment(Qt.AlignCenter)
        self.monthly_rank_change_label.setStyleSheet("background-color: transparent;")
        self.monthly_rank_change_label.hide()

        monthly_rank_layout.addWidget(self.monthly_rank_label)
        monthly_rank_layout.addWidget(self.monthly_rank_change_label)
        monthly_rank_layout.addStretch()

        score_layout.addWidget(self.score_label)
        score_layout.addWidget(self.conf_label)
        score_layout.addWidget(rank_container)
        score_layout.addWidget(monthly_rank_container)
        score_layout.addStretch()

        score_card.setProperty("class", "card")
        layout.addWidget(score_card)

        # 四维度卡片区域（参考官网配色）
        grid = QGridLayout()
        grid.setSpacing(6)  # 间距减半：12 -> 6

        # 执行力：主题色 rgb(96, 165, 250)，图标背景 rgb(59, 130, 246 / 0.2)，图标颜色 rgb(96, 165, 250)
        self.dim_exec = self._create_dim_card(
            "执行力", "--", max_value=40, weight=40,
            theme_color="rgb(96, 165, 250)",
            icon_bg_color="rgba(59, 130, 246, 0.2)",
            icon_color="rgb(96, 165, 250)",
            svg_path="M13 10V3L4 14h7v7l9-11h-7z"
        )
        self._dim_cards.append(self.dim_exec)
        # 产出质量：主题色 rgb(168, 85, 247)，图标背景 rgb(147, 51, 234 / 0.2)，图标颜色 rgb(168, 85, 247)
        self.dim_quality = self._create_dim_card(
            "产出质量", "--", max_value=30, weight=30,
            theme_color="rgb(168, 85, 247)",
            icon_bg_color="rgba(147, 51, 234, 0.2)",
            icon_color="rgb(168, 85, 247)",
            svg_path="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
        )
        self._dim_cards.append(self.dim_quality)
        # 协作：主题色 rgb(34, 197, 94)，图标背景 rgb(22, 163, 74 / 0.2)，图标颜色 rgb(34, 197, 94)
        self.dim_collab = self._create_dim_card(
            "协作", "--", max_value=20, weight=20,
            theme_color="rgb(34, 197, 94)",
            icon_bg_color="rgba(22, 163, 74, 0.2)",
            icon_color="rgb(34, 197, 94)",
            svg_path="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"
        )
        self._dim_cards.append(self.dim_collab)
        # 思考：主题色 rgb(234, 179, 8)，图标背景 rgb(202, 138, 4 / 0.2)，图标颜色 rgb(234, 179, 8)
        self.dim_reflection = self._create_dim_card(
            "思考", "--", max_value=10, weight=10,
            theme_color="rgb(234, 179, 8)",
            icon_bg_color="rgba(202, 138, 4, 0.2)",
            icon_color="rgb(234, 179, 8)",
            svg_path="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
        )
        self._dim_cards.append(self.dim_reflection)

        grid.addWidget(self.dim_exec, 0, 0)
        grid.addWidget(self.dim_quality, 0, 1)
        grid.addWidget(self.dim_collab, 1, 0)
        grid.addWidget(self.dim_reflection, 1, 1)

        layout.addLayout(grid)

        # 额外信息卡片：重新梳理布局，让更清晰明了
        extra_card = QFrame()
        extra_layout = QVBoxLayout(extra_card)
        extra_layout.setContentsMargins(10, 10, 10, 10)  # 内边距减半：20 -> 10
        extra_layout.setSpacing(10)  # 增加间距，让各部分更清晰

        extra_text_color = "#E8EAED" if self._is_dark else "#34495e"
        
        # 1. 缺失维度（单独一个区块）
        self.missing_title = QLabel("📋 缺失维度 / 指标说明")
        self.missing_title.setFont(QFont("Arial", 12, QFont.Bold))
        self.missing_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-bottom: 4px;")
        self.missing_label = QLabel("--")
        self.missing_label.setFont(QFont("Arial", 11))
        self.missing_label.setWordWrap(True)
        self.missing_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        
        # 2. AI关键证据（单独一个区块）
        self.evidence_title = QLabel("🔍 AI关键证据")
        self.evidence_title.setFont(QFont("Arial", 12, QFont.Bold))
        self.evidence_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        self.evidence_label = QLabel("--")
        self.evidence_label.setFont(QFont("Arial", 11))
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        
        # 3. 模型给出的改进建议（单独一个区块）
        self.recommend_title = QLabel("💡 模型给出的改进建议")
        self.recommend_title.setFont(QFont("Arial", 12, QFont.Bold))
        self.recommend_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        self.recommend_label = QLabel("--")
        self.recommend_label.setFont(QFont("Arial", 11))
        self.recommend_label.setWordWrap(True)
        self.recommend_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        
        # 4. 参与评优/统计（单独一个区块）
        self.eligible_title = QLabel("✅ 参与评优/统计")
        self.eligible_title.setFont(QFont("Arial", 12, QFont.Bold))
        self.eligible_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        self.eligible_label = QLabel("--")
        self.eligible_label.setFont(QFont("Arial", 11))
        self.eligible_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; padding-left: 8px;")

        # 添加到布局
        extra_layout.addWidget(self.missing_title)
        extra_layout.addWidget(self.missing_label)
        extra_layout.addWidget(self.evidence_title)
        extra_layout.addWidget(self.evidence_label)
        extra_layout.addWidget(self.recommend_title)
        extra_layout.addWidget(self.recommend_label)
        extra_layout.addWidget(self.eligible_title)
        extra_layout.addWidget(self.eligible_label)

        extra_card.setProperty("class", "card")
        layout.addWidget(extra_card)

        # 刷新按钮
        refresh_btn = QPushButton("刷新评分")
        refresh_btn.clicked.connect(self.refresh_from_api)
        refresh_btn.setFixedWidth(160)
        layout.addWidget(refresh_btn)
        layout.addStretch()

        # 初始展示占位符，不在构造函数里直接打 API，
        # 首次自动刷新交由 MainWindow 控制
        self._set_placeholders()
        
        # 启用所有文本元素的文本选择和复制功能
        self._enable_text_selection()
        
        # 设置主题变化检测定时器
        self._theme_check_timer = QTimer(self)
        self._theme_check_timer.timeout.connect(self._check_theme_change)
        self._theme_check_timer.start(1000)  # 每秒检查一次
    
    def showEvent(self, event: QEvent):
        """页面显示时立即检查主题变化"""
        super().showEvent(event)
        self._check_theme_change()
    
    def contextMenuEvent(self, event):
        """右键菜单：支持复制选中的文本"""
        # 检查是否有选中的文本
        clipboard = QApplication.clipboard()
        selected_text = None
        
        # 尝试从当前焦点widget获取选中的文本
        focus_widget = QApplication.focusWidget()
        if focus_widget and hasattr(focus_widget, 'selectedText'):
            try:
                selected_text = focus_widget.selectedText()
            except:
                pass
        
        # 如果没有选中的文本，尝试从鼠标位置下的widget获取
        if not selected_text:
            widget = self.childAt(event.pos())
            if widget and isinstance(widget, QLabel):
                if widget.hasSelectedText():
                    selected_text = widget.selectedText()
        
        # 创建右键菜单
        menu = QMenu(self)
        
        # 设置菜单样式（根据当前主题）- 使用完整的样式字符串
        if self._is_dark:
            menu_style = """QMenu {
                background-color: #2b2b2b;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                color: #E8EAED;
                padding: 6px 20px 6px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #404040;
                color: #FFFFFF;
            }"""
        else:
            menu_style = """QMenu {
                background-color: #FFFFFF;
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item {
                background-color: transparent;
                color: #2c3e50;
                padding: 6px 20px 6px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #e8e8e8;
                color: #2c3e50;
            }"""
        
        # 设置样式
        menu.setStyleSheet(menu_style)
        # 确保菜单有背景
        menu.setAutoFillBackground(True)
        
        if selected_text:
            # 有选中文本，显示复制选项
            copy_action = QAction("复制", self)
            copy_action.triggered.connect(lambda: clipboard.setText(selected_text))
            menu.addAction(copy_action)
        else:
            # 没有选中文本，显示全选选项
            select_all_action = QAction("全选", self)
            select_all_action.triggered.connect(self._select_all_text)
            menu.addAction(select_all_action)
        
        menu.exec(event.globalPos())
    
    def _select_all_text(self):
        """全选当前焦点widget的文本"""
        focus_widget = QApplication.focusWidget()
        if focus_widget:
            if hasattr(focus_widget, 'selectAll'):
                focus_widget.selectAll()
            elif isinstance(focus_widget, QLabel):
                # 对于QLabel，需要确保文本交互标志已设置
                focus_widget.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
                if hasattr(focus_widget, 'selectAll'):
                    focus_widget.selectAll()
    
    def _enable_text_selection(self):
        """为所有文本元素启用文本选择功能"""
        # 设置文本交互标志：允许鼠标和键盘选择文本
        text_flags = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        
        # 为所有QLabel启用文本选择
        labels = [
            self.date_label,
            self.score_label,
            self.conf_label,
            self.rank_label,
            self.rank_change_label,
            self.missing_title,
            self.missing_label,
            self.evidence_title,
            self.evidence_label,
            self.recommend_title,
            self.recommend_label,
            self.eligible_title,
            self.eligible_label,
        ]
        
        for label in labels:
            if label:
                label.setTextInteractionFlags(text_flags)
        
        # 为维度卡片中的标签也启用文本选择
        for card in self._dim_cards:
            if hasattr(card, 'name_label'):
                card.name_label.setTextInteractionFlags(text_flags)
            if hasattr(card, 'weight_label'):
                card.weight_label.setTextInteractionFlags(text_flags)
    
    def _check_theme_change(self):
        """检查主题是否变化，如果变化则更新UI"""
        new_is_dark = self._detect_theme()
        if new_is_dark != self._is_dark:
            self._is_dark = new_is_dark
            self._update_theme_colors()
    
    def _update_theme_colors(self):
        """更新所有UI元素的颜色以适配当前主题"""
        # 更新标题
        title_color = "#E8EAED" if self._is_dark else "#2c3e50"
        # 找到标题label（在content_widget中查找）
        if hasattr(self, '_content_widget'):
            for i in range(self._content_widget.layout().count()):
                item = self._content_widget.layout().itemAt(i)
                if item and item.widget():
                    widget = item.widget()
                    if isinstance(widget, QLabel) and widget.text() == "评分概览":
                        widget.setStyleSheet(f"background-color: transparent; color: {title_color}; margin-bottom: 4px;")
                        break
        
        # 更新日期标签
        date_color = "#9AA0A6" if self._is_dark else "#7f8c8d"
        self.date_label.setStyleSheet(f"background-color: transparent; color: {date_color};")
        
        # 更新总分标签
        score_color = "#E8EAED" if self._is_dark else "#2c3e50"
        self.score_label.setStyleSheet(f"background-color: transparent; color: {score_color};")
        
        # 更新置信度标签
        conf_color = "#9AA0A6" if self._is_dark else "#7f8c8d"
        self.conf_label.setStyleSheet(f"background-color: transparent; color: {conf_color};")
        
        # 更新维度卡片
        name_color = "#FFFFFF" if self._is_dark else "#2c3e50"  # 深色模式下使用纯白色，更清晰
        weight_color = "#B0B3B8" if self._is_dark else "#95a5a6"  # 深色模式下稍微亮一点
        
        for card in self._dim_cards:
            if hasattr(card, 'name_label'):
                card.name_label.setStyleSheet(f"background-color: transparent; color: {name_color};")
            if hasattr(card, 'weight_label'):
                card.weight_label.setStyleSheet(f"background-color: transparent; color: {weight_color};")
            if hasattr(card, 'progress_bar'):
                # 更新进度条样式和深色模式状态
                theme_color = getattr(card, 'theme_color', 'rgb(59, 130, 246)')
                card.progress_bar.setStyleSheet(self._get_progress_bar_style(theme_color))
                if hasattr(card.progress_bar, 'set_dark_mode'):
                    card.progress_bar.set_dark_mode(self._is_dark)
                card.progress_bar.update()
        
        # 更新额外信息卡片
        extra_text_color = "#E8EAED" if self._is_dark else "#34495e"
        # 更新标题颜色
        if hasattr(self, 'missing_title'):
            self.missing_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-bottom: 4px;")
        if hasattr(self, 'evidence_title'):
            self.evidence_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        if hasattr(self, 'recommend_title'):
            self.recommend_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        if hasattr(self, 'eligible_title'):
            self.eligible_title.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; margin-top: 8px; margin-bottom: 4px;")
        # 更新内容颜色
        self.missing_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        if hasattr(self, 'evidence_label'):
            self.evidence_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        self.recommend_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; line-height: 1.6; padding-left: 8px;")
        self.eligible_label.setStyleSheet(f"background-color: transparent; color: {extra_text_color}; padding-left: 8px;")
    
    def _detect_theme(self) -> bool:
        """检测当前是否为深色模式"""
        try:
            from utils.config_manager import ConfigManager
            cfg = ConfigManager.load()
            preference = cfg.get("theme", "auto")
            
            if preference == "auto":
                theme = ThemeManager.detect_system_theme()
            else:
                theme = preference  # "light" or "dark"
            
            return theme == "dark"
        except:
            return False

    # -------- 布局子工具 --------
    def _create_dim_card(self, name: str, value: str, max_value: int = 100, weight: int = 0,
                         theme_color: str = "rgb(59, 130, 246)", icon_bg_color: str = "rgba(59, 130, 246, 0.2)",
                         icon_color: str = "rgb(59, 130, 246)", svg_path: str = "") -> QWidget:
        """
        创建维度卡片，使用进度条图表显示，参考官网设计
        
        Args:
            name: 维度名称
            value: 当前值（字符串，可能是数字或"--"）
            max_value: 该维度的满分
            weight: 权重百分比
            theme_color: 主题色（RGB格式）
            icon_bg_color: 图标背景色（RGBA格式）
            icon_color: 图标颜色（RGB格式）
            svg_path: SVG 路径字符串
        """
        card = QFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)  # 内边距减半：16 -> 8
        card_layout.setSpacing(6)  # 间距减半：12 -> 6

        # 图标和名称行
        header_container = QHBoxLayout()
        header_container.setContentsMargins(0, 0, 0, 0)
        header_container.setSpacing(10)
        
        # 图标容器（参考官网设计：48x48，圆角，半透明背景）
        icon_container = QFrame()
        icon_container.setFixedSize(40, 40)  # 稍微缩小：48 -> 40
        icon_container.setStyleSheet(f"""
            QFrame {{
                background-color: {icon_bg_color};
                border-radius: 8px;
            }}
        """)
        icon_layout = QVBoxLayout(icon_container)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setAlignment(Qt.AlignCenter)
        
        # 使用自定义 QLabel 绘制 SVG 图标
        icon_label = _SvgIconLabel(svg_path, icon_color)
        icon_label.setFixedSize(20, 20)  # 稍微缩小：24 -> 20
        icon_layout.addWidget(icon_label)
        header_container.addWidget(icon_container)
        
        # 名称和权重
        name_container = QVBoxLayout()
        name_container.setContentsMargins(0, 0, 0, 0)
        name_container.setSpacing(2)

        name_label = QLabel(name)
        name_label.setFont(QFont("Arial", 14, QFont.Bold))
        name_color = "#FFFFFF" if self._is_dark else "#2c3e50"  # 深色模式下使用纯白色，更清晰
        name_label.setStyleSheet(f"background-color: transparent; color: {name_color};")
        
        weight_label = QLabel(f"权重 {weight}%")
        weight_label.setFont(QFont("Arial", 10))
        weight_color = "#B0B3B8" if self._is_dark else "#95a5a6"  # 深色模式下稍微亮一点
        weight_label.setStyleSheet(f"background-color: transparent; color: {weight_color};")
        
        name_container.addWidget(name_label)
        name_container.addWidget(weight_label)
        name_container.addStretch()
        
        header_container.addLayout(name_container)
        header_container.addStretch()

        card_layout.addLayout(header_container)

        # 进度条（统一样式：背景色 rgba(255, 255, 255, 0.03)，边框 1px solid rgba(255, 255, 255, 0.05)，进度条颜色跟随主题色）
        # 使用自定义进度条，文本靠右跟随进度位置，只显示当前值
        progress_bar = _CustomProgressBar(is_dark=self._is_dark)
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(max_value)
        progress_bar.setValue(0)
        progress_bar.setFixedHeight(36)
        # 立即应用样式，确保边框和背景色正确显示
        style = self._get_progress_bar_style(theme_color)
        progress_bar.setStyleSheet(style)
        progress_bar.update()  # 强制更新
        
        card_layout.addWidget(progress_bar)
        card_layout.addStretch()
        
        # 保存主题色，用于后续更新进度条颜色
        card.theme_color = theme_color  # type: ignore[attr-defined]

        # 保存引用以便后续更新
        card.progress_bar = progress_bar  # type: ignore[attr-defined]
        card.max_value = max_value  # type: ignore[attr-defined]
        card.name_label = name_label  # type: ignore[attr-defined]
        card.weight_label = weight_label  # type: ignore[attr-defined]
        card.setProperty("class", "card")
        return card

    def _format_date_label(self, score_date: date) -> str:
        """格式化日期标签：你的上个工作日 xxxx-xx-xx 星期X 的数据："""
        weekday_map = "一二三四五六日"
        wd = weekday_map[score_date.weekday()] if score_date.weekday() < 7 else "?"
        return f"你的上个工作日 {score_date.strftime('%Y-%m-%d')} 星期{wd} 的数据："
    
    def _get_progress_bar_style(self, theme_color: str) -> str:
        """
        获取统一的进度条样式
        
        Args:
            theme_color: 主题色（RGB格式，如 "rgb(96, 165, 250)"）
        
        Returns:
            进度条样式字符串
        """
        # 根据主题模式设置边框和背景色
        if self._is_dark:
            # 暗色模式：保持不变
            border_color = "rgba(255, 255, 255, 0.1)"  # 深色模式下稍微亮一点
            bg_color = "rgba(255, 255, 255, 0.05)"  # 深色模式下稍微亮一点
        else:
            # 亮色模式：边框 #eee，背景色 #f0f0f0
            border_color = "#eee"
            bg_color = "#f0f0f0"
        
        return (
            "QProgressBar {"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            f"background-color: {bg_color};"
            "text-align: center;"
            "font-size: 11pt;"
            "font-weight: bold;"
            "color: transparent;"  # 隐藏默认文本，使用自定义绘制
            "}"
            "QProgressBar::chunk {"
            f"background-color: {theme_color};"
            "border-radius: 7px;"
            "}"
        )
    
    def _update_dim_card(self, card: QWidget, value: Any) -> None:
        """
        更新维度卡片的进度条
        
        Args:
            card: 维度卡片widget
            value: 分数值（可能是int、None或"--"）
        """
        if value is None or value == "--":
            card.progress_bar.setValue(0)
            # 使用维度主题色
            theme_color = getattr(card, 'theme_color', 'rgb(59, 130, 246)')
            card.progress_bar.setStyleSheet(self._get_progress_bar_style(theme_color))
            # 更新深色模式状态
            if hasattr(card.progress_bar, 'set_dark_mode'):
                card.progress_bar.set_dark_mode(self._is_dark)
            card.progress_bar.update()  # 强制更新
        else:
            try:
                int_value = int(value)
                max_val = card.max_value
                # 确保值在有效范围内
                int_value = max(0, min(int_value, max_val))
                card.progress_bar.setValue(int_value)
                
                # 使用维度主题色
                theme_color = getattr(card, 'theme_color', 'rgb(59, 130, 246)')
                card.progress_bar.setStyleSheet(self._get_progress_bar_style(theme_color))
                card.progress_bar.update()  # 强制更新
            except (ValueError, TypeError):
                card.progress_bar.setValue(0)
                # 使用维度主题色
                theme_color = getattr(card, 'theme_color', 'rgb(59, 130, 246)')
                card.progress_bar.setStyleSheet(self._get_progress_bar_style(theme_color))
                card.progress_bar.update()  # 强制更新

    # -------- 对外：刷新入口（UI 线程） --------
    def refresh_from_api(self, silent: bool = False) -> None:
        """
        入口只负责：
        - 检查登录状态（未登录时不发起请求）；
        - 显示全局 Loading；
        - 准备并启动后台线程；
        - 其余工作交给 _on_load_finished / _on_load_error。
        """
        # 检查登录状态，未登录时不发起请求
        if not ApiClient.is_logged_in():
            if not silent:
                from widgets.toast import Toast
                Toast.show_message(self, "请先登录")
            return
        
        win = self.window()
        show_loading = getattr(win, "show_loading", None)

        if callable(show_loading):
            show_loading("加载最新评分中…")

        worker = _TodayWorker()
        # 保存 worker 引用，避免被垃圾回收导致信号对象被删除
        if not hasattr(self, '_active_workers'):
            self._active_workers = []
        self._active_workers.append(worker)
        
        # 使用 partial 避免 lambda 闭包问题
        from functools import partial
        worker.signals.finished.connect(
            partial(self._on_load_finished, silent=silent)
        )
        worker.signals.error.connect(
            partial(self._on_load_error, silent=silent)
        )
        
        # 连接一个清理函数，在完成后移除引用
        def cleanup():
            if hasattr(self, '_active_workers') and worker in self._active_workers:
                self._active_workers.remove(worker)
        
        worker.signals.finished.connect(cleanup)
        worker.signals.error.connect(cleanup)
        
        QThreadPool.globalInstance().start(worker)

    # -------- 后台线程回调（仍在主线程执行） --------
    def _on_load_finished(self, score: Dict[str, Any], silent: bool = False) -> None:
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        if not score:
            self._set_placeholders(text="暂无评分")
            self.date_label.setText("你的上个工作日：暂无数据")
            if not silent:
                Toast.show_message(self, "暂无评分记录。")
            return

        # 更新日期标签
        score_date_str = score.get("date")
        if score_date_str:
            try:
                if isinstance(score_date_str, str):
                    score_date = date.fromisoformat(score_date_str)
                else:
                    score_date = score_date_str
                self.date_label.setText(self._format_date_label(score_date))
            except Exception:
                self.date_label.setText("你的上个工作日：日期解析失败")
        else:
            self.date_label.setText("你的上个工作日：日期未知")

        total = score.get("total_ai")
        conf = score.get("confidence")

        self.score_label.setText(f"总分：{total if total is not None else '--'}")
        if isinstance(conf, (int, float)):
            self.conf_label.setText(f"置信度：{conf:.2f}")
        else:
            self.conf_label.setText("置信度：--")

        # 维度分（更新进度条和数值）
        self._update_dim_card(self.dim_exec, score.get("execution"))
        self._update_dim_card(self.dim_quality, score.get("quality"))
        self._update_dim_card(self.dim_collab, score.get("collaboration"))
        self._update_dim_card(self.dim_reflection, score.get("reflection"))

        # 参考排名：显示实际排名和排名变化
        rank = score.get("rank")
        rank_change = score.get("rank_change")
        
        if rank is not None:
            self.rank_label.setText(f"排名：第 {rank} 名")
        else:
            # 如果排名未锁定，尝试计算实时排名
            # 这里暂时显示"未锁定"，实际可以通过API获取实时排名
            self.rank_label.setText("排名：未锁定（实时排名）")
        
        # 显示排名变化（样式对齐排行榜）
        # - 有变化：↑/↓
        # - 无变化/不可用：显示灰色 “—”（避免看起来像没渲染）
        self.rank_change_label.show()
        if isinstance(rank_change, int) and rank_change != 0:
            if rank_change > 0:
                # 上升：绿色
                self.rank_change_label.setText(f"↑ {rank_change}")
                self.rank_change_label.setStyleSheet(
                    "color: #28a745; font-weight: bold; font-size: 11pt; background-color: transparent;"
                )
            else:
                # 下降：红色
                self.rank_change_label.setText(f"↓ {abs(rank_change)}")
                self.rank_change_label.setStyleSheet(
                    "color: #dc3545; font-weight: bold; font-size: 11pt; background-color: transparent;"
                )
        else:
            self.rank_change_label.setText("—")
            self.rank_change_label.setStyleSheet(
                "color: #999; font-weight: bold; font-size: 11pt; background-color: transparent;"
            )

        # 月度排名
        monthly_rank = score.get("monthly_rank")
        monthly_rank_change = score.get("monthly_rank_change")
        monthly_rank_locked = score.get("monthly_rank_locked", False)
        
        if monthly_rank is not None:
            locked_text = "" if monthly_rank_locked else "（未锁定）"
            self.monthly_rank_label.setText(f"本月排名{locked_text}：第 {monthly_rank} 名")
        else:
            self.monthly_rank_label.setText("本月排名（未锁定）：--")
        
        # 显示月度排名变化（第一个月没有变化）
        if monthly_rank_change is not None and monthly_rank_change != 0:
            self.monthly_rank_change_label.show()
            if monthly_rank_change > 0:
                # 上升：绿色
                self.monthly_rank_change_label.setText(f"↑ {monthly_rank_change}")
                self.monthly_rank_change_label.setStyleSheet(
                    "color: #28a745; font-weight: bold; font-size: 11pt; background-color: transparent;"
                )
            else:
                # 下降：红色
                self.monthly_rank_change_label.setText(f"↓ {abs(monthly_rank_change)}")
                self.monthly_rank_change_label.setStyleSheet(
                    "color: #dc3545; font-weight: bold; font-size: 11pt; background-color: transparent;"
                )
        else:
            # 无变化或数据不可用（第一个月），隐藏标签
            self.monthly_rank_change_label.hide()

        # 缺失维度 / 指标说明
        missing = score.get("missing_dims")
        self.missing_label.setText(self._format_missing_dims(missing))

        # AI关键证据
        evidence = score.get("evidence", [])
        self.evidence_label.setText(self._format_evidence(evidence))

        # 模型给出的改进建议
        recs = score.get("recommendations")
        self.recommend_label.setText(self._format_recommendations(recs))

        # 是否参与评优/统计
        eligible = score.get("eligible", 1)
        reason = score.get("reason")
        if int(eligible or 0) == 1:
            self.eligible_label.setText("是")
        else:
            reason_text = reason or "系统标记为不计入考核。"
            self.eligible_label.setText(f"否（理由：{reason_text}）")

    def _on_load_error(self, message: str, silent: bool = False) -> None:
        """处理加载错误"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        self._set_placeholders()

        # 显式操作时（silent=False），若是登录相关错误，则引导登录并在成功后自动刷新
        if not silent and message:
            text = str(message)
            if any(key in text for key in ("需要先登录", "会话已过期", "无效会话令牌")):
                show_login = getattr(win, "show_login_required_dialog", None)
                if callable(show_login):
                    # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                    if not getattr(win, "_login_dialog_shown", False):
                        if show_login():
                            # 登录成功后，静默刷新一次
                            self.refresh_from_api(silent=True)
                            return
                    return  # 如果已经有登录弹窗，直接返回，不显示 Toast
            Toast.show_message(self, text)
        
        # 错误时也更新日期标签
        self.date_label.setText("你的上个工作日：加载失败")

    # -------- 文本格式化 & 占位 --------
    def _format_missing_dims(self, missing: Any) -> str:
        if not missing:
            return "无明显缺失或数据不足。"
        try:
            # 兼容列表 / 字典 / 字符串等多种形式
            if isinstance(missing, list):
                return "；".join(str(x) for x in missing)
            if isinstance(missing, dict):
                return "；".join(f"{k}: {v}" for k, v in missing.items())
            return str(missing)
        except Exception:
            return "（解析失败）"
    
    def _format_evidence(self, evidence: Any) -> str:
        """格式化AI关键证据列表"""
        if not evidence:
            return "暂无关键证据。"
        try:
            if isinstance(evidence, list):
                lines: List[str] = []
                for i, item in enumerate(evidence, start=1):
                    lines.append(f"{i}. {item}")
                return "\n".join(lines)
            return str(evidence)
        except Exception:
            return "（解析失败）"

    def _format_recommendations(self, recs: Any) -> str:
        if not recs:
            return "暂无，后续可根据更多历史数据给出更具体的建议。"
        try:
            if isinstance(recs, list):
                # 用换行 + 项目符号展示
                lines: List[str] = []
                for i, item in enumerate(recs, start=1):
                    lines.append(f"{i}. {item}")
                return "\n".join(lines)
            return str(recs)
        except Exception:
            return "（解析失败）"

    def _set_placeholders(self, text: str = "--") -> None:
        self.score_label.setText(f"总分：{text}")
        self.conf_label.setText("置信度：--")
        self.rank_label.setText("排名：--")
        self.monthly_rank_label.setText("本月排名（未锁定）：--")
        self.monthly_rank_change_label.hide()
        self.rank_change_label.hide()
        self._update_dim_card(self.dim_exec, None)
        self._update_dim_card(self.dim_quality, None)
        self._update_dim_card(self.dim_collab, None)
        self._update_dim_card(self.dim_reflection, None)
        self.missing_label.setText("--")
        self.evidence_label.setText("--")
        self.recommend_label.setText("--")
        self.eligible_label.setText("--")
