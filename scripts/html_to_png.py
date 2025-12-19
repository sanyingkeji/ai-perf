#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将本地 HTML 文件渲染并导出为 PNG。

默认输入：
  www/report/week/for_png.html

默认输出：
  与输入同目录、同文件名的 .png（例如 for_png.png）

实现策略（按优先级）：
1) Playwright（推荐）：渲染效果最接近真实浏览器，支持 full page 截图
   安装：
     pip install playwright
     python -m playwright install chromium
2) 系统 Chrome/Chromium（无需 Python 额外依赖）：使用 headless 模式截图

示例：
  python scripts/html_to_png.py
  python scripts/html_to_png.py -i www/report/week/for_png.html -o www/report/week/for_png.png
  python scripts/html_to_png.py --backend chrome --width 1400 --height 2400 --scale 2
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def _repo_root() -> Path:
    # scripts/ 目录的上一级即仓库根目录
    return Path(__file__).resolve().parents[1]


def _to_file_url(path: Path) -> str:
    return path.resolve().as_uri()


def _find_chrome_binary() -> Optional[str]:
    # 先从 PATH 中探测
    names = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "microsoft-edge",
        "brave",
        "brave-browser",
    ]
    for name in names:
        p = shutil.which(name)
        if p:
            return p

    # 再探测常见安装路径（macOS）
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

    # Windows 常见路径（尽力而为）
    if sys.platform == "win32":
        candidates = []
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            candidates.extend(
                [
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(base, "Chromium", "Application", "chrome.exe"),
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                ]
            )
        for c in candidates:
            if os.path.exists(c):
                return c

    return None


def _export_with_chrome(
    *,
    chrome: str,
    input_html: Path,
    output_png: Path,
    width: int,
    height: int,
    scale: float,
    virtual_time_budget_ms: int,
) -> None:
    """
    使用系统 Chrome/Chromium 的 headless 截图能力导出 PNG。

    注意：
    - Chrome CLI 的 --screenshot 通常是“视口截图”；这里通过设置一个较大的 height 来覆盖整页内容。
    - 如需“严格全页”，推荐安装 Playwright 并使用 --backend playwright（默认 auto 会优先尝试）。
    """
    output_png.parent.mkdir(parents=True, exist_ok=True)
    file_url = _to_file_url(input_html)

    # 为了避免污染/依赖用户默认浏览器配置（以及在受限环境中写入失败），
    # 强制使用临时 profile 目录（放在仓库 .tmp 下，便于清理且可写）。
    tmp_base = _repo_root() / ".tmp" / "html_to_png"
    tmp_base.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="chrome-profile-", dir=str(tmp_base)) as profile_dir:
        crash_dir = Path(profile_dir) / "crash-dumps"
        crash_dir.mkdir(parents=True, exist_ok=True)

        base_args = [
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--disable-features=Crashpad",
            f"--crash-dumps-dir={str(crash_dir)}",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            f"--window-size={width},{height}",
            f"--force-device-scale-factor={scale}",
            f"--virtual-time-budget={virtual_time_budget_ms}",
            f"--screenshot={str(output_png)}",
            file_url,
        ]

        # 优先尝试新 headless，失败则回退旧 headless（兼容老版本 Chrome）
        for headless_flag in ("--headless=new", "--headless"):
            cmd = [chrome, headless_flag, *base_args]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                return
            except subprocess.CalledProcessError as e:
                last_err = (e.stderr or e.stdout or str(e)).strip()
                # 如果是 headless 参数不支持，继续尝试下一种；否则直接抛出
                if "headless" in last_err.lower() or "unknown" in last_err.lower():
                    continue
                raise RuntimeError(f"Chrome 截图失败：{last_err}") from e

    raise RuntimeError("Chrome 启动失败：当前浏览器可能不支持 headless 模式，请改用 Playwright。")


