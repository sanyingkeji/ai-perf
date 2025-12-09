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
import stat

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

def run_with_timeout_and_kill(cmd, timeout, check=False, capture_output=True, text=True, log_prefix=""):
    """
    运行命令并确保在超时后强制终止进程
    
    Args:
        cmd: 命令列表
        timeout: 超时时间（秒）
        check: 如果为 True，返回码非零时抛出异常
        capture_output: 是否捕获输出
        text: 是否以文本模式处理输出
        log_prefix: 日志前缀（用于显示进度）
    
    Returns:
        subprocess.CompletedProcess 对象
    
    Raises:
        subprocess.TimeoutExpired: 如果超时
        subprocess.CalledProcessError: 如果 check=True 且返回码非零
    """
    import platform
    
    # 使用 Popen 以便能够强制终止
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text
    )
    
    start_time = time.time()
    last_progress_time = start_time
    progress_interval = 30  # 每30秒输出一次进度
    
    try:
        # 轮询进程状态，同时输出进度
        while True:
            elapsed = time.time() - start_time
            remaining = timeout - elapsed
            
            # 检查是否超时
            if elapsed >= timeout:
                log_warn(f"{log_prefix}命令执行超时（{timeout} 秒），正在强制终止...")
                # 尝试优雅终止
                if platform.system() != "Windows":
                    process.terminate()
                else:
                    process.terminate()
                
                # 等待最多5秒
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # 如果5秒后还没结束，强制杀死
                    log_warn(f"{log_prefix}进程未响应，强制杀死...")
                    process.kill()
                    process.wait()
                
                raise subprocess.TimeoutExpired(cmd, timeout)
            
            # 定期输出进度（避免日志过多）
            if elapsed - last_progress_time >= progress_interval:
                log_info(f"{log_prefix}仍在运行...（已用时 {int(elapsed)} 秒，剩余 {int(remaining)} 秒）")
                last_progress_time = elapsed
            
            # 检查进程是否已结束
            returncode = process.poll()
            if returncode is not None:
                # 进程已结束
                stdout, stderr = process.communicate()
                result = subprocess.CompletedProcess(
                    cmd, returncode, stdout, stderr
                )
                
                if check and returncode != 0:
                    raise subprocess.CalledProcessError(returncode, cmd, stdout, stderr)
                
                return result
            
            # 短暂休眠，避免 CPU 占用过高
            time.sleep(1)
            
    except KeyboardInterrupt:
        # 用户取消，强制终止进程
        log_warn(f"{log_prefix}收到取消信号，正在终止进程...")
        if platform.system() != "Windows":
            process.terminate()
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise

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
    NOTARIZE_DMG = "notarize_dmg"  # 公证 DMG
    CREATE_PKG = "create_pkg"  # 创建 PKG
    SIGN_PKG = "sign_pkg"  # 签名 PKG
    NOTARIZE_PKG = "notarize_pkg"  # 公证 PKG

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
        Step.NOTARIZE_DMG,
        Step.CREATE_PKG,
        Step.SIGN_PKG,
        Step.NOTARIZE_PKG,
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
def download_file(url: str, dest_path: Path, api_key: str = None, max_retries: int = 3, retry_delay: int = 5) -> bool:
    """
    下载文件（如果文件已存在则跳过下载）
    
    Args:
        url: 下载 URL
        dest_path: 目标文件路径
        api_key: GitHub API 密钥（可选）
        max_retries: 最大重试次数（默认 3 次）
        retry_delay: 重试间隔（秒，默认 5 秒）
    
    Returns:
        下载成功返回 True，否则返回 False
    """
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
    
    # 重试循环
    for attempt in range(1, max_retries + 1):
        try:
            # 如果不是第一次尝试，删除可能存在的部分下载文件
            if attempt > 1 and dest_path.exists():
                try:
                    dest_path.unlink()
                    log_info(f"清理部分下载的文件: {dest_path}")
                except Exception as e:
                    log_warn(f"清理部分下载文件失败: {e}")
            
            headers = {}
            if api_key:
                headers["Authorization"] = f"token {api_key}"
            
            if attempt == 1:
                log_info(f"下载文件: {url}")
                log_info(f"保存到: {dest_path}")
            else:
                log_warn(f"第 {attempt}/{max_retries} 次尝试下载...")
            
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 增加超时时间：基础超时 300 秒，每次重试增加 60 秒
            timeout = 300.0 + (attempt - 1) * 60.0
            
            with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as response:
                if response.status_code != 200:
                    log_error(f"下载失败: HTTP {response.status_code}")
                    if attempt < max_retries:
                        log_warn(f"将在 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    return False
                
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                last_percent = -1
                
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 检测是否在终端环境中（支持 ANSI 转义序列）
                is_tty = sys.stderr.isatty() if hasattr(sys.stderr, 'isatty') else False
                
                # 使用 stderr 输出进度，避免与日志输出冲突
                # 先打印一个空行到 stderr，确保进度显示在独立行
                if is_tty:
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
                                progress_text = f"  进度: {percent:.1f}% ({downloaded_mb:.2f}/{total_mb:.2f} MB)"
                                if is_tty:
                                    # 在终端环境中，使用 \r 在同一行更新，\033[K 清除到行尾
                                    sys.stderr.write(f"\r{progress_text}\033[K")
                                else:
                                    # 在非终端环境中，直接换行输出（避免显示转义序列）
                                    sys.stderr.write(f"{progress_text}\n")
                                sys.stderr.flush()
                                last_percent = int(percent)
                
                # 下载完成后，清除进度行并打印完成信息
                if is_tty:
                    sys.stderr.write("\r" + " " * 80 + "\r\n")  # 清除进度行并换行
                    sys.stderr.flush()
                
                # 验证下载的文件大小
                if total_size > 0:
                    actual_size = dest_path.stat().st_size
                    if actual_size != total_size:
                        log_warn(f"下载文件大小不匹配: 期望 {total_size} 字节，实际 {actual_size} 字节")
                        if attempt < max_retries:
                            log_warn(f"将在 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            continue
                        else:
                            log_error("文件大小不匹配，且已达到最大重试次数")
                            return False
                
                log_info(f"✓ 下载完成: {dest_path}")
                if attempt > 1:
                    log_info(f"✓ 经过 {attempt} 次尝试后成功下载")
                return True
                
        except httpx.TimeoutException as e:
            log_error(f"下载超时: {e}")
            if attempt < max_retries:
                log_warn(f"将在 {retry_delay} 秒后重试（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(retry_delay)
                continue
            else:
                log_error(f"下载失败: 已达到最大重试次数 ({max_retries})")
                return False
        except httpx.RequestError as e:
            log_error(f"网络请求错误: {e}")
            if attempt < max_retries:
                log_warn(f"将在 {retry_delay} 秒后重试（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(retry_delay)
                continue
            else:
                log_error(f"下载失败: 已达到最大重试次数 ({max_retries})")
                return False
        except Exception as e:
            log_error(f"下载失败: {e}")
            if attempt < max_retries:
                log_warn(f"将在 {retry_delay} 秒后重试（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(retry_delay)
                continue
            else:
                log_error(f"下载失败: 已达到最大重试次数 ({max_retries})")
                return False
    
    # 所有重试都失败
    log_error(f"下载失败: 经过 {max_retries} 次尝试后仍无法下载")
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
    
    # 如果 app_bundle 就是 target_app，检查是否需要更新
    if app_bundle.resolve() == target_app.resolve():
        log_info(f".app 已在输出目录: {target_app}")
        # 即使路径相同，也确保使用最新的文件（这里假设调用者已经处理了更新逻辑）
    else:
        # 如果 target_app 已存在，删除它以确保使用最新文件
        if target_app.exists():
            log_warn(f"删除旧的 .app: {target_app}")
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
        # 先收集要处理的项，避免在迭代时修改目录
        items_to_check = list(frameworks_dir.iterdir())
        
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
        
        # 处理应该移动到 Resources 的文件：无论是否需要清理 resources，都要处理这些文件
        # base_library.zip、config.json 和 google_client_secret.json 应该在 Resources 目录，与本地打包一致
        resources_dir = target_app / "Contents" / "Resources"
        files_to_move_to_resources = []  # 收集需要移动的文件
        
        for item in items_to_check:
            if item.is_file() and not item.is_symlink():
                # 需要移动到 Resources 的文件类型
                if item.suffix in [".zip", ".json"]:
                    files_to_move_to_resources.append(item)
        
        # 批量处理需要移动的文件
        for item in files_to_move_to_resources:
            if not item.exists():
                continue  # 文件可能已被删除
            if resources_dir.exists():
                target_file = resources_dir / item.name
                if not target_file.exists():
                    log_warn(f"  移动 {item.suffix.upper()[1:]} 文件到 Resources 目录: {item.relative_to(target_app)} -> Resources/{item.name}")
                    try:
                        shutil.move(str(item), str(target_file))
                        log_info(f"    ✓ 已移动: {item.name}")
                    except Exception as e:
                        log_warn(f"    移动失败: {e}")
                        # 如果移动失败，尝试删除
                        try:
                            item.unlink()
                            log_info(f"    ✓ 已删除（移动失败后的回退）: {item.name}")
                        except Exception as e2:
                            log_warn(f"    删除也失败: {e2}")
                else:
                    log_warn(f"  删除 Frameworks 下的 {item.suffix.upper()[1:]} 文件（Resources 目录已存在）: {item.relative_to(target_app)}")
                    try:
                        item.unlink()
                        log_info(f"    ✓ 已删除: {item.name}")
                    except Exception as e:
                        log_warn(f"    删除失败: {e}")
                        # 尝试强制删除
                        try:
                            import stat
                            os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
                            item.unlink()
                            log_info(f"    ✓ 已强制删除: {item.name}")
                        except Exception as e2:
                            log_error(f"    强制删除也失败: {e2}")
            else:
                # Resources 目录不存在，直接删除 Frameworks 下的文件
                log_warn(f"  Resources 目录不存在，删除 Frameworks 下的 {item.suffix.upper()[1:]} 文件: {item.relative_to(target_app)}")
                try:
                    item.unlink()
                    log_info(f"    ✓ 已删除: {item.name}")
                except Exception as e:
                    log_warn(f"    删除失败: {e}")
        
        # 递归检查并移除 Frameworks 目录下（包括子目录）的所有非二进制文件
        # 这些文件会导致 codesign 签名失败
        # 注意：PySide6/Qt/translations/ 下的 .qm 文件也需要清理
        log_warn("  递归清理 Frameworks 目录下的非二进制文件...")
        other_non_binary_extensions = [".png", ".txt", ".md", ".yml", ".yaml", ".xml", ".plist", ".icns", ".qm", ".html", ".css", ".js", ".pyc", ".pyo"]
        
        # 递归遍历 Frameworks 目录下的所有文件
        for root, dirs, files in os.walk(frameworks_dir):
            root_path = Path(root)
            # 跳过 .framework 目录（这些目录内的文件由框架签名处理）
            # 跳过 resources 目录（无论是符号链接还是真实目录，都应该单独处理，避免误删 Resources/resources 下的文件）
            dirs[:] = [d for d in dirs if not d.endswith('.framework') and d != 'resources']
            
            for file_name in files:
                file_path = root_path / file_name
                # 跳过符号链接
                if file_path.is_symlink():
                    continue
                # 跳过已处理的文件（第一层的 ZIP 和 JSON 文件）
                if file_path in files_to_move_to_resources:
                    continue
                # 跳过二进制文件
                if file_path.suffix in [".dylib", ".so"]:
                    continue
                # 跳过无扩展名的文件（可能是 Mach-O 二进制文件）
                if not file_path.suffix:
                    continue
                # 移除其他非二进制文件
                if file_path.suffix in other_non_binary_extensions:
                    log_warn(f"  移除非二进制文件: {file_path.relative_to(target_app)}")
                    try:
                        file_path.unlink()
                        log_info(f"    ✓ 已移除: {file_path.relative_to(frameworks_dir)}")
                    except Exception as e:
                        log_warn(f"    移除失败: {e}")
        
        # 处理 Frameworks 下的 resources 目录（只在需要时清理）
        if needs_cleanup:
            log_warn("  清理 Frameworks 目录结构...")
            # 重新收集要处理的项（文件已处理，这里主要处理目录）
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
            
            # 移除其他不应该在 Frameworks 的目录（但保留必需的 Python 包目录）
            # 本地打包中，大部分 Python 包目录应该在 Frameworks 中（真实目录）
            # 只有 themes 和 .dist-info 应该在 Resources 中，Frameworks 中有符号链接
            essential_dirs = {
                "PySide6", "shiboken6",  # Qt 相关
                "AppKit", "CoreFoundation", "Foundation", "objc",  # PyObjC 相关
                "PIL", "bcrypt", "certifi", "charset_normalizer", "cryptography", 
                "nacl", "uvloop", "yaml",  # Python 包
                "python3.10", "python3__dot__10",  # Python 标准库
            }
            
            for item in items_to_check:
                # 跳过已处理的 resources
                if item.name == "resources":
                    continue
                # 跳过文件（已在上面处理）
                if item.is_file():
                    continue
                    
                if item.is_dir():
                    # 跳过 .framework 目录
                    if item.suffix == ".framework":
                        continue
                    # 跳过必需的 Python 包目录
                    if item.name in essential_dirs:
                        continue
                    # 跳过符号链接（如 resources 符号链接、themes 符号链接、.dist-info 符号链接）
                    if item.is_symlink():
                        continue
                    # 移除其他目录（如不应该在 Frameworks 的 .dist-info 真实目录等）
                    # 注意：.dist-info 应该在 Resources 中，Frameworks 中应该是符号链接
                    if ".dist-info" in item.name or ".egg-info" in item.name:
                        log_warn(f"  移除 .dist-info/.egg-info 目录（应该在 Resources 中）: {item.relative_to(target_app)}")
                        try:
                            shutil.rmtree(item)
                            log_info(f"    ✓ 已移除: {item.name}")
                        except Exception as e:
                            log_warn(f"    移除失败: {e}")
                    else:
                        # 其他未知目录，暂时保留（可能是新的 Python 包）
                        log_warn(f"  保留未知目录（可能是必需的 Python 包）: {item.relative_to(target_app)}")
        
        # 修复 Frameworks 目录结构：将真实文件转换为符号链接（与本地打包一致）
        log_warn("  修复 Frameworks 目录结构（转换为符号链接）...")
        qt_lib_dir = frameworks_dir / "PySide6" / "Qt" / "lib"
        if qt_lib_dir.exists():
            # 重新收集 Frameworks 目录下的项（在清理之后）
            items_to_fix = list(frameworks_dir.iterdir())
            
            # 已知的 Qt 库文件名列表（用于快速匹配）
            known_qt_libs = {
                "QtCore", "QtDBus", "QtGui", "QtNetwork", "QtOpenGL", "QtPdf",
                "QtQml", "QtQmlMeta", "QtQmlModels", "QtQmlWorkerScript",
                "QtQuick", "QtSvg", "QtVirtualKeyboard", "QtVirtualKeyboardQml", "QtWidgets"
            }
            
            for item in items_to_fix:
                # 跳过目录和符号链接
                if item.is_dir() or item.is_symlink():
                    continue
                
                # 跳过 Python.framework 和 PySide6 目录
                if item.name == "Python.framework" or item.name == "PySide6":
                    continue
                
                # 快速检查：如果是已知的 Qt 库文件名，直接转换
                if item.name in known_qt_libs:
                    framework_name = item.name
                    framework_path = qt_lib_dir / f"{framework_name}.framework"
                    
                    if framework_path.exists():
                        target_path = framework_path / "Versions" / "A" / framework_name
                        if target_path.exists():
                            log_warn(f"  将 {item.name} 转换为符号链接: {item.relative_to(target_app)}")
                            try:
                                # 删除真实文件
                                item.unlink()
                                # 创建符号链接
                                item.symlink_to(f"PySide6/Qt/lib/{framework_name}.framework/Versions/A/{framework_name}")
                                log_info(f"    ✓ 已转换: {item.name} -> PySide6/Qt/lib/{framework_name}.framework/Versions/A/{framework_name}")
                                continue  # 已处理，跳过后续检查
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                
                # 对于其他文件，检查是否是 Qt 库文件（无扩展名或特定扩展名）
                if not item.suffix or item.suffix in [".dylib", ".so"]:
                    # 检查是否是 Mach-O 二进制文件
                    try:
                        result = subprocess.run(
                            ["file", "-b", str(item)],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=10
                        )
                        if "Mach-O" in result.stdout:
                            # 这是一个 Qt 库文件，需要转换为符号链接
                            # 查找对应的 framework
                            framework_name = item.name
                            framework_path = qt_lib_dir / f"{framework_name}.framework"
                            
                            if framework_path.exists():
                                # 找到对应的 framework，创建符号链接
                                target_path = framework_path / "Versions" / "A" / framework_name
                                if target_path.exists():
                                    log_warn(f"  将 {item.name} 转换为符号链接: {item.relative_to(target_app)}")
                                    try:
                                        # 删除真实文件
                                        item.unlink()
                                        # 创建符号链接
                                        item.symlink_to(f"PySide6/Qt/lib/{framework_name}.framework/Versions/A/{framework_name}")
                                        log_info(f"    ✓ 已转换: {item.name} -> PySide6/Qt/lib/{framework_name}.framework/Versions/A/{framework_name}")
                                    except Exception as e:
                                        log_warn(f"    转换失败: {e}")
                    except Exception:
                        pass
            
            # 修复 Resources/PySide6 目录中的文件（转换为符号链接）
            resources_dir = target_app / "Contents" / "Resources"
            if resources_dir.exists():
                resources_pyside6 = resources_dir / "PySide6"
                if resources_pyside6.exists():
                    log_warn("  修复 Resources/PySide6 目录结构（转换为符号链接）...")
                    # 修复 .abi3.so 和 .dylib 文件
                    pyside6_files = list(resources_pyside6.iterdir())
                    for item in pyside6_files:
                        if item.is_file() and not item.is_symlink():
                            if item.suffix in [".so", ".dylib"] or item.name.endswith(".abi3.so"):
                                # 查找 Frameworks/PySide6 中对应的文件
                                frameworks_pyside6_file = frameworks_dir / "PySide6" / item.name
                                if frameworks_pyside6_file.exists():
                                    log_warn(f"  将 Resources/PySide6/{item.name} 转换为符号链接")
                                    try:
                                        item.unlink()
                                        item.symlink_to(f"../../Frameworks/PySide6/{item.name}")
                                        log_info(f"    ✓ 已转换: {item.name} -> ../../Frameworks/PySide6/{item.name}")
                                    except Exception as e:
                                        log_warn(f"    转换失败: {e}")
                    
                    # 检查 Resources/PySide6/Qt/lib 目录（应该是空的或符号链接）
                    resources_qt_lib = resources_pyside6 / "Qt" / "lib"
                    if resources_qt_lib.exists() and resources_qt_lib.is_dir():
                        # 检查是否与 Frameworks/PySide6/Qt/lib 重复
                        frameworks_qt_lib = frameworks_dir / "PySide6" / "Qt" / "lib"
                        if frameworks_qt_lib.exists():
                            # 如果 Resources 中的 lib 目录很大，可能是重复的，删除它
                            lib_size = sum(f.stat().st_size for f in resources_qt_lib.rglob('*') if f.is_file())
                            if lib_size > 10 * 1024 * 1024:  # 大于 10MB，可能是重复的
                                log_warn(f"  删除重复的 Resources/PySide6/Qt/lib 目录（{lib_size / 1024 / 1024:.1f}MB）")
                                try:
                                    shutil.rmtree(resources_qt_lib)
                                    log_info(f"    ✓ 已删除重复的 Resources/PySide6/Qt/lib 目录")
                                except Exception as e:
                                    log_warn(f"    删除失败: {e}")
                
                # 修复 Frameworks 中的资源文件符号链接
                # 检查 base_library.zip（Python 需要在 Frameworks 目录中找到它）
                zip_file = frameworks_dir / "base_library.zip"
                resources_zip = resources_dir / "base_library.zip"
                
                if resources_zip.exists():
                    # Resources 中有 base_library.zip
                    if zip_file.exists():
                        # Frameworks 中也有，检查是否是符号链接
                        if zip_file.is_symlink():
                            # 已经是符号链接，检查是否指向正确位置
                            target = zip_file.readlink()
                            if str(target) != "../Resources/base_library.zip":
                                log_warn(f"  修复 base_library.zip 符号链接指向: {zip_file.relative_to(target_app)}")
                                try:
                                    zip_file.unlink()
                                    zip_file.symlink_to("../Resources/base_library.zip")
                                    log_info(f"    ✓ 已修复: base_library.zip -> ../Resources/base_library.zip")
                                except Exception as e:
                                    log_warn(f"    修复失败: {e}")
                            else:
                                log_info(f"  base_library.zip 符号链接已正确: {zip_file.relative_to(target_app)}")
                        else:
                            # Frameworks 中是真实文件，需要转换为符号链接
                            log_warn(f"  将 base_library.zip 转换为符号链接: {zip_file.relative_to(target_app)}")
                            try:
                                zip_file.unlink()
                                zip_file.symlink_to("../Resources/base_library.zip")
                                log_info(f"    ✓ 已转换: base_library.zip -> ../Resources/base_library.zip")
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                    else:
                        # Frameworks 中不存在，创建符号链接
                        log_warn(f"  在 Frameworks 中创建 base_library.zip 符号链接: {zip_file.relative_to(target_app)}")
                        try:
                            zip_file.symlink_to("../Resources/base_library.zip")
                            log_info(f"    ✓ 已创建: base_library.zip -> ../Resources/base_library.zip")
                        except Exception as e:
                            log_warn(f"    创建失败: {e}")
                else:
                    log_warn(f"  ⚠ Resources 中未找到 base_library.zip，无法创建符号链接")
                
                # 检查 config.json
                config_file = frameworks_dir / "config.json"
                resources_config = resources_dir / "config.json"
                
                if resources_config.exists():
                    # Resources 中存在，确保 Frameworks 中有符号链接
                    if config_file.exists():
                        if config_file.is_symlink():
                            # 已经是符号链接，检查指向是否正确
                            target = config_file.readlink()
                            if str(target) != "../Resources/config.json":
                                log_warn(f"  修复 config.json 符号链接指向: {config_file.relative_to(target_app)}")
                                try:
                                    config_file.unlink()
                                    config_file.symlink_to("../Resources/config.json")
                                    log_info(f"    ✓ 已修复: config.json -> ../Resources/config.json")
                                except Exception as e:
                                    log_warn(f"    修复失败: {e}")
                            else:
                                log_info(f"  config.json 符号链接已正确: {config_file.relative_to(target_app)}")
                        else:
                            # Frameworks 中是真实文件，需要转换为符号链接
                            log_warn(f"  将 config.json 转换为符号链接: {config_file.relative_to(target_app)}")
                            try:
                                config_file.unlink()
                                config_file.symlink_to("../Resources/config.json")
                                log_info(f"    ✓ 已转换: config.json -> ../Resources/config.json")
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                    else:
                        # Frameworks 中不存在，创建符号链接
                        log_warn(f"  在 Frameworks 中创建 config.json 符号链接（修复文件查找问题）")
                        try:
                            config_file.symlink_to("../Resources/config.json")
                            log_info(f"    ✓ 已创建: config.json -> ../Resources/config.json")
                        except Exception as e:
                            log_error(f"    创建失败: {e}")
                else:
                    # Resources 中不存在，检查 Frameworks 中是否有
                    if config_file.exists() and not config_file.is_symlink():
                        # Frameworks 中有真实文件，移动到 Resources
                        log_warn(f"  将 config.json 移动到 Resources 目录: {config_file.relative_to(target_app)}")
                        try:
                            shutil.move(str(config_file), str(resources_config))
                            # 然后在 Frameworks 中创建符号链接
                            config_file.symlink_to("../Resources/config.json")
                            log_info(f"    ✓ 已移动并创建符号链接: config.json -> ../Resources/config.json")
                        except Exception as e:
                            log_warn(f"    移动失败: {e}")
                    elif config_file.exists() and config_file.is_symlink():
                        # Frameworks 中有符号链接，但 Resources 中不存在，删除符号链接
                        log_warn(f"  Resources 中不存在 config.json，删除 Frameworks 中的符号链接: {config_file.relative_to(target_app)}")
                        try:
                            config_file.unlink()
                            log_info(f"    ✓ 已删除无效符号链接")
                        except Exception as e:
                            log_warn(f"    删除失败: {e}")
                    else:
                        log_warn(f"  ⚠ config.json 不存在于 Resources 或 Frameworks（可能导致配置加载失败）")
                
                # 检查 google_client_secret.json
                secret_file = frameworks_dir / "google_client_secret.json"
                resources_secret = resources_dir / "google_client_secret.json"
                
                if resources_secret.exists():
                    # Resources 中存在，确保 Frameworks 中有符号链接
                    if secret_file.exists():
                        if secret_file.is_symlink():
                            # 已经是符号链接，检查指向是否正确
                            target = secret_file.readlink()
                            if str(target) != "../Resources/google_client_secret.json":
                                log_warn(f"  修复 google_client_secret.json 符号链接指向: {secret_file.relative_to(target_app)}")
                                try:
                                    secret_file.unlink()
                                    secret_file.symlink_to("../Resources/google_client_secret.json")
                                    log_info(f"    ✓ 已修复: google_client_secret.json -> ../Resources/google_client_secret.json")
                                except Exception as e:
                                    log_warn(f"    修复失败: {e}")
                            else:
                                log_info(f"  google_client_secret.json 符号链接已正确: {secret_file.relative_to(target_app)}")
                        else:
                            # Frameworks 中是真实文件，需要转换为符号链接
                            log_warn(f"  将 google_client_secret.json 转换为符号链接: {secret_file.relative_to(target_app)}")
                            try:
                                secret_file.unlink()
                                secret_file.symlink_to("../Resources/google_client_secret.json")
                                log_info(f"    ✓ 已转换: google_client_secret.json -> ../Resources/google_client_secret.json")
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                    else:
                        # Frameworks 中不存在，创建符号链接
                        log_warn(f"  在 Frameworks 中创建 google_client_secret.json 符号链接（修复文件查找问题）")
                        try:
                            secret_file.symlink_to("../Resources/google_client_secret.json")
                            log_info(f"    ✓ 已创建: google_client_secret.json -> ../Resources/google_client_secret.json")
                        except Exception as e:
                            log_error(f"    创建失败: {e}")
                else:
                    # Resources 中不存在，检查 Frameworks 中是否有
                    if secret_file.exists() and not secret_file.is_symlink():
                        # Frameworks 中有真实文件，移动到 Resources
                        log_warn(f"  将 google_client_secret.json 移动到 Resources 目录: {secret_file.relative_to(target_app)}")
                        try:
                            shutil.move(str(secret_file), str(resources_secret))
                            # 然后在 Frameworks 中创建符号链接
                            secret_file.symlink_to("../Resources/google_client_secret.json")
                            log_info(f"    ✓ 已移动并创建符号链接: google_client_secret.json -> ../Resources/google_client_secret.json")
                        except Exception as e:
                            log_warn(f"    移动失败: {e}")
                    elif secret_file.exists() and secret_file.is_symlink():
                        # Frameworks 中有符号链接，但 Resources 中不存在，删除符号链接
                        log_warn(f"  Resources 中不存在 google_client_secret.json，删除 Frameworks 中的符号链接: {secret_file.relative_to(target_app)}")
                        try:
                            secret_file.unlink()
                            log_info(f"    ✓ 已删除无效符号链接")
                        except Exception as e:
                            log_warn(f"    删除失败: {e}")
                    else:
                        log_warn(f"  ⚠ google_client_secret.json 不存在于 Resources 或 Frameworks（可能导致 Google 登录功能不可用）")
                
                # 修复 python3.10 目录位置（关键修复：解决 _struct 等模块缺失问题）
                # Python 运行时需要在 Frameworks/python3.10/lib-dynload 中找到 C 扩展模块
                # 但为了避免 codesign 将其当作 bundle 处理，应该在 Frameworks 中创建符号链接指向 Resources
                python310_in_resources = resources_dir / "python3.10"
                python310_in_frameworks = frameworks_dir / "python3.10"
                
                if python310_in_resources.exists():
                    if python310_in_frameworks.exists():
                        # Frameworks 中已存在
                        if python310_in_frameworks.is_symlink():
                            # 已经是符号链接，检查指向是否正确
                            target = python310_in_frameworks.readlink()
                            if str(target) != "../Resources/python3.10":
                                log_warn(f"  修复 python3.10 符号链接指向: {python310_in_frameworks.relative_to(target_app)}")
                                try:
                                    python310_in_frameworks.unlink()
                                    python310_in_frameworks.symlink_to("../Resources/python3.10")
                                    log_info(f"    ✓ 已修复: python3.10 -> ../Resources/python3.10")
                                except Exception as e:
                                    log_warn(f"    修复失败: {e}")
                            else:
                                log_info(f"  python3.10 符号链接已正确: {python310_in_frameworks.relative_to(target_app)}")
                        else:
                            # Frameworks 中是真实目录，需要删除并创建符号链接
                            log_warn(f"  Frameworks 中的 python3.10 是真实目录，需要转换为符号链接（避免 codesign 将其当作 bundle）")
                            try:
                                shutil.rmtree(python310_in_frameworks)
                                python310_in_frameworks.symlink_to("../Resources/python3.10")
                                log_info(f"    ✓ 已转换: python3.10 -> ../Resources/python3.10")
                            except Exception as e:
                                log_error(f"    转换失败: {e}")
                    else:
                        # Frameworks 中不存在，创建符号链接
                        log_warn(f"  在 Frameworks 中创建 python3.10 符号链接（修复 _struct 等模块缺失问题）")
                        try:
                            python310_in_frameworks.symlink_to("../Resources/python3.10")
                            log_info(f"    ✓ 已创建: python3.10 -> ../Resources/python3.10")
                        except Exception as e:
                            log_error(f"    创建失败: {e}")
                    
                    # 验证 lib-dynload 目录是否存在（通过符号链接）
                    if python310_in_frameworks.exists():
                        lib_dynload = python310_in_frameworks / "lib-dynload"
                        if lib_dynload.exists():
                            so_files = list(lib_dynload.glob("*.so"))
                            log_info(f"    ✓ lib-dynload 目录包含 {len(so_files)} 个 .so 文件")
                            # 检查关键模块
                            key_modules = ["_struct.cpython-310-darwin.so", "_ctypes.cpython-310-darwin.so"]
                            for module in key_modules:
                                module_path = lib_dynload / module
                                if module_path.exists():
                                    log_info(f"    ✓ 找到关键模块: {module}")
                                else:
                                    log_warn(f"    ⚠ 未找到关键模块: {module}")
                        else:
                            log_warn(f"    ⚠ lib-dynload 目录不存在")
                else:
                    log_warn(f"  ⚠ 未找到 python3.10 目录（可能导致 C 扩展模块缺失）")
                
                # 修复 themes 目录（应该在 Resources 中，Frameworks 中有符号链接）
                themes_in_resources = resources_dir / "themes"
                themes_in_frameworks = frameworks_dir / "themes"
                
                if themes_in_resources.exists():
                    if themes_in_frameworks.exists():
                        if not themes_in_frameworks.is_symlink():
                            # Frameworks 中是真实目录，需要删除并创建符号链接
                            log_warn(f"  Frameworks 中的 themes 是真实目录，需要转换为符号链接")
                            try:
                                shutil.rmtree(themes_in_frameworks)
                                themes_in_frameworks.symlink_to("../Resources/themes")
                                log_info(f"    ✓ 已转换: themes -> ../Resources/themes")
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                        else:
                            # 检查符号链接指向是否正确
                            target = themes_in_frameworks.readlink()
                            if str(target) != "../Resources/themes":
                                log_warn(f"  修复 themes 符号链接指向")
                                try:
                                    themes_in_frameworks.unlink()
                                    themes_in_frameworks.symlink_to("../Resources/themes")
                                    log_info(f"    ✓ 已修复: themes -> ../Resources/themes")
                                except Exception as e:
                                    log_warn(f"    修复失败: {e}")
                    else:
                        # Frameworks 中不存在，创建符号链接
                        log_warn(f"  在 Frameworks 中创建 themes 符号链接")
                        try:
                            themes_in_frameworks.symlink_to("../Resources/themes")
                            log_info(f"    ✓ 已创建: themes -> ../Resources/themes")
                        except Exception as e:
                            log_warn(f"    创建失败: {e}")
                else:
                    log_warn(f"  ⚠ 未找到 themes 目录")
                
                # 修复 .dist-info 目录（应该在 Resources 中，Frameworks 中有符号链接）
                # 查找所有 .dist-info 目录
                dist_info_dirs = []
                if resources_dir.exists():
                    for item in resources_dir.iterdir():
                        if item.is_dir() and ".dist-info" in item.name:
                            dist_info_dirs.append(item.name)
                
                for dist_info_name in dist_info_dirs:
                    dist_info_in_resources = resources_dir / dist_info_name
                    dist_info_in_frameworks = frameworks_dir / dist_info_name
                    
                    if dist_info_in_resources.exists():
                        if dist_info_in_frameworks.exists():
                            if not dist_info_in_frameworks.is_symlink():
                                # Frameworks 中是真实目录，需要删除并创建符号链接
                                log_warn(f"  Frameworks 中的 {dist_info_name} 是真实目录，需要转换为符号链接")
                                try:
                                    shutil.rmtree(dist_info_in_frameworks)
                                    dist_info_in_frameworks.symlink_to(f"../Resources/{dist_info_name}")
                                    log_info(f"    ✓ 已转换: {dist_info_name} -> ../Resources/{dist_info_name}")
                                except Exception as e:
                                    log_warn(f"    转换失败: {e}")
                        else:
                            # Frameworks 中不存在，创建符号链接
                            log_warn(f"  在 Frameworks 中创建 {dist_info_name} 符号链接")
                            try:
                                dist_info_in_frameworks.symlink_to(f"../Resources/{dist_info_name}")
                                log_info(f"    ✓ 已创建: {dist_info_name} -> ../Resources/{dist_info_name}")
                            except Exception as e:
                                log_warn(f"    创建失败: {e}")
                
                # 确保必需的 Python 包目录在正确的位置
                # 根据本地打包结构：
                # - 大部分 Python 包应该在 Frameworks 中（真实目录）：AppKit, CoreFoundation, Foundation, PIL, bcrypt, charset_normalizer, cryptography, objc, uvloop, yaml
                # - nacl 应该在 Frameworks 中（真实目录），但其中的 py.typed 应该是符号链接指向 Resources
                # - certifi 应该在 Resources 中（真实目录），Frameworks 中有符号链接
                essential_python_packages_frameworks = ["AppKit", "CoreFoundation", "Foundation", "PIL", "bcrypt", 
                                                      "charset_normalizer", "cryptography", "nacl", "objc", "uvloop", "yaml"]
                essential_python_packages_resources = ["certifi"]  # certifi 应该在 Resources 中
                
                # 修复 nacl 目录中的 py.typed 文件（应该是符号链接）
                nacl_dir = frameworks_dir / "nacl"
                if nacl_dir.exists() and nacl_dir.is_dir() and not nacl_dir.is_symlink():
                    py_typed_in_frameworks = nacl_dir / "py.typed"
                    py_typed_in_resources = resources_dir / "nacl" / "py.typed"
                    
                    if py_typed_in_resources.exists():
                        if py_typed_in_frameworks.exists():
                            if not py_typed_in_frameworks.is_symlink():
                                # Frameworks 中是真实文件，需要删除并创建符号链接
                                log_warn(f"  Frameworks/nacl/py.typed 是真实文件，需要转换为符号链接")
                                try:
                                    py_typed_in_frameworks.unlink()
                                    py_typed_in_frameworks.symlink_to("../../Resources/nacl/py.typed")
                                    log_info(f"    ✓ 已转换: nacl/py.typed -> ../../Resources/nacl/py.typed")
                                except Exception as e:
                                    log_warn(f"    转换失败: {e}")
                            else:
                                # 检查符号链接指向是否正确
                                target = py_typed_in_frameworks.readlink()
                                if str(target) != "../../Resources/nacl/py.typed":
                                    log_warn(f"  修复 nacl/py.typed 符号链接指向")
                                    try:
                                        py_typed_in_frameworks.unlink()
                                        py_typed_in_frameworks.symlink_to("../../Resources/nacl/py.typed")
                                        log_info(f"    ✓ 已修复: nacl/py.typed -> ../../Resources/nacl/py.typed")
                                    except Exception as e:
                                        log_warn(f"    修复失败: {e}")
                        else:
                            # Frameworks 中不存在，创建符号链接
                            log_warn(f"  在 Frameworks/nacl 中创建 py.typed 符号链接")
                            try:
                                py_typed_in_frameworks.symlink_to("../../Resources/nacl/py.typed")
                                log_info(f"    ✓ 已创建: nacl/py.typed -> ../../Resources/nacl/py.typed")
                            except Exception as e:
                                log_warn(f"    创建失败: {e}")
                    elif py_typed_in_frameworks.exists() and not py_typed_in_frameworks.is_symlink():
                        # Frameworks 中有真实文件，但 Resources 中没有，需要移动
                        log_warn(f"  移动 nacl/py.typed 从 Frameworks 到 Resources")
                        try:
                            # 确保 Resources/nacl 目录存在
                            resources_nacl = resources_dir / "nacl"
                            resources_nacl.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(py_typed_in_frameworks), str(py_typed_in_resources))
                            py_typed_in_frameworks.symlink_to("../../Resources/nacl/py.typed")
                            log_info(f"    ✓ 已移动并创建符号链接: nacl/py.typed")
                        except Exception as e:
                            log_warn(f"    移动失败: {e}")
                
                # 处理应该在 Frameworks 中的包
                for package_name in essential_python_packages_frameworks:
                    package_in_frameworks = frameworks_dir / package_name
                    package_in_resources = resources_dir / package_name
                    
                    if package_in_resources.exists() and not package_in_frameworks.exists():
                        # Resources 中有，Frameworks 中没有，需要移动
                        log_warn(f"  移动 {package_name} 从 Resources 到 Frameworks")
                        try:
                            shutil.move(str(package_in_resources), str(package_in_frameworks))
                            log_info(f"    ✓ 已移动: {package_name}")
                        except Exception as e:
                            log_warn(f"    移动失败: {e}")
                    elif package_in_resources.exists() and package_in_frameworks.exists():
                        # 两个位置都存在
                        if package_in_frameworks.is_symlink():
                            # Frameworks 中是符号链接，但应该是真实目录
                            log_warn(f"  Frameworks 中的 {package_name} 是符号链接，需要转换为真实目录")
                            try:
                                package_in_frameworks.unlink()
                                if package_in_resources.is_dir():
                                    shutil.move(str(package_in_resources), str(package_in_frameworks))
                                    log_info(f"    ✓ 已转换: {package_name}")
                                else:
                                    # Resources 中也是符号链接，需要找到真实目录
                                    real_path = package_in_resources.readlink()
                                    if real_path.is_absolute():
                                        shutil.copytree(real_path, package_in_frameworks)
                                    else:
                                        shutil.copytree(resources_dir / real_path, package_in_frameworks)
                                    log_info(f"    ✓ 已复制: {package_name}")
                            except Exception as e:
                                log_warn(f"    转换失败: {e}")
                
                # 处理应该在 Resources 中的包（certifi）
                for package_name in essential_python_packages_resources:
                    package_in_frameworks = frameworks_dir / package_name
                    package_in_resources = resources_dir / package_name
                    
                    if package_in_resources.exists():
                        if package_in_frameworks.exists():
                            if not package_in_frameworks.is_symlink():
                                # Frameworks 中是真实目录，需要删除并创建符号链接
                                log_warn(f"  Frameworks 中的 {package_name} 是真实目录，需要转换为符号链接")
                                try:
                                    shutil.rmtree(package_in_frameworks)
                                    package_in_frameworks.symlink_to(f"../Resources/{package_name}")
                                    log_info(f"    ✓ 已转换: {package_name} -> ../Resources/{package_name}")
                                except Exception as e:
                                    log_warn(f"    转换失败: {e}")
                            else:
                                # 检查符号链接指向是否正确
                                target = package_in_frameworks.readlink()
                                if str(target) != f"../Resources/{package_name}":
                                    log_warn(f"  修复 {package_name} 符号链接指向")
                                    try:
                                        package_in_frameworks.unlink()
                                        package_in_frameworks.symlink_to(f"../Resources/{package_name}")
                                        log_info(f"    ✓ 已修复: {package_name} -> ../Resources/{package_name}")
                                    except Exception as e:
                                        log_warn(f"    修复失败: {e}")
                        else:
                            # Frameworks 中不存在，创建符号链接
                            log_warn(f"  在 Frameworks 中创建 {package_name} 符号链接")
                            try:
                                package_in_frameworks.symlink_to(f"../Resources/{package_name}")
                                log_info(f"    ✓ 已创建: {package_name} -> ../Resources/{package_name}")
                            except Exception as e:
                                log_warn(f"    创建失败: {e}")
                    elif package_in_frameworks.exists() and not package_in_frameworks.is_symlink():
                        # Frameworks 中有真实目录，但 Resources 中没有，需要移动
                        log_warn(f"  移动 {package_name} 从 Frameworks 到 Resources")
                        try:
                            shutil.move(str(package_in_frameworks), str(package_in_resources))
                            package_in_frameworks.symlink_to(f"../Resources/{package_name}")
                            log_info(f"    ✓ 已移动并创建符号链接: {package_name}")
                        except Exception as e:
                            log_warn(f"    移动失败: {e}")
                
                # 检查 resources 目录
                resources_in_frameworks = frameworks_dir / "resources"
                if resources_in_frameworks.exists() and not resources_in_frameworks.is_symlink():
                    resources_resources = resources_dir / "resources"
                    if resources_resources.exists():
                        log_warn(f"  将 resources 目录转换为符号链接: {resources_in_frameworks.relative_to(target_app)}")
                        try:
                            shutil.rmtree(resources_in_frameworks)
                            resources_in_frameworks.symlink_to("../Resources/resources")
                            log_info(f"    ✓ 已转换: resources -> ../Resources/resources")
                        except Exception as e:
                            log_warn(f"    转换失败: {e}")
    
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
                        log_info("  修复并签名 Qt 框架...")
                        framework_dirs = [d for d in qt_dir.rglob("*.framework") if d.is_dir()]
                        for framework_dir in framework_dirs:
                            log_info(f"    处理框架: {framework_dir.relative_to(target_app)}")
                            
                            # 修复框架结构（确保符号链接和 Info.plist 存在）
                            framework_name = framework_dir.stem  # 例如 "QtQmlMeta"
                            versions_dir = framework_dir / "Versions"
                            current_dir = versions_dir / "Current"
                            resources_dir = framework_dir / "Resources"
                            
                            # 修复 Versions/Current 符号链接
                            if versions_dir.exists():
                                version_dirs = [d for d in versions_dir.iterdir() if d.is_dir() and d.name != "Current"]
                                if version_dirs:
                                    target_version = version_dirs[0].name  # 通常是 "A"
                                    if current_dir.exists() and not current_dir.is_symlink():
                                        # 删除真实目录，创建符号链接
                                        shutil.rmtree(current_dir)
                                    if not current_dir.exists():
                                        current_dir.symlink_to(target_version)
                                        log_info(f"      ✓ 修复 Versions/Current 符号链接 -> {target_version}")
                            
                            # 修复根目录的可执行文件符号链接
                            executable_link = framework_dir / framework_name
                            if executable_link.exists() and not executable_link.is_symlink():
                                # 删除真实文件，创建符号链接
                                executable_link.unlink()
                            if not executable_link.exists() and current_dir.exists():
                                executable_link.symlink_to(f"Versions/Current/{framework_name}")
                                log_info(f"      ✓ 修复 {framework_name} 符号链接")
                            
                            # 修复 Resources 目录符号链接
                            resources_link = framework_dir / "Resources"
                            if resources_link.exists() and not resources_link.is_symlink():
                                # 删除真实目录，创建符号链接
                                shutil.rmtree(resources_link)
                            if not resources_link.exists() and current_dir.exists():
                                resources_link.symlink_to("Versions/Current/Resources")
                                log_info(f"      ✓ 修复 Resources 符号链接")

                            # 修复 Helpers 目录符号链接（QtWebEngineCore 需要）
                            helpers_target = current_dir / "Helpers"
                            helpers_link = framework_dir / "Helpers"
                            if helpers_link.exists() and not helpers_link.is_symlink():
                                try:
                                    shutil.rmtree(helpers_link)
                                    log_info("      ✓ 删除根目录 Helpers 真实目录（将创建符号链接）")
                                except Exception as e:
                                    log_warn(f"      ⚠ 删除根目录 Helpers 目录失败: {e}")
                            if helpers_target.exists() and not helpers_link.exists():
                                try:
                                    helpers_link.symlink_to("Versions/Current/Helpers")
                                    log_info("      ✓ 修复 Helpers 符号链接 -> Versions/Current/Helpers")
                                except Exception as e:
                                    log_warn(f"      ⚠ 创建 Helpers 符号链接失败: {e}")
                            
                            # 第一步：彻底清理框架根目录（在修复符号链接之前）
                            # 嵌入式框架的根目录应该只包含符号链接和 Versions 目录
                            # 删除所有非符号链接的文件（包括 Info.plist）
                            log_info(f"      清理框架根目录...")
                            for item in list(framework_dir.iterdir()):  # 使用 list() 避免迭代时修改
                                item_name = item.name
                                # 跳过 Versions 目录和符号链接
                                if item_name == "Versions" or item.is_symlink():
                                    continue
                                # 如果根目录下的目录在 Versions/Current 中存在对应内容，先转成符号链接
                                target_in_versions = current_dir / item_name
                                if item.is_dir() and target_in_versions.exists():
                                    try:
                                        shutil.rmtree(item)
                                        item.symlink_to(f"Versions/Current/{item_name}")
                                        log_info(f"      ✓ 修复根目录目录为符号链接: {item_name} -> Versions/Current/{item_name}")
                                        continue
                                    except Exception as e:
                                        log_warn(f"      ⚠ 将根目录目录转为符号链接失败 {item_name}: {e}")
                                # 删除所有非符号链接的文件（包括 Info.plist）
                                if item.is_file():
                                    try:
                                        item.unlink()
                                        log_info(f"      ✓ 删除根目录文件: {item_name}")
                                    except Exception as e:
                                        log_warn(f"      ⚠ 删除根目录文件 {item_name} 失败: {e}")
                                # 如果 Resources 是真实目录（不是符号链接），也需要删除（后面会创建符号链接）
                                elif item_name == "Resources" and item.is_dir() and not item.is_symlink():
                                    try:
                                        shutil.rmtree(item)
                                        log_info(f"      ✓ 删除根目录 Resources 真实目录（将创建符号链接）")
                                    except Exception as e:
                                        log_warn(f"      ⚠ 删除根目录 Resources 目录失败: {e}")
                            
                            # 第二步：确保 Info.plist 在正确位置（Versions/Current/Resources/Info.plist）
                            # 如果根目录还有 Info.plist（清理后不应该有，但为了安全再检查一次），移动到正确位置
                            info_plist_root = framework_dir / "Info.plist"
                            if info_plist_root.exists() and info_plist_root.is_file():
                                if current_dir.exists():
                                    actual_resources = current_dir / "Resources"
                                    info_plist_correct = actual_resources / "Info.plist"
                                    if not info_plist_correct.exists():
                                        # 正确位置没有，移动到正确位置
                                        try:
                                            actual_resources.mkdir(parents=True, exist_ok=True)
                                            shutil.move(str(info_plist_root), str(info_plist_correct))
                                            log_info(f"      ✓ 移动 Info.plist 到正确位置: Versions/Current/Resources/")
                                        except Exception as e:
                                            log_warn(f"      ⚠ 移动 Info.plist 失败: {e}")
                                # 无论正确位置是否有，都删除根目录的 Info.plist（嵌入式框架不需要）
                                if info_plist_root.exists():
                                    try:
                                        info_plist_root.unlink()
                                        log_info(f"      ✓ 删除根目录下的 Info.plist（嵌入式框架不需要）")
                                    except Exception as e:
                                        log_warn(f"      ⚠ 删除根目录 Info.plist 失败: {e}")
                            
                            # 第三步：验证框架根目录是否干净（签名前最后检查）
                            root_files = [f for f in framework_dir.iterdir() if f.is_file() and not f.is_symlink()]
                            if root_files:
                                log_warn(f"      ⚠ 警告：框架根目录仍有非符号链接文件，强制删除:")
                                for root_file in root_files:
                                    try:
                                        root_file.unlink()
                                        log_info(f"      ✓ 强制删除: {root_file.name}")
                                    except Exception as e:
                                        log_warn(f"      ⚠ 删除失败 {root_file.name}: {e}")
                            
                            # 第四步：先签名框架内的所有文件
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
                            
                            # 第五步：签名整个框架目录（不使用 --deep，避免重新签名）
                            # 注意：嵌入式框架不应该使用 --deep，因为我们已经手动签名了所有文件
                            result = subprocess.run([
                                "codesign", "--force", "--sign", codesign_identity,
                                "--options", "runtime",
                                "--timestamp",
                                str(framework_dir)
                            ], capture_output=True, text=True)
                            if result.returncode != 0:
                                error_msg = (result.stderr or "") + (result.stdout or "")
                                log_warn(f"    框架签名失败: {framework_dir.relative_to(target_app)}")
                                log_warn(f"    错误: {error_msg}")
                                # 如果签名失败且错误信息包含 "unsealed contents"，需要更彻底地清理根目录
                                if "unsealed contents" in error_msg.lower():
                                    log_warn(f"    检测到未密封内容，强制清理框架根目录...")
                                    # 再次检查并删除根目录中的所有非符号链接文件
                                    for item in list(framework_dir.iterdir()):
                                        if item.name == "Versions" or item.is_symlink():
                                            continue
                                        # 优先将存在对应 Versions/Current 目录的真实目录转为符号链接
                                        target_in_versions = current_dir / item.name
                                        if item.is_dir() and target_in_versions.exists():
                                            try:
                                                shutil.rmtree(item)
                                                item.symlink_to(f"Versions/Current/{item.name}")
                                                log_info(f"      ✓ 将根目录目录转换为符号链接: {item.name} -> Versions/Current/{item.name}")
                                                continue
                                            except Exception as e:
                                                log_warn(f"      ⚠ 转换根目录目录为符号链接失败 {item.name}: {e}")
                                        if item.is_file():
                                            try:
                                                item.unlink()
                                                log_info(f"      ✓ 强制删除根目录文件: {item.name}")
                                            except Exception as e:
                                                log_warn(f"      ⚠ 删除文件 {item.name} 失败: {e}")
                                        elif item.is_dir() and item.name != "Versions":
                                            try:
                                                shutil.rmtree(item)
                                                log_info(f"      ✓ 强制删除根目录目录: {item.name}")
                                            except Exception as e:
                                                log_warn(f"      ⚠ 删除目录 {item.name} 失败: {e}")
                                    # 重新尝试签名
                                    retry_result = subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",
                                        str(framework_dir)
                                    ], capture_output=True, text=True)
                                    if retry_result.returncode == 0:
                                        log_info(f"    ✓ 框架已签名（重试成功）: {framework_dir.name}")
                                    else:
                                        retry_error = (retry_result.stderr or "") + (retry_result.stdout or "")
                                        log_warn(f"    ⚠ 框架签名重试仍失败: {retry_error}")
                                else:
                                    # 如果签名失败，检查根目录是否还有问题文件
                                    remaining_files = [f.name for f in framework_dir.iterdir() if f.is_file() and not f.is_symlink()]
                                    if remaining_files:
                                        log_warn(f"    根目录仍有文件: {remaining_files}")
                            else:
                                log_info(f"    ✓ 框架已签名: {framework_dir.name}")
                        
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
            
            # 第三步：签名 Python 包目录（在签名主可执行文件之前）
            # 这些目录在 Frameworks 中，需要确保整个目录被签名
            # 注意：即使目录中包含文本文件（如 py.typed），也需要签名整个目录
            log_info("  签名 Python 包目录...")
            python_package_dirs = ["AppKit", "CoreFoundation", "Foundation", "PIL", "bcrypt", 
                                  "charset_normalizer", "cryptography", "nacl", "objc", "uvloop", "yaml"]
            for package_name in python_package_dirs:
                package_dir = frameworks_dir / package_name
                if package_dir.exists() and package_dir.is_dir() and not package_dir.is_symlink():
                    # 先签名目录中的所有二进制文件
                    for item in package_dir.rglob("*"):
                        if item.is_file():
                            # 跳过文本文件和资源文件（这些不需要签名）
                            if item.suffix in [".py", ".pyc", ".pyo", ".txt", ".md", ".pem", ".crt", ".key", ".typed"]:
                                continue
                            # 检查是否是 Mach-O 二进制文件或 .so 文件
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
                                elif item.suffix == ".so":
                                    log_info(f"    签名: {item.relative_to(target_app)}")
                                    subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",
                                        str(item)
                                    ], check=False, capture_output=True)
                            except Exception:
                                pass
                    
                    # 然后签名整个目录（这样 codesign 就不会检查其中的文本文件）
                    log_info(f"    签名目录: {package_dir.relative_to(target_app)}")
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",
                        str(package_dir)
                    ], check=False, capture_output=True)
            
            # 第四步：验证并修复关键文件签名（在签名主可执行文件之前，与 build_client.py 保持一致）
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
            
            # 第五步：在签名主可执行文件之前，先签名所有包含 .qmltypes 和 qmldir 文件的目录
            # 这样可以"密封"这些目录，避免 codesign 检查未签名的文本文件
            # 注意：codesign 不能直接签名普通目录，但我们可以尝试签名包含这些文件的目录
            # 如果失败，我们会在签名主可执行文件时使用 --deep 选项作为备选方案
            if not should_skip_step(Step.SIGN_MAIN, start_from_step):
                log_warn("在签名主可执行文件之前，先签名包含 .qmltypes 和 qmldir 文件的目录（密封目录）...")
                qt_qml_dir = frameworks_dir / "PySide6" / "Qt" / "qml"
                if qt_qml_dir.exists():
                    # 查找所有包含 .qmltypes 或 qmldir 文件的目录
                    qml_dirs = set()
                    # 查找包含 .qmltypes 文件的目录
                    for qmltypes_file in qt_qml_dir.rglob("*.qmltypes"):
                        qml_dirs.add(qmltypes_file.parent)
                    # 查找包含 qmldir 文件的目录
                    for qmldir_file in qt_qml_dir.rglob("qmldir"):
                        qml_dirs.add(qmldir_file.parent)
                    
                    # 签名这些目录（从最深层到最浅层，避免重复签名）
                    sorted_dirs = sorted(qml_dirs, key=lambda p: len(str(p)), reverse=True)
                    signed_count = 0
                    for qml_dir in sorted_dirs:
                        try:
                            # 尝试签名目录（codesign 可能无法签名普通目录，但我们可以尝试）
                            result = subprocess.run([
                                "codesign", "--force", "--sign", codesign_identity,
                                "--options", "runtime",
                                "--timestamp",
                                "--preserve-metadata=entitlements,requirements,flags",
                                str(qml_dir)
                            ], check=False, capture_output=True, text=True, timeout=60)
                            if result.returncode == 0:
                                signed_count += 1
                        except Exception as e:
                            # 忽略错误，因为 codesign 可能无法签名普通目录
                            pass
                    log_info(f"✓ 已尝试签名 {signed_count}/{len(sorted_dirs)} 个包含 .qmltypes 或 qmldir 文件的目录")
            
            # 第六步：先签名主可执行文件（与 build_client.py 保持一致）
            if not should_skip_step(Step.SIGN_MAIN, start_from_step):
                log_step(Step.SIGN_MAIN, "签名应用包主可执行文件...")
                main_executable = target_app / "Contents" / "MacOS" / app_name
                if main_executable.exists():
                    # 修复主可执行文件的权限（GitHub Actions 打包的文件可能没有执行权限）
                    current_mode = main_executable.stat().st_mode
                    # 确保有执行权限：所有者、组、其他用户都有执行权限 (755)
                    # 使用数字常量 0o100 (S_IXUSR) 避免与 pathlib.Path.stat() 冲突
                    if not (current_mode & 0o100):  # 0o100 = stat.S_IXUSR (所有者执行权限)
                        os.chmod(main_executable, 0o755)
                        log_info("✓ 修复主可执行文件权限 (755)")
                    
                    # 先签名主可执行文件（使用 check=True，失败会立即报错，与 build_client.py 保持一致）
                    # 注意：如果签名失败并提示 .qmltypes 文件未签名，这是正常的，因为文本文件不需要签名
                    # 但 codesign 在签名主可执行文件时会检查所有子组件，包括这些文本文件
                    try:
                        subprocess.run([
                            "codesign", "--force", "--sign", codesign_identity,
                            "--options", "runtime",
                            "--timestamp",
                            str(main_executable)
                        ], check=True, capture_output=True, text=True)  # 使用 check=True，失败会立即报错
                        log_info("✓ 主可执行文件已签名")
                    except subprocess.CalledProcessError as e:
                        # 检查错误信息是否与 .qmltypes 文件相关
                        error_output = (e.stderr or "") + (e.stdout or "")
                        log_warn(f"  签名主可执行文件失败，错误信息: {error_output}")
                        if "qmltypes" in error_output.lower() or "qmldir" in error_output.lower() or "code object is not signed at all" in error_output.lower():
                            log_warn("  警告: 签名主可执行文件时检测到未签名的文本文件（如 .qmltypes 或 qmldir）")
                            log_warn("  这是正常的，文本文件不需要签名。尝试先签名整个应用包以密封它...")
                            # 先签名整个应用包（不使用 --deep 和 --strict），这样可以"密封"应用包
                            # 使用 --preserve-metadata 保留已签名组件的元数据
                            try:
                                seal_result = subprocess.run([
                                    "codesign", "--force", "--sign", codesign_identity,
                                    "--options", "runtime",
                                    "--timestamp",
                                    "--preserve-metadata=entitlements,requirements,flags",
                                    str(target_app)
                                ], check=False, capture_output=True, text=True, timeout=300)
                                if seal_result.returncode != 0:
                                    log_warn(f"  密封应用包时出现警告: {seal_result.stderr or seal_result.stdout}")
                                log_info("  应用包已初步签名（密封），现在重新签名主可执行文件...")
                                # 重新签名主可执行文件
                                retry_result = subprocess.run([
                                    "codesign", "--force", "--sign", codesign_identity,
                                    "--options", "runtime",
                                    "--timestamp",
                                    str(main_executable)
                                ], check=False, capture_output=True, text=True)
                                if retry_result.returncode != 0:
                                    retry_error = (retry_result.stderr or "") + (retry_result.stdout or "")
                                    log_warn(f"  重新签名主可执行文件失败: {retry_error}")
                                    # 如果仍然失败，尝试使用 --deep 选项签名整个应用包
                                    # 这会在签名时递归处理所有子组件，包括文本文件
                                    log_warn("  尝试使用 --deep 选项签名整个应用包（递归处理所有子组件）...")
                                    # 使用时间戳重试机制（时间戳是必备的，失败后直接停止）
                                    timestamp_max_retries = 3
                                    timestamp_retry_delay = 5
                                    timestamp_success = False
                                    
                                    for timestamp_attempt in range(1, timestamp_max_retries + 1):
                                        log_warn(f"  尝试使用 --deep 选项签名（{timestamp_attempt}/{timestamp_max_retries}）...")
                                        deep_result = subprocess.run([
                                            "codesign", "--force", "--sign", codesign_identity,
                                            "--options", "runtime",
                                            "--timestamp",
                                            "--deep",
                                            str(target_app)
                                        ], check=False, capture_output=True, text=True, timeout=600)
                                        if deep_result.returncode == 0:
                                            log_info("✓ 使用 --deep 选项成功签名整个应用包")
                                            timestamp_success = True
                                            break
                                        else:
                                            deep_error = (deep_result.stderr or "") + (deep_result.stdout or "")
                                            error_msg = deep_error.lower()
                                            if "timestamp service is not available" in error_msg or "network" in error_msg or "timeout" in error_msg:
                                                if timestamp_attempt < timestamp_max_retries:
                                                    log_warn(f"  ⚠ 时间戳服务不可用，{timestamp_retry_delay} 秒后重试...")
                                                    time.sleep(timestamp_retry_delay)
                                                    timestamp_retry_delay *= 2
                                                    continue
                                                else:
                                                    log_error(f"  ✗ 时间戳服务不可用（已重试 {timestamp_max_retries} 次），停止进程")
                                                    log_error("  时间戳是必备的，无法通过公证，停止后续流程")
                                                    raise subprocess.CalledProcessError(deep_result.returncode, deep_result.args, deep_result.stdout, deep_result.stderr)
                                            else:
                                                log_error(f"  使用 --deep 选项签名应用包失败: {deep_error}")
                                                raise subprocess.CalledProcessError(deep_result.returncode, deep_result.args, deep_result.stdout, deep_result.stderr)
                                    
                                    if not timestamp_success:
                                        log_error("✗ 使用 --deep 选项签名应用包失败（时间戳服务不可用）")
                                        log_error("  时间戳是必备的，无法通过公证，停止后续流程")
                                        raise Exception("时间戳服务不可用，无法完成签名（已重试所有次数）")
                                    
                                    # 验证主可执行文件是否已签名
                                    verify_result = subprocess.run([
                                        "codesign", "-vvv", str(main_executable)
                                    ], check=False, capture_output=True, text=True, timeout=60)
                                    if verify_result.returncode == 0:
                                        log_info("✓ 主可执行文件已签名（通过 --deep 选项）")
                                    else:
                                        log_error(f"  主可执行文件验证失败: {verify_result.stderr or verify_result.stdout}")
                                        raise subprocess.CalledProcessError(1, ["codesign", "-vvv"], verify_result.stdout, verify_result.stderr)
                                else:
                                    log_info("✓ 主可执行文件已签名")
                            except subprocess.CalledProcessError as e2:
                                error_output2 = (e2.stderr or "") + (e2.stdout or "")
                                log_error(f"  错误: 无法签名主可执行文件: {error_output2}")
                                raise
                            except Exception as e2:
                                log_error(f"  错误: 无法签名主可执行文件: {e2}")
                                raise
                        else:
                            # 其他错误，直接抛出
                            log_error(f"签名主可执行文件失败: {error_output}")
                            raise
                else:
                    log_error(f"主可执行文件不存在: {main_executable}")
                    raise FileNotFoundError(f"主可执行文件不存在: {main_executable}")
            else:
                log_info(f"[跳过] 签名主可执行文件（从步骤 {start_from_step.value} 开始）")
                main_executable = target_app / "Contents" / "MacOS" / app_name
            
            # 第七步：签名整个应用包（不使用 --deep，避免重新签名）
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
        
        # 创建 DMG 和 PKG（复用 build_client.py 的逻辑）
        dmg_path = None
        pkg_path = None
        pkg_signed_successfully = False
        
        if codesign_identity:
            # 创建 DMG
            dmg_name = app_name.replace(" ", "_")
            dmg_path = dist_dir / f"{dmg_name}.dmg"
            
            if not should_skip_step(Step.CREATE_DMG, start_from_step):
                log_info("=" * 50)
                log_warn("开始创建 DMG...")
                print()  # 空行分隔
                
                # 创建临时目录
                temp_dmg_dir = dist_dir / "dmg_temp"
                if temp_dmg_dir.exists():
                    shutil.rmtree(temp_dmg_dir)
                temp_dmg_dir.mkdir(parents=True)
                
                # 使用 ditto 复制应用（保留扩展属性和签名）
                log_warn("复制应用包到临时目录（保留签名）...")
                subprocess.run([
                    "ditto", str(target_app), str(temp_dmg_dir / target_app.name)
                ], check=True)
                
                # 创建 Applications 链接
                os.symlink("/Applications", temp_dmg_dir / "Applications")
                
                # 创建 DMG
                log_warn("创建 DMG 文件...")
                subprocess.run([
                    "hdiutil", "create",
                    "-volname", app_name,
                    "-srcfolder", str(temp_dmg_dir),
                    "-ov",
                    "-format", "UDZO",
                    str(dmg_path)
                ], check=True)
                
                # 清理临时目录
                if temp_dmg_dir.exists():
                    shutil.rmtree(temp_dmg_dir)
                
                log_info(f"✓ DMG 创建成功: {dmg_path}")
            else:
                log_info(f"[跳过] 创建 DMG（从步骤 {start_from_step.value} 开始）")
                if not dmg_path.exists():
                    log_warn(f"⚠ DMG 文件不存在: {dmg_path}，跳过后续步骤")
                    dmg_path = None
            
            # DMG 代码签名
            if not should_skip_step(Step.SIGN_DMG, start_from_step) and dmg_path and dmg_path.exists():
                log_step(Step.SIGN_DMG, "DMG 代码签名...")
                timestamp_max_retries = 3
                timestamp_retry_delay = 5
                timestamp_timeout = 180  # 180 秒超时
                timestamp_success = False
                timestamp_result = None
                
                for timestamp_attempt in range(1, timestamp_max_retries + 1):
                    log_warn(f"  尝试使用时间戳签名（{timestamp_attempt}/{timestamp_max_retries}，超时 {timestamp_timeout} 秒）...")
                    try:
                        timestamp_result = subprocess.run([
                            "codesign", "--force", "--verify", "--verbose",
                            "--sign", codesign_identity,
                            "--timestamp",
                            str(dmg_path)
                        ], capture_output=True, text=True, check=False, timeout=timestamp_timeout)
                        
                        if timestamp_result.returncode == 0:
                            log_info("✓ DMG 代码签名完成（已使用时间戳）")
                            timestamp_success = True
                            break
                        else:
                            error_msg = timestamp_result.stderr or timestamp_result.stdout or ""
                            if "timestamp service is not available" in error_msg or "network" in error_msg.lower() or "timeout" in error_msg.lower():
                                if timestamp_attempt < timestamp_max_retries:
                                    log_warn(f"  ⚠ 时间戳服务不可用，{timestamp_retry_delay} 秒后重试...")
                                    time.sleep(timestamp_retry_delay)
                                    timestamp_retry_delay *= 2
                                else:
                                    log_warn(f"  ⚠ 时间戳服务不可用（已重试 {timestamp_max_retries} 次），将回退到不使用时间戳")
                            else:
                                log_error(f"DMG 签名失败: {error_msg[:200]}")
                                raise subprocess.CalledProcessError(timestamp_result.returncode, timestamp_result.args)
                    except subprocess.TimeoutExpired:
                        log_warn(f"  ⚠ DMG 签名超时（{timestamp_timeout} 秒），可能是时间戳服务响应慢")
                        # 创建一个假的 result 对象以便后续错误处理
                        class FakeResult:
                            returncode = 1
                            stderr = "签名超时：时间戳服务响应超时"
                            stdout = ""
                        timestamp_result = FakeResult()
                        if timestamp_attempt < timestamp_max_retries:
                            log_warn(f"  ⚠ {timestamp_retry_delay} 秒后重试...")
                            time.sleep(timestamp_retry_delay)
                            timestamp_retry_delay *= 2
                        else:
                            log_warn(f"  ⚠ DMG 签名超时（已重试 {timestamp_max_retries} 次），将回退到不使用时间戳")
                
                if not timestamp_success:
                    log_warn("⚠ 时间戳服务不可用，尝试不使用时间戳签名...")
                    subprocess.run([
                        "codesign", "--force", "--verify", "--verbose",
                        "--sign", codesign_identity,
                        "--timestamp=none",
                        str(dmg_path)
                    ], check=True)
                    log_info("✓ DMG 代码签名完成（未使用时间戳）")
                
                # 验证 DMG 签名
                log_warn("验证 DMG 签名...")
                dmg_verify = subprocess.run([
                    "codesign", "--verify", "--verbose",
                    str(dmg_path)
                ], capture_output=True, text=True, check=False)
                if dmg_verify.returncode == 0:
                    log_info("✓ DMG 签名验证通过")
                else:
                    log_error(f"DMG 签名验证失败: {dmg_verify.stderr}")
            else:
                log_info(f"[跳过] 签名 DMG（从步骤 {start_from_step.value} 开始）")
            
            # Apple 公证 DMG
            if not should_skip_step(Step.NOTARIZE_DMG, start_from_step) and apple_id and team_id and notary_password and dmg_path and dmg_path.exists():
                log_step(Step.NOTARIZE_DMG, "提交 DMG 到 Apple 公证...")
                max_retries = 3
                retry_delay = 10
                notarized = False
                
                for attempt in range(1, max_retries + 1):
                    try:
                        log_info(f"  尝试 {attempt}/{max_retries}...")
                        log_info("  正在上传 DMG 文件到 Apple 服务器（可能需要几分钟）...")
                        submit_result = subprocess.run([
                            "xcrun", "notarytool", "submit", str(dmg_path),
                            "--apple-id", apple_id,
                            "--team-id", team_id,
                            "--password", notary_password
                        ], check=True, capture_output=True, text=True, timeout=600)
                        
                        submission_id = None
                        for line in submit_result.stdout.split('\n'):
                            if 'id:' in line.lower():
                                submission_id = line.split(':')[-1].strip()
                                break
                        
                        if submission_id:
                            log_info(f"  ✓ 上传完成，提交 ID: {submission_id}")
                            log_info("  等待 Apple 处理公证（可能需要 5-15 分钟，最长 30 分钟）...")
                            
                            max_wait_time = 1800  # 30分钟
                            poll_interval = 30
                            notary_start_time = time.time()
                            status = None
                            
                            while time.time() - notary_start_time < max_wait_time:
                                try:
                                    status_result = subprocess.run([
                                        "xcrun", "notarytool", "log", submission_id,
                                        "--apple-id", apple_id,
                                        "--team-id", team_id,
                                        "--password", notary_password
                                    ], capture_output=True, text=True, timeout=60, check=False)
                                    
                                    if status_result.returncode == 0 and status_result.stdout:
                                        if "not yet available" in status_result.stdout.lower():
                                            elapsed = int(time.time() - notary_start_time)
                                            log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                        else:
                                            import json
                                            try:
                                                log_data = json.loads(status_result.stdout)
                                                status = log_data.get("status", "").lower()
                                                
                                                if status in ['accepted', 'success']:
                                                    log_info("  ✓ DMG 公证成功！")
                                                    notarized = True
                                                    break
                                                elif status in ['invalid', 'rejected', 'failed']:
                                                    log_error(f"  ✗ DMG 公证失败，状态: {status}")
                                                    break
                                                elif status == 'in progress':
                                                    elapsed = int(time.time() - notary_start_time)
                                                    log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                            except json.JSONDecodeError:
                                                pass
                                except Exception as e:
                                    log_warn(f"  查询状态时出错: {e}")
                                
                                time.sleep(poll_interval)
                            
                            if notarized:
                                break
                            elif status in ['invalid', 'rejected', 'failed']:
                                # 非网络错误，不重试
                                break
                        else:
                            log_warn("  ⚠ 无法获取提交 ID")
                    except subprocess.CalledProcessError as e:
                        error_msg = e.stderr or e.stdout or ""
                        if "network" in error_msg.lower() or "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                            if attempt < max_retries:
                                log_error(f"✗ 网络错误（尝试 {attempt}/{max_retries}）")
                                log_warn(f"  等待 {retry_delay} 秒后重试...")
                                time.sleep(retry_delay)
                                retry_delay *= 2
                            else:
                                log_error("✗ DMG 公证最终失败（已重试所有次数）")
                        else:
                            log_error(f"✗ DMG 公证失败: {error_msg[:200]}")
                            break
                    except Exception as e:
                        log_error(f"✗ DMG 公证出错: {e}")
                        if attempt < max_retries:
                            log_warn(f"  等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                        else:
                            break
                
                if notarized:
                    log_info("✓ DMG 公证完成")
                else:
                    log_warn("⚠ 继续执行，但 DMG 未通过公证")
            else:
                if apple_id and team_id and notary_password:
                    log_info(f"[跳过] 公证 DMG（从步骤 {start_from_step.value} 开始）")
                else:
                    log_warn("⚠ 跳过 DMG 公证（需要设置 APPLE_ID, TEAM_ID, NOTARY_PASSWORD 环境变量）")
            
            # 创建 PKG 安装包
            pkg_name = app_name.replace(" ", "_")
            pkg_path = dist_dir / f"{pkg_name}.pkg"
            
            if not should_skip_step(Step.CREATE_PKG, start_from_step):
                log_step(Step.CREATE_PKG, "创建 PKG 安装包...")
                
                # 准备 PKG 资源目录
                pkg_resources = dist_dir / "pkg_resources"
                if pkg_resources.exists():
                    shutil.rmtree(pkg_resources)
                pkg_resources.mkdir(parents=True)
                
                # 创建 Distribution.xml
                distribution_xml = pkg_resources / "Distribution.xml"
                with open(distribution_xml, "w", encoding="utf-8") as f:
                    f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="1">
    <title>{app_name}</title>
    <organization>site.sanying</organization>
    <domains enable_localSystem="true"/>
    <options customize="never" require-scripts="false" rootVolumeOnly="true"/>
    <pkg-ref id="{app_id}"/>
    <product id="{app_id}" version="1.0.1" />
    <choices-outline>
        <line choice="{app_id}"/>
    </choices-outline>
    <choice id="{app_id}" visible="false">
        <pkg-ref id="{app_id}"/>
    </choice>
    <pkg-ref id="{app_id}" version="1.0.1" onConclusion="none">{pkg_name}_component.pkg</pkg-ref>
</installer-gui-script>''')
                
                # 使用 pkgbuild 创建组件包
                component_pkg = pkg_resources / f"{pkg_name}_component.pkg"
                log_warn("  使用 pkgbuild 创建组件包...")
                pkg_root = pkg_resources / "pkg_root"
                pkg_root.mkdir(exist_ok=True)
                applications_dir = pkg_root / "Applications"
                applications_dir.mkdir(exist_ok=True)
                
                # 使用 ditto 复制应用包（保留签名）
                log_warn("    复制应用包（保留签名）...")
                subprocess.run([
                    "ditto", str(target_app), str(applications_dir / target_app.name)
                ], check=True)
                
                subprocess.run([
                    "pkgbuild",
                    "--root", str(pkg_root),
                    "--identifier", app_id,
                    "--version", "1.0.1",
                    "--install-location", "/",
                    str(component_pkg)
                ], check=True)
                log_info("  ✓ 组件包创建成功")
                
                # 使用 productbuild 创建最终安装包
                log_warn("  使用 productbuild 创建最终安装包...")
                subprocess.run([
                    "productbuild",
                    "--distribution", str(distribution_xml),
                    "--package-path", str(pkg_resources),
                    "--resources", str(pkg_resources),
                    str(pkg_path)
                ], check=True)
                log_info("  ✓ PKG 安装包创建成功")
            else:
                log_info(f"[跳过] 创建 PKG（从步骤 {start_from_step.value} 开始）")
                if not pkg_path.exists():
                    log_warn(f"⚠ PKG 文件不存在: {pkg_path}，跳过后续步骤")
                    pkg_path = None
            
            # 签名 PKG（需要 Installer 证书）
            if not should_skip_step(Step.SIGN_PKG, start_from_step) and pkg_path and pkg_path.exists():
                log_step(Step.SIGN_PKG, "签名 PKG（使用 Installer 证书）...")
                
                if not installer_identity and codesign_identity:
                    if "Developer ID Application" in codesign_identity:
                        installer_identity = codesign_identity.replace("Developer ID Application", "Developer ID Installer")
                    else:
                        log_warn("  查找可用的 Installer 证书...")
                        find_result = subprocess.run([
                            "security", "find-identity", "-v", "-p", "codesigning"
                        ], capture_output=True, text=True, check=False)
                        
                        if find_result.returncode == 0:
                            import re
                            for line in find_result.stdout.split('\n'):
                                if "Developer ID Installer" in line:
                                    match = re.search(r'"([^"]+)"', line)
                                    if match:
                                        installer_identity = match.group(1)
                                        log_info(f"  找到 Installer 证书: {installer_identity}")
                                        break
                
                pkg_signed_successfully = False
                if installer_identity:
                    log_warn("签名 PKG（使用 Installer 证书）...")
                pkg_signed = dist_dir / f"{pkg_name}_signed.pkg"
                
                timestamp_max_retries = 3
                timestamp_retry_delay = 5
                # PKG 文件可能很大，且时间戳服务可能响应慢，设置较长的超时时间
                # 使用改进的超时机制后，即使卡住也能强制终止
                timestamp_timeout = 600  # 600 秒超时（10分钟，PKG 文件可能很大，需要更长时间）
                timestamp_success = False
                timestamp_result = None
                
                for timestamp_attempt in range(1, timestamp_max_retries + 1):
                    log_warn(f"  尝试使用时间戳签名（{timestamp_attempt}/{timestamp_max_retries}，超时 {timestamp_timeout} 秒）...")
                    try:
                        # 使用改进的超时机制，确保进程能被强制终止
                        timestamp_result = run_with_timeout_and_kill([
                            "productsign",
                            "--sign", installer_identity,
                            "--timestamp",
                            str(pkg_path),
                            str(pkg_signed)
                        ], timeout=timestamp_timeout, check=False, capture_output=True, text=True, log_prefix="    ")
                        
                        if timestamp_result.returncode == 0:
                            log_info("  ✓ PKG 签名完成（已使用时间戳）")
                            timestamp_success = True
                            break
                        else:
                            error_msg = timestamp_result.stderr or timestamp_result.stdout or ""
                            if "timestamp service is not available" in error_msg or "network" in error_msg.lower():
                                if timestamp_attempt < timestamp_max_retries:
                                    log_warn(f"  ⚠ 时间戳服务不可用，{timestamp_retry_delay} 秒后重试...")
                                    time.sleep(timestamp_retry_delay)
                                    timestamp_retry_delay *= 2
                                else:
                                    log_error(f"  ✗ 时间戳服务不可用（已重试 {timestamp_max_retries} 次）")
                            else:
                                log_error(f"  ✗ PKG 签名失败: {error_msg[:200]}")
                                raise Exception(f"PKG 签名失败: {error_msg[:200]}")
                    except subprocess.TimeoutExpired:
                        log_warn(f"  ⚠ PKG 签名超时（{timestamp_timeout} 秒），可能是时间戳服务响应慢")
                        # 创建一个假的 result 对象以便后续错误处理
                        class FakeResult:
                            returncode = 1
                            stderr = "签名超时：时间戳服务响应超时"
                            stdout = ""
                        timestamp_result = FakeResult()
                        if timestamp_attempt < timestamp_max_retries:
                            log_warn(f"  ⚠ {timestamp_retry_delay} 秒后重试...")
                            time.sleep(timestamp_retry_delay)
                            timestamp_retry_delay *= 2
                        else:
                            log_error(f"  ✗ PKG 签名超时（已重试 {timestamp_max_retries} 次）")
                
                if not timestamp_success:
                    error_msg = timestamp_result.stderr or timestamp_result.stdout or ""
                    log_error(f"  ✗ PKG 签名失败（已重试 {timestamp_max_retries} 次）: {error_msg[:200]}")
                    log_error("     无时间戳签名无法通过公证，构建终止")
                    raise Exception(f"PKG 签名失败：时间戳服务不可用（已重试 {timestamp_max_retries} 次）")
                
                if timestamp_success and pkg_signed.exists():
                    pkg_path.unlink()
                    pkg_signed.rename(pkg_path)
                    log_info("  ✓ PKG 文件已替换为签名版本")
                    pkg_signed_successfully = True
                else:
                    pkg_signed_successfully = False
            else:
                if installer_identity:
                    log_warn("⚠ 跳过 PKG 签名（需要 Developer ID Installer 证书）")
                else:
                    log_info(f"[跳过] 签名 PKG（从步骤 {start_from_step.value} 开始）")
                pkg_signed_successfully = False
            
            # 验证 PKG 签名
            if pkg_signed_successfully and pkg_path.exists():
                log_warn("验证 PKG 签名...")
                pkg_verify = subprocess.run([
                    "pkgutil", "--check-signature", str(pkg_path)
                ], capture_output=True, text=True, check=False)
                if pkg_verify.returncode == 0:
                    log_info("  ✓ PKG 签名验证通过")
                else:
                    log_warn(f"  ⚠ PKG 签名验证警告: {pkg_verify.stderr[:200]}")
            
            # PKG 公证
            if not should_skip_step(Step.NOTARIZE_PKG, start_from_step) and apple_id and team_id and notary_password and pkg_signed_successfully and pkg_path and pkg_path.exists():
                log_step(Step.NOTARIZE_PKG, "提交 PKG 到 Apple 公证...")
                max_retries = 3
                retry_delay = 10
                pkg_notarized = False
                
                for attempt in range(1, max_retries + 1):
                    try:
                        log_info(f"  尝试 {attempt}/{max_retries}...")
                        log_info("  正在上传 PKG 文件到 Apple 服务器...")
                        
                        submit_result = subprocess.run([
                            "xcrun", "notarytool", "submit", str(pkg_path),
                            "--apple-id", apple_id,
                            "--team-id", team_id,
                            "--password", notary_password
                        ], check=True, capture_output=True, text=True, timeout=600)
                        
                        submission_id = None
                        for line in submit_result.stdout.split('\n'):
                            if 'id:' in line.lower():
                                submission_id = line.split(':')[-1].strip()
                                break
                        
                        if submission_id:
                            log_info(f"  ✓ 上传完成，提交 ID: {submission_id}")
                            log_info("  等待 Apple 处理公证...")
                            
                            max_wait_time = 1800
                            poll_interval = 30
                            notary_start_time = time.time()
                            status = None
                            
                            while time.time() - notary_start_time < max_wait_time:
                                try:
                                    status_result = subprocess.run([
                                        "xcrun", "notarytool", "log", submission_id,
                                        "--apple-id", apple_id,
                                        "--team-id", team_id,
                                        "--password", notary_password
                                    ], capture_output=True, text=True, timeout=60, check=False)
                                    
                                    if status_result.returncode == 0 and status_result.stdout:
                                        if "not yet available" in status_result.stdout.lower():
                                            elapsed = int(time.time() - notary_start_time)
                                            log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                        else:
                                            import json
                                            try:
                                                log_data = json.loads(status_result.stdout)
                                                status = log_data.get("status", "").lower()
                                                
                                                if status in ['accepted', 'success']:
                                                    log_info("  ✓ PKG 公证成功！")
                                                    pkg_notarized = True
                                                    break
                                                elif status in ['invalid', 'rejected', 'failed']:
                                                    log_error(f"  ✗ PKG 公证失败，状态: {status}")
                                                    break
                                                elif status == 'in progress':
                                                    elapsed = int(time.time() - notary_start_time)
                                                    log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                            except json.JSONDecodeError:
                                                pass
                                except Exception as e:
                                    log_warn(f"  查询状态时出错: {e}")
                                
                                time.sleep(poll_interval)
                            
                            if pkg_notarized:
                                break
                            elif status in ['invalid', 'rejected', 'failed']:
                                break
                    except subprocess.CalledProcessError as e:
                        error_msg = e.stderr or e.stdout or ""
                        if "network" in error_msg.lower() or "connection" in error_msg.lower():
                            if attempt < max_retries:
                                log_error(f"✗ 网络错误（尝试 {attempt}/{max_retries}）")
                                log_warn(f"  等待 {retry_delay} 秒后重试...")
                                time.sleep(retry_delay)
                                retry_delay *= 2
                            else:
                                log_error("✗ PKG 公证最终失败（已重试所有次数）")
                        else:
                            log_error(f"✗ PKG 公证失败: {error_msg[:200]}")
                            break
                    except Exception as e:
                        log_error(f"✗ PKG 公证出错: {e}")
                        if attempt < max_retries:
                            log_warn(f"  等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                        else:
                            break
                
                if pkg_notarized:
                    log_info("✓ PKG 公证完成")
                else:
                    log_warn("⚠ 继续执行，但 PKG 未通过公证")
            else:
                if not pkg_signed_successfully:
                    log_warn("⚠ 跳过 PKG 公证（PKG 未签名）")
                elif apple_id and team_id and notary_password:
                    log_info(f"[跳过] 公证 PKG（从步骤 {start_from_step.value} 开始）")
                else:
                    log_warn("⚠ 跳过 PKG 公证（需要设置 APPLE_ID, TEAM_ID, NOTARY_PASSWORD 环境变量）")
            
            log_info("=" * 50)
            log_info("✓ DMG 和 PKG 创建完成")
            log_info(f"  DMG: {dmg_path}")
            log_info(f"  PKG: {pkg_path}")
        else:
            log_warn("⚠ 跳过 DMG 和 PKG 创建（需要设置 CODESIGN_IDENTITY 环境变量）")
            
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
    parser.add_argument(
        "--download-url",
        dest="download_url",
        help="直接指定下载URL（如果提供，将跳过从GitHub获取assets的步骤）"
    )
    parser.add_argument(
        "--arch",
        choices=["arm64", "intel"],
        help="指定架构（arm64 或 intel），如果提供--download-url则必须指定"
    )
    parser.add_argument(
        "--dir",
        dest="app_dir",
        help="直接指定 .app 路径（如果提供，将跳过下载和解压步骤，直接开始签名）"
    )
    
    args = parser.parse_args()
    
    client_type = args.client_type
    tag_name = args.tag_name
    repo_owner = args.repo_owner
    repo_name = args.repo_name
    # 优先使用 --api-key 选项，否则使用位置参数
    api_key = args.api_key_option or args.api_key
    download_url = args.download_url
    arch = args.arch
    app_dir = args.app_dir
    
    # 如果提供了 --dir，必须提供架构
    if app_dir and not arch:
        log_error("错误: 使用 --dir 时必须指定 --arch (arm64 或 intel)")
        sys.exit(1)
    
    # 如果提供了下载URL，必须提供架构
    if download_url and not arch:
        log_error("错误: 使用 --download-url 时必须指定 --arch (arm64 或 intel)")
        sys.exit(1)
    
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
    if app_dir:
        log_info(f"直接签名指定的 .app: {app_name}")
        log_info(f".app 路径: {app_dir}")
        log_info(f"架构: {arch}")
    else:
        log_info(f"从 GitHub Release 下载并签名 {app_name}")
        log_info(f"Release: {tag_name}")
        if download_url:
            log_info(f"直接下载URL: {download_url}")
            log_info(f"架构: {arch}")
        else:
            log_info(f"仓库: {repo_owner}/{repo_name}")
    log_info("=" * 50)
    print()
    
    # 如果提供了 --dir，直接使用指定的 .app 路径，跳过下载和解压
    if app_dir:
        app_path = Path(app_dir)
        if not app_path.exists():
            log_error(f"错误: .app 路径不存在: {app_path}")
            sys.exit(1)
        if not app_path.is_dir():
            log_error(f"错误: 指定的路径不是目录: {app_path}")
            sys.exit(1)
        if not app_path.name.endswith('.app'):
            log_error(f"错误: 指定的路径不是 .app 文件: {app_path}")
            sys.exit(1)
        
        # 直接开始签名流程
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
        else:
            # 直接调用签名函数
            sign_and_notarize_app_from_existing(app_path, client_type, arch, start_from_step)
            log_info(f"✓ {arch} 架构处理完成")
            print()
        
        log_info("=" * 50)
        log_info("✓ 所有架构处理完成")
        log_info("=" * 50)
        return
    
    # 如果提供了下载URL，跳过从GitHub获取assets的步骤
    if download_url:
        # 直接使用提供的URL和架构
        app_assets = {arch: download_url}
        log_info(f"使用指定的下载URL和架构: {arch}")
        log_info(f"下载URL: {download_url}")
    else:
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
    
    if not app_assets:
        if download_url:
            log_error("未找到 .app 文件（使用指定的下载URL）")
        else:
            log_error("未找到 .app 文件")
            if 'assets' in locals():
                log_error(f"可用的 assets: {[a.get('name', '') for a in assets]}")
        sys.exit(1)
    
    if download_url:
        log_info(f"✓ 使用指定的下载URL和架构: {list(app_assets.keys())}")
    else:
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
            target_app = output_dir / f"{app_name}.app"
            
            # 只检查 .app 是否已存在且完整（是目录）
            app_exists = target_app.exists() and target_app.is_dir()
            
            # 如果 .app 已存在，跳过下载和解压
            need_download = not app_exists
            
            if not should_skip_step(Step.DOWNLOAD, start_from_step):
                if need_download:
                    log_step(Step.DOWNLOAD, f"下载 {arch} .app ZIP 文件...")
                    if not download_file(url, download_path, api_key):
                        log_error(f"下载 {arch} .app 失败")
                        continue
                else:
                    log_info(f"[跳过] 下载步骤（.app 已存在且完整: {target_app}）")
            else:
                if not app_exists:
                    # 如果跳过了下载步骤，检查 .app 是否存在
                    if not download_path.exists():
                        log_error(f".app 不存在，且跳过了下载步骤。请先下载文件或使用 --start-from download")
                        continue
                    # 如果 ZIP 存在但 .app 不存在，需要解压
                    log_info(f"[跳过] 下载步骤（从步骤 {start_from_step.value} 开始）")
                else:
                    log_info(f"[跳过] 下载步骤（.app 已存在: {target_app}）")
            
            # 步骤：解压 ZIP 文件
            app_bundle = None
            
            if not should_skip_step(Step.EXTRACT, start_from_step):
                # 只检查 .app 是否已存在
                if target_app.exists() and target_app.is_dir():
                    # .app 已存在，跳过解压
                    log_info(f"[跳过] 解压步骤（.app 已存在且完整: {target_app}）")
                    app_bundle = target_app
                else:
                    # .app 不存在，需要解压
                    if not download_path.exists():
                        log_error(f"ZIP 文件不存在，无法解压。请先下载文件或使用 --start-from download")
                        continue
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
