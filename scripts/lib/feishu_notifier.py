"""Provider-neutral notification model and Feishu Card JSON 2.0 renderer.

Does not make network requests. Callers must NOT pass secrets or raw SSE data.
"""
import json
import math
import re

class NoticeError(ValueError):
    pass

_SECRET = re.compile(
    r"""(?i)(\b(?:powerkey|signature|authorization|access[_-]?token|token|deviceid|secret)\b\s*["']?\s*[:=]\s*["']?)[^\s,;；，|}"']+"""
)

def _safe(value, maximum=240):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise NoticeError("notice_text_invalid")
    raw = _SECRET.sub(lambda m: m.group(1) + "[REDACTED]", str(value))
    clean = re.sub(r"[\r\n\t*<>{}\[\]@|]", " ", raw.replace(chr(96)," ")).strip()
    return clean[:maximum] or "-"

def normalize_notice(notice):
    """Closed and bounded JSON-only contract for any Skill."""
    if not isinstance(notice, dict):
        raise NoticeError("notice_object_required")
    status = notice.get("status", "info")
    if status not in ("success", "warning", "error", "info"):
        raise NoticeError("notice_status_invalid")
    chart_type = notice.get("chart_type", "bar")
    if chart_type not in ("bar", "donut", "none"):
        raise NoticeError("chart_type_invalid")
    chart_center = notice.get("chart_center", "")
    if not isinstance(chart_center, str):
        raise NoticeError("chart_center_invalid")
    source_url = notice.get("source_url", "")
    tencent_link = r"https://docs\.qq\.com/sheet/[a-zA-Z0-9]+\?tab=[a-zA-Z0-9]+"
    feishu_link = r"https://(?:[A-Za-z0-9-]+\.)*(?:feishu\.cn|larksuite\.com)/sheets/[A-Za-z0-9_-]+\?sheet=[A-Za-z0-9_-]+"
    if not isinstance(source_url, str) or (source_url and not (
            re.fullmatch(tencent_link,source_url) or re.fullmatch(feishu_link,source_url))):
        raise NoticeError("notice_source_url_invalid")
    result = {"title": _safe(notice.get("title", "通知"), 90),
              "source": _safe(notice.get("source", "Skill"), 45),
              "status": status, "chart_type": chart_type,
              "source_url": source_url,
              "chart_center": _safe(chart_center, 12) if chart_center else "",
              "metrics": [], "distribution": [], "issues": [],
              "footer": _safe(notice["footer"], 180) if notice.get("footer") else ""}
    metrics, groups, issues = (notice.get(k, []) for k in ("metrics", "distribution", "issues"))
    if not all(isinstance(v, list) for v in (metrics, groups, issues)):
        raise NoticeError("notice_list_required")
    if len(metrics)>10 or len(groups)>10 or len(issues)>1000:
        raise NoticeError("notice_too_many_items")
    for m in metrics:
        if not isinstance(m, dict): raise NoticeError("metric_invalid")
        result["metrics"].append({"label": _safe(m.get("label", ""), 24),
                                  "value": _safe(m.get("value", "-"), 45)})
    for g in groups:
        if not isinstance(g, dict): raise NoticeError("distribution_invalid")
        v=g.get("value")
        if isinstance(v, bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:
            raise NoticeError("distribution_number_invalid")
        result["distribution"].append({"label":_safe(g.get("label", ""),28),"value":v})
    for issue in issues:
        if not isinstance(issue,dict):raise NoticeError("issue_invalid")
        result["issues"].append({"id":_safe(issue.get("id","-"),25),
                                 "name":_safe(issue.get("name","-"),45),
                                 "detail":_safe(issue.get("detail","-"),200)})
    return result

# Group-chat density tokens, adapted from the local ui_design Skill.
# The mobile rendering target is one compact metric row + 112px chart
# + at most two visible problems, rather than an entire report in chat.
COMPACT_TOKENS = {
    "body_padding": "8px 10px 8px 10px",
    "chart_height": "112px",
    "chart_margin": "0px",
    "max_issues": 2,
    "issue_detail_chars": 92,
    "title_chars": 48,
    "kpi_padding": "4px 5px 4px 5px",
    "kpi_gap": "4px",
    "kpi_label_chars": 9,
    "kpi_value_chars": 13,
}

def _pack(n, elements):
    title = n["title"][:COMPACT_TOKENS["title_chars"]]
    return {"msg_type": "interactive", "card": {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "compact"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": {"success": "green", "warning": "orange",
                         "error": "red", "info": "blue"}[n["status"]],
        },
        "body": {"direction": "vertical",
                 "vertical_spacing": "4px",
                 "padding": COMPACT_TOKENS["body_padding"],
                 "elements": elements},
    }}

def _metrics_line(n):
    """Text-only fallback when the source has no metrics."""
    parts = []
    limit = 5 if n["chart_type"] == "none" else 3
    for index, m in enumerate(n["metrics"][:limit]):
        label, value = m["label"], m["value"]
        parts.append(("**{} {}**" if index == 0 else "{} {}").format(label, value))
    return "　·　".join(parts) if parts else "**来源** " + n["source"]


