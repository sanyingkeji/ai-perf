#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输管理器（整合发现、服务器、客户端）
"""

import logging
import platform
import sys
from typing import Optional, Callable, Dict
from pathlib import Path
from PySide6.QtCore import QObject, Signal

from .discovery import DeviceDiscovery, DeviceInfo, register_service, get_local_ip
from .server import TransferServer
from .client import TransferClient

logger = logging.getLogger(__name__)


def _debug_log(message: str):
    """统一的隔空投送调试输出（已禁用）"""
    pass
    pass


class TransferManager(QObject):
    """文件传输管理器"""
    
    # 信号
    device_added = Signal(DeviceInfo)  # 设备添加
    device_removed = Signal(str)  # 设备移除（设备名称）
    transfer_request_received = Signal(str, str, str, str, int, str, int)  # 收到传输请求 (request_id, sender_name, sender_id, filename, file_size, sender_ip, sender_port)
    file_received = Signal(Path, int, str)  # 文件接收 (save_path, file_size, original_filename)
    transfer_progress = Signal(str, int, int)  # 传输进度 (target_name, uploaded, total)
    receive_progress = Signal(str, int, int)  # 接收进度 (request_id, received, total)
    transfer_completed = Signal(str, bool, str)  # 传输完成 (target_name, success, message)
    
    def __init__(self, user_id: str, user_name: str, avatar_url: Optional[str] = None,
                 group_id: Optional[str] = None, save_dir: Optional[Path] = None, port: int = 8765):
        """
        初始化传输管理器
        
        Args:
            user_id: 用户ID
            user_name: 用户名
            avatar_url: 头像URL
            save_dir: 文件保存目录
            port: 服务器端口
        """
        super().__init__()
        self._user_id = user_id
        self._user_name = user_name
        self._avatar_url = avatar_url
        self._group_id = group_id
        self._save_dir = save_dir or (Path.home() / "Downloads")
        self._port = port
        
        # 获取设备名称
        self._device_name = self._get_device_name()
        
        # 组件
        self._discovery: Optional[DeviceDiscovery] = None
        self._server: Optional[TransferServer] = None
        self._client = TransferClient(port=self._port)
        self._zeroconf = None
        self._service_info = None
        self._local_ip = None  # 当前设备的 IP 地址，用于过滤自己
        
        self._running = False
    
    def start(self):
        """启动传输服务"""
        if self._running:
            return
        
        _debug_log("TransferManager.start() called")
        try:
            # 启动HTTP服务器
            _debug_log(f"Starting TransferServer on port {self._port}, save_dir={self._save_dir}")
            self._server = TransferServer(
                port=self._port,
                save_dir=self._save_dir,
                on_transfer_request=self._on_transfer_request,
                on_file_received=self._on_file_received,
                on_receive_progress=self._on_receive_progress
            )
            self._server.start()
            
            # 注册mDNS服务
            # 使用 user_id + platform.node() + ip 确保唯一性，支持同一账号多个设备
            local_ip = get_local_ip()
            self._local_ip = local_ip  # 保存当前设备的 IP，用于过滤自己
            service_name = f"aiperf-{self._user_id}-{platform.node()}-{local_ip.replace('.', '-')}"
            _debug_log(f"Registering mDNS service {service_name} (user={self._user_name}, ip={local_ip})")
            logger.info(f"[TransferManager] Registering service: service_name={service_name}, user_id={self._user_id}, ip={local_ip}")
            self._zeroconf, self._service_info = register_service(
                name=service_name,
                port=self._port,
                user_id=self._user_id,
                user_name=self._user_name,
                avatar_url=self._avatar_url,
                device_name=self._device_name,
                group_id=self._group_id
            )
            
            # 启动设备发现
            _debug_log("Starting DeviceDiscovery...")
            self._discovery = DeviceDiscovery(
                on_device_added=self._on_device_added,
                on_device_removed=self._on_device_removed
            )
            self._discovery.start()
            
            self._running = True
            logger.info("文件传输管理器已启动")
            _debug_log("TransferManager started successfully")
        except Exception as e:
            logger.error(f"启动文件传输管理器失败: {e}")
            _debug_log(f"TransferManager.start() failed: {e}")
            raise
    
    def stop(self):
        """停止传输服务"""
        if not self._running:
            return
        
        _debug_log("TransferManager.stop() called")
        try:
            if self._discovery:
                self._discovery.stop()
                self._discovery = None
            
            if self._zeroconf and self._service_info:
                self._zeroconf.unregister_service(self._service_info)
                self._zeroconf.close()
                self._zeroconf = None
                self._service_info = None
            
            if self._server:
                self._server.stop()
                self._server = None
            
            self._running = False
            logger.info("文件传输管理器已停止")
            _debug_log("TransferManager stopped")
        except Exception as e:
            logger.error(f"停止文件传输管理器失败: {e}")
            _debug_log(f"TransferManager.stop() failed: {e}")
    
    def get_devices(self) -> list[DeviceInfo]:
        """获取发现的设备列表"""
        if not self._discovery:
            return []
        return self._discovery.get_devices()
    
    def send_file(self, file_path: Path, target_device: DeviceInfo,
                  on_progress: Optional[Callable[[int, int], None]] = None):
        """
        发送文件到目标设备（两步传输）
        
        Args:
            file_path: 文件路径
            target_device: 目标设备信息
            on_progress: 进度回调 (uploaded, total)
        """
        def progress_callback(uploaded: int, total: int):
            if on_progress:
                on_progress(uploaded, total)
            self.transfer_progress.emit(target_device.name, uploaded, total)
        
        def send_in_thread():
            # 第一步：发送传输请求
            request_result = self._client.send_transfer_request(
                file_path=file_path,
                target_ip=target_device.ip,
                target_port=target_device.port,
                sender_name=self._user_name,
                sender_id=self._user_id
            )
            
            if not request_result["success"]:
                self.transfer_completed.emit(
                    target_device.name,
                    False,
                    request_result["message"]
                )
                return
            
            request_id = request_result["request_id"]
            
            # 等待用户确认（这里需要等待，但实际应该由UI处理）
            # 暂时等待5秒，实际应该由UI调用confirm_transfer
            import time
            time.sleep(1)  # 给接收端一点时间处理请求
            
            # 检查请求状态（这里简化处理，实际应该由UI控制）
            # 第二步：确认传输（假设接受，实际应该由接收端UI决定）
            # 注意：这里不应该自动确认，应该等待接收端响应
            # 暂时跳过确认步骤，直接传输（实际应该由接收端UI调用confirm_transfer）
            
            # 第三步：传输文件
            result = self._client.send_file(
                file_path=file_path,
                target_ip=target_device.ip,
                target_port=target_device.port,
                request_id=request_id,
                on_progress=progress_callback
            )
            self.transfer_completed.emit(
                target_device.name,
                result["success"],
                result["message"]
            )
        
        # 在后台线程中发送
        import threading
        thread = threading.Thread(target=send_in_thread, daemon=True)
        thread.start()
    
    def send_transfer_request(self, file_path: Path, target_device: DeviceInfo) -> dict:
        """
        发送传输请求（第一步）
        
        Returns:
            {
                "success": bool,
                "request_id": str,
                "message": str
            }
        """
        return self._client.send_transfer_request(
            file_path=file_path,
            target_ip=target_device.ip,
            target_port=target_device.port,
            sender_name=self._user_name,
            sender_id=self._user_id
        )
    
    def confirm_transfer_request(self, request_id: str, target_ip: str, target_port: int, accepted: bool) -> dict:
        """
        确认传输请求（由接收端调用）
        
        Args:
            request_id: 请求ID
            target_ip: 目标设备IP（发送端IP）
            target_port: 目标设备端口
            accepted: 是否接受
        
        Returns:
            {
                "success": bool,
                "message": str
            }
        """
        return self._client.confirm_transfer(request_id, target_ip, target_port, accepted)
    
    def send_file_after_confirm(self, file_path: Path, target_device: DeviceInfo, request_id: str,
                                 on_progress: Optional[Callable[[int, int], None]] = None):
        """
        确认后发送文件（第三步）
        
        Args:
            file_path: 文件路径
            target_device: 目标设备信息
            request_id: 请求ID
            on_progress: 进度回调
        """
        import sys
        def progress_callback(uploaded: int, total: int):
            if on_progress:
                on_progress(uploaded, total)
            self.transfer_progress.emit(target_device.name, uploaded, total)
        
        def send_in_thread():
            try:
                result = self._client.send_file(
                    file_path=file_path,
                    target_ip=target_device.ip,
                    target_port=target_device.port,
                    request_id=request_id,
                    on_progress=progress_callback
                )
                self.transfer_completed.emit(
                    target_device.name,
                    result["success"],
                    result["message"]
                )
            except Exception as e:
                self.transfer_completed.emit(
                    target_device.name,
                    False,
                    f"发送失败: {str(e)}"
                )
        
        import threading
        thread = threading.Thread(target=send_in_thread, daemon=True)
        thread.start()
    
    def _on_device_added(self, device: DeviceInfo):
        """设备添加回调"""
        # 过滤掉自己（相同 user_id 且相同 IP），但保留同一账号的其他设备（相同 user_id 但不同 IP）
        if device.user_id == self._user_id and device.ip == self._local_ip:
            logger.info(f"[TransferManager] Ignoring self device: {device.name} ({device.ip}) user_id={device.user_id}")
            _debug_log(f"Ignoring self device discovery: {device}")
            return
        
        logger.info(f"[TransferManager] Device discovered: {device.name} ({device.ip}:{device.port}) user_id={device.user_id}, current_user_id={self._user_id}, local_ip={self._local_ip}")
        _debug_log(f"Discovered device: {device.name} ({device.ip}:{device.port}) user_id={device.user_id}")
        self.device_added.emit(device)
    
    def _on_device_removed(self, device_name: str):
        """设备移除回调"""
        _debug_log(f"Device removed: {device_name}")
        self.device_removed.emit(device_name)
    
    def _on_transfer_request(self, request_id: str, sender_name: str, sender_id: str,
                             filename: str, file_size: int, sender_ip: str = None, sender_port: int = None):
        """传输请求回调"""
        self.transfer_request_received.emit(request_id, sender_name, sender_id, filename, file_size, sender_ip or "", sender_port or 8765)
    
    def _on_receive_progress(self, request_id: str, received: int, total: int):
        """接收进度回调"""
        self.receive_progress.emit(request_id, received, total)
    
    def _on_file_received(self, save_path: Path, file_size: int, original_filename: str):
        """文件接收回调"""
        self.file_received.emit(save_path, file_size, original_filename)
    
    def accept_transfer(self, request_id: str, target_ip: str, target_port: int) -> dict:
        """接受传输请求"""
        return self._client.confirm_transfer(request_id, target_ip, target_port, True)
    
    def reject_transfer(self, request_id: str, target_ip: str, target_port: int) -> dict:
        """拒绝传输请求"""
        return self._client.confirm_transfer(request_id, target_ip, target_port, False)
    
    @staticmethod
    def _get_device_name() -> str:
        """获取设备名称"""
        system = platform.system()
        if system == "Darwin":
            # macOS
            try:
                import subprocess
                result = subprocess.run(
                    ['scutil', '--get', 'ComputerName'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except:
                pass
            return f"{platform.node()}'s Mac"
        elif system == "Windows":
            return platform.node()
        else:
            return platform.node()
    
    @property
    def is_running(self) -> bool:
        """检查是否运行中"""
        return self._running

