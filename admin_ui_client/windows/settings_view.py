#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设置页面：
- 显示登录状态
- Google OAuth 登录/退出登录
- 主题切换（单选框组）
- API服务器地址配置
- 显示会话Token和Google ID Token
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QMessageBox, QTabWidget,
    QSpinBox, QFileDialog, QButtonGroup
)
from PySide6.QtGui import QFont
from PySide6.QtCore import QTimer, QRunnable, QThreadPool, QObject, Signal, Slot, Qt
from typing import Dict, Any
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import shutil
import zipfile
import sys

from utils.config_manager import ConfigManager, CONFIG_PATH
from utils.google_login import login_and_get_id_token, GoogleLoginError
from utils.theme_manager import ThemeManager
from utils.api_client import AdminApiClient, ApiError, AuthError
from widgets.toast import Toast
from windows.update_dialog import UpdateDialog


class SettingsView(QWidget):
    def __init__(self):
        super().__init__()
        
        self.cfg = ConfigManager.load()
        # 标志：是否正在初始化（用于防止初始化时触发自动保存）
        self._is_initializing = True
        # 标记：是否已经显示过升级弹窗（防止重复弹窗）
        self._update_dialog_shown = False
        # 标记：是否正在登录中（防止重复点击登录按钮）
        self._login_in_progress = False
        
        # 从配置文件读取版本号
        self._client_version = self.cfg.get("client_version", "1.0.0")
        
        self._setup_ui()
        self._refresh_login_buttons()
        self._is_initializing = False
        
        # 定时检查版本升级（每3分钟）
        self._api_health_timer = QTimer()
        self._api_health_timer.timeout.connect(self._load_api_health)
        self._api_health_timer.setInterval(3 * 60 * 1000)  # 3分钟 = 180000毫秒
        
        # 立即加载一次，然后启动定时器
        self._load_api_health()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        
        # 标题
        title = QLabel("系统设置")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Tab 切换
        tab_widget = QTabWidget()
        
        # Tab 1: 后端登录配置
        backend_tab = QWidget()
        backend_layout = QVBoxLayout(backend_tab)
        backend_layout.setContentsMargins(16, 16, 16, 16)
        backend_layout.setSpacing(12)
        
        # --- API & 登录 ---
        api_frame = QFrame()
        api_layout = QVBoxLayout(api_frame)
        api_layout.setSpacing(8)
        
        api_title = QLabel("后端与登录配置")
        api_title_font = QFont()
        api_title_font.setPointSize(12)
        api_title_font.setBold(True)
        api_title.setFont(api_title_font)
        api_layout.addWidget(api_title)
        
        # API 地址
        api_row = QHBoxLayout()
        api_label = QLabel("后端 API 地址：")
        self.api_edit = QLineEdit()
        self.api_edit.setPlaceholderText("例如：http://127.0.0.1:8880")
        self.api_edit.setText(self.cfg.get("api_base", "http://127.0.0.1:8880"))
        # API 地址变更时自动保存（延迟500ms，避免频繁保存）
        self._api_save_timer = QTimer()
        self._api_save_timer.setSingleShot(True)
        self._api_save_timer.timeout.connect(self._auto_save_api_base)
        self.api_edit.textChanged.connect(lambda: self._api_save_timer.start(500))
        # 失去焦点或按回车时立即保存并刷新状态
        self.api_edit.editingFinished.connect(self._on_api_base_changed)
        self.api_edit.returnPressed.connect(self._on_api_base_changed)
        
        api_row.addWidget(api_label)
        api_row.addWidget(self.api_edit, 1)  # 1 表示拉伸因子，让输入框铺满剩余空间
        api_layout.addLayout(api_row)
        
        # 上传 API 地址
        upload_api_row = QHBoxLayout()
        upload_api_label = QLabel("文件上传 API 地址：")
        self.upload_api_edit = QLineEdit()
        self.upload_api_edit.setPlaceholderText("例如：https://upload.example.com/api/upload")
        self.upload_api_edit.setText(self.cfg.get("upload_api_url", "http://127.0.0.1:8882/api/upload"))
        # 上传API地址变更时自动保存（延迟500ms，避免频繁保存）
        self._upload_api_save_timer = QTimer()
        self._upload_api_save_timer.setSingleShot(True)
        self._upload_api_save_timer.timeout.connect(self._auto_save_upload_api_url)
        self.upload_api_edit.textChanged.connect(lambda: self._upload_api_save_timer.start(500))
        # 失去焦点或按回车时立即保存
        self.upload_api_edit.editingFinished.connect(self._on_upload_api_url_changed)
        self.upload_api_edit.returnPressed.connect(self._on_upload_api_url_changed)
        
        upload_api_row.addWidget(upload_api_label)
        upload_api_row.addWidget(self.upload_api_edit, 1)
        api_layout.addLayout(upload_api_row)
        
        # Google ID Token
        token_row = QHBoxLayout()
        token_label = QLabel("Google ID Token：")
        self.token_edit = QLineEdit()
        self.token_edit.setReadOnly(True)
        self.token_edit.setPlaceholderText("点击登录按钮，通过 Google 登录自动获取")
        self.token_edit.setText(self.cfg.get("google_id_token", ""))
        
        token_row.addWidget(token_label)
        token_row.addWidget(self.token_edit, 1)
        api_layout.addLayout(token_row)
        
        # 会话 Token
        session_row = QHBoxLayout()
        session_label = QLabel("会话 Token：")
        self.session_edit = QLineEdit()
        self.session_edit.setReadOnly(True)
        self.session_edit.setPlaceholderText("登录后自动获取")
        self.session_edit.setText(self.cfg.get("session_token", ""))
        
        session_row.addWidget(session_label)
        session_row.addWidget(self.session_edit, 1)
        api_layout.addLayout(session_row)
        
        # 当前登录邮箱
        email_row = QHBoxLayout()
        email_label = QLabel("当前登录邮箱：")
        self.email_value = QLabel(self.cfg.get("user_email", "") or "（未登录）")
        email_row.addWidget(email_label)
        email_row.addWidget(self.email_value)
        email_row.addStretch()
        api_layout.addLayout(email_row)
        
        # 登录 / 退出登录 按钮
        btn_row = QHBoxLayout()
        self.btn_google_login = QPushButton("谷歌授权登录")
        self.btn_google_login.clicked.connect(self.on_google_login_clicked)
        
        self.btn_google_logout = QPushButton("退出登录")
        self.btn_google_logout.setStyleSheet(
            "QPushButton { background-color: #d9534f; color: white; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #c9302c; }"
        )
        self.btn_google_logout.clicked.connect(self.on_google_logout_clicked)
        
        # 按钮统一宽度
        self.btn_google_login.setFixedWidth(120)
        self.btn_google_logout.setFixedWidth(120)
        
        btn_row.addWidget(self.btn_google_login)
        btn_row.addWidget(self.btn_google_logout)
        btn_row.addStretch()
        api_layout.addLayout(btn_row)
        
        backend_layout.addWidget(api_frame)
        
        # 后端API服务状态
        health_frame = QFrame()
        health_layout = QVBoxLayout(health_frame)
        health_layout.setContentsMargins(0, 0, 0, 0)
        health_layout.setSpacing(4)
        
        health_label = QLabel("后端API服务状态")
        health_label_font = QFont()
        health_label_font.setPointSize(12)
        health_label_font.setBold(True)
        health_label.setFont(health_label_font)
        health_layout.addWidget(health_label)
        
        self.health_status_label = QLabel("状态：检查中...")
        self.health_status_label.setFont(QFont("Arial", 9))
        health_layout.addWidget(self.health_status_label)
        
        self.health_time_label = QLabel("检查时间：--")
        self.health_time_label.setFont(QFont("Arial", 9))
        health_layout.addWidget(self.health_time_label)
        
        refresh_health_btn = QPushButton("刷新状态")
        refresh_health_btn.setFixedWidth(120)
        refresh_health_btn.clicked.connect(self._load_api_health)
        health_layout.addWidget(refresh_health_btn)
        
        backend_layout.addWidget(health_frame)
        backend_layout.addStretch()
        
        tab_widget.addTab(backend_tab, "后端登录配置")
        
        # Tab 2: 第三方平台配置
        third_party_tab = QWidget()
        third_party_layout = QVBoxLayout(third_party_tab)
        third_party_layout.setContentsMargins(16, 16, 16, 16)
        third_party_layout.setSpacing(12)
        
        third_party_title = QLabel("第三方平台 API 配置")
        third_party_title_font = QFont()
        third_party_title_font.setPointSize(12)
        third_party_title_font.setBold(True)
        third_party_title.setFont(third_party_title_font)
        third_party_layout.addWidget(third_party_title)
        
        # GitHub 配置
        github_row = QHBoxLayout()
        github_label = QLabel("GitHub API 地址：")
        self.github_api_edit = QLineEdit()
        self.github_api_edit.setPlaceholderText("例如：https://api.github.com")
        self.github_api_edit.setText(self.cfg.get("github_api_url", "https://api.github.com"))
        self._github_api_save_timer = QTimer()
        self._github_api_save_timer.setSingleShot(True)
        self._github_api_save_timer.timeout.connect(self._auto_save_github_api_url)
        self.github_api_edit.textChanged.connect(lambda: self._github_api_save_timer.start(500))
        self.github_api_edit.editingFinished.connect(self._on_github_api_url_changed)
        self.github_api_edit.returnPressed.connect(self._on_github_api_url_changed)
        github_row.addWidget(github_label)
        github_row.addWidget(self.github_api_edit, 1)
        third_party_layout.addLayout(github_row)
        
        github_key_row = QHBoxLayout()
        github_key_label = QLabel("GitHub API Key：")
        self.github_api_key_edit = QLineEdit()
        self.github_api_key_edit.setPlaceholderText("可选，用于提高API调用限制")
        self.github_api_key_edit.setEchoMode(QLineEdit.Password)  # 密码模式
        self.github_api_key_edit.setText(self.cfg.get("github_api_key", ""))
        self._github_api_key_save_timer = QTimer()
        self._github_api_key_save_timer.setSingleShot(True)
        self._github_api_key_save_timer.timeout.connect(self._auto_save_github_api_key)
        self.github_api_key_edit.textChanged.connect(lambda: self._github_api_key_save_timer.start(500))
        self.github_api_key_edit.editingFinished.connect(self._on_github_api_key_changed)
        self.github_api_key_edit.returnPressed.connect(self._on_github_api_key_changed)
        github_key_row.addWidget(github_key_label)
        github_key_row.addWidget(self.github_api_key_edit, 1)
        third_party_layout.addLayout(github_key_row)
        
        github_org_row = QHBoxLayout()
        github_org_label = QLabel("GitHub 组织（可选）：")
        self.github_org_edit = QLineEdit()
        self.github_org_edit.setPlaceholderText("例如：your-org-name（用于在组织成员中查找用户）")
        self.github_org_edit.setText(self.cfg.get("github_org", ""))
        self._github_org_save_timer = QTimer()
        self._github_org_save_timer.setSingleShot(True)
        self._github_org_save_timer.timeout.connect(self._auto_save_github_org)
        self.github_org_edit.textChanged.connect(lambda: self._github_org_save_timer.start(500))
        self.github_org_edit.editingFinished.connect(self._on_github_org_changed)
        self.github_org_edit.returnPressed.connect(self._on_github_org_changed)
        github_org_row.addWidget(github_org_label)
        github_org_row.addWidget(self.github_org_edit, 1)
        third_party_layout.addLayout(github_org_row)
        
        # Jira 配置（与后端 .env 保持一致）
        jira_base_row = QHBoxLayout()
        jira_base_label = QLabel("Jira 基础地址：")
        self.jira_base_edit = QLineEdit()
        self.jira_base_edit.setPlaceholderText("例如：https://your-domain.atlassian.net")
        self.jira_base_edit.setText(self.cfg.get("jira_base", ""))
        self._jira_base_save_timer = QTimer()
        self._jira_base_save_timer.setSingleShot(True)
        self._jira_base_save_timer.timeout.connect(self._auto_save_jira_base)
        self.jira_base_edit.textChanged.connect(lambda: self._jira_base_save_timer.start(500))
        self.jira_base_edit.editingFinished.connect(self._on_jira_base_changed)
        self.jira_base_edit.returnPressed.connect(self._on_jira_base_changed)
        jira_base_row.addWidget(jira_base_label)
        jira_base_row.addWidget(self.jira_base_edit, 1)
        third_party_layout.addLayout(jira_base_row)
        
        jira_email_row = QHBoxLayout()
        jira_email_label = QLabel("Jira 账号邮箱：")
        self.jira_account_email_edit = QLineEdit()
        self.jira_account_email_edit.setPlaceholderText("例如：your-email@example.com")
        self.jira_account_email_edit.setText(self.cfg.get("jira_account_email", ""))
        self._jira_account_email_save_timer = QTimer()
        self._jira_account_email_save_timer.setSingleShot(True)
        self._jira_account_email_save_timer.timeout.connect(self._auto_save_jira_account_email)
        self.jira_account_email_edit.textChanged.connect(lambda: self._jira_account_email_save_timer.start(500))
        self.jira_account_email_edit.editingFinished.connect(self._on_jira_account_email_changed)
        self.jira_account_email_edit.returnPressed.connect(self._on_jira_account_email_changed)
        jira_email_row.addWidget(jira_email_label)
        jira_email_row.addWidget(self.jira_account_email_edit, 1)
        third_party_layout.addLayout(jira_email_row)
        
        jira_token_row = QHBoxLayout()
        jira_token_label = QLabel("Jira API Token：")
        self.jira_token_edit = QLineEdit()
        self.jira_token_edit.setPlaceholderText("Jira API Token（从 Jira 设置中生成）")
        self.jira_token_edit.setEchoMode(QLineEdit.Password)  # 密码模式
        self.jira_token_edit.setText(self.cfg.get("jira_token", ""))
        self._jira_token_save_timer = QTimer()
        self._jira_token_save_timer.setSingleShot(True)
        self._jira_token_save_timer.timeout.connect(self._auto_save_jira_token)
        self.jira_token_edit.textChanged.connect(lambda: self._jira_token_save_timer.start(500))
        self.jira_token_edit.editingFinished.connect(self._on_jira_token_changed)
        self.jira_token_edit.returnPressed.connect(self._on_jira_token_changed)
        jira_token_row.addWidget(jira_token_label)
        jira_token_row.addWidget(self.jira_token_edit, 1)
        third_party_layout.addLayout(jira_token_row)
        
        # Figma 配置
        figma_row = QHBoxLayout()
        figma_label = QLabel("Figma API 地址：")
        self.figma_api_edit = QLineEdit()
        self.figma_api_edit.setPlaceholderText("例如：https://api.figma.com/v1")
        self.figma_api_edit.setText(self.cfg.get("figma_api_url", "https://api.figma.com/v1"))
        self._figma_api_save_timer = QTimer()
        self._figma_api_save_timer.setSingleShot(True)
        self._figma_api_save_timer.timeout.connect(self._auto_save_figma_api_url)
        self.figma_api_edit.textChanged.connect(lambda: self._figma_api_save_timer.start(500))
        self.figma_api_edit.editingFinished.connect(self._on_figma_api_url_changed)
        self.figma_api_edit.returnPressed.connect(self._on_figma_api_url_changed)
        figma_row.addWidget(figma_label)
        figma_row.addWidget(self.figma_api_edit, 1)
        third_party_layout.addLayout(figma_row)
        
        figma_key_row = QHBoxLayout()
        figma_key_label = QLabel("Figma API Key：")
        self.figma_api_key_edit = QLineEdit()
        self.figma_api_key_edit.setPlaceholderText("Figma Personal Access Token")
        self.figma_api_key_edit.setEchoMode(QLineEdit.Password)  # 密码模式
        self.figma_api_key_edit.setText(self.cfg.get("figma_api_key", ""))
        self._figma_api_key_save_timer = QTimer()
        self._figma_api_key_save_timer.setSingleShot(True)
        self._figma_api_key_save_timer.timeout.connect(self._auto_save_figma_api_key)
        self.figma_api_key_edit.textChanged.connect(lambda: self._figma_api_key_save_timer.start(500))
        self.figma_api_key_edit.editingFinished.connect(self._on_figma_api_key_changed)
        self.figma_api_key_edit.returnPressed.connect(self._on_figma_api_key_changed)
        figma_key_row.addWidget(figma_key_label)
        figma_key_row.addWidget(self.figma_api_key_edit, 1)
        third_party_layout.addLayout(figma_key_row)
        
        # OpenAI 配置
        openai_session_row = QHBoxLayout()
        openai_session_label = QLabel("OpenAI Session Key：")
        self.openai_session_key_edit = QLineEdit()
        self.openai_session_key_edit.setPlaceholderText("例如：sess-xxx（从浏览器 Cookie 中获取，用于查询余额）")
        self.openai_session_key_edit.setEchoMode(QLineEdit.Password)  # 密码模式
        self.openai_session_key_edit.setText(self.cfg.get("openai_session_key", ""))
        self._openai_session_key_save_timer = QTimer()
        self._openai_session_key_save_timer.setSingleShot(True)
        self._openai_session_key_save_timer.timeout.connect(self._auto_save_openai_session_key)
        self.openai_session_key_edit.textChanged.connect(lambda: self._openai_session_key_save_timer.start(500))
        self.openai_session_key_edit.editingFinished.connect(self._on_openai_session_key_changed)
        self.openai_session_key_edit.returnPressed.connect(self._on_openai_session_key_changed)
        openai_session_row.addWidget(openai_session_label)
        openai_session_row.addWidget(self.openai_session_key_edit, 1)
        third_party_layout.addLayout(openai_session_row)
        
        third_party_layout.addStretch()
        
        tab_widget.addTab(third_party_tab, "第三方平台配置")
        
        # Tab 3: 打包配置（独立的 GitHub 仓库配置）
        packaging_tab = QWidget()
        packaging_layout = QVBoxLayout(packaging_tab)
        packaging_layout.setContentsMargins(16, 16, 16, 16)
        packaging_layout.setSpacing(12)
        
        packaging_title = QLabel("打包配置（GitHub 仓库）")
        packaging_title_font = QFont()
        packaging_title_font.setPointSize(12)
        packaging_title_font.setBold(True)
        packaging_title.setFont(packaging_title_font)
        packaging_layout.addWidget(packaging_title)
        
        packaging_desc = QLabel("此配置用于打包TAB的版本管理和构建功能，与第三方平台配置中的GitHub配置完全独立。")
        packaging_desc.setWordWrap(True)
        packaging_desc.setStyleSheet("color: #666; font-size: 10px;")
        packaging_layout.addWidget(packaging_desc)
        
        # GitHub API 地址（打包专用）
        packaging_github_api_row = QHBoxLayout()
        packaging_github_api_label = QLabel("GitHub API 地址：")
        self.packaging_github_api_edit = QLineEdit()
        self.packaging_github_api_edit.setPlaceholderText("例如：https://api.github.com")
        self.packaging_github_api_edit.setText(self.cfg.get("packaging_github_api_url", "https://api.github.com"))
        self._packaging_github_api_save_timer = QTimer()
        self._packaging_github_api_save_timer.setSingleShot(True)
        self._packaging_github_api_save_timer.timeout.connect(self._auto_save_packaging_github_api_url)
        self.packaging_github_api_edit.textChanged.connect(lambda: self._packaging_github_api_save_timer.start(500))
        self.packaging_github_api_edit.editingFinished.connect(self._on_packaging_github_api_url_changed)
        self.packaging_github_api_edit.returnPressed.connect(self._on_packaging_github_api_url_changed)
        packaging_github_api_row.addWidget(packaging_github_api_label)
        packaging_github_api_row.addWidget(self.packaging_github_api_edit, 1)
        packaging_layout.addLayout(packaging_github_api_row)
        
        # GitHub API Key（打包专用）
        packaging_github_key_row = QHBoxLayout()
        packaging_github_key_label = QLabel("GitHub API Key：")
        self.packaging_github_api_key_edit = QLineEdit()
        self.packaging_github_api_key_edit.setPlaceholderText("必需，用于访问私有仓库")
        self.packaging_github_api_key_edit.setEchoMode(QLineEdit.Password)  # 密码模式
        self.packaging_github_api_key_edit.setText(self.cfg.get("packaging_github_api_key", ""))
        self._packaging_github_api_key_save_timer = QTimer()
        self._packaging_github_api_key_save_timer.setSingleShot(True)
        self._packaging_github_api_key_save_timer.timeout.connect(self._auto_save_packaging_github_api_key)
        self.packaging_github_api_key_edit.textChanged.connect(lambda: self._packaging_github_api_key_save_timer.start(500))
        self.packaging_github_api_key_edit.editingFinished.connect(self._on_packaging_github_api_key_changed)
        self.packaging_github_api_key_edit.returnPressed.connect(self._on_packaging_github_api_key_changed)
        packaging_github_key_row.addWidget(packaging_github_key_label)
        packaging_github_key_row.addWidget(self.packaging_github_api_key_edit, 1)
        packaging_layout.addLayout(packaging_github_key_row)
        
        # GitHub 仓库配置
        packaging_github_repo_row = QHBoxLayout()
        packaging_github_repo_label = QLabel("GitHub 仓库：")
        packaging_github_repo_label.setToolTip("格式：owner/repo，例如：sanyingkeji/ai-perf")
        packaging_github_repo_container = QHBoxLayout()
        packaging_github_repo_container.setSpacing(4)
        
        self.packaging_github_repo_owner_edit = QLineEdit()
        self.packaging_github_repo_owner_edit.setPlaceholderText("仓库所有者")
        self.packaging_github_repo_owner_edit.setText(self.cfg.get("packaging_github_repo_owner", "sanyingkeji"))
        self._packaging_github_repo_owner_save_timer = QTimer()
        self._packaging_github_repo_owner_save_timer.setSingleShot(True)
        self._packaging_github_repo_owner_save_timer.timeout.connect(self._auto_save_packaging_github_repo_owner)
        self.packaging_github_repo_owner_edit.textChanged.connect(lambda: self._packaging_github_repo_owner_save_timer.start(500))
        self.packaging_github_repo_owner_edit.editingFinished.connect(self._on_packaging_github_repo_owner_changed)
        self.packaging_github_repo_owner_edit.returnPressed.connect(self._on_packaging_github_repo_owner_changed)
        packaging_github_repo_container.addWidget(self.packaging_github_repo_owner_edit)
        
        packaging_separator_label = QLabel("/")
        packaging_separator_label.setFixedWidth(20)
        packaging_separator_label.setAlignment(Qt.AlignCenter)
        packaging_github_repo_container.addWidget(packaging_separator_label)
        
        self.packaging_github_repo_name_edit = QLineEdit()
        self.packaging_github_repo_name_edit.setPlaceholderText("仓库名称")
        self.packaging_github_repo_name_edit.setText(self.cfg.get("packaging_github_repo_name", "ai-perf"))
        self._packaging_github_repo_name_save_timer = QTimer()
        self._packaging_github_repo_name_save_timer.setSingleShot(True)
        self._packaging_github_repo_name_save_timer.timeout.connect(self._auto_save_packaging_github_repo_name)
        self.packaging_github_repo_name_edit.textChanged.connect(lambda: self._packaging_github_repo_name_save_timer.start(500))
        self.packaging_github_repo_name_edit.editingFinished.connect(self._on_packaging_github_repo_name_changed)
        self.packaging_github_repo_name_edit.returnPressed.connect(self._on_packaging_github_repo_name_changed)
        packaging_github_repo_container.addWidget(self.packaging_github_repo_name_edit)
        
        packaging_github_repo_row.addWidget(packaging_github_repo_label)
        packaging_github_repo_row.addLayout(packaging_github_repo_container, 1)
        packaging_layout.addLayout(packaging_github_repo_row)
        
        packaging_layout.addStretch()
        
        tab_widget.addTab(packaging_tab, "打包配置")
        
        # Tab 4: 其他设置
        other_tab = QWidget()
        other_layout = QVBoxLayout(other_tab)
        other_layout.setContentsMargins(16, 16, 16, 16)
        other_layout.setSpacing(12)
        
        # --- 主题 ---
        theme_frame = QFrame()
        theme_layout = QVBoxLayout(theme_frame)
        theme_layout.setSpacing(4)
        
        theme_title = QLabel("主题")
        theme_title_font = QFont()
        theme_title_font.setPointSize(12)
        theme_title_font.setBold(True)
        theme_title.setFont(theme_title_font)
        theme_layout.addWidget(theme_title)
        
        # 使用互斥的可切换按钮替代 QRadioButton，避免 macOS 原生样式崩溃
        self.theme_buttons: list[QPushButton] = []
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)

        def _make_theme_button(text: str, value: str) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("themeValue", value)
            btn.setFixedHeight(24)
            btn.setMinimumWidth(60)
            self.theme_group.addButton(btn)
            self.theme_buttons.append(btn)
            return btn

        btn_auto = _make_theme_button("跟随系统", "auto")
        btn_light = _make_theme_button("浅色模式", "light")
        btn_dark = _make_theme_button("深色模式", "dark")
        
        theme_choice = self.cfg.get("theme", "auto")
        if theme_choice == "light":
            btn_light.setChecked(True)
        elif theme_choice == "dark":
            btn_dark.setChecked(True)
        else:
            btn_auto.setChecked(True)

        # 主题变更时自动保存并应用
        def _on_theme_clicked(button: QPushButton):
            if self._is_initializing:
                return
            value = button.property("themeValue")
            if value:
                self._auto_save_theme(str(value))

        self.theme_group.buttonClicked.connect(_on_theme_clicked)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        for btn in (btn_auto, btn_light, btn_dark):
            theme_row.addWidget(btn)
        theme_row.addStretch()
        theme_layout.addLayout(theme_row)

        # 初始根据当前/系统主题刷新样式
        self._update_theme_buttons_style(theme_choice)
        
        other_layout.addWidget(theme_frame)
        
        # --- 日志管理 ---
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(6)

        log_title = QLabel("日志")
        log_title_font = QFont()
        log_title_font.setPointSize(12)
        log_title_font.setBold(True)
        log_title.setFont(log_title_font)
        log_layout.addWidget(log_title)

        log_retention_row = QHBoxLayout()
        log_retention_label = QLabel("日志保留时长（小时）：")
        self.spin_log_retention = QSpinBox()
        self.spin_log_retention.setRange(1, 72)  # 最多 3 天
        self.spin_log_retention.setValue(int(self.cfg.get("log_retention_hours", 1) or 1))
        self.spin_log_retention.setSuffix(" 小时")
        self.spin_log_retention.valueChanged.connect(self._auto_save_log_retention)
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addWidget(self.spin_log_retention)
        log_retention_row.addStretch()
        log_layout.addLayout(log_retention_row)

        export_row = QHBoxLayout()
        self.btn_export_logs = QPushButton("导出最近日志")
        self.btn_export_logs.setFixedWidth(90)
        self.btn_export_logs.setFixedHeight(26)
        self.btn_export_logs.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        self.btn_export_logs.clicked.connect(self._export_logs)
        export_row.addWidget(QLabel("日志导出："))
        export_row.addWidget(self.btn_export_logs)
        export_row.addStretch()
        log_layout.addLayout(export_row)

        other_layout.addWidget(log_frame)

        # --- 缓存管理 ---
        cache_frame = QFrame()
        cache_layout = QVBoxLayout(cache_frame)
        cache_layout.setContentsMargins(0, 0, 0, 0)
        cache_layout.setSpacing(4)
        
        cache_label = QLabel("缓存管理")
        cache_label_font = QFont()
        cache_label_font.setPointSize(12)
        cache_label_font.setBold(True)
        cache_label.setFont(cache_label_font)
        cache_layout.addWidget(cache_label)
        
        cache_desc = QLabel("清除本地缓存数据（团队、角色、职级、薪级、GitHub Teams和成员等数据的缓存，缓存时间1天）")
        cache_desc.setFont(QFont("Arial", 9))
        cache_desc.setStyleSheet("color: #666;")
        cache_layout.addWidget(cache_desc)
        
        clear_cache_btn = QPushButton("清除缓存")
        clear_cache_btn.setFixedWidth(120)
        clear_cache_btn.clicked.connect(self._on_clear_cache_clicked)
        cache_layout.addWidget(clear_cache_btn)
        
        other_layout.addWidget(cache_frame)
        other_layout.addStretch()
        
        tab_widget.addTab(other_tab, "其他设置")
        
        layout.addWidget(tab_widget, 1)  # 添加拉伸因子，让Tab占满空间
        
        # --- 版本信息（底部，与tab无关） ---
        version_frame = QFrame()
        version_layout = QVBoxLayout(version_frame)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.setSpacing(4)
        
        version_label = QLabel("版本信息")
        version_label_font = QFont()
        version_label_font.setPointSize(12)
        version_label_font.setBold(True)
        version_label.setFont(version_label_font)
        version_layout.addWidget(version_label)
        
        # 从配置文件读取版本号
        client_version = self.cfg.get("client_version", "1.0.0")
        version_text = QLabel(f"Ai 绩效管理端 v{client_version}")
        version_text.setFont(QFont("Arial", 10))
        version_text.setStyleSheet("color: #666;")
        version_layout.addWidget(version_text)
        
        layout.addWidget(version_frame)
    
    # --------- 槽函数 ---------
    def on_google_login_clicked(self):
        """在设置页发起 Google 登录流程（对齐登录弹窗的流程）。"""
        # 防止重复点击
        if self._login_in_progress:
            Toast.show_message(self, "登录正在进行中，请勿重复点击")
            return
        
        # 设置登录进行中标志
        self._login_in_progress = True
        
        main_window = self.window()
        
        # 显示"等待登录回调中"遮盖层（可关闭）
        def _on_cancel_login():
            """用户取消登录"""
            # 停止 worker
            if hasattr(worker, 'stop'):
                worker.stop()
            # 从列表中移除 worker
            if hasattr(self, '_login_worker') and worker in self._login_worker:
                self._login_worker.remove(worker)
            # 隐藏加载遮罩
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            # 重置登录状态
            self._login_in_progress = False
            # 强制退出应用（因为 run_local_server 无法中断）
            import os
            os._exit(0)
        
        if hasattr(main_window, "show_loading"):
            main_window.show_loading(
                "等待登录回调中...\n请完成浏览器中的授权操作后回到软件界面",
                closeable=True,
                close_callback=_on_cancel_login
            )
        
        # 在后台线程中执行登录
        class _LoginWorkerSignals(QObject):
            callback_received = Signal()  # 已收到回调，正在登录中
            finished = Signal()  # 登录成功
            error = Signal(str)  # 登录失败
        
        class _LoginWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = _LoginWorkerSignals()
            
            def run(self):
                try:
                    from utils.google_login import login_and_get_id_token
                    
                    # 定义回调函数：在收到 Google 回调后、调用后端接口前调用
                    def on_callback_received():
                        # 通过信号通知 UI 线程更新状态
                        self.signals.callback_received.emit()
                    
                    login_and_get_id_token(callback_received_callback=on_callback_received)
                    self.signals.finished.emit()
                except GoogleLoginError as e:
                    self.signals.error.emit(str(e))
                except Exception as e:
                    self.signals.error.emit(f"登录异常：{e}")
        
        worker = _LoginWorker()
        
        def _on_callback_received():
            """已收到回调，正在登录中"""
            if hasattr(main_window, "show_loading"):
                main_window.show_loading("已成功接收到谷歌回调信息，正在登录中...", closeable=False)
        
        def _on_login_success():
            """登录成功回调"""
            self._login_in_progress = False  # 重置登录状态
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            
            # login_and_get_id_token 内部已调用后端 /admin/auth/google_login 并刷新了配置
            try:
                self.cfg = ConfigManager.load()
            except Exception:
                self.cfg = {}

            # 只读展示 ID Token（调试用）
            self.token_edit.setText(self.cfg.get("google_id_token", ""))

            # 更新邮箱显示
            if hasattr(self, "email_value"):
                self.email_value.setText(self.cfg.get("user_email", "") or "（未登录）")

            # 展示 session_token（只读）
            if hasattr(self, "session_edit"):
                self.session_edit.setText(self.cfg.get("session_token", ""))

            # 更新按钮显隐
            self._refresh_login_buttons()

            Toast.show_message(self, "Google 登录成功")
            
            # 通知主窗口刷新菜单权限（重要：必须在登录成功后刷新菜单）
            if hasattr(main_window, "_load_menu_permission"):
                main_window._load_menu_permission()
            
            # 通知主窗口刷新当前页面（如果当前页面需要登录才能加载数据）
            if hasattr(main_window, "refresh_current_page_after_login"):
                main_window.refresh_current_page_after_login()
        
        def _on_login_error(error_msg: str):
            """登录失败回调"""
            self._login_in_progress = False  # 重置登录状态
            if hasattr(main_window, "hide_loading"):
                main_window.hide_loading()
            
            # 清理 worker 引用
            if hasattr(self, '_login_worker') and worker in self._login_worker:
                self._login_worker.remove(worker)
            
            # 如果是权限错误，使用 QMessageBox 显示更详细的提示
            if "无权限" in error_msg or "只有以下管理员邮箱" in error_msg or "权限" in error_msg:
                from PySide6.QtWidgets import QMessageBox
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("登录失败 - 无权限")
                msg_box.setText(f"您的邮箱不是管理员邮箱，无法登录管理端。\n\n{error_msg}\n\n请联系系统管理员将您的邮箱添加到管理员列表。")
                msg_box.setIcon(QMessageBox.Icon.Warning)
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.exec()  # 使用 exec() 而不是 show()，确保对话框关闭后继续执行
            else:
                # 其他错误使用 Toast 显示
                Toast.show_message(self, f"Google 登录失败：{error_msg}")
        
        worker.signals.callback_received.connect(_on_callback_received)
        worker.signals.finished.connect(_on_login_success)
        worker.signals.error.connect(_on_login_error)
        
        # 保存worker引用，防止被垃圾回收
        if not hasattr(self, '_login_worker'):
            self._login_worker = []
        self._login_worker.append(worker)
        
        QThreadPool.globalInstance().start(worker)
    
    def on_google_logout_clicked(self):
        """清除本地登录状态，相当于退出登录。"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        
        self.cfg["google_id_token"] = ""
        self.cfg["session_token"] = ""
        self.cfg["user_id"] = ""
        self.cfg["user_name"] = ""
        self.cfg["user_email"] = ""
        ConfigManager.save(self.cfg)
        
        self.token_edit.setText("")
        if hasattr(self, "email_value"):
            self.email_value.setText("（未登录）")
        if hasattr(self, "session_edit"):
            self.session_edit.setText("")
        
        self._refresh_login_buttons()
        Toast.show_message(self, "已退出登录")
    
    def _refresh_login_buttons(self):
        """根据是否存在 session_token 切换"谷歌授权登录 / 退出登录"按钮显示。"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        logged_in = bool(self.cfg.get("session_token"))
        
        if hasattr(self, "btn_google_login"):
            self.btn_google_login.setVisible(not logged_in)
        if hasattr(self, "btn_google_logout"):
            self.btn_google_logout.setVisible(logged_in)
    
    def refresh_login_status(self):
        """刷新登录状态显示（当从其他页面登录成功后调用）"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        
        # 更新邮箱显示
        if hasattr(self, "email_value"):
            self.email_value.setText(self.cfg.get("user_email", "") or "（未登录）")
        
        # 更新 ID Token 显示
        if hasattr(self, "token_edit"):
            self.token_edit.setText(self.cfg.get("google_id_token", ""))
        
        # 更新 session_token 显示
        if hasattr(self, "session_edit"):
            self.session_edit.setText(self.cfg.get("session_token", ""))
        
        # 更新按钮显隐
        self._refresh_login_buttons()
    
    def _auto_save_api_base(self):
        """自动保存 API 地址"""
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["api_base"] = self.api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_api_base_changed(self):
        """API地址改变时（失去焦点或按回车）立即保存并刷新状态"""
        # 停止定时器（如果正在运行）
        if self._api_save_timer.isActive():
            self._api_save_timer.stop()
        
        # 立即保存
        self._auto_save_api_base()
        
        # 刷新登录状态（因为API地址改变后，需要重新检查登录状态）
        self.refresh_login_status()
    
    def _auto_save_upload_api_url(self):
        """自动保存上传 API 地址"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["upload_api_url"] = self.upload_api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_upload_api_url_changed(self):
        """上传API地址改变时（失去焦点或按回车）立即保存"""
        # 停止定时器（如果正在运行）
        if self._upload_api_save_timer.isActive():
            self._upload_api_save_timer.stop()
        
        # 立即保存
        self._auto_save_upload_api_url()

    def _auto_save_log_retention(self, value: int):
        """自动保存日志保留时长（小时）"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        hours = max(1, int(value))
        self.cfg["log_retention_hours"] = hours
        ConfigManager.save(self.cfg)
        # 立即按新策略清理旧日志
        try:
            log_dir = CONFIG_PATH.parent / "logs"
            if log_dir.exists():
                deadline = datetime.now() - timedelta(hours=hours)
                for f in log_dir.glob("*.log"):
                    try:
                        if datetime.fromtimestamp(f.stat().st_mtime) < deadline:
                            f.unlink()
                    except Exception:
                        pass
        except Exception:
            pass
        Toast.show_message(self, f"日志将保留最近 {hours} 小时")

    def _export_logs(self):
        """导出当前保留范围内的日志为 zip"""
        log_dir = Path(CONFIG_PATH.parent / "logs")
        if not log_dir.exists():
            Toast.show_message(self, "暂无日志可导出")
            return

        retention_hours = int(self.cfg.get("log_retention_hours", 1) or 1)
        deadline = datetime.now(timezone.utc).astimezone() - timedelta(hours=retention_hours)

        log_files = []
        for f in log_dir.glob("*.log"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).astimezone()
                if mtime >= deadline:
                    log_files.append(f)
            except Exception:
                pass

        if not log_files:
            Toast.show_message(self, "暂无符合保留时长的日志")
            return

        default_name = Path.home() / f"ai-perf-admin-logs-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志",
            str(default_name),
            "Zip 文件 (*.zip)"
        )
        if not file_path:
            return

        try:
            with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for f in log_files:
                    zf.write(f, arcname=f.name)
            Toast.show_message(self, "日志导出成功")
        except Exception as e:
            Toast.show_message(self, f"导出失败：{e}")
            print(f"[Admin Settings] Export logs failed: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    # GitHub API 配置保存方法
    def _auto_save_github_api_url(self):
        """自动保存 GitHub API 地址"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["github_api_url"] = self.github_api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_github_api_url_changed(self):
        """GitHub API地址改变时（失去焦点或按回车）立即保存"""
        if self._github_api_save_timer.isActive():
            self._github_api_save_timer.stop()
        self._auto_save_github_api_url()
    
    def _auto_save_github_api_key(self):
        """自动保存 GitHub API Key"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["github_api_key"] = self.github_api_key_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_github_api_key_changed(self):
        """GitHub API Key改变时（失去焦点或按回车）立即保存"""
        if self._github_api_key_save_timer.isActive():
            self._github_api_key_save_timer.stop()
        self._auto_save_github_api_key()
    
    def _auto_save_github_org(self):
        """自动保存 GitHub 组织"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["github_org"] = self.github_org_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_github_org_changed(self):
        """GitHub 组织改变时（失去焦点或按回车）立即保存"""
        if self._github_org_save_timer.isActive():
            self._github_org_save_timer.stop()
        self._auto_save_github_org()
    
    # 打包配置保存方法（独立的 GitHub 仓库配置）
    def _auto_save_packaging_github_api_url(self):
        """自动保存打包用 GitHub API 地址"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["packaging_github_api_url"] = self.packaging_github_api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_packaging_github_api_url_changed(self):
        """打包用 GitHub API地址改变时（失去焦点或按回车）立即保存"""
        if self._packaging_github_api_save_timer.isActive():
            self._packaging_github_api_save_timer.stop()
        self._auto_save_packaging_github_api_url()
    
    def _auto_save_packaging_github_api_key(self):
        """自动保存打包用 GitHub API Key"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["packaging_github_api_key"] = self.packaging_github_api_key_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_packaging_github_api_key_changed(self):
        """打包用 GitHub API Key改变时（失去焦点或按回车）立即保存"""
        if self._packaging_github_api_key_save_timer.isActive():
            self._packaging_github_api_key_save_timer.stop()
        self._auto_save_packaging_github_api_key()
    
    def _auto_save_packaging_github_repo_owner(self):
        """自动保存打包用 GitHub 仓库所有者"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["packaging_github_repo_owner"] = self.packaging_github_repo_owner_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_packaging_github_repo_owner_changed(self):
        """打包用 GitHub 仓库所有者改变时（失去焦点或按回车）立即保存"""
        if self._packaging_github_repo_owner_save_timer.isActive():
            self._packaging_github_repo_owner_save_timer.stop()
        self._auto_save_packaging_github_repo_owner()
    
    def _auto_save_packaging_github_repo_name(self):
        """自动保存打包用 GitHub 仓库名称"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["packaging_github_repo_name"] = self.packaging_github_repo_name_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_packaging_github_repo_name_changed(self):
        """打包用 GitHub 仓库名称改变时（失去焦点或按回车）立即保存"""
        if self._packaging_github_repo_name_save_timer.isActive():
            self._packaging_github_repo_name_save_timer.stop()
        self._auto_save_packaging_github_repo_name()
    
    # Jira API 配置保存方法（与后端 .env 保持一致）
    def _auto_save_jira_base(self):
        """自动保存 Jira 基础地址"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["jira_base"] = self.jira_base_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_jira_base_changed(self):
        """Jira 基础地址改变时（失去焦点或按回车）立即保存"""
        if self._jira_base_save_timer.isActive():
            self._jira_base_save_timer.stop()
        self._auto_save_jira_base()
    
    def _auto_save_jira_account_email(self):
        """自动保存 Jira 账号邮箱"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["jira_account_email"] = self.jira_account_email_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_jira_account_email_changed(self):
        """Jira 账号邮箱改变时（失去焦点或按回车）立即保存"""
        if self._jira_account_email_save_timer.isActive():
            self._jira_account_email_save_timer.stop()
        self._auto_save_jira_account_email()
    
    def _auto_save_jira_token(self):
        """自动保存 Jira API Token"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["jira_token"] = self.jira_token_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_jira_token_changed(self):
        """Jira API Token改变时（失去焦点或按回车）立即保存"""
        if self._jira_token_save_timer.isActive():
            self._jira_token_save_timer.stop()
        self._auto_save_jira_token()
    
    # Figma API 配置保存方法
    def _auto_save_figma_api_url(self):
        """自动保存 Figma API 地址"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["figma_api_url"] = self.figma_api_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_figma_api_url_changed(self):
        """Figma API地址改变时（失去焦点或按回车）立即保存"""
        if self._figma_api_save_timer.isActive():
            self._figma_api_save_timer.stop()
        self._auto_save_figma_api_url()
    
    def _auto_save_figma_api_key(self):
        """自动保存 Figma API Key"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["figma_api_key"] = self.figma_api_key_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_figma_api_key_changed(self):
        """Figma API Key改变时（失去焦点或按回车）立即保存"""
        if self._figma_api_key_save_timer.isActive():
            self._figma_api_key_save_timer.stop()
        self._auto_save_figma_api_key()
    
    def _auto_save_openai_session_key(self):
        """自动保存 OpenAI Session Key"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["openai_session_key"] = self.openai_session_key_edit.text().strip()
        ConfigManager.save(self.cfg)
    
    def _on_openai_session_key_changed(self):
        """OpenAI Session Key改变时（失去焦点或按回车）立即保存"""
        if self._openai_session_key_save_timer.isActive():
            self._openai_session_key_save_timer.stop()
        self._auto_save_openai_session_key()
    
    def _auto_save_theme(self, theme: str):
        """自动保存主题设置并立即应用"""
        if self._is_initializing:
            return
        try:
            self.cfg = ConfigManager.load()
        except Exception:
            self.cfg = {}
        self.cfg["theme"] = theme
        ConfigManager.save(self.cfg)
        ThemeManager.apply_theme()
        # 同步主题按钮的配色
        self._update_theme_buttons_style(theme)

    def _resolve_effective_theme(self, pref: str) -> str:
        """根据用户偏好与系统，得出实际主题（light/dark）。"""
        if pref == "auto":
            try:
                return ThemeManager.detect_system_theme()
            except Exception:
                return "light"
        return pref if pref in ("light", "dark") else "light"

    def _update_theme_buttons_style(self, pref: str):
        """根据当前主题调整按钮配色，保证暗色/亮色都可读。"""
        effective = self._resolve_effective_theme(pref)
        if effective == "dark":
            bg = "#2d2d2d"
            bg_hover = "#383838"
            border = "#555"
            checked_bg = "#3d7bfd"
            checked_border = "#3d7bfd"
            text = "#e8e8e8"
            checked_text = "#ffffff"
        else:
            bg = "#f6f6f6"
            bg_hover = "#f0f0f0"
            border = "#c7c7c7"
            checked_bg = "#0078d4"
            checked_border = "#0078d4"
            text = "#222"
            checked_text = "#ffffff"

        style = (
            "QPushButton {"
            f"  border: 1px solid {border};"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            f"  background: {bg};"
            f"  color: {text};"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            f"  background: {bg_hover};"
            "}"
            "QPushButton:checked {"
            f"  background: {checked_bg};"
            f"  color: {checked_text};"
            f"  border-color: {checked_border};"
            "}"
        )

        for btn in getattr(self, "theme_buttons", []):
            btn.setStyleSheet(style)
    
    def _load_api_health(self):
        """加载后端API服务状态和版本信息"""
        worker = _AdminApiHealthWorker(current_version=self._client_version)
        worker.signals.finished.connect(self._on_api_health_loaded)
        worker.signals.error.connect(self._on_api_health_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_api_health_loaded(self, health_data: Dict[str, Any]):
        """后端API服务状态加载完成"""
        status = health_data.get("status", "unknown")
        if status == "ok":
            self.health_status_label.setText("状态：正常")
            self.health_status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.health_status_label.setText(f"状态：{status}")
            self.health_status_label.setStyleSheet("color: orange; font-weight: bold;")
        
        # 显示检查时间
        check_time = health_data.get("time")
        if check_time:
            try:
                # 尝试解析ISO格式时间
                if isinstance(check_time, str):
                    if check_time.endswith('Z'):
                        # UTC时间
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(check_time.replace('Z', '+00:00'))
                        local_time = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        # 本地时间
                        from datetime import datetime
                        dt = datetime.fromisoformat(check_time)
                        local_time = dt
                    time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    time_str = str(check_time)
            except Exception:
                time_str = str(check_time)
            self.health_time_label.setText(f"检查时间：{time_str}")
        else:
            self.health_time_label.setText("检查时间：--")
        
        # 检查版本升级
        version_info = health_data.get("version_info")
        if version_info:
            # 有新版本需要升级，显示升级弹窗
            self._show_update_dialog(version_info)
    
    def _on_api_health_error(self, message: str):
        """后端API服务状态加载失败"""
        self.health_status_label.setText("状态：检查失败")
        self.health_status_label.setStyleSheet("color: red; font-weight: bold;")
        self.health_time_label.setText("检查时间：--")
    
    def _show_update_dialog(self, version_info: dict):
        """显示版本升级弹窗"""
        is_force_update = version_info.get("is_force_update", True)
        new_version = version_info.get("version", "")
        
        # 非强制升级：检查今天是否已经关闭过弹窗
        if not is_force_update:
            try:
                cfg = ConfigManager.load()
                dismissed_date = cfg.get("update_dialog_dismissed_date", "")
                if dismissed_date == date.today().isoformat():
                    # 今天已经关闭过，不再显示
                    return
            except Exception:
                pass
        
        # 检查主窗口是否已经有升级弹窗在显示
        main_window = self.window()
        existing_dialog = None
        if hasattr(main_window, '_update_dialog') and main_window._update_dialog and main_window._update_dialog.isVisible():
            existing_dialog = main_window._update_dialog
        elif hasattr(self, '_update_dialog') and self._update_dialog and self._update_dialog.isVisible():
            existing_dialog = self._update_dialog
        
        # 如果已有弹窗在显示
        if existing_dialog:
            # 强制升级：检查版本是否有变化
            if is_force_update:
                existing_version = existing_dialog._version_info.get("version", "")
                if existing_version != new_version:
                    # 版本有变化，关闭旧弹窗，显示新弹窗
                    existing_dialog.close()
                    existing_dialog.deleteLater()
                else:
                    # 版本没变化，不重复显示
                    return
            else:
                # 非强制升级：已有弹窗在显示，不重复显示
                return
        
        # 如果已经显示过升级弹窗（且不是版本更新），不再重复显示
        if self._update_dialog_shown:
            return
        
        # 检查主窗口是否已经显示过升级弹窗（避免重复）
        if hasattr(main_window, '_update_dialog_shown') and main_window._update_dialog_shown:
            return
        
        self._update_dialog = UpdateDialog(self, self._client_version, version_info)
        self._update_dialog.show()
        self._update_dialog_shown = True
        
        # 同时标记主窗口，避免其他地方重复显示
        if hasattr(main_window, '_update_dialog_shown'):
            main_window._update_dialog_shown = True
    
    def _on_clear_cache_clicked(self):
        """清除缓存按钮点击事件"""
        # 确认对话框
        reply = QMessageBox.question(
            self,
            "清除缓存",
            "确定要清除所有本地缓存数据吗？\n\n清除后，下次加载数据时会重新从服务器获取。\n\n包括：团队、角色、职级、薪级、GitHub Teams和成员等数据。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        # 获取 cache 目录路径
        from utils.config_manager import CONFIG_PATH
        cache_dir = CONFIG_PATH.parent / "cache"
        
        try:
            # 如果目录存在，删除目录下的所有文件
            if cache_dir.exists() and cache_dir.is_dir():
                # 删除目录下的所有文件
                for cache_file in cache_dir.iterdir():
                    if cache_file.is_file():
                        cache_file.unlink()
                    elif cache_file.is_dir():
                        shutil.rmtree(cache_file)
                
                Toast.show_message(self, "缓存已清除")
            else:
                Toast.show_message(self, "缓存目录不存在，无需清除")
        except Exception as e:
            QMessageBox.warning(
                self,
                "清除缓存失败",
                f"清除缓存时发生错误：{e}"
            )


class _AdminApiHealthWorkerSignals(QObject):
    finished = Signal(dict)
    error = Signal(str)


class _AdminApiHealthWorker(QRunnable):
    """后台线程：获取后端API服务状态和版本信息（无需登录）"""
    def __init__(self, current_version: str):
        super().__init__()
        self.signals = _AdminApiHealthWorkerSignals()
        self._current_version = current_version

    @Slot()
    def run(self) -> None:
        try:
            import httpx
            from utils.config_manager import ConfigManager
            
            cfg = ConfigManager.load()
            api_base = (cfg.get("api_base") or cfg.get("api_base_url") or "http://127.0.0.1:8880").strip()
            
            # 直接使用HTTP请求，不需要登录
            url = f"{api_base}/admin/health"
            params = {"current_version": self._current_version} if self._current_version else None
            r = httpx.get(url, params=params, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("status") == "success":
                    health_data = data.get("data")
                    if health_data:
                        self.signals.finished.emit(health_data)
                    else:
                        self.signals.error.emit("无法获取后端API服务状态")
                else:
                    self.signals.error.emit("无法获取后端API服务状态")
            else:
                self.signals.error.emit(f"获取后端API服务状态失败：HTTP {r.status_code}")
        except Exception as e:
            self.signals.error.emit(f"获取后端API服务状态失败：{e}")
