#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户端打包脚本（Python 版本）
支持 Windows (exe) 和 macOS (dmg)
"""

import sys
import os

# 设置无缓冲输出，确保在 GitHub Actions 中能够实时看到日志
# 这对于长时间运行的脚本特别重要
try:
    if not sys.stdout.isatty():
        # 如果不是终端（如 GitHub Actions），设置行缓冲
        # 这样可以确保每行输出都立即刷新，而不是等待缓冲区满
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)  # 行缓冲
        sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)  # 行缓冲
except (OSError, AttributeError):
    # 如果无法设置（某些特殊环境），忽略错误
    pass
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
import time
import uuid
import signal
import threading

# Windows 编码修复：设置 UTF-8 编码
if sys.platform == "win32":
    # 设置标准输出和错误输出为 UTF-8
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
    # 设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# 颜色输出（Windows 兼容）
try:
    from colorama import init, Fore, Style
    # Windows 上初始化 colorama，设置 strip=False 以保留 ANSI 代码
    init(autoreset=False, strip=False)
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW
    RED = Fore.RED
    NC = Style.RESET_ALL
except ImportError:
    GREEN = YELLOW = RED = NC = ""

# 带时间戳的日志输出函数
def log_with_time(message, color=""):
    """带时间戳的日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{timestamp}] {message}{NC}", flush=True)  # flush=True 确保在 GitHub Actions 中实时显示

def log_info(message):
    """信息日志"""
    log_with_time(message, GREEN)

def log_warn(message):
    """警告日志"""
    log_with_time(message, YELLOW)

def log_error(message):
    """错误日志"""
    log_with_time(message, RED)

# 全局取消标志
_cancel_requested = threading.Event()

def signal_handler(signum, frame):
    """处理取消信号"""
    global _cancel_requested
    log_warn(f"收到取消信号 ({signum})，正在清理...")
    _cancel_requested.set()
    # 给子进程一些时间清理，然后退出
    time.sleep(2)
    sys.exit(130 if signum == signal.SIGINT else 143)

def check_cancel():
    """检查是否请求取消"""
    if _cancel_requested.is_set():
        raise KeyboardInterrupt("构建已取消")

def print_environment_diagnostics():
    """打印环境诊断信息，用于调试 PyInstaller 行为差异"""
    log_warn("========================================")
    log_warn("环境诊断信息")
    log_warn("========================================")
    
    # Python 版本
    log_info(f"Python 版本: {sys.version}")
    log_info(f"Python 可执行文件: {sys.executable}")
    
    # PyInstaller 版本
    try:
        import PyInstaller
        log_info(f"PyInstaller 版本: {PyInstaller.__version__}")
    except Exception as e:
        log_warn(f"无法获取 PyInstaller 版本: {e}")
    
    # 操作系统信息
    import platform
    log_info(f"操作系统: {platform.system()} {platform.release()}")
    log_info(f"系统版本: {platform.version()}")
    log_info(f"机器类型: {platform.machine()}")
    
    # 文件系统信息
    current_dir = os.getcwd()
    log_info(f"当前工作目录: {current_dir}")
    
    try:
        # 获取工作目录所在分区的文件系统信息
        df_result = subprocess.run(
            ["df", "-T", current_dir],
            capture_output=True,
            text=True,
            timeout=10
        )
        if df_result.returncode == 0:
            log_info(f"工作目录所在分区文件系统信息:\n{df_result.stdout}")
        
        # 获取详细的挂载信息（针对工作目录所在分区）
        mount_result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if mount_result.returncode == 0:
            # 找到工作目录所在分区的挂载信息
            df_lines = df_result.stdout.strip().split('\n')
            if len(df_lines) > 1:
                # 获取设备名（第一列）
                device = df_lines[1].split()[0]
                log_info(f"工作目录所在设备: {device}")
                # 查找该设备的挂载信息
                for line in mount_result.stdout.split('\n'):
                    if device in line:
                        log_info(f"工作目录分区挂载信息: {line}")
                        # 检查挂载选项
                        if 'read-only' in line or 'ro,' in line or ',ro' in line:
                            log_warn("  ⚠️ 工作目录所在分区是只读的！这可能导致 PyInstaller 无法创建符号链接")
                        break
    except Exception as e:
        log_warn(f"无法获取文件系统信息: {e}")
    
    # macOS 特定：检查 APFS 特性
    if platform.system() == "Darwin":
        try:
            # 检查文件系统是否区分大小写
            test_file = Path(current_dir) / ".fs_test_case_sensitive"
            try:
                test_file.write_text("test")
                if (Path(current_dir) / ".FS_TEST_CASE_SENSITIVE").exists():
                    log_info("文件系统: 不区分大小写")
                else:
                    log_info("文件系统: 区分大小写")
                test_file.unlink()
            except Exception as e:
                log_warn(f"无法测试文件系统大小写敏感性: {e}")
        except Exception as e:
            log_warn(f"无法获取 macOS 特定信息: {e}")
    
    # 环境变量
    log_info("相关环境变量:")
    env_vars = [
        "MACOSX_DEPLOYMENT_TARGET",
        "TMPDIR",
        "HOME",
        "PATH",
        "PYTHONPATH",
        "PYINSTALLER_LOG_LEVEL",
        "SKIP_SIGNING"
    ]
    for var in env_vars:
        value = os.environ.get(var, "(未设置)")
        log_info(f"  {var} = {value}")
    
    # 测试符号链接创建能力
    log_info("测试符号链接创建能力:")
    test_dir = Path(current_dir) / ".symlink_test"
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test_file.txt"
    try:
        test_file.write_text("test")
        test_link = test_dir / "test_link"
        test_link.symlink_to("test_file.txt")
        if test_link.is_symlink():
            log_info("  ✓ 可以创建符号链接")
            # 检查符号链接是否被解析
            if test_link.resolve() == test_file.resolve():
                log_info("  ✓ 符号链接可以正常解析")
            else:
                log_warn("  ⚠ 符号链接解析异常")
        test_link.unlink()
    except Exception as e:
        log_warn(f"  ✗ 无法创建符号链接: {e}")
    finally:
        try:
            if test_file.exists():
                test_file.unlink()
            if test_dir.exists():
                test_dir.rmdir()
        except:
            pass
    
    # 路径信息
    log_info(f"工作目录绝对路径: {Path(current_dir).resolve()}")
    log_info(f"工作目录路径长度: {len(str(Path(current_dir).resolve()))}")
    
    log_warn("========================================")
    print()  # 空行

def check_pyinstaller_output_structure(app_bundle: Path):
    """检查 PyInstaller 输出的文件结构"""
    log_warn("检查 PyInstaller 输出结构...")
    if not app_bundle.exists():
        log_warn("  应用包不存在，跳过结构检查")
        return
    
    frameworks_dir = app_bundle / "Contents" / "Frameworks"
    if frameworks_dir.exists():
        log_info("Frameworks 目录内容:")
        items_info = []
        for item in sorted(frameworks_dir.iterdir()):
            if item.is_symlink():
                items_info.append(f"  {item.name} -> {item.readlink()} (符号链接)")
            elif item.is_file():
                size = item.stat().st_size / 1024 / 1024  # MB
                items_info.append(f"  {item.name} ({size:.2f}MB) (真实文件)")
            elif item.is_dir():
                items_info.append(f"  {item.name}/ (目录)")
        
        for info in items_info[:30]:  # 只显示前30个
            log_info(info)
        if len(items_info) > 30:
            log_info(f"  ... 还有 {len(items_info) - 30} 个项目")
        
        # 检查 Qt 文件
        qt_files = [f for f in frameworks_dir.iterdir() 
                   if f.is_file() and f.name.startswith("Qt")]
        if qt_files:
            log_warn(f"发现 {len(qt_files)} 个 Qt 文件:")
            for qt_file in qt_files[:10]:  # 只显示前10个
                file_type = "符号链接" if qt_file.is_symlink() else "真实文件"
                size = qt_file.stat().st_size / 1024 / 1024 if qt_file.is_file() else 0
                log_info(f"  {qt_file.name}: {file_type} ({size:.2f}MB)")
            if len(qt_files) > 10:
                log_info(f"  ... 还有 {len(qt_files) - 10} 个 Qt 文件")
    
    # 检查 Resources 目录
    resources_dir = app_bundle / "Contents" / "Resources"
    if resources_dir.exists():
        resources_pyside6 = resources_dir / "PySide6"
        if resources_pyside6.exists():
            log_info("Resources/PySide6 目录:")
            qt_lib_in_resources = resources_pyside6 / "Qt" / "lib"
            if qt_lib_in_resources.exists():
                lib_size = sum(f.stat().st_size for f in qt_lib_in_resources.rglob('*') if f.is_file())
                log_warn(f"  Resources/PySide6/Qt/lib 存在: {lib_size / 1024 / 1024:.2f}MB")
            else:
                log_info("  Resources/PySide6/Qt/lib 不存在（正常）")
    
    print()  # 空行

