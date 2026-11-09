"""Read one Tencent Docs spreadsheet range by official OpenAPI (read-only).
OpenAPI scopes and ordinary sheet type must be verified for the user's account.
No fallback to stale CSV data is permitted.
"""
import json
import os
import re
from urllib.parse import quote, urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class TencentError(RuntimeError):
    pass

def document_identity(cfg):
    """Resolve file ID from a validated docs.qq.com sheet URL or an explicit ID."""
    link = str(cfg.get("doc_url") or "").strip()
    file_id = str(cfg.get("file_id") or "").strip()
    sheet_id = str(cfg.get("sheet_id") or "").strip()
    if link:
        url = urlparse(link)
        match = re.fullmatch(r"/sheet/([A-Za-z0-9_-]+)/?", url.path)
        if url.scheme != "https" or url.netloc != "docs.qq.com" or not match:
            raise TencentError("腾讯文档地址须为 https://docs.qq.com/sheet/<文档ID>")
        id_in_url = match.group(1)
        if file_id and file_id != id_in_url:
            raise TencentError("doc_url 与 file_id 不一致")
        file_id = id_in_url
        if not sheet_id:
            sheet_id = (parse_qs(url.query).get("tab") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        raise TencentError("缺少合法的腾讯文档 doc_url 或 file_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sheet_id):
        if not str(cfg.get("sheet_name") or "").strip():
            raise TencentError("需要 sheet_id、sheet_name，或文档链接的 tab 参数")
        sheet_id = ""
    return file_id, sheet_id

def _get_json(url, headers):
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            result = json.load(resp)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise TencentError("腾讯文档读取失败：检查授权、文件权限和接口支持情况") from exc
    if not isinstance(result, dict) or result.get("ret") != 0:
        raise TencentError("腾讯文档 API 返回非成功状态（不会回退旧数据）")
    if not isinstance(result.get("data"), dict):
        raise TencentError("腾讯文档 API 缺少 data 对象")
    return result["data"]

def resolve_sheet_by_name(metadata, sheet_name):
    """Only accept an exact, unique match from a documented sheets list."""
    target = str(sheet_name or "").strip()
    if not target:
        raise TencentError("工作表名称不能为空")
    candidates = [
        metadata.get("sheets"),
        metadata.get("sheetList"),
        (metadata.get("file") or {}).get("sheets") if isinstance(metadata.get("file"), dict) else None,
        (metadata.get("metadata") or {}).get("sheets") if isinstance(metadata.get("metadata"), dict) else None,
    ]
    sheet_lists = [c for c in candidates if isinstance(c, list)]
    if not sheet_lists:
        raise TencentError("元数据不包含可识别的工作表列表，无法按名称解析；请改填 sheet_id")
    matched = []
    for sheets in sheet_lists:
        for sheet in sheets:
            if not isinstance(sheet, dict):
                continue
            title = sheet.get("title") or sheet.get("name") or sheet.get("sheetName")
            sid = (sheet.get("sheetId") or sheet.get("sheet_id") or
                   sheet.get("id") or sheet.get("ID"))
            if title == target and isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                matched.append(sid)
    ids = set(matched)
    if len(ids) != 1:
        raise TencentError("找不到唯一匹配的工作表名称；请核对 sheet_name 或填写 sheet_id")
    return matched[0]

def resolve_auth_headers(auth=None):
    """Prefer local private configuration; environment variables are fallback."""
    if auth is None:
        auth = {}
    if not isinstance(auth, dict):
        raise TencentError("tencent_docs_auth 必须是 JSON 对象")
    names = (
        ("client_id", "TENCENT_DOCS_CLIENT_ID", "Client-Id"),
        ("access_token", "TENCENT_DOCS_ACCESS_TOKEN", "Access-Token"),
        ("open_id", "TENCENT_DOCS_OPEN_ID", "Open-Id"),
    )
    headers = {"Accept": "application/json"}
    for key, env_name, header_name in names:
        configured = auth.get(key)
        value = os.environ.get(env_name, "") if configured is None or configured == "" else configured
        if not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value:
            # Never surface token, credentials, URLs or raw remote error text.
            raise TencentError("腾讯文档鉴权不完整或格式错误；请检查 config.local.json 的 tencent_docs_auth")
        headers[header_name] = value
    return headers


def read_values(cfg, auth=None):
    file_id, sheet_id = document_identity(cfg)
    cell_range = str(cfg.get("range") or "A1:H500").strip()
    if not re.fullmatch(r"[A-Za-z]{1,3}[1-9][0-9]*:[A-Za-z]{1,3}[1-9][0-9]*", cell_range):
        raise TencentError("range 须为 A1:H500 这种单工作表区域")
    # Prefer config.local.json -> tencent_docs_auth; support the previous
    # environment-variable setup as a fallback without logging any values.
    headers = resolve_auth_headers(auth)
    name = str(cfg.get("sheet_name") or "").strip()
    if name:
        # The copied URL may contain a tab pointing at a different sheet;
        # sheet_name is always resolved explicitly instead of trusting tab.
        metadata_url = ("https://docs.qq.com/openapi/drive/v2/files/" +
                        quote(file_id, safe="") + "/metadata")
        resolved = resolve_sheet_by_name(_get_json(metadata_url, headers), name)
        explicit = str(cfg.get("sheet_id") or "").strip()
        if explicit and explicit != resolved:
            raise TencentError("sheet_name 与 sheet_id 指向不同工作表")
        sheet_id = resolved
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sheet_id):
        raise TencentError("找不到合法的 sheet_id，请核对工作表名称与权限")
    url = ("https://docs.qq.com/openapi/spreadsheet/v2/files/" + quote(file_id, safe="") +
           "/sheets/" + quote(sheet_id, safe="") + "/values?" + urlencode({"range": cell_range}))
    payload = _get_json(url, headers)
    values = payload.get("values")
    if not isinstance(values, list):
        raise TencentError("腾讯文档响应不包含 data.values，请检查普通表格/智能表类型")
    return values