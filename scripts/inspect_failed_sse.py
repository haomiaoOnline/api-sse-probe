#!/usr/bin/env python3
"""Inspect safe metadata or explicitly export exact private failed SSE bytes.

No business requests or Feishu calls; never print raw SSE to stdout.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path


def read_snapshot(path):
    source=Path(path)
    if not source.is_file() or source.is_symlink():
        raise ValueError("failed_sse_snapshot_not_regular_file")
    data=json.loads(source.read_text(encoding="utf8"))
    if data.get("version") not in (1, 2) or not isinstance(data.get("failed_cases"),list):
        raise ValueError("failed_sse_snapshot_schema_invalid")
    return data


def case_bytes(data, case_id):
    matches=[x for x in data["failed_cases"] if str(x.get("case_id"))==str(case_id)]
    if len(matches)!=1:
        raise ValueError("failed_sse_case_missing_or_ambiguous")
    item=matches[0]
    try:
        body=base64.b64decode(item["raw_sse_base64"],validate=True)
    except (KeyError,TypeError,ValueError,binascii.Error) as exc:
        raise ValueError("failed_sse_base64_corrupt") from exc
    if len(body)!=item["received_bytes"] or hashlib.sha256(body).hexdigest()!=item["sha256"]:
        raise ValueError("failed_sse_integrity_mismatch")
    return body,item


def main(argv=None):
    parser=argparse.ArgumentParser(description="受限失败SSE查看工具，默认只输出元数据")
    parser.add_argument("--report",required=True,help="probe.py printed failed_sse_report path")
    parser.add_argument("--case-id",help="Optional failed case ID")
    parser.add_argument("--output",help="Explicit, new, private .sse file; refuses overwrite")
    args=parser.parse_args(argv)
    if args.output and not args.case_id:
        parser.error("--output requires --case-id")
    data=read_snapshot(args.report)
    if not args.case_id:
        print(json.dumps({"target_key":data.get("target_key"),
            "failed_total":data.get("failed_total"),
            "cases":[{k:x.get(k) for k in
                ("case_id","status","reason","capture_state","received_bytes")}
                for x in data["failed_cases"]]},ensure_ascii=False))
        return 0
    body,case=case_bytes(data,args.case_id)
    if not args.output:
        print(json.dumps({k:case.get(k) for k in
            ("case_id","status","reason","capture_state","received_bytes","sha256")},
            ensure_ascii=False))
        return 0
    target=Path(args.output).expanduser()
    if target.is_symlink() or not target.parent.is_dir():
        raise ValueError("failed_sse_export_parent_or_symlink_invalid")
    flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os,"O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd=os.open(str(target),flags,0o600)
    try:
        if os.name!="nt":
            os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    print(json.dumps({"case_id":args.case_id,"exported":str(target),
                      "bytes":len(body),"capture_state":case["capture_state"]},
                      ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())