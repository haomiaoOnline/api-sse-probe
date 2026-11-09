"""Pure offline re-evaluation from exact received bytes and frozen case; no network."""
# 【离线最终裁判】输入冻结用例、原始 SSE 字节、请求元数据；不再发送 HTTP。
# 先验证 HTTP 和 SSE 协议完整性，再判断具体业务字段与文本。
# 缺少 DONE、非法 JSON 或发生协议错误时不可凭片段文本给 PASS。
import json
import hashlib

from lib.assertion_rules import accumulate_signals, evaluate_assertions, expand_expected_keywords


# 输入 case+envelope+raw，输出 PASS/FAIL/ERROR、缺失条件和规则 trace。
# 原始请求执行失败仍是 ERROR，断言缺少证据不能伪造成成功。
def judge_case(case, envelope, raw, *, assertions=None, require_done=True,
               expected_http_status=200):
    # Original transport faults remain faults; never manufacture bytes not received.
    from probe import sse_events, SSEError
    orig = dict(envelope["result"])
    def not_evaluated(value):
        return dict(value, assertion_engine_version='deterministic-1',
                    assertion_evaluation_status='NOT_EVALUATED', assertion_trace=[])
    if orig.get("status") == "ERROR":
        return not_evaluated(orig)
    api_response = envelope.get("capture_metadata", {}).get("response") or {}
    observed = orig.get("capture_observation") or {}
    protocol_reason = observed.get("reason", orig.get("reason"))
    if (protocol_reason == "unexpected_http_status" or
            (orig.get("http_status") is not None and
             orig.get("http_status") != expected_http_status)):
        return not_evaluated(dict(orig, status="FAIL", reason="unexpected_http_status"))
    if (protocol_reason == "unexpected_content_type" or
            api_response.get("body_kind") == "non_sse_http_body"):
        return not_evaluated(dict(orig, status="FAIL", reason="unexpected_content_type"))
    if api_response.get("body_kind") == "http_error_body":
        return not_evaluated(dict(orig, status="FAIL", reason="unexpected_http_status"))
    if api_response.get("capture_state") in ("partial_or_size_limit", "not_received"):
        return not_evaluated(dict(orig, status="FAIL", reason=(
            "sse_decode_or_size_error" if protocol_reason == "sse_decode_or_size_error"
            else "insufficient_evidence")))
    signals = {"answer_text_fragments": [], "answer_titles": [], "background_contents": []}
    frames = 0
    done = False
    reason = None
    try:
        for event, data in sse_events(raw.splitlines(keepends=True)):
            if data.strip() == "[DONE]":
                done = True
                continue
            if done or reason:
                continue
            if event.lower() == "error":
                reason = "sse_error_event"
                continue
            try:
                frame = json.loads(data)
            except (ValueError, UnicodeError):
                reason = "invalid_sse_json"
                continue
            if not isinstance(frame, dict):
                reason = "invalid_sse_json_shape"
                continue
            if frame.get("error") or frame.get("status") == "error":
                reason = "upstream_business_error"
                continue
            frames += 1
            accumulate_signals(frame, signals)
    except (SSEError, UnicodeError):
        reason = "sse_decode_or_size_error"
# 发生 SSE 协议或上游业务错误时，直接中止规则判定，保留真实失败原因。
    if reason:
        return not_evaluated(dict(orig, status="FAIL", reason=reason))
    if frames == 0:
        return not_evaluated(dict(orig, status="FAIL", reason="no_json_frames"))
    if require_done and not done:
        return not_evaluated(dict(orig, status="FAIL", reason="done_missing"))
# 仅在完整响应证据已经满足要求后，才执行用户编写的断言规则。
    found, missing, wanted = evaluate_assertions(
        case["expected_keywords"], [], orig.get("http_status"), signals)
    corpus = signals["assertion_corpus"]
    forbidden = [k for k in expand_expected_keywords(case.get("forbidden_keywords", [])) if k in corpus]
    if not wanted and (assertions or {}).get("require_keywords", True):
        return not_evaluated(dict(orig, status="FAIL", reason="missing_assertions"))
# 公共列 + 特殊列规则默认全部满足（all）才 PASS。
# 不能把整行改成 any 来实现“暂停或停止”，否则 HTTP 200 可能掩盖业务缺失。
    match = len(found) == len(wanted) if case.get("match_mode", "all") == "all" else bool(found)
    invalid_rules = signals.get('invalid_assertions') or []
    updated = dict(orig, status="PASS" if match and not forbidden and not invalid_rules else "FAIL",
                   reason=("invalid_assertion" if invalid_rules else
                           "ok" if match and not forbidden else "assertions_failed"),
                   matched_keywords=found, missing_keywords=missing,
                   forbidden_found=forbidden, done_received=done,
                   json_frame_count=frames,
                   assertion_engine_version="deterministic-1",
                   assertion_evaluation_status=('INVALID_RULE' if invalid_rules else 'EVALUATED'),
                   assertion_trace=signals.get('rule_trace', []))
    if updated["status"] == "FAIL":
        from probe import _failure_assertion_details
        updated["failure_assertions"] = _failure_assertion_details(
            updated, case, {}, envelope.get("capture_metadata"), signals)
    return updated