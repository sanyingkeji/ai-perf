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
                 pending_requests: Dict = None,
                 **kwargs):
        self._save_dir = save_dir or Path.home() / "Downloads"
        self._on_transfer_request = on_transfer_request
        self._on_file_received = on_file_received
        self._pending_requests = pending_requests or {}
        super().__init__(*args, **kwargs)
    
    def _get_client_ip(self) -> str:
        """获取客户端IP"""
        return self.client_address[0]
    
    def do_POST(self):
        """处理POST请求"""
        try:
            parsed_path = urlparse(self.path)
            
            if parsed_path.path == '/transfer_request':
                self._handle_transfer_request()
            elif parsed_path.path == '/transfer_confirm':
                self._handle_transfer_confirm()
            elif parsed_path.path == '/transfer':
                self._handle_file_upload()
            else:
                self._send_response(404, {"error": "Not found"})
        except Exception as e:
            logger.error(f"处理POST请求失败: {e}")
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
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_response(400, {"error": "Empty request"})
                return
            
            request_body = self.rfile.read(content_length)
            request_data = json.loads(request_body.decode('utf-8'))
            
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
            
            # 保存待处理的请求
            self._pending_requests[request_id] = {
                'filename': filename,
                'file_size': file_size,
                'sender_name': sender_name,
                'sender_id': sender_id,
                'sender_ip': sender_ip,
                'sender_port': sender_port,
                'timestamp': time.time(),
                'status': 'pending'  # pending, accepted, rejected
            }
            
            # 触发回调（显示通知）
            if self._on_transfer_request:
                try:
                    self._on_transfer_request(
                        request_id=request_id,
                        sender_name=sender_name,
                        sender_id=sender_id,
                        filename=filename,
                        file_size=file_size
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
            
            if not request_id or request_id not in self._pending_requests:
                self._send_response(404, {"error": "Request not found"})
                return
            
            # 更新请求状态
            request_info = self._pending_requests[request_id]
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
            
            if not request_id or request_id not in self._pending_requests:
                self._send_response(400, {"error": "Invalid request ID"})
                return
            
            request_info = self._pending_requests[request_id]
            
            if request_info['status'] != 'accepted':
                self._send_response(403, {"error": "Request not accepted"})
                return
            
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
            
            # 读取文件内容并保存
            bytes_read = 0
            with open(save_path, 'wb') as f:
                while bytes_read < content_length:
                    chunk_size = min(8192, content_length - bytes_read)
                    chunk = self.rfile.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_read += len(chunk)
            
            # 删除待处理的请求
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
            
            if not request_id or request_id not in self._pending_requests:
                self._send_response(404, {"error": "Request not found"})
                return
            
            request_info = self._pending_requests[request_id]
            response = {
                "status": request_info['status'],  # pending, accepted, rejected
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
    
    def __init__(self, port: int = 8765, save_dir: Optional[Path] = None,
                 on_transfer_request: Optional[Callable[[str, str, str, str, int], None]] = None,
                 on_file_received: Optional[Callable[[Path, int, str], None]] = None):
        """
        初始化传输服务器
        
        Args:
            port: 监听端口
            save_dir: 文件保存目录（默认：~/Downloads）
            on_transfer_request: 收到传输请求时的回调函数 (request_id, sender_name, sender_id, filename, file_size)
            on_file_received: 文件接收完成时的回调函数 (save_path, file_size, original_filename)
        """
        self._port = port
        self._save_dir = save_dir or (Path.home() / "Downloads")
        self._on_transfer_request = on_transfer_request
        self._on_file_received = on_file_received
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._pending_requests: Dict[str, dict] = {}  # 待处理的传输请求
    
    def start(self):
        """启动服务器"""
        if self._running:
            return
        
        try:
            # 确保保存目录存在
            self._save_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建请求处理器工厂
            def handler_factory(*args, **kwargs):
                return TransferRequestHandler(
                    *args,
                    save_dir=self._save_dir,
                    on_transfer_request=self._on_transfer_request,
                    on_file_received=self._on_file_received,
                    pending_requests=self._pending_requests,
                    **kwargs
                )
            
            # 创建HTTP服务器
            self._server = HTTPServer(('0.0.0.0', self._port), handler_factory)
            
            # 在后台线程中运行服务器
            self._thread = threading.Thread(target=self._run_server, daemon=True)
            self._thread.start()
            
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
        if request_id in self._pending_requests:
            self._pending_requests[request_id]['status'] = 'accepted' if accepted else 'rejected'
    
    def get_pending_request(self, request_id: str) -> Optional[dict]:
        """获取待处理的请求"""
        return self._pending_requests.get(request_id)
    
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
