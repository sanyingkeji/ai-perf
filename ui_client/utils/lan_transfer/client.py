#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件传输客户端（发送端）
支持两步传输：先请求确认，再传输文件
"""

import os
import httpx
import logging
import json
from pathlib import Path
from typing import Optional, Callable
from urllib.parse import quote

logger = logging.getLogger(__name__)


class TransferClient:
    """文件传输客户端"""
    
    def __init__(self, timeout: int = 300, port: int = 8765):
        """
        初始化传输客户端
        
        Args:
            timeout: 请求超时时间（秒）
        """
        self._timeout = timeout
        self._port = port
    
    def send_transfer_request(self, file_path: Path, target_ip: str, target_port: int,
                             sender_name: str, sender_id: str) -> dict:
        """
        发送传输请求（第一步）
        
        Args:
            file_path: 要发送的文件路径
            target_ip: 目标设备IP
            target_port: 目标设备端口
            sender_name: 发送者名称
            sender_id: 发送者ID
        
        Returns:
            {
                "success": bool,
                "request_id": str,
                "message": str
            }
        """
        if not file_path.exists():
            return {
                "success": False,
                "request_id": None,
                "message": "文件不存在"
            }
        
        file_size = file_path.stat().st_size
        filename = file_path.name
        
        try:
            url = f"http://{target_ip}:{target_port}/transfer_request"
            
            # 获取本机IP和端口（用于接收端确认）
            from .discovery import get_local_ip
            local_ip = get_local_ip() or target_ip  # 如果获取失败，使用目标IP
            
            request_data = {
                "filename": filename,
                "file_size": file_size,
                "sender_name": sender_name,
                "sender_id": sender_id,
                "sender_port": self._port
            }
            
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    url,
                    json=request_data,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "request_id": result.get("request_id"),
                        "message": "请求已发送"
                    }
                else:
                    error_msg = "发送请求失败"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", error_msg)
                    except:
                        error_msg = f"HTTP {response.status_code}"
                    
                    return {
                        "success": False,
                        "request_id": None,
                        "message": error_msg
                    }
        except httpx.TimeoutException:
            return {
                "success": False,
                "request_id": None,
                "message": "请求超时"
            }
        except Exception as e:
            logger.error(f"发送传输请求失败: {e}")
            return {
                "success": False,
                "request_id": None,
                "message": f"发送失败: {str(e)}"
            }
    
    def wait_for_confirm(self, request_id: str, target_ip: str, target_port: int,
                         timeout: int = 30) -> dict:
        """
        等待传输确认（由发送端调用）
        
        Args:
            request_id: 请求ID
            target_ip: 目标设备IP（接收端）
            target_port: 目标设备端口
            timeout: 超时时间（秒）
        
        Returns:
            {
                "success": bool,
                "accepted": bool,
                "message": str
            }
        """
        import time
        start_time = time.time()
        
        # 轮询检查确认状态
        while time.time() - start_time < timeout:
            try:
                url = f"http://{target_ip}:{target_port}/transfer_status"
                params = {"request_id": request_id}
                
                with httpx.Client(timeout=5) as client:
                    response = client.get(url, params=params)
                    
                    if response.status_code == 200:
                        result = response.json()
                        status = result.get("status")
                        
                        if status == "accepted":
                            return {
                                "success": True,
                                "accepted": True,
                                "message": "已接受"
                            }
                        elif status == "rejected":
                            return {
                                "success": True,
                                "accepted": False,
                                "message": "已拒绝"
                            }
            except Exception as e:
                pass
            
            time.sleep(1)  # 每秒检查一次
        return {
            "success": False,
            "accepted": False,
            "message": "等待接受已超时"
        }
    
    def confirm_transfer(self, request_id: str, target_ip: str, target_port: int,
                        accepted: bool) -> dict:
        """
        确认传输（第二步：接受或拒绝）
        
        Args:
            request_id: 请求ID
            target_ip: 目标设备IP
            target_port: 目标设备端口
            accepted: 是否接受
        
        Returns:
            {
                "success": bool,
                "message": str
            }
        """
        try:
            url = f"http://{target_ip}:{target_port}/transfer_confirm"
            
            request_data = {
                "request_id": request_id,
                "accepted": accepted
            }
            
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    url,
                    json=request_data,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "message": result.get("message", "确认成功")
                    }
                else:
                    error_msg = "确认失败"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", error_msg)
                    except:
                        error_msg = f"HTTP {response.status_code}"
                    
                    return {
                        "success": False,
                        "message": error_msg
                    }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "确认超时"
            }
        except Exception as e:
            logger.error(f"确认传输失败: {e}")
            return {
                "success": False,
                "message": f"确认失败: {str(e)}"
            }
    
    def send_file(self, file_path: Path, target_ip: str, target_port: int,
                  request_id: str,
                  on_progress: Optional[Callable[[int, int], None]] = None) -> dict:
        """
        发送文件（第三步：实际传输）
        
        Args:
            file_path: 文件路径
            target_ip: 目标设备IP
            target_port: 目标设备端口
            request_id: 请求ID
            on_progress: 进度回调 (uploaded, total)
        
        Returns:
            {
                "success": bool,
                "message": str,
                "filename": str,
                "path": str (接收端保存路径)
            }
        """
        if not file_path.exists():
            return {
                "success": False,
                "message": "文件不存在",
                "filename": file_path.name,
                "path": ""
            }
        
        file_size = file_path.stat().st_size
        filename = file_path.name
        
        try:
            url = f"http://{target_ip}:{target_port}/transfer"
            
            headers = {
                'X-Request-ID': request_id,
                'X-Filename': quote(filename),
                'Content-Length': str(file_size)
            }
            
            # 使用httpx上传（支持进度回调）
            with httpx.Client(timeout=self._timeout) as client:
                # 如果支持进度回调，使用流式上传
                if on_progress:
                    # 流式上传以支持进度
                    response = self._upload_with_progress(
                        client, url, file_path, filename, file_size, headers, on_progress
                    )
                else:
                    # 直接上传
                    with open(file_path, 'rb') as f:
                        response = client.post(
                            url,
                            content=f.read(),
                            headers=headers
                        )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "message": "文件发送成功",
                        "filename": result.get("filename", filename),
                        "path": result.get("path", "")
                    }
                else:
                    error_msg = "发送失败"
                    try:
                        error_data = response.json()
                        error_msg = error_data.get("error", error_msg)
                    except:
                        error_msg = f"HTTP {response.status_code}"
                    
                    return {
                        "success": False,
                        "message": error_msg,
                        "filename": filename,
                        "path": ""
                    }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "传输超时",
                "filename": filename,
                "path": ""
            }
        except Exception as e:
            logger.error(f"发送文件失败: {e}")
            return {
                "success": False,
                "message": f"发送失败: {str(e)}",
                "filename": filename,
                "path": ""
            }
    
    def _upload_with_progress(self, client: httpx.Client, url: str, file_path: Path,
                             filename: str, file_size: int, headers: dict,
                             on_progress: Callable[[int, int], None]):
        """带进度的文件上传"""
        # 使用简单的POST方式，直接发送文件内容
        # 服务器端会从X-Filename头部获取文件名
        
        chunk_size = 64 * 1024  # 64KB per chunk
        
        # 读取文件并发送
        uploaded = 0
        
        def file_generator():
            nonlocal uploaded
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    uploaded += len(chunk)
                    if on_progress:
                        on_progress(uploaded, file_size)
                    yield chunk
        
        # 发送请求
        response = client.post(
            url,
            content=file_generator(),
            headers=headers
        )
        return response
    
    def check_status(self, target_ip: str, target_port: int) -> bool:
        """
        检查目标设备是否在线
        
        Args:
            target_ip: 目标设备IP
            target_port: 目标设备端口
        
        Returns:
            是否在线
        """
        try:
            url = f"http://{target_ip}:{target_port}/status"
            response = httpx.get(url, timeout=5)
            return response.status_code == 200
        except Exception:
            return False
