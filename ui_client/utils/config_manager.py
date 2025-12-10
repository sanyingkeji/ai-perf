import copy
import json
import os
import platform
from pathlib import Path

APP_NAME = "ai-perf"
LEGACY_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def _get_user_config_path() -> Path:
    """返回当前平台用户配置路径，避免随应用升级被覆盖。"""
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA")
        base_path = Path(base) if base else home / "AppData" / "Roaming"
    elif system == "Darwin":
        base_path = home / "Library" / "Application Support"
    else:
        base_path = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base_path / APP_NAME / "config.json"


CONFIG_PATH = _get_user_config_path()
CURRENT_CONFIG_VERSION = 2

DEFAULT_CONFIG = {
    "api_base": "http://127.0.0.1:8000",
    # 通过 Google 登录获取到的 ID Token，仅用于调试 / 排查
    "google_id_token": "",
    # 后端 /auth/google_login 签发的会话 token，用于真正调用 /api/*
    "session_token": "",
    "user_id": "",
    "user_name": "",
    "user_email": "",
    "theme": "auto",        # auto / light / dark
    "auto_refresh": True,
    "notifications": True,
    # 日志保留时长（小时），默认仅保留最近 1 小时
    "log_retention_hours": 1,
    "client_version": "1.1.0",  # 客户端版本号（格式：x.x.x）
    "update_dialog_dismissed_date": "",  # 非强制升级弹窗关闭的日期（格式：YYYY-MM-DD），用于当天不再弹出
    # 隔空投送可被发现范围：all | group | none
    "airdrop_discover_scope": "all",
    # 配置架构版本，用于迁移
    "config_version": CURRENT_CONFIG_VERSION,
}


def _deep_merge_defaults(target: dict, defaults: dict) -> bool:
    """递归补充缺失的默认值，不覆盖已有用户配置。"""
    changed = False
    for key, default_value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(default_value)
            changed = True
        elif isinstance(default_value, dict) and isinstance(target.get(key), dict):
            if _deep_merge_defaults(target[key], default_value):
                changed = True
    return changed


# 用户数据字段列表：这些字段应该保留用户的值，不从项目配置同步
USER_DATA_FIELDS = {
    "session_token",
    "google_id_token",
    "user_id",
    "user_name",
    "user_email",
    "update_dialog_dismissed_date",  # 用户交互数据
}


def _smart_merge_project_config(user_config: dict, template_config: dict) -> bool:
    """
    智能合并配置模板到用户配置。
    系统配置字段会同步更新，用户数据字段会保留用户的值。
    适用于开发模式和正式版本。
    
    Args:
        user_config: 用户配置字典（会被修改）
        template_config: 配置模板字典（项目 config.json 或 DEFAULT_CONFIG）
    
    Returns:
        是否有变化
    """
    changed = False
    
    for key, template_value in template_config.items():
        # 跳过配置版本号（由迁移系统处理）
        if key == "config_version":
            continue
        
        # 如果是用户数据字段，保留用户的值
        if key in USER_DATA_FIELDS:
            # 如果用户配置中没有这个字段，才从模板配置补充
            if key not in user_config:
                user_config[key] = copy.deepcopy(template_value)
                changed = True
            # 否则保留用户的值，不更新
            continue
        
        # 对于系统配置字段，如果模板中的值不同，则同步更新
        if key not in user_config:
            # 用户配置中没有，直接添加
            user_config[key] = copy.deepcopy(template_value)
            changed = True
        elif isinstance(template_value, dict) and isinstance(user_config.get(key), dict):
            # 递归处理嵌套字典
            if _smart_merge_project_config(user_config[key], template_value):
                changed = True
        elif user_config[key] != template_value:
            # 值不同，同步更新为模板配置的值
            user_config[key] = copy.deepcopy(template_value)
            changed = True
    
    return changed


def _migrate_v1_to_v2(data: dict) -> dict:
    """v1 -> v2 迁移：加入 config_version，保留用户自定义。"""
    data.setdefault("config_version", 1)
    data["config_version"] = 2
    return data


MIGRATIONS = {
    1: _migrate_v1_to_v2,
}


class ConfigManager:
    @staticmethod
    def load() -> dict:
        """
        读取配置，执行迁移并补全缺失字段。
        优先使用用户目录配置，如无则尝试从旧路径迁移。
        
        配置同步策略：
        - 系统配置字段（如 client_version, api_base 等）会自动同步更新
        - 用户数据字段（如 session_token, user_id 等）会保留用户的值
        - 开发者模式：优先使用项目中的 config.json 作为模板
        - 正式版本：使用硬编码的 DEFAULT_CONFIG 作为模板
        """
        data = ConfigManager._safe_read(CONFIG_PATH)
        migrated_from_legacy = False

        if data is None and LEGACY_CONFIG_PATH.exists():
            legacy_data = ConfigManager._safe_read(LEGACY_CONFIG_PATH)
            if isinstance(legacy_data, dict):
                data = legacy_data
                migrated_from_legacy = True

        changed = False
        if data is None:
            data = copy.deepcopy(DEFAULT_CONFIG)
            changed = True
        elif not isinstance(data, dict):
            data = {}
            changed = True

        # 确保存在版本号，便于迁移
        if "config_version" not in data or not isinstance(data.get("config_version"), int):
            data["config_version"] = 1
            changed = True

        # 按版本执行迁移
        current_version = data["config_version"]
        while current_version < CURRENT_CONFIG_VERSION:
            migration = MIGRATIONS.get(current_version)
            if migration is None:
                # 无匹配迁移，则直接跳到最新版本
                data["config_version"] = CURRENT_CONFIG_VERSION
                changed = True
                break
            data = migration(data)
            current_version = data.get("config_version", current_version + 1)
            changed = True

        # 智能合并配置：系统配置字段会同步更新，用户数据字段会保留用户的值
        # 优先使用项目中的 config.json（开发者模式），否则使用硬编码的 DEFAULT_CONFIG
        project_config = None
        if LEGACY_CONFIG_PATH.exists():
            project_config = ConfigManager._safe_read(LEGACY_CONFIG_PATH)
        
        # 使用项目配置或默认配置作为模板
        template_config = project_config if project_config else DEFAULT_CONFIG
        
        # 智能合并：系统配置同步更新，用户数据保留
        if _smart_merge_project_config(data, template_config):
            changed = True

        # 如需要，写回用户目录
        if changed or migrated_from_legacy or not CONFIG_PATH.exists():
            ConfigManager.save(data)

        return data

    @staticmethod
    def save(data: dict):
        """保存配置"""
        # 确保目录存在
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 使用临时文件 + 原子替换，确保写入安全
        temp_path = CONFIG_PATH.with_suffix('.json.tmp')
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()  # 确保数据写入缓冲区
                os.fsync(f.fileno())  # 强制同步到磁盘
            # 原子替换
            temp_path.replace(CONFIG_PATH)
        except Exception:
            # 如果临时文件方式失败，尝试直接写入（向后兼容）
            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()  # 确保数据写入缓冲区
                    os.fsync(f.fileno())  # 强制同步到磁盘
            except Exception:
                # 如果还是失败，至少尝试写入（不强制同步）
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
            finally:
                # 清理临时文件
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

    @staticmethod
    def _safe_read(path: Path) -> dict | None:
        """安全读取配置文件，出错返回 None。"""
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
