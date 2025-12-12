#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局域网设备发现模块（使用 mDNS/Bonjour）
"""

import socket
import platform
import sys
import time
from typing import Dict, Optional, Callable
from dataclasses import dataclass
try:
    from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener, IPVersion  # type: ignore
except Exception:  # pragma: no cover
    from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener  # type: ignore
    IPVersion = None  # type: ignore
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
    group_id: Optional[str] = None  # 组ID（由客户端广播）
    discover_scope: Optional[str] = None  # 可被发现范围：all/group/none


class DeviceDiscovery:
    """设备发现服务"""
    
    SERVICE_TYPE = "_aiperf-transfer._tcp.local."
    
    def __init__(self, on_device_added: Optional[Callable[[DeviceInfo], None]] = None,
                 on_device_removed: Optional[Callable[[str, str, str], None]] = None,
                 local_user_id: Optional[str] = None,
                 local_ip: Optional[str] = None,
                 zeroconf: Optional[Zeroconf] = None):
        """
        初始化设备发现服务
        
        Args:
            on_device_added: 设备添加时的回调函数
            on_device_removed: 设备移除时的回调函数 (user_id, ip, name)
        """
        self._zeroconf: Optional[Zeroconf] = zeroconf
        # 是否由 DeviceDiscovery 自己创建并拥有 zeroconf（共享实例时必须为 False，避免重复 close）
        self._own_zeroconf: bool = zeroconf is None
        self._browser: Optional[ServiceBrowser] = None
        self._listener: Optional[_DeviceListener] = None
        self._on_device_added = on_device_added
        self._on_device_removed = on_device_removed
        self._devices: Dict[str, DeviceInfo] = {}  # key: service_name
        self._device_last_seen: Dict[str, float] = {}  # key: service_name, value: timestamp
        self._device_miss_count: Dict[str, int] = {}
        self._cleanup_timer: Optional[threading.Timer] = None
        # 快速检测但带防抖：TTL 15s，周期 5s，连续 miss>=2 才移除
        self._device_ttl = 15  # seconds
        self._cleanup_interval = 5  # seconds
        self._local_user_id = str(local_user_id) if local_user_id is not None else None
        self._local_ip = local_ip
        self._lock = threading.Lock()
        self._running = False
        # 记录上一次已打印的设备列表签名，避免 get_devices 被 UI 轮询时刷屏
        # 只在设备列表发生变化（新增/下线/信息变更）时才打印一次概要
        self._last_logged_devices_sig: Optional[tuple[tuple[str, str, str, int, str, str, str], ...]] = None
    
    def start(self):
        """启动设备发现服务"""
        if self._running:
            return
        
        _debug_log("DeviceDiscovery.start() called")
        try:
            if self._zeroconf is None:
                # Win11 上优先 IPv4Only，减少 IPv6/多网卡/驱动导致的底层不稳定
                try:
                    if platform.system() == "Windows" and IPVersion is not None:
                        self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
                    else:
                        self._zeroconf = Zeroconf()
                except Exception:
                    self._zeroconf = Zeroconf()
                self._own_zeroconf = True
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
            self._schedule_cleanup()
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
            if self._zeroconf and self._own_zeroconf:
                self._zeroconf.close()
                self._zeroconf = None
            if self._cleanup_timer:
                self._cleanup_timer.cancel()
                self._cleanup_timer = None
            self._running = False
            with self._lock:
                self._devices.clear()
                self._device_last_seen.clear()
                self._device_miss_count.clear()
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
            group_id = props.get('group_id')
            discover_scope = props.get('discover_scope')
            
            device_info = DeviceInfo(
                name=name,
                user_id=user_id,
                ip=ip,
                port=port,
                avatar_url=avatar_url,
                device_name=device_name,
                group_id=group_id,
                discover_scope=discover_scope,
            )
            
            # 使用 user_id + ip 作为唯一标识，支持同一账号多个设备
            # 因为 service_name 可能相同（如果主机名相同），导致设备被覆盖
            device_unique_key = f"{user_id}::{ip}"
            
            logger.info(f"[Discovery] Device found: service_name={service_name}, user_id={user_id}, ip={ip}, name={name}")
            
            should_add = False
            with self._lock:
                self._device_last_seen[service_name] = time.time()
                self._device_miss_count[service_name] = 0
                # 检查是否已存在相同的设备（user_id + ip）
                existing_device = None
                existing_service_name = None
                for existing_sn, existing_di in self._devices.items():
                    if existing_di.user_id == user_id and existing_di.ip == ip:
                        existing_device = existing_di
                        existing_service_name = existing_sn
                        logger.info(f"[Discovery] Device already exists: user_id={user_id}, ip={ip}, existing_service_name={existing_service_name}, new_service_name={service_name}")
                        break
                
                if existing_device is None:
                    # 新设备，添加
                    self._devices[service_name] = device_info
                    should_add = True
                    logger.info(f"[Discovery] Adding new device: service_name={service_name}, user_id={user_id}, ip={ip}")
                else:
                    # 设备已存在，更新属性并刷新映射（scope/group 变化需要同步）
                    if existing_service_name != service_name:
                        self._devices.pop(existing_service_name, None)
                        logger.info(f"[Discovery] Updating device service_name: {existing_service_name} -> {service_name}")
                    self._devices[service_name] = device_info
                    should_add = True  # 触发回调以让上层刷新显示（scope/group 可能改变）
            
            if should_add and self._on_device_added:
                logger.info(f"[Discovery] Calling _on_device_added callback for: user_id={user_id}, ip={ip}")
                self._on_device_added(device_info)
            elif not should_add:
                logger.info(f"[Discovery] Skipping _on_device_added callback (device already exists): user_id={user_id}, ip={ip}")
            
            logger.info(f"发现设备: {name} ({ip}:{port})")
        except Exception as e:
            logger.error(f"处理设备发现事件失败: {e}")
            _debug_log(f"_on_device_found error: {e}")
    
    def _on_device_lost(self, service_name: str):
        """设备丢失时的处理"""
        try:
            with self._lock:
                self._device_miss_count.pop(service_name, None)
                device_info = self._devices.pop(service_name, None)
            
            if device_info and self._on_device_removed:
                # 使用 user_id + ip 作为唯一标识来移除设备
                # 需要移除所有相同 user_id + ip 的设备（可能有多个 service_name 映射）
                device_unique_key = f"{device_info.user_id}::{device_info.ip}"
                # 移除所有匹配的设备
                service_names_to_remove = []
                for sn, di in list(self._devices.items()):
                    if f"{di.user_id}::{di.ip}" == device_unique_key:
                        service_names_to_remove.append(sn)
                for sn in service_names_to_remove:
                    self._devices.pop(sn, None)
                
                # 传递 user_id, ip, name 以便上层使用 user_id + ip 进行匹配
                self._on_device_removed(device_info.user_id, device_info.ip, device_info.name)
            
            logger.info(f"设备已离线: {service_name}")
        except Exception as e:
            logger.error(f"处理设备丢失事件失败: {e}")
            _debug_log(f"_on_device_lost error: {e}")
        finally:
            with self._lock:
                self._device_last_seen.pop(service_name, None)

    def _schedule_cleanup(self):
        """周期性清理超时设备（防止异常下线未发送 goodbye 时残留）"""
        if not self._running:
            return
        def cleanup():
            try:
                now = time.time()
                stale = []
                with self._lock:
                    for sn, last in list(self._device_last_seen.items()):
                        if now - last > self._device_ttl:
                            if self._is_self_service(sn):
                                continue
                            # 尝试主动刷新一次服务信息，若仍不可得再计入 miss
                            refreshed = False
                            try:
                                if self._zeroconf:
                                    info = self._zeroconf.get_service_info(self.SERVICE_TYPE, sn)
                                    if info and info.addresses:
                                        self._device_last_seen[sn] = now
                                        self._device_miss_count[sn] = 0
                                        refreshed = True
                            except Exception:
                                pass
                            if refreshed:
                                continue
                            
                            miss = self._device_miss_count.get(sn, 0) + 1
                            self._device_miss_count[sn] = miss
                            # 连续 2 次超时才标记离线，降低误判
                            if miss >= 2:
                                stale.append(sn)
                for sn in stale:
                    self._on_device_lost(sn)
            finally:
                if self._running:
                    self._schedule_cleanup()
        timer = threading.Timer(self._cleanup_interval, cleanup)
        timer.daemon = True
        timer.start()
        self._cleanup_timer = timer
    
    def mark_unreachable(self, user_id: str, ip: str):
        """主动标记某设备不可达，触发离线处理"""
        targets = []
        with self._lock:
            for sn, di in list(self._devices.items()):
                if di.user_id == str(user_id) and di.ip == ip:
                    targets.append(sn)
        for sn in targets:
            self._on_device_lost(sn)

    def get_devices(self) -> list[DeviceInfo]:
        """获取当前发现的设备列表"""
        # 注意：get_devices 可能被 UI 定时调用（轮询刷新列表），这里必须避免每次都打印日志
        # 仅当设备列表签名发生变化时（新增/下线/信息变更）才打印一次当前列表
        changed = False
        with self._lock:
            devices = list(self._devices.values())
            sig = tuple(sorted(
                (
                    str(d.user_id),
                    str(d.ip),
                    str(d.name),
                    int(d.port),
                    str(d.group_id or ""),
                    str(d.discover_scope or ""),
                    str(d.device_name or ""),
                )
                for d in devices
            ))
            if sig != self._last_logged_devices_sig:
                self._last_logged_devices_sig = sig
                changed = True
        
        if changed:
            logger.info(f"[Discovery] devices changed: {len(devices)} devices")
            for device in sorted(devices, key=lambda x: (str(x.user_id), str(x.ip), str(x.name))):
                logger.info(f"[Discovery]   - {device.name} (user_id={device.user_id}, ip={device.ip})")
        return devices
    
    def _is_self_service(self, service_name: str) -> bool:
        """判断给定 service 是否为本机发布的服务"""
        try:
            device = self._devices.get(service_name)
            if not device:
                return False
            if self._local_user_id and device.user_id == self._local_user_id:
                return True
            if self._local_ip and device.ip == self._local_ip:
                return True
            return False
        except Exception:
            return False
    
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
                     device_name: Optional[str] = None,
                     group_id: Optional[str] = None,
                     discover_scope: Optional[str] = None,
                     zeroconf: Optional[Zeroconf] = None) -> tuple[Zeroconf, ServiceInfo]:
    """
    注册mDNS服务
    
    Args:
        name: 服务名称（唯一标识）
        port: 服务端口
        user_id: 用户ID
        user_name: 用户名
        avatar_url: 头像URL（可选）
        device_name: 设备名称（可选）
        group_id: 组ID（可选，由客户端在登录后提供）
        discover_scope: 可被发现范围（all/group/none）
    
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
    if group_id:
        properties['group_id'] = str(group_id).encode('utf-8')
    if discover_scope:
        properties['discover_scope'] = str(discover_scope).encode('utf-8')
    
    # 创建服务信息
    service_info = ServiceInfo(
        DeviceDiscovery.SERVICE_TYPE,
        f"{name}.{DeviceDiscovery.SERVICE_TYPE}",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties=properties
    )
    
    # 注册服务（允许复用共享 Zeroconf，避免多条 _run_loop 线程导致 Windows/Qt6 原生崩溃）
    _debug_log(f"Registering AirDrop service: name={name}, ip={local_ip}, port={port}")
    if zeroconf is not None:
        zc = zeroconf
    else:
        # Win11 上优先 IPv4Only，减少 IPv6/多网卡/驱动导致的底层不稳定
        try:
            if platform.system() == "Windows" and IPVersion is not None:
                zc = Zeroconf(ip_version=IPVersion.V4Only)
            else:
                zc = Zeroconf()
        except Exception:
            zc = Zeroconf()
    zc.register_service(service_info)
    
    return zc, service_info


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

