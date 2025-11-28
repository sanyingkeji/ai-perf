#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
版本管理工具类
统一管理所有客户端版本号，避免全局替换
"""

import re
from pathlib import Path
from typing import Optional, Dict, List
import json


class VersionManager:
    """版本管理工具类"""
    
    # 版本号存储的文件列表
    VERSION_FILES = {
        "employee_macos": "ui_client/build_macos.spec",
        "employee_windows_linux": "ui_client/build.spec",
        "admin_macos": "admin_ui_client/build_macos.spec",
        "admin_windows_linux": "admin_ui_client/build.spec",
    }
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        初始化版本管理器
        
        Args:
            project_root: 项目根目录路径，如果为 None，则自动检测
        """
        if project_root is None:
            # 自动检测项目根目录（从当前文件位置向上查找）
            current_file = Path(__file__).resolve()
            # admin_ui_client/utils/version_manager.py -> admin_ui_client -> 项目根目录
            self.project_root = current_file.parent.parent.parent
        else:
            self.project_root = Path(project_root).resolve()
    
    def get_current_version(self) -> Optional[str]:
        """
        获取当前版本号（从任意一个 spec 文件中读取）
        
        Returns:
            版本号字符串，如果未找到则返回 None
        """
        # 优先从 admin_macos.spec 读取（因为管理端更常用）
        spec_file = self.project_root / self.VERSION_FILES["admin_macos"]
        if spec_file.exists():
            version = self._read_version_from_spec(spec_file)
            if version:
                return version
        
        # 如果未找到，尝试从其他文件读取
        for spec_path in self.VERSION_FILES.values():
            spec_file = self.project_root / spec_path
            if spec_file.exists():
                version = self._read_version_from_spec(spec_file)
                if version:
                    return version
        
        return None
    
    def get_all_versions(self) -> Dict[str, Optional[str]]:
        """
        获取所有文件中的版本号
        
        Returns:
            字典，键为文件标识，值为版本号
        """
        versions = {}
        for key, spec_path in self.VERSION_FILES.items():
            spec_file = self.project_root / spec_path
            if spec_file.exists():
                versions[key] = self._read_version_from_spec(spec_file)
            else:
                versions[key] = None
        return versions
    
    def update_version(self, new_version: str, files: Optional[List[str]] = None) -> Dict[str, bool]:
        """
        更新版本号
        
        Args:
            new_version: 新版本号（格式：x.x.x）
            files: 要更新的文件列表（如果为 None，则更新所有文件）
                   可选值：["employee_macos", "employee_windows_linux", "admin_macos", "admin_windows_linux"]
        
        Returns:
            字典，键为文件标识，值为是否更新成功
        """
        if files is None:
            files = list(self.VERSION_FILES.keys())
        
        results = {}
        for key in files:
            if key not in self.VERSION_FILES:
                results[key] = False
                continue
            
            spec_path = self.VERSION_FILES[key]
            spec_file = self.project_root / spec_path
            
            if not spec_file.exists():
                results[key] = False
                continue
            
            try:
                results[key] = self._update_version_in_spec(spec_file, new_version)
            except Exception as e:
                print(f"更新 {spec_path} 失败: {e}")
                results[key] = False
        
        return results
    
    def _read_version_from_spec(self, spec_file: Path) -> Optional[str]:
        """
        从 spec 文件中读取版本号
        
        Args:
            spec_file: spec 文件路径
        
        Returns:
            版本号字符串，如果未找到则返回 None
        """
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 查找 version='...' 或 version="..."
            match = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", content)
            if match:
                return match.group(1)
            
            # 也查找 CFBundleShortVersionString
            match = re.search(r"['\"]CFBundleShortVersionString['\"]:\s*['\"]([^'\"]+)['\"]", content)
            if match:
                return match.group(1)
        except Exception as e:
            print(f"读取版本号失败: {e}")
        
        return None
    
    def _update_version_in_spec(self, spec_file: Path, new_version: str) -> bool:
        """
        更新 spec 文件中的版本号
        
        Args:
            spec_file: spec 文件路径
            new_version: 新版本号
        
        Returns:
            是否更新成功
        """
        try:
            with open(spec_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # 更新 version='...' 或 version="..."
            content = re.sub(
                r"version\s*=\s*['\"]([^'\"]+)['\"]",
                f"version='{new_version}'",
                content
            )
            
            # 更新 CFBundleShortVersionString
            content = re.sub(
                r"(['\"])CFBundleShortVersionString(['\"]):\s*['\"]([^'\"]+)['\"]",
                f"\\1CFBundleShortVersionString\\2: '{new_version}'",
                content
            )
            
            # 更新 CFBundleVersion
            content = re.sub(
                r"(['\"])CFBundleVersion(['\"]):\s*['\"]([^'\"]+)['\"]",
                f"\\1CFBundleVersion\\2: '{new_version}'",
                content
            )
            
            # 如果内容有变化，写回文件
            if content != original_content:
                with open(spec_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True
            else:
                # 如果没有变化，可能是版本号已经是新值
                return True
        except Exception as e:
            print(f"更新版本号失败: {e}")
            return False
    
    def validate_version(self, version: str) -> bool:
        """
        验证版本号格式
        
        Args:
            version: 版本号字符串
        
        Returns:
            是否为有效格式（x.x.x）
        """
        pattern = r'^\d+\.\d+\.\d+$'
        return bool(re.match(pattern, version))
    
    def get_version_info(self) -> Dict[str, any]:
        """
        获取版本信息摘要
        
        Returns:
            包含当前版本和所有文件版本信息的字典
        """
        current_version = self.get_current_version()
        all_versions = self.get_all_versions()
        
        # 检查版本是否一致
        unique_versions = set(v for v in all_versions.values() if v is not None)
        is_consistent = len(unique_versions) <= 1
        
        return {
            "current_version": current_version,
            "all_versions": all_versions,
            "is_consistent": is_consistent,
            "unique_versions": list(unique_versions) if unique_versions else []
        }

