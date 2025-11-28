#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管理端操作日志页面：
- 支持按日期范围、管理员邮箱、HTTP方法、接口路径、响应状态码筛选
- 显示所有管理端操作详情
- 查看请求参数和响应详情
- 支持无限滚动分页（滚动到底部自动加载更多）
"""

import json
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QDialog,
    QTextEdit, QHeaderView, QDateEdit, QLineEdit, QMessageBox,
    QAbstractItemView, QScrollBar, QTabWidget
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QDate
from PySide6.QtGui import QFont

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from widgets.toast import Toast


class _OperationLogWorkerSignals(QObject):
    finished = Signal(list, int)  # List[Dict], total_count
    error = Signal(str)


class _OperationLogWorker(QRunnable):
    """后台加载操作日志数据"""
    def __init__(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        client_type: Optional[str] = None,
        admin_email: Optional[str] = None,
        user_id: Optional[str] = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        response_status: Optional[int] = None,
        offset: int = 0,
        limit: int = 50
    ):
        super().__init__()
        self._start_date = start_date
        self._end_date = end_date
        self._client_type = client_type
        self._admin_email = admin_email
        self._user_id = user_id
        self._method = method
        self._path = path
        self._response_status = response_status
        self._offset = offset
        self._limit = limit
        self.signals = _OperationLogWorkerSignals()

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
            resp = client.get_operation_logs(
                start_date=self._start_date,
                end_date=self._end_date,
                client_type=self._client_type,
                admin_email=self._admin_email,
                user_id=self._user_id,
                method=self._method,
                path=self._path,
                response_status=self._response_status,
                offset=self._offset,
                limit=self._limit
            )
            items = resp.get("items", []) if isinstance(resp, dict) else []
            # total_count 是返回的数据条数，用于判断是否还有更多数据
            total_count = len(items)
            self.signals.finished.emit(items, total_count)
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"加载操作日志失败：{e}")


class OperationLogView(QWidget):
    def __init__(self):
        super().__init__()
        
        # 分页相关状态
        self._current_offset = 0
        self._page_size = 50
        self._is_loading = False
        self._has_more = True
        self._current_filters = {}  # 保存当前筛选条件

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(8)

        title = QLabel("操作日志")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # TAB切换：管理端/员工端
        self._tab_widget = QTabWidget()
        self._tab_widget.setMaximumHeight(120)  # 限制TAB高度
        self._admin_tab = QWidget()
        self._employee_tab = QWidget()
        self._tab_widget.addTab(self._admin_tab, "管理端")
        self._tab_widget.addTab(self._employee_tab, "员工端")
        layout.addWidget(self._tab_widget)
        
        # 当前选中的TAB（默认管理端）
        self._current_client_type = "admin"
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        # 管理端筛选区域（两行布局）
        admin_filter_container = QVBoxLayout(self._admin_tab)
        admin_filter_container.setContentsMargins(8, 8, 8, 8)
        admin_filter_container.setSpacing(6)
        
        # 第一行
        admin_filter_row1 = QHBoxLayout()
        admin_filter_row1.setSpacing(6)

        admin_filter_row1.addWidget(QLabel("开始日期："))
        self._admin_start_date_edit = QDateEdit()
        self._admin_start_date_edit.setCalendarPopup(True)
        self._admin_start_date_edit.setDate(QDate.currentDate().addDays(-7))
        self._admin_start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._admin_start_date_edit.setFixedHeight(28)
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._admin_start_date_edit)
        admin_filter_row1.addWidget(self._admin_start_date_edit)

        admin_filter_row1.addWidget(QLabel("结束日期："))
        self._admin_end_date_edit = QDateEdit()
        self._admin_end_date_edit.setCalendarPopup(True)
        self._admin_end_date_edit.setDate(QDate.currentDate())
        self._admin_end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._admin_end_date_edit.setFixedHeight(28)
        apply_theme_to_date_edit(self._admin_end_date_edit)
        admin_filter_row1.addWidget(self._admin_end_date_edit)

        admin_filter_row1.addWidget(QLabel("管理员："))
        self._admin_email_edit = QLineEdit()
        self._admin_email_edit.setPlaceholderText("留空显示所有管理员")
        self._admin_email_edit.setFixedHeight(28)
        admin_filter_row1.addWidget(self._admin_email_edit)

        admin_filter_row1.addWidget(QLabel("方法："))
        self._admin_method_combo = QComboBox()
        self._admin_method_combo.addItems(["全部", "POST", "PUT", "DELETE"])
        self._admin_method_combo.setCurrentIndex(0)
        self._admin_method_combo.setFixedHeight(28)
        admin_filter_row1.addWidget(self._admin_method_combo)

        admin_filter_row1.addStretch()

        # 第二行
        admin_filter_row2 = QHBoxLayout()
        admin_filter_row2.setSpacing(6)

        admin_filter_row2.addWidget(QLabel("路径："))
        self._admin_path_edit = QLineEdit()
        self._admin_path_edit.setPlaceholderText("留空显示所有路径")
        self._admin_path_edit.setFixedHeight(28)
        admin_filter_row2.addWidget(self._admin_path_edit)

        admin_filter_row2.addWidget(QLabel("状态码："))
        self._admin_status_code_edit = QLineEdit()
        self._admin_status_code_edit.setPlaceholderText("如：200")
        self._admin_status_code_edit.setFixedWidth(100)
        self._admin_status_code_edit.setFixedHeight(28)
        admin_filter_row2.addWidget(self._admin_status_code_edit)

        btn_admin_filter = QPushButton("筛选")
        btn_admin_filter.setFixedHeight(28)
        btn_admin_filter.clicked.connect(lambda: self._on_filter_clicked("admin"))
        admin_filter_row2.addWidget(btn_admin_filter)

        btn_admin_clear = QPushButton("清除筛选")
        btn_admin_clear.setFixedHeight(28)
        btn_admin_clear.clicked.connect(lambda: self._on_clear_filter("admin"))
        admin_filter_row2.addWidget(btn_admin_clear)

        admin_filter_row2.addStretch()

        admin_filter_container.addLayout(admin_filter_row1)
        admin_filter_container.addLayout(admin_filter_row2)

        # 员工端筛选区域（两行布局）
        employee_filter_container = QVBoxLayout(self._employee_tab)
        employee_filter_container.setContentsMargins(8, 8, 8, 8)
        employee_filter_container.setSpacing(6)
        
        # 第一行
        employee_filter_row1 = QHBoxLayout()
        employee_filter_row1.setSpacing(6)

        employee_filter_row1.addWidget(QLabel("开始日期："))
        self._employee_start_date_edit = QDateEdit()
        self._employee_start_date_edit.setCalendarPopup(True)
        self._employee_start_date_edit.setDate(QDate.currentDate().addDays(-7))
        self._employee_start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._employee_start_date_edit.setFixedHeight(28)
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self._employee_start_date_edit)
        employee_filter_row1.addWidget(self._employee_start_date_edit)

        employee_filter_row1.addWidget(QLabel("结束日期："))
        self._employee_end_date_edit = QDateEdit()
        self._employee_end_date_edit.setCalendarPopup(True)
        self._employee_end_date_edit.setDate(QDate.currentDate())
        self._employee_end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._employee_end_date_edit.setFixedHeight(28)
        apply_theme_to_date_edit(self._employee_end_date_edit)
        employee_filter_row1.addWidget(self._employee_end_date_edit)

        employee_filter_row1.addWidget(QLabel("用户ID："))
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("留空显示所有用户")
        self._user_id_edit.setFixedHeight(28)
        employee_filter_row1.addWidget(self._user_id_edit)

        employee_filter_row1.addWidget(QLabel("方法："))
        self._employee_method_combo = QComboBox()
        self._employee_method_combo.addItems(["全部", "POST", "PUT", "DELETE"])
        self._employee_method_combo.setCurrentIndex(0)
        self._employee_method_combo.setFixedHeight(28)
        employee_filter_row1.addWidget(self._employee_method_combo)

        employee_filter_row1.addStretch()

        # 第二行
        employee_filter_row2 = QHBoxLayout()
        employee_filter_row2.setSpacing(6)

        employee_filter_row2.addWidget(QLabel("路径："))
        self._employee_path_edit = QLineEdit()
        self._employee_path_edit.setPlaceholderText("留空显示所有路径")
        self._employee_path_edit.setFixedHeight(28)
        employee_filter_row2.addWidget(self._employee_path_edit)

        employee_filter_row2.addWidget(QLabel("状态码："))
        self._employee_status_code_edit = QLineEdit()
        self._employee_status_code_edit.setPlaceholderText("如：200")
        self._employee_status_code_edit.setFixedWidth(100)
        self._employee_status_code_edit.setFixedHeight(28)
        employee_filter_row2.addWidget(self._employee_status_code_edit)

        btn_employee_filter = QPushButton("筛选")
        btn_employee_filter.setFixedHeight(28)
        btn_employee_filter.clicked.connect(lambda: self._on_filter_clicked("employee"))
        employee_filter_row2.addWidget(btn_employee_filter)

        btn_employee_clear = QPushButton("清除筛选")
        btn_employee_clear.setFixedHeight(28)
        btn_employee_clear.clicked.connect(lambda: self._on_clear_filter("employee"))
        employee_filter_row2.addWidget(btn_employee_clear)

        employee_filter_row2.addStretch()

        employee_filter_container.addLayout(employee_filter_row1)
        employee_filter_container.addLayout(employee_filter_row2)


        # 表格区域
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels([
            "操作时间", "客户端类型", "操作人", "操作描述", "方法", "路径", "状态码", "响应时间(ms)", "操作"
        ])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 操作时间
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 客户端类型
        # 操作人列：使用固定宽度模式，避免占用太多空间，不需要完整显示
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # 操作人（管理员/用户ID）
        self._table.setColumnWidth(2, 150)  # 操作人列固定宽度150px
        # 操作描述列：自动撑满剩余空间，确保完整显示
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # 操作描述（自动撑满剩余空间，完整显示）
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 方法
        # 路径列：固定宽度，不需要完整显示
        header.setSectionResizeMode(5, QHeaderView.Fixed)  # 路径
        self._table.setColumnWidth(5, 200)  # 路径列固定宽度200px
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 状态码
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # 响应时间
        header.setSectionResizeMode(8, QHeaderView.ResizeToContents)  # 操作
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格水平撑满
        from PySide6.QtWidgets import QSizePolicy
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # 监听滚动事件，实现无限滚动
        scroll_bar = self._table.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._on_scroll_changed)
        
        layout.addWidget(self._table, 1)  # 添加拉伸因子，让表格撑满
        
        # 底部状态栏（显示加载状态和"没有更多数据"）
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet("color: #666; padding: 8px;")
        layout.addWidget(self._status_label)

    def _on_tab_changed(self, index: int):
        """TAB切换事件"""
        if index == 0:
            self._current_client_type = "admin"
        else:
            self._current_client_type = "employee"
        # 切换TAB时重置分页并重新加载
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)
        # 自动执行筛选（延迟执行，避免在切换时立即触发）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, lambda: self._on_filter_clicked(self._current_client_type))

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
        
        worker = _OperationLogWorker(
            start_date=self._current_filters.get("start_date"),
            end_date=self._current_filters.get("end_date"),
            client_type=self._current_client_type,
            admin_email=self._current_filters.get("admin_email"),
            user_id=self._current_filters.get("user_id"),
            method=self._current_filters.get("method"),
            path=self._current_filters.get("path"),
            response_status=self._current_filters.get("response_status"),
            offset=self._current_offset,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_more_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_filter_clicked(self, client_type: str = None):
        """执行筛选（重置分页）"""
        if client_type is None:
            client_type = self._current_client_type
        
        if client_type == "admin":
            start_date = self._admin_start_date_edit.date().toString("yyyy-MM-dd")
            end_date = self._admin_end_date_edit.date().toString("yyyy-MM-dd")
            admin_email = self._admin_email_edit.text().strip() or None
            method = self._admin_method_combo.currentText()
            method = None if method == "全部" else method
            path = self._admin_path_edit.text().strip() or None
            status_code_str = self._admin_status_code_edit.text().strip()
            user_id = None
        else:  # employee
            start_date = self._employee_start_date_edit.date().toString("yyyy-MM-dd")
            end_date = self._employee_end_date_edit.date().toString("yyyy-MM-dd")
            user_id = self._user_id_edit.text().strip() or None
            method = self._employee_method_combo.currentText()
            method = None if method == "全部" else method
            path = self._employee_path_edit.text().strip() or None
            status_code_str = self._employee_status_code_edit.text().strip()
            admin_email = None
        
        response_status = None
        if status_code_str:
            try:
                response_status = int(status_code_str)
            except ValueError:
                QMessageBox.warning(self, "输入错误", "状态码必须是数字")
                return

        # 保存筛选条件
        self._current_filters = {
            "start_date": start_date,
            "end_date": end_date,
            "admin_email": admin_email,
            "user_id": user_id,
            "method": method,
            "path": path,
            "response_status": response_status,
        }
        self._current_client_type = client_type
        
        # 重置分页状态
        self._current_offset = 0
        self._has_more = True
        self._table.setRowCount(0)  # 清空表格
        
        main_window = self.window()
        if hasattr(main_window, "show_loading"):
            main_window.show_loading("加载操作日志中...")

        worker = _OperationLogWorker(
            start_date=start_date,
            end_date=end_date,
            client_type=client_type,
            admin_email=admin_email,
            user_id=user_id,
            method=method,
            path=path,
            response_status=response_status,
            offset=0,
            limit=self._page_size
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_clear_filter(self, client_type: str = None):
        """清除筛选条件"""
        if client_type is None:
            client_type = self._current_client_type
        
        if client_type == "admin":
            self._admin_start_date_edit.setDate(QDate.currentDate().addDays(-7))
            self._admin_end_date_edit.setDate(QDate.currentDate())
            self._admin_email_edit.clear()
            self._admin_method_combo.setCurrentIndex(0)
            self._admin_path_edit.clear()
            self._admin_status_code_edit.clear()
        else:  # employee
            self._employee_start_date_edit.setDate(QDate.currentDate().addDays(-7))
            self._employee_end_date_edit.setDate(QDate.currentDate())
            self._user_id_edit.clear()
            self._employee_method_combo.setCurrentIndex(0)
            self._employee_path_edit.clear()
            self._employee_status_code_edit.clear()
        
        # 清除后自动执行一次筛选
        self._on_filter_clicked(client_type)

    def _on_data_loaded(self, items: List[Dict[str, Any]], total_count: int):
        """首次数据加载完成"""
        main_window = self.window()
        if hasattr(main_window, "hide_loading"):
            main_window.hide_loading()
        
        self._is_loading = False
        self._apply_rows_to_table(items, append=False)
        
        # 更新分页状态：offset 应该是已加载的数据条数
        self._current_offset = len(items)
        # 如果返回的数据量等于 page_size，说明可能还有更多数据
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
        
        # 更新分页状态：offset 应该是已加载的数据条数（累加）
        self._current_offset += len(items)
        # 如果返回的数据量小于 page_size，说明没有更多数据了
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
            created_at = item.get("created_at", "")
            admin_email = item.get("admin_email", "")
            # 优先使用后端返回的 operation_desc（人性化描述）
            # 如果后端没有返回或为空，则使用 method + path 作为 fallback
            operation_desc = item.get("operation_desc")
            if not operation_desc or operation_desc.strip() == "":
                operation_desc = f"{item.get('method', '')} {item.get('path', '')}"
            method = item.get("method", "")
            path = item.get("path", "")
            response_status = item.get("response_status", 0)
            response_time_ms = item.get("response_time_ms")
            error_message = item.get("error_message") or ""
            query_params = item.get("query_params")
            request_body = item.get("request_body")

            # 格式化时间
            if created_at:
                if isinstance(created_at, str):
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass

            client_type = item.get("client_type", "admin")
            admin_email = item.get("admin_email")
            user_id = item.get("user_id")
            
            self._table.setItem(row, 0, QTableWidgetItem(created_at))
            
            # 客户端类型列
            client_type_item = QTableWidgetItem("管理端" if client_type == "admin" else "员工端")
            client_type_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 1, client_type_item)
            
            # 操作人列（管理员邮箱或用户ID）
            operator_str = admin_email if client_type == "admin" and admin_email else (user_id if user_id else "-")
            self._table.setItem(row, 2, QTableWidgetItem(operator_str))
            
            # 操作描述列（人性化文字）- 确保完整显示
            desc_item = QTableWidgetItem(operation_desc)
            desc_item.setToolTip(operation_desc)
            # 设置文本对齐方式，确保文本完整显示
            desc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._table.setItem(row, 3, desc_item)
            
            self._table.setItem(row, 4, QTableWidgetItem(method))
            self._table.setItem(row, 5, QTableWidgetItem(path))
            
            # 状态码列：根据状态码设置颜色
            status_item = QTableWidgetItem(str(response_status))
            if 200 <= response_status < 300:
                status_item.setForeground(Qt.green)
            elif 400 <= response_status < 500:
                status_item.setForeground(Qt.yellow)
            elif response_status >= 500:
                status_item.setForeground(Qt.red)
            status_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 6, status_item)
            
            response_time_str = str(response_time_ms) if response_time_ms is not None else "-"
            response_time_item = QTableWidgetItem(response_time_str)
            response_time_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 7, response_time_item)

            # 操作列：查看详情按钮
            btn_view = QPushButton("查看")
            btn_view.setFixedSize(52, 22)
            btn_view.setStyleSheet("""
                QPushButton {
                    font-size: 9pt;
                    padding: 0px;
                }
            """)
            btn_view.setProperty("query_params", json.dumps(query_params, ensure_ascii=False) if query_params else "")
            btn_view.setProperty("request_body", json.dumps(request_body, ensure_ascii=False) if request_body else "")
            btn_view.setProperty("response_status", response_status)
            btn_view.setProperty("error_message", error_message)
            btn_view.setProperty("path", path)
            btn_view.setProperty("method", method)
            btn_view.setProperty("admin_email", admin_email or "")
            btn_view.setProperty("user_id", user_id or "")
            btn_view.setProperty("client_type", client_type)
            btn_view.setProperty("ip_address", item.get("ip_address") or "")
            btn_view.setProperty("user_agent", item.get("user_agent") or "")
            btn_view.setProperty("response_time_ms", response_time_ms or 0)
            btn_view.clicked.connect(self._on_view_detail_clicked)
            self._table.setCellWidget(row, 8, btn_view)

    def _on_view_detail_clicked(self):
        """查看详情"""
        btn = self.sender()
        if not btn:
            return

        query_params_str = btn.property("query_params") or ""
        request_body_str = btn.property("request_body") or ""
        response_status = btn.property("response_status") or 0
        error_message = btn.property("error_message") or ""
        path = btn.property("path") or ""
        method = btn.property("method") or ""
        admin_email = btn.property("admin_email") or ""
        ip_address = btn.property("ip_address") or ""
        user_agent = btn.property("user_agent") or ""
        response_time_ms = btn.property("response_time_ms") or 0

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{method} {path} - 详情")
        dlg.resize(900, 700)

        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))

        client_type = btn.property("client_type") or "admin"
        admin_email = btn.property("admin_email") or ""
        user_id = btn.property("user_id") or ""
        
        content = []
        if client_type == "admin":
            content.append(f"客户端类型：管理端\n")
            content.append(f"管理员：{admin_email}\n")
        else:
            content.append(f"客户端类型：员工端\n")
            content.append(f"用户ID：{user_id}\n")
        content.append(f"请求方法：{method}\n")
        content.append(f"请求路径：{path}\n")  # 添加路径到body中，方便复制
        content.append(f"IP地址：{ip_address}\n")
        content.append(f"User-Agent：{user_agent}\n")
        content.append(f"响应时间：{response_time_ms}ms\n")
        content.append(f"响应状态码：{response_status}\n")
        content.append("\n" + "="*50 + "\n\n")
        
        if query_params_str:
            try:
                query_params = json.loads(query_params_str)
                content.append("查询参数：\n")
                content.append(json.dumps(query_params, ensure_ascii=False, indent=2))
                content.append("\n\n")
            except:
                content.append(f"查询参数（原始）：\n{query_params_str}\n\n")
        
        if request_body_str:
            try:
                request_body = json.loads(request_body_str)
                content.append("请求体：\n")
                content.append(json.dumps(request_body, ensure_ascii=False, indent=2))
                content.append("\n\n")
            except:
                content.append(f"请求体（原始）：\n{request_body_str}\n\n")
        
        if error_message:
            content.append("错误信息：\n")
            content.append(error_message)

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
