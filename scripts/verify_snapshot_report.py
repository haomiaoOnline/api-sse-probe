#!/usr/bin/env python3
"""Read-only verification of private frozen case IDs against a completed probe report."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.case_snapshot import read_snapshot


def verify(snapshot, report):
    frozen = read_snapshot(snapshot)
    document = json.loads(Path(report).read_text(encoding="utf8"))
    expected = [str(x["case_id"]) for x in frozen["payload"]["cases"]]
    rows = document.get("results")
    if not isinstance(rows, list):
        raise ValueError("report_results_invalid")
    observed = [str(x["case_id"]) for x in rows]
    if expected != observed:
        raise ValueError("snapshot_report_case_sequence_mismatch")
    statuses = {"PASS": 0, "FAIL": 0, "ERROR": 0}
    for result in rows:
        state = result.get("status")
        if state not in statuses:
            raise ValueError("report_unknown_status")
        statuses[state] += 1
    summary = document.get("summary")
    if not isinstance(summary, dict) or any(
        summary.get(key) != count for key, count in
        {"total": len(rows), "pass": statuses["PASS"],
         "fail": statuses["FAIL"], "error": statuses["ERROR"]}.items()
    ):
        raise ValueError("report_summary_mismatch")
    return {
        "verified": True, "case_count": len(expected),
        "snapshot_sha256": frozen["snapshot_sha256"],
        "snapshot_run_id": frozen["run_id"],
        "report_run_id": document.get("run_id"),
        "pass": statuses["PASS"], "fail": statuses["FAIL"],
        "error": statuses["ERROR"], "business_posts": 0,
        "feishu_messages": 0,
        "warning": "comparison_only_not_cryptographic_request_to_snapshot_link"
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify(args.snapshot, args.report), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())