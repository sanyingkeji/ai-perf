#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本管理页面：
- 显示版本列表（支持按客户端类型筛选）
- 添加版本
- 编辑版本
- 删除版本（软删除）
"""

from typing import Dict, Any, List, Optional
from functools import partial
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QFrame, QComboBox, QDialog, QLineEdit, QTextEdit,
    QCheckBox, QMessageBox, QHeaderView, QAbstractItemView, QScrollArea,
    QFileDialog, QProgressDialog
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt, QRunnable, QThreadPool, QObject, Signal, Slot, QTimer
import httpx
import os

from utils.api_client import AdminApiClient
from utils.error_handler import handle_api_error


class _VersionWorkerSignals(QObject):
    finished = Signal(list)  # List[Dict]
    error = Signal(str)


class _UploadWorkerSignals(QObject):
    """上传进度信号（支持断点续传）"""
    progress = Signal(int, int)  # 已上传字节数, 总字节数
    finished = Signal(str)  # 下载URL
    error = Signal(str)  # 错误信息
    chunk_progress = Signal(int, int, int)  # 当前分片索引, 总分片数, 当前分片进度(0-100)


class _UploadWorker(QRunnable):
    """后台线程：上传文件（支持断点续传）"""
    def __init__(self, file_path: str, platform: str, version: str, upload_api_url: str, chunk_size: int = 10 * 1024 * 1024):
        super().__init__()
        self.signals = _UploadWorkerSignals()
        self._file_path = Path(file_path)
        self._platform = platform
        self._version = version
        self._upload_api_url = upload_api_url
        self._chunk_size = chunk_size  # 分片大小，默认10MB
        self._should_stop = False
        self._upload_id = None
        self._total_chunks = 0
        self._uploaded_chunks = set()
        
        # 进度文件路径（用于断点续传）
        self._progress_file = self._file_path.parent / f".{self._file_path.name}.upload_progress"
    
    def stop(self):
        """停止上传"""
        self._should_stop = True
    
    def _load_progress(self) -> Optional[Dict]:
        """加载上传进度"""
        if self._progress_file.exists():
            try:
                import json
                with open(self._progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return None
    
    def _save_progress(self, upload_id: str, uploaded_chunks: set):
        """保存上传进度"""
        try:
            import json
            file_size = self._file_path.stat().st_size if self._file_path.exists() else 0
            progress_data = {
                "upload_id": upload_id,
                "uploaded_chunks": list(uploaded_chunks),
                "file_path": str(self._file_path),
                "platform": self._platform,
                "version": self._version,
                "file_size": file_size  # 保存文件大小用于验证
            }
            with open(self._progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress_data, f)
        except Exception:
            pass  # 忽略保存错误
    
    def _clear_progress(self):
        """清除上传进度"""
        try:
            if self._progress_file.exists():
                self._progress_file.unlink()
        except Exception:
            pass
    
    @Slot()
    def run(self) -> None:
        try:
            if self._should_stop:
                return
            
            # 检查文件是否存在
            if not self._file_path.exists():
                self.signals.error.emit(f"文件不存在：{self._file_path}")
                return
            
            file_size = self._file_path.stat().st_size
            if file_size == 0:
                self.signals.error.emit("文件为空")
                return
            
            # 尝试加载之前的进度
            progress_data = self._load_progress()
            if progress_data and progress_data.get("upload_id"):
                # 验证文件路径、大小、平台和版本是否匹配（防止文件被修改）
                saved_file_path = progress_data.get("file_path")
                saved_platform = progress_data.get("platform")
                saved_version = progress_data.get("version")
                saved_file_size = progress_data.get("file_size", 0)
                
                # 如果文件路径、大小、平台或版本不匹配，清除进度并重新开始
                if (saved_file_path != str(self._file_path) or 
                    saved_platform != self._platform or 
                    saved_version != self._version or
                    saved_file_size != file_size):
                    self._clear_progress()
                    progress_data = None
                else:
                    # 恢复上传会话
                    self._upload_id = progress_data["upload_id"]
                    self._uploaded_chunks = set(progress_data.get("uploaded_chunks", []))
                    # 查询服务器上的进度
                    try:
                        progress_response = httpx.get(
                            f"{self._upload_api_url}/progress",
                            params={"upload_id": self._upload_id},
                            timeout=10.0
                        )
                        if progress_response.status_code == 200:
                            progress_result = progress_response.json()
                            if progress_result.get("status") == "success":
                                server_chunks = set(progress_result.get("uploaded_chunks", []))
                                # 合并本地和服务器进度
                                self._uploaded_chunks = self._uploaded_chunks.union(server_chunks)
                                self._total_chunks = progress_result.get("total_chunks", 0)
                            else:
                                # 服务器会话已过期或无效，清除本地进度并重新初始化
                                self._clear_progress()
                                self._upload_id = None
                                self._uploaded_chunks = set()
                        else:
                            # 服务器返回错误，清除本地进度并重新初始化
                            self._clear_progress()
                            self._upload_id = None
                            self._uploaded_chunks = set()
                    except Exception:
                        # 查询失败，清除本地进度并重新初始化（避免使用过期的会话）
                        self._clear_progress()
                        self._upload_id = None
                        self._uploaded_chunks = set()
            
            # 如果没有上传ID，初始化上传
            if not self._upload_id:
                # 初始化上传
                init_data = {
                    "filename": self._file_path.name,
                    "platform": self._platform,
                    "version": self._version,
                    "total_size": file_size
                }
                
                init_response = httpx.post(
                    f"{self._upload_api_url}/init",
                    data=init_data,
                    timeout=30.0
                )
                
                if init_response.status_code != 200:
                    self.signals.error.emit(f"初始化上传失败：HTTP {init_response.status_code}")
                    return
                
                init_result = init_response.json()
                if init_result.get("status") != "success":
                    self.signals.error.emit(f"初始化上传失败：{init_result.get('message', '未知错误')}")
                    return
                
                self._upload_id = init_result["upload_id"]
                self._total_chunks = init_result["total_chunks"]
                self._chunk_size = init_result.get("chunk_size", self._chunk_size)
            
            # 计算总分片数（如果还没有）
            if self._total_chunks == 0:
                self._total_chunks = (file_size + self._chunk_size - 1) // self._chunk_size
            
            # 上传所有分片
            with open(self._file_path, "rb") as f:
                uploaded_size = 0
                
                for chunk_index in range(self._total_chunks):
                    if self._should_stop:
                        return
                    
                    # 跳过已上传的分片
                    if chunk_index in self._uploaded_chunks:
                        # 计算已上传的字节数
                        if chunk_index < self._total_chunks - 1:
                            uploaded_size += self._chunk_size
                        else:
                            # 最后一个分片
                            uploaded_size += file_size - (self._total_chunks - 1) * self._chunk_size
                        # 发送进度更新
                        self.signals.progress.emit(uploaded_size, file_size)
                        self.signals.chunk_progress.emit(chunk_index + 1, self._total_chunks, 100)
                        continue
                    
                    # 读取分片数据
                    f.seek(chunk_index * self._chunk_size)
                    chunk_data = f.read(self._chunk_size)
                    
                    if not chunk_data:
                        break
                    
                    # 上传分片
                    chunk_files = {
                        "chunk": (f"chunk_{chunk_index}", chunk_data, "application/octet-stream")
                    }
                    chunk_data_form = {
                        "upload_id": self._upload_id,
                        "chunk_index": chunk_index
                    }
                    
                    chunk_response = httpx.post(
                        f"{self._upload_api_url}/chunk",
                        files=chunk_files,
                        data=chunk_data_form,
                        timeout=300.0
                    )
                    
                    if self._should_stop:
                        return
                    
                    if chunk_response.status_code != 200:
                        error_msg = f"上传分片 {chunk_index} 失败：HTTP {chunk_response.status_code}"
                        try:
                            error_result = chunk_response.json()
                            if "message" in error_result:
                                error_msg = f"上传分片 {chunk_index} 失败：{error_result['message']}"
                        except:
                            pass
                        self.signals.error.emit(error_msg)
                        return
                    
                    chunk_result = chunk_response.json()
                    if chunk_result.get("status") != "success":
                        self.signals.error.emit(f"上传分片 {chunk_index} 失败：{chunk_result.get('message', '未知错误')}")
                        return
                    
                    # 记录已上传的分片
                    self._uploaded_chunks.add(chunk_index)
                    uploaded_size += len(chunk_data)
                    
                    # 保存进度
                    self._save_progress(self._upload_id, self._uploaded_chunks)
                    
                    # 发送进度更新
                    self.signals.progress.emit(uploaded_size, file_size)
                    self.signals.chunk_progress.emit(chunk_index + 1, self._total_chunks, 100)
            
            # 所有分片上传完成，通知服务器合并
            if self._should_stop:
                return
            
            complete_data = {
                "upload_id": self._upload_id
            }
            
            complete_response = httpx.post(
                f"{self._upload_api_url}/complete",
                data=complete_data,
                timeout=300.0
            )
            
            if complete_response.status_code != 200:
                self.signals.error.emit(f"完成上传失败：HTTP {complete_response.status_code}")
                return
            
            complete_result = complete_response.json()
            if complete_result.get("status") != "success":
                self.signals.error.emit(f"完成上传失败：{complete_result.get('message', '未知错误')}")
                return
            
            # 清除进度文件
            self._clear_progress()
            
            # 发送完成信号
            download_url = complete_result.get("url")
            if download_url:
                self.signals.finished.emit(download_url)
            else:
                self.signals.error.emit("上传成功但未返回URL")
        
        except httpx.TimeoutException:
            self.signals.error.emit("上传超时，请检查网络连接。可以稍后继续上传（断点续传）")
        except Exception as e:
            self.signals.error.emit(f"上传失败：{type(e).__name__}: {e}")


class _VersionWorker(QRunnable):
    """后台线程：获取版本列表"""
    def __init__(self, client_type: Optional[str] = None):
        super().__init__()
        self.signals = _VersionWorkerSignals()
        self._client_type = client_type

    @Slot()
    def run(self) -> None:
        # 检查登录状态（版本升级除外）
        if not AdminApiClient.is_logged_in():
            return
        
        try:
            client = AdminApiClient.from_config()
            params = {}
            if self._client_type:
                params["client_type"] = self._client_type
            data = client._get("/admin/api/versions", params=params if params else None)
            if isinstance(data, dict) and "items" in data:
                self.signals.finished.emit(data["items"])
            else:
                self.signals.finished.emit([])
        except Exception as e:
            self.signals.error.emit(f"加载版本列表失败：{e}")


class VersionEditDialog(QDialog):
    """版本编辑对话框"""
    def __init__(self, parent, version_data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self._version_data = version_data
        self._is_edit = version_data is not None
        self._thread_pool = QThreadPool.globalInstance()
        self._upload_worker = None
        self._upload_progress = None
        
        self.setWindowTitle("编辑版本" if self._is_edit else "添加版本")
        self.resize(800, 700)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # 版本号
        version_layout = QHBoxLayout()
        version_label = QLabel("版本号：")
        version_label.setFixedWidth(100)
        self.version_edit = QLineEdit()
        self.version_edit.setPlaceholderText("例如：1.0.0")
        if self._is_edit:
            self.version_edit.setText(version_data.get("version", ""))
            self.version_edit.setReadOnly(True)  # 编辑时不允许修改版本号
        version_layout.addWidget(version_label)
        version_layout.addWidget(self.version_edit)
        layout.addLayout(version_layout)
        
        # 客户端类型
        client_type_layout = QHBoxLayout()
        client_type_label = QLabel("客户端类型：")
        client_type_label.setFixedWidth(100)
        self.client_type_combo = QComboBox()
        self.client_type_combo.addItems(["employee", "admin"])
        if self._is_edit:
            current_type = version_data.get("client_type", "employee")
            index = self.client_type_combo.findText(current_type)
            if index >= 0:
                self.client_type_combo.setCurrentIndex(index)
            self.client_type_combo.setEnabled(False)  # 编辑时不允许修改客户端类型
        client_type_layout.addWidget(client_type_label)
        client_type_layout.addWidget(self.client_type_combo)
        layout.addLayout(client_type_layout)
        
        # 多平台下载地址
        download_label = QLabel("下载地址（多平台，每个平台可配置多个安装包）：")
        download_label.setFont(QFont("Arial", 10, QFont.Bold))
        layout.addWidget(download_label)
        
        # 使用滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(12)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        
        # 存储每个平台的包列表
        self.darwin_packages = []  # List[Dict[str, str]]: [{"name": "...", "url": "..."}, ...]
        self.windows_packages = []
        self.linux_packages = []
        
        # macOS (Darwin) - Apple Silicon 和 Intel
        darwin_frame = QFrame()
        darwin_frame.setProperty("class", "card")
        darwin_frame_layout = QVBoxLayout(darwin_frame)
        darwin_frame_layout.setContentsMargins(12, 12, 12, 12)
        darwin_frame_layout.setSpacing(8)
        
        darwin_title = QLabel("macOS")
        darwin_title.setFont(QFont("Arial", 11, QFont.Bold))
        darwin_frame_layout.addWidget(darwin_title)
        
        self.darwin_container = QWidget()
        self.darwin_layout = QVBoxLayout(self.darwin_container)
        self.darwin_layout.setContentsMargins(0, 0, 0, 0)
        self.darwin_layout.setSpacing(6)
        
        # 加载已有的macOS包
        if self._is_edit:
            download_urls = version_data.get("download_urls") or {}
            if isinstance(download_urls, dict):
                darwin_data = download_urls.get("darwin", [])
                if isinstance(darwin_data, list):
                    self.darwin_packages = darwin_data
                elif isinstance(darwin_data, str):
                    # 兼容旧版本：单个URL
                    self.darwin_packages = [{"name": "macOS", "url": darwin_data}]
        
        # 如果没有包，添加默认的两个
        if not self.darwin_packages:
            self.darwin_packages = [
                {"name": "Apple Silicon", "url": ""},
                {"name": "Intel", "url": ""}
            ]
        
        self._render_darwin_packages()
        darwin_frame_layout.addWidget(self.darwin_container)
        
        add_darwin_btn = QPushButton("+ 添加macOS包")
        add_darwin_btn.setFixedHeight(28)
        add_darwin_btn.clicked.connect(lambda: self._add_package("darwin"))
        darwin_frame_layout.addWidget(add_darwin_btn)
        
        scroll_layout.addWidget(darwin_frame)
        
        # Windows - .exe 和 .msi
        windows_frame = QFrame()
        windows_frame.setProperty("class", "card")
        windows_frame_layout = QVBoxLayout(windows_frame)
        windows_frame_layout.setContentsMargins(12, 12, 12, 12)
        windows_frame_layout.setSpacing(8)
        
        windows_title = QLabel("Windows")
        windows_title.setFont(QFont("Arial", 11, QFont.Bold))
        windows_frame_layout.addWidget(windows_title)
        
        self.windows_container = QWidget()
        self.windows_layout = QVBoxLayout(self.windows_container)
        self.windows_layout.setContentsMargins(0, 0, 0, 0)
        self.windows_layout.setSpacing(6)
        
        # 加载已有的Windows包
        if self._is_edit:
            download_urls = version_data.get("download_urls") or {}
            if isinstance(download_urls, dict):
                windows_data = download_urls.get("windows", [])
                if isinstance(windows_data, list):
                    self.windows_packages = windows_data
                elif isinstance(windows_data, str):
                    # 兼容旧版本：单个URL
                    self.windows_packages = [{"name": "Windows", "url": windows_data}]
        
        # 如果没有包，添加默认的两个
        if not self.windows_packages:
            self.windows_packages = [
                {"name": "Installer (.exe)", "url": ""},
                {"name": "MSI 安装包", "url": ""}
            ]
        
        self._render_windows_packages()
        windows_frame_layout.addWidget(self.windows_container)
        
        add_windows_btn = QPushButton("+ 添加Windows包")
        add_windows_btn.setFixedHeight(28)
        add_windows_btn.clicked.connect(lambda: self._add_package("windows"))
        windows_frame_layout.addWidget(add_windows_btn)
        
        scroll_layout.addWidget(windows_frame)
        
        # Linux - .deb 和 .rpm
        linux_frame = QFrame()
        linux_frame.setProperty("class", "card")
        linux_frame_layout = QVBoxLayout(linux_frame)
        linux_frame_layout.setContentsMargins(12, 12, 12, 12)
        linux_frame_layout.setSpacing(8)
        
        linux_title = QLabel("Linux")
        linux_title.setFont(QFont("Arial", 11, QFont.Bold))
        linux_frame_layout.addWidget(linux_title)
        
        self.linux_container = QWidget()
        self.linux_layout = QVBoxLayout(self.linux_container)
        self.linux_layout.setContentsMargins(0, 0, 0, 0)
        self.linux_layout.setSpacing(6)
        
        # 加载已有的Linux包
        if self._is_edit:
            download_urls = version_data.get("download_urls") or {}
            if isinstance(download_urls, dict):
                linux_data = download_urls.get("linux", [])
                if isinstance(linux_data, list):
                    self.linux_packages = linux_data
                elif isinstance(linux_data, str):
                    # 兼容旧版本：单个URL
                    self.linux_packages = [{"name": "Linux", "url": linux_data}]
        
        # 如果没有包，添加默认的两个
        if not self.linux_packages:
            self.linux_packages = [
                {"name": ".deb (Debian/Ubuntu)", "url": ""},
                {"name": ".rpm (Fedora/RHEL)", "url": ""}
            ]
        
        self._render_linux_packages()
        linux_frame_layout.addWidget(self.linux_container)
        
        add_linux_btn = QPushButton("+ 添加Linux包")
        add_linux_btn.setFixedHeight(28)
        add_linux_btn.clicked.connect(lambda: self._add_package("linux"))
        linux_frame_layout.addWidget(add_linux_btn)
        
        scroll_layout.addWidget(linux_frame)
        scroll_layout.addStretch()
        
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        # 更新内容
        notes_layout = QVBoxLayout()
        notes_label = QLabel("更新内容：")
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("请输入本次更新的内容...")
        self.notes_edit.setMaximumHeight(150)
        if self._is_edit:
            self.notes_edit.setPlainText(version_data.get("release_notes", "") or "")
        notes_layout.addWidget(notes_label)
        notes_layout.addWidget(self.notes_edit)
        layout.addLayout(notes_layout)
        
        # 是否强制升级
        self.force_update_check = QCheckBox("强制升级")
        self.force_update_check.setChecked(True)
        if self._is_edit:
            self.force_update_check.setChecked(version_data.get("is_force_update", True))
        layout.addWidget(self.force_update_check)
        
        # 是否启用
        self.active_check = QCheckBox("启用")
        self.active_check.setChecked(True)
        if self._is_edit:
            self.active_check.setChecked(version_data.get("is_active", True))
        layout.addWidget(self.active_check)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
    
    def _on_save(self):
        """保存版本信息"""
        version = self.version_edit.text().strip()
        if not version:
            QMessageBox.warning(self, "错误", "请输入版本号")
            return
        
        # 验证版本号格式
        parts = version.split('.')
        if len(parts) != 3:
            QMessageBox.warning(self, "错误", "版本号格式错误，应为 x.x.x（例如：1.0.0）")
            return
        for part in parts:
            try:
                int(part)
            except ValueError:
                QMessageBox.warning(self, "错误", "版本号格式错误，应为 x.x.x（例如：1.0.0）")
                return
        
        # 收集多平台下载地址（过滤掉空的包）
        darwin_packages = [p for p in self.darwin_packages if p.get("url", "").strip()]
        windows_packages = [p for p in self.windows_packages if p.get("url", "").strip()]
        linux_packages = [p for p in self.linux_packages if p.get("url", "").strip()]
        
        # 至少需要配置一个平台的下载地址
        if not darwin_packages and not windows_packages and not linux_packages:
            QMessageBox.warning(self, "错误", "请至少配置一个平台的下载地址")
            return
        
        # 构建 download_urls 字典
        download_urls = {}
        if darwin_packages:
            download_urls["darwin"] = darwin_packages
        if windows_packages:
            download_urls["windows"] = windows_packages
        if linux_packages:
            download_urls["linux"] = linux_packages
        
        # 兼容旧版本：使用第一个可用的下载地址作为 download_url
        first_url = ""
        if darwin_packages:
            first_url = darwin_packages[0].get("url", "")
        elif windows_packages:
            first_url = windows_packages[0].get("url", "")
        elif linux_packages:
            first_url = linux_packages[0].get("url", "")
        download_url = first_url
        
        client_type = self.client_type_combo.currentText()
        release_notes = self.notes_edit.toPlainText().strip()
        is_force_update = self.force_update_check.isChecked()
        is_active = self.active_check.isChecked()
        
        try:
            client = AdminApiClient.from_config()
            if self._is_edit:
                # 更新
                payload = {
                    "download_url": download_url,  # 兼容旧版本
                    "download_urls": download_urls if download_urls else None,
                    "release_notes": release_notes if release_notes else None,
                    "is_force_update": is_force_update,
                    "is_active": is_active,
                }
                data = client._put(f"/admin/api/versions/{self._version_data['id']}", payload)
            else:
                # 创建
                payload = {
                    "version": version,
                    "client_type": client_type,
                    "download_url": download_url,  # 兼容旧版本
                    "download_urls": download_urls if download_urls else None,
                    "release_notes": release_notes if release_notes else None,
                    "is_force_update": is_force_update,
                    "is_active": is_active,
                }
                data = client._post("/admin/api/versions", payload)
            
            if isinstance(data, dict) and data.get("status") == "success":
                QMessageBox.information(self, "成功", "保存成功")
                self.accept()
            else:
                message = data.get("message", "保存失败")
                QMessageBox.warning(self, "错误", message)
        except Exception as e:
            handle_api_error(self, e, "保存失败")
    
    def _render_darwin_packages(self):
        """渲染macOS包列表"""
        # 清空现有内容
        while self.darwin_layout.count():
            child = self.darwin_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # 渲染每个包
        for i, pkg in enumerate(self.darwin_packages):
            pkg_layout = QHBoxLayout()
            pkg_layout.setSpacing(6)
            
            name_edit = QLineEdit()
            name_edit.setPlaceholderText("包名称（如：Apple Silicon）")
            name_edit.setText(pkg.get("name", ""))
            name_edit.setFixedWidth(150)
            name_edit.textChanged.connect(lambda text, idx=i: self._update_package_name("darwin", idx, text))
            pkg_layout.addWidget(name_edit)
            
            url_edit = QLineEdit()
            url_edit.setPlaceholderText("下载URL")
            url_edit.setText(pkg.get("url", ""))
            url_edit.textChanged.connect(lambda text, idx=i: self._update_package_url("darwin", idx, text))
            pkg_layout.addWidget(url_edit, 1)
            
            delete_btn = QPushButton("删除")
            delete_btn.setFixedWidth(60)
            delete_btn.setFixedHeight(28)
            delete_btn.clicked.connect(partial(self._remove_package, "darwin", i))
            pkg_layout.addWidget(delete_btn)
            
            upload_btn = QPushButton("上传")
            upload_btn.setFixedWidth(60)
            upload_btn.setFixedHeight(28)
            upload_btn.clicked.connect(partial(self._upload_file, "darwin", i, url_edit))
            pkg_layout.addWidget(upload_btn)
            
            pkg_widget = QWidget()
            pkg_widget.setLayout(pkg_layout)
            self.darwin_layout.addWidget(pkg_widget)
    
    def _render_windows_packages(self):
        """渲染Windows包列表"""
        # 清空现有内容
        while self.windows_layout.count():
            child = self.windows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # 渲染每个包
        for i, pkg in enumerate(self.windows_packages):
            pkg_layout = QHBoxLayout()
            pkg_layout.setSpacing(6)
            
            name_edit = QLineEdit()
            name_edit.setPlaceholderText("包名称（如：Installer (.exe)）")
            name_edit.setText(pkg.get("name", ""))
            name_edit.setFixedWidth(150)
            name_edit.textChanged.connect(lambda text, idx=i: self._update_package_name("windows", idx, text))
            pkg_layout.addWidget(name_edit)
            
            url_edit = QLineEdit()
            url_edit.setPlaceholderText("下载URL")
            url_edit.setText(pkg.get("url", ""))
            url_edit.textChanged.connect(lambda text, idx=i: self._update_package_url("windows", idx, text))
            pkg_layout.addWidget(url_edit, 1)
            
            delete_btn = QPushButton("删除")
            delete_btn.setFixedWidth(60)
            delete_btn.setFixedHeight(28)
            delete_btn.clicked.connect(partial(self._remove_package, "windows", i))
            pkg_layout.addWidget(delete_btn)
            
            upload_btn = QPushButton("上传")
            upload_btn.setFixedWidth(60)
            upload_btn.setFixedHeight(28)
            upload_btn.clicked.connect(partial(self._upload_file, "windows", i, url_edit))
            pkg_layout.addWidget(upload_btn)
            
            pkg_widget = QWidget()
            pkg_widget.setLayout(pkg_layout)
            self.windows_layout.addWidget(pkg_widget)
    
    def _render_linux_packages(self):
        """渲染Linux包列表"""
        # 清空现有内容
        while self.linux_layout.count():
            child = self.linux_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # 渲染每个包
        for i, pkg in enumerate(self.linux_packages):
            pkg_layout = QHBoxLayout()
            pkg_layout.setSpacing(6)
            
            name_edit = QLineEdit()
            name_edit.setPlaceholderText("包名称（如：.deb (Debian/Ubuntu)）")
            name_edit.setText(pkg.get("name", ""))
            name_edit.setFixedWidth(150)
            name_edit.textChanged.connect(lambda text, idx=i: self._update_package_name("linux", idx, text))
            pkg_layout.addWidget(name_edit)
            
            url_edit = QLineEdit()
            url_edit.setPlaceholderText("下载URL")
            url_edit.setText(pkg.get("url", ""))
            url_edit.textChanged.connect(lambda text, idx=i: self._update_package_url("linux", idx, text))
            pkg_layout.addWidget(url_edit, 1)
            
            delete_btn = QPushButton("删除")
            delete_btn.setFixedWidth(60)
            delete_btn.setFixedHeight(28)
            delete_btn.clicked.connect(partial(self._remove_package, "linux", i))
            pkg_layout.addWidget(delete_btn)
            
            upload_btn = QPushButton("上传")
            upload_btn.setFixedWidth(60)
            upload_btn.setFixedHeight(28)
            upload_btn.clicked.connect(partial(self._upload_file, "linux", i, url_edit))
            pkg_layout.addWidget(upload_btn)
            
            pkg_widget = QWidget()
            pkg_widget.setLayout(pkg_layout)
            self.linux_layout.addWidget(pkg_widget)
    
    def _add_package(self, platform: str):
        """添加新包"""
        new_pkg = {"name": "", "url": ""}
        if platform == "darwin":
            self.darwin_packages.append(new_pkg)
            self._render_darwin_packages()
        elif platform == "windows":
            self.windows_packages.append(new_pkg)
            self._render_windows_packages()
        elif platform == "linux":
            self.linux_packages.append(new_pkg)
            self._render_linux_packages()
    
    def _remove_package(self, platform: str, index: int):
        """删除包"""
        if platform == "darwin":
            if 0 <= index < len(self.darwin_packages):
                self.darwin_packages.pop(index)
                self._render_darwin_packages()
        elif platform == "windows":
            if 0 <= index < len(self.windows_packages):
                self.windows_packages.pop(index)
                self._render_windows_packages()
        elif platform == "linux":
            if 0 <= index < len(self.linux_packages):
                self.linux_packages.pop(index)
                self._render_linux_packages()
    
    def _update_package_name(self, platform: str, index: int, name: str):
        """更新包名称"""
        if platform == "darwin":
            if 0 <= index < len(self.darwin_packages):
                self.darwin_packages[index]["name"] = name
        elif platform == "windows":
            if 0 <= index < len(self.windows_packages):
                self.windows_packages[index]["name"] = name
        elif platform == "linux":
            if 0 <= index < len(self.linux_packages):
                self.linux_packages[index]["name"] = name
    
    def _update_package_url(self, platform: str, index: int, url: str):
        """更新包URL"""
        if platform == "darwin":
            if 0 <= index < len(self.darwin_packages):
                self.darwin_packages[index]["url"] = url
        elif platform == "windows":
            if 0 <= index < len(self.windows_packages):
                self.windows_packages[index]["url"] = url
        elif platform == "linux":
            if 0 <= index < len(self.linux_packages):
                self.linux_packages[index]["url"] = url
    
    def _upload_file(self, platform: str, index: int, url_edit: QLineEdit):
        """上传文件（带进度显示）"""
        # 获取版本号
        version = self.version_edit.text().strip()
        if not version:
            QMessageBox.warning(self, "错误", "请先填写版本号")
            return
        
        # 选择文件
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {platform} 安装包文件",
            "",
            "所有文件 (*.*);;DMG文件 (*.dmg);;EXE文件 (*.exe);;DEB文件 (*.deb);;RPM文件 (*.rpm);;ZIP文件 (*.zip);;TAR文件 (*.tar.gz)"
        )
        
        if not file_path:
            return
        
        # 获取文件大小
        try:
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            file_name = os.path.basename(file_path)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法读取文件信息：{e}")
            return
        
        # 创建进度对话框
        self._upload_progress = QProgressDialog(
            f"正在上传文件：{file_name}\n大小：{file_size_mb:.2f} MB",
            "取消",
            0,
            100,
            self
        )
        self._upload_progress.setWindowTitle("文件上传")
        self._upload_progress.setWindowModality(Qt.WindowModal)
        self._upload_progress.setMinimumDuration(0)  # 立即显示
        self._upload_progress.setValue(0)
        self._upload_progress.setAutoClose(False)
        self._upload_progress.setAutoReset(False)
        
        # 从配置读取上传API地址
        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load()
        upload_api_url = cfg.get("upload_api_url", "http://27.0.0.1:8882/api/upload")
        
        # 存储文件大小用于进度计算
        self._upload_file_size = file_size
        
        # 创建上传Worker（支持断点续传）
        self._upload_worker = _UploadWorker(file_path, platform, version, upload_api_url)
        
        # 连接信号
        self._upload_worker.signals.finished.connect(
            lambda url: self._on_upload_finished(url, platform, index, url_edit)
        )
        self._upload_worker.signals.error.connect(self._on_upload_error)
        self._upload_worker.signals.progress.connect(self._on_upload_progress)
        self._upload_worker.signals.chunk_progress.connect(self._on_upload_chunk_progress)
        self._upload_progress.canceled.connect(self._on_upload_canceled)
        
        # 启动上传
        self._thread_pool.start(self._upload_worker)
    
    def _on_upload_progress(self, uploaded_bytes: int, total_bytes: int):
        """更新上传进度（真实进度）"""
        if self._upload_progress and not self._upload_progress.wasCanceled():
            if total_bytes > 0:
                percent = int((uploaded_bytes / total_bytes) * 100)
                # 保留5%给完成阶段
                percent = min(percent, 95)
                self._upload_progress.setValue(percent)
    
    def _on_upload_chunk_progress(self, current_chunk: int, total_chunks: int, chunk_percent: int):
        """更新分片进度（可选，用于显示详细信息）"""
        if self._upload_progress and not self._upload_progress.wasCanceled():
            # 可以在这里更新进度对话框的文本，显示分片信息
            pass
    
    def _on_upload_finished(self, download_url: str, platform: str, index: int, url_edit: QLineEdit):
        """上传完成"""
        if self._upload_progress:
            self._upload_progress.setValue(100)
            self._upload_progress.close()
            self._upload_progress = None
        
        # 将URL填入输入框
        url_edit.setText(download_url)
        # 更新数据
        self._update_package_url(platform, index, download_url)
        
        QMessageBox.information(self, "上传成功", f"文件上传成功！\n\n下载URL：\n{download_url}")
        
        self._upload_worker = None
    
    def _on_upload_error(self, error_msg: str):
        """上传错误"""
        if self._upload_progress:
            self._upload_progress.close()
            self._upload_progress = None
        
        QMessageBox.warning(self, "上传失败", error_msg)
        
        self._upload_worker = None
    
    def _on_upload_canceled(self):
        """取消上传"""
        if self._upload_worker:
            self._upload_worker.stop()
        
        if self._upload_progress:
            self._upload_progress.close()
            self._upload_progress = None
        
        self._upload_worker = None


class VersionView(QWidget):
    def __init__(self):
        super().__init__()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        # 标题
        title = QLabel("版本管理")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        layout.addWidget(title)
        
        # 过滤区域
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)
        
        client_type_label = QLabel("客户端类型：")
        # 使用主题颜色，不设置固定颜色
        self.client_type_combo = QComboBox()
        self.client_type_combo.addItems(["全部", "employee", "admin"])
        
        filter_layout.addWidget(client_type_label)
        filter_layout.addWidget(self.client_type_combo)
        filter_layout.addStretch()
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.reload_from_api)
        filter_layout.addWidget(refresh_btn)
        
        add_btn = QPushButton("添加版本")
        add_btn.clicked.connect(self._on_add_version)
        filter_layout.addWidget(add_btn)
        
        filter_frame.setProperty("class", "card")
        layout.addWidget(filter_frame)
        
        # 表格
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "版本号", "客户端类型", "下载地址", "更新内容", "强制升级", "操作"]
        )
        
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 版本号
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 客户端类型
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 下载地址
        header.setSectionResizeMode(4, QHeaderView.Stretch)           # 更新内容
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 强制升级
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # 操作
        
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        layout.addWidget(self.table)
        
        # 绑定事件
        self.client_type_combo.currentIndexChanged.connect(self._reload_versions)
    
    def reload_from_api(self):
        """外部调用的重新加载方法（用于首次自动加载）"""
        self._reload_versions()
    
    def _reload_versions(self):
        """重新加载版本列表"""
        client_type = None
        if self.client_type_combo.currentIndex() > 0:
            client_type = self.client_type_combo.currentText()
        
        win = self.window()
        show_loading = getattr(win, "show_loading", None)
        if callable(show_loading):
            show_loading("加载版本列表中…")
        
        worker = _VersionWorker(client_type=client_type)
        worker.signals.finished.connect(self._on_versions_loaded)
        worker.signals.error.connect(self._on_versions_error)
        QThreadPool.globalInstance().start(worker)
    
    def _on_versions_loaded(self, items: List[Dict[str, Any]]):
        """版本列表加载完成"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        self.table.setRowCount(len(items))
        
        for i, item in enumerate(items):
            # ID
            self.table.setItem(i, 0, QTableWidgetItem(str(item.get("id", ""))))
            
            # 版本号
            version_item = QTableWidgetItem(item.get("version", ""))
            version_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 1, version_item)
            
            # 客户端类型
            client_type = item.get("client_type", "")
            client_type_text = "员工端" if client_type == "employee" else "管理端"
            client_type_item = QTableWidgetItem(client_type_text)
            client_type_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 2, client_type_item)
            
            # 下载地址（显示多平台信息）
            download_urls = item.get("download_urls") or {}
            if isinstance(download_urls, dict):
                platform_info = []
                for platform, packages in download_urls.items():
                    if isinstance(packages, list):
                        # 新格式：列表
                        pkg_count = len([p for p in packages if p.get("url", "").strip()])
                        if pkg_count > 0:
                            platform_name = {"darwin": "macOS", "windows": "Windows", "linux": "Linux"}.get(platform, platform)
                            platform_info.append(f"{platform_name}({pkg_count}个包)")
                    elif isinstance(packages, str) and packages.strip():
                        # 兼容旧格式：单个URL
                        platform_name = {"darwin": "macOS", "windows": "Windows", "linux": "Linux"}.get(platform, platform)
                        platform_info.append(platform_name)
                display_url = f"已配置：{', '.join(platform_info)}" if platform_info else "未配置"
            else:
                # 兼容旧版本
                download_url = item.get("download_url", "")
                display_url = download_url[:50] + "..." if len(download_url) > 50 else (download_url or "未配置")
            self.table.setItem(i, 3, QTableWidgetItem(display_url))
            
            # 更新内容
            release_notes = item.get("release_notes", "") or ""
            # 如果内容太长，只显示前100个字符
            display_notes = release_notes[:100] + "..." if len(release_notes) > 100 else release_notes
            self.table.setItem(i, 4, QTableWidgetItem(display_notes))
            
            # 强制升级
            is_force = "是" if item.get("is_force_update", False) else "否"
            force_item = QTableWidgetItem(is_force)
            force_item.setTextAlignment(Qt.AlignCenter)
            if is_force == "是":
                force_item.setForeground(Qt.red)
            self.table.setItem(i, 5, force_item)
            
            # 操作
            action_combo = QComboBox()
            action_combo.addItem("选择操作", None)
            action_combo.addItem("编辑", "edit")
            action_combo.addItem("删除", "delete")
            action_combo.setFixedWidth(100)
            action_combo.currentTextChanged.connect(
                lambda text, row=i, data=item: self._on_action_selected(text, row, data, action_combo)
            )
            self.table.setCellWidget(i, 6, action_combo)
    
    def _on_versions_error(self, message: str):
        """版本列表加载失败"""
        win = self.window()
        hide_loading = getattr(win, "hide_loading", None)
        if callable(hide_loading):
            hide_loading()
        
        QMessageBox.warning(self, "错误", message)
    
    def _on_action_selected(self, text: str, row: int, data: Dict[str, Any], combo: QComboBox):
        """操作选择"""
        if text == "选择操作":
            return
        
        if text == "编辑":
            dlg = VersionEditDialog(self, version_data=data)
            if dlg.exec():
                self._reload_versions()
        elif text == "删除":
            reply = QMessageBox.question(
                self, "确认删除", 
                f"确定要删除版本 {data.get('version', '')} 吗？\n（将设置为不启用状态）",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                try:
                    client = AdminApiClient.from_config()
                    result = client._delete(f"/admin/api/versions/{data['id']}")
                    if isinstance(result, dict) and result.get("status") == "success":
                        QMessageBox.information(self, "成功", "删除成功")
                        self._reload_versions()
                    else:
                        message = result.get("message", "删除失败")
                        QMessageBox.warning(self, "错误", message)
                except Exception as e:
                    handle_api_error(self, e, "删除失败")
        
        # 重置下拉框
        combo.setCurrentIndex(0)
    
    def _on_add_version(self):
        """添加版本"""
        dlg = VersionEditDialog(self, version_data=None)
        if dlg.exec():
            self._reload_versions()

