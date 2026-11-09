"""Feishu Card 2.0 report: one card when possible, otherwise complete pages.

All pages are rendered and size-validated before any webhook request.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from lib.feishu_notifier import _safe, NoticeError, render_card_v2
from lib.reporting import display_seconds

def source_link_from_config(source):
    """Return a safe *shareable path*, never forward query auth credentials."""
    if not isinstance(source, dict):
        return ""
    kind = source.get("type")
    if kind not in ("tencent", "feishu"):
        return ""
    config = source.get(kind) or {}
    if not isinstance(config, dict):
        return ""
    raw = config.get("doc_url") or ""
    try:
        uri = urlsplit(raw)
        permitted = (uri.scheme == "https" and uri.port in (None, 443)
                     and not uri.username and not uri.password)
    except (TypeError, ValueError):
        return ""
    if not permitted:
        return ""
    if kind == "tencent":
        match = re.fullmatch(r"/sheet/([A-Za-z0-9]+)", uri.path)
        tab = str(config.get("sheet_id") or "")
        if uri.hostname != "docs.qq.com" or not match or not re.fullmatch(r"[A-Za-z0-9]+", tab):
            return ""
        return "https://docs.qq.com/sheet/{}?tab={}".format(match.group(1), tab)
    from lib.feishu_sheet import identity, FeishuSheetError
    try:
        token, sheet = identity(config)
    except (FeishuSheetError, ValueError):
        return ""
    if not sheet:
        return ""
    return "https://{}/sheets/{}?sheet={}".format(uri.hostname, token, sheet)


def report_label(source, run_time=None):
    # UTC+08:00 year-round; avoids unavailable timezone database on Windows.
    local = run_time or datetime.now(timezone(timedelta(hours=8)))
    time_label = local.strftime("%Y-%m-%d %H:%M")
    return ({"tencent":"腾讯文档｜","feishu":"飞书电子表格｜"}.get(source,"数据源 {}｜".format(_safe(source,20)))) + time_label


# Signed Feishu interactive webhook payload is limited to 20,000 bytes.
# Reserve headroom for dynamically generated timestamp/HMAC envelope.
MAX_SINGLE_CARD_BYTES = 18000


def _compact_failure_line(item, density=0):
    """Every failed case gets an ID/status/scene/reason; no case omission."""
    if not isinstance(item,dict):
        raise NoticeError("single_card_failure_item_invalid")
    if density == 0:
        scene_cap, cause_cap = 24, 72
    elif density == 1:
        scene_cap, cause_cap = 18, 42
    else:
        scene_cap, cause_cap = 12, 26
    original_id=str(item.get("case_id") or "")
    # An identifier must never be shortened or "normalized" to a different
    # value: the full set of case IDs is the single-card completeness contract.
    if (not original_id or len(original_id)>64 or
            _safe(original_id,64)!=original_id):
        raise NoticeError("single_card_case_id_not_safe")
    cid=original_id
    status = _safe(item.get("status","FAIL"),8)
    scene = _safe(item.get("scene") or "-",scene_cap)
    reason = _safe(item.get("reason") or item.get("failure_brief") or "未通过",cause_cap)
    missing=item.get("missing_keywords") or []
    forbidden=item.get("forbidden_found") or []
    if not isinstance(missing,list) or not isinstance(forbidden,list):
        raise NoticeError("failed_assertions_format_invalid")
    diagnostics=("缺失:"+";".join(str(x) for x in missing) if missing else
                 "禁止:"+";".join(str(x) for x in forbidden) if forbidden else
                 str(item.get("failure_brief") or reason))
    if item.get("status")=="ERROR" and item.get("error_phase"):
        diagnostics = "阶段:"+str(item["error_phase"]) + " " + diagnostics
    detail=_safe(diagnostics,cause_cap)
    first=display_seconds(item.get("first_token_ms"))
    dur=display_seconds(item.get("duration_ms"))
    if density == 0:
        return "**#{} {} · {}**｜{}｜首帧 {} 总 {}" .format(
            cid,status,scene,detail,first,dur)
    if density == 1:
        return "**#{} {} · {}**｜{}｜{}".format(cid,status,scene,detail,dur)
    return "**#{} {} · {}**｜{}".format(cid,status,scene,detail)


def build_single_probe_card(notice, failure_cases):
    """Single compact Feishu card: all failed case identities included.

    Dynamic density controls message size, not number of cases. If even the
    densest complete list exceeds the server budget, fail before networking.
    """
    if not isinstance(failure_cases,list):
        raise NoticeError("failure_cases_list_required")
    for density in range(3):
        card=render_card_v2(dict(notice, issues=[]),with_chart=False)
        elements=card["card"]["body"]["elements"]
        if failure_cases:
            elements.append({"tag":"markdown",
                             "content":"**失败及异常用例（{} 条，全部列出）**".format(len(failure_cases)),
                             "margin":"0px"})
            # Keep Markdown element sizes bounded and still one card/message.
            for start in range(0,len(failure_cases),10):
                lines=[_compact_failure_line(x,density)
                       for x in failure_cases[start:start+10]]
                elements.append({"tag":"markdown","content":"\n".join(lines),
                                 "margin":"0px"})
        else:
            summary = "✅ 本轮全部用例通过"
            if notice.get("status") == "error" or not any(
                m.get("label") == "总数" and str(m.get("value")) not in ("0","")
                for m in notice.get("metrics",[]) if isinstance(m,dict)
            ):
                summary = "⚠️ 本轮无可展示的失败用例；请核对执行状态及本地报告"
            elements.append({"tag":"markdown","content":summary,
                             "margin":"0px"})
        actual=len(json.dumps(card,ensure_ascii=False,
                              separators=(",",":")).encode("utf8"))
        if actual<=MAX_SINGLE_CARD_BYTES:
            return card
    # The single-card interface remains strict; build_probe_cards paginates overflow.
    raise NoticeError("single_card_failure_list_too_large")


def _render_paged_card(notice, page_cases, all_count, page_index, page_total):
    """One page with the original KPI header and an explicit global position."""
    card = render_card_v2(dict(notice, issues=[]), with_chart=False)
    card["card"]["header"]["title"]["content"] += " · {}/{}".format(page_index, page_total)
    elements = card["card"]["body"]["elements"]
    elements.append({"tag":"markdown",
                     "content":"**失败及异常用例（第 {}/{} 张，本页 {} 条，全部 {} 条）**".format(
                         page_index, page_total, len(page_cases), all_count),
                     "margin":"0px"})
    for start in range(0,len(page_cases),10):
        lines = [_compact_failure_line(item, density=0) for item in page_cases[start:start+10]]
        elements.append({"tag":"markdown","content":"\n".join(lines),"margin":"0px"})
    return card


def _card_bytes(card):
    return len(json.dumps(card,ensure_ascii=False,separators=(",",":")).encode("utf8"))


def build_probe_cards(notice, failure_cases):
    """Pre-render all cards before webhook delivery; preserve every failed row.

    A small run still produces exactly the original single card. For overflow,
    greedily pack readable pages with conservative worst-case page labels, then
    rerender with the actual total and validate *all* pages before sending.
    """
    if not isinstance(failure_cases,list):
        raise NoticeError("failure_cases_list_required")
    try:
        return [build_single_probe_card(notice,failure_cases)]
    except NoticeError as exc:
        if str(exc) != "single_card_failure_list_too_large":
            raise
    if not failure_cases:
        raise NoticeError("failure_list_cannot_fit")
    ranges=[]
    start=0
    count=len(failure_cases)
    while start<count:
        lo,hi=1,count-start
        best=0
        while lo<=hi:
            size=(lo+hi)//2
            trial=_render_paged_card(notice,failure_cases[start:start+size],
                                     count,count,count)
            if _card_bytes(trial)<=MAX_SINGLE_CARD_BYTES:
                best=size
                lo=size+1
            else:
                hi=size-1
        if best==0:
            raise NoticeError("failure_item_exceeds_card_budget")
        ranges.append((start,start+best))
        start+=best
    total_pages=len(ranges)
    cards=[]
    for idx,(begin,end) in enumerate(ranges,1):
        card=_render_paged_card(notice,failure_cases[begin:end],count,idx,total_pages)
        if _card_bytes(card)>MAX_SINGLE_CARD_BYTES:
            raise NoticeError("page_over_card_budget")
        cards.append(card)
    if sum(end-begin for begin,end in ranges)!=count:
        raise NoticeError("failure_list_count_mismatch")
    return cards