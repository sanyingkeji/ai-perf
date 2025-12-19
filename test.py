#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test.py

用途：列出当前 GEMINI_API_KEY/GOOGLE_API_KEY 在 Gemini API(v1beta) 下可用的模型。

默认只展示支持 generateContent 的模型（也就是你能拿来生成周报 HTML 的模型）。

用法：
  python test.py
  python test.py --all
  python test.py --method generateContent
  python test.py --raw

环境变量：
  - GEMINI_API_KEY（或 GOOGLE_API_KEY）: 必填
  - GEMINI_API_BASE: 可选，默认 https://generativelanguage.googleapis.com/v1beta

说明：
  - 脚本会尝试自动加载项目根目录下的 .env（如果安装了 python-dotenv）
  - 输出中的模型名通常形如：models/gemini-3-pro-preview
    你在业务里设置 GEMINI_MODEL 时可以用：gemini-3-pro-preview（去掉 models/ 前缀）
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import httpx


def _load_dotenv_if_possible() -> None:
    """尝试加载 .env（不强依赖）"""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        return


def _get_auth() -> Tuple[str, str]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("缺少 GEMINI_API_KEY（或 GOOGLE_API_KEY）。请在 .env 或环境变量中配置。")

    api_base = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
    api_base = (api_base or "").strip().rstrip("/")
    if not api_base:
        api_base = "https://generativelanguage.googleapis.com/v1beta"

    return api_key, api_base


def _get_upload_base(api_base: str) -> str:
    """把 .../v1beta 转换为 .../upload/v1beta"""
    base = (api_base or "").rstrip("/")
    if "/v1beta" not in base:
        return base.replace("/v1", "/upload/v1")
    prefix = base.split("/v1beta", 1)[0]
    return f"{prefix}/upload/v1beta"


def _file_uri(api_base: str, file_obj: Dict[str, Any]) -> Optional[str]:
    """优先用 api_base + name 生成标准 fileUri"""
    name = file_obj.get("name")
    if name:
        return f"{api_base.rstrip('/')}/{name}"
    uri = file_obj.get("uri")
    return str(uri) if uri else None


def _upload_bytes(api_key: str, api_base: str, display_name: str, mime_type: str, content: bytes) -> Dict[str, Any]:
    """上传 bytes 到 Gemini Files API（multipart/related）"""
    upload_base = _get_upload_base(api_base)
    url = f"{upload_base.rstrip('/')}/files"

    boundary = "BOUNDARY_TESTPY"
    meta = json.dumps({"file": {"displayName": display_name}}, ensure_ascii=False).encode("utf-8")

    body = b""
    body += f"--{boundary}\r\n".encode("utf-8")
    body += b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    body += meta + b"\r\n"
    body += f"--{boundary}\r\n".encode("utf-8")
    body += f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8")
    body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": f"multipart/related; boundary={boundary}",
        "X-Goog-Upload-Protocol": "multipart",
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, params={"key": api_key}, headers=headers, content=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Files 上传失败 HTTP {resp.status_code}: {resp.text[:2000]}")

    data = resp.json() or {}
    fobj = data.get("file") if isinstance(data, dict) else None
    return fobj or data


def _wait_file_active(api_key: str, api_base: str, file_name: str, timeout_s: int = 180) -> Dict[str, Any]:
    """等待文件状态变为 ACTIVE"""
    url = f"{api_base.rstrip('/')}/{file_name}"
    deadline = time.time() + max(1, int(timeout_s))
    last_state = None
    while time.time() < deadline:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params={"key": api_key})
        if resp.status_code >= 400:
            raise RuntimeError(f"Files 查询失败 HTTP {resp.status_code}: {resp.text[:2000]}")
        data = resp.json() or {}
        fobj = data.get("file") if isinstance(data, dict) else None
        fobj = fobj or data
        state = str(fobj.get("state") or "").upper()
        last_state = state
        if state == "ACTIVE" or state == "":
            return fobj
        if state == "FAILED":
            raise RuntimeError(f"Files 处理失败：{json.dumps(fobj, ensure_ascii=False)[:2000]}")
        time.sleep(1.5)
    raise RuntimeError(f"等待文件变为 ACTIVE 超时（last_state={last_state}）：{file_name}")


