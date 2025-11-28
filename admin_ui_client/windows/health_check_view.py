#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健康检查视图：显示AI健康检查记录
"""

import json
import httpx
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, Optional
from functools import partial

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QComboBox, QDateEdit, QMessageBox, QDialog, QTextEdit,
    QTabWidget
)
from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt, QDate, QRunnable, QThreadPool, QObject, Signal, Slot

from utils.api_client import AdminApiClient, ApiError, AuthError
from utils.error_handler import handle_api_error
from utils.config_manager import ConfigManager
from widgets.toast import Toast


class _HealthCheckWorkerSignals(QObject):
    finished = Signal(list)
    error = Signal(str)


class _OpenAIBalanceWorkerSignals(QObject):
    finished = Signal(dict)  # Dict[str, Any]
    error = Signal(str)


class _HealthCheckWorker(QRunnable):
    """后台线程：获取健康检查记录"""
    def __init__(self, start_date: Optional[str] = None, end_date: Optional[str] = None,
                 status_filter: Optional[str] = None, limit: int = 30):
        super().__init__()
        self.signals = _HealthCheckWorkerSignals()
        self.start_date = start_date
        self.end_date = end_date
        self.status_filter = status_filter
        self.limit = limit

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
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
            resp = client.get_health_checks(
                start_date=self.start_date,
                end_date=self.end_date,
                status_filter=self.status_filter,
                limit=self.limit,
            )
            if resp.get("status") == "success":
                items = resp.get("items", [])
                self.signals.finished.emit(items)
            else:
                self.signals.error.emit(resp.get("message", "查询失败"))
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
        except Exception as e:
            self.signals.error.emit(f"查询失败：{type(e).__name__}: {e}")


class _OpenAIBalanceWorker(QRunnable):
    """后台线程：获取OpenAI余额信息"""
    def __init__(self, session_key: str):
        super().__init__()
        self.signals = _OpenAIBalanceWorkerSignals()
        self.session_key = session_key

    @Slot()
    def run(self) -> None:
        try:
            # 使用 session key，格式：Bearer sess-xxx
            headers = {"Authorization": f"Bearer {self.session_key}"}
            
            # 获取信用额度信息
            credit_url = "https://api.openai.com/v1/dashboard/billing/credit_grants"
            
            try:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(credit_url, headers=headers)
                    if resp.status_code == 200:
                        credit_data = resp.json()
                        
                        balance_info = {}
                        
                        # 解析信用额度信息
                        total_granted = credit_data.get("total_granted")
                        total_used = credit_data.get("total_used")
                        total_available = credit_data.get("total_available")
                        total_paid_available = credit_data.get("total_paid_available")
                        
                        if total_granted is not None:
                            balance_info["total_granted_usd"] = total_granted
                            balance_info["total_granted_cny"] = total_granted * 7.2
                        
                        if total_used is not None:
                            balance_info["total_used_usd"] = total_used
                            balance_info["total_used_cny"] = total_used * 7.2
                        
                        if total_available is not None:
                            balance_info["total_available_usd"] = total_available
                            balance_info["total_available_cny"] = total_available * 7.2
                        
                        if total_paid_available is not None:
                            balance_info["total_paid_available_usd"] = total_paid_available
                            balance_info["total_paid_available_cny"] = total_paid_available * 7.2
                        
                        # 计算使用率
                        if total_granted is not None and total_granted > 0:
                            if total_used is not None:
                                balance_info["usage_percentage"] = (total_used / total_granted) * 100
                            else:
                                balance_info["usage_percentage"] = 0
                        
                        # 获取 grants 信息（如果有）
                        grants = credit_data.get("grants", {})
                        grants_data = grants.get("data", [])
                        if grants_data:
                            # 使用第一个 grant 的信息
                            first_grant = grants_data[0]
                            if "effective_at" in first_grant:
                                balance_info["effective_at"] = first_grant["effective_at"]
                            if "expires_at" in first_grant:
                                balance_info["expires_at"] = first_grant["expires_at"]
                        
                        self.signals.finished.emit(balance_info)
                    else:
                        error_text = resp.text[:500] if resp.text else "无响应内容"
                        self.signals.error.emit(f"API返回状态码: {resp.status_code}, 响应: {error_text}")
            except httpx.HTTPError as e:
                self.signals.error.emit(f"HTTP请求失败: {str(e)}")
            except Exception as e:
                self.signals.error.emit(f"获取余额失败: {type(e).__name__}: {str(e)}")
        except Exception as e:
            self.signals.error.emit(f"获取余额失败: {type(e).__name__}: {str(e)}")


class _DetailDialog(QDialog):
    """详情对话框"""
    def __init__(self, item: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("健康检查详情")
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 基本信息
        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(6)

        info_layout.addWidget(QLabel(f"检查日期: {item.get('check_date', 'N/A')}"))
        # 检查时间（数据库存储的是本地时间，直接显示）
        check_time = item.get('check_time', 'N/A')
        if isinstance(check_time, datetime):
            # 如果datetime是naive（无时区），假设是本地时间，直接格式化
            if check_time.tzinfo is None:
                check_time_str = check_time.strftime('%Y-%m-%d %H:%M:%S')
            else:
                # 如果有时区信息，转换为本地时间
                local_time = check_time.astimezone()
                check_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(check_time, str):
            try:
                # 尝试解析ISO格式
                if 'Z' in check_time or '+' in check_time or check_time.count('-') > 2:
                    # 带时区信息
                    dt = datetime.fromisoformat(check_time.replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    local_time = dt.astimezone()
                    check_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    # 无时区信息，假设是本地时间，直接格式化
                    dt = datetime.fromisoformat(check_time)
                    check_time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                check_time_str = str(check_time)
        else:
            check_time_str = str(check_time)
        info_layout.addWidget(QLabel(f"检查时间: {check_time_str}"))
        info_layout.addWidget(QLabel(f"状态: {item.get('status', 'N/A')}"))
        info_layout.addWidget(QLabel(f"HTTP状态码: {item.get('http_status', 'N/A')}"))
        info_layout.addWidget(QLabel(f"响应时间: {item.get('response_time_ms', 'N/A')}ms"))
        info_layout.addWidget(QLabel(f"使用的模型: {item.get('model_used', 'N/A')}"))
        info_layout.addWidget(QLabel(f"已发送告警: {'是' if item.get('alert_sent') else '否'}"))

        if item.get('error_message'):
            error_label = QLabel(f"错误信息: {item.get('error_message')}")
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: red;")
            info_layout.addWidget(error_label)

        layout.addWidget(info_frame)

        # 详细信息（JSON）
        detail_label = QLabel("详细信息（JSON）:")
        detail_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(detail_label)

        detail_text = QTextEdit()
        detail_text.setReadOnly(True)
        detail_text.setFont(QFont("Courier", 9))
        detail_json = item.get('detail_json')
        if detail_json:
            detail_text.setPlainText(json.dumps(detail_json, ensure_ascii=False, indent=2))
        else:
            detail_text.setPlainText("无详细信息")
        layout.addWidget(detail_text)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class HealthCheckView(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("AI健康检查")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)

        # OpenAI余额显示区域
        balance_frame = QFrame()
        balance_frame.setProperty("class", "card")
        balance_layout = QHBoxLayout(balance_frame)
        balance_layout.setContentsMargins(12, 12, 12, 12)
        balance_layout.setSpacing(12)
        
        balance_title = QLabel("OpenAI余额:")
        balance_title.setFont(QFont("Arial", 10, QFont.Bold))
        # 使用主题颜色，不设置固定颜色
        balance_layout.addWidget(balance_title)
        
        self.balance_label = QLabel("加载中...")
        self.balance_label.setFont(QFont("Arial", 10))
        self.balance_label.setWordWrap(True)
        # 使用主题颜色，不设置固定颜色
        balance_layout.addWidget(self.balance_label)
        
        balance_layout.addStretch()
        
        refresh_balance_btn = QPushButton("刷新余额")
        refresh_balance_btn.clicked.connect(self._on_refresh_balance)
        balance_layout.addWidget(refresh_balance_btn)
        
        open_dashboard_btn = QPushButton("打开Dashboard")
        open_dashboard_btn.clicked.connect(self._on_open_dashboard)
        balance_layout.addWidget(open_dashboard_btn)
        
        layout.addWidget(balance_frame)
        
        # 初始加载余额
        self._on_refresh_balance()

        # 过滤区域
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)

        start_label = QLabel("开始日期:")
        # 使用主题颜色，不设置固定颜色
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addDays(-30))
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        from utils.date_edit_helper import apply_theme_to_date_edit
        apply_theme_to_date_edit(self.start_date_edit)

        end_label = QLabel("结束日期:")
        # 使用主题颜色，不设置固定颜色
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        apply_theme_to_date_edit(self.end_date_edit)

        status_label = QLabel("状态筛选:")
        # 使用主题颜色，不设置固定颜色
        self.status_combo = QComboBox()
        self.status_combo.addItems(["全部", "healthy", "rate_limited", "error", "unknown"])

        filter_layout.addWidget(start_label)
        filter_layout.addWidget(self.start_date_edit)
        filter_layout.addWidget(end_label)
        filter_layout.addWidget(self.end_date_edit)
        filter_layout.addWidget(status_label)
        filter_layout.addWidget(self.status_combo)
        filter_layout.addStretch()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._on_refresh)
        filter_layout.addWidget(refresh_btn)

        filter_frame.setProperty("class", "card")
        layout.addWidget(filter_frame)

        # 表格
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "检查日期", "检查时间", "状态", "HTTP状态码", "响应时间(ms)",
            "模型", "已发送告警", "操作"
        ])

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)  # 模型列撑满
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        from PySide6.QtWidgets import QSizePolicy
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.table, 1)  # 添加拉伸因子，让表格撑满

        # 初始不自动加载，等待首次切换到此页面时加载
    
    def reload_from_api(self):
        """从API重新加载数据（供MainWindow调用）"""
        self._on_refresh()

    def _on_refresh(self):
        """刷新数据"""
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading("加载健康检查记录中…")

        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        status_filter = self.status_combo.currentText()
        if status_filter == "全部":
            status_filter = None

        worker = _HealthCheckWorker(
            start_date=start_date,
            end_date=end_date,
            status_filter=status_filter,
            limit=100,
        )
        worker.signals.finished.connect(self._on_data_loaded)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_data_loaded(self, items: list):
        """数据加载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        self.table.setRowCount(0)
        self.table.setRowCount(len(items))

        for row, item in enumerate(items):
            # 检查日期
            check_date = item.get('check_date', '')
            if isinstance(check_date, str):
                date_item = QTableWidgetItem(check_date)
            else:
                date_item = QTableWidgetItem(str(check_date))
            date_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, date_item)

            # 检查时间（数据库存储的是本地时间，直接显示）
            check_time = item.get('check_time', '')
            if isinstance(check_time, datetime):
                # 如果datetime是naive（无时区），假设是本地时间，直接格式化
                if check_time.tzinfo is None:
                    time_str = check_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    # 如果有时区信息，转换为本地时间
                    local_time = check_time.astimezone()
                    time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
            elif isinstance(check_time, str):
                # 尝试解析字符串格式的datetime
                try:
                    # 尝试解析ISO格式
                    if 'Z' in check_time or '+' in check_time or check_time.count('-') > 2:
                        # 带时区信息
                        dt = datetime.fromisoformat(check_time.replace('Z', '+00:00'))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        local_time = dt.astimezone()
                        time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # 无时区信息，假设是本地时间，直接格式化
                        dt = datetime.fromisoformat(check_time)
                        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    time_str = str(check_time)
            else:
                time_str = str(check_time)
            time_item = QTableWidgetItem(time_str)
            time_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, time_item)

            # 状态（带颜色，适配暗色模式）
            status = item.get('status', 'unknown')
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignCenter)
            
            # 检测当前主题
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            theme_pref = cfg.get("theme", "auto")
            if theme_pref == "auto":
                current_theme = ThemeManager.detect_system_theme()
            else:
                current_theme = theme_pref
            
            # 根据主题设置背景色和文字颜色
            if current_theme == "dark":
                # 暗色模式
                if status == 'healthy':
                    status_item.setBackground(QColor(30, 80, 30))  # 深绿色
                    status_item.setForeground(QColor(100, 255, 100))  # 浅绿色文字
                elif status == 'rate_limited':
                    status_item.setBackground(QColor(80, 60, 20))  # 深橙色
                    status_item.setForeground(QColor(255, 200, 100))  # 浅橙色文字
                elif status == 'error':
                    status_item.setBackground(QColor(80, 30, 30))  # 深红色
                    status_item.setForeground(QColor(255, 150, 150))  # 浅红色文字
                else:
                    status_item.setBackground(QColor(50, 50, 50))  # 深灰色
                    status_item.setForeground(QColor(200, 200, 200))  # 浅灰色文字
            else:
                # 亮色模式
                if status == 'healthy':
                    status_item.setBackground(QColor(200, 255, 200))
                elif status == 'rate_limited':
                    status_item.setBackground(QColor(255, 200, 100))
                elif status == 'error':
                    status_item.setBackground(QColor(255, 200, 200))
                else:
                    status_item.setBackground(QColor(240, 240, 240))
            
            self.table.setItem(row, 2, status_item)

            # HTTP状态码
            http_status = item.get('http_status')
            http_item = QTableWidgetItem(str(http_status) if http_status is not None else 'N/A')
            http_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, http_item)

            # 响应时间
            response_time = item.get('response_time_ms')
            time_ms_item = QTableWidgetItem(str(response_time) if response_time is not None else 'N/A')
            time_ms_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 4, time_ms_item)

            # 模型
            model = item.get('model_used', 'N/A')
            model_item = QTableWidgetItem(str(model))
            model_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, model_item)

            # 已发送告警
            alert_sent = item.get('alert_sent', False)
            alert_item = QTableWidgetItem('是' if alert_sent else '否')
            alert_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 6, alert_item)

            # 操作按钮
            view_btn = QPushButton("查看")
            view_btn.setFixedSize(52, 22)
            view_btn.setStyleSheet("font-size: 9pt; padding: 0px;")
            # 使用 functools.partial 来正确传递 item，避免闭包问题
            view_btn.clicked.connect(partial(self._on_view_detail, item))
            self.table.setCellWidget(row, 7, view_btn)

    def _on_error(self, message: str):
        """错误处理"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()

        handle_api_error(self, message)

    def _on_view_detail(self, item: Dict[str, Any]):
        """查看详情"""
        dialog = _DetailDialog(item, self)
        dialog.exec()
    
    def _on_refresh_balance(self):
        """刷新OpenAI余额"""
        # 从配置中读取 session key
        cfg = ConfigManager.load()
        session_key = cfg.get("openai_session_key", "").strip()
        
        if not session_key:
            self.balance_label.setText("⚠️ 未配置 OpenAI Session Key\n请在 config.json 中设置 openai_session_key")
            # 暗色模式下使用更亮的橙色
            from utils.theme_manager import ThemeManager
            theme_pref = cfg.get("theme", "auto")
            if theme_pref == "auto":
                current_theme = ThemeManager.detect_system_theme()
            else:
                current_theme = theme_pref
            
            if current_theme == "dark":
                self.balance_label.setStyleSheet("color: #FFA500;")  # 亮橙色
            else:
                self.balance_label.setStyleSheet("color: orange;")
            self.balance_label.setToolTip("Session Key 可以从浏览器 Cookie 中获取（访问 platform.openai.com）")
            return
        
        self.balance_label.setText("加载中...")
        # 使用主题颜色，不设置固定颜色（让主题系统自动处理）
        # self.balance_label.setStyleSheet("color: gray;")
        
        # 在后台线程中获取余额
        worker = _OpenAIBalanceWorker(session_key)
        worker.signals.finished.connect(self._on_balance_loaded)
        worker.signals.error.connect(self._on_balance_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_balance_loaded(self, balance_info: Dict[str, Any]):
        """余额加载完成"""
        total_granted_usd = balance_info.get("total_granted_usd")
        total_used_usd = balance_info.get("total_used_usd")
        total_available_usd = balance_info.get("total_available_usd")
        usage_percentage = balance_info.get("usage_percentage", 0)
        
        if total_available_usd is not None:
            balance_text = f"可用余额: ${total_available_usd:.2f} USD"
            if total_granted_usd is not None:
                balance_text += f" | 总额度: ${total_granted_usd:.2f} USD"
            if total_used_usd is not None:
                balance_text += f" | 已使用: ${total_used_usd:.2f} USD"
            if usage_percentage > 0:
                balance_text += f" ({usage_percentage:.1f}%)"
            
            # 根据使用率设置颜色（适配暗色模式）
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            theme_pref = cfg.get("theme", "auto")
            if theme_pref == "auto":
                current_theme = ThemeManager.detect_system_theme()
            else:
                current_theme = theme_pref
            
            if current_theme == "dark":
                # 暗色模式使用更亮的颜色
                if usage_percentage >= 90:
                    self.balance_label.setStyleSheet("color: #FF6B6B; font-weight: bold;")  # 亮红色
                elif usage_percentage >= 70:
                    self.balance_label.setStyleSheet("color: #FFA500; font-weight: bold;")  # 亮橙色
                else:
                    self.balance_label.setStyleSheet("color: #4ECDC4; font-weight: bold;")  # 亮青色
            else:
                # 亮色模式
                if usage_percentage >= 90:
                    self.balance_label.setStyleSheet("color: red; font-weight: bold;")
                elif usage_percentage >= 70:
                    self.balance_label.setStyleSheet("color: orange; font-weight: bold;")
                else:
                    self.balance_label.setStyleSheet("color: green;")
            
            self.balance_label.setText(balance_text)
            
            # 添加工具提示显示详细信息
            tooltip = f"可用余额: ${total_available_usd:.2f} USD"
            if total_granted_usd is not None:
                tooltip += f"\n总额度: ${total_granted_usd:.2f} USD"
            if total_used_usd is not None:
                tooltip += f"\n已使用: ${total_used_usd:.2f} USD"
            if usage_percentage > 0:
                tooltip += f"\n使用率: {usage_percentage:.1f}%"
            self.balance_label.setToolTip(tooltip)
        else:
            self.balance_label.setText("无法获取余额信息")
            # 暗色模式下使用更亮的橙色
            from utils.theme_manager import ThemeManager
            cfg = ConfigManager.load()
            theme_pref = cfg.get("theme", "auto")
            if theme_pref == "auto":
                current_theme = ThemeManager.detect_system_theme()
            else:
                current_theme = theme_pref
            
            if current_theme == "dark":
                self.balance_label.setStyleSheet("color: #FFA500;")  # 亮橙色
            else:
                self.balance_label.setStyleSheet("color: orange;")
    
    def _on_balance_error(self, message: str):
        """余额获取失败"""
        self.balance_label.setText(f"获取失败: {message}")
        # 使用主题颜色，不设置固定颜色（让主题系统自动处理）
        # self.balance_label.setStyleSheet("color: red;")
        self.balance_label.setToolTip("请检查 session key 是否正确，或点击右侧按钮打开 Dashboard 查看")
    
    def _on_open_dashboard(self):
        """打开OpenAI Dashboard"""
        import webbrowser
        try:
            webbrowser.open("https://platform.openai.com/account/billing")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开浏览器: {str(e)}")

