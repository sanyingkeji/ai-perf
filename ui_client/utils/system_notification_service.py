#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统通知服务管理器
用于安装、启用、禁用、卸载后台通知服务（macOS LaunchAgent / Windows 任务计划程序）
"""

import sys
import platform
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from utils.config_manager import CONFIG_PATH


def _get_subprocess_kwargs() -> dict:
    """获取 subprocess 调用的参数，Windows 上避免弹出命令行窗口"""
    kwargs = {}
    if platform.system() == "Windows":
        # Windows: 使用 CREATE_NO_WINDOW 标志避免弹出命令行窗口
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


class SystemNotificationService:
    """系统通知服务管理器"""

    # 后台通知运行模式：由客户端可执行文件自行执行一次检查并退出（不拉起 UI）
    # 该参数由 `ui_client/main.py` 解析处理
    BACKGROUND_FLAG = "--run-background-notification-service"
    
    def __init__(self):
        self.system = platform.system()
        self.project_root = Path(__file__).resolve().parents[2]
        self.user_data_dir = CONFIG_PATH.parent
        
        # macOS LaunchAgent 配置
        self.macos_label = "site.sanying.aiperf.notification"
        self.macos_plist_path = Path.home() / "Library" / "LaunchAgents" / f"{self.macos_label}.plist"
        
        # Windows 任务计划程序配置
        self.windows_task_name = "AiPerfNotificationService"
        
        # 后台服务脚本路径（需要根据实际打包后的路径确定）
        self._service_script_path = None

    @staticmethod
    def _is_frozen_app() -> bool:
        """是否为打包后的应用（PyInstaller）。"""
        return hasattr(sys, "frozen") and bool(getattr(sys, "frozen", False))
    
    def _get_service_script_path(self) -> Optional[Path]:
        """获取后台服务脚本路径"""
        if self._service_script_path:
            return self._service_script_path
        
        # 判断是否在打包后的应用中
        is_frozen = hasattr(sys, 'frozen') and sys.frozen
        
        # 尝试多个可能的路径
        possible_paths = []
        
        if is_frozen:
            # 打包后的应用
            if self.system == "Darwin":
                # macOS: 应用包内 Resources 目录
                # 从可执行文件路径推导应用包路径
                exe_path = Path(sys.executable)
                if exe_path.parts[-3:] == ('Contents', 'MacOS', 'Ai Perf Client'):
                    # 标准应用包结构
                    app_bundle = exe_path.parent.parent.parent
                    possible_paths.append(app_bundle / "Contents" / "Resources" / "scripts" / "notification_background_service.py")
                # 标准应用包路径（如果应用安装在 /Applications）
                possible_paths.append(Path("/Applications/Ai Perf Client.app/Contents/Resources/scripts/notification_background_service.py"))
            elif self.system == "Windows":
                # Windows: 应用目录
                exe_dir = Path(sys.executable).parent
                # PyInstaller onedir 模式：脚本在 exe 同目录下的 scripts 子目录
                possible_paths.append(exe_dir / "scripts" / "notification_background_service.py")
                # 尝试从 sys._MEIPASS 获取（PyInstaller 临时目录，运行时使用）
                if hasattr(sys, '_MEIPASS'):
                    meipass_path = Path(sys._MEIPASS) / "scripts" / "notification_background_service.py"
                    possible_paths.append(meipass_path)
                # 常见安装位置
                possible_paths.extend([
                    Path.home() / "AppData" / "Local" / "Ai Perf Client" / "scripts" / "notification_background_service.py",
                    Path("C:/Program Files/Ai Perf Client/scripts/notification_background_service.py"),
                    Path("C:/Program Files (x86)/Ai Perf Client/scripts/notification_background_service.py"),
                ])
        else:
            # 开发环境
            possible_paths.append(self.project_root / "scripts" / "notification_background_service.py")
        
        # 通用路径（无论是否打包）
        possible_paths.extend([
            # 用户配置目录
            self.user_data_dir / "scripts" / "notification_background_service.py",
            # 当前可执行文件目录
            Path(sys.executable).parent / "scripts" / "notification_background_service.py",
        ])
        
        # 如果是在打包后的应用中，也尝试使用 resource_path 工具
        if is_frozen:
            try:
                from utils.resource_path import get_resource_path
                resource_path = get_resource_path("scripts/notification_background_service.py")
                if resource_path.exists():
                    possible_paths.insert(0, resource_path)  # 优先级最高
            except Exception:
                pass
        
        for path in possible_paths:
            if path and path.exists():
                self._service_script_path = path
                return path
        
        # 如果找不到，返回开发环境的路径（用于开发测试）
        dev_path = self.project_root / "scripts" / "notification_background_service.py"
        if dev_path.exists():
            return dev_path
        
        return None
    
    def _get_python_executable(self) -> str:
        """获取 Python 可执行文件路径"""
        if self.system == "Darwin":
            # macOS: 尝试使用系统 Python 或应用内 Python
            # 如果是打包后的应用，可能需要使用应用内的 Python
            if hasattr(sys, 'frozen') and sys.frozen:
                # PyInstaller 打包的应用
                return sys.executable
            else:
                # 开发环境或普通 Python
                return sys.executable
        elif self.system == "Windows":
            # Windows: 使用 pythonw.exe（无窗口版本）避免弹出命令行窗口
            return self._get_pythonw_executable()
        else:
            return sys.executable
    
    def _get_pythonw_executable(self) -> str:
        """获取 Windows 无窗口版本的 Python 可执行文件路径（pythonw.exe）"""
        python_exe = sys.executable
        python_path = Path(python_exe)
        
        # 如果是打包后的应用（PyInstaller），可执行文件本身就是无窗口的
        is_frozen = hasattr(sys, 'frozen') and sys.frozen
        if is_frozen:
            # 打包后的应用，直接使用可执行文件（已经是无窗口的）
            # 但需要确保可执行文件确实是 .exe 文件（Windows）
            if python_path.suffix.lower() == '.exe':
                return python_exe
            # 如果不是 .exe 文件，继续查找 pythonw.exe
        
        # 开发环境：尝试找到 pythonw.exe
        # 如果已经是 pythonw.exe，直接返回
        if python_path.name.lower() == "pythonw.exe":
            return python_exe
        
        # 尝试找到 pythonw.exe
        # 通常 pythonw.exe 和 python.exe 在同一目录
        pythonw_path = python_path.parent / "pythonw.exe"
        if pythonw_path.exists():
            return str(pythonw_path)
        
        # 如果在 Scripts 目录（虚拟环境），向上一级查找
        if python_path.parent.name.lower() == "scripts":
            pythonw_path = python_path.parent.parent / "pythonw.exe"
            if pythonw_path.exists():
                return str(pythonw_path)
        
        # 如果找不到 pythonw.exe，尝试查找 pythonw.exe 在系统路径中
        import shutil
        pythonw_system = shutil.which("pythonw.exe")
        if pythonw_system:
            return pythonw_system
        
        # 最后回退：如果确实找不到 pythonw.exe，使用 python.exe
        # 但记录警告（这会导致弹出命令行窗口）
        print(f"[WARNING] 未找到 pythonw.exe，将使用 python.exe（可能会弹出命令行窗口）")
        return python_exe
    
    def is_installed(self) -> bool:
        """检查服务是否已安装"""
        if self.system == "Darwin":
            return self.macos_plist_path.exists()
        elif self.system == "Windows":
            try:
                result = subprocess.run(
                    ["schtasks", "/query", "/tn", self.windows_task_name],
                    capture_output=True,
                    timeout=5,
                    **_get_subprocess_kwargs()
                )
                return result.returncode == 0
            except Exception:
                return False
        else:
            return False
    
    def is_configuration_valid(self) -> bool:
        """检查已安装的服务配置是否正确（用于覆盖安装时验证）"""
        if not self.is_installed():
            return False

        # 说明：
        # - 开发环境：通常由 python + scripts/notification_background_service.py 组成
        # - 打包环境：优先使用 “可执行文件 + BACKGROUND_FLAG” 组成（避免把 App 当 python 去跑导致每分钟拉起窗口）
        script_path = self._get_service_script_path()
        
        if self.system == "Windows":
            try:
                # 查询任务详情，检查脚本路径是否匹配
                result = subprocess.run(
                    ["schtasks", "/query", "/tn", self.windows_task_name, "/xml"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_get_subprocess_kwargs()
                )
                if result.returncode == 0:
                    # 检查 XML 中是否包含当前脚本路径
                    xml_content = result.stdout
                    xml_lower = (xml_content or "").lower()

                    # 新版：如果任务参数里包含后台模式参数，则认为配置有效
                    if self.BACKGROUND_FLAG.lower() in xml_lower:
                        return True
                    
                    # 规范化路径进行比较（处理不同的路径格式）
                    if not script_path:
                        return False
                    script_path_normalized = str(script_path).replace("\\", "/").lower()
                    script_path_backslash = str(script_path).lower()
                    script_path_escaped = str(script_path).replace("\\", "\\\\").lower()
                    # 也检查文件名（更宽松的匹配）
                    script_filename = script_path.name.lower()

                    # 检查脚本路径是否在任务配置中（多种格式）
                    # 如果路径匹配失败，至少检查文件名是否匹配（更宽松）
                    path_match = (script_path_normalized in xml_lower or 
                            script_path_backslash in xml_lower or 
                            script_path_escaped in xml_lower)
                    filename_match = script_filename in xml_lower
                    
                    # 如果路径或文件名匹配，认为配置有效
                    if path_match or filename_match:
                        return True
                    
                    # 如果都不匹配，但任务存在且已启用，也认为配置可能有效
                    # （可能是路径格式问题，但任务本身是有效的）
                    if self.is_enabled():
                        return True
                    
                    return False
                return False
            except Exception:
                # 如果检查失败，但任务存在，认为配置可能有效（避免误判）
                return self.is_enabled()
        elif self.system == "Darwin":
            try:
                # 读取 plist 文件，检查 ProgramArguments
                # plistlib 是 Python 标准库，所有平台都可用
                import plistlib
                with open(self.macos_plist_path, 'rb') as f:
                    plist_data = plistlib.load(f)
                program_args = plist_data.get("ProgramArguments", [])

                if not isinstance(program_args, list):
                    return False

                # 新版：如果包含后台模式参数，认为配置有效
                if any(str(x) == self.BACKGROUND_FLAG for x in program_args):
                    return True

                # 若是“App 可执行文件 + 脚本路径”的旧配置（会导致每分钟拉起窗口），视为无效
                try:
                    if self._is_frozen_app() and len(program_args) >= 1:
                        exe0 = program_args[0]
                        if exe0 and Path(str(exe0)).resolve() == Path(sys.executable).resolve():
                            return False
                except Exception:
                    # 无法解析路径时，不强行判 invalid，继续走脚本匹配逻辑
                    pass

                # 兼容旧版/手动安装：python3 + notification_background_service.py（不要求精确路径）
                if any(str(x).endswith("notification_background_service.py") for x in program_args):
                    return True

                # 更严格：若脚本路径可解析，尝试精确匹配
                if script_path and len(program_args) >= 2:
                    installed_script = program_args[1]
                    try:
                        return str(script_path) == installed_script or Path(installed_script).resolve() == script_path.resolve()
                    except Exception:
                        return str(script_path) == str(installed_script)

                return False
            except Exception:
                return False
        else:
            return False
    
    def is_enabled(self) -> bool:
        """检查服务是否已启用"""
        if not self.is_installed():
            return False
        
        if self.system == "Darwin":
            try:
                result = subprocess.run(
                    ["launchctl", "list", self.macos_label],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False
        elif self.system == "Windows":
            try:
                result = subprocess.run(
                    ["schtasks", "/query", "/tn", self.windows_task_name, "/fo", "list"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_get_subprocess_kwargs()
                )
                if result.returncode == 0:
                    # 检查任务状态（Enabled 或 Disabled）
                    output = result.stdout.lower()
                    return "disabled" not in output
                return False
            except Exception:
                return False
        else:
            return False
    
    def install(self, force_reinstall: bool = False) -> Tuple[bool, str]:
        """
        安装后台服务
        
        Args:
            force_reinstall: 如果为 True，即使服务已安装也会重新安装（用于覆盖安装）
        
        Returns:
            (成功标志, 错误消息)
        """
        is_frozen = self._is_frozen_app()
        script_path = self._get_service_script_path()

        # 打包环境下（特别是 macOS），后台服务可以通过“可执行文件 + BACKGROUND_FLAG”运行，
        # 不强依赖脚本文件路径，避免把 App 当作 python 解释器执行导致拉起窗口。
        if not script_path and not (is_frozen and self.system in ("Darwin", "Windows")):
            return False, "找不到后台服务脚本文件"
        
        # 如果服务已安装，检查配置是否正确
        if self.is_installed():
            if not force_reinstall:
                # 检查配置是否正确
                if self.is_configuration_valid():
                    # 配置正确，不需要重新安装
                    return True, "服务已安装且配置正确"
                else:
                    # 配置不正确，需要重新安装
                    force_reinstall = True
        
        # 如果需要强制重新安装，先卸载旧服务
        if force_reinstall and self.is_installed():
            uninstall_success, uninstall_msg = self.uninstall()
            if not uninstall_success:
                return False, f"卸载旧服务失败: {uninstall_msg}"
        
        if self.system == "Darwin":
            return self._install_macos(script_path)
        elif self.system == "Windows":
            return self._install_windows(script_path)
        else:
            return False, f"不支持的操作系统: {self.system}"
    
    def _install_macos(self, script_path: Optional[Path]) -> Tuple[bool, str]:
        """安装 macOS LaunchAgent"""
        try:
            is_frozen = self._is_frozen_app()
            python_exe = self._get_python_executable()
            
            # 创建 plist 文件
            log_dir = self.user_data_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_log = str(log_dir / 'notification_service.log')
            stderr_log = str(log_dir / 'notification_service_error.log')

            # ProgramArguments：
            # - 打包环境：直接运行 App 可执行文件，并通过 BACKGROUND_FLAG 进入后台任务模式
            # - 开发环境：python + notification_background_service.py --once
            if is_frozen and self.system == "Darwin":
                program_args = [sys.executable, self.BACKGROUND_FLAG]
            else:
                if not script_path:
                    return False, "找不到后台服务脚本文件"
                program_args = [python_exe, str(script_path), "--once"]

            program_args_xml = "\n".join([f"        <string>{arg}</string>" for arg in program_args])
            
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.macos_label}</string>
    <key>ProgramArguments</key>
    <array>
{program_args_xml}
    </array>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>"""
            
            # 确保目录存在
            self.macos_plist_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 写入 plist 文件
            with open(self.macos_plist_path, 'w', encoding='utf-8') as f:
                f.write(plist_content)
            
            # 加载服务
            result = subprocess.run(
                ["launchctl", "load", str(self.macos_plist_path)],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                # 如果服务已存在，先卸载再加载
                subprocess.run(
                    ["launchctl", "unload", str(self.macos_plist_path)],
                    capture_output=True,
                    timeout=5
                )
                result = subprocess.run(
                    ["launchctl", "load", str(self.macos_plist_path)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            
            if result.returncode == 0:
                return True, ""
            else:
                return False, f"加载 LaunchAgent 失败: {result.stderr}"
        
        except Exception as e:
            return False, f"安装 macOS 服务失败: {str(e)}"
    
    def _install_windows(self, script_path: Optional[Path]) -> Tuple[bool, str]:
        """安装 Windows 任务计划程序任务"""
        try:
            python_exe = self._get_python_executable()
            # 验证并确保使用 pythonw.exe（避免弹出命令行窗口）
            python_exe_path = Path(python_exe)
            python_exe_name = python_exe_path.name.lower()
            
            # 检查是否已经是 pythonw.exe 或打包后的应用（无窗口）
            is_pythonw = python_exe_name == "pythonw.exe"
            is_frozen_app = hasattr(sys, 'frozen') and sys.frozen and python_exe_path.suffix.lower() == '.exe'
            
            if not is_pythonw and not is_frozen_app:
                # 如果不是 pythonw.exe 且不是打包后的应用，强制查找 pythonw.exe
                import shutil
                # 尝试多种方式查找 pythonw.exe
                pythonw_candidates = []
                
                # 同目录查找
                candidate = python_exe_path.parent / "pythonw.exe"
                if candidate.exists():
                    pythonw_candidates.append(str(candidate))
                
                # 虚拟环境查找
                if python_exe_path.parent.name.lower() == "scripts":
                    candidate = python_exe_path.parent.parent / "pythonw.exe"
                    if candidate.exists():
                        pythonw_candidates.append(str(candidate))
                
                # 系统路径查找
                pythonw_system = shutil.which("pythonw.exe")
                if pythonw_system:
                    pythonw_candidates.append(pythonw_system)
                
                # 使用找到的第一个 pythonw.exe
                if pythonw_candidates:
                    python_exe = pythonw_candidates[0]
                    print(f"[INFO] 找到并使用 pythonw.exe: {python_exe}")
                else:
                    print(f"[WARNING] 未找到 pythonw.exe，使用: {python_exe}（可能会弹出命令行窗口）")
            
            # 如果任务已存在，先删除（确保完全删除，避免重复任务）
            if self.is_installed():
                # 先禁用任务
                try:
                    subprocess.run(
                        ["schtasks", "/change", "/tn", self.windows_task_name, "/disable"],
                        capture_output=True,
                        timeout=5,
                        **_get_subprocess_kwargs()
                    )
                except Exception:
                    pass
                # 删除任务
                uninstall_success, uninstall_msg = self.uninstall()
                if not uninstall_success:
                    # 如果卸载失败，尝试强制删除
                    try:
                        subprocess.run(
                            ["schtasks", "/delete", "/tn", self.windows_task_name, "/f"],
                            capture_output=True,
                            timeout=10,
                            **_get_subprocess_kwargs()
                        )
                    except Exception:
                        pass
            
            # 任务执行内容：
            # - 打包环境：直接运行客户端可执行文件，并通过 BACKGROUND_FLAG 进入后台模式
            # - 开发环境：pythonw.exe + notification_background_service.py --once
            if is_frozen_app:
                task_command = python_exe
                task_arguments = self.BACKGROUND_FLAG
            else:
                if not script_path:
                    return False, "找不到后台服务脚本文件"
                task_command = python_exe
                task_arguments = f"\"{str(script_path)}\" --once"

            # 创建任务（每1分钟运行一次）
            # 使用 XML 方式创建任务，更可靠
            xml_content = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}</Date>
    <Author>Ai Perf Client</Author>
    <Description>Ai Perf 后台通知服务 - 定期检查并发送系统通知</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>"{task_command}"</Command>
      <Arguments>{task_arguments}</Arguments>
    </Exec>
  </Actions>
</Task>"""
            
            # 创建临时 XML 文件
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-16') as f:
                f.write(xml_content)
                xml_file = f.name
            
            try:
                # 再次确认任务不存在（双重检查）
                if self.is_installed():
                    # 如果任务仍然存在，再次尝试删除
                    try:
                        subprocess.run(
                            ["schtasks", "/delete", "/tn", self.windows_task_name, "/f"],
                            capture_output=True,
                            timeout=10,
                            **_get_subprocess_kwargs()
                        )
                        import time
                        time.sleep(0.5)  # 等待任务完全删除
                    except Exception:
                        pass
                
                # 使用 schtasks 创建任务
                result = subprocess.run(
                    ["schtasks", "/create", "/tn", self.windows_task_name, "/xml", xml_file, "/f"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    **_get_subprocess_kwargs()
                )
                
                if result.returncode == 0:
                    # 验证任务是否创建成功
                    if self.is_installed():
                        return True, ""
                    else:
                        return False, "任务创建后验证失败"
                else:
                    return False, f"创建任务失败: {result.stderr}"
            finally:
                # 删除临时 XML 文件
                try:
                    Path(xml_file).unlink()
                except Exception:
                    pass
        
        except Exception as e:
            return False, f"安装 Windows 服务失败: {str(e)}"
    
    def enable(self) -> Tuple[bool, str]:
        """启用服务"""
        if not self.is_installed():
            return self.install()
        
        if self.system == "Darwin":
            try:
                result = subprocess.run(
                    ["launchctl", "load", str(self.macos_plist_path)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return True, ""
                else:
                    # 如果已加载，忽略错误
                    if "already loaded" in result.stderr.lower():
                        return True, ""
                    return False, f"启用服务失败: {result.stderr}"
            except Exception as e:
                return False, f"启用服务失败: {str(e)}"
        elif self.system == "Windows":
            try:
                result = subprocess.run(
                    ["schtasks", "/change", "/tn", self.windows_task_name, "/enable"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **_get_subprocess_kwargs()
                )
                if result.returncode == 0:
                    return True, ""
                else:
                    return False, f"启用服务失败: {result.stderr}"
            except Exception as e:
                return False, f"启用服务失败: {str(e)}"
        else:
            return False, f"不支持的操作系统: {self.system}"
    
    def disable(self) -> Tuple[bool, str]:
        """禁用服务"""
        if not self.is_installed():
            return True, ""
        
        if self.system == "Darwin":
            try:
                result = subprocess.run(
                    ["launchctl", "unload", str(self.macos_plist_path)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return True, ""
                else:
                    # 如果未加载，忽略错误
                    if "could not find specified service" in result.stderr.lower():
                        return True, ""
                    return False, f"禁用服务失败: {result.stderr}"
            except Exception as e:
                return False, f"禁用服务失败: {str(e)}"
        elif self.system == "Windows":
            try:
                result = subprocess.run(
                    ["schtasks", "/change", "/tn", self.windows_task_name, "/disable"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **_get_subprocess_kwargs()
                )
                if result.returncode == 0:
                    return True, ""
                else:
                    return False, f"禁用服务失败: {result.stderr}"
            except Exception as e:
                return False, f"禁用服务失败: {str(e)}"
        else:
            return False, f"不支持的操作系统: {self.system}"
    
    def uninstall(self) -> Tuple[bool, str]:
        """卸载服务"""
        if not self.is_installed():
            return True, ""
        
        # 先禁用
        self.disable()
        
        if self.system == "Darwin":
            try:
                # 卸载 LaunchAgent
                subprocess.run(
                    ["launchctl", "unload", str(self.macos_plist_path)],
                    capture_output=True,
                    timeout=5
                )
                # 删除 plist 文件
                if self.macos_plist_path.exists():
                    self.macos_plist_path.unlink()
                return True, ""
            except Exception as e:
                return False, f"卸载服务失败: {str(e)}"
        elif self.system == "Windows":
            try:
                # 先尝试停止正在运行的任务
                try:
                    subprocess.run(
                        ["schtasks", "/end", "/tn", self.windows_task_name],
                        capture_output=True,
                        timeout=5,
                        **_get_subprocess_kwargs()
                    )
                except Exception:
                    pass
                
                # 删除任务
                result = subprocess.run(
                    ["schtasks", "/delete", "/tn", self.windows_task_name, "/f"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    **_get_subprocess_kwargs()
                )
                if result.returncode == 0:
                    # 等待一下，确保任务完全删除
                    import time
                    time.sleep(0.5)
                    return True, ""
                else:
                    # 即使删除失败，也检查任务是否真的不存在了
                    if not self.is_installed():
                        return True, ""  # 任务已经不存在，认为卸载成功
                    return False, f"卸载服务失败: {result.stderr}"
            except Exception as e:
                # 检查任务是否已经不存在
                if not self.is_installed():
                    return True, ""  # 任务已经不存在，认为卸载成功
                return False, f"卸载服务失败: {str(e)}"
        else:
            return False, f"不支持的操作系统: {self.system}"
    
    def get_status(self) -> Dict[str, Any]:
        """获取服务状态"""
        return {
            "installed": self.is_installed(),
            "enabled": self.is_enabled(),
            "system": self.system,
        }


# 便捷函数
def install_background_service() -> Tuple[bool, str]:
    """安装后台通知服务"""
    service = SystemNotificationService()
    return service.install()


def enable_background_service() -> Tuple[bool, str]:
    """启用后台通知服务"""
    service = SystemNotificationService()
    return service.enable()


def disable_background_service() -> Tuple[bool, str]:
    """禁用后台通知服务"""
    service = SystemNotificationService()
    return service.disable()


def uninstall_background_service() -> Tuple[bool, str]:
    """卸载后台通知服务"""
    service = SystemNotificationService()
    return service.uninstall()


def get_background_service_status() -> Dict[str, Any]:
    """获取后台服务状态"""
    service = SystemNotificationService()
    return service.get_status()


if __name__ == "__main__":
    # 测试代码
    service = SystemNotificationService()
    print(f"系统: {service.system}")
    print(f"已安装: {service.is_installed()}")
    print(f"已启用: {service.is_enabled()}")
    
    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == "install":
            success, msg = service.install()
            print(f"安装: {'成功' if success else '失败'} - {msg}")
        elif action == "enable":
            success, msg = service.enable()
            print(f"启用: {'成功' if success else '失败'} - {msg}")
        elif action == "disable":
            success, msg = service.disable()
            print(f"禁用: {'成功' if success else '失败'} - {msg}")
        elif action == "uninstall":
            success, msg = service.uninstall()
            print(f"卸载: {'成功' if success else '失败'} - {msg}")

