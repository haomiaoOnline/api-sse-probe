"""API SSE probe -> generic notification contract. Feishu-agnostic mapping."""
from lib.reporting import display_seconds, failure_brief

def probe_notice(summary, failure_cases, slow_cases, title="API拨测报告",
                 source_url="", report_label=""):
    total=int(summary.get("total",0))
    passed=int(summary.get("pass",0))
    failed=int(summary.get("fail",0))
    errors=int(summary.get("error",0))
    issues=[]
    for f in failure_cases or []:
        missing=f.get("missing_keywords") or []
        forbidden=f.get("forbidden_found") or []
        reason=f.get("failure_brief") or failure_brief(f)
        extras=[]
        if missing: extras.append("缺失："+ "；".join(map(str,missing)))
        if forbidden: extras.append("禁止词："+ "；".join(map(str,forbidden)))
        extras.append("首帧 "+display_seconds(f.get("first_token_ms")))
        issues.append({"id":str(f.get("case_id","-")),
                       "name":str(f.get("scene") or "未知场景"),
                       "detail":" / ".join([reason]+extras)})
    return {
      "title":title,
      "source":"api-sse-probe",
      "chart_type":"none",
      "source_url":source_url,
      "chart_center":"",
      "status":"success" if total and failed==0 and errors==0 else "error",
      "metrics":[
        {"label":"总数","value":str(total)},
        {"label":"通过","value":str(passed)},
        {"label":"失败","value":str(failed)},
        {"label":"异常","value":str(errors)},
        {"label":"通过率","value":"{:.2f}%".format(float(summary.get("pass_rate",0)))},
      ],
      "distribution":[
        {"label":"PASS","value":passed},
        {"label":"FAIL","value":failed},
        {"label":"ERROR","value":errors}],
      "issues":issues,
      "footer": " · ".join(part for part in (
        report_label,
        "平均首帧 "+display_seconds(summary.get("average_latency_ms")),
        "总耗时 "+display_seconds(summary.get("wall_duration_ms")),
        "慢首帧 {} 条".format(len(slow_cases or [])),
      ) if part),
    }