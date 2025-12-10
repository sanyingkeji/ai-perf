#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""google_login.py
从桌面客户端发起 Google OAuth 登录，获取 ID Token，并写入 config.json。

使用方式：
- 在 Google Cloud Console 里创建 OAuth 2.0 Client（应用类型：桌面应用 / Installed app）
- 下载得到的 client_secret JSON，命名为 `google_client_secret.json`，
  放到 ui_client 目录下（与 main.py 同级）
- 在设置页点击“使用 Google 登录获取 ID Token”按钮即可完成登录
"""

from __future__ import annotations

from pathlib import Path
import httpx
from typing import Optional
import webbrowser
import sys
import subprocess
import threading
import time

from utils.config_manager import ConfigManager


class GoogleLoginError(Exception):
    """Google 登录相关错误。"""


# 全局登录状态锁，防止并发登录
_login_lock = threading.Lock()
_login_in_progress = False

# 防止重复打开浏览器的标志（在同一个登录流程中）
_browser_opened = False
_browser_lock = threading.Lock()


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _open_in_new_window1(auth_url: str):
    """尽量在默认浏览器中新开一个窗口，而不是当前窗口的新 tab。"""
    try:
        webbrowser.open_new(auth_url)  # new window
    except Exception:
        # 回退：至少要能打开
        webbrowser.open(auth_url)


def _open_in_new_window(auth_url: str):
    """打开浏览器进行授权，防止重复打开（在同一个登录流程中只打开一次）
    
    此函数使用全局标志和锁机制，确保即使在 run_local_server 多次调用的情况下，
    也只会打开一次浏览器窗口。适用于所有平台（macOS、Windows、Linux）。
    """
    global _browser_opened
    
    # 使用锁确保线程安全
    with _browser_lock:
        # 如果已经打开过浏览器，直接返回（防止重复打开）
        if _browser_opened:
            return
        _browser_opened = True
    
    # macOS 上使用 subprocess 直接调用 open 命令，更可靠且避免重复打开
    if sys.platform == "darwin":
        try:
            # 使用 macOS 的 open 命令，这是最可靠的方式
            subprocess.Popen(["open", auth_url])
            return
        except Exception:
            # 回退到 webbrowser
            try:
                webbrowser.open_new(auth_url)
                return
            except Exception:
                webbrowser.open(auth_url)
                return

    # Windows：优先用 Edge app 模式，接近“无地址栏小窗”
    if sys.platform.startswith("win"):
        try:
            # 尝试使用 Edge app 模式
            subprocess.Popen([
                "msedge.exe",
                f"--app={auth_url}",
                "--window-size=900,700",
            ], creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            return
        except Exception:
            # Edge app 模式失败，尝试使用 start 命令（Windows 原生方式，更可靠）
            try:
                subprocess.Popen(["start", auth_url], shell=True)
                return
            except Exception:
                # 如果 start 命令也失败，回退到 webbrowser
                pass

    # Linux 和其他平台：使用 xdg-open（如果可用）
    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["xdg-open", auth_url])
            return
        except Exception:
            # xdg-open 不可用，回退到 webbrowser
            pass

    # 兜底：所有平台通用的默认浏览器打开方式
    # 注意：由于前面已经设置了 _browser_opened = True，即使这里被多次调用也不会重复打开
    try:
        webbrowser.open_new(auth_url)
    except Exception:
        webbrowser.open(auth_url)


def _get_client_secret_file() -> Path:
    """返回 OAuth 客户端配置文件路径。"""
    base_dir = Path(__file__).resolve().parents[1]
    path = base_dir / "google_client_secret.json"
    if not path.exists():
        raise GoogleLoginError(
            f"未找到 Google OAuth 客户端配置文件: {path}\n"
            f"请将 Google 控制台下载的 client_secret JSON 命名为 google_client_secret.json 放在 ui_client 根目录下。"
        )
    return path


def _get_callback_html() -> str:
    """返回美化的回调页面 HTML"""
    html_file = Path(__file__).resolve().parents[1] / "resources" / "oauth_callback.html"
    if html_file.exists():
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    
    # 如果文件不存在或读取失败，返回默认的美化 HTML
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>授权成功 - Ai Perf</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 60px 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
            animation: fadeInUp 0.6s ease-out;
        }
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(30px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .success-icon {
            width: 80px;
            height: 80px;
            margin: 0 auto 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            animation: scaleIn 0.5s ease-out 0.2s both;
        }
        @keyframes scaleIn {
            from { transform: scale(0); }
            to { transform: scale(1); }
        }
        .success-icon svg {
            width: 50px;
            height: 50px;
            color: white;
        }
        h1 {
            font-size: 28px;
            color: #333;
            margin-bottom: 15px;
            font-weight: 600;
        }
        .message {
            font-size: 16px;
            color: #666;
            line-height: 1.6;
            margin-bottom: 30px;
        }
        .highlight {
            color: #667eea;
            font-weight: 600;
        }
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-left: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .footer {
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 14px;
            color: #999;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon">
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
            </svg>
        </div>
        <h1>已得到授权！</h1>
        <p class="message">
            您已成功完成 <span class="highlight">Google 授权登录</span>第一步，<br>
            接下来请切回 Ai Perf 端...
        </p>
        <div class="loading"></div>
        <div class="footer">
            <p>Ai Perf - 让绩效管理公平、透明、自动化</p>
        </div>
    </div>
    <script>
        setTimeout(function() {
            try { window.close(); } catch (e) { console.log('窗口将保持打开，您可以手动关闭'); }
        }, 2000);
    </script>
</body>
</html>"""


