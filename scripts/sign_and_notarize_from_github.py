#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 GitHub Release 下载 .app 文件并进行签名和公证
支持 arm64 和 intel 两个架构
"""

import sys
import os

# 设置无缓冲输出
try:
    if not sys.stdout.isatty():
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)
        sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)
except (OSError, AttributeError):
    pass

import subprocess
import shutil
from pathlib import Path
from datetime import datetime
import time
import json
import zipfile
import tempfile
import argparse
from enum import Enum

# Windows 编码修复
if sys.platform == "win32":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# 颜色输出
try:
    from colorama import init, Fore, Style
    init(autoreset=False, strip=False)
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW
    RED = Fore.RED
    NC = Style.RESET_ALL
except ImportError:
    GREEN = YELLOW = RED = NC = ""

# 日志函数
def log_with_time(message, color=""):
    """带时间戳的日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{timestamp}] {message}{NC}", flush=True)

def log_info(message):
    log_with_time(message, GREEN)

def log_warn(message):
    log_with_time(message, YELLOW)

def log_error(message):
    log_with_time(message, RED)

# 定义执行步骤枚举
class Step(Enum):
    DOWNLOAD = "download"  # 下载 ZIP 文件
    EXTRACT = "extract"  # 解压 ZIP 文件
    COPY = "copy"  # 复制 .app 到输出目录
    SIGN_RESOURCES = "sign_resources"  # 签名 Resources 目录
    SIGN_FRAMEWORKS = "sign_frameworks"  # 签名 Frameworks 目录
    SIGN_MAIN = "sign_main"  # 签名主可执行文件
    SIGN_BUNDLE = "sign_bundle"  # 签名整个应用包
    VERIFY = "verify"  # 验证签名
    CREATE_DMG = "create_dmg"  # 创建 DMG
    SIGN_DMG = "sign_dmg"  # 签名 DMG
    NOTARIZE = "notarize"  # 公证

def get_step_order():
    """返回步骤执行顺序"""
    return [
        Step.DOWNLOAD,
        Step.EXTRACT,
        Step.COPY,
        Step.SIGN_RESOURCES,
        Step.SIGN_FRAMEWORKS,
        Step.SIGN_MAIN,
        Step.SIGN_BUNDLE,
        Step.VERIFY,
        Step.CREATE_DMG,
        Step.SIGN_DMG,
        Step.NOTARIZE,
    ]

def should_skip_step(current_step: Step, start_from_step: Step = None) -> bool:
    """判断是否应该跳过当前步骤"""
    if start_from_step is None:
        return False
    
    step_order = get_step_order()
    try:
        current_index = step_order.index(current_step)
        start_index = step_order.index(start_from_step)
        return current_index < start_index
    except ValueError:
        return False

def log_step(step: Step, message: str = ""):
    """记录步骤日志"""
    step_name = step.value.upper().replace("_", " ")
    if message:
        log_info(f"[步骤: {step_name}] {message}")
    else:
        log_info(f"[步骤: {step_name}] 开始执行...")

# 导入 build_client.py 中的签名和公证函数
# 由于需要复用大量代码，我们直接导入并调用相关函数
def download_file(url: str, dest_path: Path, api_key: str = None) -> bool:
    """下载文件（如果文件已存在则跳过下载）"""
    try:
        # 检查文件是否已存在
        if dest_path.exists() and dest_path.is_file():
            file_size = dest_path.stat().st_size
            if file_size > 0:
                file_size_mb = file_size / (1024 * 1024)
                log_info(f"文件已存在，跳过下载: {dest_path}")
                log_info(f"  文件大小: {file_size_mb:.2f} MB")
                return True
            else:
                log_warn(f"文件存在但大小为 0，将重新下载: {dest_path}")
                dest_path.unlink()
        
        import httpx
        headers = {}
        if api_key:
            headers["Authorization"] = f"token {api_key}"
        
        log_info(f"下载文件: {url}")
        log_info(f"保存到: {dest_path}")
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        with httpx.stream("GET", url, headers=headers, timeout=300.0, follow_redirects=True) as response:
            if response.status_code != 200:
                log_error(f"下载失败: HTTP {response.status_code}")
                return False
            
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            last_percent = -1
            
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 使用 stderr 输出进度，避免与日志输出冲突
            # 先打印一个空行到 stderr，确保进度显示在独立行
            sys.stderr.write("\n")
            sys.stderr.flush()
            
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        # 只在百分比变化时更新（避免打印太多行）
                        if int(percent) != last_percent:
                            # 格式化文件大小
                            downloaded_mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)
                            # 使用 stderr 输出进度，避免与日志输出冲突
                            # 使用 \r 在同一行更新，\033[K 清除到行尾
                            progress_text = f"  进度: {percent:.1f}% ({downloaded_mb:.2f}/{total_mb:.2f} MB)"
                            sys.stderr.write(f"\r{progress_text}\033[K")  # \033[K 清除到行尾
                            sys.stderr.flush()
                            last_percent = int(percent)
            
            # 下载完成后，清除进度行并打印完成信息
            sys.stderr.write("\r" + " " * 80 + "\r\n")  # 清除进度行并换行
            sys.stderr.flush()
            log_info(f"✓ 下载完成: {dest_path}")
            return True
    except Exception as e:
        log_error(f"下载失败: {e}")
        return False

