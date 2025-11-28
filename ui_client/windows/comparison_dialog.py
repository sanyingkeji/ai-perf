#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comparison_dialog.py

对比分析对话框：
- 显示目标用户和当前用户的输入数据
- 调用AI进行对比分析
- 显示AI分析结果
"""

import json
from typing import Dict, Any, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTabWidget, QWidget, QFrame
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot

from utils.api_client import ApiClient, ApiError, AuthError
from widgets.toast import Toast


class _ComparisonWorkerSignals(QObject):
    finished = Signal(dict)  # ComparisonResponse
    error = Signal(str)


class _ComparisonWorker(QRunnable):
    """在后台线程中获取对比分析结果"""
    def __init__(self, target_user_id: str, date_str: str):
        super().__init__()
        self._target_user_id = target_user_id
        self._date_str = date_str
        self.signals = _ComparisonWorkerSignals()

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
            resp = client.get_comparison(self._target_user_id, self._date_str)
            if isinstance(resp, dict):
                self.signals.finished.emit(resp)
            else:
                self.signals.error.emit("API 返回格式错误")
        except (ApiError, AuthError) as e:
            self.signals.error.emit(str(e))
            return
        except Exception as e:
            self.signals.error.emit(f"获取对比分析失败：{e}")
            return


class _InputDataWorkerSignals(QObject):
    target_finished = Signal(dict)  # 目标用户数据
    current_finished = Signal(dict)  # 当前用户数据
    error = Signal(str)


class _InputDataWorker(QRunnable):
    """在后台线程中加载输入数据"""
    def __init__(self, target_user_id: str, current_user_id: Optional[str], date_str: str):
        super().__init__()
        self._target_user_id = target_user_id
        self._current_user_id = current_user_id
        self._date_str = date_str
        self.signals = _InputDataWorkerSignals()

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

        # 加载目标用户数据
        try:
            target_snapshot = client.get_daily_snapshot(self._date_str, self._target_user_id)
            if target_snapshot:
                self.signals.target_finished.emit(target_snapshot)
            else:
                self.signals.target_finished.emit({})
        except Exception as e:
            self.signals.error.emit(f"加载目标用户数据失败：{e}")
            return

        # 加载当前用户数据
        if self._current_user_id:
            try:
                current_snapshot = client.get_daily_snapshot(self._date_str, self._current_user_id)
                if current_snapshot:
                    self.signals.current_finished.emit(current_snapshot)
                else:
                    self.signals.current_finished.emit({})
            except Exception as e:
                # 当前用户数据加载失败不影响目标用户数据
                self.signals.current_finished.emit({})


class ComparisonDialog(QDialog):
    def __init__(self, parent: QWidget, target_user_id: str, target_user_name: str, date_str: str):
        super().__init__(parent)
        self._target_user_id = target_user_id
        self._target_user_name = target_user_name
        self._date_str = date_str
        
        self.setWindowTitle(f"向 {target_user_name} 学习 - {date_str}")
        self.resize(1000, 700)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # 标题
        title = QLabel(f"📊 对比分析：向 {target_user_name} 学习")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)
        
        # 使用Tab显示不同内容
        self.tabs = QTabWidget()
        
        # Tab 1: AI分析结果
        self.analysis_tab = QWidget()
        analysis_layout = QVBoxLayout(self.analysis_tab)
        analysis_layout.setContentsMargins(12, 12, 12, 12)
        
        self.analysis_text = QTextEdit()
        self.analysis_text.setReadOnly(True)
        self.analysis_text.setFont(QFont("Arial", 10))
        self.analysis_text.setPlainText("正在加载对比分析，请稍候…")
        analysis_layout.addWidget(self.analysis_text)
        
        self.tabs.addTab(self.analysis_tab, "AI分析结果")
        
        # Tab 2: 目标用户输入数据
        self.target_tab = QWidget()
        target_layout = QVBoxLayout(self.target_tab)
        target_layout.setContentsMargins(12, 12, 12, 12)
        
        target_label = QLabel(f"{target_user_name} 的输入数据：")
        target_label.setFont(QFont("Arial", 11, QFont.Bold))
        target_layout.addWidget(target_label)
        
        self.target_data_text = QTextEdit()
        self.target_data_text.setReadOnly(True)
        self.target_data_text.setFont(QFont("Courier New", 9))
        self.target_data_text.setPlainText("加载中…")
        target_layout.addWidget(self.target_data_text)
        
        self.tabs.addTab(self.target_tab, f"{target_user_name} 的数据")
        
        # Tab 3: 我的输入数据
        self.my_tab = QWidget()
        my_layout = QVBoxLayout(self.my_tab)
        my_layout.setContentsMargins(12, 12, 12, 12)
        
        my_label = QLabel("我的输入数据：")
        my_label.setFont(QFont("Arial", 11, QFont.Bold))
        my_layout.addWidget(my_label)
        
        self.my_data_text = QTextEdit()
        self.my_data_text.setReadOnly(True)
        self.my_data_text.setFont(QFont("Courier New", 9))
        self.my_data_text.setPlainText("加载中…")
        my_layout.addWidget(self.my_data_text)
        
        self.tabs.addTab(self.my_tab, "我的数据")
        
        layout.addWidget(self.tabs)
        
        # 按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        # 开始加载对比分析
        self._load_comparison()
    
    def _load_comparison(self):
        """加载对比分析"""
        worker = _ComparisonWorker(self._target_user_id, self._date_str)
        worker.signals.finished.connect(self._on_load_finished)
        worker.signals.error.connect(self._on_load_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_load_finished(self, resp: Dict[str, Any]):
        """对比分析加载完成"""
        if resp.get("status") != "success":
            error_msg = resp.get("message") or "加载失败"
            self.analysis_text.setPlainText(f"加载失败：{error_msg}")
            return
        
        analysis_result = resp.get("analysis_result")
        if not analysis_result:
            self.analysis_text.setPlainText("暂无分析结果")
            return
        
        # 格式化显示AI分析结果
        analysis_text = self._format_analysis_result(analysis_result)
        self.analysis_text.setPlainText(analysis_text)
        
        # 加载输入数据（从API响应中获取，或单独请求）
        # 使用后台线程加载输入数据，避免阻塞UI
        current_user_id = resp.get("current_user_id")
        self._load_input_data(self._target_user_id, current_user_id)
    
    def _load_input_data(self, target_user_id: str, current_user_id: Optional[str]):
        """在后台线程中加载输入数据"""
        worker = _InputDataWorker(target_user_id, current_user_id, self._date_str)
        worker.signals.target_finished.connect(self._on_target_data_loaded)
        worker.signals.current_finished.connect(self._on_current_data_loaded)
        worker.signals.error.connect(self._on_input_data_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_target_data_loaded(self, data: Dict[str, Any]):
        """目标用户数据加载完成"""
        if not data:
            self.target_data_text.setPlainText("（暂无输入数据）")
            return
        try:
            target_json = json.dumps(data, ensure_ascii=False, indent=2)
            self.target_data_text.setPlainText(target_json)
        except Exception as e:
            self.target_data_text.setPlainText(f"（格式化数据失败：{e}）")
    
    def _on_current_data_loaded(self, data: Dict[str, Any]):
        """当前用户数据加载完成"""
        if not data:
            self.my_data_text.setPlainText("（暂无输入数据）")
            return
        try:
            current_json = json.dumps(data, ensure_ascii=False, indent=2)
            self.my_data_text.setPlainText(current_json)
        except Exception as e:
            self.my_data_text.setPlainText(f"（格式化数据失败：{e}）")
    
    def _on_input_data_error(self, message: str):
        """输入数据加载失败"""
        self.target_data_text.setPlainText(f"（加载失败：{message}）")
        self.my_data_text.setPlainText(f"（加载失败：{message}）")
    
    def _format_analysis_result(self, result: Dict[str, Any]) -> str:
        """格式化AI分析结果为可读文本"""
        lines = []
        
        # 总结
        summary = result.get("summary", "")
        if summary:
            lines.append("=" * 60)
            lines.append("📝 总结")
            lines.append("=" * 60)
            lines.append(summary)
            lines.append("")
        
        # 关键差异
        key_differences = result.get("key_differences", [])
        if key_differences:
            lines.append("=" * 60)
            lines.append("🔍 关键差异")
            lines.append("=" * 60)
            for i, diff in enumerate(key_differences, 1):
                dimension = diff.get("dimension", "未知维度")
                description = diff.get("description", "")
                target_data = diff.get("target_user_data", "")
                current_data = diff.get("current_user_data", "")
                learning_point = diff.get("learning_point", "")
                
                lines.append(f"\n【{i}】{dimension}")
                lines.append(f"  差异描述：{description}")
                lines.append(f"  优秀员工：{target_data}")
                lines.append(f"  我的情况：{current_data}")
                lines.append(f"  学习要点：{learning_point}")
            lines.append("")
        
        # 最佳实践
        best_practices = result.get("best_practices", [])
        if best_practices:
            lines.append("=" * 60)
            lines.append("⭐ 最佳实践")
            lines.append("=" * 60)
            for i, practice in enumerate(best_practices, 1):
                lines.append(f"{i}. {practice}")
            lines.append("")
        
        # 改进建议
        recommendations = result.get("actionable_recommendations", [])
        if recommendations:
            lines.append("=" * 60)
            lines.append("💡 改进建议")
            lines.append("=" * 60)
            for rec in recommendations:
                priority = rec.get("priority", "中")
                recommendation = rec.get("recommendation", "")
                expected_impact = rec.get("expected_impact", "")
                
                priority_icon = "🔴" if priority == "高" else "🟡" if priority == "中" else "🟢"
                lines.append(f"\n{priority_icon} 【{priority}优先级】{recommendation}")
                if expected_impact:
                    lines.append(f"   预期效果：{expected_impact}")
            lines.append("")
        
        # 数据质量说明
        data_quality_note = result.get("data_quality_note", "")
        if data_quality_note:
            lines.append("=" * 60)
            lines.append("ℹ️ 数据质量说明")
            lines.append("=" * 60)
            lines.append(data_quality_note)
        
        return "\n".join(lines)
    
    def _on_load_error(self, message: str):
        """对比分析加载失败"""
        self.analysis_text.setPlainText(f"加载失败：{message}")
        
        # 登录相关错误：弹出登录对话框
        if any(key in message for key in ("需要先登录", "会话已过期", "无效会话令牌")):
            win = self.window()
            show_login = getattr(win, "show_login_required_dialog", None)
            if callable(show_login):
                # 检查是否已经有登录弹窗在显示（避免重复弹窗）
                if not getattr(win, "_login_dialog_shown", False):
                    if show_login():
                        self._load_comparison()

