"""
资源路径工具
用于在开发环境和打包后的应用中正确获取资源文件路径
"""
import sys
import platform
from pathlib import Path


def get_resource_path(relative_path: str) -> Path:
    """
    获取资源文件路径（兼容开发环境和 PyInstaller 打包后的应用）
    
    Args:
        relative_path: 相对于项目根目录的资源文件路径，例如 "resources/app_icon.icns"
    
    Returns:
        资源文件的完整路径
    """
    # 判断是否在打包后的应用中
    is_frozen = hasattr(sys, 'frozen') and sys.frozen
    
    if is_frozen:
        # 打包后的应用
        if platform.system() == "Darwin":
            # macOS: 从应用包获取资源
            exe_path = Path(sys.executable)
            # 检查是否是标准应用包结构: Contents/MacOS/executable
            # 注意：exe_path.parts[-1] 是文件名，可能是 "Ai Perf Client" 或 "Ai Perf Admin"
            if len(exe_path.parts) >= 3:
                # 检查最后三个部分是否是 Contents/MacOS/executable
                if exe_path.parts[-3] == 'Contents' and exe_path.parts[-2] == 'MacOS':
                    # 标准应用包结构
                    app_bundle = exe_path.parent.parent.parent
                    resource_path = app_bundle / "Contents" / "Resources" / relative_path
                    if resource_path.exists():
                        return resource_path
                # 也尝试从当前路径向上查找应用包
                current = exe_path.parent
                while current != current.parent:  # 未到达根目录
                    if current.name.endswith('.app'):
                        resource_path = current / "Contents" / "Resources" / relative_path
                        if resource_path.exists():
                            return resource_path
                    current = current.parent
            # 尝试从 sys._MEIPASS 获取（PyInstaller 临时目录）
            if hasattr(sys, '_MEIPASS'):
                meipass_path = Path(sys._MEIPASS) / relative_path
                if meipass_path.exists():
                    return meipass_path
        elif platform.system() == "Windows":
            # Windows: 从应用目录获取
            exe_dir = Path(sys.executable).parent
            resource_path = exe_dir / relative_path
            if resource_path.exists():
                return resource_path
            # 尝试从 sys._MEIPASS 获取
            if hasattr(sys, '_MEIPASS'):
                meipass_path = Path(sys._MEIPASS) / relative_path
                if meipass_path.exists():
                    return meipass_path
        else:
            # Linux: 从应用目录获取
            exe_dir = Path(sys.executable).parent
            resource_path = exe_dir / relative_path
            if resource_path.exists():
                return resource_path
            # 尝试从 sys._MEIPASS 获取
            if hasattr(sys, '_MEIPASS'):
                meipass_path = Path(sys._MEIPASS) / relative_path
                if meipass_path.exists():
                    return meipass_path
    
    # 开发环境：从项目根目录获取
    # 假设这个文件在 ui_client/utils/ 目录下
    project_root = Path(__file__).resolve().parents[1]
    resource_path = project_root / relative_path
    return resource_path


def get_app_icon_path() -> Path | None:
    """
    获取应用图标路径（按优先级尝试不同格式）
    
    Returns:
        图标文件路径，如果不存在则返回 None
    """
    if platform.system() == "Darwin":
        # macOS: 优先使用 .icns
        icon_paths = [
            "resources/app_icon.icns",
            "resources/app_icon.png",
            "resources/app_icon.ico",
        ]
    elif platform.system() == "Windows":
        # Windows: 优先使用 .ico
        icon_paths = [
            "resources/app_icon.ico",
            "resources/app_icon.png",
            "resources/app_icon.icns",
        ]
    else:
        # Linux/其他: 优先使用 .png
        icon_paths = [
            "resources/app_icon.png",
            "resources/app_icon.ico",
            "resources/app_icon.icns",
        ]
    
    for icon_path in icon_paths:
        full_path = get_resource_path(icon_path)
        if full_path.exists():
            return full_path
    
    return None

