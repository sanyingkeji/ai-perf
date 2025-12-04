#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全面对比本地打包和 GitHub Actions 打包的差异
找出所有缺失的文件和目录
"""

import os
from pathlib import Path
from collections import defaultdict

def get_all_items(path, base_path=None):
    """递归获取所有文件和目录"""
    if base_path is None:
        base_path = path
    
    items = {
        'files': [],
        'dirs': [],
        'symlinks': []
    }
    
    if not path.exists():
        return items
    
    try:
        for item in path.iterdir():
            rel_path = item.relative_to(base_path)
            
            if item.is_symlink():
                items['symlinks'].append(str(rel_path))
            elif item.is_file():
                items['files'].append(str(rel_path))
            elif item.is_dir():
                items['dirs'].append(str(rel_path))
                # 递归获取子目录内容
                sub_items = get_all_items(item, base_path)
                items['files'].extend(sub_items['files'])
                items['dirs'].extend(sub_items['dirs'])
                items['symlinks'].extend(sub_items['symlinks'])
    except PermissionError:
        pass
    
    return items

def compare_app_bundles(local_path, github_path):
    """全面对比两个 .app 文件的结构差异"""
    
    local_app = Path(local_path)
    github_app = Path(github_path)
    
    if not local_app.exists():
        print(f"❌ 本地应用包不存在: {local_app}")
        return
    
    if not github_app.exists():
        print(f"❌ GitHub 应用包不存在: {github_app}")
        return
    
    print("=" * 80)
    print("全面对比本地打包和 GitHub Actions 打包的差异")
    print("=" * 80)
    
    # 对比 Frameworks 目录
    print("\n1. 对比 Frameworks 目录")
    local_frameworks = local_app / "Contents" / "Frameworks"
    github_frameworks = github_app / "Contents" / "Frameworks"
    
    if local_frameworks.exists() and github_frameworks.exists():
        local_fw_items = get_all_items(local_frameworks)
        github_fw_items = get_all_items(github_frameworks)
        
        local_fw_all = set(local_fw_items['files'] + local_fw_items['dirs'] + local_fw_items['symlinks'])
        github_fw_all = set(github_fw_items['files'] + github_fw_items['dirs'] + github_fw_items['symlinks'])
        
        missing_in_github = local_fw_all - github_fw_all
        extra_in_github = github_fw_all - local_fw_all
        
        if missing_in_github:
            print(f"\n  ⚠ GitHub Frameworks 中缺失的项 ({len(missing_in_github)} 个):")
            # 按类型分组
            missing_dirs = [x for x in missing_in_github if '/' not in x or x.endswith('/')]
            missing_files = [x for x in missing_in_github if x not in missing_dirs]
            
            if missing_dirs:
                print(f"\n  缺失的目录 ({len(missing_dirs)} 个):")
                for item in sorted(missing_dirs)[:50]:
                    print(f"    - {item}")
                if len(missing_dirs) > 50:
                    print(f"    ... 还有 {len(missing_dirs) - 50} 个目录")
            
            if missing_files:
                print(f"\n  缺失的文件 ({len(missing_files)} 个):")
                for item in sorted(missing_files)[:50]:
                    print(f"    - {item}")
                if len(missing_files) > 50:
                    print(f"    ... 还有 {len(missing_files) - 50} 个文件")
        
        if extra_in_github:
            print(f"\n  ℹ GitHub Frameworks 中额外的项 ({len(extra_in_github)} 个):")
            for item in sorted(list(extra_in_github))[:20]:
                print(f"    + {item}")
            if len(extra_in_github) > 20:
                print(f"    ... 还有 {len(extra_in_github) - 20} 个项")
    
    # 对比 Resources 目录
    print("\n2. 对比 Resources 目录")
    local_resources = local_app / "Contents" / "Resources"
    github_resources = github_app / "Contents" / "Resources"
    
    if local_resources.exists() and github_resources.exists():
        local_res_items = get_all_items(local_resources)
        github_res_items = get_all_items(github_resources)
        
        local_res_all = set(local_res_items['files'] + local_res_items['dirs'] + local_res_items['symlinks'])
        github_res_all = set(github_res_items['files'] + github_res_items['dirs'] + github_res_items['symlinks'])
        
        missing_in_github = local_res_all - github_res_all
        extra_in_github = github_res_all - local_res_all
        
        if missing_in_github:
            print(f"\n  ⚠ GitHub Resources 中缺失的项 ({len(missing_in_github)} 个):")
            # 按类型分组
            missing_dirs = [x for x in missing_in_github if '/' not in x or x.endswith('/')]
            missing_files = [x for x in missing_in_github if x not in missing_dirs]
            
            if missing_dirs:
                print(f"\n  缺失的目录 ({len(missing_dirs)} 个):")
                for item in sorted(missing_dirs)[:30]:
                    print(f"    - {item}")
                if len(missing_dirs) > 30:
                    print(f"    ... 还有 {len(missing_dirs) - 30} 个目录")
            
            if missing_files:
                print(f"\n  缺失的文件 ({len(missing_files)} 个):")
                for item in sorted(missing_files)[:30]:
                    print(f"    - {item}")
                if len(missing_files) > 30:
                    print(f"    ... 还有 {len(missing_files) - 30} 个文件")
        
        if extra_in_github:
            print(f"\n  ℹ GitHub Resources 中额外的项 ({len(extra_in_github)} 个):")
            for item in sorted(list(extra_in_github))[:20]:
                print(f"    + {item}")
            if len(extra_in_github) > 20:
                print(f"    ... 还有 {len(extra_in_github) - 20} 个项")
    
    # 检查关键目录和文件
    print("\n3. 检查关键目录和文件")
    key_items = [
        "Frameworks/themes",
        "Frameworks/shiboken6",
        "Frameworks/python3.10",
        "Frameworks/paramiko-4.0.0.dist-info",
        "Resources/themes",
        "Resources/shiboken6",
        "Resources/python3.10",
    ]
    
    for item_path in key_items:
        parts = item_path.split('/')
        if parts[0] == "Frameworks":
            local_item = local_frameworks / '/'.join(parts[1:])
            github_item = github_frameworks / '/'.join(parts[1:])
        else:
            local_item = local_resources / '/'.join(parts[1:])
            github_item = github_resources / '/'.join(parts[1:])
        
        local_exists = local_item.exists()
        github_exists = github_item.exists()
        
        if local_exists and not github_exists:
            item_type = "目录" if local_item.is_dir() else "文件"
            print(f"  ❌ {item_path}: 本地存在 ({item_type})，GitHub 缺失")
        elif not local_exists and github_exists:
            item_type = "目录" if github_item.is_dir() else "文件"
            print(f"  ⚠ {item_path}: GitHub 存在 ({item_type})，本地缺失")
        elif local_exists and github_exists:
            local_type = "目录" if local_item.is_dir() else ("符号链接" if local_item.is_symlink() else "文件")
            github_type = "目录" if github_item.is_dir() else ("符号链接" if github_item.is_symlink() else "文件")
            if local_type != github_type:
                print(f"  ⚠ {item_path}: 类型不一致 (本地: {local_type}, GitHub: {github_type})")
            else:
                print(f"  ✓ {item_path}: 都存在 ({local_type})")
    
    # 检查 .dist-info 目录
    print("\n4. 检查 .dist-info 目录（包元数据）")
    if local_frameworks.exists():
        local_dist_info = [d.name for d in local_frameworks.iterdir() if d.is_dir() and '.dist-info' in d.name]
        if github_frameworks.exists():
            github_dist_info = [d.name for d in github_frameworks.iterdir() if d.is_dir() and '.dist-info' in d.name]
            
            missing_dist_info = set(local_dist_info) - set(github_dist_info)
            if missing_dist_info:
                print(f"  ⚠ GitHub Frameworks 中缺失的 .dist-info 目录 ({len(missing_dist_info)} 个):")
                for item in sorted(missing_dist_info):
                    print(f"    - {item}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    local_path = "/Users/liuwei/Downloads/ai-perf/admin_ui_client/dist/intel/Ai Perf Admin.app"
    github_path = "/Users/liuwei/Downloads/ai-perf/admin_ui_client/dist/from_github/admin/intel/Ai Perf Admin.app"
    
    compare_app_bundles(local_path, github_path)