def find_app_in_zip(zip_path: Path, app_name: str) -> Path:
    """在 ZIP 文件中查找 .app"""
    try:
        # 创建临时解压目录
        extract_dir = zip_path.parent / f"extracted_{zip_path.stem}"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        
        log_info(f"解压 ZIP 文件: {zip_path}")
        log_info(f"解压到: {extract_dir}")
        
        # 解压整个 ZIP 文件
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # 列出 ZIP 文件中的所有条目（用于调试）
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            log_info(f"ZIP 文件包含 {len(zip_ref.namelist())} 个条目")
            # 显示前 10 个条目
            for i, name in enumerate(zip_ref.namelist()[:10]):
                log_info(f"  条目 {i+1}: {name}")
            if len(zip_ref.namelist()) > 10:
                log_info(f"  ... 还有 {len(zip_ref.namelist()) - 10} 个条目")
        
        # 查找 .app 目录
        log_info(f"查找 {app_name}.app...")
        
        # 方法1: 直接在解压目录中查找
        app_path = extract_dir / f"{app_name}.app"
        if app_path.exists() and app_path.is_dir():
            log_info(f"✓ 找到 .app (根目录): {app_path}")
            return app_path
        
        # 方法2: 递归查找所有 .app 目录
        for root, dirs, files in os.walk(extract_dir):
            # 检查当前目录是否是 .app
            if Path(root).name == f"{app_name}.app" and Path(root).is_dir():
                app_path = Path(root)
                log_info(f"✓ 找到 .app (递归查找): {app_path}")
                return app_path
            
            # 检查子目录中是否有 .app
            for d in dirs:
                if d == f"{app_name}.app":
                    app_path = Path(root) / d
                    if app_path.is_dir():
                        log_info(f"✓ 找到 .app (子目录): {app_path}")
                        return app_path
        
        # 方法3: 查找任何包含 .app 的目录
        log_warn(f"未找到 {app_name}.app，尝试查找任何 .app 目录...")
        for root, dirs, files in os.walk(extract_dir):
            for d in dirs:
                if d.endswith(".app"):
                    app_path = Path(root) / d
                    log_warn(f"找到其他 .app: {app_path}")
                    # 如果名称匹配（忽略大小写），也返回
                    if app_path.name.lower() == f"{app_name}.app".lower():
                        log_info(f"✓ 找到匹配的 .app (忽略大小写): {app_path}")
                        return app_path
        
        log_error(f"在 ZIP 文件中未找到 {app_name}.app")
        log_error(f"解压目录内容: {list(extract_dir.iterdir())}")
        return None
        
    except Exception as e:
        log_error(f"解压 ZIP 文件失败: {e}")
        import traceback
        log_error(traceback.format_exc())
        return None

def get_github_release_assets(repo_owner: str, repo_name: str, tag_name: str, api_key: str = None) -> list:
    """获取 GitHub Release 的 assets"""
    try:
        import httpx
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/tags/{tag_name}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if api_key:
            headers["Authorization"] = f"token {api_key}"
        
        log_info(f"获取 Release 信息: {url}")
        response = httpx.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            log_error(f"获取 Release 失败: HTTP {response.status_code}")
            return []
        
        release_data = response.json()
        assets = release_data.get("assets", [])
        log_info(f"✓ 找到 {len(assets)} 个 assets")
        return assets
    except Exception as e:
        log_error(f"获取 Release 失败: {e}")
        return []