def _generate_content(
    api_key: str,
    api_base: str,
    model: str,
    parts: List[Dict[str, Any]],
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    """调用 generateContent（用于探测 MIME 支持）"""
    model = (model or "").strip()
    if model.startswith("models/"):
        model = model[len("models/") :]
    url = f"{api_base.rstrip('/')}/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 64},
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(url, params={"key": api_key}, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini API HTTP {resp.status_code}: {resp.text[:2000]}")
    return resp.json() or {}


def _make_min_pdf_bytes(text: str = "Hello") -> bytes:
    """生成一个最小可解析的 PDF（用于 MIME 探测）"""
    # 简易转义
    safe = (text or "Hello").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 24 Tf\n72 120 Td\n({safe}) Tj\nET\n".encode("utf-8")

    objs: List[bytes] = []
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objs.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n"
    )
    objs.append(
        b"4 0 obj\n<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream\nendobj\n"
    )
    objs.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = b""
    offsets: List[int] = [0]  # 0号对象占位
    cur = len(header)
    for obj in objs:
        offsets.append(cur)
        body += obj
        cur += len(obj)

    xref_start = len(header) + len(body)
    xref = b"xref\n0 6\n"
    xref += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode("ascii")

    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_start).encode("ascii")
        + b"\n%%EOF\n"
    )

    return header + body + xref + trailer


def _make_min_docx_bytes(text: str = "Hello") -> bytes:
    """生成一个最小 docx（zip）用于 MIME 探测"""
    t = (text or "Hello").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{t}</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X+fQAAAABJRU5ErkJggg=="
)


_JPG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAALCAABAAEBAREA/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCkA//Z"
)


def _sample_bytes_for_mime(mime: str) -> bytes:
    m = (mime or "").strip().lower()
    if m in {"text/plain", "text/markdown"}:
        return "Hello from test.py".encode("utf-8")
    if m == "text/html":
        return "<!doctype html><html><body><p>Hello</p></body></html>".encode("utf-8")
    if m == "application/json":
        return json.dumps({"hello": "world"}, ensure_ascii=False).encode("utf-8")
    if m == "text/csv":
        return "a,b\n1,2\n".encode("utf-8")
    if m == "application/pdf":
        return _make_min_pdf_bytes("Hello")
    if m == "image/png":
        return _PNG_1X1
    if m == "image/jpeg":
        return _JPG_1X1
    if m == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _make_min_docx_bytes("Hello")
    # 兜底
    return b"Hello"


def probe_mime_support(api_key: str, api_base: str, model: str, mimes: List[str]) -> List[Dict[str, Any]]:
    """探测指定模型对 MIME 的支持情况（基于实际 generateContent 返回）"""
    results: List[Dict[str, Any]] = []
    for mime in mimes:
        mime = (mime or "").strip()
        if not mime:
            continue
        try:
            content = _sample_bytes_for_mime(mime)
            fobj = _upload_bytes(api_key, api_base, display_name=f"probe.{mime.replace('/', '_')}", mime_type=mime, content=content)
            name = str(fobj.get("name") or "")
            if name:
                fobj = _wait_file_active(api_key, api_base, name, timeout_s=180)
            uri = _file_uri(api_base, fobj) or ""
            parts = [
                {"text": f"请简单回复 OK，并说明你是否能读取该文件内容（mime={mime}）。"},
                {"fileData": {"fileUri": uri, "mimeType": mime}},
            ]
            _generate_content(api_key, api_base, model, parts, timeout_s=60.0)
            results.append({"mime": mime, "supported": True, "detail": "OK"})
        except Exception as e:
            msg = str(e)
            unsupported = "Unsupported MIME type" in msg or "unsupported mime type" in msg.lower()
            results.append({"mime": mime, "supported": False if unsupported else None, "detail": msg[:500]})
    return results


def list_models(
    api_key: str,
    api_base: str,
    page_size: int = 200,
    timeout_s: float = 30.0,
) -> List[Dict[str, Any]]:
    """调用 ListModels，返回 models 列表"""
    url = f"{api_base}/models"

    out: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {"key": api_key, "pageSize": int(page_size)}
        if page_token:
            params["pageToken"] = page_token

        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url, params=params)

        if resp.status_code >= 400:
            raise RuntimeError(f"ListModels 失败 HTTP {resp.status_code}: {resp.text[:2000]}")

        data = resp.json() or {}
        models = data.get("models") or []
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict):
                    out.append(m)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return out


def _model_supports_method(m: Dict[str, Any], method: str) -> bool:
    methods = m.get("supportedGenerationMethods") or []
    if not isinstance(methods, list):
        return False
    return method in methods


def _fmt_int(v: Any) -> str:
    try:
        return str(int(v))
    except Exception:
        return "-"


