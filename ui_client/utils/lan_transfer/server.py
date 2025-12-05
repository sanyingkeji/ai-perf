#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输服务器（接收端）
支持两步传输：先请求确认，再传输文件
"""

import os
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, Callable, Dict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

logger = logging.getLogger(__name__)


class TransferRequestHandler(BaseHTTPRequestHandler):
    """文件传输请求处理器"""
    
    def __init__(self, *args, save_dir: Path = None, 
                 on_transfer_request: Optional[Callable] = None,
                 on_file_received: Optional[Callable] = None,
                 on_receive_progress: Optional[Callable[[str, int, int], None]] = None,
                 pending_requests: Dict = None,
                 lock: threading.Lock = None,
                 **kwargs):
        self._save_dir = save_dir or Path.home() / "Downloads"
        self._on_transfer_request = on_transfer_request
        self._on_file_received = on_file_received
        self._on_receive_progress = on_receive_progress
        # 重要：必须使用传入的字典，不能创建新字典
        if pending_requests is None:
            self._pending_requests = {}
        else:
            self._pending_requests = pending_requests
        self._lock = lock  # 共享锁，用于保护 pending_requests
        super().__init__(*args, **kwargs)
    
    def _get_client_ip(self) -> str:
        """获取客户端IP"""
        return self.client_address[0]
    
    def do_POST(self):
        """处理POST请求"""
        try:
            parsed_path = urlparse(self.path)
            logger.info(f"收到POST请求: path={parsed_path.path}")
            
            if parsed_path.path == '/transfer_request':
                logger.info("开始处理传输请求...")
                self._handle_transfer_request()
            elif parsed_path.path == '/transfer_confirm':
                self._handle_transfer_confirm()
            elif parsed_path.path == '/transfer':
                self._handle_file_upload()
            else:
                logger.warning(f"未知的POST路径: {parsed_path.path}")
                self._send_response(404, {"error": "Not found"})
        except Exception as e:
            logger.error(f"处理POST请求失败: {e}", exc_info=True)
            self._send_response(500, {"error": str(e)})
    
    def do_GET(self):
        """处理GET请求"""
        try:
            parsed_path = urlparse(self.path)
            
            if parsed_path.path == '/status':
                self._handle_status()
            elif parsed_path.path == '/transfer_status':
                self._handle_transfer_status()
            else:
                self._send_response(404, {"error": "Not found"})
        except Exception as e:
            logger.error(f"处理GET请求失败: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _handle_transfer_request(self):
        """处理传输请求（第一步：发送文件信息，等待确认）"""
        try:
            logger.info("_handle_transfer_request 开始执行")
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            logger.info(f"请求体长度: {content_length}")
            if content_length == 0:
                logger.warning("请求体为空")
                self._send_response(400, {"error": "Empty request"})
                return
            
            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body.decode('utf-8'))
            logger.info(f"解析请求数据: {request_data}")
            
            # 获取文件信息
            filename = request_data.get('filename', 'unknown_file')
            file_size = request_data.get('file_size', 0)
            sender_name = request_data.get('sender_name', 'Unknown')
            sender_id = request_data.get('sender_id', '')
            
            # 生成请求ID
            request_id = str(uuid.uuid4())
            
            # 获取发送端IP和端口（从请求头或客户端地址）
            sender_ip = self._get_client_ip()
            sender_port = request_data.get('sender_port', 8765)  # 默认端口
            
            # 保存待处理的请求（使用锁保护）
            current_timestamp = time.time()
            request_data_dict = {
                'filename': filename,
                'file_size': file_size,
                'sender_name': sender_name,
                'sender_id': sender_id,
                'sender_ip': sender_ip,
                'sender_port': sender_port,
                'timestamp': current_timestamp,
                'status': 'pending'  # pending, accepted, rejected
            }
            if self._lock:
                with self._lock:
                    self._pending_requests[request_id] = request_data_dict
            else:
                self._pending_requests[request_id] = request_data_dict
            
            logger.info(f"收到传输请求: {request_id}, filename={filename}, timestamp={current_timestamp}, 有效期={TransferServer.REQUEST_EXPIRY_TIME}秒")
            
            # 触发回调（显示通知），传递完整的请求信息包括 sender_ip 和 sender_port
            if self._on_transfer_request:
                try:
                    self._on_transfer_request(
                        request_id=request_id,
                        sender_name=sender_name,
                        sender_id=sender_id,
                        filename=filename,
                        file_size=file_size,
                        sender_ip=sender_ip,
                        sender_port=sender_port
                    )
                except Exception as e:
                    logger.error(f"传输请求回调失败: {e}")
            
            # 返回请求ID
            response = {
                "status": "success",
                "request_id": request_id,
                "message": "Transfer request received"
            }
            self._send_response(200, response)
            
            logger.info(f"收到传输请求: {filename} ({file_size} bytes) from {sender_name}")
        except Exception as e:
            logger.error(f"处理传输请求失败: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _handle_transfer_confirm(self):
        """处理传输确认（第二步：接受或拒绝）"""
        try:
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_response(400, {"error": "Empty request"})
                return
            
            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body.decode('utf-8'))
            
            request_id = request_data.get('request_id')
            accepted = request_data.get('accepted', False)
            
            if not request_id:
                self._send_response(400, {"error": "Missing request_id"})
                return
            
            # 检查请求是否存在且未过期（使用锁保护）
            if self._lock:
                with self._lock:
                    request_info = self._pending_requests.get(request_id)
                    if not request_info:
                        self._send_response(404, {"error": "Request not found or expired"})
                        return
                    
                    # 检查请求是否过期
                    timestamp = request_info.get('timestamp', 0)
                    if time.time() - timestamp > TransferServer.REQUEST_EXPIRY_TIME:
                        # 请求已过期，删除它
                        del self._pending_requests[request_id]
                        self._send_response(410, {"error": "Request expired"})  # 410 Gone
                        return
                    
                    # 更新请求状态
                    request_info['status'] = 'accepted' if accepted else 'rejected'
            else:
                request_info = self._pending_requests.get(request_id)
                if not request_info:
                    self._send_response(404, {"error": "Request not found or expired"})
                    return
                
                # 检查请求是否过期
                timestamp = request_info.get('timestamp', 0)
                if time.time() - timestamp > TransferServer.REQUEST_EXPIRY_TIME:
                    # 请求已过期，删除它
                    del self._pending_requests[request_id]
                    self._send_response(410, {"error": "Request expired"})  # 410 Gone
                    return
                
                # 更新请求状态
                request_info['status'] = 'accepted' if accepted else 'rejected'
            
            # 返回确认结果
            response = {
                "status": "success",
                "accepted": accepted,
                "message": "Accepted" if accepted else "Rejected"
            }
            self._send_response(200, response)
            
            logger.info(f"传输确认: {request_id} - {'接受' if accepted else '拒绝'}")
        except Exception as e:
            logger.error(f"处理传输确认失败: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _handle_file_upload(self):
        """处理文件上传（第三步：实际传输文件）"""
        try:
            # 获取请求ID
            request_id = self.headers.get('X-Request-ID', '')
            
            if not request_id:
                self._send_response(400, {"error": "Missing request ID"})
                return
            
            # 检查请求是否存在（使用锁保护）
            if self._lock:
                with self._lock:
                    request_info = self._pending_requests.get(request_id)
                    if not request_info:
                        self._send_response(404, {"error": "Request not found or expired"})
                        return
                    
                    # 检查请求状态
                    status = request_info.get('status', 'pending')
                    if status != 'accepted':
                        self._send_response(403, {"error": "Request not accepted"})
                        return
                    
                    # 已接受的请求不检查过期（因为正在传输中）
                    # 只有在pending状态时才检查过期
            else:
                request_info = self._pending_requests.get(request_id)
                if not request_info:
                    self._send_response(404, {"error": "Request not found or expired"})
                    return
                
                # 检查请求状态
                status = request_info.get('status', 'pending')
                if status != 'accepted':
                    self._send_response(403, {"error": "Request not accepted"})
                    return
                
                # 已接受的请求不检查过期（因为正在传输中）
                # 只有在pending状态时才检查过期
            
            # 获取Content-Length
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_response(400, {"error": "Empty file"})
                return
            
            # 获取文件名
            filename = request_info['filename']
            
            # 保存文件
            save_path = self._save_dir / filename
            
            # 如果文件已存在，添加序号
            counter = 1
            original_path = save_path
            while save_path.exists():
                stem = original_path.stem
                suffix = original_path.suffix
                save_path = self._save_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            # 读取文件内容并保存（带进度回调）
            # 使用较小的chunk大小（16KB）来更频繁地读取和更新进度
            # 这样可以更及时地反映接收进度，避免与发送端进度差异过大
            bytes_read = 0
            chunk_size = 16 * 1024  # 16KB，更频繁地读取和更新进度
            
            with open(save_path, 'wb') as f:
                while bytes_read < content_length:
                    # 计算本次读取的大小
                    read_size = min(chunk_size, content_length - bytes_read)
                    chunk = self.rfile.read(read_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_read += len(chunk)
                    
                    # 每次读取后立即更新进度，确保及时反映接收状态
                    # 使用较小的chunk可以更频繁地更新，减少与发送端进度的差异
                    if self._on_receive_progress:
                        try:
                            self._on_receive_progress(request_id, bytes_read, content_length)
                        except Exception as e:
                            logger.warning(f"接收进度回调失败: {e}")
            
            # 删除待处理的请求（使用锁保护）
            if self._lock:
                with self._lock:
                    if request_id in self._pending_requests:
                        del self._pending_requests[request_id]
            else:
                if request_id in self._pending_requests:
                    del self._pending_requests[request_id]
            
            # 发送成功响应
            response = {
                "status": "success",
                "filename": save_path.name,
                "path": str(save_path),
                "size": bytes_read
            }
            self._send_response(200, response)
            
            # 触发回调
            if self._on_file_received:
                try:
                    self._on_file_received(save_path, bytes_read, filename)
                except Exception as e:
                    logger.error(f"文件接收回调失败: {e}")
            
            logger.info(f"文件接收成功: {save_path.name} ({bytes_read} bytes)")
        except Exception as e:
            logger.error(f"文件上传处理失败: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _handle_status(self):
        """处理状态查询"""
        response = {"status": "running"}
        self._send_response(200, response)
    
    def _handle_transfer_status(self):
        """处理传输状态查询（用于发送端轮询确认状态）"""
        try:
            # 解析查询参数
            parsed_path = urlparse(self.path)
            query_params = parse_qs(parsed_path.query)
            request_id = query_params.get('request_id', [None])[0]
            
            if not request_id:
                self._send_response(400, {"error": "Missing request_id"})
                return
            
            # 添加调试日志
            logger.debug(f"查询传输状态: request_id={request_id}, 当前请求数={len(self._pending_requests)}")
            all_request_ids = list(self._pending_requests.keys())
            logger.debug(f"当前所有请求ID: {all_request_ids}")
            
            # 检查请求是否存在（使用锁保护）
            if self._lock:
                with self._lock:
                    request_info = self._pending_requests.get(request_id)
                    if not request_info:
                        self._send_response(404, {"error": "Request not found or expired"})
                        return
                    
                    # 检查请求是否过期（但已接受的请求不应该过期，因为正在传输中）
                    timestamp = request_info.get('timestamp', 0)
                    status = request_info.get('status', 'pending')
                    
                    # 如果请求已接受或已拒绝，不检查过期（因为需要让发送端看到状态）
                    if status == 'accepted' or status == 'rejected':
                        response = {
                            "status": status,
                            "request_id": request_id
                        }
                    elif time.time() - timestamp > TransferServer.REQUEST_EXPIRY_TIME:
                        # 只有pending状态的请求才检查过期
                        del self._pending_requests[request_id]
                        self._send_response(410, {"error": "Request expired", "status": "expired"})  # 410 Gone
                        return
                    else:
                        response = {
                            "status": status,  # pending
                            "request_id": request_id
                        }
            else:
                request_info = self._pending_requests.get(request_id)
                if not request_info:
                    self._send_response(404, {"error": "Request not found or expired"})
                    return
                
                # 检查请求是否过期（但已接受的请求不应该过期，因为正在传输中）
                timestamp = request_info.get('timestamp', 0)
                status = request_info.get('status', 'pending')
                
                # 如果请求已接受或已拒绝，不检查过期（因为需要让发送端看到状态）
                if status == 'accepted' or status == 'rejected':
                    response = {
                        "status": status,
                        "request_id": request_id
                    }
                elif time.time() - timestamp > TransferServer.REQUEST_EXPIRY_TIME:
                    # 只有pending状态的请求才检查过期
                    del self._pending_requests[request_id]
                    self._send_response(410, {"error": "Request expired", "status": "expired"})  # 410 Gone
                    return
                else:
                    response = {
                        "status": status,  # pending
                        "request_id": request_id
                    }
            
            self._send_response(200, response)
        except Exception as e:
            logger.error(f"处理传输状态查询失败: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _send_response(self, status_code: int, data: dict):
        """发送JSON响应"""
        response_body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)
    
    def log_message(self, format, *args):
        """重写日志方法，使用logger"""
        logger.debug(f"{self.address_string()} - {format % args}")


class TransferServer:
    """文件传输服务器"""
    
    # 请求有效期（秒），默认5分钟
    REQUEST_EXPIRY_TIME = 5 * 60
    
    def __init__(self, port: int = 8765, save_dir: Optional[Path] = None,
                 on_transfer_request: Optional[Callable[[str, str, str, str, int, str, int], None]] = None,
                 on_file_received: Optional[Callable[[Path, int, str], None]] = None,
                 on_receive_progress: Optional[Callable[[str, int, int], None]] = None):
        """
        初始化传输服务器
        
        Args:
            port: 监听端口
            save_dir: 文件保存目录（默认：~/Downloads）
            on_transfer_request: 收到传输请求时的回调函数 (request_id, sender_name, sender_id, filename, file_size)
            on_file_received: 文件接收完成时的回调函数 (save_path, file_size, original_filename)
            on_receive_progress: 接收进度回调函数 (request_id, received, total)
        """
        self._port = port
        self._save_dir = save_dir or (Path.home() / "Downloads")
        self._on_transfer_request = on_transfer_request
        self._on_file_received = on_file_received
        self._on_receive_progress = on_receive_progress
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._cleanup_timer: Optional[threading.Timer] = None
        self._running = False
        self._pending_requests: Dict[str, dict] = {}  # 待处理的传输请求
        self._lock = threading.Lock()  # 保护 pending_requests 的锁
    
    def start(self):
        """启动服务器"""
        if self._running:
            return
        
        try:
            # 确保保存目录存在
            self._save_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建请求处理器工厂
            def handler_factory(*args, **kwargs):
                handler = TransferRequestHandler(
                    *args,
                    save_dir=self._save_dir,
                    on_transfer_request=self._on_transfer_request,
                    on_file_received=self._on_file_received,
                    on_receive_progress=self._on_receive_progress,
                    pending_requests=self._pending_requests,
                    lock=self._lock,
                    **kwargs
                )
                return handler
            
            # 创建HTTP服务器
            self._server = HTTPServer(('0.0.0.0', self._port), handler_factory)
            
            # 在后台线程中运行服务器
            self._thread = threading.Thread(target=self._run_server, daemon=True)
            self._thread.start()
            
            # 启动过期请求清理任务
            self._start_cleanup_timer()
            
            self._running = True
            logger.info(f"文件传输服务器已启动，监听端口: {self._port}")
        except Exception as e:
            logger.error(f"启动文件传输服务器失败: {e}")
            raise
    
    def stop(self):
        """停止服务器"""
        if not self._running:
            return
        
        try:
            # 停止清理定时器
            self._stop_cleanup_timer()
            
            if self._server:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
            
            if self._thread:
                self._thread.join(timeout=2)
                self._thread = None
            
            self._running = False
            logger.info("文件传输服务器已停止")
        except Exception as e:
            logger.error(f"停止文件传输服务器失败: {e}")
    
    def confirm_transfer(self, request_id: str, accepted: bool):
        """
        确认传输请求（由UI调用）
        
        Args:
            request_id: 请求ID
            accepted: 是否接受
        """
        with self._lock:
            # 打印所有待处理的请求ID，用于调试
            all_request_ids = list(self._pending_requests.keys())
            logger.info(f"confirm_transfer: 当前待处理请求列表: {all_request_ids}")
            logger.info(f"confirm_transfer: 查找请求ID: {request_id}, 类型: {type(request_id)}")
            
            if request_id not in self._pending_requests:
                logger.warning(f"confirm_transfer: 请求 {request_id} 不存在于服务器端")
                # 尝试查找相似的请求ID（可能是字符串匹配问题）
                for req_id in all_request_ids:
                    if str(req_id) == str(request_id):
                        logger.info(f"找到匹配的请求ID（字符串转换后）: {req_id}")
                        request_id = req_id
                        break
                else:
                    return
            
            request_data = self._pending_requests[request_id]
            old_status = request_data.get('status', 'unknown')
            request_data['status'] = 'accepted' if accepted else 'rejected'
            logger.info(f"确认传输请求: {request_id}, 状态从 {old_status} 变为 {request_data['status']}")
    
    def get_pending_request(self, request_id: str) -> Optional[dict]:
        """
        获取待处理的请求（自动检查过期）
        
        Args:
            request_id: 请求ID
        
        Returns:
            请求信息字典，如果请求不存在或已过期则返回 None
        """
        with self._lock:
            if request_id not in self._pending_requests:
                logger.warning(f"请求 {request_id} 不存在于待处理列表中")
                return None
            
            request_data = self._pending_requests[request_id]
            
            # 检查请求是否过期
            timestamp = request_data.get('timestamp', 0)
            current_time = time.time()
            elapsed_time = current_time - timestamp
            
            # 如果 timestamp 为 0 或异常，认为请求无效
            if timestamp == 0 or timestamp > current_time:
                logger.error(f"请求 {request_id} 的 timestamp 异常: {timestamp}, 当前时间: {current_time}")
                del self._pending_requests[request_id]
                return None
            
            # 检查是否过期（5分钟 = 300秒）
            if elapsed_time > self.REQUEST_EXPIRY_TIME:
                # 请求已过期，删除它
                logger.info(f"请求 {request_id} 已过期 (已过 {elapsed_time:.1f} 秒，有效期 {self.REQUEST_EXPIRY_TIME} 秒)，已自动清理")
                del self._pending_requests[request_id]
                return None
            
            # 记录调试信息
            logger.debug(f"请求 {request_id} 有效 (已过 {elapsed_time:.1f} 秒，剩余 {self.REQUEST_EXPIRY_TIME - elapsed_time:.1f} 秒)")
            return request_data
    
    def _start_cleanup_timer(self):
        """启动过期请求清理定时器"""
        def cleanup_expired_requests():
            """清理过期的请求"""
            if not self._running:
                return
            
            current_time = time.time()
            expired_ids = []
            
            with self._lock:
                for request_id, request_data in list(self._pending_requests.items()):
                    timestamp = request_data.get('timestamp', 0)
                    # 只清理 pending 状态的过期请求，accepted 状态的请求等待文件传输
                    if request_data.get('status') == 'pending' and current_time - timestamp > self.REQUEST_EXPIRY_TIME:
                        expired_ids.append(request_id)
                
                # 删除过期的请求
                for request_id in expired_ids:
                    del self._pending_requests[request_id]
                    logger.info(f"自动清理过期请求: {request_id}")
            
            # 如果还有请求，继续定时清理
            if self._running:
                self._cleanup_timer = threading.Timer(60.0, cleanup_expired_requests)  # 每60秒清理一次
                self._cleanup_timer.daemon = True
                self._cleanup_timer.start()
        
        # 启动第一次清理（60秒后）
        self._cleanup_timer = threading.Timer(60.0, cleanup_expired_requests)
        self._cleanup_timer.daemon = True
        self._cleanup_timer.start()
    
    def _stop_cleanup_timer(self):
        """停止清理定时器"""
        if self._cleanup_timer:
            self._cleanup_timer.cancel()
            self._cleanup_timer = None
    
    def _run_server(self):
        """运行服务器（在后台线程中）"""
        try:
            self._server.serve_forever()
        except Exception as e:
            logger.error(f"服务器运行错误: {e}")
    
    @property
    def port(self) -> int:
        """获取监听端口"""
        return self._port
    
    @property
    def is_running(self) -> bool:
        """检查服务器是否运行中"""
        return self._running
