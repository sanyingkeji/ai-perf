#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一处理管理端 API 请求：
- 使用 session_token（由后端生成，不再使用 Google ID Token）
- 封装 GET / POST / PUT / DELETE
- 自动处理错误
"""

import json
import httpx
from typing import Any, Dict, Optional, List
from datetime import date

from utils.config_manager import ConfigManager


# ==== 自定义异常 ====

class ApiError(Exception):
    """业务错误（HTTP 200，但返回错误结构 / message）。"""
    pass


class AuthError(Exception):
    """鉴权错误（需要重新登录）。"""
    pass


# ==== 客户端实现 ====

class AdminApiClient:
    """
    管理端 API 客户端：负责所有 /admin/api/* 请求。
    使用后端签发的 session_token，不再使用 Google ID Token。
    """

    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token

    # ---------- 工厂方法 ----------
    @classmethod
    def from_config(cls) -> "AdminApiClient":
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
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"

        try:
            r = httpx.get(url, headers=self._headers(), params=params, timeout=30)
        except Exception as e:
            raise ApiError(f"网络异常：{type(e).__name__}: {e}")

        return self._handle_response(r)

    # ---------- GET Binary (for file downloads) ----------
    def _get_binary(self, path: str) -> bytes:
        """下载二进制文件"""
        url = f"{self.base_url}{path}"
        
        try:
            r = httpx.get(url, headers=self._headers(), timeout=300)  # 5分钟超时
            if r.status_code == 401:
                raise AuthError("需要重新登录")
            if r.status_code != 200:
                raise ApiError(f"下载失败: HTTP {r.status_code}")
            return r.content
        except (ApiError, AuthError):
            raise
        except Exception as e:
            raise ApiError(f"网络异常：{type(e).__name__}: {e}")

    # ---------- POST ----------
    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"

        try:
            r = httpx.post(url, headers=self._headers(),
                           content=json.dumps(payload), timeout=30)
        except Exception as e:
            raise ApiError(f"网络异常：{type(e).__name__}: {e}")

        return self._handle_response(r)

    # ---------- PUT ----------
    def _put(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"

        try:
            r = httpx.put(url, headers=self._headers(),
                          content=json.dumps(payload), timeout=30)
        except Exception as e:
            raise ApiError(f"网络异常：{type(e).__name__}: {e}")

        return self._handle_response(r)

    # ---------- DELETE ----------
    def _delete(self, path: str) -> Any:
        url = f"{self.base_url}{path}"

        try:
            r = httpx.delete(url, headers=self._headers(), timeout=30)
        except Exception as e:
            raise ApiError(f"网络异常：{type(e).__name__}: {e}")

        return self._handle_response(r)

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
            # 尝试提取 detail 字段
            try:
                data = r.json()
                detail = data.get("detail")
                if detail:
                    # 使用特殊格式标记，便于后续识别并弹出对话框
                    raise ApiError(f"HTTP_ERROR_DETAIL:{detail}")
            except (json.JSONDecodeError, ValueError):
                pass
            # 如果没有 detail 或解析失败，使用默认错误消息
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

    def get_history_scores(
        self, 
        date_str: Optional[str] = None, 
        user_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """GET /admin/api/history_scores"""
        params = {"offset": offset, "limit": limit}
        if date_str:
            params["date"] = date_str
        if user_id:
            params["user_id"] = user_id
        return self._get("/admin/api/history_scores", params=params)

    def get_daily_snapshot(self, date_str: str, user_id: str) -> Dict[str, Any]:
        """GET /admin/api/daily_snapshot"""
        return self._get("/admin/api/daily_snapshot", params={"date": date_str, "user_id": user_id})

    def get_review_result(self, date_str: str, user_id: str) -> Dict[str, Any]:
        """GET /admin/api/review_result"""
        return self._get("/admin/api/review_result", params={"date": date_str, "user_id": user_id})
    
    def get_daily_output(self, date_str: str, user_id: str) -> Dict[str, Any]:
        """GET /admin/api/daily_output"""
        return self._get("/admin/api/daily_output", params={"date": date_str, "user_id": user_id})
    
    def get_review_input(self, date_str: str, user_id: str) -> Dict[str, Any]:
        """GET /admin/api/review_input"""
        return self._get("/admin/api/review_input", params={"date": date_str, "user_id": user_id})

    def rerun_etl(self, date_str: str, user_id: str, platforms: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        POST /admin/api/rerun_etl
        platforms: 平台列表（如 ["jira", "github"]），None表示所有平台
        """
        payload = {"date": date_str, "user_id": user_id}
        if platforms:
            payload["platforms"] = platforms
        return self._post("/admin/api/rerun_etl", payload)

    def get_employees(self) -> Dict[str, Any]:
        """GET /admin/api/employees"""
        return self._get("/admin/api/employees")

    def create_employee(self, employee_data: Dict[str, Any]) -> Dict[str, Any]:
        """POST /admin/api/employees"""
        return self._post("/admin/api/employees", employee_data)

    def update_employee(self, user_id: str, employee_data: Dict[str, Any]) -> Dict[str, Any]:
        """PUT /admin/api/employees/{user_id}"""
        return self._put(f"/admin/api/employees/{user_id}", employee_data)

    def delete_employee(self, user_id: str) -> Dict[str, Any]:
        """DELETE /admin/api/employees/{user_id}"""
        return self._delete(f"/admin/api/employees/{user_id}")
    
    def set_team_leader(self, user_id: str) -> Dict[str, Any]:
        """PUT /admin/api/employees/{user_id}/set_team_leader - 设置员工为组长"""
        return self._put(f"/admin/api/employees/{user_id}/set_team_leader", {})

    def get_account_bindings(self, user_id: str) -> Dict[str, Any]:
        """GET /admin/api/employees/{user_id}/bindings"""
        return self._get(f"/admin/api/employees/{user_id}/bindings")

    def create_account_binding(self, user_id: str, binding_data: Dict[str, Any]) -> Dict[str, Any]:
        """POST /admin/api/employees/{user_id}/bindings"""
        return self._post(f"/admin/api/employees/{user_id}/bindings", binding_data)

    def update_account_binding(self, binding_id: int, binding_data: Dict[str, Any]) -> Dict[str, Any]:
        """PUT /admin/api/bindings/{binding_id}"""
        return self._put(f"/admin/api/bindings/{binding_id}", binding_data)

    def delete_account_binding(self, binding_id: int) -> Dict[str, Any]:
        """DELETE /admin/api/bindings/{binding_id}"""
        return self._delete(f"/admin/api/bindings/{binding_id}")
    
    # ---------- 维度数据查询 ----------
    def get_teams(self) -> List[Dict[str, Any]]:
        """获取团队列表"""
        resp = self._get("/admin/api/dimensions/teams")
        if resp.get("status") == "success":
            items = resp.get("items", [])
            return [
                {
                    "id": item["id"], 
                    "name": item["name"],
                    "team_leader": item.get("team_leader")  # 可能为None
                } 
                for item in items
            ]
        return []
    
    def get_roles(self) -> List[Dict[str, Any]]:
        """获取角色列表"""
        resp = self._get("/admin/api/dimensions/roles")
        if resp.get("status") == "success":
            items = resp.get("items", [])
            return [{"id": item["id"], "name": item["name"]} for item in items]
        return []
    
    def get_subroles(self, role_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取子角色列表"""
        params = {}
        if role_id is not None:
            params["role_id"] = role_id
        resp = self._get("/admin/api/dimensions/subroles", params=params)
        if resp.get("status") == "success":
            items = resp.get("items", [])
            return [{"id": item["id"], "name": item["name"]} for item in items]
        return []
    
    def get_levels(self) -> List[Dict[str, Any]]:
        """获取职级列表"""
        resp = self._get("/admin/api/dimensions/levels")
        if resp.get("status") == "success":
            items = resp.get("items", [])
            return [{"id": item["id"], "name": item["name"]} for item in items]
        return []
    
    def get_salary_bands(self) -> List[Dict[str, Any]]:
        """获取薪级列表（带薪资范围）"""
        resp = self._get("/admin/api/dimensions/salary_bands")
        if resp.get("status") == "success":
            items = resp.get("items", [])
            return [
                {
                    "band": item["band"],
                    "name": item["name"],
                    "salary_min": item["salary_min"],
                    "salary_max": item["salary_max"],
                    "salary_median": item["salary_median"],
                }
                for item in items
            ]
        return []
    
    def get_next_user_id(self) -> str:
        """获取下一个可用的员工ID"""
        resp = self._get("/admin/api/employees/next_user_id")
        if resp.get("status") == "success":
            return resp.get("next_user_id", "u1001")
        return "u1001"

    def get_etl_job_runs(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        job_name: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Any:
        """
        GET /admin/api/etl_job_runs
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        params = {"offset": offset, "limit": limit}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if job_name:
            params["job_name"] = job_name
        if status:
            params["status"] = status
        data = self._get("/admin/api/etl_job_runs", params=params if params else None)
        return data

    def get_ai_run_logs(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
        model: Optional[str] = None,
        ok: Optional[bool] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Any:
        """
        GET /admin/api/ai_run_logs
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        params = {"offset": offset, "limit": limit}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if user_id:
            params["user_id"] = user_id
        if model:
            params["model"] = model
        if ok is not None:
            params["ok"] = "true" if ok else "false"
        data = self._get("/admin/api/ai_run_logs", params=params if params else None)
        return data
    
    def get_operation_logs(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        client_type: Optional[str] = None,
        admin_email: Optional[str] = None,
        user_id: Optional[str] = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        response_status: Optional[int] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Any:
        """
        GET /admin/api/operation_logs
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        params = {"offset": offset, "limit": limit}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if client_type:
            params["client_type"] = client_type
        if admin_email:
            params["admin_email"] = admin_email
        if user_id:
            params["user_id"] = user_id
        if method:
            params["method"] = method
        if path:
            params["path"] = path
        if response_status is not None:
            params["response_status"] = response_status
        data = self._get("/admin/api/operation_logs", params=params if params else None)
        return data

    def get_health_checks(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 30,
    ) -> Any:
        """
        GET /admin/api/health_checks
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        params = {"limit": limit}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if status_filter:
            params["status_filter"] = status_filter
        data = self._get("/admin/api/health_checks", params=params if params else None)
        return data
    
    def get_services(self) -> Dict[str, Any]:
        """
        GET /admin/api/services
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        return self._get("/admin/api/services")
    
    def control_service(self, service_name: str, action: str) -> Dict[str, Any]:
        """
        POST /admin/api/services/{service_name}/control
        控制服务（start/stop/restart/enable/disable）
        """
        return self._post(f"/admin/api/services/{service_name}/control", {"action": action})
    
    def get_cron_jobs(self) -> Dict[str, Any]:
        """
        GET /admin/api/cron_jobs
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        return self._get("/admin/api/cron_jobs")
    
    def control_cron_job(self, job_name: str, action: str) -> Dict[str, Any]:
        """
        POST /admin/api/cron_jobs/{job_name}/control
        控制定时任务（enable/disable/start/stop，仅支持systemd timer）
        """
        return self._post(f"/admin/api/cron_jobs/{job_name}/control", {"action": action})
    
    def get_backups(self) -> Dict[str, Any]:
        """
        GET /admin/api/backups
        返回格式：{"status": "success", "items": [...], "message": null}
        """
        return self._get("/admin/api/backups")
    
    # ---------- 日历管理 ----------
    def get_workdays(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """获取工作日列表"""
        params = {}
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        return self._get("/admin/api/workdays", params=params)

    def update_workday(
        self,
        date: date,
        is_workday: bool,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """更新单个工作日状态"""
        payload = {
            "is_workday": is_workday,
            "note": note,
        }
        return self._put(f"/admin/api/workdays/{date.isoformat()}", payload)

    def batch_update_workdays(
        self,
        start_date: date,
        end_date: date,
        is_workday: bool,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """批量更新工作日状态"""
        payload = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "is_workday": is_workday,
            "note": note,
        }
        return self._post("/admin/api/workdays/batch", payload)

    def init_workdays(self, months: int = 6) -> Dict[str, Any]:
        """初始化工作日数据"""
        return self._post("/admin/api/workdays/init", {"months": months})

    def download_backup(self, filename: str, save_path: str) -> None:
        """
        下载备份文件到本地
        GET /admin/api/backups/{filename}/download
        """
        url = f"{self.base_url}/admin/api/backups/{filename}/download"
        
        try:
            # 使用 stream=True 下载大文件
            with httpx.stream("GET", url, headers=self._headers(), timeout=300) as response:
                if response.status_code == 401:
                    raise AuthError("认证失败，请重新登录。")
                if response.status_code != 200:
                    raise ApiError(f"下载失败：HTTP {response.status_code}")
                
                # 写入文件
                with open(save_path, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
        except Exception as e:
            if isinstance(e, (AuthError, ApiError)):
                raise
            raise ApiError(f"下载备份文件失败：{type(e).__name__}: {e}")
    
    def get_menu_permission(self) -> Dict[str, Any]:
        """
        GET /admin/api/menu_permission
        获取当前用户的菜单权限
        返回格式：{"status": "success", "is_admin": true/false, "allowed_menus": [...], "message": null}
        """
        return self._get("/admin/api/menu_permission")
    
    def get_monthly_scores(
        self,
        month: Optional[str] = None,
        user_id: Optional[str] = None,
        salary_ratio_filter: Optional[str] = None,
        sort_by: Optional[str] = "final_score",
        sort_order: Optional[str] = "desc",
    ) -> Dict[str, Any]:
        """
        GET /admin/api/monthly_scores
        获取月度评分列表
        返回格式：{"status": "success", "items": [...], "total": 0, "message": null}
        """
        params = {}
        if month:
            params["month"] = month
        if user_id:
            params["user_id"] = user_id
        if salary_ratio_filter:
            params["salary_ratio_filter"] = salary_ratio_filter
        if sort_by:
            params["sort_by"] = sort_by
        if sort_order:
            params["sort_order"] = sort_order
        return self._get("/admin/api/monthly_scores", params=params if params else None)
    
    def lock_month_rank(self, month: str) -> Dict[str, Any]:
        """
        POST /admin/api/lock_month_rank
        锁定指定月份最后一个工作日的排名
        返回格式：{"status": "success", "message": "..."}
        """
        payload = {"month": month}
        return self._post("/admin/api/lock_month_rank", payload)
    
    def get_report_generation_logs(
        self,
        report_type: Optional[str] = None,
        offset: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        GET /admin/api/report_generation_logs
        查询报表生成记录列表
        返回格式：{"status": "success", "items": [...], "total": 0, "message": null}
        """
        params = {"offset": offset, "limit": limit}
        if report_type:
            params["report_type"] = report_type
        return self._get("/admin/api/report_generation_logs", params=params)
    
    def generate_report(
        self,
        report_type: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        month: Optional[str] = None,
        week_number: Optional[int] = None,
        user_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        POST /admin/api/generate_report
        手动生成报表
        返回格式：{"status": "success", "message": "...", "file_path": "...", "file_size": 0}
        """
        payload = {"report_type": report_type}
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if month:
            payload["month"] = month
        if week_number:
            payload["week_number"] = week_number
        if user_ids:
            payload["user_ids"] = user_ids
        return self._post("/admin/api/generate_report", payload)
    
    def download_report(self, log_id: int) -> bytes:
        """
        GET /admin/api/download_report/{log_id}
        下载报表文件（ZIP压缩）
        返回：ZIP文件的二进制数据
        """
        return self._get_binary(f"/admin/api/download_report/{log_id}")

