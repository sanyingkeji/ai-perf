#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比本地打包和 GitHub Actions 打包的差异
"""

import os
import zipfile
from pathlib import Path
import subprocess

def compare_app_bundles(local_path, github_path):
    """对比两个 .app 文件的结构差异"""
    
    local_app = Path(local_path)
    github_app = Path(github_path)
    
    if not local_app.exists():
        print(f"❌ 本地应用包不存在: {local_app}")
        return
    if not github_app.exists():
        print(f"❌ GitHub 应用包不存在: {github_app}")
        return
    
    print("=" * 80)
    print("对比本地打包和 GitHub Actions 打包的差异")
    print("=" * 80)
    
    # 1. 对比 base_library.zip
    print("\n1. 对比 base_library.zip")
    local_zip = local_app / "Contents" / "Frameworks" / "base_library.zip"
    github_zip_resources = github_app / "Contents" / "Resources" / "base_library.zip"
    github_zip_frameworks = github_app / "Contents" / "Frameworks" / "base_library.zip"
    
    github_zip = None
    if github_zip_frameworks.exists():
        github_zip = github_zip_frameworks
        print(f"  GitHub: {github_zip} (Frameworks)")
    elif github_zip_resources.exists():
        github_zip = github_zip_resources
        print(f"  GitHub: {github_zip} (Resources)")
    
    if local_zip.exists() and github_zip and github_zip.exists():
        print(f"  本地: {local_zip}")
        
        # 对比内容
        local_files = set()
        github_files = set()
        
        with zipfile.ZipFile(local_zip, 'r') as z:
            local_files = set(z.namelist())
        
        with zipfile.ZipFile(github_zip, 'r') as z:
            github_files = set(z.namelist())
        
        missing_in_github = local_files - github_files
        extra_in_github = github_files - local_files
        
        if missing_in_github:
            print(f"  ⚠ GitHub 中缺失的文件 ({len(missing_in_github)} 个):")
            for f in sorted(list(missing_in_github))[:30]:
                print(f"    - {f}")
            if len(missing_in_github) > 30:
                print(f"    ... 还有 {len(missing_in_github) - 30} 个文件")
        
        if extra_in_github:
            print(f"  ℹ GitHub 中额外的文件 ({len(extra_in_github)} 个):")
            for f in sorted(list(extra_in_github))[:10]:
                print(f"    + {f}")
        
        # 检查关键模块
        key_modules = ['encodings/__init__.pyc', 'struct.pyc', '_struct.cpython']
        print(f"\n  检查关键模块:")
        for module in key_modules:
            local_has = any(m.endswith(module) or module in m for m in local_files)
            github_has = any(m.endswith(module) or module in m for m in github_files)
            status = "✓" if (local_has and github_has) else ("⚠" if local_has else "✗")
            print(f"    {status} {module}: 本地={local_has}, GitHub={github_has}")
    elif local_zip.exists():
        print(f"  ⚠ 本地有 base_library.zip，但 GitHub 中没有找到")
    elif github_zip and github_zip.exists():
        print(f"  ⚠ GitHub 有 base_library.zip，但本地中没有找到")
    
    # 2. 对比 lib-dynload 目录
    print("\n2. 对比 lib-dynload 目录")
    local_dynload = local_app / "Contents" / "Frameworks" / "python3.10" / "lib-dynload"
    github_dynload_frameworks = github_app / "Contents" / "Frameworks" / "python3.10" / "lib-dynload"
    github_dynload_resources = github_app / "Contents" / "Resources" / "python3.10" / "lib-dynload"
    
    github_dynload = None
    if github_dynload_frameworks.exists():
        github_dynload = github_dynload_frameworks
        print(f"  GitHub: {github_dynload} (Frameworks)")
    elif github_dynload_resources.exists():
        github_dynload = github_dynload_resources
        print(f"  GitHub: {github_dynload} (Resources)")
    
    if local_dynload.exists():
        print(f"  本地: {local_dynload}")
        local_so_files = {f.name for f in local_dynload.glob("*.so")}
        print(f"  本地 .so 文件数: {len(local_so_files)}")
        
        if github_dynload and github_dynload.exists():
            github_so_files = {f.name for f in github_dynload.glob("*.so")}
            print(f"  GitHub .so 文件数: {len(github_so_files)}")
            
            missing_so = local_so_files - github_so_files
            if missing_so:
                print(f"  ⚠ GitHub 中缺失的 .so 文件 ({len(missing_so)} 个):")
                for f in sorted(list(missing_so)):
                    print(f"    - {f}")
            
            extra_so = github_so_files - local_so_files
            if extra_so:
                print(f"  ℹ GitHub 中额外的 .so 文件 ({len(extra_so)} 个):")
                for f in sorted(list(extra_so))[:10]:
                    print(f"    + {f}")
            
            # 检查关键模块
            key_so = ['_struct.cpython-310-darwin.so', '_ctypes.cpython-310-darwin.so', 
                     '_socket.cpython-310-darwin.so', '_ssl.cpython-310-darwin.so']
            print(f"\n  检查关键 .so 文件:")
            for so in key_so:
                local_has = so in local_so_files
                github_has = so in github_so_files
                status = "✓" if (local_has and github_has) else ("⚠" if local_has else "✗")
                print(f"    {status} {so}: 本地={local_has}, GitHub={github_has}")
        else:
            print(f"  ❌ GitHub 中没有找到 lib-dynload 目录！")
            print(f"    这是导致 _struct 等模块缺失的根本原因！")
    else:
        print(f"  ⚠ 本地中没有找到 lib-dynload 目录")
        if github_dynload and github_dynload.exists():
            print(f"  ℹ GitHub 中有 lib-dynload 目录: {github_dynload}")
    
    # 3. 检查 python3.10 目录结构
    print("\n3. 检查 python3.10 目录结构")
    local_python = local_app / "Contents" / "Frameworks" / "python3.10"
    github_python_frameworks = github_app / "Contents" / "Frameworks" / "python3.10"
    github_python_resources = github_app / "Contents" / "Resources" / "python3.10"
    
    if local_python.exists():
        print(f"  本地: {local_python}")
        local_python_items = [f.name for f in local_python.iterdir()]
        print(f"  本地 python3.10 目录内容: {', '.join(sorted(local_python_items))}")
    
    github_python = None
    if github_python_frameworks.exists():
        github_python = github_python_frameworks
        print(f"  GitHub: {github_python} (Frameworks)")
    elif github_python_resources.exists():
        github_python = github_python_resources
        print(f"  GitHub: {github_python} (Resources)")
    
    if github_python and github_python.exists():
        github_python_items = [f.name for f in github_python.iterdir()]
        print(f"  GitHub python3.10 目录内容: {', '.join(sorted(github_python_items))}")
    else:
        print(f"  ❌ GitHub 中没有找到 python3.10 目录！")
    
    # 4. 对比 Frameworks 目录结构
    print("\n4. 对比 Frameworks 目录结构")
    local_frameworks = local_app / "Contents" / "Frameworks"
    github_frameworks = github_app / "Contents" / "Frameworks"
    
    if local_frameworks.exists():
        local_fw_items = sorted([f.name for f in local_frameworks.iterdir()])
        print(f"  本地 Frameworks 目录项数: {len(local_fw_items)}")
        print(f"  本地 Frameworks 主要项: {', '.join(local_fw_items[:20])}")
    
    if github_frameworks.exists():
        github_fw_items = sorted([f.name for f in github_frameworks.iterdir()])
        print(f"  GitHub Frameworks 目录项数: {len(github_fw_items)}")
        print(f"  GitHub Frameworks 主要项: {', '.join(github_fw_items[:20])}")
        
        missing_in_github_fw = set(local_fw_items) - set(github_fw_items) if local_frameworks.exists() else set()
        if missing_in_github_fw:
            print(f"  ⚠ GitHub Frameworks 中缺失的项 ({len(missing_in_github_fw)} 个):")
            for item in sorted(list(missing_in_github_fw)):
                print(f"    - {item}")
    
    # 5. 检查 Resources 目录
    print("\n5. 检查 Resources 目录")
    local_resources = local_app / "Contents" / "Resources"
    github_resources = github_app / "Contents" / "Resources"
    
    if local_resources.exists():
        local_res_items = sorted([f.name for f in local_resources.iterdir()])
        print(f"  本地 Resources 目录项数: {len(local_res_items)}")
        print(f"  本地 Resources 主要项: {', '.join(local_res_items[:20])}")
    
    if github_resources.exists():
        github_res_items = sorted([f.name for f in github_resources.iterdir()])
        print(f"  GitHub Resources 目录项数: {len(github_res_items)}")
        print(f"  GitHub Resources 主要项: {', '.join(github_res_items[:20])}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    local_path = "/Users/liuwei/Downloads/ai-perf/admin_ui_client/dist/intel/Ai Perf Admin.app"
    github_path = "/Users/liuwei/Downloads/ai-perf/admin_ui_client/dist/from_github/admin/intel/Ai Perf Admin.app"
    
    compare_app_bundles(local_path, github_path)

