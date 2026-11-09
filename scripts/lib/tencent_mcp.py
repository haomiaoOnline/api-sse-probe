"""Tencent Docs MCP v1.0.42: read-only ordinary sheet source using ONE token.

Uses official MCP endpoint https://docs.qq.com/openapi/mcp with
sheet.get_sheet_info and sheet.get_cell_data; no legacy OpenAPI credentials.
The API returns MCP tool envelopes, not spreadsheet OpenAPI data.values.
"""
import csv
import io
import json
import os
import re
import ssl
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, HTTPSHandler, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

MCP_URL = "https://docs.qq.com/openapi/mcp"
MCP_PROTOCOL = "2025-03-26"
MAX_RESPONSE = 3 * 1024 * 1024

class TencentMCPError(RuntimeError):
    """Safe error codes only; never echo remote content or credentials."""

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def document_identity(cfg):
    """Resolve document identifier and optional tab from an approved Tencent URL."""
    if not isinstance(cfg, dict):
        raise TencentMCPError("tencent_source_invalid")
    file_id = str(cfg.get("file_id") or "").strip()
    sheet_id = str(cfg.get("sheet_id") or "").strip()
    url = str(cfg.get("doc_url") or "").strip()
    if url:
        u = urlsplit(url)
        m = re.fullmatch(r"/sheet/([A-Za-z0-9_-]+)/?", u.path)
        if u.scheme != "https" or u.netloc != "docs.qq.com" or not m:
            raise TencentMCPError("invalid_docs_url")
        linked_file_id = m.group(1)
        if file_id and file_id != linked_file_id:
            raise TencentMCPError("conflicting_file_id")
        file_id = linked_file_id
        if not sheet_id:
            sheet_id = (parse_qs(u.query).get("tab") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        raise TencentMCPError("missing_file_id")
    if sheet_id and not re.fullmatch(r"[A-Za-z0-9_-]+", sheet_id):
        raise TencentMCPError("invalid_sheet_id")
    if not sheet_id and not str(cfg.get("sheet_name") or "").strip():
        raise TencentMCPError("missing_sheet_name_or_id")
    return file_id, sheet_id

def resolve_token(auth=None):
    auth = {} if auth is None else auth
    if not isinstance(auth, dict):
        raise TencentMCPError("tencent_docs_auth_invalid")
    token = auth.get("token") or os.environ.get("TENCENT_DOCS_TOKEN", "")
    if not isinstance(token, str) or not token.strip() or "\r" in token or "\n" in token:
        raise TencentMCPError("tencent_docs_token_missing_or_invalid")
    return token.strip()

def _unpack_tool_response(payload):
    """Translate MCP content/structuredContent to actual tool business JSON."""
    if not isinstance(payload, dict):
        raise TencentMCPError("mcp_invalid_envelope")
    if payload.get("error"):
        e = payload["error"]
        code = e.get("code") if isinstance(e, dict) else None
        # Errors from RPC sometimes include sensitive user content; code only.
        raise TencentMCPError("mcp_rpc_error_" + str(code if isinstance(code, int) else "unknown"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TencentMCPError("mcp_missing_tool_result")
    if result.get("isError"):
        raise TencentMCPError("mcp_tool_business_error")
    if isinstance(result.get("structuredContent"), dict):
        data = result["structuredContent"]
    else:
        data = None
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                try:
                    data = json.loads(block["text"])
                except ValueError:
                    raise TencentMCPError("mcp_unparseable_tool_data")
                break
    if isinstance(data, dict) and data.get("isError"):
        raise TencentMCPError("mcp_tool_business_error")
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict) and not (
            "sheets" in data or "csv_data" in data or "cells" in data):
        data = data["data"]
    if not isinstance(data, dict):
        raise TencentMCPError("mcp_tool_data_missing")
    return data

def _parse_rpc_body(raw, content_type):
    if "text/event-stream" in (content_type or "").lower():
        for line in raw.splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                try:
                    return json.loads(line[5:].strip())
                except ValueError as exc:
                    raise TencentMCPError("mcp_invalid_sse") from exc
        raise TencentMCPError("mcp_sse_no_data")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise TencentMCPError("mcp_invalid_json") from exc

def call_tool(token, tool, arguments, opener=None, timeout=20):
    if tool not in ("sheet.get_sheet_info", "sheet.get_cell_data"):
        raise TencentMCPError("mcp_unsupported_tool")
    if opener is None:
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}},
                      ensure_ascii=False).encode("utf-8")
    req = Request(MCP_URL, data=body, method="POST", headers={
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL,
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TencentMCPError("mcp_http_status_error")
            raw = resp.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise TencentMCPError("mcp_response_too_large")
            response = _parse_rpc_body(raw.decode("utf-8"), resp.headers.get("Content-Type", ""))
    except (HTTPError, URLError, TimeoutError, OSError, ssl.SSLError) as exc:
        raise TencentMCPError("mcp_transport_error") from exc
    return _unpack_tool_response(response)

def resolve_sheet_id(metadata, sheet_name, explicit_id=""):
    sheets = metadata.get("sheets")
    if not isinstance(sheets, list):
        raise TencentMCPError("mcp_sheet_list_unavailable")
    matched = []
    for sh in sheets:
        if not isinstance(sh, dict):
            continue
        sid = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("sheet_name") or sh.get("title") or sh.get("name")
        if title == sheet_name and isinstance(sid, str):
            matched.append(sid)
    if len(matched) != 1:
        raise TencentMCPError("mcp_sheet_name_not_unique_or_missing")
    if explicit_id and explicit_id != matched[0]:
        raise TencentMCPError("mcp_sheet_id_name_conflict")
    return matched[0]

def parse_range(a1):
    m = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]*):([A-Za-z]{1,3})([1-9][0-9]*)", a1)
    if not m:
        raise TencentMCPError("mcp_invalid_a1_range")
    def col(v):
        n = 0
        for c in v.upper():
            n = n * 26 + ord(c) - ord("A") + 1
        return n - 1
    left, right = col(m[1]), col(m[3])
    top, bottom = int(m[2]) - 1, int(m[4]) - 1
    if left > right or top > bottom or (right-left+1)*(bottom-top+1) > 20000:
        raise TencentMCPError("mcp_invalid_or_excessive_range")
    return top, left, bottom, right

