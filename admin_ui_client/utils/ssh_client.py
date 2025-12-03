#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH客户端工具，用于远程执行systemctl命令
"""

import paramiko
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SSHClient:
    """SSH客户端，用于远程执行命令"""
    
    def __init__(self, host: str, port: int = 22, username: str = "", 
                 password: Optional[str] = None, key_path: Optional[str] = None):
        """
        初始化SSH客户端
        
        Args:
            host: SSH服务器地址
            port: SSH端口，默认22
            username: SSH用户名
            password: SSH密码（如果使用密码认证）
            key_path: SSH密钥文件路径（如果使用密钥认证）
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self._client: Optional[paramiko.SSHClient] = None
    
    def connect(self, max_retries: int = 3, retry_delay: float = 2.0) -> bool:
        """
        建立SSH连接（带重试机制）
        
        Args:
            max_retries: 最大重试次数，默认3次
            retry_delay: 重试延迟（秒），默认2秒
        
        Returns:
            bool: 连接是否成功
        """
        import time
        
        last_error = None
        for attempt in range(max_retries):
            try:
                self._client = paramiko.SSHClient()
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                # 设置连接超时和banner超时（增加超时时间以处理慢速网络）
                connect_timeout = 30  # 连接超时30秒
                banner_timeout = 60  # banner读取超时60秒
                
                # 如果提供了密钥路径，使用密钥认证
                if self.key_path:
                    try:
                        # 尝试加载不同类型的密钥文件（按常见顺序尝试）
                        key = None
                        key_exceptions = []
                        
                        # 密钥类型列表（按常见程度排序）
                        # 注意：DSSKey 在新版本的 paramiko 中已被移除
                        key_classes = [
                            paramiko.RSAKey,
                            paramiko.Ed25519Key,
                            paramiko.ECDSAKey,
                        ]
                        # 尝试添加 DSSKey（如果 paramiko 版本支持）
                        try:
                            if hasattr(paramiko, 'DSSKey'):
                                key_classes.append(paramiko.DSSKey)
                        except AttributeError:
                            pass  # DSSKey 不可用，跳过
                        
                        # 尝试每种密钥类型（先不提供密码）
                        for key_class in key_classes:
                            try:
                                key = key_class.from_private_key_file(self.key_path)
                                logger.info(f"成功加载 {key_class.__name__} 密钥")
                                break
                            except Exception as e:
                                key_exceptions.append(f"{key_class.__name__}: {str(e)}")
                                continue
                        
                        # 如果都失败且提供了密码，尝试使用密码作为密钥的passphrase
                        if key is None and self.password:
                            for key_class in key_classes:
                                try:
                                    key = key_class.from_private_key_file(
                                        self.key_path,
                                        password=self.password
                                    )
                                    logger.info(f"成功加载 {key_class.__name__} 密钥（使用密码作为passphrase）")
                                    break
                                except Exception as e:
                                    key_exceptions.append(f"{key_class.__name__}(with passphrase): {str(e)}")
                                    continue
                        
                        if key is None:
                            error_msg = f"无法加载密钥文件 '{self.key_path}'，尝试了所有格式都失败。错误信息: {'; '.join(key_exceptions[:3])}"  # 只显示前3个错误
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        
                        # 使用密钥连接
                        self._client.connect(
                            hostname=self.host,
                            port=self.port,
                            username=self.username,
                            pkey=key,
                            timeout=connect_timeout,
                            banner_timeout=banner_timeout,
                            auth_timeout=30,  # 认证超时30秒
                            look_for_keys=False,  # 不自动查找密钥
                            allow_agent=False  # 不使用SSH agent
                        )
                        # 设置保活机制（连接成功后）
                        transport = self._client.get_transport()
                        if transport:
                            transport.set_keepalive(60)  # 每60秒发送一次保活包
                        logger.info(f"SSH连接成功（使用密钥认证）")
                    except Exception as e:
                        logger.error(f"使用密钥文件连接失败: {e}")
                        # 如果密钥认证失败，尝试使用密码作为SSH密码（不是密钥的passphrase）
                        if self.password:
                            try:
                                logger.info("尝试使用密码认证...")
                                # 重新创建 SSH 客户端（因为之前的连接可能已经失败）
                                try:
                                    if self._client:
                                        self._client.close()
                                except Exception:
                                    pass  # 忽略关闭失败的错误
                                self._client = paramiko.SSHClient()
                                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                                self._client.connect(
                                    hostname=self.host,
                                    port=self.port,
                                    username=self.username,
                                    password=self.password,
                                    timeout=connect_timeout,
                                    banner_timeout=banner_timeout,
                                    auth_timeout=30,  # 认证超时30秒
                                    look_for_keys=False,
                                    allow_agent=False
                                )
                                # 设置保活机制（连接成功后）
                                transport = self._client.get_transport()
                                if transport:
                                    transport.set_keepalive(60)  # 每60秒发送一次保活包
                                logger.info("SSH连接成功（使用密码认证）")
                            except Exception as e2:
                                logger.error(f"使用密码连接也失败: {e2}")
                                raise ValueError(f"密钥认证失败: {e}，密码认证也失败: {e2}")
                        else:
                            raise ValueError(f"密钥认证失败: {e}")
                elif self.password:
                    # 使用密码认证
                    self._client.connect(
                        hostname=self.host,
                        port=self.port,
                        username=self.username,
                        password=self.password,
                        timeout=connect_timeout,
                        banner_timeout=banner_timeout,
                        auth_timeout=30,  # 认证超时30秒
                        look_for_keys=False,
                        allow_agent=False
                    )
                    # 设置保活机制（连接成功后）
                    transport = self._client.get_transport()
                    if transport:
                        transport.set_keepalive(60)  # 每60秒发送一次保活包
                else:
                    raise ValueError("必须提供密码或密钥文件路径")
                
                return True
            except paramiko.ssh_exception.SSHException as e:
                error_msg = str(e)
                last_error = e
                if "Error reading SSH protocol banner" in error_msg:
                    logger.warning(f"SSH连接失败（尝试 {attempt + 1}/{max_retries}）: 无法读取SSH协议banner")
                else:
                    logger.warning(f"SSH连接失败（尝试 {attempt + 1}/{max_retries}）: {e}")
                
                # 清理失败的连接
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    pass
                self._client = None
                
                # 如果不是最后一次尝试，等待后重试
                if attempt < max_retries - 1:
                    logger.info(f"等待 {retry_delay} 秒后重试...")
                    time.sleep(retry_delay)
                    continue
                else:
                    # 最后一次尝试也失败
                    if "Error reading SSH protocol banner" in error_msg:
                        logger.error(f"SSH连接失败（已重试 {max_retries} 次）: 无法读取SSH协议banner，可能是网络延迟或服务器响应慢。请检查网络连接和SSH服务器状态。")
                    else:
                        logger.error(f"SSH连接失败（已重试 {max_retries} 次）: {last_error}")
                    return False
            except Exception as e:
                last_error = e
                # 只在最后一次尝试失败时记录错误，避免大量重复日志
                if attempt == max_retries - 1:
                    # 简化错误消息，避免输出详细的堆栈信息
                    error_str = str(e)
                    if "Error reading SSH protocol banner" in error_str:
                        logger.warning(f"SSH连接失败（已重试 {max_retries} 次）: 无法读取SSH协议横幅，请检查网络连接")
                    else:
                        logger.warning(f"SSH连接失败（已重试 {max_retries} 次）: {error_str}")
                
                # 清理失败的连接
                try:
                    if self._client:
                        self._client.close()
                except Exception:
                    pass
                self._client = None
                
                # 如果不是最后一次尝试，等待后重试
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    return False
        
        return False
    
    def execute(self, command: str, sudo: bool = False) -> Dict[str, Any]:
        """
        执行远程命令
        
        Args:
            command: 要执行的命令
            sudo: 是否使用sudo执行
        
        Returns:
            {
                "success": bool,
                "stdout": str,
                "stderr": str,
                "returncode": int,
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "",
                    "returncode": -1,
                    "error": "SSH连接失败"
                }
        
        try:
            # 如果需要sudo，在命令前添加sudo
            if sudo:
                command = f"sudo {command}"
            
            stdin, stdout, stderr = self._client.exec_command(command, timeout=30)
            returncode = stdout.channel.recv_exit_status()
            
            stdout_text = stdout.read().decode('utf-8', errors='ignore').strip()
            stderr_text = stderr.read().decode('utf-8', errors='ignore').strip()
            
            return {
                "success": returncode == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": returncode,
                "error": None
            }
        except Exception as e:
            logger.error(f"执行命令失败: {e}")
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "returncode": -1,
                "error": str(e)
            }
    
    def close(self):
        """关闭SSH连接"""
        if self._client:
            self._client.close()
            self._client = None
    
    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()

    def download_file(self, remote_path: str, local_path: str) -> Dict[str, Any]:
        """
        通过SFTP下载远程文件
        
        Args:
            remote_path: 远程文件路径
            local_path: 本地保存路径
        
        Returns:
            {
                "success": bool,
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "error": "SSH连接失败"
                }
        
        try:
            sftp = self._client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            return {
                "success": True,
                "error": None
            }
        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def list_files(self, remote_dir: str, recursive: bool = False) -> Dict[str, Any]:
        """
        列出远程目录中的文件
        
        Args:
            remote_dir: 远程目录路径
            recursive: 是否递归列出子目录
        
        Returns:
            {
                "success": bool,
                "files": List[Dict],  # [{"name": str, "path": str, "size": int, "is_dir": bool, "mtime": float}]
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "files": [],
                    "error": "SSH连接失败"
                }
        
        try:
            sftp = self._client.open_sftp()
            files = []
            
            def _list_dir(path: str):
                """递归列出目录"""
                try:
                    items = sftp.listdir_attr(path)
                    for item in items:
                        # 构建完整路径
                        if path.endswith("/"):
                            item_path = f"{path}{item.filename}"
                        else:
                            item_path = f"{path}/{item.filename}"
                        
                        # 计算相对路径（相对于remote_dir）
                        remote_dir_normalized = remote_dir.rstrip("/")
                        if item_path.startswith(remote_dir_normalized + "/"):
                            rel_path = item_path[len(remote_dir_normalized) + 1:]
                        elif item_path == remote_dir_normalized:
                            rel_path = ""
                        else:
                            rel_path = item.filename
                        
                        is_dir = (item.st_mode & 0o040000 != 0)
                        
                        files.append({
                            "name": item.filename,
                            "path": item_path,
                            "rel_path": rel_path,
                            "size": item.st_size,
                            "is_dir": is_dir,
                            "mtime": item.st_mtime,
                        })
                        
                        # 如果是目录且需要递归，继续列出
                        if recursive and is_dir:
                            _list_dir(item_path)
                except Exception as e:
                    logger.warning(f"列出目录 {path} 失败: {e}")
            
            _list_dir(remote_dir)
            sftp.close()
            
            return {
                "success": True,
                "files": files,
                "error": None
            }
        except Exception as e:
            logger.error(f"列出文件失败: {e}")
            return {
                "success": False,
                "files": [],
                "error": str(e)
            }
    
    def read_file(self, remote_path: str, max_size: int = 10 * 1024 * 1024) -> Dict[str, Any]:
        """
        读取远程文件内容（如果文件太大则返回错误）
        
        Args:
            remote_path: 远程文件路径
            max_size: 最大读取大小（字节），默认10MB
        
        Returns:
            {
                "success": bool,
                "content": str,
                "size": int,
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "content": "",
                    "size": 0,
                    "error": "SSH连接失败"
                }
        
        try:
            sftp = self._client.open_sftp()
            file_attr = sftp.stat(remote_path)
            file_size = file_attr.st_size
            
            if file_size > max_size:
                sftp.close()
                return {
                    "success": False,
                    "content": "",
                    "size": file_size,
                    "error": f"文件太大（{file_size / 1024 / 1024:.2f} MB），超过最大限制（{max_size / 1024 / 1024:.2f} MB），请下载后查看"
                }
            
            # 读取文件内容
            with sftp.open(remote_path, 'r') as f:
                content = f.read().decode('utf-8', errors='ignore')
            
            sftp.close()
            
            return {
                "success": True,
                "content": content,
                "size": file_size,
                "error": None
            }
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return {
                "success": False,
                "content": "",
                "size": 0,
                "error": str(e)
            }
    
    def upload_file(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """
        通过SFTP上传文件到远程服务器
        
        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
        
        Returns:
            {
                "success": bool,
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "error": "SSH连接失败"
                }
        
        try:
            sftp = self._client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            return {
                "success": True,
                "error": None
            }
        except Exception as e:
            logger.error(f"上传文件失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def write_file(self, remote_path: str, content: str) -> Dict[str, Any]:
        """
        通过SFTP写入内容到远程文件
        
        Args:
            remote_path: 远程文件路径
            content: 文件内容（字符串）
        
        Returns:
            {
                "success": bool,
                "error": str
            }
        """
        if not self._client:
            if not self.connect():
                return {
                    "success": False,
                    "error": "SSH连接失败"
                }
        
        try:
            sftp = self._client.open_sftp()
            # 使用SFTP写入文件
            with sftp.open(remote_path, 'w') as f:
                f.write(content.encode('utf-8'))
            sftp.close()
            return {
                "success": True,
                "error": None
            }
        except Exception as e:
            logger.error(f"写入文件失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

