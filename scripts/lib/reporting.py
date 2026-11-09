"""Monitoring statistics and diagnostic cases (no API/Webhook secrets)."""
import math

DEFAULT_SLOW_TOKEN_THRESHOLD_MS = 3000

REASONS = {
    "ok": "校验通过",
    "assertions_failed": "业务判断未通过",
    "missing_assertions": "未配置有效判断依据",
    "unexpected_http_status": "HTTP 响应码异常",
    "unexpected_content_type": "接口未返回 SSE 流",
    "sse_error_event": "服务端返回 SSE 错误事件",
    "invalid_sse_json": "响应数据帧不是合法 JSON",
    "invalid_sse_json_shape": "响应帧不是 JSON 对象",
    "upstream_business_error": "接口返回业务错误",
    "no_json_frames": "未收到有效业务数据帧",
    "done_missing": "未收到 SSE 结束标记",
    "http_error": "HTTP 请求异常",
    "timeout": "请求超时",
    "tls_error": "TLS 证书或握手异常",
    "network_error": "网络连接失败",
    "configuration_error": "请求或鉴权配置异常",
    "sse_decode_or_size_error": "SSE 解码或响应大小异常",
    "unexpected_error": "未预期错误",
}

def slow_threshold(config):
    if not isinstance(config, dict):
        raise ValueError("reports must be a JSON object")
    value = config.get("slow_token_threshold_ms", DEFAULT_SLOW_TOKEN_THRESHOLD_MS)
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value <= 0 or value > 3600000):
        raise ValueError("reports.slow_token_threshold_ms must be a positive number <= 3600000")
    return int(math.ceil(value))

def _ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return int(value)

