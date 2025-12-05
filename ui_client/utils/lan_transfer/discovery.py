#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网设备发现模块（使用 mDNS/Bonjour）
"""

import socket
import platform
import sys
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener
import threading
import logging

logger = logging.getLogger(__name__)


def _debug_log(message: str):
    """统一的调试输出（已禁用）"""
    pass


@dataclass
class DeviceInfo:
    """设备信息"""
    name: str  # 设备名称（用户名）
    user_id: str  # 用户ID
    ip: str  # IP地址
    port: int  # 端口
    avatar_url: Optional[str] = None  # 头像URL
    device_name: Optional[str] = None  # 设备名称（如：MacBook Pro）


class DeviceDiscovery:
    """设备发现服务"""
    
    SERVICE_TYPE = "_aiperf-transfer._tcp.local."
    
    def __init__(self, on_device_added: Optional[Callable[[DeviceInfo], None]] = None,
                 on_device_removed: Optional[Callable[[str], None]] = None):
        """
        初始化设备发现服务
        
        Args:
            on_device_added: 设备添加时的回调函数
            on_device_removed: 设备移除时的回调函数
        """
        self._zeroconf: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._listener: Optional[_DeviceListener] = None
        self._on_device_added = on_device_added
        self._on_device_removed = on_device_removed
        self._devices: Dict[str, DeviceInfo] = {}  # key: service_name
        self._lock = threading.Lock()
        self._running = False
    
    def start(self):
        """启动设备发现服务"""
        if self._running:
            return
        
        _debug_log("DeviceDiscovery.start() called")
        try:
            self._zeroconf = Zeroconf()
            self._listener = _DeviceListener(
                on_add=self._on_device_found,
                on_remove=self._on_device_lost
            )
            self._browser = ServiceBrowser(
                self._zeroconf,
                self.SERVICE_TYPE,
                self._listener
            )
            _debug_log("ServiceBrowser started for _aiperf-transfer._tcp.local.")
            self._running = True
            logger.info("设备发现服务已启动")
        except Exception as e:
            logger.error(f"启动设备发现服务失败: {e}")
            _debug_log(f"DeviceDiscovery.start() failed: {e}")
            raise
    
    def stop(self):
        """停止设备发现服务"""
        if not self._running:
            return
        
        _debug_log("DeviceDiscovery.stop() called")
        try:
            if self._browser:
                self._browser.cancel()
                self._browser = None
            if self._zeroconf:
                self._zeroconf.close()
                self._zeroconf = None
            self._running = False
            with self._lock:
                self._devices.clear()
            logger.info("设备发现服务已停止")
        except Exception as e:
            logger.error(f"停止设备发现服务失败: {e}")
    
    def _on_device_found(self, service_name: str, service_info: ServiceInfo):
        """设备被发现时的处理"""
        try:
            # 解析服务信息
            addresses = service_info.addresses
            if not addresses:
                return
            
            # 获取IP地址（优先IPv4）
            ip = None
            for addr in addresses:
                addr_str = socket.inet_ntoa(addr)
                if self._is_ipv4(addr_str):
                    ip = addr_str
                    break
            
            if not ip:
                return
            
            port = service_info.port
            
            # 从properties中获取设备信息
            properties = service_info.properties
            if not properties:
                return
            
            # 解析properties（bytes -> str）
            props = {}
            for k, v in properties.items():
                if isinstance(k, bytes):
                    k = k.decode('utf-8')
                if isinstance(v, bytes):
                    v = v.decode('utf-8')
                props[k] = v
            
            name = props.get('name', 'Unknown')
            user_id = props.get('user_id', '')
            avatar_url = props.get('avatar_url')
            device_name = props.get('device_name')
            
            device_info = DeviceInfo(
                name=name,
                user_id=user_id,
                ip=ip,
                port=port,
                avatar_url=avatar_url,
                device_name=device_name
            )
            
            with self._lock:
                self._devices[service_name] = device_info
            
            if self._on_device_added:
                self._on_device_added(device_info)
            
            logger.info(f"发现设备: {name} ({ip}:{port})")
        except Exception as e:
            logger.error(f"处理设备发现事件失败: {e}")
            _debug_log(f"_on_device_found error: {e}")
    
    def _on_device_lost(self, service_name: str):
        """设备丢失时的处理"""
        try:
            with self._lock:
                device_info = self._devices.pop(service_name, None)
            
            if device_info and self._on_device_removed:
                self._on_device_removed(device_info.name)
            
            logger.info(f"设备已离线: {service_name}")
        except Exception as e:
            logger.error(f"处理设备丢失事件失败: {e}")
            _debug_log(f"_on_device_lost error: {e}")
    
    def get_devices(self) -> list[DeviceInfo]:
        """获取当前发现的设备列表"""
        with self._lock:
            return list(self._devices.values())
    
    @staticmethod
    def _is_ipv4(ip: str) -> bool:
        """检查是否为IPv4地址"""
        try:
            socket.inet_aton(ip)
            return True
        except socket.error:
            return False


class _DeviceListener(ServiceListener):
    """设备监听器"""
    
    def __init__(self, on_add: Callable[[str, ServiceInfo], None],
                 on_remove: Callable[[str], None]):
        self._on_add = on_add
        self._on_remove = on_remove
    
    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str):
        """服务添加"""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self._on_add(name, info)
    
    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str):
        """服务移除"""
        self._on_remove(name)
    
    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str):
        """服务更新"""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self._on_add(name, info)


def register_service(name: str, port: int, user_id: str, user_name: str,
                     avatar_url: Optional[str] = None,
                     device_name: Optional[str] = None) -> tuple[Zeroconf, ServiceInfo]:
    """
    注册mDNS服务
    
    Args:
        name: 服务名称（唯一标识）
        port: 服务端口
        user_id: 用户ID
        user_name: 用户名
        avatar_url: 头像URL（可选）
        device_name: 设备名称（可选）
    
    Returns:
        (zeroconf实例, service_info实例)
    """
    # 获取本机IP地址
    local_ip = get_local_ip()
    if not local_ip:
        raise RuntimeError("无法获取本机IP地址")
    
    # 构建properties
    properties = {
        'name': user_name.encode('utf-8'),
        'user_id': str(user_id).encode('utf-8'),
    }
    if avatar_url:
        properties['avatar_url'] = avatar_url.encode('utf-8')
    if device_name:
        properties['device_name'] = device_name.encode('utf-8')
    
    # 创建服务信息
    service_info = ServiceInfo(
        DeviceDiscovery.SERVICE_TYPE,
        f"{name}.{DeviceDiscovery.SERVICE_TYPE}",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties=properties
    )
    
    # 注册服务
    _debug_log(f"Registering AirDrop service: name={name}, ip={local_ip}, port={port}")
    zeroconf = Zeroconf()
    zeroconf.register_service(service_info)
    
    return zeroconf, service_info


def get_local_ip() -> Optional[str]:
    """获取本机局域网IP地址"""
    try:
        # 方法1: 连接到外部地址获取本机IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 不需要真正连接，只是获取路由
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            _debug_log(f"get_local_ip(): method1 got {ip}")
            return ip
        finally:
            s.close()
    except Exception:
        pass
    
    try:
        # 方法2: 遍历网络接口
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        # 检查是否为本地回环地址
        if ip.startswith('127.'):
            _debug_log("get_local_ip(): method2 returned loopback address, ignoring")
            return None
        _debug_log(f"get_local_ip(): method2 got {ip}")
        return ip
    except Exception:
        pass
    
    _debug_log("get_local_ip(): failed to determine LAN IP")
    return None