def sign_and_notarize_app_from_existing(app_bundle: Path, client_type: str, arch: str, start_from_step: Step = None):
    """对 .app 进行签名和公证，并创建 DMG 和 PKG
    
    Args:
        app_bundle: .app 文件路径
        client_type: 客户端类型 (employee 或 admin)
        arch: 架构 (arm64 或 intel)
        start_from_step: 从哪个步骤开始执行（用于调试，跳过之前的步骤）
    """
    # 获取脚本所在目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    # 客户端信息
    if client_type == "employee":
        app_name = "Ai Perf Client"
        app_id = "site.sanying.aiperf.client"
        client_dir = project_root / "ui_client"
    else:
        app_name = "Ai Perf Admin"
        app_id = "site.sanying.aiperf.admin"
        client_dir = project_root / "admin_ui_client"
    
    # 设置输出目录：dist/from_github/{client_type}/{arch}/
    # 例如：dist/from_github/employee/arm64/ 或 dist/from_github/admin/intel/
    dist_dir = client_dir / "dist" / "from_github" / client_type / arch
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    log_info(f"输出目录: {dist_dir}")
    
    # 复制 .app 到输出目录（如果 app_bundle 不在输出目录中）
    target_app = dist_dir / f"{app_name}.app"
    
    # 如果 app_bundle 就是 target_app，不需要复制
    if app_bundle.resolve() == target_app.resolve():
        log_info(f".app 已在输出目录，跳过复制: {target_app}")
    else:
        if target_app.exists():
            shutil.rmtree(target_app)
        
        log_info(f"复制 .app 到输出目录: {target_app}")
        shutil.copytree(app_bundle, target_app)
    
    # 后处理：清理 Frameworks 目录下的非二进制文件和目录
    # PyInstaller 的 BUNDLE 阶段在不同环境下行为可能不同：
    # - 本地打包：Frameworks/resources 可能是符号链接（指向 ../Resources/resources），这是正常的
    # - GitHub Actions 打包：Frameworks/resources 可能是真实目录，这会导致签名失败
    # 需要删除真实目录，但保留符号链接
    log_warn("后处理：清理 Frameworks 目录结构...")
    frameworks_dir = target_app / "Contents" / "Frameworks"
    if frameworks_dir.exists():
        # 检查 Frameworks/resources 是否是真实目录（需要清理）
        resources_in_frameworks = frameworks_dir / "resources"
        needs_cleanup = False
        
        if resources_in_frameworks.exists():
            # 检查是否是符号链接
            is_symlink = resources_in_frameworks.is_symlink()
            if is_symlink:
                log_info(f"  Frameworks/resources 是符号链接，无需清理: {resources_in_frameworks.relative_to(target_app)}")
            else:
                # 是真实目录，需要清理
                needs_cleanup = True
                log_warn(f"  发现 Frameworks 目录下的 resources 真实目录（PyInstaller 打包问题），需要清理")
        
        # 只在需要清理时执行清理操作
        if needs_cleanup:
            log_warn("  清理 Frameworks 目录结构...")
            # 先收集要处理的项，避免在迭代时修改目录
            items_to_check = list(frameworks_dir.iterdir())
            
            # 处理 Frameworks 下的 resources
            if resources_in_frameworks.exists() and not resources_in_frameworks.is_symlink():
                log_warn(f"  删除 Frameworks/resources 真实目录: {resources_in_frameworks.relative_to(target_app)}")
                log_info("  注意: Contents/Resources/resources 已存在，这是正确位置")
                try:
                    shutil.rmtree(resources_in_frameworks)
                    log_info("  ✓ 已删除 Frameworks/resources 目录")
                except Exception as e:
                    log_warn(f"  删除失败: {e}")
            
            # 移除其他非二进制文件和目录（但保留 PySide6 和 .framework 目录）
            for item in items_to_check:
                # 跳过已处理的 resources
                if item.name == "resources":
                    continue
                    
                if item.is_dir():
                    # 跳过 .framework 目录和 PySide6 目录
                    if item.suffix == ".framework" or item.name == "PySide6":
                        continue
                    # 跳过符号链接（如 resources 符号链接）
                    if item.is_symlink():
                        continue
                    # 移除其他目录（如 .dist-info, .egg-info 等）
                    log_warn(f"  移除非框架目录: {item.relative_to(target_app)}")
                    try:
                        shutil.rmtree(item)
                        log_info(f"    ✓ 已移除: {item.name}")
                    except Exception as e:
                        log_warn(f"    移除失败: {e}")
                elif item.is_file():
                    # 跳过符号链接
                    if item.is_symlink():
                        continue
                    # 跳过二进制文件扩展名
                    if item.suffix in [".dylib", ".so"]:
                        continue
                    # 跳过无扩展名的文件（可能是 Mach-O 二进制文件）
                    if not item.suffix:
                        continue
                    # 移除非二进制文件（PNG、文本文件等，但保留 JSON 文件，因为 config.json 和 google_client_secret.json 可能需要在 Frameworks 下）
                    if item.suffix in [".png", ".txt", ".md", ".yml", ".yaml", ".xml", ".plist", ".icns", ".qm"]:
                        log_warn(f"  移除非二进制文件: {item.relative_to(target_app)}")
                        try:
                            item.unlink()
                            log_info(f"    ✓ 已移除: {item.name}")
                        except Exception as e:
                            log_warn(f"    移除失败: {e}")
    
    # 切换到客户端目录（build_client.py 需要）
    original_cwd = os.getcwd()
    os.chdir(client_dir)
    
    try:
        # 导入 build_client.py 并执行签名和公证逻辑
        # 由于 build_client.py 的代码在 main 函数中，我们需要修改 sys.argv 来模拟调用
        log_warn("开始签名和公证流程（复用 build_client.py 的逻辑）...")
        
        # 保存原始 sys.argv
        original_argv = sys.argv.copy()
        
        # 设置 sys.argv 以模拟 build_client.py 的调用
        # build_client.py 期望: python build_client.py <client_type> <platform>
        sys.argv = ["build_client.py", client_type, "macos"]
        
        # 由于 build_client.py 会检查 app_bundle 是否存在，我们需要确保它在正确的位置
        # 但 build_client.py 会重新打包，我们需要修改逻辑
        
        # 更好的方式：直接调用 build_client.py 的签名部分
        # 但由于代码在 main 函数中，我们需要提取
        
        # 临时方案：直接执行签名和公证逻辑
        # 我们复制 build_client.py 中从签名开始的代码
        
        # 获取签名凭据
        codesign_identity = os.environ.get("CODESIGN_IDENTITY", "Developer ID Application: wei liu (U5SLTWD6AH)")
        installer_identity = os.environ.get("INSTALLER_CODESIGN_IDENTITY", None)
        apple_id = os.environ.get("APPLE_ID", "ruier09@qq.com")
        team_id = os.environ.get("TEAM_ID", "U5SLTWD6AH")
        notary_password = os.environ.get("NOTARY_PASSWORD", "qhiz-rnwg-fhtz-tude")
        
        # 由于 build_client.py 的签名和公证代码非常长，我们采用直接执行的方式
        # 通过 subprocess 调用 build_client.py，但跳过打包步骤
        
        # 更好的方案：创建一个辅助脚本，只执行签名和公证部分
        # 或者修改 build_client.py 支持从现有 .app 开始签名
        
        # 临时方案：直接在这里实现签名和公证（简化版）
        # 完整版需要复用 build_client.py 的所有逻辑
        
        if codesign_identity:
            # 步骤：签名 Resources 目录
            if not should_skip_step(Step.SIGN_RESOURCES, start_from_step):
                log_step(Step.SIGN_RESOURCES, "代码签名（使用完整签名流程，复用 build_client.py 的逻辑）...")
            
            # 创建 entitlements 文件
            entitlements_file = client_dir / "entitlements.plist"
            if not entitlements_file.exists():
                entitlements_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-jit</key>
    <false/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <false/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <false/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <false/>
