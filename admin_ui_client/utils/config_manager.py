import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"

DEFAULT_CONFIG = {
    "api_base": "http://127.0.0.1:8880",  # 管理端API端口
    # 通过 Google 登录获取到的 ID Token，仅用于调试 / 排查
    "google_id_token": "",
    # 后端 /admin/auth/google_login 签发的会话 token，用于真正调用 /admin/api/*
    "session_token": "",
    "user_id": "",
    "user_name": "",
    "user_email": "",
    "theme": "auto",        # auto / light / dark
    "auto_refresh": True,
    "notifications": True,
    "client_version": "1.0.0",  # 客户端版本号（格式：x.x.x）
    "update_dialog_dismissed_date": "",  # 非强制升级弹窗关闭的日期（格式：YYYY-MM-DD），用于当天不再弹出
    # SSH 配置（用于服务管理）
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_username": "",
    "ssh_password": "",  # 密码或密钥文件路径
    "ssh_key_path": "",  # SSH密钥文件路径（可选，如果使用密钥认证）
    # 文件上传服务配置
    "upload_api_url": "http://127.0.0.1:8882/api/upload",  # 文件上传API地址（用于上传文件）
    # OpenAI Session Key（用于获取余额信息，从浏览器中获取）
    "openai_session_key": "",  # OpenAI Dashboard session key（从浏览器 Cookie 中获取）
}

class ConfigManager:
    @staticmethod
    def load() -> dict:
        """读取配置，如果不存在则创建默认配置。"""
        if not CONFIG_PATH.exists():
            ConfigManager.save(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config.json 格式错误")

            # 补全缺失字段
            changed = False
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
                    changed = True
            if changed:
                ConfigManager.save(data)
            return data
        except Exception:
            # 出错则恢复默认
            ConfigManager.save(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()

    @staticmethod
    def save(data: dict):
        """保存配置"""
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

