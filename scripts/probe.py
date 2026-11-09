#!/usr/bin/env python3
"""HTTPS POST JSON -> SSE -> business assertions. No external dependencies."""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, Request, build_opener
from lib.reporting import build_analysis, slow_threshold, DEFAULT_SLOW_TOKEN_THRESHOLD_MS, display_seconds, public_summary
from lib.assertion_rules import accumulate_signals, evaluate_assertions, expand_expected_keywords
from lib.phase_transport import PhaseTracker, PhaseTimeout, PhaseTransportError, open_staged
from lib.diagnostic_context import header_pairs, safe_context
from lib.source_override import apply_tencent_source_url
from lib.expression_assertions import parse_expression
from lib.natural_assertions import parse_natural_rule

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

class ProbeError(ValueError):
    pass

class SSEError(RuntimeError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

## 第一步：单元格多条规则按引号外的分隔符拆开；引号内 URL/关键词不会被拆开。
def split_words(value):
    """Split unquoted clauses; keep JSON expressions, quoted URLs and tokens intact."""
    if isinstance(value, list):
        return [part for element in value for part in split_words(element)]
    raw = str(value or '').strip()
    if raw.startswith('{'):
        parse_expression(raw)
        return [raw]
    pairs = {'“': '”', '‘': '’', '"': '"', "'": "'", '「': '」', '『': '』', '《': '》'}
    parts, start, quote, escaped = [], 0, None, False
    for index, char in enumerate(raw):
        if quote:
            if char == quote and not escaped:
                quote = None
            escaped = char == '\\' and not escaped
            continue
        if char in pairs:
            quote = pairs[char]
            continue
        slash = char == '/' and not re.search(r'[A-Za-z][A-Za-z0-9+.-]*://\S*$', raw[start:index])
        if char in ';；|\n／' or (slash and raw[index - 1:index] not in (':', '/') and raw[index + 1:index + 2] != '/'):
            if raw[start:index].strip():
                parts.append(raw[start:index].strip())
            start = index + 1
    if raw[start:].strip():
        parts.append(raw[start:].strip())
    return parts


# 同上仅继承同一列上一条有效用例：公共与特殊判断依据分别继承。
def resolve_above(raw, previous, field, case_id):
    """Resolve 同上 from the preceding *valid* business row, same column."""
    tokens = split_words(raw)
    resolved = []
    for token in tokens:
        if token == "同上":
            if not previous:
                raise ProbeError("case_id={} 的 {} 使用同上但上条有效用例无可继承断言".format(
                    case_id, field))
            resolved.extend(previous)
        else:
            resolved.append(token)
    return list(dict.fromkeys(resolved))

def enabled(value):
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是", "启用"}

def excel_column_index(letter):
    """Zero-based absolute Excel column index; supports A..ZZZ."""
    if not isinstance(letter, str) or not re.fullmatch(r"[A-Za-z]{1,3}", letter):
        raise ProbeError("列标识必须是 A、C、AA 等 Excel 列字母")
    n = 0
    for char in letter.upper():
        n = n * 26 + (ord(char) - ord("A") + 1)
    return n - 1

def tencent_rows(values, cfg):
    """Project a rectangular cell range to named case fields, with explicit mapping."""
    rng = str(cfg.get("range") or "A1:I500").strip()
    m = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]*):([A-Za-z]{1,3})([1-9][0-9]*)", rng)
    if not m:
        raise ProbeError("腾讯文档 range 应为 A1:I500 这样的 A1 区间")
    left, first_row, right, last_row = (excel_column_index(m[1]), int(m[2]),
                                        excel_column_index(m[3]), int(m[4]))
    if left > right or first_row > last_row:
        raise ProbeError("腾讯文档 range 起止顺序错误")
    if not isinstance(values, list):
        raise ProbeError("腾讯文档数据格式错误")
    hdr = cfg.get("header_row", 1)
    if not isinstance(hdr, int) or isinstance(hdr, bool) or hdr < 0:
        raise ProbeError("header_row 应为 >= 0 的数字，0 表示没有表头")
    columns = cfg.get("columns")
    if columns is not None and not isinstance(columns, dict):
        raise ProbeError("columns 应为字段到列字母或表头名的映射")
    fields = ("case_id", "enabled", "content", "deviceId", "expected_keywords",
              "special_keywords", "forbidden_keywords", "match_mode", "endpoint_json",
              "session_json", "scene", "category", "owner")
    if columns is not None:
        if not all(k in fields for k in columns):
            raise ProbeError("columns 包含不支持的字段名")
        if not {"content", "deviceId", "expected_keywords"}.issubset(columns):
            raise ProbeError("columns 必须配置 content/deviceId/expected_keywords")
    if columns is None and not (first_row <= hdr <= last_row):
        raise ProbeError("按表头识别需要 header_row 落在 range 内读取")
    names = None
    for idx, row in enumerate(values):
        if not isinstance(row, list):
            raise ProbeError("腾讯文档返回的单行不是数组")
        absolute_row = first_row + idx
        if hdr and absolute_row == hdr:
            names = [str(x).strip().lstrip("\ufeff") if x is not None else "" for x in row]
            if len([n for n in names if n]) != len(set(n for n in names if n)):
                raise ProbeError("腾讯文档表头存在重复的非空列名")
            break
    if columns is None:
        if names is None:
            raise ProbeError("无法找到配置的表头行")
        columns = {field: field for field in fields if field in names}
        if not {"content", "deviceId", "expected_keywords"}.issubset(columns):
            raise ProbeError("找不到 content/deviceId/expected_keywords 表头，请配置 columns")
    # Preflight all selectors before any case execution; never silently shift a column.
    selectors = {}
    for field, selector in columns.items():
        if not isinstance(selector, str) or not selector.strip():
            raise ProbeError("列映射值应为非空字符串")
        selector = selector.strip()
        if re.fullmatch(r"[A-Za-z]{1,3}", selector):
            offset = excel_column_index(selector) - left
            if offset < 0 or offset > right - left:
                raise ProbeError("配置的列不在腾讯文档 range 范围内")
        else:
            if names is None or selector not in names:
                raise ProbeError("配置的表头名称不存在或表头行未读取")
            offset = names.index(selector)
        selectors[field] = offset
    result = []
    for idx, row in enumerate(values):
        absolute_row = first_row + idx
        if hdr and absolute_row == hdr:
            continue
        if not isinstance(row, list):
            raise ProbeError("腾讯文档返回的单行不是数组")
        if not any(c is not None and str(c).strip() for c in row):
            continue
        item = {field: row[pos] if pos < len(row) else "" for field, pos in selectors.items()}
        if not str(item.get("case_id", "")).strip():
            item["case_id"] = "ROW{:05d}".format(absolute_row)
        result.append(item)
    return result

