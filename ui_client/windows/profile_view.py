#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_view.py

员工端"我的"页面，显示用户信息
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea, QApplication
)
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal
from PySide6.QtGui import QFont
from utils.api_client import ApiClient
from widgets.toast import Toast


class _UserInfoWorkerSignals(QObject):
    finished = Signal(dict)  # 成功：返回用户信息
    error = Signal(str)  # 失败：返回错误信息


class _UserInfoWorker(QRunnable):
    """后台获取用户信息"""
    
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.signals = _UserInfoWorkerSignals()
        self._api_client = api_client
    
    def run(self):
        try:
            response = self._api_client._get("/api/user_info")
            # _get() 返回的是字典，不是响应对象
            if isinstance(response, dict) and response.get("status") == "success":
                self.signals.finished.emit(response.get("data", {}))
            else:
                self.signals.error.emit(response.get("message", "获取用户信息失败"))
        except Exception as e:
            self.signals.error.emit(f"获取用户信息异常：{e}")


class _AccountBindingsWorkerSignals(QObject):
    finished = Signal(list)  # 成功：返回账号绑定列表
    error = Signal(str)  # 失败：返回错误信息


class _AccountBindingsWorker(QRunnable):
    """后台获取账号绑定信息"""
    
    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.signals = _AccountBindingsWorkerSignals()
        self._api_client = api_client
    
    def run(self):
        try:
            response = self._api_client._get("/api/account_bindings")
            # _get() 返回的是字典，格式：{"status": "success", "items": [...], "message": null}
            # 或者如果出错，_handle_response 会抛出 ApiError 异常
            if isinstance(response, dict) and response.get("status") == "success":
                items = response.get("items", [])
                # 确保 items 是列表
                if not isinstance(items, list):
                    items = []
                self.signals.finished.emit(items)
            else:
                # 这种情况理论上不会发生（因为 _handle_response 会在 status="error" 时抛出异常）
                # 但为了安全起见，还是处理一下
                error_msg = response.get("message", "获取账号绑定失败") if isinstance(response, dict) else "响应格式错误"
                self.signals.error.emit(error_msg)
        except Exception as e:
            error_msg = str(e)
            self.signals.error.emit(f"获取账号绑定异常：{error_msg}")