def _to_rows(result, top, left, bottom, right):
    height, width = bottom-top+1, right-left+1
    if isinstance(result.get("csv_data"), str):
        rows = list(csv.reader(io.StringIO(result["csv_data"],newline="")))
        if len(rows) > height or any(len(row)>width for row in rows):
            raise TencentMCPError("mcp_csv_exceeds_range")
        return [row + [""] * (width-len(row)) for row in rows]
    cells = result.get("cells")
    if not isinstance(cells, list):
        raise TencentMCPError("mcp_cells_missing")
    rows = [[""] * width for _ in range(height)]
    for c in cells:
        if not isinstance(c, dict):
            raise TencentMCPError("mcp_invalid_cell")
        try:
            row, col = int(c["row"]), int(c["col"])
        except (ValueError, TypeError, KeyError) as exc:
            raise TencentMCPError("mcp_invalid_cell_coordinate") from exc
        if not top <= row <= bottom or not left <= col <= right:
            raise TencentMCPError("mcp_cell_out_of_range")
        val = (c.get("string_value") if "string_value" in c else
               c.get("number_value") if "number_value" in c else
               c.get("bool_value") if "bool_value" in c else
               c.get("formula", ""))
        rows[row-top][col-left] = "" if val is None else str(val)
    return rows

def read_values(cfg, auth=None, caller=call_tool):
    file_id, sheet_id = document_identity(cfg)
    token = resolve_token(auth)
    name = str(cfg.get("sheet_name") or "").strip()
    if name:
        metadata = caller(token, "sheet.get_sheet_info", {"file_id": file_id})
        sheet_id = resolve_sheet_id(metadata, name, str(cfg.get("sheet_id") or "").strip())
    if not sheet_id:
        raise TencentMCPError("mcp_sheet_id_missing")
    top, left, bottom, right = parse_range(str(cfg.get("range") or "A1:H500"))
    content = caller(token, "sheet.get_cell_data", {
        "file_id": file_id, "sheet_id": sheet_id,
        "start_row": top, "start_col": left, "end_row": bottom,
        "end_col": right, "return_csv": True,
    })
    return _to_rows(content, top, left, bottom, right)