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
    
    # Inno Setup 模板文件（Windows 安装器）
    INNO_SETUP_TEMPLATE = "scripts/inno_setup_template.iss"
    
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
                version = self._read_version_from_spec(spec_file)
                # 如果 Windows/Linux 的 build.spec 中没有版本号，尝试从对应的 macOS spec 文件读取
                if not version and key in ["employee_windows_linux", "admin_windows_linux"]:
                    # 尝试从对应的 macOS spec 文件读取
                    macos_key = "employee_macos" if key == "employee_windows_linux" else "admin_macos"
                    macos_spec_path = self.VERSION_FILES.get(macos_key)
                    if macos_spec_path:
                        macos_spec_file = self.project_root / macos_spec_path
                        if macos_spec_file.exists():
                            version = self._read_version_from_spec(macos_spec_file)
                versions[key] = version
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
        
        # 同时更新 config.json 文件中的 client_version
        config_results = self._update_config_json_versions(new_version)
        results.update(config_results)
        
        # 同时更新 config_manager.py 中的 DEFAULT_CONFIG 版本号
        config_manager_results = self._update_config_manager_defaults(new_version)
        results.update(config_manager_results)
        
        # 更新 Inno Setup 模板文件
        inno_template = self.project_root / self.INNO_SETUP_TEMPLATE
        if inno_template.exists():
            try:
                results["inno_setup_template"] = self._update_version_in_inno_template(inno_template, new_version)
            except Exception as e:
                print(f"更新 {self.INNO_SETUP_TEMPLATE} 失败: {e}")
                results["inno_setup_template"] = False
        else:
            results["inno_setup_template"] = False
        
        return results
    
    def _update_config_json_versions(self, new_version: str) -> Dict[str, bool]:
        """
        更新 config.json 文件中的 client_version
        
        Args:
            new_version: 新版本号
        
        Returns:
            字典，键为配置文件路径，值为是否更新成功
        """
        results = {}
        
        # 更新员工端的 config.json
        employee_config = self.project_root / "ui_client" / "config.json"
        if employee_config.exists():
            try:
                results["employee_config"] = self._update_config_json_version(employee_config, new_version)
            except Exception as e:
                print(f"更新 {employee_config} 失败: {e}")
                results["employee_config"] = False
        else:
            results["employee_config"] = False
        
        # 更新管理端的 config.json
        admin_config = self.project_root / "admin_ui_client" / "config.json"
        if admin_config.exists():
            try:
                results["admin_config"] = self._update_config_json_version(admin_config, new_version)
            except Exception as e:
                print(f"更新 {admin_config} 失败: {e}")
                results["admin_config"] = False
        else:
            results["admin_config"] = False
        
        return results
    
    def _update_config_json_version(self, config_file: Path, new_version: str) -> bool:
        """
        更新单个 config.json 文件中的 client_version
        
        Args:
            config_file: config.json 文件路径
            new_version: 新版本号
        
        Returns:
            是否更新成功
        """
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 更新 client_version
            config["client_version"] = new_version
            
            # 写回文件
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False, sort_keys=True)
            
            return True
        except Exception as e:
            print(f"更新 {config_file} 中的版本号失败: {e}")
            return False
    
    def _update_config_manager_defaults(self, new_version: str) -> Dict[str, bool]:
        """
        更新 config_manager.py 文件中的 DEFAULT_CONFIG 的 client_version
        
        Args:
            new_version: 新版本号
        
        Returns:
            字典，键为配置文件路径，值为是否更新成功
        """
        results = {}
        
        # 更新员工端的 config_manager.py
        employee_config_manager = self.project_root / "ui_client" / "utils" / "config_manager.py"
        if employee_config_manager.exists():
            try:
                results["employee_config_manager"] = self._update_config_manager_version(employee_config_manager, new_version)
            except Exception as e:
                print(f"更新 {employee_config_manager} 失败: {e}")
                results["employee_config_manager"] = False
        else:
            results["employee_config_manager"] = False
        
        # 更新管理端的 config_manager.py
        admin_config_manager = self.project_root / "admin_ui_client" / "utils" / "config_manager.py"
        if admin_config_manager.exists():
            try:
                results["admin_config_manager"] = self._update_config_manager_version(admin_config_manager, new_version)
            except Exception as e:
                print(f"更新 {admin_config_manager} 失败: {e}")
                results["admin_config_manager"] = False
        else:
            results["admin_config_manager"] = False
        
        return results
    
    def _update_config_manager_version(self, config_manager_file: Path, new_version: str) -> bool:
        """
        更新单个 config_manager.py 文件中的 DEFAULT_CONFIG 的 client_version
        
        Args:
            config_manager_file: config_manager.py 文件路径
            new_version: 新版本号
        
        Returns:
            是否更新成功
        """
        try:
            with open(config_manager_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # 使用正则表达式匹配并更新 client_version
            # 匹配格式: "client_version": "x.x.x",  # 客户端版本号（格式：x.x.x）
            # 只匹配版本号部分（x.x.x格式），保留引号和后面的内容
            pattern = r'("client_version":\s*")[0-9]+\.[0-9]+\.[0-9]+(")'
            replacement = f'\\g<1>{new_version}\\g<2>'
            new_content = re.sub(pattern, replacement, content)
            
            # 如果内容有变化，写回文件
            if new_content != original_content:
                with open(config_manager_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return True
            else:
                # 如果没有变化，可能是版本号已经是新值
                return True
        except Exception as e:
            print(f"更新 {config_manager_file} 中的版本号失败: {e}")
            return False
    
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
            
            # 对于 Windows/Linux 的 build.spec，查找注释形式的版本号
            # 格式: # version: x.x.x
            if spec_file.name == "build.spec":
                match = re.search(r"^#\s*version:\s*([^\n]+)", content, re.MULTILINE)
                if match:
                    return match.group(1).strip()
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
            
            # 对于 Windows/Linux 的 build.spec 文件，如果没有版本号字段，添加注释形式的版本号
            # 检查是否是 build.spec（不是 build_macos.spec）
            if spec_file.name == "build.spec" and "version=" not in content:
                # 在文件开头添加版本号注释
                if not content.startswith(f"# version: {new_version}\n"):
                    # 检查是否已有版本号注释
                    version_comment_pattern = r"^#\s*version:\s*[^\n]+\n"
                    if re.search(version_comment_pattern, content, re.MULTILINE):
                        # 更新现有注释
                        content = re.sub(
                            version_comment_pattern,
                            f"# version: {new_version}\n",
                            content,
                            flags=re.MULTILINE
                        )
                    else:
                        # 添加新注释（在文件开头）
                        content = f"# version: {new_version}\n{content}"
            
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
    
    def _update_version_in_inno_template(self, template_file: Path, new_version: str) -> bool:
        """
        更新 Inno Setup 模板文件中的版本号
        
        Args:
            template_file: Inno Setup 模板文件路径
            new_version: 新版本号
        
        Returns:
            是否更新成功
        """
        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # 更新 AppVersion=...
            content = re.sub(
                r"AppVersion\s*=\s*[^\n]+",
                f"AppVersion={new_version}",
                content
            )
            
            # 如果内容有变化，写回文件
            if content != original_content:
                with open(template_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True
            else:
                # 如果没有变化，可能是版本号已经是新值
                return True
        except Exception as e:
            print(f"更新 Inno Setup 模板版本号失败: {e}")
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
        
        # 获取 config.json 中的版本号
        config_versions = self._get_config_json_versions()
        all_versions.update(config_versions)
        
        # 获取 config_manager.py 中的版本号
        config_manager_versions = self._get_config_manager_versions()
        all_versions.update(config_manager_versions)
        
        # 检查版本是否一致
        unique_versions = set(v for v in all_versions.values() if v is not None)
        is_consistent = len(unique_versions) <= 1
        
        return {
            "current_version": current_version,
            "all_versions": all_versions,
            "is_consistent": is_consistent,
            "unique_versions": list(unique_versions) if unique_versions else []
        }
    
    def _get_config_json_versions(self) -> Dict[str, Optional[str]]:
        """
        获取 config.json 文件中的版本号
        
        Returns:
            字典，键为配置文件标识，值为版本号
        """
        versions = {}
        
        # 读取员工端的 config.json
        employee_config = self.project_root / "ui_client" / "config.json"
        if employee_config.exists():
            try:
                with open(employee_config, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                versions["employee_config"] = config.get("client_version")
            except Exception:
                versions["employee_config"] = None
        else:
            versions["employee_config"] = None
        
        # 读取管理端的 config.json
        admin_config = self.project_root / "admin_ui_client" / "config.json"
        if admin_config.exists():
            try:
                with open(admin_config, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                versions["admin_config"] = config.get("client_version")
            except Exception:
                versions["admin_config"] = None
        else:
            versions["admin_config"] = None
        
        return versions
    
    def _get_config_manager_versions(self) -> Dict[str, Optional[str]]:
        """
        获取 config_manager.py 文件中的版本号
        
        Returns:
            字典，键为配置文件标识，值为版本号
        """
        versions = {}
        
        # 读取员工端的 config_manager.py
        employee_config_manager = self.project_root / "ui_client" / "utils" / "config_manager.py"
        if employee_config_manager.exists():
            try:
                version = self._read_version_from_config_manager(employee_config_manager)
                versions["employee_config_manager"] = version
            except Exception:
                versions["employee_config_manager"] = None
        else:
            versions["employee_config_manager"] = None
        
        # 读取管理端的 config_manager.py
        admin_config_manager = self.project_root / "admin_ui_client" / "utils" / "config_manager.py"
        if admin_config_manager.exists():
            try:
                version = self._read_version_from_config_manager(admin_config_manager)
                versions["admin_config_manager"] = version
            except Exception:
                versions["admin_config_manager"] = None
        else:
            versions["admin_config_manager"] = None
        
        return versions
    
    def _read_version_from_config_manager(self, config_manager_file: Path) -> Optional[str]:
        """
        从 config_manager.py 文件中读取 client_version
        
        Args:
            config_manager_file: config_manager.py 文件路径
        
        Returns:
            版本号字符串，如果未找到则返回 None
        """
        try:
            with open(config_manager_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 查找 "client_version": "x.x.x"
            match = re.search(r'"client_version":\s*"([0-9]+\.[0-9]+\.[0-9]+)"', content)
            if match:
                return match.group(1)
        except Exception as e:
            print(f"读取 config_manager.py 版本号失败: {e}")
        
        return None