def seconds(value):
    """Present millisecond metrics in seconds, to three decimal places."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if value < 0:
        return None
    return round(value / 1000, 3)

def display_seconds(value):
    converted = seconds(value)
    return "-" if converted is None else f"{converted:.3f}s"

def public_summary(summary):
    """Console output uses seconds; legacy ms remain in saved JSON."""
    converted = {k: v for k, v in summary.items() if not k.endswith("_ms")}
    for k in ("average_latency_ms", "total_duration_ms", "fastest_ms",
              "slowest_ms", "wall_duration_ms", "slow_token_threshold_ms"):
        converted[k[:-3]+"_s"] = seconds(summary.get(k))
    converted["display_unit"] = "s"
    return converted

def _case_text(value):
    return str(value) if value is not None else ""

def failure_brief(result):
    reason = result.get("reason")
    if reason=="timeout":
        where=result.get("error_phase")
        labels = {
            "dns": "DNS 解析阶段超时",
            "tcp_connect": "TCP 连接阶段超时",
            "tls_handshake": "TLS 握手阶段超时",
            "request_send": "发送 POST 阶段超时",
            "response_headers": "等待 HTTP 响应头超时（不代表服务端未处理）",
            "sse_read": "读取 SSE 流阶段超时",
        }
        return labels.get(where,"请求超时")
    if reason=="network_error" and result.get("error_phase"):
        return "网络错误，阶段：" + str(result["error_phase"])
    brief = REASONS.get(reason, "用例执行失败")
    if reason == "assertions_failed":
        missing = len(result.get("missing_keywords") or [])
        forbidden = len(result.get("forbidden_found") or [])
        if missing:
            brief += "（缺失 {} 项关键词）".format(missing)
        if forbidden:
            brief += "（命中 {} 项禁止词）".format(forbidden)
    if reason in ("unexpected_http_status", "http_error"):
        code = result.get("http_status")
        if isinstance(code, int) and not isinstance(code, bool):
            brief += "（HTTP {}）".format(code)
    return brief

def build_analysis(results, cases=None, threshold_ms=DEFAULT_SLOW_TOKEN_THRESHOLD_MS, wall_duration_ms=None):
    """Outputs overall statistics and diagnostic lists, one row per input case.

    Primary latency metrics (average_latency_ms, fastest_ms, slowest_ms)
    use first_token_ms: time to first valid business JSON frame (TTFT proxy).
    Missing first frames do not count toward primary latency samples.
    total_duration_ms is still the sum of full per-case durations,
    not the first-frame sum or overall wall time.
    """
    cases_by_id = {}
    for case in cases or []:
        cases_by_id[str(case["case_id"])] = case
    total = len(results)
    passed = sum(r.get("status") == "PASS" for r in results)
    failed = sum(r.get("status") == "FAIL" for r in results)
    errors = sum(r.get("status") == "ERROR" for r in results)
    durations = [_ms(r.get("duration_ms")) for r in results]
    durations = [value for value in durations if value is not None]
    first_frames = [_ms(r.get("first_token_ms")) for r in results]
    first_frames = [value for value in first_frames if value is not None]
    summaries = {
        "display_unit": "s",
        "total": total,
        "pass": passed,
        "fail": failed,
        "error": errors,
        "failed_total": total-passed,
        "pass_rate": round(100.0*passed/total, 2) if total else 0.0,
        "latency_metric": "first_token_ms",
        "first_token_sample_count": len(first_frames),
        "average_latency_ms": round(sum(first_frames)/len(first_frames), 2) if first_frames else None,
        "total_duration_ms": sum(durations),
        "fastest_ms": min(first_frames) if first_frames else None,
        "slowest_ms": max(first_frames) if first_frames else None,
        "wall_duration_ms": _ms(wall_duration_ms),
        "slow_token_threshold_ms": threshold_ms,
        "slow_token_count": 0,
    }
    summaries.update({
        "average_latency_s": seconds(summaries["average_latency_ms"]),
        "total_duration_s": seconds(summaries["total_duration_ms"]),
        "fastest_s": seconds(summaries["fastest_ms"]),
        "slowest_s": seconds(summaries["slowest_ms"]),
        "wall_duration_s": seconds(summaries["wall_duration_ms"]),
        "slow_token_threshold_s": seconds(threshold_ms),
    })
    enriched, failures, slow = [], [], []
    for r in results:
        case = cases_by_id.get(str(r.get("case_id")), {})
        scene = _case_text(case.get("scene") or r.get("scene") or "-")
        entry = dict(r, scene=scene, latency_ms=_ms(r.get("first_token_ms")),
                     latency_s=seconds(r.get("first_token_ms")),
                     first_token_s=seconds(r.get("first_token_ms")),
                     duration_s=seconds(r.get("duration_ms")))
        # latency_ms is the displayed first-business-frame metric.
        # duration_ms remains the full-request diagnostic duration.
        # Do not expose request content via general per-case rows.
        entry.pop("content", None)
        entry.pop("deviceId", None)
        entry.pop("response_excerpt", None)
        entry.pop("response_truncated", None)
        entry.pop("diagnostic_context", None)
        entry.pop("failure_assertions", None)
        entry.pop("raw_evidence", None)
        entry.pop("raw_sse_text", None)
        entry.pop("raw_sse_encoding", None)
        enriched.append(entry)
        if r.get("status") != "PASS":
            failures.append({
                "case_id": _case_text(r.get("case_id")),
                "scene": scene,
                "content": _case_text(case.get("content", "")),
                "status": _case_text(r.get("status")),
                "reason": _case_text(r.get("reason", "unexpected_error")),
                "failure_brief": failure_brief(r),
                "missing_keywords": list(r.get("missing_keywords") or []),
                "forbidden_found": list(r.get("forbidden_found") or []),
                "matched_keywords": list(r.get("matched_keywords") or []),
                "failure_assertions": list(r.get("failure_assertions") or []),
                "device_id": _case_text(case.get("deviceId", "")),
                "diagnostic_context": dict(r.get("diagnostic_context") or {}),
                "raw_evidence": dict(r.get("raw_evidence") or {}),
                "raw_sse_text": _case_text(r.get("raw_sse_text", "")),
                "raw_sse_encoding": _case_text(r.get("raw_sse_encoding", "UTF-8")),
                "response_excerpt": _case_text(r.get("response_excerpt", "")),
                "response_truncated": bool(r.get("response_truncated", False)),
                "error_phase": r.get("error_phase"),
                "exception_type": r.get("exception_type"),
                "exception_chain": list(r.get("exception_chain") or [])[:5],
                "exception_errno": r.get("exception_errno"),
                "timeout_kind": r.get("timeout_kind"),
                "http_headers_received": bool(r.get("http_headers_received",r.get("http_status") is not None)),
                "response_bytes_received": int(r.get("response_bytes_received") or 0),
                "stage_timings_ms": dict(r.get("stage_timings_ms") or {}),
                "first_token_ms": _ms(r.get("first_token_ms")),
                "latency_ms": _ms(r.get("first_token_ms")),
                "duration_ms": _ms(r.get("duration_ms")),
                "first_token_s": seconds(r.get("first_token_ms")),
                "latency_s": seconds(r.get("first_token_ms")),
                "duration_s": seconds(r.get("duration_ms")),
            })
        first_token = _ms(r.get("first_token_ms"))
        if first_token is not None and first_token >= threshold_ms:
            slow.append({
                "case_id": _case_text(r.get("case_id")),
                "scene": scene,
                "first_token_ms": first_token,
                "duration_ms": _ms(r.get("duration_ms")),
                "status": _case_text(r.get("status")),
                "first_token_s": seconds(r.get("first_token_ms")),
                "duration_s": seconds(r.get("duration_ms")),
            })
    slow.sort(key=lambda x: (-x["first_token_ms"], x["case_id"]))
    summaries["slow_token_count"] = len(slow)
    return summaries, enriched, failures, slow