def _metric_tiles(n):
    """Actual Feishu Card 2.0 column_set cards, not Markdown fake tiles.

    Desktop and phone both use three weighted columns with no stacking;
    labels and numbers have controlled size, contrast, padding and truncation.
    """
    if not n["metrics"]:
        return None
    columns = []
    limit = 5 if n["chart_type"] == "none" else 3
    for idx, m in enumerate(n["metrics"][:limit]):
        background, value_color = (
            ("blue-50", "blue-700") if idx == 0 else
            ("green-50", "green-700") if idx == 1 else
            ("orange-50", "orange-700") if idx == 2 else
            ("red-50", "red-700") if idx == 3 else
            ("green-50", "green-700"))
        columns.append({
            "tag": "column",
            "width": "weighted",
            "weight": 1,
            "vertical_align": "center",
            "horizontal_align": "center",
            "vertical_spacing": "0px",
            "padding": COMPACT_TOKENS["kpi_padding"],
            "background_style": background,
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": m["label"][:COMPACT_TOKENS["kpi_label_chars"]],
                        "text_size": "notation",
                        "text_color": "grey-600",
                        "text_align": "center",
                    },
                    "margin": "0px",
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "plain_text",
                        "content": m["value"][:COMPACT_TOKENS["kpi_value_chars"]],
                        "text_size": "heading-4",
                        "text_color": value_color,
                        "text_align": "center",
                    },
                    "margin": "0px",
                },
            ],
        })
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": COMPACT_TOKENS["kpi_gap"],
        "margin": "0px",
        "columns": columns,
    }

def _distribution_summary(n):
    return "　·　".join("{} {}".format(d["label"], d["value"])
                        for d in n["distribution"])

def _chart_element(n):
    points = [{"category": d["label"], "count": d["value"]}
              for d in n["distribution"]]
    if n["chart_type"] == "donut":
        # VChart Pie+innerRadius is a doughnut chart. The exact numeric
        # breakdown remains above the chart for tiny minority sectors.
        spec = {
            "type": "pie",
            "data": [{"id": "status", "values": points}],
            "categoryField": "category",
            "valueField": "count",
            "seriesField": "category",
            "innerRadius": 0.58,
            "outerRadius": 0.84,
            "label": {"visible": False},
            "legends": {"visible": False},
            "animation": False,
            "color": ["#2BA875", "#EDAA45", "#E35C60"],
        }
        if n["chart_center"]:
            spec["indicator"] = {
                "visible": True,
                "title": {
                    "visible": True,
                    "text": n["chart_center"],
                    "autoFit": True,
                    "style": {
                        "fontSize": 15,
                        "fontWeight": 600,
                        "fill": "#26374B",
                    },
                },
            }
    else:
        spec = {
            "type": "bar",
            "data": [{"id": "status", "values": points}],
            "xField": "category",
            "yField": "count",
            "barWidth": 20,
            "padding": {"top": 5, "bottom": 4, "left": 4, "right": 4},
            "axes": [
                {"orient": "left", "visible": False},
                {"orient": "bottom", "tick": {"visible": False},
                 "domainLine": {"visible": False}},
            ],
            "animation": False,
        }
    return {
        "tag": "chart", "element_id": "status_chart",
        "height": COMPACT_TOKENS["chart_height"],
        "margin": COMPACT_TOKENS["chart_margin"],
        "preview": False,
        "color_theme": "brand",
        "chart_spec": spec,
    }

def render_card_v2(notice, max_payload_bytes=15000, with_chart=True):
    """Compact JSON 2.0 group-chat card: metrics, small chart, top issues.

    Content length and chart height are deterministic regardless of source
    issue count. Full diagnostic data is retained by the calling Skill.
    """
    n = normalize_notice(notice)
    if not isinstance(max_payload_bytes, int) or isinstance(max_payload_bytes, bool) or max_payload_bytes < 1500:
        raise NoticeError("payload_limit_invalid")
    budget = min(max_payload_bytes, 17500) - 1000
    show_chart = bool(with_chart and n["chart_type"] != "none" and n["distribution"] and
                      any(item["value"] > 0 for item in n["distribution"]))
    tiles = _metric_tiles(n)
    elements = [tiles] if tiles else [{"tag": "markdown", "content": _metrics_line(n)}]
    if n["distribution"] and n["chart_type"] != "none":
        elements.append({"tag": "markdown", "content": _distribution_summary(n),
                         "text_size": "notation", "margin": "0px"})
    if n["source_url"]:
        elements.append({"tag": "markdown",
                         "content": "监控数据源：[打开{} · 查看用例表](".format(
                             "飞书电子表格" if "/sheets/" in n["source_url"] else "腾讯文档")
                             + n["source_url"] + ")",
                         "margin": "0px"})
    if show_chart:
        elements.append(_chart_element(n))
    count = len(n["issues"]) if n["chart_type"] != "none" else 0
    if count:
        shown = min(count, COMPACT_TOKENS["max_issues"])
        issue_lines = ["**异常 {} 项**".format(count)]
        for issue in n["issues"][:shown]:
            detail = issue["detail"][:COMPACT_TOKENS["issue_detail_chars"]]
            issue_lines.append("❌ {} {}：{}".format(
                issue["id"][:12], issue["name"][:28], detail))
        if count > shown:
            issue_lines.append("其余 {} 项见报告（展示 {}/{}）".format(count-shown, shown, count))
        elements.append({"tag": "markdown", "content": "\n".join(issue_lines)})
    meta = []
    metric_limit = 5 if n["chart_type"] == "none" else 3
    if len(n["metrics"]) > metric_limit:
        meta.extend("{} {}".format(m["label"], m["value"]) for m in n["metrics"][metric_limit:])
    if n["footer"]:
        meta.append(n["footer"][:85])
    if meta:
        elements.append({"tag": "markdown", "content": " · ".join(meta)[:160]})

    card = _pack(n, elements)
    if len(json.dumps(card, ensure_ascii=False).encode("utf8")) > budget:
        if show_chart:
            return render_card_v2(n, max_payload_bytes, with_chart=False)
        # The content has an intentionally small constant upper bound.
        raise NoticeError("notice_too_large")
    return card

def render_markdown_v2(notice, max_payload_bytes=15000):
    """Explicit no-chart fallback with the same concise information."""
    return render_card_v2(notice, max_payload_bytes, with_chart=False)