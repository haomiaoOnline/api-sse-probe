"""Stable report data model and Markdown rendering; does not write files or send messages.

Private report schema v6 adds the complete received SSE text to each failure.
Remote sinks must only receive public_projection(document), never the private original.
"""
import re
from datetime import datetime, timezone
from uuid import uuid4

from lib.reporting import build_analysis, display_seconds, slow_threshold


def build_report_document(source, cfg, results, cases=None, wall_duration_ms=None):
    threshold_ms = slow_threshold(cfg.get("reports", {}))
    summary, enriched, failures, slow = build_analysis(
        results, cases, threshold_ms, wall_duration_ms)
    return {
        "version": 6,
        "run_id": str(uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_type": source,
        "summary": summary,
        "results": enriched,
        "failure_cases": failures,
        "slow_token_cases": slow,
    }


def public_projection(document):
    """Explicitly exclude user utterances, assistant excerpts and opaque IDs.

    A future cloud writer must also get user authorization and an explicit
    remote destination. This projection is deliberately conservative.
    """
    allowed = ("version", "run_id", "timestamp_utc", "source_type", "summary")
    result_fields = ("case_id", "scene", "status", "reason", "http_status",
                     "first_token_ms", "first_token_s", "duration_ms", "duration_s",
                     "done_received")
    failure_fields = ("case_id", "scene", "status", "reason", "failure_brief",
                      "error_phase", "timeout_kind", "http_headers_received",
                      "response_bytes_received",
                      "missing_keywords", "forbidden_found", "first_token_ms",
                      "first_token_s", "duration_ms", "duration_s")
    safe = {key: document.get(key) for key in allowed}
    safe["results"] = [
        {key: x.get(key) for key in result_fields if key in x}
        for x in document.get("results", [])]
    safe["failure_cases"] = [
        {key: x.get(key) for key in failure_fields if key in x}
        for x in document.get("failure_cases", [])]
    safe["slow_token_cases"] = [
        {key: x.get(key) for key in result_fields if key in x}
        for x in document.get("slow_token_cases", [])]
    return safe


def _markdown_cell(value):
    """Prevent line breaks or untrusted table text from breaking reports."""
    s = str(value if value is not None else "-")
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("|", "&#124;").replace("\r", " ").replace("\n", " ")
             .replace("\t", " "))


def _display_s(value):
    return display_seconds(value)


def _fenced_block(value, language="text"):
    """Render untrusted multiline text without allowing it to close its fence."""
    text = str(value if value is not None else "")
    if not text:
        return ["（无内容）"]
    longest = max((len(run) for run in re.findall(r"~+", text)), default=0)
    fence = "~" * max(3, longest + 1)
    return [fence + language, text, fence]


def render_markdown(document):
    """Render concise, private failure diagnostics with complete received SSE."""
    summary = document["summary"]
    failures = document["failure_cases"]
    lines = [
        "# SSE API 拨测失败诊断报告", "",
        "- **执行结果**：总计 {total} 条；通过 {pass} 条；失败 {fail} 条；异常 {error} 条".format(**summary),
        "", "## 失败诊断详情",
    ]
    if not failures:
        lines.append("本轮没有 FAIL/ERROR 用例。")
        return "\n".join(lines) + "\n"

    for item in failures:
        evidence = item.get("raw_evidence") or {}
        assertions = item.get("failure_assertions") or []
        scene = _markdown_cell(item.get("scene") or "-")
        case_id = _markdown_cell(item.get("case_id") or "-")
        lines += ["", "### 场景：{}（用例 ID：{}）".format(scene, case_id),
                  "", "**语料**"]
        lines.extend(_fenced_block(item.get("content") or "", "text"))
        lines += ["", "**失败判断原因**"]
        if assertions:
            for rule in assertions:
                condition = _markdown_cell(rule.get("condition") or rule.get("rule_id") or "未命名判断")
                expected = _markdown_cell(rule.get("expected") or "-")
                actual = _markdown_cell(rule.get("actual") or "未能提取实际值")
                failure_reason = _markdown_cell(rule.get("failure_reason") or "assertion_not_satisfied")
                field_path = rule.get("field_path")
                field_label = "；字段：{}".format(_markdown_cell(field_path)) if field_path else ""
                lines.append("- **{}**：{}；预期：{}；实际：{}{}".format(
                    condition, failure_reason, expected, actual, field_label))
        else:
            brief = item.get("failure_brief") or item.get("reason") or "未能自动定位失败原因"
            lines.append("- {}（原因代码：{}）".format(
                _markdown_cell(brief), _markdown_cell(item.get("reason") or "unknown")))

        lines += ["", "**完整 SSE 响应参数**"]
        http_status = evidence.get("http_status", item.get("http_status"))
        content_type = evidence.get("content_type") or "-"
        received = evidence.get("received_bytes", item.get("response_bytes_received", 0))
        capture_state = evidence.get("capture_state") or "unknown"
        digest = evidence.get("sha256") or "-"
        lines.append("- HTTP 状态：{}；Content-Type：{}；接收字节：{}；捕获状态：{}；SHA-256：{}".format(
            _markdown_cell(http_status if http_status is not None else "-"),
            _markdown_cell(content_type), int(received or 0),
            _markdown_cell(capture_state), _markdown_cell(digest)))
        if http_status is None and not received:
            lines.append("- 尚未收到 HTTP 响应头或响应体。")
        elif capture_state in ("http_error_body", "non_sse_http_body"):
            lines.append("- 以下内容是实际收到的 HTTP 非 SSE 响应体。")
        raw_text = item.get("raw_sse_text") or ""
        if raw_text:
            encoding = item.get("raw_sse_encoding") or "UTF-8"
            if encoding != "UTF-8":
                lines.append("- 文本显示编码：{}（精确原始字节以私有快照为准）".format(
                    _markdown_cell(encoding)))
            lines.extend(_fenced_block(raw_text, "text"))
        elif evidence:
            lines.append("未收到响应体字节。" if not received else "原始响应文本不可用；请使用私有 Base64 快照核对精确字节。")
        else:
            lines.append("未关联完整原始响应证据；不能将局部响应摘录视为完整 SSE。")

    lines += ["", "完整响应按原始接收顺序展示；如果捕获状态不是 protocol_done，不得将其视为完整结束的 SSE 流。",
              "本文件包含测试语料和完整业务响应，仅限授权人员在本地受限目录查看；不会发送到飞书或团队共享输出。"]
    return "\n".join(lines) + "\n"