def login_and_get_id_token(callback_received_callback=None) -> str:
    """打开浏览器进行 Google 登录，返回 ID Token 字符串。

    成功后会顺便把 token 写回 config.json 的 google_id_token 字段。
    
    Args:
        callback_received_callback: 可选的回调函数，在收到 Google 回调后、调用后端接口前调用
    
    Raises:
        GoogleLoginError: 如果登录过程中出现错误，或已有登录流程在进行中
    """
    global _login_in_progress, _browser_opened
    
    # 检查是否已有登录流程在进行中
    with _login_lock:
        if _login_in_progress:
            raise GoogleLoginError("登录流程正在进行中，请勿重复点击。")
        _login_in_progress = True
    
    # 重置浏览器打开标志，允许新的登录流程打开浏览器
    with _browser_lock:
        _browser_opened = False
    
    try:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(
                "缺少依赖 google-auth-oauthlib，请先执行: pip install google-auth-oauthlib google-auth"
            ) from e

        client_file = _get_client_secret_file()
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=SCOPES)

        try:
            # 获取美化的回调页面 HTML
            callback_html = _get_callback_html()
            
            # 让库自己选择可用端口（port=0），避免固定 8789 被占用的问题
            # 通过继承和替换来美化回调页面
            try:
                from google_auth_oauthlib.flow import _RedirectWSGIApp
                import google_auth_oauthlib.flow
                
                # 保存原始类
                OriginalRedirectWSGIApp = _RedirectWSGIApp
                
                # 创建美化的包装类
                class BeautifiedRedirectWSGIApp(OriginalRedirectWSGIApp):
                    """美化的回调页面 WSGI 应用"""
                    
                    def __call__(self, environ, start_response):
                        """处理请求，如果是成功回调则返回美化的 HTML"""
                        path_info = environ.get('PATH_INFO', '')
                        query_string = environ.get('QUERY_STRING', '')
                        
                        # 检查是否是成功回调（包含 code 参数）
                        if path_info == '/' and 'code=' in query_string:
                            # 先调用父类方法处理回调（让库获取授权码并设置内部状态）
                            # 我们需要捕获响应但不发送给客户端
                            response_sent = [False]
                            captured_status = [None]
                            captured_headers = []
                        
                            def dummy_start_response(status, headers):
                                """捕获状态和头部，但不真正发送响应"""
                                if not response_sent[0]:
                                    captured_status[0] = status
                                    captured_headers[:] = headers
                                    response_sent[0] = True
                                # 返回一个空的写入函数
                                def write(data):
                                    pass
                                return write
                        
                            # 调用父类方法处理回调（这会设置内部状态）
                            try:
                                response_iter = super().__call__(environ, dummy_start_response)
                                # 消耗响应以确保回调被处理
                                for _ in response_iter:
                                    pass
                                if hasattr(response_iter, 'close'):
                                    response_iter.close()
                            except Exception:
                                pass
                        
                            # 现在返回美化的 HTML
                            html_bytes = callback_html.encode('utf-8')
                            start_response('200 OK', [
                                ('Content-Type', 'text/html; charset=utf-8'),
                                ('Content-Length', str(len(html_bytes)))
                            ])
                            return [html_bytes]
                        
                        # 其他情况使用原始处理
                        return super().__call__(environ, start_response)
                
                # 临时替换类
                google_auth_oauthlib.flow._RedirectWSGIApp = BeautifiedRedirectWSGIApp
                
                # 执行 OAuth 流程
                creds = flow.run_local_server(
                    port=0,
                    authorization_prompt_message="正在打开浏览器进行 Google 登录...\n请在浏览器中完成授权。",
                    success_message="登录成功，可以回到 Ai Perf 客户端。",
                    open_browser=_open_in_new_window,  # type: ignore
                )
                
                # 恢复原始类
                google_auth_oauthlib.flow._RedirectWSGIApp = OriginalRedirectWSGIApp
                
            except Exception as patch_error:
                # 如果美化失败，使用默认行为
                creds = flow.run_local_server(
                    port=0,
                    authorization_prompt_message="正在打开浏览器进行 Google 登录...\n请在浏览器中完成授权。",
                    success_message="登录成功，可以回到 Ai Perf 客户端。",
                    open_browser=_open_in_new_window,  # type: ignore
                )
        except OSError as e:
            # 比如端口占用等情况
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(f"本地回调端口被占用或无法绑定，请重试或重启应用。\n详细信息: {e}") from e
        except Exception as e:
            # 兜底处理，避免直接把异常抛到 UI 层导致崩溃
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(f"调用 Google 登录流程失败: {e}") from e

        id_token: Optional[str] = getattr(creds, "id_token", None)
        if not id_token:
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(
                "Google 登录完成，但未获取到 ID Token。\n请在 OAuth Client 的授权范围中包含 openid / email / profile。"
            )

    # 第一步：把 ID Token 写回配置（方便调试）
        cfg = ConfigManager.load()
        cfg["google_id_token"] = id_token

        # 已成功接收到谷歌回调信息，正在登录中
        if callback_received_callback:
            try:
                callback_received_callback()
            except Exception:
                # 回调失败不影响登录流程
                pass

    # 第二步：调用后端 /auth/google_login 换取 session_token
        base_url = (cfg.get("api_base") or "http://127.0.0.1:8000").rstrip("/")
        url = f"{base_url}/auth/google_login"

    # 增加超时时间到30秒，并添加重试机制
        max_retries = 2
        retry_delay = 2  # 秒
    
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={"id_token": id_token},
                    timeout=30,  # 增加到30秒
                )
                last_error = None
                break  # 成功，退出重试循环
            except httpx.TimeoutException as e:
                last_error = e
                if attempt < max_retries:
                # 等待后重试
                    time.sleep(retry_delay)
                    continue
                # 最后一次尝试也失败
                    with _login_lock:
                        _login_in_progress = False
                        raise GoogleLoginError(
                        f"登录请求超时（已重试{max_retries}次），请检查网络连接或稍后重试。\n"
                        f"如果问题持续存在，可能是服务器响应较慢，请联系管理员。"
                        ) from e
            except httpx.RequestError as e:
                last_error = e
                if attempt < max_retries:
                    # 等待后重试
                    time.sleep(retry_delay)
                    continue
                # 最后一次尝试也失败
                with _login_lock:
                    _login_in_progress = False
                raise GoogleLoginError(
                    f"无法连接到服务器（已重试{max_retries}次），请检查网络连接和服务器地址配置。\n"
                    f"服务器地址：{base_url}\n详细信息: {e}"
                ) from e
            except Exception as e:
                # 其他异常不重试，直接抛出
                with _login_lock:
                    _login_in_progress = False
                raise GoogleLoginError(f"调用后端登录接口失败：{e}") from e
        
        if last_error is not None:
            # 如果所有重试都失败，抛出最后一个错误
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(f"登录失败：{last_error}")

        if resp.status_code != 200:
            # 尝试从后端错误中提取信息
            try:
                data = resp.json()
                detail = data.get("detail") or data.get("message") or data
                detail_str = str(detail)
                # 检查是否是权限相关的错误（403 或包含权限相关关键词）
                if (resp.status_code == 403 or 
                    "未在内部系统中找到该邮箱" in detail_str or 
                    "权限" in detail_str or 
                    "permission" in detail_str.lower() or 
                    "unauthorized" in detail_str.lower() or
                    "forbidden" in detail_str.lower()):
                    with _login_lock:
                        _login_in_progress = False
                    raise GoogleLoginError(f"无权限：{detail}\n\n请联系管理员添加您的邮箱到系统白名单。")
                # 检查是否是超时错误（408），但可能是邮箱不存在导致的
                if resp.status_code == 408:
                    # 408 超时错误，可能是数据库查询超时，但也可能是邮箱不存在
                    # 提示用户可能是权限问题
                    with _login_lock:
                        _login_in_progress = False
                    raise GoogleLoginError(f"登录超时：{detail}\n\n如果您的邮箱未在系统中注册，请联系管理员添加。")
            except GoogleLoginError:
                raise  # 重新抛出权限错误
            except Exception:
                detail = resp.text
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(f"后端登录失败：HTTP {resp.status_code} - {detail}")

        # 解析响应（当状态码为 200 时）
        try:
            data = resp.json()
        except Exception as e:
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError(f"解析后端登录响应失败：{e}") from e

        session_token = data.get("session_token") or ""
        user_id = data.get("user_id") or ""
        user_name = data.get("name") or ""
        user_email = data.get("google_email") or ""

        if not session_token:
            with _login_lock:
                _login_in_progress = False
            raise GoogleLoginError("后端登录响应中缺少 session_token。")

        cfg["session_token"] = session_token
        cfg["user_id"] = user_id
        cfg["user_name"] = user_name
        cfg["user_email"] = user_email

        ConfigManager.save(cfg)
    
        # 登录成功，释放锁
        with _login_lock:
            _login_in_progress = False

        return id_token
    except Exception as e:
        # 确保在发生任何异常时都释放锁
        with _login_lock:
            _login_in_progress = False
        # 如果是 GoogleLoginError，直接重新抛出
        if isinstance(e, GoogleLoginError):
            raise
        # 其他异常包装成 GoogleLoginError
        raise GoogleLoginError(f"登录过程中发生错误：{e}") from e
