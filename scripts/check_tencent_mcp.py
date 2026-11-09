#!/usr/bin/env python3
"""Read-only Tencent Docs MCP health check using the private project Token.

Does not open any personal documents, list document names, or print credentials.
"""
import json
import ssl
import sys
from pathlib import Path
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from lib.tencent_mcp import resolve_token, MCP_URL, MCP_PROTOCOL, TencentMCPError

ROOT = Path(__file__).resolve().parents[1]

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def check(config):
    cfg = json.loads(Path(config).read_text(encoding="utf-8"))
    token = resolve_token(cfg.get("tencent_docs_auth", {}))
    req = Request(MCP_URL, method="POST", data=json.dumps({
        "jsonrpc":"2.0","id":1,"method":"tools/list","params":{}
    }).encode("utf-8"), headers={
        "Authorization":token,"Content-Type":"application/json",
        "Accept":"application/json, text/event-stream",
        "MCP-Protocol-Version":MCP_PROTOCOL
    })
    try:
        with build_opener(HTTPSHandler(context=ssl.create_default_context()),
                          _NoRedirect()).open(req, timeout=20) as resp:
            if resp.status != 200:
                raise TencentMCPError("mcp_http_status_error")
            raw = resp.read(1024*1024)
            if len(raw) >= 1024*1024:
                raise TencentMCPError("mcp_tool_list_too_large")
            payload = json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
        raise TencentMCPError("mcp_connection_or_response_error") from exc
    tools = (payload.get("result") or {}).get("tools", [])
    if not isinstance(tools, list):
        raise TencentMCPError("mcp_tool_list_unavailable")
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    expected = {"sheet.get_sheet_info", "sheet.get_cell_data"}
    if not expected.issubset(names):
        raise TencentMCPError("mcp_required_read_only_tools_missing")
    return {"mcp_connected":True,"read_only_tools_available":True,
            "sheet_content_read_tested":False}

if __name__=="__main__":
    path=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"config.local.json"
    try:
        print(json.dumps(check(path), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"mcp_connected":False,"reason":(
            str(exc) if isinstance(exc,TencentMCPError) else "mcp_healthcheck_failed")}),
              file=sys.stderr)
        raise SystemExit(2)