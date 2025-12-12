#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台通知“单次检查并发送”任务

设计目标：
1) 可被 LaunchAgent/计划任务周期性调用（建议每 60 秒一次）
2) 不创建 Qt 窗口、不激活前台，避免“自动拉起 App”
3) 复用现有 API（/api/notifications、/api/notifications/{id}/read）
4) 具备简单去重：持久化已发送通知 ID，避免重复弹出

注意：
- 这里尽量只使用标准库 + 项目已有的 ApiClient/ConfigManager
- macOS 下采用 osascript 发送系统通知（后台任务场景更稳，且不依赖 PyObjC/UI）
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Set

from utils.api_client import ApiClient
from utils.config_manager import CONFIG_PATH, ConfigManager


BACKGROUND_FLAG = "--run-background-notification-service"


def _fix_api_base(api_base: str) -> Optional[str]:
    """修复常见误填并做最基本校验。"""
    api_base = (api_base or "").strip()
    if not api_base:
        return None
    # 容错：修复误填的协议前缀（例如 "ttps://..."）
    if api_base.startswith("ttps://"):
        api_base = f"h{api_base}"
    if api_base.startswith("://"):
        api_base = f"http{api_base}"
    if not api_base.startswith(("http://", "https://")):
        return None
    return api_base


def _sent_notifications_file() -> Path:
    """已发送通知记录文件路径（跟随用户配置目录）。"""
    return CONFIG_PATH.parent / "sent_notifications.json"


def _load_sent_ids(limit: int = 1000) -> Set[int]:
    """加载已发送通知ID集合（最多保留最近 limit 个）。"""
    path = _sent_notifications_file()
    try:
        if not path.exists():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("ids", [])
        if not isinstance(ids, list):
            return set()
        result: Set[int] = set()
        for x in ids[-limit:]:
            try:
                result.add(int(x))
            except Exception:
                continue
        return result
    except Exception:
        return set()


def _save_sent_ids(ids: Set[int], limit: int = 1000) -> None:
    """保存已发送通知ID集合（只保留最近 limit 个）。"""
    path = _sent_notifications_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ids_list = list(ids)
        if len(ids_list) > limit:
            ids_list = ids_list[-limit:]
        path.write_text(json.dumps({"ids": ids_list}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # 后台任务失败不应影响主流程
        pass


def _is_gui_app_running_macos() -> bool:
    """
    尽量判断“前台/主程序实例”是否已经在运行。

    目的：避免后台任务在 App 正常运行时抢先把通知标记为已读，导致 UI 侧收不到。
    说明：这是一个启发式判断，失败时宁可不拦截（保持功能可用）。
    """
    if platform.system() != "Darwin":
        return False
    try:
        current_pid = os.getpid()
        # ps 输出：PID + 完整命令行
        r = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode != 0:
            return False
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, cmd = parts
            try:
                pid = int(pid_str)
            except Exception:
                continue
            if pid == current_pid:
                continue
            # 只要发现同名进程且不是后台模式参数，就认为主程序在跑
            if "Ai Perf Client" in cmd and BACKGROUND_FLAG not in cmd:
                return True
    except Exception:
        return False
    return False


def _send_macos_notification(title: str, message: str, subtitle: Optional[str] = None) -> None:
    """macOS：使用 osascript 发送系统通知（后台场景更稳，不依赖 PyObjC）。"""
    # 转义特殊字符，避免 AppleScript 语法报错
    def _esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')

    title_escaped = _esc(title)
    message_escaped = _esc(message)
    script = f'display notification "{message_escaped}" with title "{title_escaped}"'
    if subtitle:
        script += f' subtitle "{_esc(subtitle)}"'
    script += ' sound name "Glass"'

    subprocess.Popen(
        ["osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_once() -> None:
    """执行一次：拉取未读通知 → 发送系统通知 → 标记已读 → 持久化去重。"""
    system = platform.system()

    # 如果主程序正在运行，后台任务直接退出（避免抢读/重复）
    if system == "Darwin" and _is_gui_app_running_macos():
        return

    try:
        cfg = ConfigManager.load()
    except Exception:
        return

    if not cfg.get("notifications", True):
        return

    session_token = (cfg.get("session_token") or "").strip()
    if not session_token:
        return

    api_base = _fix_api_base((cfg.get("api_base") or cfg.get("api_base_url") or "").strip())
    if not api_base:
        return

    client = ApiClient(api_base, session_token)

    try:
        resp = client._get("/api/notifications", params={"unread_only": True, "limit": 10})
    except Exception:
        return

    if not isinstance(resp, dict) or resp.get("status") != "success":
        return

    items = resp.get("items", [])
    if not isinstance(items, list) or not items:
        return

    sent_ids = _load_sent_ids()
    changed = False

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if raw_id is None:
            continue
        try:
            notification_id = int(raw_id)
        except Exception:
            continue

        # 去重：已经处理过就跳过
        if notification_id in sent_ids:
            continue

        # 双重保护：如果服务端返回已读，也直接记录为已处理，避免重复轮询
        if item.get("is_read", False):
            sent_ids.add(notification_id)
            changed = True
            continue

        title = (item.get("title") or "系统通知").strip() or "系统通知"
        message = (item.get("message") or "").strip()
        subtitle = item.get("subtitle")

        # 发送系统通知（只做展示，不唤醒应用）
        try:
            if system == "Darwin":
                _send_macos_notification(title, message, subtitle=subtitle)
            else:
                # 其它平台目前由原有脚本/计划任务方案处理，这里保持最小实现
                pass
        except Exception:
            # 发送失败也不要阻断后续
            pass

        # 记录已发送并尝试标记为已读
        sent_ids.add(notification_id)
        changed = True
        try:
            client._post(f"/api/notifications/{notification_id}/read", {})
        except Exception:
            pass

    if changed:
        _save_sent_ids(sent_ids)