# 第二步：读取并规范化用例字段；请求前编译规则，非法断言直接报错。
# 不允许未识别规则被忽略，再默认以 HTTP 200 当成业务成功。
def read_cases(source, tencent_docs_auth=None, stats=None, feishu_docs_auth=None):
    stype = source.get("type", "csv")
    if stype in ("tencent", "feishu"):
        cfg = source.get(stype, {})
        if stype == "tencent":
            from lib.tencent_mcp import read_values
            values = read_values(cfg, auth=tencent_docs_auth)
        else:
            from lib.feishu_sheet import read_values
            values = read_values(cfg, auth=feishu_docs_auth)
        if not values or not isinstance(values, list):
            raise ProbeError("云表格用例表为空")
        rows = tencent_rows(values, cfg)  # Same columns, no schema migration.
    elif stype in ("csv", "json"):
        path = Path(source.get("path") or "")
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise ProbeError("用例文件不存在")
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f)) if stype == "csv" else json.load(f)
        if not isinstance(rows, list):
            raise ProbeError("JSON 用例必须是数组")
        if stype == 'csv' and rows and '序号' in rows[0]:
            aliases = {'序号': 'case_id', '场景': 'scene', '类别': 'category',
                       '语料': 'content', '判断依据（公共）': 'expected_keywords',
                       '特殊判断依据': 'special_keywords', '研发负责人': 'owner'}
            rows = [{aliases.get(key, key): value for key, value in row.items()} for row in rows]
    else:
        raise ProbeError("source.type 仅支持 csv/json/tencent/feishu")
    needed = {"case_id", "content", "deviceId", "expected_keywords"}
    result, seen = [], set()
    previous_public, previous_special = [], []
    metrics = {"source_rows": len(rows), "skipped_disabled": 0,
               "skipped_missing_device_id": 0}
    for idx, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            raise ProbeError("用例数据项必须是对象")
        if not needed.issubset(row):
            raise ProbeError("缺少表头或字段：case_id/content/deviceId/expected_keywords")
        if not enabled(row.get("enabled", "true")):
            metrics["skipped_disabled"] += 1
            previous_public, previous_special = [], []
            continue
        if stype in ("tencent", "feishu") and not str(row.get("deviceId") or "").strip():
            # Skip example/section rows in Tencent sheets; report the number
            # prominently so missing production device IDs aren't hidden.
            metrics["skipped_missing_device_id"] += 1
            previous_public, previous_special = [], []
            continue
        cid = str(row["case_id"]).strip()
        if not cid or cid in seen:
            raise ProbeError("用例 case_id 为空或重复")
        seen.add(cid)
        content, dev = str(row["content"]).strip(), str(row["deviceId"]).strip()
        if not content or not dev:
            raise ProbeError("用例 content/deviceId 不得为空")
        mode = str(row.get("match_mode") or "all").strip().lower()
        if mode not in ("all", "any"):
            raise ProbeError("match_mode 只能为 all/any")
        parsed = {}
        for key in ("endpoint_json", "session_json"):
            raw = row.get(key) or "{}"
            try:
                val = raw if isinstance(raw, dict) else json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise ProbeError("endpoint_json/session_json 格式错误") from exc
            if not isinstance(val, dict):
                raise ProbeError("endpoint_json/session_json 必须为 JSON 对象")
            parsed[key] = val
        public = resolve_above(row.get("expected_keywords"), previous_public,
                               "expected_keywords", cid)
        special = resolve_above(row.get("special_keywords"), previous_special,
                                "special_keywords", cid)
        # Keep columns independently inheritable while evaluating the union
        # with AND semantics by default.
        keywords = list(dict.fromkeys(public + special))
        from lib.rule_contract import compile_rule
        for field_name, rules in (("判断依据（公共）", public),
                                  ("特殊判断依据", special)):
            for requirement in rules:
                try:
                    compile_rule(requirement)
                except ValueError as exc:
                    # Never print the potentially private sentence itself.
                    raise ProbeError("用例 {} 的 {}：{}".format(cid,field_name,str(exc))) from None
        # Forbidden keywords are intentional literal substrings, NOT positive
        # business predicates; e.g. the word "回复" may itself be forbidden.
        forbidden_words = split_words(row.get("forbidden_keywords"))
        if any(not str(value).strip() or len(str(value)) > 200 for value in forbidden_words):
            raise ProbeError("用例 {} 的禁止关键词无效".format(cid))
        previous_public, previous_special = public, special
        result.append({"case_id": cid, "scene": str(row.get("scene") or "-").strip(),
                       "category": str(row.get("category") or "").strip(),
                       "content": content, "deviceId": dev,
                       "expected_keywords": keywords,
                       "forbidden_keywords": forbidden_words,
                       "match_mode": mode, "endpoint": parsed["endpoint_json"],
                       "session": parsed["session_json"]})
    if stats is not None:
        stats.update(metrics)
        stats["skipped_rows"] = (metrics["skipped_disabled"] +
                                 metrics["skipped_missing_device_id"])
        stats["enabled_cases"] = len(result)
    if not result:
        raise ProbeError("没有启用的有效用例")
    return result