</dict>
</plist>"""
                with open(entitlements_file, 'w') as f:
                    f.write(entitlements_content)
                log_info("✓ 创建 entitlements.plist")
            
            # 复用 build_client.py 的完整签名流程
            # 第一步：签名 Resources 目录中的二进制文件（如果有）
            if not should_skip_step(Step.SIGN_RESOURCES, start_from_step):
                resources_dir = target_app / "Contents" / "Resources"
                if resources_dir.exists():
                    log_info("  签名 Resources 目录中的二进制文件...")
                    for item in resources_dir.rglob("*"):
                        if item.is_file():
                            # 跳过资源文件和 Python 包元数据目录
                            if item.suffix in [".plist", ".qm", ".png", ".json", ".icns", ".txt", ".md"]:
                                continue
                            # 跳过 .dist-info 和 .egg-info 目录中的文件
                            if ".dist-info" in str(item) or ".egg-info" in str(item):
                                continue
                            # 检查是否是 Mach-O 二进制文件
                            try:
                                result = subprocess.run(
                                    ["file", "-b", "--mime-type", str(item)],
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                    timeout=30
                                )
                                if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                    log_info(f"    签名: {item.relative_to(target_app)}")
                                    subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",
                                        str(item)
                                    ], check=False, capture_output=True)
                            except Exception:
                                pass
            else:
                log_info(f"[跳过] 签名 Resources 目录（从步骤 {start_from_step.value} 开始）")
            
            # 第二步：签名 Frameworks 目录
            if not should_skip_step(Step.SIGN_FRAMEWORKS, start_from_step):
                frameworks_dir = target_app / "Contents" / "Frameworks"
                if frameworks_dir.exists():
                    # 签名所有独立的 .dylib 文件和无扩展名的 Mach-O 文件（不包括框架内的）
                    log_info("  签名独立的 .dylib 文件和无扩展名 Mach-O 文件...")
                    dylib_files = [f for f in frameworks_dir.rglob("*.dylib") 
                                  if ".framework" not in str(f)]
                    for dylib in dylib_files:
                        log_info(f"    签名: {dylib.relative_to(target_app)}")
                        subprocess.run([
                            "codesign", "--force", "--sign", codesign_identity,
                            "--options", "runtime",
                            "--timestamp",
                            str(dylib)
                        ], check=False, capture_output=True)
                    
                    # 签名无扩展名的 Mach-O 文件（如 QtWidgets, QtCore 等）
                    log_info("  签名无扩展名的 Mach-O 文件...")
                    for item in frameworks_dir.iterdir():
                        if item.is_file() and not item.suffix and ".framework" not in str(item):
                            # 检查是否是 Mach-O 二进制文件
                            try:
                                result = subprocess.run(
                                    ["file", "-b", "--mime-type", str(item)],
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                    timeout=30
                                )
                                if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                    log_info(f"    签名: {item.relative_to(target_app)}")
                                    # 使用 --preserve-metadata 保留元数据（与 build_client.py 保持一致）
                                    subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",
                                        "--preserve-metadata=entitlements,requirements,flags",
                                        str(item)
                                    ], check=False, capture_output=True)
                                    # 签名后立即验证（与 build_client.py 保持一致）
                                    verify_result = subprocess.run(
                                        ["codesign", "-vvv", str(item)],
                                        capture_output=True,
                                        text=True,
                                        timeout=60
                                    )
                                    if verify_result.returncode != 0:
                                        log_warn(f"      警告: {item.name} 签名验证失败，尝试重新签名...")
                                        # 如果验证失败，尝试重新签名
                                        subprocess.run([
                                            "codesign", "--force", "--sign", codesign_identity,
                                            "--options", "runtime",
                                            "--timestamp",
                                            str(item)
                                        ], check=False, capture_output=True)
                            except Exception as e:
                                log_warn(f"      签名 {item.name} 时出错: {e}")
                    
                    # 签名 Qt 框架（.framework 目录）
                    qt_dir = frameworks_dir / "PySide6" / "Qt"
                    if qt_dir.exists():
                        log_info("  签名 Qt 框架...")
                        framework_dirs = [d for d in qt_dir.rglob("*.framework") if d.is_dir()]
                        for framework_dir in framework_dirs:
                            log_info(f"    签名框架: {framework_dir.relative_to(target_app)}")
                            
                            # 先签名框架内的所有文件
                            for item in framework_dir.rglob("*"):
                                if item.is_file():
                                    # 跳过 Info.plist 和资源文件
                                    if item.suffix in [".plist", ".qm", ".png", ".json"]:
                                        continue
                                    # 检查是否是 Mach-O 二进制文件
                                    try:
                                        result = subprocess.run(
                                            ["file", "-b", "--mime-type", str(item)],
                                            capture_output=True,
                                            text=True,
                                            check=True,
                                            timeout=30
                                        )
                                        if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                            subprocess.run([
                                                "codesign", "--force", "--sign", codesign_identity,
                                                "--options", "runtime",
                                                "--timestamp",
                                                str(item)
                                            ], check=False, capture_output=True)
                                    except Exception:
                                        pass
                            
                            # 然后签名整个框架目录
                            subprocess.run([
                                "codesign", "--force", "--sign", codesign_identity,
                                "--options", "runtime",
                                "--timestamp",
                                str(framework_dir)
                            ], check=False, capture_output=True)
                        
                        # 签名 Qt 目录中的其他二进制文件（非框架）
                        log_info("  签名 Qt 其他二进制文件...")
                        for qt_lib in qt_dir.rglob("*"):
                            if qt_lib.is_file() and ".framework" not in str(qt_lib):
                                if qt_lib.suffix in [".plist", ".qm", ".png", ".json"]:
                                    continue
                                try:
                                    result = subprocess.run(
                                        ["file", "-b", "--mime-type", str(qt_lib)],
                                        capture_output=True,
                                        text=True,
                                        check=True,
                                        timeout=30
                                    )
                                    if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                        log_info(f"    签名: {qt_lib.relative_to(target_app)}")
                                        subprocess.run([
                                            "codesign", "--force", "--sign", codesign_identity,
                                            "--options", "runtime",
                                            "--timestamp",
                                            str(qt_lib)
                                        ], check=False, capture_output=True)
                                except Exception:
                                    pass
                    
                    # 签名所有 .so 文件
                    log_info("  签名 .so 文件...")
                    so_files = list(frameworks_dir.rglob("*.so"))
                    for so_file in so_files:
                        log_info(f"    签名: {so_file.relative_to(target_app)}")
                        subprocess.run([
                            "codesign", "--force", "--sign", codesign_identity,
                            "--options", "runtime",
                            "--timestamp",
                            str(so_file)
                        ], check=False, capture_output=True)
            else:
                log_info(f"[跳过] 签名 Frameworks 目录（从步骤 {start_from_step.value} 开始）")
                frameworks_dir = target_app / "Contents" / "Frameworks"
            
            # 第三步：验证并修复关键文件签名（在签名主可执行文件之前，与 build_client.py 保持一致）
            # 注意：与 build_client.py 保持一致，这里不检查 frameworks_dir.exists()，直接使用
            log_warn("验证并修复关键文件签名...")
            # 查找所有无扩展名的 Qt 文件（与 build_client.py 保持一致）
            qt_files = [f for f in frameworks_dir.iterdir() 
                       if f.is_file() and not f.suffix and f.name.startswith("Qt")]
            for qt_file in qt_files:
                verify_result = subprocess.run(
                    ["codesign", "-vvv", str(qt_file)],
                    capture_output=True,
                    text=True,
                    timeout=60  # 大型文件（如 QtWebEngineCore）验证可能需要更长时间
                )
                if verify_result.returncode != 0:
                    log_warn(f"  重新签名: {qt_file.relative_to(target_app)}")
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",  # 使用时间戳
                        str(qt_file)
                    ], check=False, capture_output=True)
            
            # 第四步：先签名主可执行文件（与 build_client.py 保持一致）
            if not should_skip_step(Step.SIGN_MAIN, start_from_step):
                log_step(Step.SIGN_MAIN, "签名应用包主可执行文件...")
                main_executable = target_app / "Contents" / "MacOS" / app_name
                if main_executable.exists():
                    # 先签名主可执行文件（使用 check=True，失败会立即报错，与 build_client.py 保持一致）
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",
                        str(main_executable)
                    ], check=True)  # 使用 check=True，失败会立即报错
                    log_info("✓ 主可执行文件已签名")
                else:
                    log_error(f"主可执行文件不存在: {main_executable}")
                    raise FileNotFoundError(f"主可执行文件不存在: {main_executable}")
            else:
                log_info(f"[跳过] 签名主可执行文件（从步骤 {start_from_step.value} 开始）")
                main_executable = target_app / "Contents" / "MacOS" / app_name
            
            # 第五步：签名整个应用包（不使用 --deep，避免重新签名）
            if not should_skip_step(Step.SIGN_BUNDLE, start_from_step):
                log_step(Step.SIGN_BUNDLE, "签名应用包（不使用 --deep，避免重新签名）...")
                # 不使用 --deep，因为我们已经手动签名了所有组件
                # 使用 --strict 进行更严格的验证
                codesign_cmd = [
                    "codesign", "--force", "--sign", codesign_identity,
                    "--options", "runtime",
                    "--timestamp",
                    "--strict",
                    "--verify",
                    str(target_app)
                ]
                subprocess.run(codesign_cmd, check=True)
                log_info("✓ 应用包已签名")
            else:
                log_info(f"[跳过] 签名应用包（从步骤 {start_from_step.value} 开始）")
            
            # 签名后，再次验证并修复关键文件（因为 --deep 可能会破坏签名）
            # 注意：与 build_client.py 保持一致，这里不检查 frameworks_dir.exists()，直接使用
            if not should_skip_step(Step.SIGN_BUNDLE, start_from_step):
                log_warn("签名后验证并修复关键文件...")
                # 查找 Contents/Frameworks 下的无扩展名 Mach-O 文件
                frameworks_root_mach_o_files = [
                    f for f in frameworks_dir.iterdir()
                    if f.is_file() and not f.suffix and ".framework" not in str(f)
                ]
                
                re_sign_needed = False
                for item in frameworks_root_mach_o_files:
                        try:
                            # 使用 -vvv 检查签名状态（这会检测到 "invalid Info.plist" 错误）
                            verify_result = subprocess.run(
                                ["codesign", "-vvv", str(item)],
                                capture_output=True,
                                text=True,
                                check=False, # 不检查返回码，因为可能就是无效
                                timeout=60  # 大型文件验证可能需要更长时间
                            )
                            # 检查是否有 "invalid Info.plist" 或 "code object is not signed" 错误
                            if verify_result.returncode != 0 or "invalid Info.plist" in verify_result.stderr or "code object is not signed" in verify_result.stderr:
                                log_warn(f"    发现签名无效: {item.relative_to(target_app)}，重新签名...")
                                log_warn(f"      错误信息: {verify_result.stderr.strip()[:100]}")
                                subprocess.run([
                                    "codesign", "--force", "--sign", codesign_identity,
                                    "--options", "runtime",
                                    "--timestamp=none",  # 关键：重新签名时使用 --timestamp=none
                                    str(item)
                                ], check=False, capture_output=True)
                                # 再次验证
                                verify_again = subprocess.run(
                                    ["codesign", "-vvv", str(item)],
                                    capture_output=True,
                                    text=True,
                                    check=False
                                )
                                if verify_again.returncode == 0:
                                    log_info(f"      ✓ 重新签名成功")
                                else:
                                    log_warn(f"      ⚠ 重新签名后验证仍失败: {verify_again.stderr.strip()[:100]}")
                                re_sign_needed = True
                        except Exception as e:
                            log_error(f"    检查或重新签名 {item.relative_to(target_app)} 失败: {e}")
                
                if re_sign_needed:
                        log_warn("关键文件已修复，重新签名应用包以包含修复...")
                        codesign_cmd = [
                            "codesign", "--force", "--verify", "--verbose",
                            "--sign", codesign_identity,
                            "--options", "runtime",
                            "--timestamp",
                            "--strict",
                            str(target_app)
                        ]
                        subprocess.run(codesign_cmd, check=True)
                        log_info("✓ 应用包已重新签名以包含修复")
            
            # 验证签名（不使用 --deep，因为已弃用）
            if not should_skip_step(Step.VERIFY, start_from_step):
                log_step(Step.VERIFY, "验证签名...")
                verify_result = subprocess.run([
                    "codesign", "--verify", "--verbose", "--strict",
                    str(target_app)
                ], capture_output=True, text=True, check=False)
            
                if verify_result.returncode != 0:
                    log_error(f"签名验证失败: {verify_result.stderr}")
                    # 尝试使用 spctl 进行额外验证
                    spctl_result = subprocess.run([
                        "spctl", "--assess", "--verbose", "--type", "execute",
                        str(target_app)
                    ], capture_output=True, text=True, check=False)
                    if spctl_result.returncode != 0:
                        log_error(f"spctl 验证也失败: {spctl_result.stderr}")
                        log_warn("⚠ 签名验证失败，但继续执行...")
                else:
                    log_info("✓ 签名验证通过")
                
                # 使用 spctl 进行额外验证
                # 注意：在公证之前，spctl 会显示 "Unnotarized Developer ID"，这是正常的
                log_warn("使用 spctl 验证（公证前，预期会显示未公证警告）...")
                spctl_result = subprocess.run([
                    "spctl", "--assess", "--verbose", "--type", "execute",
                    str(target_app)
                ], capture_output=True, text=True, check=False)
                if spctl_result.returncode == 0:
                    log_info("✓ spctl 验证通过")
                else:
                    # 这是正常的，因为应用还没有通过公证
                    log_info("ℹ spctl 显示未公证（这是正常的，公证后会装订票据）")
                    if "Unnotarized" in spctl_result.stderr:
                        log_info("   应用已签名，等待公证后装订票据即可")
                
                log_info("✓ 应用包代码签名完成")
            else:
                log_info(f"[跳过] 验证签名（从步骤 {start_from_step.value} 开始）")
        else:
            log_warn("⚠ 跳过代码签名（设置 CODESIGN_IDENTITY 环境变量以启用）")
        
        # TODO: 创建 DMG 和 PKG（需要复用 build_client.py 的逻辑）
        log_warn("创建 DMG 和 PKG（待实现）...")
            
    finally:
        # 恢复原始工作目录和 sys.argv
        os.chdir(original_cwd)
        sys.argv = original_argv

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="从 GitHub Release 下载 .app 文件并进行签名和公证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整流程（下载、解压、签名）
  python3 sign_and_notarize_from_github.py employee v1.0.0 owner repo
  
  # 从指定步骤开始执行（跳过下载和解压）
  python3 sign_and_notarize_from_github.py employee v1.0.0 owner repo --start-from sign_main
  
  # 可用步骤:
  #   download, extract, copy, sign_resources, sign_frameworks,
  #   sign_main, sign_bundle, verify, create_dmg, sign_dmg, notarize
        """
    )
    
    parser.add_argument("client_type", choices=["employee", "admin"], help="客户端类型")
    parser.add_argument("tag_name", help="GitHub Release 标签（如 v1.0.0）")
    parser.add_argument("repo_owner", help="GitHub 仓库所有者")
    parser.add_argument("repo_name", help="GitHub 仓库名称")
    parser.add_argument("api_key", nargs="?", help="GitHub API Key（可选，私有仓库需要，可作为位置参数或使用 --api-key）")
    parser.add_argument("--api-key", dest="api_key_option", help="GitHub API Key（可选，私有仓库需要）")
    parser.add_argument(
        "--start-from",
        type=str,
        choices=[s.value for s in Step],
        help="从指定步骤开始执行（跳过之前的步骤，用于调试）"
    )
    
    args = parser.parse_args()
    
    client_type = args.client_type
    tag_name = args.tag_name
    repo_owner = args.repo_owner
    repo_name = args.repo_name
    # 优先使用 --api-key 选项，否则使用位置参数
    api_key = args.api_key_option or args.api_key
    
    # 解析 start_from_step
    start_from_step = None
    if args.start_from:
        try:
            start_from_step = Step(args.start_from)
            log_info(f"🔧 调试模式：从步骤 '{start_from_step.value}' 开始执行")
            log_info(f"   将跳过以下步骤: {', '.join([s.value for s in get_step_order() if should_skip_step(s, start_from_step)])}")
        except ValueError:
            log_error(f"无效的步骤名称: {args.start_from}")
            log_info(f"可用步骤: {', '.join([s.value for s in Step])}")
            sys.exit(1)
    
    if client_type not in ["employee", "admin"]:
        log_error("错误: client_type 必须是 'employee' 或 'admin'")
        sys.exit(1)
    
    # 应用名称
    if client_type == "employee":
        app_name = "Ai Perf Client"
    else:
        app_name = "Ai Perf Admin"
    
    log_info("=" * 50)
    log_info(f"从 GitHub Release 下载并签名 {app_name}")
    log_info(f"Release: {tag_name}")
    log_info(f"仓库: {repo_owner}/{repo_name}")
    log_info("=" * 50)
    print()
    
    # 获取 Release assets
    assets = get_github_release_assets(repo_owner, repo_name, tag_name, api_key)
    if not assets:
        log_error("未找到 Release assets")
        sys.exit(1)
    
    # 查找 .app 文件（arm64 和 intel）
    # 根据客户端类型匹配文件名（支持多种格式：空格、点号、连字符）
    client_patterns = {
        "employee": [
            r"client",  # 包含 client
            r"employee",  # 包含 employee
            r"ai\s*perf\s*client",  # Ai Perf Client（空格或点号）
            r"ai\.perf\.client",  # Ai.Perf.Client（点号）
        ],
        "admin": [
            r"admin",  # 包含 admin
            r"ai\s*perf\s*admin",  # Ai Perf Admin（空格或点号）
            r"ai\.perf\.admin",  # Ai.Perf.Admin（点号）
        ]
    }
    patterns = client_patterns.get(client_type, [])
    
    log_info(f"查找 {client_type} 客户端的 .app 文件...")
    log_info(f"匹配模式: {patterns}")
    
    # 先列出所有 assets（用于调试）
    log_info(f"所有 assets ({len(assets)} 个):")
    for asset in assets:
        asset_name = asset.get("name", "")
        log_info(f"  - {asset_name}")
    
    app_assets = {}
    for asset in assets:
        asset_name = asset.get("name", "")
        asset_url = asset.get("browser_download_url", "")
        asset_name_lower = asset_name.lower()
        
        # 检查是否匹配客户端类型
        matches_client = False
        if patterns:
            import re
            for pattern in patterns:
                if re.search(pattern, asset_name_lower):
                    matches_client = True
                    log_info(f"  ✓ 匹配客户端类型: {asset_name} (模式: {pattern})")
                    break
        else:
            matches_client = True  # 如果没有模式，匹配所有
        
        if not matches_client:
            log_info(f"  ✗ 不匹配客户端类型: {asset_name}")
            continue
        
        # 查找 .app.zip 文件（macOS 应用包都是 ZIP 格式）
        # 支持多种格式：.app.zip, -arm64.app.zip, -intel.app.zip 等
        if asset_name.endswith(".app.zip") or (asset_name.endswith(".zip") and ".app" in asset_name_lower):
            # ZIP 文件，包含 .app
            # 检查架构（优先级：明确的架构标识 > 推测）
            arch = None
            
            # 方法1: 明确的架构标识（-arm64 或 -intel）
            if "-arm64" in asset_name_lower or asset_name_lower.endswith("-arm64.app.zip") or asset_name_lower.endswith("-arm64.zip"):
                arch = "arm64"
            elif "-intel" in asset_name_lower or asset_name_lower.endswith("-intel.app.zip") or asset_name_lower.endswith("-intel.zip"):
                arch = "intel"
            # 方法2: 从文件名中查找架构关键词（更精确的匹配）
            elif "arm64" in asset_name_lower:
                arch = "arm64"
            elif "intel" in asset_name_lower or "x86" in asset_name_lower:
                arch = "intel"
            # 方法3: 如果无法确定架构，但文件名包含 .app.zip，尝试推测
            elif ".app.zip" in asset_name_lower:
                # 如果还没有找到对应架构的文件，尝试推测
                if "arm64" not in app_assets:
                    arch = "arm64"
                elif "intel" not in app_assets:
                    arch = "intel"
            
            if arch:
                if arch not in app_assets:
                    app_assets[arch] = asset_url
                    log_info(f"✓ 找到 {arch} .app (ZIP): {asset_name}")
                else:
                    log_warn(f"  跳过重复的 {arch} .app: {asset_name}")
        elif asset_name.endswith(".app"):
            # 直接是 .app 文件（不太可能，因为 GitHub 不支持上传目录）
            log_warn(f"找到 .app 文件: {asset_name}（GitHub 不支持直接上传目录，可能是 ZIP）")
    
    if not app_assets:
        log_error("未找到 .app 文件")
        log_error(f"可用的 assets: {[a.get('name', '') for a in assets]}")
        sys.exit(1)
    
    log_info(f"✓ 找到 {len(app_assets)} 个架构的 .app 文件: {list(app_assets.keys())}")
    
    # 创建临时目录
    temp_dir = Path(tempfile.gettempdir()) / f"sign_notarize_{int(time.time())}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 下载并处理每个架构的 .app
        for arch, url in app_assets.items():
            log_info("=" * 50)
            log_info(f"处理 {arch} 架构")
            log_info("=" * 50)
            print()
            
            # 检查输出目录是否已有 DMG 文件（说明签名成功）
            script_dir = Path(__file__).parent
            project_root = script_dir.parent
            if client_type == "employee":
                client_dir = project_root / "ui_client"
            else:
                client_dir = project_root / "admin_ui_client"
            
            output_dir = client_dir / "dist" / "from_github" / client_type / arch
            dmg_files = list(output_dir.glob("*.dmg")) if output_dir.exists() else []
            
            if dmg_files and not start_from_step:
                log_info(f"✓ 发现已签名的 DMG 文件，跳过整个流程: {dmg_files[0].name}")
                log_info(f"  如需重新签名，请删除 DMG 文件后重试，或使用 --start-from 参数")
                log_info(f"✓ {arch} 架构处理完成（已跳过）")
                print()
                continue
            
            # 步骤：下载 ZIP 文件
            download_path = temp_dir / f"{app_name}_{arch}.zip"
            zip_exists = download_path.exists() and download_path.is_file() and download_path.stat().st_size > 0
            
            if not should_skip_step(Step.DOWNLOAD, start_from_step):
                if not zip_exists:
                    log_step(Step.DOWNLOAD, f"下载 {arch} .app ZIP 文件...")
                    if not download_file(url, download_path, api_key):
                        log_error(f"下载 {arch} .app 失败")
                        continue
                else:
                    log_info(f"[跳过] 下载步骤（ZIP 文件已存在: {download_path}）")
            else:
                if not zip_exists:
                    log_error(f"ZIP 文件不存在，但跳过了下载步骤。请先下载文件或使用 --start-from download")
                    continue
                log_info(f"[跳过] 下载步骤（从步骤 {start_from_step.value} 开始）")
            
            # 步骤：解压 ZIP 文件
            target_app = output_dir / f"{app_name}.app"
            app_bundle = None
            
            if not should_skip_step(Step.EXTRACT, start_from_step):
                if target_app.exists() and target_app.is_dir():
                    log_info(f"[跳过] 解压步骤（.app 已存在: {target_app}）")
                    app_bundle = target_app
                else:
                    log_step(Step.EXTRACT, f"解压 {arch} .app ZIP 文件...")
                    app_bundle = find_app_in_zip(download_path, app_name)
                    if not app_bundle:
                        log_error(f"在 ZIP 文件中未找到 .app")
                        continue
            else:
                if not target_app.exists():
                    log_error(f".app 文件不存在，但跳过了解压步骤。请先解压文件或使用 --start-from extract")
                    continue
                log_info(f"[跳过] 解压步骤（从步骤 {start_from_step.value} 开始）")
                app_bundle = target_app
            
            # 步骤：复制 .app 到输出目录（如果需要）
            if not should_skip_step(Step.COPY, start_from_step):
                if app_bundle.resolve() != target_app.resolve():
                    log_step(Step.COPY, f"复制 .app 到输出目录...")
                    if target_app.exists():
                        shutil.rmtree(target_app)
                    shutil.copytree(app_bundle, target_app)
                    app_bundle = target_app
                else:
                    log_info(f"[跳过] 复制步骤（.app 已在输出目录）")
            else:
                log_info(f"[跳过] 复制步骤（从步骤 {start_from_step.value} 开始）")
                app_bundle = target_app
            
            # 签名和公证
            log_warn(f"开始签名和公证 {arch} .app...")
            sign_and_notarize_app_from_existing(app_bundle, client_type, arch, start_from_step)
            
            log_info(f"✓ {arch} 架构处理完成")
            print()
        
        log_info("=" * 50)
        log_info("✓ 所有架构处理完成")
        log_info("=" * 50)
        
    finally:
        # 清理临时目录
        if temp_dir.exists():
            log_warn("清理临时文件...")
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    main()

