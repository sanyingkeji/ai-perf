#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输管理器（整合发现、服务器、客户端）
"""

import logging
import platform
import sys
import os
import queue
from typing import Optional, Callable, Dict
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QTimer, Slot, QMetaObject, Qt, Q_ARG, QMetaObject, Qt
try:
    from shiboken6 import isValid as _qt_is_valid
except Exception:  # pragma: no cover
    _qt_is_valid = None

from utils.config_manager import ConfigManager
try:
    # IPVersion 在较新 zeroconf 版本存在；Win11 上强制 IPv4-only 可显著降低底层崩溃概率
    from zeroconf import Zeroconf, IPVersion  # type: ignore
except Exception:  # pragma: no cover
    from zeroconf import Zeroconf  # type: ignore
    IPVersion = None  # type: ignore
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
    # Windows/Qt6：避免使用自定义 Python 类型做 Signal 签名，容易触发元类型封装问题甚至原生崩溃
    device_added = Signal(object)  # 设备添加（DeviceInfo）
    device_removed = Signal(str, str, str)  # 设备移除（user_id, ip, name）
    transfer_request_received = Signal(str, str, str, str, int, str, int)  # 收到传输请求 (request_id, sender_name, sender_id, filename, file_size, sender_ip, sender_port)
    file_received = Signal(str, int, str)  # 文件接收 (save_path_str, file_size, original_filename)
    transfer_progress = Signal(str, int, int)  # 传输进度 (target_name, uploaded, total)
    receive_progress = Signal(str, int, int)  # 接收进度 (request_id, received, total)
    transfer_completed = Signal(str, bool, str)  # 传输完成 (target_name, success, message)
    
    def __init__(self, user_id: str, user_name: str, avatar_url: Optional[str] = None,
                 group_id: Optional[str] = None, discover_scope: str = "all",
                 save_dir: Optional[Path] = None, port: int = 8765):
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
        self._discover_scope = discover_scope or "all"
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
        self._service_name: Optional[str] = None

        # 跨线程事件投递到 Qt 主线程（避免在非主线程 emit Qt 信号导致 Qt6Core.dll 0xc0000005）
        # 说明：DeviceDiscovery/TransferServer 的回调都在后台线程触发，必须先切回 QObject 所在线程再 emit。
        self._ui_event_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._ui_dispatch_enabled: bool = True
        try:
            self.destroyed.connect(self._on_destroyed)  # type: ignore[attr-defined]
        except Exception:
            pass
        # UI 线程任务调度器：周期性 drain 队列（避免从 Python Thread 里 invokeMethod/emit signal）
        self._ui_dispatch_timer = QTimer(self)
        self._ui_dispatch_timer.setInterval(20)
        self._ui_dispatch_timer.timeout.connect(self._drain_ui_events)
        self._ui_dispatch_timer.start()

        # 可选禁用 mDNS/zeroconf（用于排查 Windows 上的 Access Violation）
        # AI_PERF_DISABLE_MDNS=1      -> 同时关闭广播+发现
        # AI_PERF_DISABLE_MDNS_REGISTER=1 -> 仅关闭广播（仍能发现别人）
        # AI_PERF_DISABLE_MDNS_BROWSE=1   -> 仅关闭发现（仍能被别人发现）
        cfg_mdns_enabled = True
        try:
            cfg = ConfigManager.load()
            cfg_mdns_enabled = bool(cfg.get("airdrop_mdns_enabled", True))
        except Exception:
            cfg_mdns_enabled = True

        self._disable_mdns = (os.environ.get("AI_PERF_DISABLE_MDNS") == "1") or (not cfg_mdns_enabled)
        self._disable_mdns_register = os.environ.get("AI_PERF_DISABLE_MDNS_REGISTER") == "1" or self._disable_mdns
        self._disable_mdns_browse = os.environ.get("AI_PERF_DISABLE_MDNS_BROWSE") == "1" or self._disable_mdns
        
        self._running = False

    def _post_to_ui_thread(self, fn: Callable[[], None]) -> None:
        """
        从任意线程安全地把工作投递到 TransferManager 所在线程（通常是 UI 主线程）。
        """
        if not self._ui_dispatch_enabled:
            return
        # 不在后台线程调用 shiboken6.isValid（Win11/Qt6 环境下有概率引发原生崩溃）；
        # 直接 try/except + destroyed 标志即可。
        try:
            self._ui_event_queue.put_nowait(fn)
        except Exception as e:
            logger.error(f"[TransferManager] enqueue ui event failed: {e}", exc_info=True)
            return

    def _create_zeroconf(self) -> Zeroconf:
        """
        创建 Zeroconf 实例。

        Win11 上启用 IPv4Only 可减少 IPv6/多网卡/驱动相关导致的崩溃概率。
        """
        try:
            if platform.system() == "Windows" and IPVersion is not None:
                return Zeroconf(ip_version=IPVersion.V4Only)
        except Exception:
            pass
        return Zeroconf()

    @Slot()
    def _on_destroyed(self) -> None:
        # QObject 销毁后，禁止继续向其投递事件
        self._ui_dispatch_enabled = False
        try:
            if hasattr(self, "_ui_dispatch_timer") and self._ui_dispatch_timer:
                self._ui_dispatch_timer.stop()
        except Exception:
            pass

    @Slot()
    def _drain_ui_events(self) -> None:
        """
        在 UI 线程执行队列中的事件。
        """
        while True:
            try:
                fn = self._ui_event_queue.get_nowait()
            except queue.Empty:
                break
            except Exception as e:
                logger.error(f"[TransferManager] dequeue ui event failed: {e}", exc_info=True)
                break

            try:
                fn()
            except Exception as e:
                logger.error(f"[TransferManager] ui event execution failed: {e}", exc_info=True)
    
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
            
            # 注册mDNS服务（可通过环境变量禁用以排查底层崩溃）
            local_ip = get_local_ip()
            self._local_ip = local_ip  # 保存当前设备的 IP，用于过滤自己
            # 共享同一个 Zeroconf 实例用于 register + browse，避免产生多条 _run_loop 线程（Win11 上更容易触发 0xc0000005）
            need_mdns = (not self._disable_mdns_register and self._discover_scope != "none") or (not self._disable_mdns_browse)
            if need_mdns and self._zeroconf is None:
                self._zeroconf = self._create_zeroconf()
            # 广播（注册服务）
            if self._disable_mdns_register or self._discover_scope == "none":
                if self._disable_mdns_register:
                    logger.warning("[TransferManager] AI_PERF_DISABLE_MDNS_REGISTER=1，跳过 mDNS 广播（仍可发现他人）")
                else:
                    logger.info("[TransferManager] discover_scope=none, skip mDNS register (仅停止广播)")
                self._service_info = None
                self._service_name = f"aiperf-{self._user_id}-{platform.node()}-{local_ip.replace('.', '-')}"
            else:
                self._service_name = f"aiperf-{self._user_id}-{platform.node()}-{local_ip.replace('.', '-')}"
                _debug_log(f"Registering mDNS service {self._service_name} (user={self._user_name}, ip={local_ip}, scope={self._discover_scope})")
                logger.info(f"[TransferManager] Registering service: service_name={self._service_name}, user_id={self._user_id}, ip={local_ip}, scope={self._discover_scope}")
                self._zeroconf, self._service_info = register_service(
                    name=self._service_name,
                    port=self._port,
                    user_id=self._user_id,
                    user_name=self._user_name,
                    avatar_url=self._avatar_url,
                    device_name=self._device_name,
                    group_id=self._group_id,
                    discover_scope=self._discover_scope,
                    zeroconf=self._zeroconf,
                )
            
            # 发现（浏览服务）
            if self._disable_mdns_browse:
                logger.warning("[TransferManager] AI_PERF_DISABLE_MDNS_BROWSE=1，跳过 mDNS 发现（仅能被他人发现或直连 IP）")
            else:
                _debug_log("Starting DeviceDiscovery...")
                self._discovery = DeviceDiscovery(
                    on_device_added=self._on_device_added,
                    on_device_removed=self._on_device_removed,
                    local_user_id=self._user_id,
                    local_ip=self._local_ip,
                    zeroconf=self._zeroconf,
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
                try:
                    self._discovery.stop()
                except Exception as e:
                    logger.warning(f"[TransferManager] stop discovery failed: {e}")
                self._discovery = None
            
            if self._zeroconf and self._service_info:
                try:
                    self._zeroconf.unregister_service(self._service_info)
                except Exception as e:
                    logger.warning(f"[TransferManager] unregister service failed: {e}")
                self._service_info = None
                self._service_name = None

            # 最后统一关闭共享 Zeroconf（如果存在）
            if self._zeroconf:
                try:
                    self._zeroconf.close()
                except Exception:
                    pass
                self._zeroconf = None
            
            if self._server:
                self._server.stop()
                self._server = None
            
            self._running = False
            logger.info("文件传输管理器已停止")
            _debug_log("TransferManager stopped")
        except Exception as e:
            logger.error(f"停止文件传输管理器失败: {e}")
            _debug_log(f"TransferManager.stop() failed: {e}")

    def set_discover_scope(self, scope: str):
        """
        更新可被发现范围：
        all   -> 正常广播
        group -> 正常广播，标记为仅同组
        none  -> 取消广播（仍可发现别人）
        """
        self._discover_scope = scope or "all"
        if not self._running:
            return
        
        # 1) 如果关闭广播
        if self._discover_scope == "none":
            if self._zeroconf and self._service_info:
                try:
                    self._zeroconf.unregister_service(self._service_info)
                except Exception as e:
                    logger.warning(f"[TransferManager] unregister service failed: {e}")
                self._service_info = None
            return
        
        # 2) 需要广播：先注销旧的，再按新 scope 注册
        try:
            if self._zeroconf and self._service_info:
                self._zeroconf.unregister_service(self._service_info)
        except Exception as e:
            logger.warning(f"[TransferManager] re-register cleanup failed: {e}")
        finally:
            self._service_info = None
        
        # 使用已有信息重注册
        local_ip = self._local_ip or get_local_ip()
        if not local_ip:
            logger.warning("[TransferManager] cannot re-register service: no local ip")
            return
        self._local_ip = local_ip
        if not self._service_name:
            self._service_name = f"aiperf-{self._user_id}-{platform.node()}-{local_ip.replace('.', '-')}"
        
        try:
            if self._zeroconf is None:
                    self._zeroconf = self._create_zeroconf()
            self._zeroconf, self._service_info = register_service(
                name=self._service_name,
                port=self._port,
                user_id=self._user_id,
                user_name=self._user_name,
                avatar_url=self._avatar_url,
                device_name=self._device_name,
                group_id=self._group_id,
                discover_scope=self._discover_scope,
                zeroconf=self._zeroconf,
            )
            logger.info(f"[TransferManager] re-registered service with scope={self._discover_scope}")
        except Exception as e:
            logger.error(f"[TransferManager] re-register service failed: {e}")
    
    def get_devices(self) -> list[DeviceInfo]:
        """获取发现的设备列表"""
        if not self._discovery:
            return []
        devices = self._discovery.get_devices()
        # 过滤“自己”：同 user_id 且同 IP 的设备不应展示
        try:
            local_ip = self._local_ip
            if local_ip:
                return [d for d in devices if not (d.user_id == self._user_id and d.ip == local_ip)]
        except Exception:
            pass
        return devices
    
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
            # 避免在后台线程 emit Qt 信号
            self._post_to_ui_thread(lambda n=target_device.name, u=uploaded, t=total: self.transfer_progress.emit(n, u, t))
        
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
                self._post_to_ui_thread(
                    lambda n=target_device.name, m=request_result["message"]: self.transfer_completed.emit(n, False, m)
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
            self._post_to_ui_thread(
                lambda n=target_device.name, s=result.get("success", False), m=result.get("message", ""): self.transfer_completed.emit(n, s, m)
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
            try:
                if on_progress:
                    on_progress(uploaded, total)
                # 避免在后台线程 emit Qt 信号
                self._post_to_ui_thread(lambda n=target_device.name, u=uploaded, t=total: self.transfer_progress.emit(n, u, t))
            except Exception as e:
                logger.error(f"[TransferManager] 进度回调异常: {e}", exc_info=True)
        
        def send_in_thread():
            try:
                logger.info(f"[TransferManager] 开始发送文件: {file_path} -> {target_device.ip}:{target_device.port}")
                result = self._client.send_file(
                    file_path=file_path,
                    target_ip=target_device.ip,
                    target_port=target_device.port,
                    request_id=request_id,
                    on_progress=progress_callback
                )
                logger.info(f"[TransferManager] 文件发送完成: success={result.get('success')}, message={result.get('message')}")
                self._post_to_ui_thread(
                    lambda n=target_device.name, s=result.get("success", False), m=result.get("message", ""): self.transfer_completed.emit(n, s, m)
                )
            except Exception as e:
                logger.error(f"[TransferManager] 发送文件时发生异常: {e}", exc_info=True)
                self._post_to_ui_thread(
                    lambda n=target_device.name, m=str(e): self.transfer_completed.emit(n, False, f"发送失败: {m}")
                )
        
        import threading
        thread = threading.Thread(target=send_in_thread, daemon=True)
        thread.start()
    
    # 兼容旧代码：保留槽函数名，避免外部 invokeMethod 失败（内部已统一走 _post_to_ui_thread）
    @Slot(str, bool, str)
    def _emit_transfer_completed_slot(self, target_name: str, success: bool, message: str):
        try:
            self.transfer_completed.emit(target_name, success, message)
        except Exception as e:
            logger.error(f"[TransferManager] 发送传输完成信号失败: {e}", exc_info=True)

    @Slot(str, int, int)
    def _emit_transfer_progress_slot(self, target_name: str, uploaded: int, total: int):
        try:
            self.transfer_progress.emit(target_name, uploaded, total)
        except Exception as e:
            logger.error(f"[TransferManager] 发送传输进度信号失败: {e}", exc_info=True)
    
    def _on_device_added(self, device: DeviceInfo):
        """设备添加回调"""
        # 过滤掉自己（相同 user_id 且相同 IP），但保留同一账号的其他设备（相同 user_id 但不同 IP）
        if device.user_id == self._user_id and device.ip == self._local_ip:
            logger.info(f"[TransferManager] Ignoring self device: {device.name} ({device.ip}) user_id={device.user_id}")
            _debug_log(f"Ignoring self device discovery: {device}")
            return
        
        logger.info(f"[TransferManager] Device discovered: {device.name} ({device.ip}:{device.port}) user_id={device.user_id}, current_user_id={self._user_id}, local_ip={self._local_ip}")
        _debug_log(f"Discovered device: {device.name} ({device.ip}:{device.port}) user_id={device.user_id}")
        # DeviceDiscovery 回调通常在非 UI 线程，必须切回 UI 线程再 emit
        self._post_to_ui_thread(lambda d=device: self.device_added.emit(d))
    
    def _on_device_removed(self, user_id: str, ip: str, device_name: str):
        """设备移除回调"""
        _debug_log(f"Device removed: {device_name} (user_id={user_id}, ip={ip})")
        # 过滤掉自己（相同 user_id 且相同 IP），但保留同一账号的其他设备（相同 user_id 但不同 IP）
        # 注意：这里不需要过滤，因为"自己"设备在添加时就被过滤了，不会出现在列表中
        # 但如果是"自己"的另一设备（相同 user_id 但不同 IP），需要正常处理
        self._post_to_ui_thread(lambda uid=user_id, dip=ip, dn=device_name: self.device_removed.emit(uid, dip, dn))
    
    def _on_transfer_request(self, request_id: str, sender_name: str, sender_id: str,
                             filename: str, file_size: int, sender_ip: str = None, sender_port: int = None):
        """传输请求回调"""
        # TransferServer 回调在 HTTPServer 线程
        self._post_to_ui_thread(
            lambda rid=request_id, sn=sender_name, sid=sender_id, fn=filename, fs=file_size, sip=(sender_ip or ""), sp=(sender_port or 8765):
            self.transfer_request_received.emit(rid, sn, sid, fn, fs, sip, sp)
        )
    
    def _on_receive_progress(self, request_id: str, received: int, total: int):
        """接收进度回调"""
        self._post_to_ui_thread(lambda rid=request_id, r=received, t=total: self.receive_progress.emit(rid, r, t))
    
    def _on_file_received(self, save_path: Path, file_size: int, original_filename: str):
        """文件接收回调"""
        self._post_to_ui_thread(
            lambda p=str(save_path), s=file_size, n=original_filename: self.file_received.emit(p, s, n)
        )
    
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

