import json
from datetime import date
from typing import List, Optional, Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTextEdit, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QHBoxLayout, QFrame, QStackedWidget,
    QAbstractItemView, QMenu, QInputDialog, QMessageBox
)
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot

from utils.api_client import ApiClient, ApiError, AuthError
from widgets.toast import Toast


class _ReviewSubmitWorkerSignals(QObject):
    finished = Signal(dict)  # ReviewResponse
    error = Signal(str)


class _ReviewSubmitWorker(QRunnable):
    """在后台线程中提交复评请求"""
    def __init__(self, payload: Dict[str, Any]):
        super().__init__()
        self._payload = payload
        self.signals = _ReviewSubmitWorkerSignals()

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
            resp = client.submit_review(self._payload)
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"提交失败：{type(e).__name__}: {e}")


class _ReviewStatusWorkerSignals(QObject):
    finished = Signal(dict)  # ReviewStatusResponse
    error = Signal(str)


class _LatestDateWorkerSignals(QObject):
    finished = Signal(str)  # date_str
    error = Signal(str)


class _LatestDateWorker(QRunnable):
    """在后台线程中获取最新的评分日期"""
    def __init__(self):
        super().__init__()
        self.signals = _LatestDateWorkerSignals()

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
            if not isinstance(score, dict):
                self.signals.error.emit("暂无评分记录")
                return
            date_str = score.get("date")
            if not date_str:
                self.signals.error.emit("评分记录中无日期信息")
                return
            self.signals.finished.emit(date_str)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"获取最新评分日期失败：{type(e).__name__}: {e}")


class _ReviewStatusWorker(QRunnable):
    """在后台线程中查询复评状态"""
    def __init__(self, date_str: str):
        super().__init__()
        self._date_str = date_str
        self.signals = _ReviewStatusWorkerSignals()

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
            resp = client.get_review_status(self._date_str)
            self.signals.finished.emit(resp)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"查询失败：{type(e).__name__}: {e}")