def _export_with_playwright(
    *,
    input_html: Path,
    output_png: Path,
    width: int,
    height: int,
    scale: float,
    timeout_ms: int,
    full_page: bool,
    wait_ms: int,
) -> None:
    output_png.parent.mkdir(parents=True, exist_ok=True)
    file_url = _to_file_url(input_html)

    # Playwright（同步 API）
    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        page = context.new_page()
        page.emulate_media(media="screen")
        page.goto(file_url, wait_until="load", timeout=timeout_ms)

        # 等待字体/布局稳定（对本文件影响不大，但更稳）
        try:
            page.evaluate(
                """() => (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve()"""
            )
        except Exception:
            pass
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)

        page.screenshot(path=str(output_png), full_page=full_page, type="png")
        context.close()
        browser.close()


def _parse_args() -> argparse.Namespace:
    rr = _repo_root()
    default_input = rr / "www" / "report" / "week" / "for_png.html"

    p = argparse.ArgumentParser(description="将本地 HTML 渲染导出为 PNG（优先 Playwright，回退 Chrome）。")
    p.add_argument(
        "-i",
        "--input",
        default=str(default_input),
        help="输入 HTML 路径（默认：仓库内 www/report/week/for_png.html）",
    )
    p.add_argument(
        "-o",
        "--output",
        default="",
        help="输出 PNG 路径（默认：与输入同目录同名 .png）",
    )
    p.add_argument(
        "--backend",
        choices=["auto", "playwright", "chrome"],
        default="auto",
        help="渲染后端：auto（默认）、playwright、chrome",
    )
    p.add_argument("--width", type=int, default=1400, help="视口宽度（像素）")
    p.add_argument("--height", type=int, default=2400, help="视口高度（像素，Chrome 截图时建议设大一些）")
    p.add_argument("--scale", type=float, default=2.0, help="设备缩放（清晰度倍率，推荐 2）")
    p.add_argument("--timeout-ms", type=int, default=30_000, help="加载超时（毫秒，仅 Playwright）")
    p.add_argument(
        "--full-page",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否截取全页（仅 Playwright）",
    )
    p.add_argument("--wait-ms", type=int, default=200, help="额外等待渲染稳定（毫秒，仅 Playwright）")
    p.add_argument("--virtual-time-budget-ms", type=int, default=2000, help="虚拟时间预算（毫秒，仅 Chrome）")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    input_html = Path(args.input).expanduser().resolve()
    if not input_html.exists():
        print(f"❌ 输入 HTML 不存在：{input_html}")
        return 2

    output_png = Path(args.output).expanduser()
    if str(output_png).strip() == "":
        output_png = input_html.with_suffix(".png")
    output_png = output_png.resolve()

    # auto：优先 Playwright（若可用），否则回退 Chrome
    if args.backend in ("auto", "playwright"):
        try:
            _export_with_playwright(
                input_html=input_html,
                output_png=output_png,
                width=args.width,
                height=args.height,
                scale=args.scale,
                timeout_ms=args.timeout_ms,
                full_page=bool(args.full_page),
                wait_ms=args.wait_ms,
            )
            print(f"✅ 已导出 PNG：{output_png}")
            return 0
        except ModuleNotFoundError:
            if args.backend == "playwright":
                print("❌ 未安装 Playwright。请执行：pip install playwright && python -m playwright install chromium")
                return 3
        except Exception as e:
            if args.backend == "playwright":
                print(f"❌ Playwright 导出失败：{e}")
                return 4
            # auto 模式：Playwright 失败则继续回退 Chrome

    chrome = _find_chrome_binary()
    if not chrome:
        print(
            "❌ 未找到可用的 Chrome/Chromium。\n"
            "建议安装 Playwright：pip install playwright && python -m playwright install chromium\n"
            "或在系统安装 Google Chrome 后重试（脚本会自动探测）。"
        )
        return 5

    try:
        _export_with_chrome(
            chrome=chrome,
            input_html=input_html,
            output_png=output_png,
            width=args.width,
            height=args.height,
            scale=args.scale,
            virtual_time_budget_ms=args.virtual_time_budget_ms,
        )
    except Exception as e:
        print(f"❌ Chrome 导出失败：{e}")
        return 6

    print(f"✅ 已导出 PNG：{output_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

