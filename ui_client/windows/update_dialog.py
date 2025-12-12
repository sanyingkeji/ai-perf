#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本升级弹窗（强制升级，不可关闭）
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
)
from PySide6.QtGui import QFont, QCursor
from PySide6.QtCore import Qt
import webbrowser
import platform
import sys
import os
from datetime import date
from utils.config_manager import ConfigManager


class UpdateDialog(QDialog):
    """版本升级弹窗（强制升级，不可关闭）"""
    def __init__(self, parent, current_version: str, version_info: dict):
        super().__init__(parent)
        self._current_version = current_version
        self._version_info = version_info
        
        # 根据当前操作系统选择下载地址
        self._download_url = self._get_download_url_for_current_platform(version_info)
        
        # 获取内网镜像下载地址（从 version_info 中获取，如果为空则使用默认值）
        self._mirror_url = version_info.get("mirror_url", "").strip()
        if not self._mirror_url:
            self._mirror_url = "http://192.168.2.1/download"
        
        self.setWindowTitle("版本升级")
        self.setModal(True)
        
        # 根据 is_force_update 决定是否可以关闭弹窗
        is_force_update = version_info.get("is_force_update", True)
        self._is_force_update = is_force_update
        
        if is_force_update:
            # 强制升级：禁用关闭按钮
            self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint)
            # 移除关闭按钮（在macOS上可能不生效，但至少禁用）
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        else:
            # 非强制升级：允许关闭
            self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        
        self.resize(600, 500)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # 标题
        title = QLabel("发现新版本")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # 版本信息
        version_layout = QHBoxLayout()
        version_layout.addStretch()
        
        current_version_label = QLabel(f"当前版本：v{current_version}")
        current_version_label.setFont(QFont("Arial", 12))
        version_layout.addWidget(current_version_label)
        
        arrow_label = QLabel("→")
        arrow_label.setFont(QFont("Arial", 14))
        arrow_label.setStyleSheet("color: #666; margin: 0 16px;")
        version_layout.addWidget(arrow_label)
        
        new_version = version_info.get("version", "")
        new_version_label = QLabel(f"新版本：v{new_version}")
        new_version_label.setFont(QFont("Arial", 12, QFont.Bold))
        new_version_label.setStyleSheet("color: #0066cc;")
        version_layout.addWidget(new_version_label)
        
        version_layout.addStretch()
        layout.addLayout(version_layout)
        
        # 升级内容
        release_notes_label = QLabel("本次升级内容：")
        release_notes_label.setFont(QFont("Arial", 11, QFont.Bold))
        layout.addWidget(release_notes_label)
        
        release_notes_text = QTextEdit()
        release_notes_text.setReadOnly(True)
        release_notes_text.setFont(QFont("Arial", 10))
        release_notes = version_info.get("release_notes", "暂无更新内容")
        release_notes_text.setPlainText(release_notes)
        release_notes_text.setMaximumHeight(200)
        layout.addWidget(release_notes_text)
        
        # 提示信息（根据是否强制升级显示不同文案）
        if self._is_force_update:
            tip_label = QLabel("⚠️ 此版本为强制升级，请下载新版本后继续使用")
        else:
            tip_label = QLabel("💡 发现新版本，建议下载更新以获得更好的体验")
        tip_label.setFont(QFont("Arial", 10))
        tip_label.setStyleSheet("color: #ff6600; font-weight: bold;")
        tip_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(tip_label)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        download_btn = QPushButton("去下载")
        download_btn.setFont(QFont("Arial", 12, QFont.Bold))
        download_btn.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                padding: 10px 30px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
        """)
        download_btn.clicked.connect(self._on_download_clicked)
        btn_layout.addWidget(download_btn)
        
        # 内网镜像下载链接
        mirror_link = QLabel('<a href="#" style="color: #0066cc; text-decoration: none;">内网镜像下载</a>')
        mirror_link.setFont(QFont("Arial", 11))
        mirror_link.setCursor(QCursor(Qt.PointingHandCursor))
        mirror_link.setOpenExternalLinks(False)  # 禁用默认的打开链接行为，使用自定义处理
        mirror_link.mousePressEvent = lambda e: self._on_mirror_download_clicked()
        btn_layout.addSpacing(20)  # 添加间距
        btn_layout.addWidget(mirror_link)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        layout.addStretch()
    
    def _get_download_url_for_current_platform(self, version_info: dict) -> str:
        """根据当前操作系统获取对应的下载地址"""
        # 优先使用多平台下载地址
        download_urls = version_info.get("download_urls")
        if download_urls and isinstance(download_urls, dict):
            # 检测操作系统
            os_name = platform.system().lower()
            platform_urls = None
            if os_name == "darwin":
                platform_urls = download_urls.get("darwin")
            elif os_name == "windows":
                platform_urls = download_urls.get("windows")
            elif os_name == "linux":
                platform_urls = download_urls.get("linux")
            
            # 处理不同的数据格式
            if platform_urls:
                if isinstance(platform_urls, list) and len(platform_urls) > 0:
                    # 如果是列表格式，取第一个元素的 url
                    first_item = platform_urls[0]
                    if isinstance(first_item, dict) and "url" in first_item:
                        return first_item["url"]
                elif isinstance(platform_urls, str):
                    # 如果是字符串格式（兼容旧版本），直接返回
                    return platform_urls
        
        # 如果没有多平台地址，使用兼容的 download_url
        return version_info.get("download_url", "")
    
    def _on_download_clicked(self):
        """点击下载按钮，打开下载链接并退出程序"""
        if self._download_url:
            webbrowser.open(self._download_url)
            # 直接退出应用程序（因为要下载后覆盖安装，不能运行应用程序）
            # 先停止隔空投送服务（注销 mDNS 服务，让其他端知道设备已离线）
            widget = self.parent()
            main_window = None
            while widget:
                # 检查是否是主窗口（MainWindow）
                if widget.__class__.__name__ == "MainWindow":
                    main_window = widget
                    break
                # 如果不是，继续向上查找
                widget = widget.parent()
            
            # 停止隔空投送服务
            if main_window and hasattr(main_window, '_airdrop_window') and main_window._airdrop_window:
                airdrop_window = main_window._airdrop_window
                if hasattr(airdrop_window, '_transfer_manager') and airdrop_window._transfer_manager:
                    try:
                        airdrop_window._transfer_manager.stop()
                    except Exception:
                        pass
            
            # 关闭主窗口
            if main_window:
                main_window.close()
            else:
                # 如果找不到主窗口，直接退出
                QApplication = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
                QApplication.instance().quit()
            
            # 优雅退出，避免绕过 atexit/日志
            import sys
            from PySide6.QtCore import QTimer
            QApplication = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
            QApplication.instance().quit()
            QTimer.singleShot(1000, lambda: sys.exit(0))
        else:
            # 如果没有找到对应平台的下载地址，显示提示
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, 
                "下载地址未配置", 
                f"当前操作系统（{platform.system()}）的下载地址未配置，请联系管理员。"
            )
    
    def _on_mirror_download_clicked(self):
        """点击内网镜像下载链接，打开浏览器并退出程序"""
        if self._mirror_url:
            webbrowser.open(self._mirror_url)
            # 直接退出应用程序（因为要下载后覆盖安装，不能运行应用程序）
            # 先停止隔空投送服务（注销 mDNS 服务，让其他端知道设备已离线）
            widget = self.parent()
            main_window = None
            while widget:
                # 检查是否是主窗口（MainWindow）
                if widget.__class__.__name__ == "MainWindow":
                    main_window = widget
                    break
                # 如果不是，继续向上查找
                widget = widget.parent()
            
            # 停止隔空投送服务
            if main_window and hasattr(main_window, '_airdrop_window') and main_window._airdrop_window:
                airdrop_window = main_window._airdrop_window
                if hasattr(airdrop_window, '_transfer_manager') and airdrop_window._transfer_manager:
                    try:
                        airdrop_window._transfer_manager.stop()
                    except Exception:
                        pass
            
            # 关闭主窗口
            if main_window:
                main_window.close()
            else:
                # 如果找不到主窗口，直接退出
                QApplication = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
                QApplication.instance().quit()
            
            # 优雅退出，避免绕过 atexit/日志
            import sys
            from PySide6.QtCore import QTimer
            QApplication = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
            QApplication.instance().quit()
            QTimer.singleShot(1000, lambda: sys.exit(0))
    
    def closeEvent(self, event):
        """重写关闭事件，根据是否强制升级决定是否允许关闭"""
        if self._is_force_update:
            # 强制升级：不允许关闭
            event.ignore()
        else:
            # 非强制升级：允许关闭，记录关闭日期
            try:
                cfg = ConfigManager.load()
                cfg["update_dialog_dismissed_date"] = date.today().isoformat()
                ConfigManager.save(cfg)
            except Exception:
                pass
            event.accept()