def resolve_headers(api):
    headers = {"Content-Type": "application/json",
               "Accept": "text/event-stream",
               "Cache-Control": "no-cache"}
    for name, value in api.get("headers", {}).items():
        if not isinstance(name, str) or not name:
            raise ProbeError("鉴权请求头格式非法")
        # Timestamp is runtime-controlled, even when an older private config
        # still contains a stale timestamp entry.
        if name.lower() == "timestamp":
            continue
        if isinstance(value, dict):
            value = os.environ.get(str(value.get("env", "")), "")
        if not isinstance(name, str) or not isinstance(value, str):
            raise ProbeError("鉴权请求头格式非法")
        if not name or not value.strip():
            raise ProbeError("认证请求头未填写")
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise ProbeError("请求头禁止换行")
        headers[name] = value
    for key in ("powerkey", "signature"):
        if not any(k.lower() == key for k in headers):
            raise ProbeError("缺少必要认证请求头")
    # Unix epoch milliseconds, generated independently for every POST.
    headers["timestamp"] = str(time.time_ns() // 1_000_000)
    return headers

# 第三步：把 HTTP 字节流解析为 SSE 事件；只做协议拆分，不做语义判断。
def sse_events(lines):
    """SSE event protocol: comments, multi-line data, blank-line framing."""
    data, event = [], "message"
    for line in lines:
        try:
            text = line.decode("utf-8", errors="strict").rstrip("\r\n")
        except UnicodeError as exc:
            raise SSEError("SSE 非 UTF-8 编码") from exc
        if not text:
            if data:
                yield event, "\n".join(data)
            data, event = [], "message"
            continue
        if text.startswith(":"):
            continue
        field, _, value = text.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
    if data:
        yield event, "\n".join(data)


_RESPONSE_PREVIEW_LIMIT = 12000

def _response_parts(frame):
    """Only collect allowlisted assistant business fields; never raw SSE."""
    choices = frame.get("choices")
    if not isinstance(choices, list):
        return []
    parts = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        content = delta.get("content") if isinstance(delta, dict) else None
        data = content.get("data") if isinstance(content, dict) else None
        if not isinstance(data, dict):
            continue
        for field in ("text", "title", "jsonBody", "jsonHeader"):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                parts.append((field, value))
    return parts

_SECRET_KV = re.compile(
    r"""(?i)(\b(?:powerkey|signature|authorization|access[_-]?token|token|deviceid|secret)\b\s*["']?\s*[:=]\s*["']?)[^\s,;；，|}"']+"""
)

def _redact_diagnostic(value, case, api):
    """Defensive redaction, not a guarantee the business content is public."""
    text = str(value)
    private = [case.get("deviceId")]
    for name, secret in (api.get("headers") or {}).items():
        if isinstance(secret, str) and name.lower() in (
            "powerkey", "signature", "authorization", "token", "secret"):
            private.append(secret)
    for secret in private:
        if isinstance(secret, str) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return _SECRET_KV.sub(lambda match: match.group(1) + "[REDACTED]", text)

def _failure_assertion_details(result, case, api, capture_metadata=None, signals=None):
    """Explain the exact failed conditions without inventing an observed value."""
    reason = result.get("reason")
    details = []
    missing = set(result.get("missing_keywords") or [])
    forbidden = list(result.get("forbidden_found") or [])
    observations = (signals or {}).get('field_rule_observations', {})
    for index, condition in enumerate(expand_expected_keywords(case.get("expected_keywords") or []), 1):
        if condition in missing:
            field_observation = observations.get(condition)
            if field_observation:
                kind = "json_field_" + field_observation["operation"]
            elif condition.startswith("HTTP "):
                kind = "http_status"
            elif condition == "文本回复":
                kind = "assistant_text_nonempty"
            elif condition.startswith("文本回复 包含"):
                kind = "assistant_text_contains"
            elif condition == "衍生问题":
                kind = "followup_questions"
            elif condition == "出行建议":
                kind = "travel_advice"
            else:
                kind = "expected_keyword"
            details.append({
                "rule_id": "expected-{:02d}".format(index),
                "rule_type": kind, "condition": condition,
                "expected": field_observation["expected"] if field_observation else condition,
                "actual": (_redact_diagnostic(field_observation["actual"], case, api)
                           if field_observation else "未匹配该判断条件"),
                "field_path": field_observation["path"] if field_observation else None,
                "passed": False,
                "failure_reason": ("structured_field_condition_not_met" if field_observation
                                   else "expected_condition_not_met"),
            })
    for index, condition in enumerate(expand_expected_keywords(case.get("forbidden_keywords") or []), 1):
        if condition in forbidden:
            details.append({"rule_id": "forbidden-{:02d}".format(index),
                            "rule_type": "forbidden_keyword", "condition": condition,
                            "expected": "响应中不得出现该词",
                            "actual": "命中禁止词：" + str(condition),
                            "passed": False,
                            "failure_reason": "forbidden_condition_matched"})
    if details:
        return details
    response = (capture_metadata or {}).get("response") or {}
    response_headers = response.get("headers") or []
    content_type = next((v for k, v in response_headers
                         if str(k).lower() == "content-type"), None)
    if reason == "missing_assertions":
        details.append({"rule_id": "assertions-configured", "rule_type": "assertion_configuration",
                        "condition": "至少配置一项有效判断依据", "expected": ">= 1 项",
                        "actual": "0 项有效判断依据", "passed": False,
                        "failure_reason": "no_valid_assertions"})
    elif reason == "unexpected_http_status":
        details.append({"rule_id": "protocol-http-status", "rule_type": "http_status",
                        "condition": "HTTP 状态码符合配置", "expected": int(api.get("expected_http_status", 200)),
                        "actual": result.get("http_status"), "passed": False,
                        "failure_reason": "unexpected_http_status"})
    elif reason == "unexpected_content_type":
        details.append({"rule_id": "protocol-content-type", "rule_type": "content_type",
                        "condition": "响应为 SSE", "expected": "text/event-stream",
                        "actual": content_type or "未收到 Content-Type", "passed": False,
                        "failure_reason": "unexpected_content_type"})
    elif reason == "no_json_frames":
        details.append({"rule_id": "protocol-json-frame", "rule_type": "json_frame",
                        "condition": "至少收到一个合法 JSON 业务帧", "expected": ">= 1",
                        "actual": result.get("json_frame_count", 0), "passed": False,
                        "failure_reason": "no_json_frames"})
    elif reason == "done_missing":
        details.append({"rule_id": "protocol-done", "rule_type": "sse_terminator",
                        "condition": "SSE 正常结束标记", "expected": "[DONE]",
                        "actual": "未收到 [DONE]", "passed": False,
                        "failure_reason": "done_missing"})
    elif reason == "invalid_sse_json":
        details.append({"rule_id": "protocol-json-parse", "rule_type": "json_parse",
                        "condition": "每个业务 data 帧均为合法 JSON", "expected": "合法 JSON 对象",
                        "actual": "第 {} 个事件帧解析失败；原文见完整 SSE 证据".format(result.get("event_count", 0)),
                        "passed": False, "failure_reason": "invalid_sse_json"})
    elif reason == "invalid_sse_json_shape":
        details.append({"rule_id": "protocol-json-shape", "rule_type": "json_shape",
                        "condition": "业务帧顶层为 JSON 对象", "expected": "object",
                        "actual": "收到非对象 JSON；原文见完整 SSE 证据", "passed": False,
                        "failure_reason": "invalid_sse_json_shape"})
    elif reason == "sse_error_event":
        details.append({"rule_id": "protocol-sse-event", "rule_type": "sse_event",
                        "condition": "不得返回 error 事件", "expected": "非 error 事件",
                        "actual": "收到 event:error", "passed": False,
                        "failure_reason": "sse_error_event"})
    elif reason == "upstream_business_error":
        details.append({"rule_id": "business-upstream-status", "rule_type": "upstream_status",
                        "condition": "上游业务响应无 error 状态", "expected": "无 error/status=error",
                        "actual": "响应帧包含业务 error 状态；详情见完整 SSE 证据",
                        "passed": False, "failure_reason": "upstream_business_error"})
    else:
        phase = result.get("error_phase") or "unknown"
        details.append({"rule_id": "transport-{}".format(reason or "unknown"),
                        "rule_type": "transport", "condition": "请求完整接收并完成协议处理",
                        "expected": "完整 HTTP/SSE 响应", "actual": "{}（阶段：{}）".format(
                            reason or "未知错误", phase), "passed": False,
                        "failure_reason": reason or "unknown_failure"})
    return details


# 第四步：发出真实请求，收集原始 SSE、HTTP 信息、耗时与失败证据。
# v2 调用时 defer_assertions=True：先落盘、后离线断言，避免请求与判定耦合。
def run_case(case, api, assertions=None, opener=None, raw_capture=None,
             capture_metadata=None, defer_assertions=False, request_id=None):
    """Capture raw response bytes separately from safe result/report metadata."""
    assertions = assertions or {}
    if capture_metadata is not None:
        capture_metadata.clear()
        capture_metadata.update({"request": None, "response": {
            "headers": [], "headers_received": False}})
    started = time.monotonic()
    response_parts, response_length, response_truncated = [], 0, False
    tracker = None
    signals = {"valid_followup_questions": False, "answer_text_fragments": [],
               "business_records": []}
    r = {"case_id": case["case_id"], "status": "ERROR", "reason": "unexpected_error",
         "http_status": None, "reqId": None, "event_count": 0, "json_frame_count": 0,
         "done_received": False, "first_event_ms": None, "first_token_ms": None,
         "duration_ms": None, "error_phase": None, "exception_type": None,
         "exception_chain": [], "exception_errno": None, "timeout_kind": None,
         "http_headers_received": False, "response_bytes_received": 0,
         "stage_timings_ms": {},
         "matched_keywords": [], "missing_keywords": [], "forbidden_found": []}
    def error_metadata(exc):
        chain, seen = [], set()
        current = exc
        while current is not None and id(current) not in seen and len(chain) < 5:
            seen.add(id(current))
            chain.append(type(current).__name__)
            if r["exception_errno"] is None and isinstance(getattr(current, "errno", None), int):
                r["exception_errno"] = current.errno
            nxt = current.__cause__ or current.__context__
            if nxt is None and isinstance(current, URLError):
                reason = getattr(current, "reason", None)
                if isinstance(reason, BaseException):
                    nxt = reason
            current = nxt
        r["exception_chain"] = chain

    try:
        endpoint = case["endpoint"] or api.get("body_defaults", {}).get("endpoint", {})
        session = case["session"] or api.get("body_defaults", {}).get("session", {})
        body = {"endpoint": endpoint, "stream": True, "session": session,
                "messages": [{"role": "user", "content": case["content"]}],
                "deviceId": case["deviceId"],
                "reqId": str(uuid.UUID(request_id)) if request_id is not None
                else str(uuid.uuid4())}
        r["reqId"] = body["reqId"]
        url = str(api.get("url") or "")
        allow_http = os.environ.get("API_SSE_PROBE_ALLOW_HTTP_LOCALHOST") == "1"
        if not url.startswith("https://") and not (
            allow_http and (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:"))
        ):
            raise ProbeError("出于密钥安全考虑，仅允许 HTTPS URL")
        if api.get("verify_tls", True) is False:
            raise ProbeError("禁止关闭 TLS 验证")
        ctimeout = float(api.get("connect_timeout_seconds", 8))
        header_timeout = float(api.get("response_header_timeout_seconds", 20))
        idle = float(api.get("idle_timeout_seconds", 20))
        total = float(api.get("total_timeout_seconds", 60))
        tracker = PhaseTracker(started, total)
        event_max = int(api.get("max_event_bytes", 1048576))
        total_max = int(api.get("max_response_bytes", 4194304))
        if min(ctimeout, header_timeout, idle, total, event_max, total_max) <= 0:
            raise ProbeError("超时与响应大小限制必须大于零")
        headers = resolve_headers(api)
        request_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=request_bytes, headers=headers, method="POST")
        if capture_metadata is not None:
            capture_metadata["request"] = {
                "method": req.get_method(), "url": url,
                "headers": header_pairs(req.header_items()),
                "body_base64": base64.b64encode(request_bytes).decode("ascii"),
                "body_bytes": len(request_bytes),
                "body_sha256": hashlib.sha256(request_bytes).hexdigest(),
                "body_json": body, "device_id": body.get("deviceId"),
                "req_id": body.get("reqId"),
            }
        if opener is None:
            transport = open_staged(req, tracker, ctimeout, header_timeout, idle)
        else:
            # Explicit injected openers retain their historical testing API.
            # The production transport above has independently bounded phases.
            tracker.enter("response_headers")
            transport = opener.open(req, timeout=tracker.remaining(header_timeout))
        with transport as resp:
            tracker.headers_received = True
            r["http_headers_received"] = True
            status = resp.status
            r["http_status"] = status
            if capture_metadata is not None:
                pairs = header_pairs(resp.headers)
                capture_metadata["response"] = {
                    "status": status, "headers": pairs,
                    "headers_received": True,
                    "content_type": next((v for k, v in pairs
                                           if str(k).lower() == "content-type"), None),
                    "body_kind": "sse" if "text/event-stream" in str(
                        resp.headers.get("Content-Type", "")).lower() else "non_sse_http_body",
                }
            # Preserve the entire *received* error body before reporting an
            # HTTP/content-type failure. The old early return lost that data.
            mismatch_reason = None
            if status != int(api.get("expected_http_status", 200)):
                mismatch_reason = "unexpected_http_status"
            elif "text/event-stream" not in resp.headers.get("Content-Type", "").lower():
                mismatch_reason = "unexpected_content_type"
            sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
            if sock:
                sock.settimeout(idle)
            count = 0
            def lines():
                nonlocal count
                reader = getattr(resp, "read1", None)
                if not callable(reader):
                    reader = getattr(getattr(resp, "fp", None), "read1", None)
                if callable(reader):
                    pending = bytearray()
                    while True:
                        if time.monotonic() - started >= total:
                            raise TimeoutError("总超时")
                        chunk = reader(8192)
                        if chunk and raw_capture is not None:
                            raw_capture.write(chunk)
                        if chunk and opener is not None:
                            tracker.response_bytes += len(chunk)
                        if not chunk:
                            if pending:
                                yield bytes(pending)
                            break
                        count += len(chunk)
                        if count > total_max:
                            raise SSEError("SSE 数据体超出限制")
                        pending.extend(chunk)
                        while True:
                            end = pending.find(b"\n")
                            if end < 0:
                                break
                            line_end = end + 1
                            if line_end > event_max:
                                raise SSEError("SSE 数据体超出限制")
                            yield bytes(pending[:line_end])
                            del pending[:line_end]
                        if len(pending) > event_max:
                            raise SSEError("SSE 数据体超出限制")
                    return
                while True:
                    if time.monotonic() - started >= total:
                        raise TimeoutError("总超时")
                    chunk = resp.readline(event_max + 1)
                    if chunk and raw_capture is not None:
                        raw_capture.write(chunk)
                    if chunk and opener is not None:
                        tracker.response_bytes += len(chunk)
                    if not chunk:
                        break
                    count += len(chunk)
                    if len(chunk) > event_max or count > total_max:
                        raise SSEError("SSE 数据体超出限制")
                    yield chunk
            if mismatch_reason:
                # Drain even non-SSE HTTP error bodies (bounded by total/event
                # size and absolute deadline); preserve the original verdict.
                for _ in lines():
                    pass
                r.update(status="FAIL", reason=mismatch_reason)
                return r
            terminal_stream_errors = {
                'sse_error_event', 'invalid_sse_json',
                'invalid_sse_json_shape', 'upstream_business_error',
            }
            for event, data in sse_events(lines()):
                if r["first_event_ms"] is None:
                    r["first_event_ms"] = int((time.monotonic() - started) * 1000)
                r["event_count"] += 1
                if data.strip() == "[DONE]":
                    r["done_received"] = True
                    # Drain through EOF so the private capture contains every
                    # byte the server actually sent, including trailing frames.
                    continue
                if r["done_received"] or r["reason"] in terminal_stream_errors:
                    continue
                if event.lower() == "error":
                    r.update(status="FAIL", reason="sse_error_event")
                    continue
                try:
                    frame = json.loads(data)
                except ValueError:
                    r.update(status="FAIL", reason="invalid_sse_json")
                    continue
                if not isinstance(frame, dict):
                    r.update(status="FAIL", reason="invalid_sse_json_shape")
                    continue
                if frame.get("error") or frame.get("status") == "error":
                    r.update(status="FAIL", reason="upstream_business_error")
                    continue
                r["json_frame_count"] += 1
                if r["first_token_ms"] is None:
                    # TTFT proxy: time to the first valid business JSON frame,
                    # not a model-token emission timestamp.
                    r["first_token_ms"] = int((time.monotonic() - started) * 1000)
                accumulate_signals(frame, signals)
                for field, value in _response_parts(frame):
                    # Preserve a bounded assistant-only diagnostic per case.
                    segment = ("[{}] ".format(field) if field != "text" else "") + value
                    remaining = _RESPONSE_PREVIEW_LIMIT - response_length
                    if remaining > 0:
                        fragment = segment[:remaining]
                        response_parts.append(fragment)
                        response_length += len(fragment)
                    if len(segment) > max(remaining, 0):
                        response_truncated = True
            if r["reason"] in terminal_stream_errors:
                pass  # Preserve the first protocol/business error after draining.
            elif r["json_frame_count"] == 0:
                r.update(status="FAIL", reason="no_json_frames")
            elif api.get("require_done", True) and not r["done_received"]:
                r.update(status="FAIL", reason="done_missing")
            elif defer_assertions:
                # v2 capture-only path: no business assertions until all capture
                # evidence is committed. Legacy default behavior stays unchanged.
                r.update(status="CAPTURED", reason="capture_only")
            else:
                found, missing, wanted = evaluate_assertions(
                    case["expected_keywords"], [], r["http_status"], signals)
                corpus = signals['assertion_corpus']
                forbidden = [k for k in expand_expected_keywords(case["forbidden_keywords"]) if k in corpus]
                if not wanted and assertions.get("require_keywords", True):
                    r.update(status="FAIL", reason="missing_assertions")
                else:
                    match = (len(found) == len(wanted) if case["match_mode"] == "all"
                             else bool(found))
                    invalid_rules = signals.get('invalid_assertions') or []
                    r.update(status="PASS" if match and not forbidden and not invalid_rules else "FAIL",
                             reason=("invalid_assertion" if invalid_rules else
                                     "ok" if match and not forbidden else "assertions_failed"),
                             matched_keywords=found, missing_keywords=missing,
                             forbidden_found=forbidden,
                             assertion_engine_version="deterministic-1",
                             assertion_trace=signals.get('rule_trace', []))
    except HTTPError as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="http_error", http_status=exc.code)
        if tracker:
            tracker.headers_received = True
            tracker.phase = "response_headers"
        if capture_metadata is not None:
            pairs = header_pairs(exc.headers)
            capture_metadata["response"] = {
                "status": exc.code, "headers": pairs,
                "headers_received": True,
                "content_type": next((v for k, v in pairs
                                       if str(k).lower() == "content-type"), None),
                "body_kind": "http_error_body",
            }
        try:
            limit = int(api.get("max_response_bytes", 4194304))
            error_body = exc.read(limit + 1)
        except Exception:
            error_body = b""
        truncated = len(error_body) > limit if "limit" in locals() else False
        captured_error_body = error_body[:limit] if truncated else error_body
        if raw_capture is not None and captured_error_body:
            raw_capture.write(captured_error_body)
        if tracker:
            tracker.response_bytes = len(captured_error_body)
        if capture_metadata is not None:
            capture_metadata["response"]["body_truncated"] = truncated
        r["response_bytes_received"] = len(captured_error_body)
        r["exception_type"] = type(exc).__name__
    except PhaseTimeout as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="timeout", timeout_kind=exc.kind,
                 error_phase=exc.phase, exception_type=type(exc).__name__)
    except (socket.timeout, TimeoutError) as exc:
        error_metadata(exc)
        phase = tracker.phase if tracker else "configuration"
        r.update(status="ERROR", reason="timeout", error_phase=phase,
                 timeout_kind=("total_deadline_exceeded" if tracker and
                               time.monotonic() >= tracker.deadline else
                               "phase_timeout"), exception_type=type(exc).__name__)
    except ssl.SSLError as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="tls_error",
                 error_phase=tracker.phase if tracker else "configuration",
                 exception_type=type(exc).__name__)
    except (URLError, OSError) as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="network_error",
                 error_phase=getattr(exc,"phase",tracker.phase if tracker else "configuration"),
                 exception_type=type(exc).__name__)
        if tracker and time.monotonic() >= tracker.deadline:
            r.update(reason="timeout", timeout_kind="total_deadline_exceeded")
    except (ValueError, ProbeError) as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="configuration_error",
                 error_phase=tracker.phase if tracker else "configuration",
                 exception_type=type(exc).__name__)
    except SSEError as exc:
        error_metadata(exc)
        r.update(status="FAIL", reason="sse_decode_or_size_error",
                 error_phase="sse_parse", exception_type=type(exc).__name__)
    except Exception as exc:
        error_metadata(exc)
        r.update(status="ERROR", reason="unexpected_error",
                 error_phase=tracker.phase if tracker else "configuration",
                 exception_type=type(exc).__name__)
    finally:
        if tracker:
            tracker.finalize()
            r["stage_timings_ms"]=dict(tracker.timings)
            r["http_headers_received"]=tracker.headers_received
            r["response_bytes_received"]=tracker.response_bytes
        r["duration_ms"] = int((time.monotonic() - started) * 1000)
        if r["status"] not in ("PASS", "CAPTURED") and not r["error_phase"]:
            if not r["http_headers_received"]:
                r["error_phase"] = tracker.phase if tracker else "configuration"
            elif r["reason"] in ("unexpected_http_status","unexpected_content_type","http_error"):
                r["error_phase"] = "response_headers"
            elif r["reason"] in ("assertions_failed","missing_assertions"):
                r["error_phase"] = "business_assertion"
            else:
                r["error_phase"] = "sse_parse"
        if r["status"] != "PASS" and not defer_assertions:
            r["response_excerpt"] = _redact_diagnostic(
                "".join(response_parts), case, api)
            r["response_truncated"] = response_truncated
            r["failure_assertions"] = _failure_assertion_details(
                r, case, api, capture_metadata, signals)
            if capture_metadata is not None:
                r["diagnostic_context"] = safe_context(capture_metadata)
        if capture_metadata is not None:
            response_context = capture_metadata.setdefault("response", {})
            response_context["response_bytes_received"] = int(
                response_context.get("response_bytes_received", r.get("response_bytes_received", 0)) or 0)
            if response_context.get("body_truncated") or r.get("reason") == "sse_decode_or_size_error":
                response_context["capture_state"] = "partial_or_size_limit"
            elif r.get("done_received"):
                response_context["capture_state"] = "protocol_done"
            elif raw_capture is not None and raw_capture.getvalue():
                response_context["capture_state"] = "partial_or_unterminated"
            else:
                response_context["capture_state"] = "not_received"
        if defer_assertions:
            # Do not persist protocol/business verdicts in capture phase.
            # Retain observations for offline assertion and protocol judge.
            r["capture_observation"] = {"status":r["status"],"reason":r["reason"]}
            if r["status"] != "ERROR":
                r.update(status="CAPTURED",reason="capture_only")
    return r

