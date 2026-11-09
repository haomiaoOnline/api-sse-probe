"""Feishu custom group robot notifications.

Use seconds for Feishu signatures; probe API uses milliseconds.
Never log secret, webhook URL, raw SSE or deviceId. Request utterances and
response excerpts are never sent, even with deprecated opt-in flags.
"""
import base64
import hashlib
import hmac
import json
import re
import ssl
import time
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from lib.feishu_transport import (FeishuDeliveryError, _NoRedirect, _required, _webhook,
                                  generate_sign, send_card_payload)

_NOTIFICATION_SECRET_KV = re.compile(
    r"""(?i)(\b(?:powerkey|signature|authorization|access[_-]?token|token|deviceid|secret)\b\s*["']?\s*[:=]\s*["']?)[^\s,;；，|}"']+"""
)

def _safe(value, limit=70):
    # User-supplied assertions can contain request-like secrets. Never render
    # credential assignment values even within the missing-term list.
    cleaned = _NOTIFICATION_SECRET_KV.sub(
        lambda match: match.group(1) + "[REDACTED]", str(value or ""))
    text = re.sub(r"[\r\n\t|*\x60<>{}\[\]@]", " ", cleaned).strip()
    return text[:limit] or "-"

from lib.reporting import display_seconds

def _card(summary, results, source, outcome, keyword, limit_bytes=17000,
          failure_cases=None, slow_token_cases=None):
    """Prioritized: summary -> actionable failures -> slow TTFT. No all-case list."""
    title = (keyword or "API拨测") + "｜" + ("全部通过" if outcome == "PASS" else "异常提醒")
    summary = summary or {}
    total = int(summary.get("total", 0))
    passed = int(summary.get("pass", 0))
    failed = int(summary.get("fail", 0))
    errors = int(summary.get("error", 0))
    pass_rate = summary.get("pass_rate", round(100 * passed / total, 2) if total else 0.0)
    avg = summary.get("average_latency_ms")
    total_ms = summary.get("total_duration_ms")
    fastest = summary.get("fastest_ms")
    slowest = summary.get("slowest_ms")
    wall = summary.get("wall_duration_ms")
    threshold = summary.get("slow_token_threshold_ms", 3000)

    def sec(value):
        return display_seconds(value)

    prefix = [
        "**" + _safe(title, 120) + "**",
        "**执行时间：** " + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "**数据源：** " + _safe(source, 20),
        "**用例：** " + str(total) + "　✅ " + str(passed) +
        "　❌ " + str(failed) + "　⚠️ " + str(errors) +
        "　通过率 " + str(pass_rate) + "%",
        "**首业务帧耗时：** 均值 " + sec(avg) + "　最快 " + sec(fastest) +
        "　最慢 " + sec(slowest) + "　有效 " +
        str(summary.get("first_token_sample_count", "-")) + "/" + str(total),
        "**总请求耗时：** 累计 " + sec(total_ms) + "　本轮 " + sec(wall),
    ]
    if not results and outcome != "PASS":
        prefix.append("**异常阶段：** 配置、用例读取或报告写入失败；请检查本地日志")

    failures = failure_cases if failure_cases is not None else [
        {"case_id": r.get("case_id"), "scene": r.get("scene"),
         "failure_brief": r.get("reason"), "duration_ms": r.get("duration_ms"),
         "first_token_ms": r.get("first_token_ms"),
         "missing_keywords": r.get("missing_keywords", []),
         "forbidden_found": r.get("forbidden_found", [])}
        for r in results if r.get("status") != "PASS"]
    slow = slow_token_cases if slow_token_cases is not None else [
        {"case_id": r.get("case_id"), "scene": r.get("scene"),
         "first_token_ms": r.get("first_token_ms"), "duration_ms": r.get("duration_ms")}
        for r in results if isinstance(r.get("first_token_ms"), (int, float)) and
                            r.get("first_token_ms") >= threshold]

    sections = []
    if failures:
        failure_lines = []
        for f in failures:
            missing = f.get("missing_keywords") or []
            forbidden = f.get("forbidden_found") or []
            line = ("❌ " + _safe(f.get("case_id"), 45) + " / " +
                    _safe(f.get("scene"), 30) + " / " +
                    _safe(f.get("failure_brief") or f.get("reason"), 55) +
                    " / 首帧 " + sec(f.get("first_token_ms")) +
                    " / 总 " + sec(f.get("duration_ms")))
            if missing:
                line += " / 缺失：" + _safe("；".join(map(str, missing)), 220)
            if forbidden:
                line += " / 命中禁止词：" + _safe("；".join(map(str, forbidden)), 160)
            if f.get("response_available"):
                line += " / 实际响应：见本地报告"
            failure_lines.append(line)
        sections.append(("**失败用例（含 ERROR）：**", failure_lines))
    if slow:
        sections.append(("**慢 Token 用例（首业务帧 ≥ " + sec(threshold) + "）：**", [
            "🐢 " + _safe(f.get("case_id"), 45) + " / " +
            _safe(f.get("scene"), 30) + " / 首帧 " +
            sec(f.get("first_token_ms")) + " / 总 " + sec(f.get("duration_ms"))
            for f in slow]))

    template = "green" if outcome == "PASS" else ("red" if outcome == "FAIL" else "orange")
    def pack(content):
        return {"msg_type": "interactive", "card": {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": _safe(title, 120)},
                       "template": template},
            "elements": [{"tag": "markdown", "content": content}]}}

    displayed = 0
    total_lines = sum(len(lines) for _, lines in sections)
    assembled = list(prefix)
    full = False
    for heading, details in sections:
        if full:
            break
        if not details:
            continue
        candidate = assembled + [heading]
        if len(json.dumps(pack("\n".join(candidate)), ensure_ascii=False).encode("utf-8")) > limit_bytes:
            break
        assembled.append(heading)
        for line in details:
            candidate = assembled + [line, f"（显示 {displayed+1}/{total_lines} 条）"]
            if len(json.dumps(pack("\n".join(candidate)), ensure_ascii=False).encode("utf-8")) > limit_bytes:
                full = True
                break
            assembled.append(line)
            displayed += 1
    if total_lines:
        assembled.append(f"（显示 {displayed}/{total_lines} 条）")
    payload = pack("\n".join(assembled))
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > limit_bytes:
        raise FeishuDeliveryError("notification_payload_too_large")
    return payload