class ProfileView(QWidget):
    """我的页面"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._api_client = None
        self._init_ui()
    
    def _init_ui(self):
        # 主布局（无边距，用于放置滚动区域）
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建滚动区域
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建内容widget
        self._content_widget = QWidget()
        layout = QVBoxLayout(self._content_widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        
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
        
        # 标题
        title = QLabel("我的")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        # 允许选择和复制文本
        title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(title)
        
        # 信息容器
        info_frame = QFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(12)
        info_layout.setContentsMargins(16, 16, 16, 16)
        
        # 信息项
        self.user_id_label = QLabel("用户ID：--")
        self.name_label = QLabel("姓名：--")
        self.email_label = QLabel("邮箱：--")
        self.team_label = QLabel("团队：--")
        self.role_label = QLabel("角色：--")
        self.subrole_label = QLabel("子角色：--")
        self.level_label = QLabel("职级：--")
        self.salary_band_label = QLabel("薪级：--")
        self.active_label = QLabel("状态：--")
        self.join_date_label = QLabel("入职日期：--")
        self.days_in_company_label = QLabel("已加入公司：--")
        self.expected_ai_label = QLabel("岗位基准期望分：--")
        
        # 设置字体和文本交互（允许复制）
        for label in [
            self.user_id_label, self.name_label, self.email_label,
            self.team_label, self.role_label, self.subrole_label,
            self.level_label, self.salary_band_label, self.active_label,
            self.join_date_label, self.days_in_company_label, self.expected_ai_label
        ]:
            label.setFont(QFont("Arial", 11))
            # 允许选择和复制文本
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            info_layout.addWidget(label)
        
        # 岗位职责（显示在岗位基准期望分下面）
        self.responsibilities_label = QLabel("岗位职责：--")
        self.responsibilities_label.setFont(QFont("Arial", 11))
        self.responsibilities_label.setWordWrap(True)
        self.responsibilities_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.responsibilities_label.setAlignment(Qt.AlignTop)
        info_layout.addWidget(self.responsibilities_label)
        
        layout.addWidget(info_frame)
        
        # 账号绑定容器
        bindings_frame = QFrame()
        bindings_layout = QVBoxLayout(bindings_frame)
        bindings_layout.setSpacing(12)
        bindings_layout.setContentsMargins(16, 16, 16, 16)
        
        # 账号绑定标题
        bindings_title = QLabel("账号绑定")
        bindings_title_font = QFont()
        bindings_title_font.setPointSize(12)
        bindings_title_font.setBold(True)
        bindings_title.setFont(bindings_title_font)
        # 允许选择和复制文本
        bindings_title.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        bindings_layout.addWidget(bindings_title)
        
        # 账号绑定列表容器（使用滚动区域）
        self.bindings_container = QWidget()
        self.bindings_container_layout = QVBoxLayout(self.bindings_container)
        self.bindings_container_layout.setSpacing(8)
        self.bindings_container_layout.setContentsMargins(0, 0, 0, 0)
        self.bindings_container_layout.addStretch()
        
        scroll_area = QScrollArea()
        scroll_area.setWidget(self.bindings_container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setMaximumHeight(200)
        scroll_area.setFrameShape(QFrame.NoFrame)
        bindings_layout.addWidget(scroll_area)
        
        # 默认显示"暂无账号绑定"
        self.bindings_placeholder = QLabel("暂无账号绑定")
        self.bindings_placeholder.setFont(QFont("Arial", 10))
        self.bindings_placeholder.setStyleSheet("color: #888;")
        # 允许选择和复制文本
        self.bindings_placeholder.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.bindings_container_layout.insertWidget(0, self.bindings_placeholder)
        
        layout.addWidget(bindings_frame)
        
        # 刷新按钮（放在左边）
        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.clicked.connect(self.reload_from_api)
        btn_layout.addWidget(self.refresh_btn)
        btn_layout.addStretch()  # 按钮在左边，右边留空
        layout.addLayout(btn_layout)
        
        layout.addStretch()
    
    def reload_from_api(self):
        """从 API 重新加载用户信息"""
        try:
            self._api_client = ApiClient.from_config()
        except Exception:
            Toast.show_message(self, "请先登录")
            return
        
        if not self._api_client.is_logged_in():
            Toast.show_message(self, "请先登录")
            return
        
        # 显示加载状态
        self._set_loading_state()
        
        # 在后台线程中获取用户信息
        worker = _UserInfoWorker(self._api_client)
        worker.signals.finished.connect(self._on_user_info_loaded)
        worker.signals.error.connect(self._on_user_info_error)
        QThreadPool.globalInstance().start(worker)
        
        # 在后台线程中获取账号绑定信息
        bindings_worker = _AccountBindingsWorker(self._api_client)
        bindings_worker.signals.finished.connect(self._on_account_bindings_loaded)
        bindings_worker.signals.error.connect(self._on_account_bindings_error)
        QThreadPool.globalInstance().start(bindings_worker)
    
    def _set_loading_state(self):
        """设置加载状态"""
        self.user_id_label.setText("用户ID：加载中...")
        self.name_label.setText("姓名：加载中...")
        self.email_label.setText("邮箱：加载中...")
        self.team_label.setText("团队：加载中...")
        self.role_label.setText("角色：加载中...")
        self.subrole_label.setText("子角色：加载中...")
        self.level_label.setText("职级：加载中...")
        self.salary_band_label.setText("薪级：加载中...")
        self.active_label.setText("状态：加载中...")
        self.join_date_label.setText("入职日期：加载中...")
        self.days_in_company_label.setText("已加入公司：加载中...")
        self.expected_ai_label.setText("岗位基准期望分：加载中...")
        self.responsibilities_label.setText("岗位职责：加载中...")
        self.refresh_btn.setEnabled(False)
    
    def _on_user_info_loaded(self, data: dict):
        """用户信息加载成功"""
        self.refresh_btn.setEnabled(True)
        
        # 更新标签
        self.user_id_label.setText(f"用户ID：{data.get('user_id', '--')}")
        
        # 姓名（如果是组长，在姓名后显示"xxx 组组长"）
        name = data.get('name', '--')
        is_team_leader = data.get('is_team_leader', False)
        team_name = data.get('team_name')
        if is_team_leader and team_name:
            name_display = f"{name}（{team_name}组组长）"
        else:
            name_display = name
        self.name_label.setText(f"姓名：{name_display}")
        
        self.email_label.setText(f"邮箱：{data.get('email', '--')}")
        
        # 团队（显示名称，如果有）
        team_name_display = team_name or f"ID: {data.get('team_id', '--')}"
        self.team_label.setText(f"团队：{team_name_display}")
        
        # 角色（显示名称，如果有）
        role_name = data.get('role_name') or f"ID: {data.get('role_id', '--')}"
        self.role_label.setText(f"角色：{role_name}")
        
        # 子角色（显示名称，如果有）
        subrole_id = data.get('subrole_id')
        if subrole_id:
            subrole_name = data.get('subrole_name') or f"ID: {subrole_id}"
            self.subrole_label.setText(f"子角色：{subrole_name}")
        else:
            self.subrole_label.setText("子角色：--")
        
        # 职级（显示名称，如果有）
        level_name = data.get('level_name') or f"ID: {data.get('level_id', '--')}"
        self.level_label.setText(f"职级：{level_name}")
        
        # 薪级
        self.salary_band_label.setText(f"薪级：{data.get('salary_band', '--')}")
        
        # 状态
        active = data.get('active', 0)
        if active == 1:
            self.active_label.setText("状态：在职")
        elif active == 0:
            self.active_label.setText("状态：离职")
        else:
            self.active_label.setText(f"状态：{active}")
        
        # 入职日期
        join_date = data.get('join_date')
        self.join_date_label.setText(f"入职日期：{join_date if join_date else '--'}")
        
        # 已加入公司天数
        from datetime import date
        if join_date:
            try:
                join_date_obj = date.fromisoformat(join_date)
                today = date.today()
                days = (today - join_date_obj).days
                self.days_in_company_label.setText(f"已加入公司：{days} 天")
            except Exception:
                self.days_in_company_label.setText("已加入公司：--")
        else:
            self.days_in_company_label.setText("已加入公司：--")
        
        # 岗位基准期望分
        expected_ai = data.get('expected_ai')
        if expected_ai is not None:
            self.expected_ai_label.setText(f"岗位基准期望分：{expected_ai:.2f}")
        else:
            self.expected_ai_label.setText("岗位基准期望分：--")
        
        # 岗位职责
        responsibilities = data.get('responsibilities')
        if responsibilities and isinstance(responsibilities, list) and len(responsibilities) > 0:
            # 将列表转换为带编号的文本，每行缩进2em（2个中文字符宽度）
            responsibilities_text = "\n".join([f"　　{i+1}. {item}" for i, item in enumerate(responsibilities) if item])
            self.responsibilities_label.setText(f"岗位职责：\n{responsibilities_text}")
        else:
            self.responsibilities_label.setText("岗位职责：--")
    
    def _on_user_info_error(self, error_msg: str):
        """用户信息加载失败"""
        self.refresh_btn.setEnabled(True)
        Toast.show_message(self, f"获取用户信息失败：{error_msg}")
        
        # 恢复为默认状态
        self.user_id_label.setText("用户ID：--")
        self.name_label.setText("姓名：--")
        self.email_label.setText("邮箱：--")
        self.team_label.setText("团队：--")
        self.role_label.setText("角色：--")
        self.subrole_label.setText("子角色：--")
        self.level_label.setText("职级：--")
        self.salary_band_label.setText("薪级：--")
        self.active_label.setText("状态：--")
        self.join_date_label.setText("入职日期：--")
        self.days_in_company_label.setText("已加入公司：--")
        self.expected_ai_label.setText("岗位基准期望分：--")
        self.responsibilities_label.setText("岗位职责：--")
    
    def _on_account_bindings_loaded(self, items: list):
        """账号绑定加载成功"""
        # 清除现有绑定显示（包括占位符）
        while self.bindings_container_layout.count() > 1:  # 保留最后的stretch
            item = self.bindings_container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # 重置占位符引用（因为之前的已被删除）
        self.bindings_placeholder = None
        
        if not items or len(items) == 0:
            # 没有绑定，显示占位符
            self.bindings_placeholder = QLabel("暂无账号绑定")
            self.bindings_placeholder.setFont(QFont("Arial", 10))
            self.bindings_placeholder.setStyleSheet("color: #888;")
            self.bindings_container_layout.insertWidget(0, self.bindings_placeholder)
            return
        
        # 显示绑定列表
        for binding_item in items:
            if not isinstance(binding_item, dict):
                continue
                
            platform = binding_item.get('platform', '').lower()
            external_id = binding_item.get('external_id', '')
            extra_json = binding_item.get('extra_json')
            alias_github_author = binding_item.get('alias_github_author')
            
            # 构建显示文本
            platform_display = platform.upper() if platform else '未知平台'
            
            # 根据平台类型构建显示文本
            display_parts = [platform_display]
            
            # 添加平台特定的显示名称
            if extra_json and isinstance(extra_json, dict):
                if platform == 'jira':
                    # Jira：显示 displayName
                    display_name = extra_json.get('displayName')
                    if display_name:
                        display_parts.append(f"{display_name} ({external_id})")
                    else:
                        display_parts.append(external_id)
                elif platform == 'github':
                    # GitHub：显示 login
                    login = extra_json.get('login')
                    if login:
                        display_parts.append(f"{login} ({external_id})")
                    else:
                        display_parts.append(external_id)
                else:
                    # 其他平台：只显示 external_id
                    display_parts.append(external_id)
            else:
                # 没有 extra_json，只显示 external_id
                display_parts.append(external_id)
            
            # 如果是GitHub且有别名，添加别名信息
            if platform == 'github' and alias_github_author:
                if isinstance(alias_github_author, list) and alias_github_author:
                    aliases = ', '.join(str(a) for a in alias_github_author if a)
                    if aliases:
                        display_parts.append(f"（别名：{aliases}）")
                elif isinstance(alias_github_author, str) and alias_github_author:
                    display_parts.append(f"（别名：{alias_github_author}）")
            
            display_text = "：".join(display_parts)
            
            # 创建绑定项标签
            binding_label = QLabel(display_text)
            binding_label.setFont(QFont("Arial", 10))
            binding_label.setWordWrap(True)
            # 允许选择和复制文本
            binding_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            self.bindings_container_layout.insertWidget(
                self.bindings_container_layout.count() - 1,  # 插入到stretch之前
                binding_label
            )
    
    def _on_account_bindings_error(self, error_msg: str):
        """账号绑定加载失败"""
        # 清除现有显示
        while self.bindings_container_layout.count() > 1:  # 保留最后的stretch
            item = self.bindings_container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # 显示占位符（不显示错误，避免干扰用户）
        self.bindings_placeholder = QLabel("暂无账号绑定")
        self.bindings_placeholder.setFont(QFont("Arial", 10))
        self.bindings_placeholder.setStyleSheet("color: #888;")
        # 允许选择和复制文本
        self.bindings_placeholder.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.bindings_container_layout.insertWidget(0, self.bindings_placeholder)

