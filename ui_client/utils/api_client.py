#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一处理 API 请求：
- 使用 session_token（由后端生成，不再使用 Google ID Token）
- 封装 GET / POST
- 自动处理错误
"""

import json
import httpx
from typing import Any, Dict, Optional

from utils.config_manager import ConfigManager


# ==== 自定义异常 ====

class ApiError(Exception):
    """业务错误（HTTP 200，但返回错误结构 / message）。"""
    pass


class AuthError(Exception):
    """鉴权错误（需要重新登录）。"""
    pass


# ==== 客户端实现 ====

class ApiClient:
    """
    API 客户端：负责所有 /api/* 请求。
    使用后端签发的 session_token，不再使用 Google ID Token。
    """

    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token

    # ---------- 工厂方法 ----------
    @classmethod
    def from_config(cls) -> "ApiClient":
        cfg = ConfigManager.load()

        base_url = (cfg.get("api_base") or cfg.get("api_base_url") or "").strip()
        if not base_url:
            raise AuthError("API 服务器地址未配置。")

        token = (cfg.get("session_token") or "").strip()
        if not token:
            raise AuthError("需要先登录。")

        return cls(base_url=base_url, session_token=token)
    
    @staticmethod
    def is_logged_in() -> bool:
        """检查是否已登录（不抛出异常）"""
        try:
            cfg = ConfigManager.load()
            token = (cfg.get("session_token") or "").strip()
            return bool(token)
        except Exception:
            return False

    # ---------- Headers ----------
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session_token}",
            "Content-Type": "application/json",
        }

    # ---------- GET ----------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> Any:
        url = f"{self.base_url}{path}"
        import time
        
        last_exception = None
        retry_delay = 1  # 初始延迟1秒
        
        for attempt in range(max_retries):
            try:
                r = httpx.get(url, headers=self._headers(), params=params, timeout=15)
                return self._handle_response(r)
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    # 还有重试机会，等待后重试
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    # 所有重试都失败了
                    raise ApiError(f"网络异常：{type(e).__name__}: {e}（已重试 {max_retries} 次）")
        
        # 理论上不会到达这里，但为了类型检查
        raise ApiError(f"网络异常：{type(last_exception).__name__}: {last_exception}")

    # ---------- POST ----------
    def _post(self, path: str, payload: Dict[str, Any], max_retries: int = 3, timeout: float = 15.0) -> Any:
        """
        POST 请求
        
        Args:
            path: API 路径
            payload: 请求体
            max_retries: 最大重试次数，默认 3 次
            timeout: 超时时间（秒），默认 15 秒
        """
        url = f"{self.base_url}{path}"
        import time
        
        last_exception = None
        retry_delay = 1  # 初始延迟1秒
        
        for attempt in range(max_retries):
            try:
                r = httpx.post(url, headers=self._headers(),
                             content=json.dumps(payload), timeout=timeout)
                return self._handle_response(r)
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    # 还有重试机会，等待后重试
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    # 所有重试都失败了
                    raise ApiError(f"网络异常：{type(e).__name__}: {e}（已重试 {max_retries} 次）")
        
        # 理论上不会到达这里，但为了类型检查
        raise ApiError(f"网络异常：{type(last_exception).__name__}: {last_exception}")

    # ---------- 通用响应处理 ----------
    def _handle_response(self, r: httpx.Response) -> Any:
        """
        对 HTTP 状态和 JSON body 进行统一处理。
        401 → AuthError
        非 200 → ApiError
        content-type 必须是 JSON
        """
        if r.status_code == 401:
            # 必须重新登录
            try:
                data = r.json()
                msg = data.get("detail") or "认证失败，请重新登录。"
            except Exception:
                msg = "认证失败，请重新登录。"
            raise AuthError(msg)

        if r.status_code != 200:
            raise ApiError(f"服务器错误：HTTP {r.status_code}")

        try:
            data = r.json()
        except Exception:
            raise ApiError("服务器返回非 JSON 数据")

        # 如果返回的是 {status: "error", message: "..."} 也算业务错误
        if isinstance(data, dict) and data.get("status") == "error":
            msg = data.get("message") or "请求失败"
            raise ApiError(msg)

        return data

    # ---------- 业务 API 封装 ----------

    def get_daily_score(self, date_str: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        GET /api/daily_score?date=YYYY-MM-DD&user_id=xxx
        返回格式：从 DailyScoreResponse 中提取 data 字段
        """
        params = {"date": date_str}
        if user_id:
            params["user_id"] = user_id
        data = self._get("/api/daily_score", params=params)
        # API 返回格式：{"status": "success", "data": {...}, "message": null}
        # 或 {"status": "error", "data": null, "message": "..."}
        if isinstance(data, dict):
            if "data" in data:
                result = data["data"]
                # 如果 data 为 None（错误情况），返回 None 让调用方处理
                if result is None:
                    return None
                return result
        return data

    def get_latest_score(self) -> Dict[str, Any]:
        """
        GET /api/latest_score
        返回格式：从 DailyScoreResponse 中提取 data 字段（包含 date 字段）
        """
        data = self._get("/api/latest_score")
        # API 返回格式：{"status": "success", "data": {...}, "message": null}
        # 或 {"status": "error", "data": null, "message": "..."}
        if isinstance(data, dict):
            if "data" in data:
                result = data["data"]
                # 如果 data 为 None（错误情况），返回 None 让调用方处理
                if result is None:
                    return None
                return result
        return data

    def get_history_scores(self, limit: int = 7, offset: int = 0, user_id: Optional[str] = None, days: Optional[int] = None) -> Any:
        """
        GET /api/history_scores?limit=N&offset=M&user_id=xxx&days=D
        返回格式：从 HistoryScoresResponse 中提取 items 字段（列表）
        
        Args:
            limit: 最多返回多少条记录（用于向后兼容，如果指定了days则忽略）
            offset: 偏移量（暂时未使用，保留用于向后兼容）
            user_id: 用户ID（可选）
            days: 返回最近多少天的记录（如果指定，则按日期范围筛选，而不是按记录数限制）
        """
        params = {}
        if days is not None:
            params["days"] = days
        else:
            params["limit"] = limit
        if offset > 0:
            params["offset"] = offset
        if user_id:
            params["user_id"] = user_id
        data = self._get("/api/history_scores", params=params)
        # API 返回格式：{"status": "success", "user_id": "...", "items": [...], "message": null}
        # 或 {"status": "error", "user_id": "...", "items": [], "message": "..."}
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            # 确保返回的是列表，即使为空
            if isinstance(items, list):
                return items
            # 如果不是列表，返回空列表
            return []
        # 如果格式不符合预期，返回空列表而不是原始数据
        return []
    
    def get_team_leader_info(self) -> Dict[str, Any]:
        """
        GET /api/team_leader_info
        返回格式：TeamLeaderInfoResponse（包含 is_leader, team_name, members）
        """
        return self._get("/api/team_leader_info")
    
    def get_team_member_history_scores(self, limit: int = 30) -> Dict[str, Any]:
        """
        GET /api/team_member_history_scores?limit=N
        返回格式：TeamMemberHistoryScoresResponse（包含 is_leader, team_name, items）
        """
        return self._get("/api/team_member_history_scores", params={"limit": limit})

    def submit_review(self, payload: Dict[str, Any]) -> Any:
        """
        POST /api/reviews  提交复评数据
        
        注意：复评是耗时操作（需要调用 AI 进行评分），因此使用较长的超时时间（3 分钟）
        """
        return self._post("/api/reviews", payload, timeout=180.0)  # 3 分钟超时

    def get_daily_snapshot(self, date_str: str, user_id: Optional[str] = None) -> Any:
        """
        GET /api/daily_snapshot?date=YYYY-MM-DD&user_id=xxx
        返回格式：从 DailySnapshotResponse 中提取 snapshot 字段
        """
        params = {"date": date_str}
        if user_id:
            params["user_id"] = user_id
        data = self._get("/api/daily_snapshot", params=params)
        # API 返回格式：{"status": "success", "date": "...", "user_id": "...", "snapshot": {...}, "message": null}
        if isinstance(data, dict) and "snapshot" in data:
            return data["snapshot"]
        return data

    def get_review_status(self, date_str: str) -> Any:
        """
        GET /api/review_status?date=YYYY-MM-DD
        返回格式：ReviewStatusResponse
        """
        data = self._get("/api/review_status", params={"date": date_str})
        return data

    def get_ranking(self, date_str: Optional[str] = None) -> Any:
        """
        GET /api/ranking?date=YYYY-MM-DD
        返回格式：RankingResponse
        """
        params = {}
        if date_str:
            params["date"] = date_str
        data = self._get("/api/ranking", params=params if params else None)
        return data

    def get_monthly_ranking(self, month_str: Optional[str] = None) -> Any:
        """
        GET /api/monthly_ranking?month=YYYY-MM-DD
        返回格式：MonthlyRankingResponse
        """
        params = {}
        if month_str:
            params["month"] = month_str
        data = self._get("/api/monthly_ranking", params=params if params else None)
        return data

    def get_monthly_detail(self, month_str: Optional[str] = None) -> Any:
        """
        GET /api/monthly_detail?month=YYYY-MM-DD
        返回格式：MonthlyDetailResponse
        """
        params = {}
        if month_str:
            params["month"] = month_str
        data = self._get("/api/monthly_detail", params=params if params else None)
        return data

    def get_comparison(self, target_user_id: str, date_str: str) -> Any:
        """
        POST /api/comparison
        返回格式：ComparisonResponse
        
        注意：对比分析是耗时操作（需要调用 AI 进行对比分析），因此使用较长的超时时间（3 分钟）
        """
        payload = {
            "target_user_id": target_user_id,
            "date": date_str,
        }
        data = self._post("/api/comparison", payload, timeout=180.0)  # 3 分钟超时
        return data

    def get_daily_output(self, date_str: str) -> Any:
        """
        GET /api/daily_output?date=YYYY-MM-DD
        返回格式：{"status": "success", "date": "...", "user_id": "...", "result": {...}}
        """
        data = self._get("/api/daily_output", params={"date": date_str})
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def get_health_check(self) -> Any:
        """
        GET /api/health_check
        返回格式：HealthCheckResponse
        """
        data = self._get("/api/health_check")
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def get_api_health(self, current_version: Optional[str] = None) -> Any:
        """
        GET /api/health?current_version=x.x.x
        返回格式：ApiHealthResponse
        """
        params = {}
        if current_version:
            params["current_version"] = current_version
        data = self._get("/api/health", params=params)
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