def main():
    # 注册信号处理器，以便能够响应取消操作
    # Windows 只支持 SIGINT，Unix 系统支持 SIGINT 和 SIGTERM
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # 获取脚本所在目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    # 客户端类型
    client_type = sys.argv[1] if len(sys.argv) > 1 else "employee"
    
    if client_type not in ["employee", "admin"]:
        log_error("错误: 客户端类型必须是 'employee' 或 'admin'")
        print("用法: python3 build_client.py [employee|admin] [windows|macos]")
        sys.exit(1)
    
    # 平台检测
    platform = sys.argv[2] if len(sys.argv) > 2 else None
    if not platform:
        if sys.platform == "darwin":
            platform = "macos"
        elif sys.platform == "win32":
            platform = "windows"
        else:
            platform = "linux"
    
    # 客户端目录
    if client_type == "employee":
        client_dir = project_root / "ui_client"
        app_name = "Ai Perf Client"
        app_id = "site.sanying.aiperf.client"
    else:
        client_dir = project_root / "admin_ui_client"
        app_name = "Ai Perf Admin"
        app_id = "site.sanying.aiperf.admin"
    
    os.chdir(client_dir)
    
    start_time = time.time()
    log_info("========================================")
    log_info(f"打包 {app_name}")
    log_info(f"平台: {platform}")
    log_info("========================================")
    print()
    
    # 检查 PyInstaller
    try:
        import PyInstaller
    except ImportError:
        log_warn("安装 PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    
    # 清理之前的构建
    log_warn("清理之前的构建...")
    # 只清理 build 目录，保留 dist 目录（因为可能包含不同架构的构建）
    build_dir = Path("build")
    if build_dir.exists():
        shutil.rmtree(build_dir)
    
    # 清理 spec.bak 文件
    for spec_file in Path(".").glob("*.spec.bak"):
        spec_file.unlink()
    
    # 备份并修改 config.json（打包时使用）
    config_file = Path("config.json")
    config_backup = Path("config.json.bak")
    config_modified = False
    config_existed_before = config_file.exists()  # 记录原始文件是否存在
    
    # 从 spec 文件中读取版本号
    version = None
    # 优先从 build_macos.spec 读取（如果存在），因为版本号应该是统一的
    spec_files_to_try = []
    if platform == "macos":
        spec_files_to_try = [Path("build_macos.spec")]
    else:
        # Windows 和 Linux 也尝试从 build_macos.spec 读取（如果存在）
        spec_files_to_try = [Path("build_macos.spec"), Path("build.spec")]
    
    for spec_file in spec_files_to_try:
        if spec_file.exists():
            try:
                import re
                with open(spec_file, 'r', encoding='utf-8') as f:
                    spec_content = f.read()
                # 查找 version='...' 或 version="..."
                version_match = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", spec_content)
                if version_match:
                    version = version_match.group(1)
                    log_info(f"从 {spec_file} 读取版本号: {version}")
                    break
            except Exception as e:
                log_warn(f"无法从 {spec_file} 读取版本号: {e}")
    
    # 如果从 spec 文件读取失败，尝试从环境变量读取
    if not version:
        version = os.environ.get("CLIENT_VERSION")
        if version:
            log_info(f"从环境变量 CLIENT_VERSION 读取版本号: {version}")
    
    # 如果还是没有，使用默认值
    if not version:
        version = "1.0.1"  # 默认版本号
        log_warn(f"未找到版本号，使用默认值: {version}")
    
    # 如果 config.json 不存在，创建默认配置
    if not config_file.exists():
        log_warn("config.json 不存在，创建默认配置...")
        import json
        # 根据客户端类型创建不同的默认配置
        if client_type == "admin":
            default_config = {
                "api_base": "http://127.0.0.1:8880",
                "google_id_token": "",
                "session_token": "",
                "user_id": "",
                "user_name": "",
                "user_email": "",
                "theme": "auto",
                "auto_refresh": True,
                "notifications": True,
                "client_version": version,
                "update_dialog_dismissed_date": "",
                "ssh_host": "",
                "ssh_port": 22,
                "ssh_username": "",
                "ssh_password": "",
                "ssh_key_path": "",
                "upload_api_url": "http://127.0.0.1:8882/api/upload",
                "openai_session_key": "",
            }
        else:  # employee
            default_config = {
                "api_base": "http://127.0.0.1:8000",
                "google_id_token": "",
                "session_token": "",
                "user_id": "",
                "user_name": "",
                "user_email": "",
                "theme": "auto",
                "auto_refresh": True,
                "notifications": True,
                "client_version": version,
                "update_dialog_dismissed_date": "",
            }
        
        # 创建默认配置文件
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False, sort_keys=True)
        log_info("✓ 已创建默认 config.json")
    
    # 检查并创建 google_client_secret.json（如果不存在）
    google_secret_file = Path("google_client_secret.json")
    google_secret_backup = Path("google_client_secret.json.bak")
    google_secret_modified = False
    google_secret_existed_before = google_secret_file.exists()
    google_secret_from_env = None  # 标记是否从环境变量创建
    
    # 优先从环境变量读取 Google OAuth 凭据（用于 CI/CD）
    google_secret_env_value = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")
    
    if google_secret_env_value:
        # 从环境变量读取（Base64 编码或直接 JSON 字符串）
        log_warn("从环境变量读取 Google OAuth 凭据...")
        try:
            import base64
            # 尝试 Base64 解码
            try:
                decoded = base64.b64decode(google_secret_env_value).decode('utf-8')
                google_secret_data = json.loads(decoded)
            except:
                # 如果不是 Base64，尝试直接解析 JSON
                google_secret_data = json.loads(google_secret_env_value)
            
            # 如果文件存在，先备份
            if google_secret_file.exists():
                shutil.copy2(google_secret_file, google_secret_backup)
            
            # 写入从环境变量读取的凭据
            with open(google_secret_file, 'w', encoding='utf-8') as f:
                json.dump(google_secret_data, f, indent=2, ensure_ascii=False)
            log_info("✓ 已从环境变量创建 google_client_secret.json（包含真实凭据）")
            google_secret_modified = True
            google_secret_from_env = True  # 标记为从环境变量创建
        except Exception as e:
            log_warn(f"从环境变量读取 Google OAuth 凭据失败: {e}，将使用占位文件")
            google_secret_from_env = False
    
    if not google_secret_from_env:
        # 如果没有从环境变量读取，使用本地文件或创建占位文件
        if not google_secret_file.exists():
            log_warn("google_client_secret.json 不存在，创建占位文件...")
            # 创建一个空的占位 JSON 文件（用于打包，实际使用时需要用户配置）
            placeholder_google_secret = {
                "installed": {
                    "client_id": "",
                    "project_id": "",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_secret": "",
                    "redirect_uris": ["http://localhost"]
                }
            }
            with open(google_secret_file, 'w', encoding='utf-8') as f:
                json.dump(placeholder_google_secret, f, indent=2, ensure_ascii=False)
            log_info("✓ 已创建占位 google_client_secret.json（打包用，实际使用时需要用户配置）")
            google_secret_modified = True
        else:
            # 如果文件存在，备份它（打包时使用原文件，不替换）
            log_warn("使用现有的 google_client_secret.json...")
            # 不备份，直接使用现有文件
            google_secret_modified = False  # 文件已存在，不需要恢复
    
    if config_file.exists():
        log_warn("备份并修改 config.json...")
        # 备份原始文件
        shutil.copy2(config_file, config_backup)
        config_modified = True
        
        # 读取并修改配置
        import json
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 清空登录信息和隐私数据
        privacy_fields = [
            # 登录信息
            "session_token",
            "google_id_token",
            "user_id",
            "user_name",
            "user_email",
            # Jira 相关
            "jira_token",
            "jira_account_email",
            "jira_base",
            # Figma 相关
            "figma_api_key",
            # GitHub 相关
            "github_api_key",
            "packaging_github_api_key",
            # AI 相关
            "openai_session_key",
            # SSH 相关
            "ssh_key_path",
            "ssh_username",
            "ssh_password",
            "ssh_host",
            "ssh_port",
        ]
        for field in privacy_fields:
            if field in config:
                config[field] = ""
        
        # 清空 update_dialog_dismissed_date
        if "update_dialog_dismissed_date" in config:
            config["update_dialog_dismissed_date"] = ""
        
        # 修改 api_base
        config["api_base"] = "https://api-perf.sanying.site"
        
        # 设置 upload_api_url（管理端必须设置）
        if client_type == "admin":
            config["upload_api_url"] = "https://file.sanying.site/api/upload"
        elif "upload_api_url" in config:
            # 员工端如果存在这个字段，也更新它
            config["upload_api_url"] = "https://file.sanying.site/api/upload"
        
        # 更新版本号（如果从 spec 文件中读取到了版本号）
        if version:
            config["client_version"] = version
            log_info(f"✓ 更新 client_version 为: {version}")
        
        # 保存修改后的配置（确保对齐）
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False, sort_keys=True)
        
        version_info = f"，版本号: {version}" if version else ""
        log_info(f"✓ config.json 已修改（清空隐私数据，设置 api_base 和 upload_api_url{version_info}，已对齐）")
    
    if platform == "macos":
        # macOS 打包
        log_warn("开始 macOS 打包...")
        
        # 设置部署目标，确保向后兼容性（支持 macOS 10.13+）
        # 问题分析：
        # - 之前没有在编译 Python 时设置 MACOSX_DEPLOYMENT_TARGET，Python 使用了默认值（14.7）
        # - 导致 Python 库链接了 macOS 13+ 的符号（如 _mkfifoat）
        # - _mkfifoat 符号在 macOS 13.0+ 才可用，在 macOS 12.7.6 上会找不到
        # 解决方案：
        # - 在编译 Python 时设置 MACOSX_DEPLOYMENT_TARGET=10.13
        # - 编译器会检查符号可用性，避免使用 macOS 13+ 的符号
        # - 这样既能支持 macOS 10.13+，又能确保在 macOS 12.7.6 上运行
        deployment_target = "10.13"
        log_info(f"设置 macOS 部署目标: {deployment_target}")
        os.environ["MACOSX_DEPLOYMENT_TARGET"] = deployment_target
        
        # 设置 SDK 路径（如果可用）
        try:
            sdk_result = subprocess.run(
                ["xcrun", "--show-sdk-path"],
                capture_output=True,
                text=True,
                check=False
            )
            if sdk_result.returncode == 0:
                sdk_path = sdk_result.stdout.strip()
                os.environ["SDKROOT"] = sdk_path
                log_info(f"SDK 路径: {sdk_path}")
        except Exception as e:
            log_warn(f"无法获取 SDK 路径: {e}")
        
        # 设置编译器标志，确保使用兼容的 API
        # 这些环境变量会影响所有编译过程，包括 Python 扩展模块
        if "CFLAGS" not in os.environ:
            os.environ["CFLAGS"] = f"-mmacosx-version-min={deployment_target}"
        else:
            os.environ["CFLAGS"] += f" -mmacosx-version-min={deployment_target}"
        
        if "LDFLAGS" not in os.environ:
            os.environ["LDFLAGS"] = f"-mmacosx-version-min={deployment_target}"
        else:
            os.environ["LDFLAGS"] += f" -mmacosx-version-min={deployment_target}"
        
        log_info("✓ 部署目标环境变量已设置")
        
        # 检测芯片类型并设置输出目录
        try:
            # 使用 uname -m 检测芯片架构
            uname_result = subprocess.run(
                ["uname", "-m"],
                capture_output=True,
                text=True,
                check=True
            )
            arch = uname_result.stdout.strip()
            
            # 检测 Python 解释器的架构
            python_arch_result = subprocess.run(
                [sys.executable, "-c", "import platform; print(platform.machine())"],
                capture_output=True,
                text=True,
                check=True
            )
            python_arch = python_arch_result.stdout.strip()
            
            if arch == "arm64":
                # Apple Silicon (M1, M2, M3 等)
                dist_subdir = "m"
                chip_name = "Apple Silicon (M系列)"
                target_arch = "arm64"
                
                # 检查 Python 架构
                if python_arch != "arm64":
                    log_warn(f"⚠ 警告: 系统是 {arch}，但 Python 解释器是 {python_arch}")
                    log_warn("  这会导致打包出来的应用是 x86_64 架构，需要 Rosetta")
                    log_warn("  建议使用 arm64 版本的 Python 解释器")
                    log_warn("  或者使用 arch -arm64 python3 来运行打包脚本")
            else:
                # Intel
                dist_subdir = "intel"
                chip_name = "Intel"
                target_arch = "x86_64"
            
            log_info(f"检测到芯片类型: {chip_name} ({arch})")
            log_info(f"Python 解释器架构: {python_arch}")
            log_info(f"目标架构: {target_arch}")
            dist_dir = Path("dist") / dist_subdir
            dist_dir.mkdir(parents=True, exist_ok=True)
            log_info(f"输出目录: {dist_dir}")
        except Exception as e:
            log_warn(f"无法检测芯片类型: {e}，使用默认目录 dist")
            dist_dir = Path("dist")
            target_arch = None
            arch = None
            python_arch = None
        
        spec_file = "build_macos.spec"
        if not Path(spec_file).exists():
            log_error(f"错误: 找不到 {spec_file}")
            sys.exit(1)
        
        # 动态修改 spec 文件，设置正确的 target_arch
        spec_backup = None
        if target_arch:
            log_warn(f"修改 spec 文件，设置 target_arch={target_arch}...")
            spec_backup = Path(f"{spec_file}.bak")
            shutil.copy2(spec_file, spec_backup)
            
            # 读取 spec 文件
            with open(spec_file, 'r', encoding='utf-8') as f:
                spec_content = f.read()
            
            # 替换 target_arch
            import re
            # 匹配 target_arch=None 或 target_arch='...' 或 target_arch="..."
            spec_content = re.sub(
                r'target_arch\s*=\s*(None|["\'].*?["\'])',
                f'target_arch="{target_arch}"',
                spec_content
            )
            
            # 如果 spec 文件中没有 target_arch，在 EXE 部分添加
            if 'target_arch=' not in spec_content:
                # 在 EXE( 部分添加 target_arch
                spec_content = re.sub(
                    r'(exe\s*=\s*EXE\()',
                    rf'\1\n    target_arch="{target_arch}",',
                    spec_content
                )
            
            # 写回 spec 文件
            with open(spec_file, 'w', encoding='utf-8') as f:
                f.write(spec_content)
            
            log_info(f"✓ spec 文件已更新，target_arch={target_arch}")
        
        # 打印环境诊断信息（用于调试）
        print_environment_diagnostics()
        
        # 打包
        log_warn("执行 PyInstaller 打包...")
        # 确保 PyInstaller 使用部署目标环境变量
        # 注意：只传递 MACOSX_DEPLOYMENT_TARGET，不传递 CFLAGS/LDFLAGS
        # 避免干扰 PyInstaller 的标准库收集过程
        pyinstaller_env = os.environ.copy()
        pyinstaller_env["MACOSX_DEPLOYMENT_TARGET"] = deployment_target
        # 移除可能干扰的编译标志（PyInstaller 需要自己管理这些）
        pyinstaller_env.pop("CFLAGS", None)
        pyinstaller_env.pop("LDFLAGS", None)
        pyinstaller_env.pop("CPPFLAGS", None)
        pyinstaller_env.pop("CC", None)
        pyinstaller_env.pop("CXX", None)
        
        # 获取日志级别（默认为 INFO，可以通过环境变量覆盖）
        log_level = os.environ.get("PYINSTALLER_LOG_LEVEL", "INFO")
        
        try:
            check_cancel()  # 检查是否请求取消
            # 使用实时输出而不是 capture_output，以便能够看到进度并响应取消
            log_info("开始 PyInstaller 打包（实时输出）...")
            result = subprocess.run([
                sys.executable, "-m", "PyInstaller",
                spec_file,
                "--clean",
                "--noconfirm",
                "--collect-all", "encodings",  # 强制收集所有 encodings 模块（修复 ModuleNotFoundError）
                f"--log-level={log_level}"
            ], check=True, env=pyinstaller_env, timeout=3600)  # 1小时超时，实时输出
            result = subprocess.CompletedProcess([], 0, "", "")  # 创建成功结果对象
        except subprocess.TimeoutExpired:
            log_error("PyInstaller 执行超时（超过1小时）")
            raise
        except KeyboardInterrupt:
            log_warn("PyInstaller 打包被用户取消")
            raise
        except subprocess.CalledProcessError as e:
            log_error(f"PyInstaller 执行失败，退出码: {e.returncode}")
            if e.stdout:
                log_error("PyInstaller 标准输出:")
                print(e.stdout)
            if e.stderr:
                log_error("PyInstaller 标准错误:")
                print(e.stderr)
            # 错误信息已经在实时输出中显示，直接抛出异常
            raise
        
        log_info("✓ PyInstaller 打包完成，开始后续处理...")
        print()  # 空行分隔
        
        # 恢复 spec 文件（如果修改过）
        if spec_backup and spec_backup.exists():
            log_warn("恢复 spec 文件...")
            shutil.move(spec_backup, spec_file)
            log_info("✓ spec 文件已恢复")
        
        # 如果 PyInstaller 输出到了 dist，需要移动到对应的子目录
        log_warn("检查应用包位置...")
        temp_app_bundle = Path("dist") / f"{app_name}.app"
        app_bundle = dist_dir / f"{app_name}.app"
        log_info(f"  临时应用包路径: {temp_app_bundle}")
        log_info(f"  目标应用包路径: {app_bundle}")
        
        if temp_app_bundle.exists() and temp_app_bundle != app_bundle:
            log_warn(f"移动应用包到 {dist_dir}...")
            if app_bundle.exists():
                log_info(f"  删除已存在的应用包: {app_bundle}")
                shutil.rmtree(app_bundle)
            log_info(f"  移动 {temp_app_bundle} -> {app_bundle}")
            shutil.move(str(temp_app_bundle), str(app_bundle))
            log_info("✓ 应用包移动完成")
        elif app_bundle.exists():
            log_info(f"✓ 应用包已在目标位置: {app_bundle}")
        else:
            log_warn(f"  临时应用包存在: {temp_app_bundle.exists()}")
            log_warn(f"  目标应用包存在: {app_bundle.exists()}")
        
        if not app_bundle.exists():
            log_error("错误: 应用包未生成")
            log_error(f"  检查路径: {app_bundle}")
            log_error(f"  临时路径: {temp_app_bundle}")
            sys.exit(1)
        
        log_info(f"✓ 应用包生成成功: {app_bundle}")
        print()  # 空行分隔
        
        # 检查 PyInstaller 输出的文件结构（用于调试）
        check_pyinstaller_output_structure(app_bundle)
        
        # 后处理：清理 Frameworks 目录下的非二进制文件和目录
        # PyInstaller 的 BUNDLE 阶段在不同环境下行为可能不同：
        # - 本地打包：Frameworks/resources 可能是符号链接（指向 ../Resources/resources），这是正常的
        # - GitHub Actions 打包：Frameworks/resources 可能是真实目录，这会导致签名失败
        # 需要删除真实目录，但保留符号链接
        frameworks_dir = app_bundle / "Contents" / "Frameworks"
        if frameworks_dir.exists():
            # 检查 Frameworks/resources 是否是真实目录（需要清理）
            resources_in_frameworks = frameworks_dir / "resources"
            needs_cleanup = False
            
            if resources_in_frameworks.exists():
                # 检查是否是符号链接
                is_symlink = resources_in_frameworks.is_symlink()
                if is_symlink:
                    log_info(f"  Frameworks/resources 是符号链接，无需清理: {resources_in_frameworks.relative_to(app_bundle)}")
                else:
                    # 是真实目录，需要清理
                    needs_cleanup = True
                    log_warn(f"  发现 Frameworks 目录下的 resources 真实目录（PyInstaller 打包问题），需要清理")
            
            # 只在需要清理时执行清理操作
            if needs_cleanup:
                log_warn("后处理：清理 Frameworks 目录结构...")
                # 先收集要处理的项，避免在迭代时修改目录
                items_to_check = list(frameworks_dir.iterdir())
                
                # 处理 Frameworks 下的 resources
                if resources_in_frameworks.exists() and not resources_in_frameworks.is_symlink():
                    log_warn(f"  删除 Frameworks/resources 真实目录: {resources_in_frameworks.relative_to(app_bundle)}")
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
                        log_warn(f"  移除非框架目录: {item.relative_to(app_bundle)}")
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
                            log_warn(f"  移除非二进制文件: {item.relative_to(app_bundle)}")
                            try:
                                item.unlink()
                                log_info(f"    ✓ 已移除: {item.name}")
                            except Exception as e:
                                log_warn(f"    移除失败: {e}")
                
                # 验证清理后应用包仍然存在
                if not app_bundle.exists():
                    log_error(f"错误: 清理后应用包不存在: {app_bundle}")
                    log_error(f"  当前工作目录: {os.getcwd()}")
                    log_error(f"  绝对路径: {app_bundle.resolve()}")
                    sys.exit(1)
        
        # 验证应用包的架构
        if arch and target_arch:
            log_warn("验证应用包架构...")
            main_executable = app_bundle / "Contents" / "MacOS" / app_name
            if main_executable.exists():
                try:
                    # 使用 file 命令检查架构
                    file_result = subprocess.run(
                        ["file", str(main_executable)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    log_info(f"  可执行文件架构信息: {file_result.stdout.strip()}")
                    
                    # 使用 lipo 检查架构（如果可用）
                    lipo_result = subprocess.run(
                        ["lipo", "-info", str(main_executable)],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if lipo_result.returncode == 0:
                        log_info(f"  架构详情: {lipo_result.stdout.strip()}")
                        if arch == "arm64" and "arm64" not in lipo_result.stdout:
                            log_error(f"  ✗ 警告: 应用包不是 arm64 架构！")
                            log_error(f"  预期: arm64，实际: {lipo_result.stdout.strip()}")
                            log_error(f"  这会导致在 M 芯片 Mac 上需要 Rosetta")
                            log_error(f"  请确保使用 arm64 版本的 Python 解释器")
                            log_error(f"  或使用 'arch -arm64 python3' 运行打包脚本")
                        elif arch == "arm64" and "arm64" in lipo_result.stdout:
                            log_info(f"  ✓ 应用包架构正确: arm64")
                        elif arch == "x86_64" and "x86_64" in lipo_result.stdout:
                            log_info(f"  ✓ 应用包架构正确: x86_64")
                except Exception as e:
                    log_warn(f"  无法验证架构: {e}")
        
        # 代码签名（使用配置的凭据）
        # 如果设置了 SKIP_SIGNING 环境变量，则跳过签名和公证（用于 CI/CD 环境）
        skip_signing = os.environ.get("SKIP_SIGNING", "").lower() in ("true", "1", "yes")
        
        if skip_signing:
            log_info("=" * 50)
            log_warn("跳过代码签名和公证（SKIP_SIGNING=true）")
            log_info("  仅打包 .app，不进行签名和公证")
            codesign_identity = None
            installer_identity = None
            apple_id = None
            team_id = None
            notary_password = None
            application_p12_path = None
            installer_p12_path = None
        else:
            codesign_identity = os.environ.get("CODESIGN_IDENTITY", "Developer ID Application: wei liu (U5SLTWD6AH)")
            installer_identity = os.environ.get("INSTALLER_CODESIGN_IDENTITY", None)
            apple_id = os.environ.get("APPLE_ID", "ruier09@qq.com")
            team_id = os.environ.get("TEAM_ID", "U5SLTWD6AH")
            notary_password = os.environ.get("NOTARY_PASSWORD", "qhiz-rnwg-fhtz-tude")
        
        # 支持从 p12 文件导入证书（包含证书和私钥）
        # APPLICATION_P12_PATH: Developer ID Application 证书 p12 文件路径（用于 DMG）
        # APPLICATION_P12_PASSWORD: Application p12 文件密码（可选）
        # INSTALLER_P12_PATH: Developer ID Installer 证书 p12 文件路径（用于 PKG）
        # INSTALLER_P12_PASSWORD: Installer p12 文件密码（可选）
        # 默认路径：项目根目录下的 apple-p12 目录
        # 注意：project_root 已经在 main() 函数开头定义
        if not skip_signing:
            default_application_p12 = project_root / "apple-p12" / "developerID_application.p12"
            default_installer_p12 = project_root / "apple-p12" / "developerID_installer.p12"
            
            application_p12_path = os.environ.get("APPLICATION_P12_PATH", None)
            if not application_p12_path and default_application_p12.exists():
                application_p12_path = str(default_application_p12)
                log_info(f"使用默认 Application p12 证书: {application_p12_path}")
            
            application_p12_password = os.environ.get("APPLICATION_P12_PASSWORD", "123456")
            
            installer_p12_path = os.environ.get("INSTALLER_P12_PATH", None)
            if not installer_p12_path and default_installer_p12.exists():
                installer_p12_path = str(default_installer_p12)
                log_info(f"使用默认 Installer p12 证书: {installer_p12_path}")
            
            installer_p12_password = os.environ.get("INSTALLER_P12_PASSWORD", "123456")
        else:
            application_p12_path = None
            application_p12_password = None
            installer_p12_path = None
            installer_p12_password = None
        
        # 导入 Application p12 证书（如果提供了 p12 文件路径）
        if application_p12_path and Path(application_p12_path).exists():
            log_warn(f"导入 Application p12 证书: {application_p12_path}")
            try:
                # 构建 security import 命令
                import_cmd = [
                    "security", "import", application_p12_path,
                    "-k", os.path.expanduser("~/Library/Keychains/login.keychain-db"),
                    "-T", "/usr/bin/codesign",
                    "-T", "/usr/bin/productsign",
                    "-P", application_p12_password if application_p12_password else ""
                ]
                
                import_result = subprocess.run(
                    import_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    input=application_p12_password if application_p12_password else None
                )
                
                if import_result.returncode == 0:
                    log_info("  ✓ Application p12 证书导入成功")
                    # 如果未设置 CODESIGN_IDENTITY，尝试从证书中提取
                    if not codesign_identity or codesign_identity.startswith("Developer ID Application: wei liu"):
                        # 查找证书名称
                        find_result = subprocess.run([
                            "security", "find-identity", "-v", "-p", "codesigning"
                        ], capture_output=True, text=True, check=False)
                        
                        if find_result.returncode == 0:
                            for line in find_result.stdout.split('\n'):
                                if "Developer ID Application" in line:
                                    import re
                                    match = re.search(r'"([^"]+)"', line)
                                    if match:
                                        codesign_identity = match.group(1)
                                        log_info(f"  ✓ 找到 Application 证书: {codesign_identity}")
                                        break
                else:
                    error_msg = import_result.stderr or import_result.stdout or ""
                    log_warn(f"  ⚠ Application p12 证书导入失败: {error_msg[:200]}")
            except Exception as e:
                log_warn(f"  ⚠ 导入 Application p12 证书时出错: {e}")
        
        # 导入 Installer p12 证书（如果提供了 p12 文件路径）
        if installer_p12_path and Path(installer_p12_path).exists():
            log_warn(f"导入 Installer p12 证书: {installer_p12_path}")
            try:
                # 构建 security import 命令
                import_cmd = [
                    "security", "import", installer_p12_path,
                    "-k", os.path.expanduser("~/Library/Keychains/login.keychain-db"),
                    "-T", "/usr/bin/productsign",
                    "-P", installer_p12_password if installer_p12_password else ""
                ]
                
                import_result = subprocess.run(
                    import_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    input=installer_p12_password if installer_p12_password else None
                )
                
                if import_result.returncode == 0:
                    log_info("  ✓ Installer p12 证书导入成功")
                    # 如果未设置 INSTALLER_CODESIGN_IDENTITY，尝试从证书中提取
                    if not installer_identity:
                        # 查找证书名称
                        find_result = subprocess.run([
                            "security", "find-identity", "-v", "-p", "codesigning"
                        ], capture_output=True, text=True, check=False)
                        
                        if find_result.returncode == 0:
                            for line in find_result.stdout.split('\n'):
                                if "Developer ID Installer" in line:
                                    import re
                                    match = re.search(r'"([^"]+)"', line)
                                    if match:
                                        installer_identity = match.group(1)
                                        log_info(f"  ✓ 找到 Installer 证书: {installer_identity}")
                                        break
                else:
                    error_msg = import_result.stderr or import_result.stdout or ""
                    log_warn(f"  ⚠ Installer p12 证书导入失败: {error_msg[:200]}")
            except Exception as e:
                log_warn(f"  ⚠ 导入 Installer p12 证书时出错: {e}")
        
        if codesign_identity:
            log_info("=" * 50)
            log_warn("开始代码签名（使用改进的签名流程）...")
            log_info(f"  签名身份: {codesign_identity}")
            
            # 强制检查时间戳服务器连接，失败则终止构建
            log_info("  强制检查时间戳服务器连接（timestamp.apple.com:80）...")
            apple_timestamp_available = False
            
            try:
                import socket
                
                # 只测试 Apple 时间戳服务器，重试3次
                host = "timestamp.apple.com"
                port = 80
                max_retries = 3
                retry_delay = 2
                
                for attempt in range(1, max_retries + 1):
                    try:
                        sock = socket.create_connection((host, port), timeout=5)
                        sock.close()
                        apple_timestamp_available = True
                        log_info(f"  ✓ 时间戳服务器 {host}:{port} 可访问")
                        break
                    except (socket.timeout, socket.error, OSError) as e:
                        if attempt < max_retries:
                            log_warn(f"  ⚠ 时间戳服务器 {host}:{port} 不可访问（尝试 {attempt}/{max_retries}），{retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            retry_delay *= 2  # 指数退避
                        else:
                            log_error(f"  ✗ 时间戳服务器 {host}:{port} 不可访问（尝试 {attempt}/{max_retries}）")
                
                # 如果检查失败，立即终止构建
                if not apple_timestamp_available:
                    log_error("  ✗ 错误: Apple 时间戳服务器不可访问（已重试所有次数）")
                    log_error("     无法继续签名，构建终止")
                    log_error("     请检查网络连接或 DNS 配置")
                    raise Exception("Apple 时间戳服务器不可访问，无法继续签名")
            except Exception as e:
                # 如果检查过程本身出错，也终止构建
                if "Apple 时间戳服务器不可访问" in str(e):
                    raise  # 重新抛出我们的异常
                log_error(f"  ✗ 无法测试时间戳服务器连接: {e}")
                log_error("     构建终止")
                raise Exception(f"无法测试时间戳服务器连接: {e}")
            
            print()  # 空行分隔
            
            # 创建基本的 entitlements 文件（如果需要）
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
            
            # 签名 Resources 目录中的二进制文件（如果有）
            resources_dir = app_bundle / "Contents" / "Resources"
            if resources_dir.exists():
                log_info("  签名 Resources 目录中的二进制文件...")
                for item in resources_dir.rglob("*"):
                    if item.is_file():
                        # 跳过资源文件
                        if item.suffix in [".plist", ".qm", ".png", ".json", ".icns", ".txt", ".md"]:
                            continue
                        # 检查是否是 Mach-O 二进制文件
                        try:
                            result = subprocess.run(
                                ["file", "-b", "--mime-type", str(item)],
                                capture_output=True,
                                text=True,
                                check=True,
                                timeout=30  # 大型文件可能需要更长时间
                            )
                            if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                log_info(f"    签名: {item.relative_to(app_bundle)}")
                                subprocess.run([
                                    "codesign", "--force", "--sign", codesign_identity,
                                    "--options", "runtime",
                                    "--timestamp",
                                    str(item)
                                ], check=False, capture_output=True)
                        except Exception:
                            pass
            
            frameworks_dir = app_bundle / "Contents" / "Frameworks"
            if frameworks_dir.exists():
                # 第一步：签名所有独立的 .dylib 文件和无扩展名的 Mach-O 文件（不包括框架内的）
                log_info("  签名独立的 .dylib 文件和无扩展名 Mach-O 文件...")
                dylib_files = [f for f in frameworks_dir.rglob("*.dylib") 
                              if ".framework" not in str(f)]
                for dylib in dylib_files:
                    log_info(f"    签名: {dylib.relative_to(app_bundle)}")
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",  # 使用时间戳，对公证很重要
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
                                timeout=30  # 大型文件可能需要更长时间
                            )
                            if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                log_info(f"    签名: {item.relative_to(app_bundle)}")
                                # 使用 --preserve-metadata 和 --deep 确保签名完整
                                subprocess.run([
                                    "codesign", "--force", "--sign", codesign_identity,
                                    "--options", "runtime",
                                    "--timestamp",  # 使用时间戳
                                    "--preserve-metadata=entitlements,requirements,flags",
                                    str(item)
                                ], check=False, capture_output=True)
                                # 验证签名
                                verify_result = subprocess.run(
                                    ["codesign", "-vvv", str(item)],
                                    capture_output=True,
                                    text=True,
                                    timeout=60  # 大型文件（如 QtWebEngineCore）验证可能需要更长时间
                                )
                                if verify_result.returncode != 0:
                                    log_warn(f"      警告: {item.name} 签名验证失败，尝试重新签名...")
                                    # 如果验证失败，尝试使用 --deep 重新签名
                                    subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",  # 使用时间戳
                                        str(item)
                                    ], check=False, capture_output=True)
                        except Exception as e:
                            log_warn(f"      签名 {item.name} 时出错: {e}")
                
                # 第二步：签名 Qt 框架（.framework 目录）
                # 必须先签名框架内的所有文件，再签名整个框架
                qt_dir = frameworks_dir / "PySide6" / "Qt"
                if qt_dir.exists():
                    log_info("  签名 Qt 框架...")
                    # 查找所有 .framework 目录
                    framework_dirs = [d for d in qt_dir.rglob("*.framework") if d.is_dir()]
                    for framework_dir in framework_dirs:
                        framework_name = framework_dir.name
                        log_info(f"    签名框架: {framework_dir.relative_to(app_bundle)}")
                        
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
                                        timeout=30  # 大型文件可能需要更长时间
                                    )
                                    if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                        subprocess.run([
                                            "codesign", "--force", "--sign", codesign_identity,
                                            "--options", "runtime",
                                            "--timestamp",  # 使用时间戳
                                            str(item)
                                        ], check=False, capture_output=True)
                                except Exception:
                                    pass
                        
                        # 然后签名整个框架目录
                        subprocess.run([
                            "codesign", "--force", "--sign", codesign_identity,
                            "--options", "runtime",
                            "--timestamp",  # 使用时间戳
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
                                    timeout=30  # 大型文件可能需要更长时间
                                )
                                if "application/x-mach-binary" in result.stdout or "application/x-executable" in result.stdout:
                                    log_info(f"    签名: {qt_lib.relative_to(app_bundle)}")
                                    subprocess.run([
                                        "codesign", "--force", "--sign", codesign_identity,
                                        "--options", "runtime",
                                        "--timestamp",  # 使用时间戳
                                        str(qt_lib)
                                    ], check=False, capture_output=True)
                            except Exception:
                                pass
                
                # 第三步：签名所有 .so 文件（它们依赖已签名的框架）
                log_info("  签名 .so 文件...")
                so_files = list(frameworks_dir.rglob("*.so"))
                for so_file in so_files:
                    log_info(f"    签名: {so_file.relative_to(app_bundle)}")
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",  # 使用时间戳
                        str(so_file)
                    ], check=False, capture_output=True)
            
            # 最后签名整个应用包
            # 注意：使用 --deep 会重新签名所有内容，可能会破坏之前的签名
            # 所以先验证并修复所有关键文件的签名
            log_warn("验证并修复关键文件签名...")
            # 查找所有无扩展名的 Qt 文件
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
                    log_warn(f"  重新签名: {qt_file.relative_to(app_bundle)}")
                    subprocess.run([
                        "codesign", "--force", "--sign", codesign_identity,
                        "--options", "runtime",
                        "--timestamp",  # 使用时间戳
                        str(qt_file)
                    ], check=False, capture_output=True)
            
            log_warn("签名应用包主可执行文件...")
            # 先签名主可执行文件
            main_executable = app_bundle / "Contents" / "MacOS" / app_name
            if main_executable.exists():
                subprocess.run([
                    "codesign", "--force", "--sign", codesign_identity,
                    "--options", "runtime",
                    "--timestamp",
                    str(main_executable)
                ], check=True)
                log_info("✓ 主可执行文件已签名")
            
            log_warn("签名应用包（不使用 --deep，避免重新签名）...")
            # 不使用 --deep，因为我们已经手动签名了所有组件
            # 使用 --strict 进行更严格的验证
            
            # 验证应用包存在（避免路径问题）
            if not app_bundle.exists():
                log_error(f"错误: 应用包不存在: {app_bundle}")
                log_error(f"  当前工作目录: {os.getcwd()}")
                try:
                    log_error(f"  绝对路径: {app_bundle.resolve()}")
                except Exception as e:
                    log_error(f"  无法解析绝对路径: {e}")
                # 检查 dist 目录是否存在
                dist_dir_check = Path("dist")
                if dist_dir_check.exists():
                    log_error(f"  dist 目录存在，内容: {list(dist_dir_check.iterdir())}")
                else:
                    log_error(f"  dist 目录不存在")
                sys.exit(1)
            
            codesign_cmd = [
                "codesign", "--force", "--sign", codesign_identity,
                "--options", "runtime",
                "--timestamp",
                "--strict",
                "--verify",
                str(app_bundle)
            ]
            subprocess.run(codesign_cmd, check=True)
            
            # 签名后，再次验证并修复关键文件（因为 --deep 可能会破坏签名）
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
                            log_warn(f"    发现签名无效: {item.relative_to(app_bundle)}，重新签名...")
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
                        log_error(f"    检查或重新签名 {item.relative_to(app_bundle)} 失败: {e}")

            if re_sign_needed:
                log_warn("关键文件已修复，重新签名应用包以包含修复...")
                codesign_cmd = [
                    "codesign", "--force", "--verify", "--verbose",
                    "--sign", codesign_identity,
                    "--options", "runtime",
                    "--timestamp",
                    "--strict",
                    str(app_bundle)
                ]
                subprocess.run(codesign_cmd, check=True)
                log_info("✓ 应用包已重新签名以包含修复")
            
            # 验证签名（不使用 --deep，因为已弃用）
            log_warn("验证签名...")
            verify_result = subprocess.run([
                "codesign", "--verify", "--verbose", "--strict",
                str(app_bundle)
            ], capture_output=True, text=True, check=False)
            
            if verify_result.returncode != 0:
                log_error(f"签名验证失败: {verify_result.stderr}")
                # 尝试使用 spctl 进行额外验证
                spctl_result = subprocess.run([
                    "spctl", "--assess", "--verbose", "--type", "execute",
                    str(app_bundle)
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
                str(app_bundle)
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
            log_warn("⚠ 跳过代码签名（设置 CODESIGN_IDENTITY 环境变量以启用）")
        
        # 如果设置了 SKIP_SIGNING，则跳过 DMG 和 PKG 的创建（仅打包 .app）
        if skip_signing:
            log_info("=" * 50)
            log_warn("跳过 DMG 和 PKG 创建（SKIP_SIGNING=true）")
            log_info(f"✓ 仅打包 .app 完成: {app_bundle}")
            log_info("  可以在本机下载后进行签名和公证")
            return
        
        log_info("=" * 50)
        log_warn("开始创建 DMG...")
        print()  # 空行分隔
        # 创建 DMG
        log_warn("创建 DMG...")
        dmg_name = app_name.replace(" ", "_")
        dmg_path = dist_dir / f"{dmg_name}.dmg"
        
        # 创建临时目录
        temp_dmg_dir = dist_dir / "dmg_temp"
        if temp_dmg_dir.exists():
            shutil.rmtree(temp_dmg_dir)
        temp_dmg_dir.mkdir(parents=True)
        
        # 使用 ditto 复制应用（保留扩展属性和签名）
        # ditto 会保留所有扩展属性，包括代码签名信息
        log_warn("复制应用包到临时目录（保留签名）...")
        subprocess.run([
            "ditto", str(app_bundle), str(temp_dmg_dir / app_bundle.name)
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
        
        # 验证 DMG 中的签名是否完整（在删除临时目录之前）
        log_warn("验证 DMG 中的应用包签名...")
        # 挂载 DMG 来验证签名
        mount_result = subprocess.run([
            "hdiutil", "attach", "-nobrowse", "-mountpoint", "/tmp/dmg_verify",
            str(dmg_path)
        ], capture_output=True, text=True, check=False)
        
        signature_valid = True
        if mount_result.returncode == 0:
            try:
                dmg_app_path = Path("/tmp/dmg_verify") / app_bundle.name
                if dmg_app_path.exists():
                    verify_result = subprocess.run([
                        "codesign", "--verify", "--deep", "--strict", "--verbose",
                        str(dmg_app_path)
                    ], capture_output=True, text=True, check=False)
                    if verify_result.returncode != 0:
                        log_warn("⚠ DMG 中的应用包签名验证失败")
                        log_warn(f"  错误: {verify_result.stderr[:300]}")
                        signature_valid = False
                    else:
                        log_info("✓ DMG 中的应用包签名验证通过")
            finally:
                # 卸载 DMG
                subprocess.run([
                    "hdiutil", "detach", "/tmp/dmg_verify"
                ], capture_output=True, check=False)
        
        # 如果签名验证失败，重新创建 DMG（使用原始应用包，因为 ditto 应该已经保留了签名）
        if not signature_valid:
            log_warn("签名验证失败，重新创建 DMG（使用原始已签名的应用包）...")
            if dmg_path.exists():
                dmg_path.unlink()
            # 重新创建临时目录（如果已被删除）
            if not temp_dmg_dir.exists():
                temp_dmg_dir.mkdir(parents=True)
                # 使用 ditto 重新复制应用包
                subprocess.run([
                    "ditto", str(app_bundle), str(temp_dmg_dir / app_bundle.name)
                ], check=True)
                # 创建 Applications 链接
                os.symlink("/Applications", temp_dmg_dir / "Applications")
            
            # 重新创建 DMG
            subprocess.run([
                "hdiutil", "create",
                "-volname", app_name,
                "-srcfolder", str(temp_dmg_dir),
                "-ov",
                "-format", "UDZO",
                str(dmg_path)
            ], check=True)
            log_info("✓ DMG 已重新创建")
        
        # 清理临时目录
        if temp_dmg_dir.exists():
            shutil.rmtree(temp_dmg_dir)
        
        log_info(f"✓ DMG 创建成功: {dmg_path}")
        
        # DMG 代码签名
        if codesign_identity:
            log_warn("DMG 代码签名...")
            # 尝试使用时间戳签名（带重试机制）
            timestamp_max_retries = 3
            timestamp_retry_delay = 5
            timestamp_success = False
            timestamp_result = None
            
            for timestamp_attempt in range(1, timestamp_max_retries + 1):
                log_warn(f"  尝试使用时间戳签名（{timestamp_attempt}/{timestamp_max_retries}）...")
                timestamp_result = subprocess.run([
                    "codesign", "--force", "--verify", "--verbose",
                    "--sign", codesign_identity,
                    "--timestamp",  # 使用时间戳，这对公证很重要
                    str(dmg_path)
                ], capture_output=True, text=True, check=False)
                
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
                            timestamp_retry_delay *= 2  # 指数退避
                        else:
                            # 最后一次重试失败，将在循环外处理
                            log_warn(f"  ⚠ 时间戳服务不可用（已重试 {timestamp_max_retries} 次），将回退到不使用时间戳")
                    else:
                        # 其他错误，立即抛出异常
                        log_error(f"DMG 签名失败: {error_msg[:200]}")
                        raise subprocess.CalledProcessError(timestamp_result.returncode, timestamp_result.args)
            
            # 如果时间戳签名失败，回退到不使用时间戳
            if not timestamp_success:
                log_warn("⚠ 时间戳服务不可用，尝试不使用时间戳签名...")
                log_warn("   注意：不使用时间戳可能影响公证，但可以继续签名")
                subprocess.run([
                    "codesign", "--force", "--verify", "--verbose",
                    "--sign", codesign_identity,
                    "--timestamp=none",  # 回退到不使用时间戳
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
        
        # Apple 公证（带重试机制和详细错误信息）
        if apple_id and team_id and notary_password:
            log_warn("提交 Apple 公证...")
            
            # 公证前检查：验证应用包和 DMG 的签名
            log_warn("公证前验证...")
            pre_check_result = subprocess.run([
                "codesign", "--verify", "--deep", "--strict", "--verbose",
                str(app_bundle)
            ], capture_output=True, text=True, check=False)
            if pre_check_result.returncode != 0:
                log_error("⚠ 公证前验证失败，但继续尝试公证...")
                log_error(f"  错误: {pre_check_result.stderr[:300]}")
            
            max_retries = 3
            retry_delay = 10  # 秒
            notarized = False
            last_error = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    log_info(f"  尝试 {attempt}/{max_retries}...")
                    log_info("  正在上传 DMG 文件到 Apple 服务器（可能需要几分钟）...")
                    # 先提交（不等待），获取 submission ID
                    submit_result = subprocess.run([
                        "xcrun", "notarytool", "submit", str(dmg_path),
                        "--apple-id", apple_id,
                        "--team-id", team_id,
                        "--password", notary_password
                    ], check=True, capture_output=True, text=True, timeout=600)  # 10分钟上传超时
                    
                    # 解析 submission ID
                    submission_id = None
                    for line in submit_result.stdout.split('\n'):
                        if 'id:' in line.lower():
                            submission_id = line.split(':')[-1].strip()
                            break
                    
                    if submission_id:
                        log_info(f"  ✓ 上传完成，提交 ID: {submission_id}")
                        log_info("  等待 Apple 处理公证（可能需要 5-15 分钟，最长 30 分钟）...")
                        
                        # 使用轮询方式检查状态，而不是 wait 命令
                        max_wait_time = 1800  # 30分钟
                        poll_interval = 30  # 每30秒检查一次
                        notary_start_time = time.time()  # 使用不同的变量名，避免覆盖全局 start_time
                        status = None
                        result_output = submit_result.stdout
                        
                        while time.time() - notary_start_time < max_wait_time:
                            check_cancel()  # 检查是否请求取消
                            # 查询状态
                            try:
                                status_result = subprocess.run([
                                    "xcrun", "notarytool", "log", submission_id,
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60, check=False)
                                
                                if status_result.returncode == 0 and status_result.stdout:
                                    # 检查是否是 "not yet available" 消息
                                    if "not yet available" in status_result.stdout.lower() or "does not exist" in status_result.stdout.lower():
                                        # 还在处理中，继续等待
                                        elapsed = int(time.time() - notary_start_time)
                                        log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                    else:
                                        # 尝试解析 JSON 输出获取状态
                                        import json
                                        try:
                                            log_data = json.loads(status_result.stdout)
                                            status = log_data.get("status", "").lower()
                                            
                                            if status in ['accepted', 'success']:
                                                log_info("  ✓ 公证成功！")
                                                result_output = status_result.stdout
                                                break
                                            elif status in ['invalid', 'rejected', 'failed']:
                                                log_error(f"  ✗ 公证失败，状态: {status}")
                                                # 检查是否是账户配置问题
                                                status_summary = log_data.get("statusSummary", "")
                                                status_code = log_data.get("statusCode", "")
                                                if status_code == 7000 or "not yet configured" in status_summary.lower():
                                                    log_error("  ⚠ 账户配置问题：团队尚未启用公证功能")
                                                    log_error("  请访问 https://developer.apple.com/contact/ 联系 Apple Developer Support")
                                                    log_error("  主题选择：Development and Technical / Other Development or Technical Questions")
                                                    log_error("  说明需要启用公证（Notarization）功能")
                                                result_output = status_result.stdout
                                                break
                                            elif status == 'in progress':
                                                elapsed = int(time.time() - notary_start_time)
                                                log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                        except json.JSONDecodeError:
                                            # 如果不是 JSON，尝试从文本中解析
                                            if "status:" in status_result.stdout.lower():
                                                for line in status_result.stdout.split('\n'):
                                                    if 'status:' in line.lower():
                                                        status = line.split(':')[-1].strip().lower()
                                                        if status in ['accepted', 'success', 'invalid', 'rejected', 'failed']:
                                                            result_output = status_result.stdout
                                                            break
                                            if status and status in ['accepted', 'success', 'invalid', 'rejected', 'failed']:
                                                break
                                elif status_result.returncode != 0:
                                    # 查询失败，可能是还在处理中
                                    elapsed = int(time.time() - notary_start_time)
                                    log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                            except Exception as e:
                                log_warn(f"  查询状态时出错: {e}")
                            
                            # 等待后再次检查
                            time.sleep(poll_interval)
                        
                        # 如果超时，尝试最后一次查询
                        if not status or status not in ['accepted', 'success', 'invalid', 'rejected', 'failed']:
                            log_warn("  等待超时，尝试最后一次查询...")
                            try:
                                final_result = subprocess.run([
                                    "xcrun", "notarytool", "log", submission_id,
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60, check=False)
                                if final_result.returncode == 0:
                                    result_output = final_result.stdout
                                    # 尝试解析状态
                                    import json
                                    try:
                                        log_data = json.loads(final_result.stdout)
                                        status = log_data.get("status", "").lower()
                                    except:
                                        pass
                            except:
                                pass
                        
                        # 创建结果对象
                        class NotaryResult:
                            def __init__(self, stdout):
                                self.stdout = stdout
                                self.stderr = ""
                        result = NotaryResult(result_output)
                    else:
                        log_warn("  ⚠ 无法获取提交 ID，使用轮询模式代替 --wait...")
                        # 使用轮询模式代替 --wait，以便更好地控制超时和日志输出
                        max_wait_time = 1800  # 30分钟
                        poll_interval = 30  # 每30秒检查一次
                        notary_start_time = time.time()  # 使用不同的变量名，避免覆盖全局 start_time
                        status = None
                        result_output = ""
                        
                        # 先提交（不使用 --wait）
                        submit_result = subprocess.run([
                            "xcrun", "notarytool", "submit", str(dmg_path),
                            "--apple-id", apple_id,
                            "--team-id", team_id,
                            "--password", notary_password
                        ], capture_output=True, text=True, timeout=600, check=False)
                        
                        if submit_result.returncode != 0:
                            log_error(f"  提交失败: {submit_result.stderr or submit_result.stdout}")
                            raise subprocess.CalledProcessError(submit_result.returncode, "notarytool submit")
                        
                        # 尝试从输出中提取提交 ID
                        submission_id = None
                        for line in submit_result.stdout.split('\n'):
                            if 'id:' in line.lower() or 'submission id:' in line.lower():
                                submission_id = line.split(':')[-1].strip().strip('"')
                                break
                        
                        if not submission_id:
                            # 如果无法提取 ID，尝试从历史记录中获取最新的
                            log_warn("  无法从输出中提取提交 ID，尝试从历史记录获取...")
                            try:
                                history_result = subprocess.run([
                                    "xcrun", "notarytool", "history",
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60, check=False)
                                if history_result.returncode == 0:
                                    # 解析历史记录获取最新的提交 ID
                                    import json
                                    try:
                                        history_data = json.loads(history_result.stdout)
                                        if isinstance(history_data, list) and len(history_data) > 0:
                                            submission_id = history_data[0].get("id", "")
                                    except:
                                        pass
                            except:
                                pass
                        
                        if submission_id:
                            log_info(f"  ✓ 提交成功，ID: {submission_id}")
                            log_info("  等待 Apple 处理公证（轮询模式）...")
                            
                            while time.time() - notary_start_time < max_wait_time:
                                check_cancel()  # 检查是否请求取消
                                try:
                                    status_result = subprocess.run([
                                        "xcrun", "notarytool", "log", submission_id,
                                        "--apple-id", apple_id,
                                        "--team-id", team_id,
                                        "--password", notary_password
                                    ], capture_output=True, text=True, timeout=60, check=False)
                                    
                                    if status_result.returncode == 0 and status_result.stdout:
                                        import json
                                        try:
                                            log_data = json.loads(status_result.stdout)
                                            status = log_data.get("status", "").lower()
                                            
                                            if status in ['accepted', 'success']:
                                                log_info("  ✓ 公证成功！")
                                                result_output = status_result.stdout
                                                break
                                            elif status in ['invalid', 'rejected', 'failed']:
                                                log_error(f"  ✗ 公证失败，状态: {status}")
                                                result_output = status_result.stdout
                                                break
                                            elif status == 'in progress':
                                                elapsed = int(time.time() - notary_start_time)
                                                log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                        except json.JSONDecodeError:
                                            if "not yet available" in status_result.stdout.lower():
                                                elapsed = int(time.time() - notary_start_time)
                                                log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                
                                except Exception as e:
                                    log_warn(f"  查询状态时出错: {e}")
                                
                                time.sleep(poll_interval)
                            
                            if time.time() - notary_start_time >= max_wait_time:
                                log_error("  ✗ 公证等待超时（30分钟）")
                                raise subprocess.TimeoutExpired("notarytool", max_wait_time)
                            
                            # 创建结果对象
                            class NotaryResult:
                                def __init__(self, stdout):
                                    self.stdout = stdout
                                    self.stderr = ""
                            result = NotaryResult(result_output)
                        else:
                            log_error("  ✗ 无法获取提交 ID，无法使用轮询模式")
                            raise RuntimeError("无法获取公证提交 ID")
                    # 检查公证结果
                    if result.stdout:
                        print(result.stdout)
                    
                    # 解析公证状态（优先从 JSON 解析）
                    status = None
                    submission_id = None
                    
                    # 尝试解析 JSON
                    import json
                    try:
                        if isinstance(result.stdout, str):
                            log_data = json.loads(result.stdout)
                            status = log_data.get("status", "").strip()
                            submission_id = log_data.get("jobId", "").strip()
                    except (json.JSONDecodeError, AttributeError):
                        # 如果不是 JSON，从文本中解析
                        for line in result.stdout.split('\n'):
                            if 'status:' in line.lower():
                                status = line.split(':')[-1].strip().strip('"')
                            if 'id:' in line.lower() and not submission_id:
                                submission_id = line.split(':')[-1].strip().strip('"')
                    
                    # 检查状态
                    if status:
                        status_lower = status.lower()
                        if status_lower in ['accepted', 'success']:
                            log_info("✓ Apple 公证成功")
                            notarized = True
                            
                            # 公证成功后，装订票据到应用包
                            log_warn("装订公证票据到应用包...")
                            try:
                                subprocess.run([
                                    "xcrun", "stapler", "staple",
                                    str(app_bundle)
                                ], check=True, capture_output=True, text=True)
                                log_info("✓ 票据装订成功")
                                
                                # 验证装订
                                log_warn("验证装订的票据...")
                                validate_result = subprocess.run([
                                    "xcrun", "stapler", "validate",
                                    str(app_bundle)
                                ], capture_output=True, text=True, check=False)
                                if validate_result.returncode == 0:
                                    log_info("✓ 票据验证通过")
                                else:
                                    log_warn(f"⚠ 票据验证警告: {validate_result.stderr[:200]}")
                                
                                # 再次验证 spctl（现在应该通过）
                                log_warn("再次验证 spctl（装订后）...")
                                spctl_result = subprocess.run([
                                    "spctl", "--assess", "--verbose", "--type", "execute",
                                    str(app_bundle)
                                ], capture_output=True, text=True, check=False)
                                if spctl_result.returncode == 0:
                                    log_info("✓ spctl 验证通过（应用已公证）")
                                else:
                                    log_warn(f"⚠ spctl 验证仍失败: {spctl_result.stderr[:200]}")
                            except subprocess.CalledProcessError as e:
                                log_error(f"装订票据失败: {e.stderr or e.stdout}")
                                log_warn("⚠ 继续执行，但票据未装订")
                        else:
                            # 公证失败
                            log_error(f"✗ Apple 公证失败，状态: {status}")
                            notarized = False
                            
                            # 获取详细错误信息
                            if submission_id:
                                log_warn("获取详细错误信息...")
                                try:
                                    log_result = subprocess.run([
                                        "xcrun", "notarytool", "log",
                                        submission_id,
                                        "--apple-id", apple_id,
                                        "--team-id", team_id,
                                        "--password", notary_password
                                    ], capture_output=True, text=True, timeout=60)
                                    if log_result.returncode == 0 and log_result.stdout:
                                        log_error("公证错误详情:")
                                        print(log_result.stdout)
                                    else:
                                        log_warn("无法获取详细日志，尝试查看历史记录...")
                                        history_result = subprocess.run([
                                            "xcrun", "notarytool", "history",
                                            "--apple-id", apple_id,
                                            "--team-id", team_id,
                                            "--password", notary_password
                                        ], capture_output=True, text=True, timeout=60)  # 网络请求可能需要更长时间
                                        if history_result.returncode == 0:
                                            print(history_result.stdout[:1000])
                                except Exception as e:
                                    log_warn(f"获取错误详情失败: {e}")
                            
                            log_error("公证失败，无法装订票据")
                            log_warn("建议检查:")
                            log_warn("  1. 应用包签名是否完整: codesign -vvv --deep <app_bundle>")
                            log_warn("  2. 是否有硬编码路径或临时文件")
                            log_warn("  3. 证书是否有效: security find-identity -v -p codesigning")
                    else:
                        # 无法解析状态，假设成功（向后兼容）
                        log_warn("⚠ 无法解析公证状态，假设成功")
                        notarized = True
                    
                    break
                except subprocess.TimeoutExpired:
                    log_error(f"✗ Apple 公证超时（尝试 {attempt}/{max_retries}）")
                    if attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr or e.stdout or "未知错误"
                    last_error = error_msg
                    
                    # 尝试获取更详细的错误信息
                    log_error(f"✗ Apple 公证失败（尝试 {attempt}/{max_retries}）")
                    if e.stdout:
                        print("标准输出:")
                        print(e.stdout)
                    if e.stderr:
                        print("错误输出:")
                        print(e.stderr)
                    
                    # 如果是公证被拒绝，尝试获取详细日志
                    if "rejected" in error_msg.lower() or "invalid" in error_msg.lower():
                        log_error("⚠ 公证被拒绝，尝试获取详细日志...")
                        # 尝试从历史记录中获取错误详情
                        try:
                            history_result = subprocess.run([
                                "xcrun", "notarytool", "history",
                                "--apple-id", apple_id,
                                "--team-id", team_id,
                                "--password", notary_password
                            ], capture_output=True, text=True, timeout=60)  # 网络请求可能需要更长时间
                            if history_result.returncode == 0:
                                log_info("最近的公证历史:")
                                print(history_result.stdout[:500])
                        except Exception:
                            pass
                    
                    # 检查是否是网络相关错误
                    network_errors = ["network", "connection", "timeout", "resolve", "unreachable"]
                    is_network_error = any(err in error_msg.lower() for err in network_errors)
                    
                    if is_network_error and attempt < max_retries:
                        log_error(f"  网络错误，等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    elif attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                except Exception as e:
                    log_error(f"✗ Apple 公证异常（尝试 {attempt}/{max_retries}）: {str(e)}")
                    if attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
            
            if not notarized:
                log_error("✗ Apple 公证最终失败（已重试所有次数）")
                if last_error:
                    log_error(f"最后错误信息: {last_error[:500]}")
                log_warn("⚠ 继续执行，但 DMG 未通过公证")
                log_warn("建议检查:")
                log_warn("  1. 证书是否有效: security find-identity -v -p codesigning")
                log_warn("  2. 应用包签名是否完整: codesign -vvv --deep <app_bundle>")
                log_warn("  3. 查看公证历史: xcrun notarytool history --apple-id <id> --team-id <id>")
                log_warn("  4. 检查 Apple ID 和密码是否正确")
        else:
            log_warn("⚠ 跳过 Apple 公证（需要设置 APPLE_ID, TEAM_ID, NOTARY_PASSWORD 环境变量）")
        
        # 创建 PKG 安装包（在 DMG 和公证之后）
        log_warn("创建 PKG 安装包...")
        pkg_name = app_name.replace(" ", "_")
        pkg_path = dist_dir / f"{pkg_name}.pkg"
        
        # 准备 PKG 资源目录
        pkg_resources = dist_dir / "pkg_resources"
        if pkg_resources.exists():
            shutil.rmtree(pkg_resources)
        pkg_resources.mkdir(parents=True)
        
        # 创建 Distribution.xml（用于 productbuild）
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
        # 创建临时目录用于 pkgbuild
        pkg_root = pkg_resources / "pkg_root"
        pkg_root.mkdir(exist_ok=True)
        applications_dir = pkg_root / "Applications"
        applications_dir.mkdir(exist_ok=True)
        # 使用 ditto 复制应用包到临时目录（保留扩展属性和代码签名）
        # 注意：必须使用 ditto 而不是 shutil.copytree，因为 shutil.copytree 会破坏代码签名
        log_warn("    复制应用包（保留签名和架构）...")
        subprocess.run([
            "ditto", str(app_bundle), str(applications_dir / app_bundle.name)
        ], check=True)
        
        # 验证复制后的应用包架构（如果之前检测过）
        if arch == "arm64":
            copied_app = applications_dir / app_bundle.name
            copied_executable = copied_app / "Contents" / "MacOS" / app_name
            if copied_executable.exists():
                try:
                    lipo_result = subprocess.run(
                        ["lipo", "-info", str(copied_executable)],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if lipo_result.returncode == 0:
                        if "arm64" not in lipo_result.stdout:
                            log_error(f"  ✗ 错误: 复制后的应用包架构不正确！")
                            log_error(f"  实际架构: {lipo_result.stdout.strip()}")
                            log_error(f"  这会导致 PKG 安装后需要 Rosetta")
                            log_error(f"  请检查 PyInstaller 是否正确生成了 arm64 架构的应用")
                        else:
                            log_info(f"  ✓ 复制后的应用包架构正确: arm64")
                except Exception as e:
                    log_warn(f"  无法验证复制后的架构: {e}")
        
        subprocess.run([
            "pkgbuild",
            "--root", str(pkg_root),
            "--identifier", app_id,
            "--version", "1.0.1",
            "--install-location", "/",
            str(component_pkg)
        ], check=True)
        log_info("  ✓ 组件包创建成功")
        
        # 验证组件包内的应用包架构
        if arch == "arm64":
            log_warn("    验证组件包内的应用包架构...")
            try:
                # 展开组件包以检查架构
                expanded_dir = pkg_resources / "expanded_component"
                if expanded_dir.exists():
                    shutil.rmtree(expanded_dir)
                expanded_dir.mkdir()
                
                subprocess.run([
                    "pkgutil", "--expand", str(component_pkg), str(expanded_dir)
                ], check=True, capture_output=True)
                
                # 查找展开后的应用包
                expanded_app = None
                for root, dirs, files in os.walk(expanded_dir):
                    if app_bundle.name in dirs:
                        expanded_app = Path(root) / app_bundle.name
                        break
                
                if expanded_app and expanded_app.exists():
                    expanded_executable = expanded_app / "Contents" / "MacOS" / app_name
                    if expanded_executable.exists():
                        lipo_result = subprocess.run(
                            ["lipo", "-info", str(expanded_executable)],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if lipo_result.returncode == 0:
                            if "arm64" not in lipo_result.stdout:
                                log_error(f"  ✗ 错误: 组件包内的应用包架构不正确！")
                                log_error(f"  实际架构: {lipo_result.stdout.strip()}")
                                log_error(f"  这会导致 PKG 安装后需要 Rosetta")
                            else:
                                log_info(f"  ✓ 组件包内的应用包架构正确: arm64")
                
                # 清理展开的目录
                if expanded_dir.exists():
                    shutil.rmtree(expanded_dir)
            except Exception as e:
                log_warn(f"  无法验证组件包架构: {e}")
        
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
        
        # 验证最终 PKG 内的应用包架构（关键检查）
        if arch == "arm64":
            log_warn("    验证最终 PKG 内的应用包架构...")
            try:
                # 展开最终 PKG 以检查架构
                expanded_pkg_dir = dist_dir / "expanded_pkg"
                if expanded_pkg_dir.exists():
                    shutil.rmtree(expanded_pkg_dir)
                expanded_pkg_dir.mkdir()
                
                subprocess.run([
                    "pkgutil", "--expand", str(pkg_path), str(expanded_pkg_dir)
                ], check=True, capture_output=True)
                
                # 查找展开后的应用包
                expanded_app = None
                for root, dirs, files in os.walk(expanded_pkg_dir):
                    if app_bundle.name in dirs:
                        expanded_app = Path(root) / app_bundle.name
                        break
                
                if expanded_app and expanded_app.exists():
                    expanded_executable = expanded_app / "Contents" / "MacOS" / app_name
                    if expanded_executable.exists():
                        lipo_result = subprocess.run(
                            ["lipo", "-info", str(expanded_executable)],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        if lipo_result.returncode == 0:
                            log_info(f"  PKG 内应用包架构: {lipo_result.stdout.strip()}")
                            if "arm64" not in lipo_result.stdout:
                                log_error(f"  ✗ 严重错误: 最终 PKG 内的应用包架构不正确！")
                                log_error(f"  实际架构: {lipo_result.stdout.strip()}")
                                log_error(f"  这会导致安装时提示需要 Rosetta")
                                log_error(f"  可能原因: pkgbuild 或 productbuild 改变了架构")
                                log_error(f"  建议: 检查 pkgbuild 选项或使用不同的打包方法")
                            else:
                                log_info(f"  ✓ 最终 PKG 内的应用包架构正确: arm64")
                        else:
                            log_warn(f"  无法获取架构信息: {lipo_result.stderr}")
                    else:
                        log_warn(f"  未找到可执行文件: {expanded_executable}")
                else:
                    log_warn(f"  未在 PKG 中找到应用包")
                
                # 清理展开的目录
                if expanded_pkg_dir.exists():
                    shutil.rmtree(expanded_pkg_dir)
            except Exception as e:
                log_warn(f"  无法验证最终 PKG 架构: {e}")
                import traceback
                log_warn(f"  错误详情: {traceback.format_exc()}")
        
        # 签名 PKG（需要 Installer 证书，不是 Application 证书）
        # installer_identity 已经在上面从环境变量或 p12 文件中获取
        pkg_signed_successfully = False
        
        # 如果没有明确指定，尝试从 Application 证书名称推断 Installer 证书
        if not installer_identity and codesign_identity:
            # 将 "Developer ID Application" 替换为 "Developer ID Installer"
            if "Developer ID Application" in codesign_identity:
                installer_identity = codesign_identity.replace("Developer ID Application", "Developer ID Installer")
            else:
                # 尝试查找所有可用的 Installer 证书
                log_warn("  查找可用的 Installer 证书...")
                find_result = subprocess.run([
                    "security", "find-identity", "-v", "-p", "codesigning"
                ], capture_output=True, text=True, check=False)
                
                if find_result.returncode == 0:
                    for line in find_result.stdout.split('\n'):
                        if "Developer ID Installer" in line:
                            # 提取证书标识（引号中的内容）
                            import re
                            match = re.search(r'"([^"]+)"', line)
                            if match:
                                installer_identity = match.group(1)
                                log_info(f"  找到 Installer 证书: {installer_identity}")
                                break
        
        if installer_identity:
            log_warn("签名 PKG（使用 Installer 证书）...")
            # 使用临时文件进行签名（productsign 不能使用相同的输入和输出路径）
            pkg_signed = dist_dir / f"{pkg_name}_signed.pkg"
            
            # 尝试使用时间戳签名（带重试机制）
            timestamp_max_retries = 3
            timestamp_retry_delay = 5
            timestamp_success = False
            
            for timestamp_attempt in range(1, timestamp_max_retries + 1):
                log_warn(f"  尝试使用时间戳签名（{timestamp_attempt}/{timestamp_max_retries}）...")
                timestamp_result = subprocess.run([
                    "productsign",
                    "--sign", installer_identity,
                    "--timestamp",
                    str(pkg_path),
                    str(pkg_signed)
                ], capture_output=True, text=True, check=False)
                
                if timestamp_result.returncode == 0:
                    log_info("  ✓ PKG 签名完成（已使用时间戳）")
                    timestamp_success = True
                    break
                else:
                    error_msg = timestamp_result.stderr or timestamp_result.stdout or ""
                    if "timestamp service is not available" in error_msg or "network" in error_msg.lower() or "timeout" in error_msg.lower():
                        if timestamp_attempt < timestamp_max_retries:
                            log_warn(f"  ⚠ 时间戳服务不可用，{timestamp_retry_delay} 秒后重试...")
                            time.sleep(timestamp_retry_delay)
                            timestamp_retry_delay *= 2  # 指数退避
                        else:
                            # 最后一次重试失败，将在循环外抛出异常
                            log_error(f"  ✗ 时间戳服务不可用（已重试 {timestamp_max_retries} 次）")
                    else:
                        # 其他错误，立即抛出异常
                        log_error(f"  ✗ PKG 签名失败: {error_msg[:200]}")
                        raise Exception(f"PKG 签名失败: {error_msg[:200]}")
            
            # 如果时间戳签名失败，终止构建（不使用无时间戳签名，因为无法通过公证）
            if not timestamp_success:
                error_msg = timestamp_result.stderr or timestamp_result.stdout or ""
                log_error(f"  ✗ PKG 签名失败（已重试 {timestamp_max_retries} 次）: {error_msg[:200]}")
                log_error("     无时间戳签名无法通过公证，构建终止")
                raise Exception(f"PKG 签名失败：时间戳服务不可用（已重试 {timestamp_max_retries} 次）")
            
            # 替换原文件（如果签名成功）
            if timestamp_success and pkg_signed.exists():
                pkg_path.unlink()
                pkg_signed.rename(pkg_path)
                log_info("  ✓ PKG 文件已替换为签名版本")
                pkg_signed_successfully = True
            else:
                pkg_signed_successfully = False
        else:
            log_warn("⚠ 跳过 PKG 签名（需要 Developer ID Installer 证书）")
            log_warn("  PKG 安装包需要 'Developer ID Installer' 证书，而不是 'Developer ID Application' 证书")
            log_warn("  获取方法：")
            log_warn("    1. 访问 https://developer.apple.com/account/")
            log_warn("    2. 进入 Certificates, Identifiers & Profiles")
            log_warn("    3. 创建 'Developer ID Installer' 证书（用于分发安装包）")
            log_warn("    4. 下载并安装到 Keychain")
            log_warn("    5. 设置环境变量: export INSTALLER_CODESIGN_IDENTITY='Developer ID Installer: Your Name (TEAM_ID)'")
            log_warn("  或通过环境变量 INSTALLER_CODESIGN_IDENTITY 指定 Installer 证书")
        
        # 验证 PKG 签名（如果已签名）
        if pkg_signed_successfully and pkg_path.exists():
            log_warn("验证 PKG 签名...")
            pkg_verify = subprocess.run([
                "pkgutil", "--check-signature", str(pkg_path)
            ], capture_output=True, text=True, check=False)
            if pkg_verify.returncode == 0:
                log_info("  ✓ PKG 签名验证通过")
            else:
                log_warn(f"  ⚠ PKG 签名验证警告: {pkg_verify.stderr[:200]}")
        
        # PKG 公证（复用 DMG 的公证流程）
        # 注意：PKG 必须已签名才能进行公证
        if apple_id and team_id and notary_password and pkg_signed_successfully:
            log_warn("提交 PKG 到 Apple 公证...")
            
            max_retries = 3
            retry_delay = 10
            pkg_notarized = False
            last_pkg_error = None
            
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
                        notary_start_time = time.time()  # 使用不同的变量名，避免覆盖全局 start_time
                        status = None
                        result_output = submit_result.stdout
                        
                        while time.time() - notary_start_time < max_wait_time:
                            check_cancel()  # 检查是否请求取消
                            try:
                                status_result = subprocess.run([
                                    "xcrun", "notarytool", "log", submission_id,
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60, check=False)
                                
                                if status_result.returncode == 0 and status_result.stdout:
                                    if "not yet available" in status_result.stdout.lower() or "does not exist" in status_result.stdout.lower():
                                        elapsed = int(time.time() - notary_start_time)
                                        log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                    else:
                                        import json
                                        try:
                                            log_data = json.loads(status_result.stdout)
                                            status = log_data.get("status", "").lower()
                                            
                                            if status in ['accepted', 'success']:
                                                log_info("  ✓ PKG 公证成功！")
                                                result_output = status_result.stdout
                                                break
                                            elif status in ['invalid', 'rejected', 'failed']:
                                                log_error(f"  ✗ PKG 公证失败，状态: {status}")
                                                # 获取详细错误日志
                                                log_error("  获取详细错误信息...")
                                                try:
                                                    log_result = subprocess.run([
                                                        "xcrun", "notarytool", "log",
                                                        submission_id,
                                                        "--apple-id", apple_id,
                                                        "--team-id", team_id,
                                                        "--password", notary_password
                                                    ], capture_output=True, text=True, timeout=60)
                                                    if log_result.returncode == 0 and log_result.stdout:
                                                        log_error("PKG 公证错误详情:")
                                                        print(log_result.stdout)
                                                    else:
                                                        log_warn("无法获取详细日志，尝试查看历史记录...")
                                                        history_result = subprocess.run([
                                                            "xcrun", "notarytool", "history",
                                                            "--apple-id", apple_id,
                                                            "--team-id", team_id,
                                                            "--password", notary_password
                                                        ], capture_output=True, text=True, timeout=30)
                                                        if history_result.returncode == 0:
                                                            print(history_result.stdout[:1000])
                                                except Exception as e:
                                                    log_warn(f"获取错误详情失败: {e}")
                                                
                                                log_error("PKG 公证失败，请检查签名问题")
                                                log_error("常见原因：应用包内的二进制文件签名无效或损坏")
                                                log_error("建议：")
                                                log_error("  1. 检查应用包签名: codesign -vvv --deep <app_bundle>")
                                                log_error("  2. 重新签名应用包后重新打包 PKG")
                                                log_error("  3. 确保所有二进制文件都已正确签名")
                                                result_output = status_result.stdout
                                                # 非网络错误，不重试，直接中断
                                                raise Exception("PKG 公证失败（非网络错误），请检查签名问题")
                                            elif status == 'in progress':
                                                elapsed = int(time.time() - notary_start_time)
                                                log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                                        except json.JSONDecodeError:
                                            if "status:" in status_result.stdout.lower():
                                                for line in status_result.stdout.split('\n'):
                                                    if 'status:' in line.lower():
                                                        status = line.split(':')[-1].strip().lower()
                                                        if status in ['accepted', 'success', 'invalid', 'rejected', 'failed']:
                                                            result_output = status_result.stdout
                                                            break
                                            if status and status in ['accepted', 'success', 'invalid', 'rejected', 'failed']:
                                                break
                                elif status_result.returncode != 0:
                                    elapsed = int(time.time() - notary_start_time)
                                    log_info(f"  处理中... (已等待 {elapsed//60} 分 {elapsed%60} 秒)")
                            except Exception as e:
                                log_warn(f"  查询状态时出错: {e}")
                            
                            time.sleep(poll_interval)
                        
                        # 检查是否超时
                        if time.time() - notary_start_time >= max_wait_time:
                            log_error("  ✗ PKG 公证等待超时（30分钟）")
                            # 尝试最后一次查询
                            try:
                                final_result = subprocess.run([
                                    "xcrun", "notarytool", "log", submission_id,
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60, check=False)
                                if final_result.returncode == 0:
                                    result_output = final_result.stdout
                                    import json
                                    try:
                                        log_data = json.loads(final_result.stdout)
                                        status = log_data.get("status", "").lower()
                                    except:
                                        pass
                            except:
                                pass
                            
                            if not status or status not in ['accepted', 'success']:
                                raise subprocess.TimeoutExpired("notarytool", max_wait_time)
                        
                        # 解析公证状态
                        import json
                        try:
                            if isinstance(result_output, str):
                                log_data = json.loads(result_output)
                                status = log_data.get("status", "").strip().lower()
                        except (json.JSONDecodeError, AttributeError):
                            for line in result_output.split('\n'):
                                if 'status:' in line.lower():
                                    status = line.split(':')[-1].strip().strip('"').lower()
                                    break
                        
                        if status and status in ['accepted', 'success']:
                            log_info("✓ PKG Apple 公证成功")
                            pkg_notarized = True
                            
                            # 装订票据到 PKG
                            log_warn("装订公证票据到 PKG...")
                            try:
                                subprocess.run([
                                    "xcrun", "stapler", "staple",
                                    str(pkg_path)
                                ], check=True, capture_output=True, text=True)
                                log_info("✓ PKG 票据装订成功")
                            except subprocess.CalledProcessError as e:
                                log_error(f"装订 PKG 票据失败: {e.stderr or e.stdout}")
                                log_warn("⚠ 继续执行，但 PKG 票据未装订")
                            break
                        else:
                            # 非网络错误，获取详细日志并中断
                            log_error(f"✗ PKG 公证失败，状态: {status}")
                            log_error("  获取详细错误信息...")
                            if submission_id:
                                try:
                                    log_result = subprocess.run([
                                        "xcrun", "notarytool", "log",
                                        submission_id,
                                        "--apple-id", apple_id,
                                        "--team-id", team_id,
                                        "--password", notary_password
                                    ], capture_output=True, text=True, timeout=60)
                                    if log_result.returncode == 0 and log_result.stdout:
                                        log_error("PKG 公证错误详情:")
                                        print(log_result.stdout)
                                    else:
                                        log_warn("无法获取详细日志，尝试查看历史记录...")
                                        history_result = subprocess.run([
                                            "xcrun", "notarytool", "history",
                                            "--apple-id", apple_id,
                                            "--team-id", team_id,
                                            "--password", notary_password
                                        ], capture_output=True, text=True, timeout=60)  # 网络请求可能需要更长时间
                                        if history_result.returncode == 0:
                                            print(history_result.stdout[:1000])
                                except Exception as log_err:
                                    log_warn(f"获取错误详情失败: {log_err}")
                            
                            log_error("PKG 公证失败（非网络错误），请检查签名问题")
                            log_error("常见原因：应用包内的二进制文件签名无效或损坏")
                            log_error("建议：")
                            log_error("  1. 检查应用包签名: codesign -vvv --deep <app_bundle>")
                            log_error("  2. 重新签名应用包后重新打包 PKG")
                            log_error("  3. 确保所有二进制文件都已正确签名")
                            # 非网络错误，不重试，直接中断
                            raise Exception("PKG 公证失败（非网络错误），请检查签名问题")
                    else:
                        log_warn("  ⚠ 无法获取提交 ID")
                        break
                except subprocess.TimeoutExpired:
                    log_error(f"✗ PKG 公证超时（尝试 {attempt}/{max_retries}）")
                    if attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr or e.stdout or "未知错误"
                    last_pkg_error = error_msg
                    log_error(f"✗ PKG 公证失败（尝试 {attempt}/{max_retries}）")
                    
                    # 检查是否是网络相关错误
                    network_errors = ["network", "connection", "timeout", "resolve", "unreachable"]
                    is_network_error = any(err in error_msg.lower() for err in network_errors)
                    
                    if is_network_error and attempt < max_retries:
                        log_error(f"  网络错误，等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    elif not is_network_error:
                        # 非网络错误，获取详细日志并中断
                        log_error("  非网络错误，获取详细错误信息...")
                        if submission_id:
                            try:
                                log_result = subprocess.run([
                                    "xcrun", "notarytool", "log",
                                    submission_id,
                                    "--apple-id", apple_id,
                                    "--team-id", team_id,
                                    "--password", notary_password
                                ], capture_output=True, text=True, timeout=60)
                                if log_result.returncode == 0 and log_result.stdout:
                                    log_error("PKG 公证错误详情:")
                                    print(log_result.stdout)
                            except Exception as log_err:
                                log_warn(f"获取详细日志失败: {log_err}")
                        
                        log_error("PKG 公证失败（非网络错误），请检查签名问题")
                        log_error("常见原因：应用包内的二进制文件签名无效或损坏")
                        log_error("建议：")
                        log_error("  1. 检查应用包签名: codesign -vvv --deep <app_bundle>")
                        log_error("  2. 重新签名应用包后重新打包 PKG")
                        log_error("  3. 确保所有二进制文件都已正确签名")
                        raise Exception("PKG 公证失败（非网络错误），请检查签名问题")
                    elif attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                except Exception as e:
                    error_str = str(e)
                    # 检查是否是之前抛出的非网络错误异常
                    if "PKG 公证失败（非网络错误）" in error_str:
                        # 直接重新抛出，不重试
                        raise
                    log_error(f"✗ PKG 公证异常（尝试 {attempt}/{max_retries}）: {error_str}")
                    if attempt < max_retries:
                        log_warn(f"  等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2
            
            if not pkg_notarized:
                log_warn("⚠ PKG 未通过公证，但继续执行")
        else:
            log_warn("⚠ 跳过 PKG 公证（需要设置 APPLE_ID, TEAM_ID, NOTARY_PASSWORD 环境变量）")
        
        # 清理临时文件
        if pkg_resources.exists():
            shutil.rmtree(pkg_resources)
        
        log_info(f"✓ PKG 安装包创建完成: {pkg_path}")
        
    elif platform in ["windows", "win"]:
        # Windows 打包
        log_warn("开始 Windows 打包...")
        
        spec_file = "build.spec"
        if not Path(spec_file).exists():
            log_error(f"错误: 找不到 {spec_file}")
            sys.exit(1)
        
        # 打包
        log_warn("执行 PyInstaller 打包...")
        # 获取日志级别（默认为 INFO，可以通过环境变量覆盖）
        log_level = os.environ.get("PYINSTALLER_LOG_LEVEL", "INFO")
        
        try:
            check_cancel()  # 检查是否请求取消
            # 使用实时输出而不是 capture_output，以便能够看到进度并响应取消
            log_info("开始 PyInstaller 打包（实时输出）...")
            result = subprocess.run([
                sys.executable, "-m", "PyInstaller",
                spec_file,
                "--clean",
                "--noconfirm",
                f"--log-level={log_level}"
            ], check=True, timeout=3600)  # 1小时超时，实时输出
            result = subprocess.CompletedProcess([], 0, "", "")  # 创建成功结果对象
        except subprocess.TimeoutExpired:
            log_error("PyInstaller 执行超时（超过1小时）")
            raise
        except KeyboardInterrupt:
            log_warn("PyInstaller 打包被用户取消")
            raise
        except subprocess.CalledProcessError as e:
            log_error(f"PyInstaller 执行失败，退出码: {e.returncode}")
            # 错误信息已经在实时输出中显示，直接抛出异常
            raise
        
        exe_path = Path("dist") / f"{app_name}.exe"
        
        if not exe_path.exists():
            log_error("错误: EXE 文件未生成")
            sys.exit(1)
        
        log_info(f"✓ EXE 文件生成成功: {exe_path}")
        
        # 创建 EXE 安装器（Inno Setup）
        log_warn("创建 EXE 安装器（Inno Setup）...")
        
        # 检查 Inno Setup 是否安装
        inno_compiler = None
        inno_paths = [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]
        
        # 检查 PATH 中是否有 ISCC
        if shutil.which("ISCC.exe"):
            inno_compiler = "ISCC.exe"
        else:
            for path in inno_paths:
                if Path(path).exists():
                    inno_compiler = path
                    break
        
        if not inno_compiler:
            log_warn("⚠ 未找到 Inno Setup 编译器 (ISCC.exe)")
            log_warn("  请安装 Inno Setup: https://jrsoftware.org/isinfo.php")
            log_warn("  或跳过安装器创建，直接使用 EXE 文件")
        else:
            # 准备 Inno Setup 脚本
            inno_script = client_dir / "setup.iss"
            template_script = project_root / "scripts" / "inno_setup_template.iss"
            
            # 读取模板或创建基本脚本
            if template_script.exists():
                with open(template_script, "r", encoding="utf-8") as f:
                    content = f.read()
                
                content = content.replace("{APP_NAME}", app_name)
                content = content.replace("{EXE_NAME}", app_name)
                
                with open(inno_script, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                # 创建基本脚本
                with open(inno_script, "w", encoding="utf-8") as f:
                    f.write(f'''[Setup]
AppName={app_name}
AppVersion=1.0.0
AppPublisher=SanYing
AppPublisherURL=https://perf.sanying.site
DefaultDirName={{autopf}}\\{app_name}
DefaultGroupName={app_name}
OutputDir=dist
OutputBaseFilename={app_name}_Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"
Name: "quicklaunchicon"; Description: "创建快速启动栏快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "dist\\{app_name}.exe"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\{app_name}"; Filename: "{{app}}\\{app_name}.exe"
Name: "{{group}}\\卸载 {app_name}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{app_name}"; Filename: "{{app}}\\{app_name}.exe"; Tasks: desktopicon
Name: "{{userappdata}}\\Microsoft\\Internet Explorer\\Quick Launch\\{app_name}"; Filename: "{{app}}\\{app_name}.exe"; Tasks: quicklaunchicon

[Run]
Filename: "{{app}}\\{app_name}.exe"; Description: "启动 {app_name}"; Flags: nowait postinstall skipifsilent
''')
            
            # 编译安装器
            log_warn("  编译 Inno Setup 安装器...")
            try:
                subprocess.run([
                    inno_compiler,
                    str(inno_script)
                ], check=True, cwd=str(client_dir))
                
                installer_path = client_dir / "dist" / f"{app_name}_Setup.exe"
                if installer_path.exists():
                    log_info(f"  ✓ EXE 安装器创建成功: {installer_path}")
                else:
                    log_warn("  ⚠ 安装器文件未生成，请检查 Inno Setup 脚本")
            except subprocess.CalledProcessError as e:
                log_error(f"  ✗ 编译安装器失败: {e}")
                log_warn("  ⚠ 跳过 EXE 安装器创建")
        
        # 创建 MSI 安装包（WiX Toolset 6.x）
        log_warn("创建 MSI 安装包（WiX Toolset 6.x）...")
        
        # 检查 WiX Toolset 6.x 是否安装
        # WiX 6.x 使用 wix.exe 或 wix 命令
        wix_build = shutil.which("wix.exe") or shutil.which("wix")
        
        # 检查 WiX 6.x 路径
        if not wix_build:
            wix6_paths = [
                r"C:\Program Files\WiX Toolset v6.0\bin\wix.exe",
                r"C:\Program Files (x86)\WiX Toolset v6.0\bin\wix.exe",
                r"C:\Program Files\WiX Toolset v6\bin\wix.exe",
                r"C:\Program Files (x86)\WiX Toolset v6\bin\wix.exe",
            ]
            for path in wix6_paths:
                if Path(path).exists():
                    wix_build = path
                    log_info(f"  找到 WiX Toolset 6.x: {Path(path).parent.parent}")
                    break
        
        if not wix_build:
            log_warn("⚠ 未找到 WiX Toolset 6.x")
            log_warn("  需要: wix.exe 或 wix 命令")
            log_warn("  请安装 WiX Toolset 6.0.2 或更高版本: https://wixtoolset.org/releases/")
            log_warn("  或跳过 MSI 安装包创建")
        else:
            # 生成固定的 UpgradeCode GUID（基于应用名称，确保每次打包时保持一致）
            # 使用 uuid5 基于命名空间和应用名称生成确定性 GUID
            namespace_uuid = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')  # DNS namespace
            upgrade_code = str(uuid.uuid5(namespace_uuid, f"site.sanying.{app_name}.upgrade")).upper()
            
            # 创建 WXS 文件（WiX 6.x 格式）
            wxs_file = client_dir / f"{app_name}.wxs"
            with open(wxs_file, "w", encoding="utf-8") as f:
                f.write(f'''<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
    <Package Name="{app_name}" Version="1.0.1" Manufacturer="SanYing" UpgradeCode="{upgrade_code}" Language="2052">
        <MajorUpgrade DowngradeErrorMessage="无法安装旧版本，请先卸载当前版本。" />
        <MediaTemplate EmbedCab="yes" />
        
        <Feature Id="ProductFeature" Title="{app_name}" Level="1">
            <ComponentGroupRef Id="ApplicationFiles" />
            <ComponentGroupRef Id="ApplicationShortcut" />
        </Feature>
        
        <StandardDirectory Id="ProgramFilesFolder">
            <Directory Id="INSTALLFOLDER" Name="{app_name}" />
        </StandardDirectory>
        
        <StandardDirectory Id="ProgramMenuFolder">
            <Directory Id="ApplicationProgramsFolder" Name="{app_name}" />
        </StandardDirectory>
        
        <ComponentGroup Id="ApplicationFiles">
            <Component Id="ApplicationFilesComponent" Directory="INSTALLFOLDER">
                <File Id="ApplicationFile" Source="dist/{app_name}.exe" KeyPath="yes" />
            </Component>
        </ComponentGroup>
        
        <ComponentGroup Id="ApplicationShortcut">
            <Component Id="ApplicationShortcutComponent" Directory="ApplicationProgramsFolder">
                <Shortcut Id="ApplicationStartMenuShortcut"
                          Name="{app_name}"
                          Description="{app_name}"
                          Target="[INSTALLFOLDER]{app_name}.exe"
                          WorkingDirectory="INSTALLFOLDER" />
                <RemoveFolder Id="ApplicationProgramsFolder" On="uninstall" />
                <RegistryValue Root="HKCU" Key="Software/SanYing/{app_name}" Type="string" Value="" KeyPath="yes" />
            </Component>
        </ComponentGroup>
    </Package>
</Wix>
''')
            
            # WiX 6.x 使用 wix build 命令
            msi_path = client_dir / "dist" / f"{app_name}.msi"
            log_warn("  使用 wix build 构建 MSI...")
            try:
                subprocess.run([
                    wix_build,
                    "build",
                    str(wxs_file),
                    "-o", str(msi_path)
                ], check=True, cwd=str(client_dir))
                
                if msi_path.exists():
                    log_info(f"  ✓ MSI 安装包创建成功: {msi_path}")
                else:
                    log_warn("  ⚠ MSI 文件未生成")
            except subprocess.CalledProcessError as e:
                log_error(f"  ✗ WiX 构建失败: {e}")
                log_warn("  ⚠ 跳过 MSI 安装包创建")
            finally:
                # 清理临时文件
                if wxs_file.exists():
                    wxs_file.unlink()
        
    elif platform == "linux":
        # Linux 打包
        log_warn("开始 Linux 打包...")
        
        spec_file = "build.spec" if Path("build.spec").exists() else "build_macos.spec"
        if not Path(spec_file).exists():
            log_error(f"错误: 找不到 {spec_file}")
            sys.exit(1)
        
        # 打包
        log_warn("执行 PyInstaller 打包...")
        # 获取日志级别（默认为 INFO，可以通过环境变量覆盖）
        log_level = os.environ.get("PYINSTALLER_LOG_LEVEL", "INFO")
        
        try:
            check_cancel()  # 检查是否请求取消
            # 使用实时输出而不是 capture_output，以便能够看到进度并响应取消
            log_info("开始 PyInstaller 打包（实时输出）...")
            result = subprocess.run([
                sys.executable, "-m", "PyInstaller",
                spec_file,
                "--clean",
                "--noconfirm",
                f"--log-level={log_level}"
            ], check=True, timeout=3600)  # 1小时超时，实时输出
            result = subprocess.CompletedProcess([], 0, "", "")  # 创建成功结果对象
        except subprocess.TimeoutExpired:
            log_error("PyInstaller 执行超时（超过1小时）")
            raise
        except KeyboardInterrupt:
            log_warn("PyInstaller 打包被用户取消")
            raise
        except subprocess.CalledProcessError as e:
            log_error(f"PyInstaller 执行失败，退出码: {e.returncode}")
            # 错误信息已经在实时输出中显示，直接抛出异常
            raise
        
        # 查找生成的可执行文件
        exe_name = app_name.replace(" ", "_")
        exe_path = None
        for possible_name in [app_name, exe_name, f"{app_name}.bin"]:
            possible_path = Path("dist") / possible_name
            if possible_path.exists():
                exe_path = possible_path
                break
        
        if not exe_path:
            log_error("错误: 可执行文件未生成")
            sys.exit(1)
        
        log_info(f"✓ 可执行文件生成成功: {exe_path}")
        
        # 创建 .deb 安装包
        log_warn("创建 .deb 安装包...")
        deb_name = app_name.lower().replace(" ", "-")
        deb_dir = Path("dist") / f"{deb_name}_deb"
        if deb_dir.exists():
            shutil.rmtree(deb_dir)
        
        # 创建 DEBIAN 目录结构
        deb_dir.mkdir(parents=True)
        debian_dir = deb_dir / "DEBIAN"
        debian_dir.mkdir()
        
        # 创建 control 文件
        control_file = debian_dir / "control"
        with open(control_file, "w", encoding="utf-8") as f:
            f.write(f'''Package: {deb_name}
Version: 1.0.0
Section: utils
Priority: optional
Architecture: amd64
Depends: libc6 (>= 2.17)
Maintainer: Ai Perf <support@sanying.site>
Description: {app_name}
 {app_name} - Performance management application
''')
        
        # 创建 usr/bin 目录并复制可执行文件
        usr_bin = deb_dir / "usr" / "bin"
        usr_bin.mkdir(parents=True)
        shutil.copy2(exe_path, usr_bin / exe_name)
        os.chmod(usr_bin / exe_name, 0o755)
        
        # 创建应用程序目录（可选）
        usr_share = deb_dir / "usr" / "share" / deb_name
        usr_share.mkdir(parents=True)
        
        # 使用 dpkg-deb 创建 .deb 包
        deb_package = Path("dist") / f"{deb_name}_1.0.0_amd64.deb"
        try:
            subprocess.run([
                "dpkg-deb",
                "--build",
                str(deb_dir),
                str(deb_package)
            ], check=True)
            log_info(f"  ✓ .deb 安装包创建成功: {deb_package}")
        except subprocess.CalledProcessError as e:
            log_error(f"  ✗ 创建 .deb 失败: {e}")
            log_warn("  ⚠ 请确保已安装 dpkg-deb")
        except FileNotFoundError:
            log_warn("  ⚠ dpkg-deb 未找到，跳过 .deb 创建")
            log_warn("    请安装: sudo apt-get install dpkg-dev")
        finally:
            if deb_dir.exists():
                shutil.rmtree(deb_dir)
        
        # 创建 .rpm 安装包
        log_warn("创建 .rpm 安装包...")
        rpm_name = deb_name
        rpm_dir = Path("dist") / f"{rpm_name}_rpm"
        if rpm_dir.exists():
            shutil.rmtree(rpm_dir)
        
        # 创建 RPM 目录结构
        rpm_dir.mkdir(parents=True)
        rpmbuild_dir = rpm_dir / "rpmbuild"
        rpmbuild_dir.mkdir()
        for subdir in ["BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"]:
            (rpmbuild_dir / subdir).mkdir()
        
        # 创建 spec 文件
        spec_file_rpm = rpmbuild_dir / "SPECS" / f"{rpm_name}.spec"
        with open(spec_file_rpm, "w", encoding="utf-8") as f:
            f.write(f'''Name: {rpm_name}
Version: 1.0.0
Release: 1%{{?dist}}
Summary: {app_name}
License: Proprietary
Group: Applications/System
Source0: {exe_name}
BuildArch: x86_64

%description
{app_name} - Performance management application

%prep
# No prep needed

%build
# No build needed

%install
mkdir -p %{{buildroot}}/usr/bin
cp %{{SOURCE0}} %{{buildroot}}/usr/bin/{exe_name}
chmod 755 %{{buildroot}}/usr/bin/{exe_name}

%files
/usr/bin/{exe_name}

%changelog
* {datetime.now().strftime("%a %b %d %Y")} Ai Perf <support@sanying.site> - 1.0.0-1
- Initial release
''')
        
        # 复制源文件到 SOURCES
        shutil.copy2(exe_path, rpmbuild_dir / "SOURCES" / exe_name)
        os.chmod(rpmbuild_dir / "SOURCES" / exe_name, 0o755)
        
        # 使用 rpmbuild 创建 .rpm 包
        rpm_package = Path("dist") / f"{rpm_name}-1.0.0-1.x86_64.rpm"
        try:
            subprocess.run([
                "rpmbuild",
                "--define", f"_topdir {rpmbuild_dir.absolute()}",
                "-bb",
                str(spec_file_rpm)
            ], check=True)
            
            # 查找生成的 RPM 文件
            generated_rpm = list((rpmbuild_dir / "RPMS" / "x86_64").glob("*.rpm"))
            if generated_rpm:
                shutil.copy2(generated_rpm[0], rpm_package)
                log_info(f"  ✓ .rpm 安装包创建成功: {rpm_package}")
            else:
                log_warn("  ⚠ RPM 文件未生成")
        except subprocess.CalledProcessError as e:
            log_error(f"  ✗ 创建 .rpm 失败: {e}")
            log_warn("  ⚠ 请确保已安装 rpmbuild")
        except FileNotFoundError:
            log_warn("  ⚠ rpmbuild 未找到，跳过 .rpm 创建")
            log_warn("    请安装: sudo yum install rpm-build 或 sudo apt-get install rpm")
        finally:
            if rpm_dir.exists():
                shutil.rmtree(rpm_dir)
        
    else:
        log_error(f"错误: 不支持的平台: {platform}")
        print("支持的平台: macos, windows, linux")
        sys.exit(1)
    
    # 恢复 config.json（如果之前修改过）
    if config_modified and config_backup.exists():
        log_warn("恢复 config.json...")
        if config_existed_before:
            # 如果原来有文件，恢复备份
            shutil.move(config_backup, config_file)
            log_info("✓ config.json 已恢复")
        else:
            # 如果原来没有文件，删除创建的文件和备份
            if config_file.exists():
                config_file.unlink()
            config_backup.unlink()
            log_info("✓ 已删除临时创建的 config.json")
    
    # 恢复 google_client_secret.json（如果之前修改过）
    # 注意：如果是从环境变量创建的，且原来没有文件，不删除（因为这是打包需要的）
    if google_secret_modified:
        if google_secret_backup.exists():
            # 如果原来有文件，恢复备份
            log_warn("恢复 google_client_secret.json...")
            if google_secret_file.exists():
                google_secret_file.unlink()
            shutil.move(google_secret_backup, google_secret_file)
            log_info("✓ google_client_secret.json 已恢复")
        elif not google_secret_existed_before and not google_secret_from_env:
            # 如果原来没有文件，且不是从环境变量创建的，删除创建的占位文件
            log_warn("删除临时创建的 google_client_secret.json...")
            if google_secret_file.exists():
                google_secret_file.unlink()
            log_info("✓ 已删除临时创建的 google_client_secret.json")
        # 如果是从环境变量创建的，且原来没有文件，保留文件（因为这是打包需要的）
    
    elapsed_time = time.time() - start_time
    print()
    log_info("========================================")
    log_info("打包完成！")
    log_info(f"总耗时: {elapsed_time:.2f} 秒 ({elapsed_time/60:.2f} 分钟)")
    log_info("========================================")
    print()
    print(f"输出文件在: {client_dir / 'dist'}")


if __name__ == "__main__":
    main()


