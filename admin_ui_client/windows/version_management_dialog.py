#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本号统一管理对话框
使用 VersionManager 统一管理所有客户端的版本号
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from utils.version_manager import VersionManager
from pathlib import Path


class VersionManagementDialog(QDialog):
    """版本号统一管理对话框"""
    
    def __init__(self, parent=None, version_manager: VersionManager = None):
        super().__init__(parent)
        self.setWindowTitle("版本号管理")
        self.resize(700, 500)
        
        # 使用传入的 VersionManager 或创建新的
        if version_manager is None:
            self.version_manager = VersionManager()
        else:
            self.version_manager = version_manager
        
        self._init_ui()
        self._load_versions()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # 标题
        title = QLabel("统一管理版本号")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)
        
        # 说明
        info_label = QLabel(
            "此功能可以统一更新所有客户端的版本号，避免手动修改多个文件。\n"
            "版本号格式：x.x.x（例如：1.0.1）"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; padding: 8px;")
        layout.addWidget(info_label)
        
        # 版本号输入
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("新版本号："))
        self.version_input = QLineEdit()
        self.version_input.setPlaceholderText("例如：1.0.1")
        input_layout.addWidget(self.version_input, 1)
        
        update_btn = QPushButton("更新所有")
        update_btn.setFixedWidth(100)
        update_btn.clicked.connect(self._update_all_versions)
        input_layout.addWidget(update_btn)
        
        layout.addLayout(input_layout)
        
        # 当前版本号表格
        table_label = QLabel("当前各文件的版本号：")
        table_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(table_label)
        
        self.version_table = QTableWidget()
        self.version_table.setColumnCount(3)
        self.version_table.setHorizontalHeaderLabels(["文件类型", "文件路径", "当前版本号"])
        self.version_table.horizontalHeader().setStretchLastSection(True)
        self.version_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.version_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.version_table.setAlternatingRowColors(True)
        
        # 设置列宽
        header = self.version_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        
        layout.addWidget(self.version_table, 1)
        
        # 版本信息摘要
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 8px; background-color: #f5f5f5; border-radius: 4px;")
        layout.addWidget(self.summary_label)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(100)
        refresh_btn.clicked.connect(self._load_versions)
        btn_layout.addWidget(refresh_btn)
        
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
    
    def _load_versions(self):
        """加载当前版本号信息"""
        # 获取所有版本号
        all_versions = self.version_manager.get_all_versions()
        version_info = self.version_manager.get_version_info()
        
        # 文件类型映射
        file_type_map = {
            "employee_macos": "员工端 (macOS)",
            "employee_windows_linux": "员工端 (Windows/Linux)",
            "admin_macos": "管理端 (macOS)",
            "admin_windows_linux": "管理端 (Windows/Linux)",
            "employee_config": "员工端 config.json",
            "admin_config": "管理端 config.json",
        }
        
        # 更新表格
        self.version_table.setRowCount(len(all_versions))
        
        for row, (key, version) in enumerate(all_versions.items()):
            # 文件类型
            file_type = file_type_map.get(key, key)
            type_item = QTableWidgetItem(file_type)
            self.version_table.setItem(row, 0, type_item)
            
            # 文件路径
            if key in self.version_manager.VERSION_FILES:
                spec_path = self.version_manager.VERSION_FILES.get(key, "")
            elif key == "employee_config":
                spec_path = "ui_client/config.json"
            elif key == "admin_config":
                spec_path = "admin_ui_client/config.json"
            else:
                spec_path = ""
            path_item = QTableWidgetItem(spec_path)
            self.version_table.setItem(row, 1, path_item)
            
            # 当前版本号
            version_text = version if version else "未找到"
            version_item = QTableWidgetItem(version_text)
            if not version:
                version_item.setForeground(QColor("#999"))
            self.version_table.setItem(row, 2, version_item)
        
        # 更新摘要信息
        current_version = version_info.get("current_version", "未知")
        is_consistent = version_info.get("is_consistent", False)
        unique_versions = version_info.get("unique_versions", [])
        
        if is_consistent:
            summary_text = f"✓ 当前版本号：{current_version}\n✓ 所有文件版本号一致"
            self.summary_label.setStyleSheet("padding: 8px; background-color: #d4edda; border-radius: 4px; color: #155724;")
        else:
            summary_text = f"⚠ 版本号不一致！\n当前版本：{current_version}\n发现多个版本：{', '.join(unique_versions)}"
            self.summary_label.setStyleSheet("padding: 8px; background-color: #fff3cd; border-radius: 4px; color: #856404;")
        
        self.summary_label.setText(summary_text)
        
        # 设置输入框默认值为当前版本
        if current_version and current_version != "未知":
            self.version_input.setText(current_version)
    
    def _update_all_versions(self):
        """更新所有文件的版本号"""
        new_version = self.version_input.text().strip()
        
        # 验证版本号格式
        if not new_version:
            QMessageBox.warning(self, "错误", "请输入版本号")
            return
        
        if not self.version_manager.validate_version(new_version):
            QMessageBox.warning(
                self,
                "错误",
                "版本号格式不正确！\n\n"
                "版本号格式应为：x.x.x（例如：1.0.1）"
            )
            return
        
        # 确认操作
        reply = QMessageBox.question(
            self,
            "确认更新",
            f"确定要将所有文件的版本号更新为 {new_version} 吗？\n\n"
            f"这将更新以下文件：\n"
            f"- ui_client/build_macos.spec\n"
            f"- ui_client/build.spec\n"
            f"- admin_ui_client/build_macos.spec\n"
            f"- admin_ui_client/build.spec\n"
            f"- ui_client/config.json (client_version)\n"
            f"- admin_ui_client/config.json (client_version)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 更新版本号
        results = self.version_manager.update_version(new_version)
        
        # 检查结果
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        if success_count == total_count:
            QMessageBox.information(
                self,
                "更新成功",
                f"所有文件的版本号已成功更新为 {new_version}！"
            )
            # 重新加载版本信息
            self._load_versions()
        else:
            failed_files = [key for key, success in results.items() if not success]
            QMessageBox.warning(
                self,
                "部分更新失败",
                f"成功更新 {success_count}/{total_count} 个文件。\n\n"
                f"失败的文件：\n" + "\n".join(failed_files)
            )
            # 仍然重新加载版本信息
            self._load_versions()