def write_reports(source, cfg, results, cases=None, wall_duration_ms=None):
    """Backward-compatible facade delegating document modeling and file IO."""
    from lib.report_document import build_report_document
    from lib.outputs.registry import dispatch_report_outputs
    document = build_report_document(source, cfg, results, cases, wall_duration_ms)
    receipts = dispatch_report_outputs(document, cfg, ROOT)
    local = next((x for x in receipts if x["type"] == "file"), None)
    if local is None:
        raise ValueError("local_report_sink_missing")
    return document["summary"], Path(local["json_report"]), Path(local["markdown_report"])


def _summarize(results, threshold_ms=DEFAULT_SLOW_TOKEN_THRESHOLD_MS, wall_duration_ms=None):
    return build_analysis(results, threshold_ms=threshold_ms,
                          wall_duration_ms=wall_duration_ms)[0]

def _legacy_main(argv=None):
    p = argparse.ArgumentParser(description="SSE API 拨测")
    p.add_argument("--config", default="config.local.json")
    p.add_argument("--check", action="store_true")
    p.add_argument("--source", choices=("csv", "json", "tencent"))
    p.add_argument("--cases")
    p.add_argument("--case-id")
    p.add_argument("--source-url", help="本轮腾讯文档完整链接，优先于配置中的文档身份，不写回配置")
    p.add_argument("--no-feishu", action="store_true", help="本轮关闭飞书通知，不修改私有配置")
    args = p.parse_args(argv)
    cfg, results, source_name, source = None, [], "unknown", {}
    cases, enriched, failure_cases, slow_token_cases = [], [], [], []
    report_document = None
    summary = _summarize(results)
    outcome, exit_code = "ERROR", 2
    notification_preflight_error = None
    try:
        path = Path(args.config)
        if not path.is_absolute():
            path = ROOT / path
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or "api" not in cfg or (
                "source" not in cfg and not args.source_url):
            raise ProbeError("配置文件必须包含 api 和 source（或使用 --source-url）")
        from lib.outputs.registry import validate_report_outputs
        validate_report_outputs(cfg)  # fail closed BEFORE any business POST
        configured_source = cfg.get("source", {})
        if not isinstance(configured_source, dict):
            raise ProbeError("source 配置必须是对象")
        source = dict(configured_source)
        if args.source_url and (args.cases or (args.source and args.source != "tencent")):
            raise ProbeError("--source-url 不能与 --cases 或其他 source.type 同时使用")
        if args.source_url:
            source = apply_tencent_source_url(source, args.source_url)
        if args.source:
            source["type"] = args.source
        if args.cases:
            source["path"] = args.cases
        # The runtime source governs reports too; the private file is never changed.
        cfg["source"] = source
        if args.no_feishu:
            cfg["feishu"] = dict(cfg.get("feishu") or {}, enabled=False)
        source_name = str(source.get("type", "unknown"))
        threshold_ms = slow_threshold(cfg.get("reports", {}))
        source_stats = {}
        cases = read_cases(source, cfg.get("tencent_docs_auth"), stats=source_stats)
        if args.case_id:
            cases = [x for x in cases if x["case_id"] == args.case_id]
        if not cases:
            raise ProbeError("指定用例不存在/未启用")
        if args.check:
            print(json.dumps({"case_schema_valid": True, "enabled_cases": len(cases),
                              "case_ids": [x["case_id"] for x in cases], "source": source_name,
                              "auth_headers_checked": False,
                              "source_rows": source_stats.get("source_rows", 0),
                              "skipped_rows": source_stats.get("skipped_rows", 0),
                              "skipped_missing_device_id": source_stats.get("skipped_missing_device_id", 0),
                              "skipped_disabled": source_stats.get("skipped_disabled", 0)}, ensure_ascii=False))
            return 0
        from lib.feishu import FeishuDeliveryError
        from lib.feishu_notification_router import validate_probe_notification
        try:
            validate_probe_notification(cfg.get("feishu", {}))
        except FeishuDeliveryError as exc:
            notification_preflight_error = str(exc)
            raise
        # Raw SSE is transient in memory for each case only; a per-target
        # private .pending snapshot records just FAIL/ERROR, replacing the
        # previous .json only after the whole batch and normal reports finish.
        from lib.failed_sse import LatestFailureCapture
        report_settings = cfg.get("reports", {})
        report_root = Path(report_settings.get("dir", "reports"))
        if not report_root.is_absolute():
            report_root = ROOT / report_root
        with LatestFailureCapture(report_root, cfg["api"], source,
                                  report_settings.get("failed_sse_max_total_bytes")) as raw_store:
            batch_started = time.monotonic()
            for case in cases:
                capture = io.BytesIO()
                capture_metadata = {}
                result = run_case(case, cfg["api"], cfg.get("assertions"),
                                  raw_capture=capture,
                                  capture_metadata=capture_metadata)
                results.append(result)
                raw_bytes = capture.getvalue()
                evidence = raw_store.add(result, raw_bytes, capture_metadata)
                if evidence is not None:
                    result["raw_evidence"] = evidence
                    try:
                        result["raw_sse_text"] = raw_bytes.decode("utf-8")
                        result["raw_sse_encoding"] = "UTF-8"
                    except UnicodeDecodeError:
                        # Keep an exact byte-level snapshot and a readable escaped text view.
                        result["raw_sse_text"] = raw_bytes.decode("utf-8", "backslashreplace")
                        result["raw_sse_encoding"] = "UTF-8 with invalid bytes escaped as \\xNN"
                capture.close()
            wall_ms = int((time.monotonic() - batch_started) * 1000)
            from lib.report_document import build_report_document
            from lib.outputs.registry import dispatch_report_outputs
            report_document = build_report_document(
                source_name, cfg, results, cases, wall_ms)
            summary = report_document["summary"]
            enriched = report_document["results"]
            failure_cases = report_document["failure_cases"]
            slow_token_cases = report_document["slow_token_cases"]
            outcome = "PASS" if summary["pass"] == summary["total"] else "FAIL"
            output_receipts = dispatch_report_outputs(report_document, cfg, ROOT)
            local = next((x for x in output_receipts if x["type"]=="file"), {})
            jf, mf = local.get("json_report"), local.get("markdown_report")
            raw_store.finish(len(results))
            failed_sse_path = raw_store.commit()
        print(json.dumps({"summary": public_summary(summary),
                          "json_report": jf, "markdown_report": mf,
                          "report_outputs": output_receipts,
                          "failed_sse_report": str(failed_sse_path),
                          "failed_sse_cases": raw_store.failure_count},
                         ensure_ascii=False))
        exit_code = 0 if outcome == "PASS" else 1
    except (ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
        # Do not print remote error strings; they could contain credentials.
        summary = _summarize(results)
        outcome, exit_code = "ERROR", 2
        print(json.dumps({"error": type(exc).__name__,
                          "detail": "配置、用例读取或报告写入失败，请检查本地配置和权限"},
                         ensure_ascii=False), file=sys.stderr)
    if not args.check:
        if notification_preflight_error is not None:
            print(json.dumps({"feishu_notification": "failed",
                              "reason": notification_preflight_error,
                              "cards_attempted": 0}, ensure_ascii=False), file=sys.stderr)
            return exit_code
        from lib.feishu import FeishuDeliveryError
        from lib.feishu_notification_router import send_probe_notice, PagedDeliveryError
        if not isinstance(cfg, dict):
            # An unreadable config does not expose a webhook; cannot deliver.
            print(json.dumps({"feishu_notification": "unavailable",
                              "reason": "configuration_not_readable"}, ensure_ascii=False),
                  file=sys.stderr)
        else:
            try:
                feishu_cfg = cfg.get("feishu", {})
                from lib.report_document import public_projection
                # Only the explicit notification allowlist may cross the Feishu boundary.
                visible_failures = (public_projection(report_document)["failure_cases"]
                                    if isinstance(report_document, dict) else [])
                delivery = send_probe_notice(feishu_cfg, summary,
                                             enriched if enriched else results,
                                             source_name, outcome,
                                             failure_cases=visible_failures,
                                             slow_token_cases=slow_token_cases,
                                             source_config=source)
                print(json.dumps({"feishu_notification": delivery}, ensure_ascii=False))
            except PagedDeliveryError as exc:
                print(json.dumps({"feishu_notification": "partial",
                                  "delivery": exc.details()}, ensure_ascii=False),
                      file=sys.stderr)
                if exit_code == 0:
                    exit_code = 3
            except FeishuDeliveryError as exc:
                print(json.dumps({"feishu_notification": "failed",
                                  "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
                if exit_code == 0:
                    exit_code = 3
            except Exception as exc:
                # Never expose exception messages (could contain request URL).
                print(json.dumps({"feishu_notification": "failed",
                                  "reason": "notification_unexpected_" + type(exc).__name__},
                                 ensure_ascii=False), file=sys.stderr)
                if exit_code == 0:
                    exit_code = 3
    return exit_code

# 正式公开入口：委托 unified_workflow 组织各阶段；v2_engine 为内部阶段执行器。
def main(argv=None):
    """Only public workflow entry; all real requests use durable v2."""
    from lib.unified_workflow import main as run_workflow
    return run_workflow(argv)

if __name__ == "__main__":
    raise SystemExit(main())