class ReviewView(QWidget):
    def __init__(self):
        super().__init__()

        # 使用 StackedWidget 管理三种状态：表单、加载中、成功/已复评
        self.stack = QStackedWidget()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.stack)

        # 状态 0: 表单页面
        self.form_page = self._create_form_page()
        self.stack.addWidget(self.form_page)

        # 状态 1: 加载中页面
        self.loading_page = self._create_loading_page()
        self.stack.addWidget(self.loading_page)

        # 状态 2: 成功/已复评页面
        self.result_page = self._create_result_page()
        self.stack.addWidget(self.result_page)

        # 默认显示加载中，先获取最新评分日期，然后查询状态
        self._latest_date = None  # 存储最新的评分日期
        
        # 如果已登录，立即加载；否则显示表单页面等待登录
        from utils.api_client import ApiClient
        if ApiClient.is_logged_in():
            self.stack.setCurrentIndex(1)  # 显示加载中
            self._load_latest_date()
        else:
            # 未登录，显示表单页面（用户可以先填写，登录后再提交）
            self.stack.setCurrentIndex(0)
            # 更新日期标签提示需要登录
            self.date_label.setText("请先登录以查看你的上个工作日数据")
    
    def reload_from_api(self):
        """从API重新加载数据（供外部调用，如登录成功后）"""
        # 重置状态，重新加载
        from utils.api_client import ApiClient
        if ApiClient.is_logged_in():
            # 已登录，显示加载中并重新加载
            self.stack.setCurrentIndex(1)  # 显示加载中
            self._latest_date = None
            self._load_latest_date()
        else:
            # 未登录，显示表单页面
            self.stack.setCurrentIndex(0)

    def _create_form_page(self) -> QWidget:
        """创建表单页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("复评中心")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        title.setStyleSheet("background-color: transparent;")
        layout.addWidget(title)

        self.date_label = QLabel("你的上个工作日：加载中…")
        self.date_label.setFont(QFont("Arial", 11))
        self.date_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(self.date_label)

        notice = QLabel("提示：每个自然日只有一次复评机会，请一次性填写完整。")
        notice.setFont(QFont("Arial", 10))
        notice.setStyleSheet("background-color: transparent;")
        layout.addWidget(notice)

        # 自评摘要卡片
        summary_card = QFrame()
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(12, 12, 12, 12)

        summary_row = QHBoxLayout()
        summary_label = QLabel("今天整体工作怎么评价？（必填）")
        summary_label.setFont(QFont("Arial", 11))
        summary_label.setStyleSheet("background-color: transparent;")
        summary_help = QLabel("?")
        summary_help.setStyleSheet("background-color: transparent;")
        summary_help.setToolTip("用 2-4 句概括今天的工作：做了什么、产生了哪些结果、有哪些不足。\n这会作为 AI 复评的总览描述。")
        summary_row.addWidget(summary_label)
        summary_row.addWidget(summary_help)
        summary_row.addStretch()

        self.summary_edit = QTextEdit()

        summary_layout.addLayout(summary_row)
        summary_layout.addWidget(self.summary_edit)

        summary_card.setProperty("class", "card")
        layout.addWidget(summary_card)

        # Key Points 卡片
        key_card = QFrame()
        key_layout = QVBoxLayout(key_card)
        key_layout.setContentsMargins(12, 12, 12, 12)

        key_row = QHBoxLayout()
        key_label = QLabel("关键事实 / 支撑点（可添加多条）")
        key_label.setFont(QFont("Arial", 11))
        key_label.setStyleSheet("background-color: transparent;")
        key_help = QLabel("?")
        key_help.setStyleSheet("background-color: transparent;")
        key_help.setToolTip("列出 2-5 条你认为最能代表今天价值的具体事实：\n例如完成了哪些需求、解决了哪些疑难问题、推动了哪些协作。")
        key_row.addWidget(key_label)
        key_row.addWidget(key_help)
        key_row.addStretch()
        key_layout.addLayout(key_row)

        key_input_layout = QHBoxLayout()
        self.key_edit = QLineEdit()
        add_btn = QPushButton("添加 +")
        add_btn.clicked.connect(self.add_key_point)
        key_input_layout.addWidget(self.key_edit)
        key_input_layout.addWidget(add_btn)

        key_layout.addLayout(key_input_layout)

        self.key_list = QListWidget()
        # 禁用直接编辑，改为双击弹窗编辑
        self.key_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 启用选择
        self.key_list.setSelectionMode(QAbstractItemView.SingleSelection)
        # 添加右键菜单
        self.key_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.key_list.customContextMenuRequested.connect(self._show_key_list_context_menu)
        # 双击弹窗编辑
        self.key_list.itemDoubleClicked.connect(self._on_key_item_double_clicked)
        # 设置列表的 tooltip，提示用户可以双击或右键编辑和删除
        self.key_list.setToolTip("提示：双击可编辑，右键可编辑或删除")
        key_layout.addWidget(self.key_list)

        key_card.setProperty("class", "card")
        layout.addWidget(key_card)

        # 补充证据卡片
        extra_card = QFrame()
        extra_layout = QVBoxLayout(extra_card)
        extra_layout.setContentsMargins(12, 12, 12, 12)

        extra_row = QHBoxLayout()
        extra_label = QLabel("补充证据（可选）")
        extra_label.setFont(QFont("Arial", 11))
        extra_label.setStyleSheet("background-color: transparent;")
        extra_help = QLabel("?")
        extra_help.setStyleSheet("background-color: transparent;")
        extra_help.setToolTip("如果有额外可以佐证你表现的材料，可以贴在这里：\n例如 Jira 任务号、PR 链接、文档链接、会议记录等。")
        extra_row.addWidget(extra_label)
        extra_row.addWidget(extra_help)
        extra_row.addStretch()

        self.extra_edit = QTextEdit()

        extra_layout.addLayout(extra_row)
        extra_layout.addWidget(self.extra_edit)

        extra_card.setProperty("class", "card")
        layout.addWidget(extra_card)

        submit_btn = QPushButton("提交复评")
        submit_btn.clicked.connect(self.submit_review)
        submit_btn.setFixedWidth(160)
        layout.addWidget(submit_btn)

        layout.addStretch()
        return page

    def _create_loading_page(self) -> QWidget:
        """创建加载中页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("加载中…")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("background-color: transparent;")
        layout.addWidget(title)

        hint = QLabel("正在查询复评状态…")
        hint.setFont(QFont("Arial", 12))
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("background-color: transparent;")
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _create_result_page(self) -> QWidget:
        """创建成功/已复评结果页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 标题（动态：提交成功 vs 您今日已完成复评）
        self.result_title = QLabel("✓ 复评提交成功")
        self.result_title.setFont(QFont("Arial", 20, QFont.Bold))
        self.result_title.setStyleSheet("color: green; background-color: transparent;")
        self.result_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.result_title)

        # 结果摘要
        self.result_summary = QLabel()
        self.result_summary.setFont(QFont("Arial", 12))
        self.result_summary.setAlignment(Qt.AlignCenter)
        self.result_summary.setStyleSheet("background-color: transparent;")
        layout.addWidget(self.result_summary)

        # 对比区域（仅提交成功后显示）
        self.comparison_frame = QFrame()
        comparison_layout = QVBoxLayout(self.comparison_frame)
        comparison_layout.setContentsMargins(12, 12, 12, 12)

        comparison_title = QLabel("复评对比")
        comparison_title.setFont(QFont("Arial", 11, QFont.Bold))
        comparison_title.setStyleSheet("background-color: transparent;")
        comparison_layout.addWidget(comparison_title)

        comparison_content = QHBoxLayout()
        
        # 复评前
        before_frame = QFrame()
        before_layout = QVBoxLayout(before_frame)
        before_layout.setContentsMargins(8, 8, 8, 8)
        before_label = QLabel("复评前")
        before_label.setFont(QFont("Arial", 10))
        before_label.setStyleSheet("background-color: transparent;")
        before_layout.addWidget(before_label)
        self.before_score_label = QLabel("--")
        self.before_score_label.setFont(QFont("Arial", 16, QFont.Bold))
        self.before_score_label.setStyleSheet("background-color: transparent;")
        before_layout.addWidget(self.before_score_label)
        comparison_content.addWidget(before_frame)

        # 箭头
        arrow_label = QLabel("→")
        arrow_label.setFont(QFont("Arial", 20, QFont.Bold))
        arrow_label.setAlignment(Qt.AlignCenter)
        arrow_label.setStyleSheet("background-color: transparent;")
        comparison_content.addWidget(arrow_label)

        # 复评后
        after_frame = QFrame()
        after_layout = QVBoxLayout(after_frame)
        after_layout.setContentsMargins(8, 8, 8, 8)
        after_label = QLabel("复评后")
        after_label.setFont(QFont("Arial", 10))
        after_label.setStyleSheet("background-color: transparent;")
        after_layout.addWidget(after_label)
        self.after_score_label = QLabel("--")
        self.after_score_label.setFont(QFont("Arial", 16, QFont.Bold))
        self.after_score_label.setStyleSheet("background-color: transparent;")
        after_layout.addWidget(self.after_score_label)
        comparison_content.addWidget(after_frame)

        # 排名变化（预留占位）
        rank_frame = QFrame()
        rank_layout = QVBoxLayout(rank_frame)
        rank_layout.setContentsMargins(8, 8, 8, 8)
        rank_label = QLabel("排名变化")
        rank_label.setFont(QFont("Arial", 10))
        rank_label.setStyleSheet("background-color: transparent;")
        rank_layout.addWidget(rank_label)
        self.rank_change_label = QLabel("--")
        self.rank_change_label.setFont(QFont("Arial", 12))
        self.rank_change_label.setStyleSheet("color: gray; background-color: transparent;")
        rank_layout.addWidget(self.rank_change_label)
        comparison_content.addWidget(rank_frame)

        comparison_layout.addLayout(comparison_content)
        self.comparison_frame.setProperty("class", "card")
        layout.addWidget(self.comparison_frame)

        # 复评输入 JSON 区域
        input_label = QLabel("复评输入 JSON（提交给 AI 的数据）")
        input_label.setStyleSheet("background-color: transparent;")
        input_label.setFont(QFont("Arial", 11, QFont.Bold))
        layout.addWidget(input_label)

        self.input_json_text = QTextEdit()
        self.input_json_text.setReadOnly(True)
        self.input_json_text.setFont(QFont("Courier New", 9))
        self.input_json_text.setMaximumHeight(300)  # 限高
        self.input_json_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.input_json_text)

        # 复评结果 JSON 区域
        result_label = QLabel("复评结果 JSON（AI 评分结果）")
        result_label.setFont(QFont("Arial", 11, QFont.Bold))
        result_label.setStyleSheet("background-color: transparent;")
        layout.addWidget(result_label)

        self.result_json_text = QTextEdit()
        self.result_json_text.setReadOnly(True)
        self.result_json_text.setFont(QFont("Courier New", 9))
        self.result_json_text.setMaximumHeight(300)  # 限高
        self.result_json_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(self.result_json_text)

        layout.addStretch()
        return page

    def _update_date_label(self, date_str: Optional[str] = None) -> None:
        """更新日期标签"""
        if date_str:
            try:
                d = date.fromisoformat(date_str)
            except Exception:
                d = date.today()
        else:
            d = date.today()
        weekday_map = "一二三四五六日"
        wd = weekday_map[d.weekday()] if d.weekday() < 7 else "?"
        self.date_label.setText(f"你的上个工作日 {d.strftime('%Y-%m-%d')} 星期{wd} 的数据：")

    def _get_latest_date_str(self) -> str:
        """获取最新的评分日期字符串"""
        if self._latest_date:
            return self._latest_date
        # 如果还没有获取到，返回今天的日期（作为fallback）
        return date.today().isoformat()

    def _load_latest_date(self):
        """加载最新的评分日期"""
        worker = _LatestDateWorker()
        worker.signals.finished.connect(self._on_latest_date_loaded)
        worker.signals.error.connect(self._on_latest_date_error)
        QThreadPool.globalInstance().start(worker)

    def _on_latest_date_loaded(self, date_str: str):
        """最新日期加载完成"""
        self._latest_date = date_str
        self._update_date_label(date_str)
        # 获取到日期后，加载复评状态
        self._load_review_status()

    def _on_latest_date_error(self, error_msg: str):
        """最新日期加载失败"""
        # 使用今天的日期作为fallback
        self._latest_date = date.today().isoformat()
        self._update_date_label(self._latest_date)
        # 即使获取失败，也尝试加载复评状态
        self._load_review_status()
        # 显示错误提示
        Toast.show_message(self, f"获取最新评分日期失败：{error_msg}")

    def _show_key_list_context_menu(self, position):
        """显示关键事实列表的右键菜单"""
        item = self.key_list.itemAt(position)
        if item is None:
            return
        
        menu = QMenu(self)
        
        # 编辑选项
        edit_action = menu.addAction("编辑")
        edit_action.triggered.connect(lambda: self._edit_key_point(item))
        
        # 删除选项
        delete_action = menu.addAction("删除")
        delete_action.triggered.connect(lambda: self._delete_key_point(item))
        
        menu.exec_(self.key_list.mapToGlobal(position))
    
    def _edit_key_point(self, item):
        """编辑关键事实项"""
        if item is None:
            return
        current_text = item.text()
        dialog = QInputDialog(self)
        dialog.setWindowTitle("编辑关键事实")
        dialog.setLabelText("请输入新的内容：")
        dialog.setTextValue(current_text)
        dialog.setTextEchoMode(QLineEdit.Normal)
        # 设置对话框宽度
        dialog.resize(500, dialog.height())
        ok = dialog.exec()
        new_text = dialog.textValue()
        if ok and new_text.strip():
            item.setText(new_text.strip())
            # 更新 tooltip
            item.setToolTip("双击可编辑，右键可编辑或删除")
    
    def _delete_key_point(self, item):
        """删除关键事实项"""
        if item is None:
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除这条关键事实吗？\n\n{item.text()}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            row = self.key_list.row(item)
            self.key_list.takeItem(row)
    
    def _on_key_item_double_clicked(self, item):
        """关键事实项双击事件，弹窗编辑"""
        if item is None:
            return
        self._edit_key_point(item)
    
    def add_key_point(self):
        text = self.key_edit.text().strip()
        if not text:
            return
        # QListWidget.addItem() 不返回值，需要先创建 QListWidgetItem
        item = QListWidgetItem(text)
        # 不需要设置可编辑标志，因为使用弹窗编辑
        item.setToolTip("双击可编辑，右键可编辑或删除")
        self.key_list.addItem(item)
        self.key_edit.clear()

    def _collect_key_points(self) -> List[str]:
        pts: List[str] = []
        for i in range(self.key_list.count()):
            item = self.key_list.item(i)
            if item is not None:
                t = item.text().strip()
                if t:
                    pts.append(t)
        return pts

    def _load_review_status(self):
        """加载复评状态（页面初始化时调用）"""
        date_str = self._get_latest_date_str()
        worker = _ReviewStatusWorker(date_str)
        worker.signals.finished.connect(self._on_status_loaded)
        worker.signals.error.connect(self._on_status_error)
        QThreadPool.globalInstance().start(worker)

    def _on_status_loaded(self, resp: Dict[str, Any]):
        """复评状态加载完成"""
        status = resp.get("status")
        if status != "success":
            # 查询失败，显示表单页面
            self.stack.setCurrentIndex(0)
            return

        is_reviewed = resp.get("is_reviewed", False)
        if is_reviewed:
            # 已复评，显示结果页面
            self._show_review_result(resp, is_submit_success=False)
        else:
            # 未复评，显示表单页面
            self.stack.setCurrentIndex(0)

    def _on_status_error(self, error_msg: str):
        """复评状态查询失败"""
        # 检查是否需要登录
        if any(key in error_msg for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            win = self.window()
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        # 登录成功后重新加载
                        self._load_review_status()
                        return
                return  # 如果已经有登录弹窗，直接返回

        # 查询失败，显示表单页面（允许用户尝试提交）
        self.stack.setCurrentIndex(0)
        Toast.show_message(self, f"查询复评状态失败：{error_msg}")

    def submit_review(self):
        summary = self.summary_edit.toPlainText().strip()
        if not summary:
            Toast.show_message(self, "请先填写自评摘要。")
            return

        key_points = self._collect_key_points()
        extra = self.extra_edit.toPlainText().strip() or None

        # 切换到加载中页面
        self.stack.setCurrentIndex(1)

        # 构造请求体
        payload = {
            "date": self._get_latest_date_str(),
            "daily_summary": summary,
            "key_points": key_points if key_points else None,
            "extra_evidence": extra,
        }

        # 在后台线程中提交
        worker = _ReviewSubmitWorker(payload)
        worker.signals.finished.connect(self._on_submit_success)
        worker.signals.error.connect(self._on_submit_error)
        QThreadPool.globalInstance().start(worker)

    def _on_submit_success(self, resp: Dict[str, Any]):
        """提交成功回调"""
        status = resp.get("status")
        if status != "success":
            # 虽然返回了，但状态不是 success，当作错误处理
            message = resp.get("message") or "提交失败"
            self._on_submit_error(message)
            return

        # 检查是否需要登录
        if any(key in str(resp) for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            win = self.window()
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        # 登录成功后重新提交
                        self.submit_review()
                        return
                    else:
                        # 取消登录，返回表单
                        self.stack.setCurrentIndex(0)
                        return
                else:
                    # 已经有登录弹窗，返回表单
                    self.stack.setCurrentIndex(0)
                    return

        # 显示成功页面（带对比）
        self._show_review_result(resp, is_submit_success=True)

    def _show_review_result(self, resp: Dict[str, Any], is_submit_success: bool = False):
        """显示复评结果页面"""
        # 设置标题
        if is_submit_success:
            self.result_title.setText("✓ 复评提交成功")
            self.result_title.setStyleSheet("color: green;")
            self.comparison_frame.setVisible(True)  # 显示对比
        else:
            self.result_title.setText("您今日已完成复评")
            self.result_title.setStyleSheet("color: blue;")
            self.comparison_frame.setVisible(False)  # 隐藏对比

        # 显示结果摘要
        total = resp.get("total_ai")
        conf = resp.get("confidence")
        if total is not None and conf is not None:
            self.result_summary.setText(f"总分：{total} | 置信度：{conf:.2f}")
        else:
            self.result_summary.setText("复评结果已保存")

        # 显示对比（仅提交成功后）
        if is_submit_success:
            original_total = resp.get("original_total_ai")
            if original_total is not None:
                self.before_score_label.setText(str(original_total))
            else:
                self.before_score_label.setText("--")

            if total is not None:
                self.after_score_label.setText(str(total))
            else:
                self.after_score_label.setText("--")

            # 排名变化
            rank_change = resp.get("rank_change")
            if rank_change is not None:
                if rank_change > 0:
                    self.rank_change_label.setText(f"↑ 上升 {rank_change} 名")
                    self.rank_change_label.setStyleSheet("color: #28a745; font-weight: bold;")
                elif rank_change < 0:
                    self.rank_change_label.setText(f"↓ 下降 {abs(rank_change)} 名")
                    self.rank_change_label.setStyleSheet("color: #dc3545; font-weight: bold;")
                else:
                    self.rank_change_label.setText("→ 无变化")
                    self.rank_change_label.setStyleSheet("color: gray;")
            else:
                self.rank_change_label.setText("（暂无排名数据）")
                self.rank_change_label.setStyleSheet("color: gray;")

        # 显示复评输入 JSON
        review_input = resp.get("review_input_json")
        if review_input:
            try:
                input_json_str = json.dumps(review_input, ensure_ascii=False, indent=2)
                self.input_json_text.setPlainText(input_json_str)
            except Exception:
                self.input_json_text.setPlainText(str(review_input))
        else:
            self.input_json_text.setPlainText("（无输入数据）")

        # 显示复评结果 JSON
        raw_result = resp.get("raw_result") or resp.get("review_result")
        if raw_result:
            try:
                result_json_str = json.dumps(raw_result, ensure_ascii=False, indent=2)
                self.result_json_text.setPlainText(result_json_str)
            except Exception:
                self.result_json_text.setPlainText(str(raw_result))
        else:
            self.result_json_text.setPlainText("（无结果数据）")

        # 切换到结果页面
        self.stack.setCurrentIndex(2)

    def _on_submit_error(self, error_msg: str):
        """提交失败回调"""
        # 返回表单页面
        self.stack.setCurrentIndex(0)

        # 检查是否需要登录
        if any(key in error_msg for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            win = self.window()
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        # 登录成功后重新提交
                        self.submit_review()
                        return
                return  # 如果已经有登录弹窗，直接返回，不显示 Toast

        # 显示错误提示
        Toast.show_message(self, f"提交失败：{error_msg}")
