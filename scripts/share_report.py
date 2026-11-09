"""Read an existing local probe report and republish all failures to Feishu.

No SSE calls. Safe by default: only --send performs webhook writes.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.feishu_notification_router import (send_probe_notice, prepare_probe_cards,
                                            PagedDeliveryError)
from lib.feishu_transport import FeishuDeliveryError


def locate_report(directory, requested):
    base = directory.resolve()
    if requested == "latest":
        files = sorted(base.glob("probe-*.json"),
                       key=lambda p:p.stat().st_mtime, reverse=True)
        if not files:
            raise ValueError("no_saved_reports")
        target = files[0]
    else:
        target = Path(requested)
        if not target.is_absolute():
            target = base / target
    target = target.resolve()
    if target.parent != base or target.suffix != ".json":
        raise ValueError("report_path_outside_private_dir")
    return target


def stored_time(path):
    found = re.match(r"^probe-(\d{8}T\d{6}Z)-", path.name)
    if not found:
        return None
    return datetime.strptime(found.group(1), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8)))


def main(argv=None):
    parser=argparse.ArgumentParser(description="从历史报告向群发送完整失败卡片（按容量分页）；默认只预检")
    parser.add_argument("--config",default="config.local.json")
    parser.add_argument("--report",default="latest")
    parser.add_argument("--send",action="store_true",
                        help="Explicitly send all prevalidated Feishu cards, without retries")
    args=parser.parse_args(argv)
    path=Path(args.config)
    if not path.is_absolute():
        path=ROOT/path
    cfg=json.loads(path.read_text(encoding="utf8"))
    reportdir=Path(cfg.get("reports",{}).get("dir","reports"))
    if not reportdir.is_absolute():
        reportdir=path.parent/reportdir
    reportpath=locate_report(reportdir,args.report)
    data=json.loads(reportpath.read_text(encoding="utf8"))
    summary=data["summary"]
    failures=data["failure_cases"]
    results=data.get("results") or []
    if not isinstance(failures,list) or not isinstance(summary,dict) or not isinstance(results,list):
        raise ValueError("report_structure_invalid")
    if int(data.get("version",0)) < 5:
        raise ValueError("report_version_too_old_for_complete_group_detail")
    total=int(summary["total"])
    expected=int(summary["fail"])+int(summary["error"])
    if not (total == int(summary["pass"])+expected and len(failures)==expected and
            len(results)==total):
        raise ValueError("report_case_counts_inconsistent")
    feishu=cfg.get("feishu") or {}
    if feishu.get("notification_style", "v2") not in ("v2", "auto"):
        raise ValueError("v2_group_report_not_enabled")
    # Render every page before the first webhook call, identical to actual send.
    pages=prepare_probe_cards(
        feishu, summary, str(data.get("source_type") or "tencent"),
        failure_cases=failures, slow_token_cases=data.get("slow_token_cases") or [],
        source_config=cfg.get("source") or {}, run_time=stored_time(reportpath),
        report_note="历史报告补发")
    if not args.send:
        print(json.dumps({"check":True,"report":reportpath.name,"total":total,
                          "failed_cases":expected,"group_cards":len(pages),
                          "requests_sent":0,"source":"stored_report"},ensure_ascii=False))
        return 0
    stamp=stored_time(reportpath)
    try:
        receipt=send_probe_notice(
            feishu, summary, results, str(data.get("source_type") or "tencent"),
            "FAIL" if expected else "PASS",
            failure_cases=failures,
            slow_token_cases=data.get("slow_token_cases") or [],
            source_config=cfg["source"],
            run_time=stamp,
            report_note="历史报告补发")
    except PagedDeliveryError as exc:
        print(json.dumps({"result":"partial",**exc.details(),
                          "requests_to_business_api":0},ensure_ascii=False),
              file=sys.stderr)
        return 3
    except FeishuDeliveryError as exc:
        print(json.dumps({"result":"failed","reason":str(exc),
                          "requests_to_business_api":0},ensure_ascii=False),
              file=sys.stderr)
        return 3
    print(json.dumps({"result":receipt,"source":"stored_report",
                      "report":reportpath.name,"failed_cases":expected,
                      "cards_requested":len(pages),"requests_to_business_api":0},
                     ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())