def send_notification(settings, summary, results, source, outcome, opener=None,
                      failure_cases=None, slow_token_cases=None):
    """Attempt one webhook delivery; require HTTP 200 and Feishu code 0."""
    if not isinstance(settings, dict):
        raise FeishuDeliveryError("feishu_config_invalid")
    if settings.get("enabled", True) is False:
        return "disabled"
    # Uppercase keys match the operators' configuration terminology.
    # Retain lowercase aliases for existing integrations.
    webhook = _webhook(settings.get("WEBHOOK_URL") or settings.get("webhook_url", ""))
    secret = _required(settings.get("SECRET") or settings.get("secret", ""), "secret")
    keyword = settings.get("keyword", "API拨测")
    if not isinstance(keyword, str) or len(keyword) > 100:
        raise FeishuDeliveryError("keyword_invalid")
    # Previous opt-in flags are deliberately ignored. Group notifications
    # remain limited to safe failure metadata, never private corpus or SSE.
    failures = [{**{k: v for k, v in x.items()
                     if k not in ('content', 'response_excerpt', 'deviceId', 'device_id',
                                  'raw_evidence', 'diagnostic_context')},
                 'response_available': bool(x.get('response_excerpt'))}
                for x in (failure_cases or [])]
    payload = _card(summary, results, source, outcome, keyword,
                    failure_cases=failures if failure_cases is not None else None,
                    slow_token_cases=slow_token_cases)
    return send_card_payload(settings, payload, opener=opener)