def main() -> None:
    _load_dotenv_if_possible()

    p = argparse.ArgumentParser(description="列出当前 key 可用的 Gemini 模型 / 探测模型支持的文件类型")
    p.add_argument("--all", action="store_true", help="展示所有模型（包含不支持 generateContent 的）")
    p.add_argument("--method", default="generateContent", help="筛选支持某个方法的模型（默认 generateContent）")
    p.add_argument("--raw", action="store_true", help="输出原始 JSON（不做格式化）")
    p.add_argument("--page-size", type=int, default=200, help="ListModels pageSize（默认 200）")
    p.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数（默认 30）")
    p.add_argument("--probe-mimes", action="store_true", help="探测当前模型支持的 MIME 类型（会产生少量 API 调用）")
    p.add_argument("--model", type=str, default=None, help="探测用模型（默认取环境变量 GEMINI_MODEL，否则自动选第一个支持 generateContent 的）")
    p.add_argument("--mimes", type=str, default=None, help="要探测的 MIME 列表（逗号分隔）。不传则用默认集合。")
    args = p.parse_args()

    api_key, api_base = _get_auth()

    # MIME 探测模式：基于真实 generateContent 返回判断（Gemini 目前没有直接返回“支持哪些 MIME”的接口）
    if args.probe_mimes:
        models = list_models(api_key=api_key, api_base=api_base, page_size=args.page_size, timeout_s=args.timeout)
        model = (args.model or os.getenv("GEMINI_MODEL") or "").strip()
        if not model:
            # 自动挑一个支持 generateContent 的模型
            for m in models:
                methods = m.get("supportedGenerationMethods") or []
                if isinstance(methods, list) and "generateContent" in methods:
                    name = str(m.get("name") or "")
                    model = name[len("models/") :] if name.startswith("models/") else name
                    break
        if not model:
            raise SystemExit("找不到可用的 generateContent 模型，请先运行：python test.py 查看可用模型后用 --model 指定。")

        if args.mimes:
            mimes = [x.strip() for x in args.mimes.split(",") if x.strip()]
        else:
            # 你们周报场景常见的候选类型（可自行追加）
            mimes = [
                "text/plain",
                "text/html",
                "application/json",
                "text/csv",
                "application/pdf",
                "image/png",
                "image/jpeg",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ]

        print("=" * 80)
        print(f"API_BASE: {api_base}")
        print(f"MODEL: {model}")
        print(f"探测 MIME 数量: {len(mimes)}")
        print("=" * 80)

        results = probe_mime_support(api_key=api_key, api_base=api_base, model=model, mimes=mimes)
        for r in results:
            mime = r["mime"]
            supported = r.get("supported")
            if supported is True:
                print(f"[SUPPORTED] {mime}")
            elif supported is False:
                print(f"[UNSUPPORTED] {mime}  |  {r.get('detail')}")
            else:
                print(f"[UNKNOWN] {mime}  |  {r.get('detail')}")
        return

    models = list_models(api_key=api_key, api_base=api_base, page_size=args.page_size, timeout_s=args.timeout)

    if args.raw:
        print(json.dumps({"api_base": api_base, "models": models}, ensure_ascii=False, indent=2))
        return

    method = (args.method or "").strip()
    if not method:
        method = "generateContent"

    if not args.all:
        models = [m for m in models if _model_supports_method(m, method)]

    # 按名字排序
    models.sort(key=lambda m: str(m.get("name") or ""))

    print("=" * 80)
    print(f"API_BASE: {api_base}")
    print(f"筛选方法: {method}  |  all={args.all}")
    print(f"模型数量: {len(models)}")
    print("=" * 80)

    if not models:
        print("没有找到可用模型。可能原因：key 无权限 / API 未开通 / method 筛选不匹配。")
        return

    # 打印表格
    for m in models:
        name = str(m.get("name") or "")
        display = str(m.get("displayName") or "")
        desc = str(m.get("description") or "")
        in_lim = _fmt_int(m.get("inputTokenLimit"))
        out_lim = _fmt_int(m.get("outputTokenLimit"))
        methods = m.get("supportedGenerationMethods") or []
        if not isinstance(methods, list):
            methods = []

        # 给出可直接写入 .env 的 GEMINI_MODEL 建议值
        env_model = name
        if env_model.startswith("models/"):
            env_model = env_model[len("models/") :]

        print(f"- {name}")
        if display:
            print(f"  displayName: {display}")
        if desc:
            # 描述可能很长，截一下便于阅读
            desc_short = desc if len(desc) <= 160 else desc[:160] + "..."
            print(f"  description: {desc_short}")
        print(f"  supportedGenerationMethods: {methods}")
        print(f"  inputTokenLimit: {in_lim}  |  outputTokenLimit: {out_lim}")
        print(f"  建议配置: GEMINI_MODEL={env_model}")
        print("")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误：{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
