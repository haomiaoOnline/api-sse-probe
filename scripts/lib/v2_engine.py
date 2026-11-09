#!/usr/bin/env python3
"""Durable v2 capture/judge/report engine; imported by unified public entry."""
# 【v2 执行编排】分离准备、请求采集、离线复判、报告/通知阶段。
# 原始响应先落盘，后续改变判断依据可基于同一份证据重新判定。
import argparse
import io
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probe
from lib.case_snapshot import read_snapshot, canonical
from lib.v2_repository import RunStore, _atomic_private
from lib.v2_rejudge import judge_case

@contextmanager
def exclusive_run_lock(directory):
    """Advisory per-run lock; safe on macOS/Linux; Windows needs separate validation."""
    if os.name == "nt":
        raise RuntimeError("v2_windows_lock_not_verified")
    import fcntl
    path = directory / "execution.lock"
    if path.is_symlink():
        raise ValueError("execution_lock_symlink")
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


# capture：冻结用例逐条处理，先记录请求意图、再发起 POST、最后持久保存原始 SSE。
# 本阶段不把关键字匹配视为最终业务 PASS/FAIL。
def capture(store, cfg, max_cases=0, max_seconds=0):
    snap = store.snapshot()
    store.validate_api(cfg["api"])
    api = cfg["api"]
    if not isinstance(api, dict) or not str(api.get("url", "")).startswith("https://"):
        raise ValueError("v2_api_https_required")
    if api.get("verify_tls", True) is False:
        raise ValueError("v2_tls_verification_required")
    # Offline header validation avoids marking an invalid config as dispatched.
    probe.resolve_headers(api)
    cases = snap["payload"]["cases"]
    lookup = {str(c["case_id"]): c for c in cases}
    plan = store.plan()
    # Recover only PENDING cases; DISPATCH_INTENT remains unresolved, never replayed.
    from lib.feishu_notification_router import validate_probe_notification
    from lib.outputs.registry import validate_report_outputs
    if "file" not in validate_report_outputs(cfg):
        raise ValueError("v2_private_file_output_required")
    validate_probe_notification(cfg.get("feishu", {}))
    if max_cases < 0 or max_seconds < 0:
        raise ValueError("batch_limit_nonnegative_required")
    started = time.monotonic()
    processed = 0
    with exclusive_run_lock(store.directory):
        # Explicitly opt-in NEW runs automatically retry one unresolved
        # dispatch exactly once. Older runs retain their no-retry policy.
        # Retrying an in-flight request may execute the same business action
        # twice: old UUID and new UUID are both kept for audit.
        schedule = (list(plan["unknown"]) if store.automatic_retry_allowed() else []) + list(plan["pending"])
        for case_id in schedule:
            if max_cases and processed >= max_cases:
                break
            if max_seconds and processed and time.monotonic() - started >= max_seconds:
                break
            case = lookup[case_id]
            if case_id in plan["unknown"]:
                try:
                    attempt = store.queue_unknown_retry(case_id)
                except ValueError as exc:
                    if str(exc) == 'unknown_auto_retry_limit_reached':
                        continue  # Keep second UNKNOWN for manual review.
                    raise
            else:
                attempt = store.intent(case_id)  # fsync transaction BEFORE POST
            raw = io.BytesIO()
            metadata = {}
            result = probe.run_case(case, cfg["api"], cfg.get("assertions"),
                                    raw_capture=raw, capture_metadata=metadata,
                                    defer_assertions=True,
                                    request_id=attempt)
            # Even an ERROR must carry the exact received raw bytes and headers.
            store.captured(case_id, attempt, result, raw.getvalue(), metadata)
            processed += 1
        elapsed = int((time.monotonic() - started) * 1000)
        if plan["pending"]:
            prior = store.db.execute(
                "SELECT value FROM run_state WHERE key='capture_wall_ms'").fetchone()
            total = elapsed + (int(prior[0]) if prior else 0)
            with store.db:
                store.db.execute(
                    "INSERT OR REPLACE INTO run_state(key,value) VALUES('capture_wall_ms',?)",
                    (str(total),))
        return {"plan": store.plan(), "processed_this_batch": processed,
                "elapsed_ms": elapsed,
                "unknown_requests_not_replayed": len(store.plan()["unknown"]),
                "automatic_retry_count": len(store.retry_history())}


# rejudge：待执行和未知状态清零后，读取冻结证据，逐条调用 v2_rejudge.judge_case。
# 离线规则变更会生成新 evaluation，不覆盖原始响应或历史裁决。
def rejudge(store, cfg, rule_overrides=None):
    snap = store.snapshot()
    plan = store.plan()
    if plan["unknown"] or plan["pending"]:
        raise ValueError("run_incomplete_no_report_or_rejudge")
    cases = {str(c["case_id"]): dict(c) for c in snap["payload"]["cases"]}
    if rule_overrides is not None:
        if not isinstance(rule_overrides, dict) or set(rule_overrides) - set(cases):
            raise ValueError("rule_override_case_id_invalid")
        from lib.expression_assertions import parse_expression
        from lib.natural_assertions import parse_natural_rule
        for cid, override in rule_overrides.items():
            if not isinstance(override, dict) or set(override) - {
                    "expected_keywords", "forbidden_keywords", "match_mode"}:
                raise ValueError("rule_override_fields_invalid")
            for key in ("expected_keywords", "forbidden_keywords"):
                if key in override and (not isinstance(override[key], list)
                                       or not all(isinstance(x, str) and x.strip()
                                                  for x in override[key])):
                    raise ValueError("rule_override_list_invalid")
                for item in override.get(key, []):
                    parse_expression(item)
                    parse_natural_rule(item)
            if override.get("match_mode", "all") not in ("all", "any"):
                raise ValueError("rule_override_match_mode_invalid")
            cases[cid].update(override)
    require_done_row = store.db.execute(
        "SELECT value FROM run_state WHERE key='require_done'").fetchone()
    expected_http_row = store.db.execute(
        "SELECT value FROM run_state WHERE key='expected_http_status'").fetchone()
    require_done = require_done_row[0] == "1" if require_done_row else True
    expected_http = int(expected_http_row[0]) if expected_http_row else 200
    evaluated = []
    for row in store.rows():
        envelope, raw = store.evidence(row)  # digest and attempt verified
        evaluated.append(judge_case(cases[row[1]], envelope, raw,
                                    assertions=cfg.get("assertions"),
                                    require_done=require_done,
                                    expected_http_status=expected_http))
    # Append-only evaluation; existing historical evaluations not overwritten.
    evaluation_id = str(uuid.uuid4())
    root = store.directory / "evaluations"
    root.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        os.chmod(root, 0o700)
    report_path = root / (evaluation_id + ".json")
    payload = {"run_id": snap["run_id"], "evaluation_id": evaluation_id,
               "case_snapshot_sha256": snap["snapshot_sha256"],
               "rules_override_sha256": (__import__("hashlib").sha256(canonical(rule_overrides)).hexdigest()
                                         if rule_overrides is not None else None),
               "engine_version": "v2-m3-1",
               "assertion_engine_version": "deterministic-1",
               "results": evaluated, "created_at": datetime.now(timezone.utc).isoformat(),
               "automatic_retry_history": store.retry_history()}
    _atomic_private(report_path, canonical(payload) + b"\n")
    digest = __import__("hashlib").sha256(canonical([
        {"case_id": c["case_id"], "expected_keywords": c["expected_keywords"],
         "forbidden_keywords": c.get("forbidden_keywords", []),
         "match_mode": c.get("match_mode", "all")}
        for c in cases.values()])).hexdigest()
    with store.db:
        store.db.execute("INSERT INTO evaluations VALUES (?,?,?,?)",
                         (evaluation_id, payload["created_at"], digest, str(report_path)))
        store.db.execute("INSERT INTO evaluation_digests VALUES (?,?)",
                         (evaluation_id, __import__("hashlib").sha256(
                             canonical(payload)).hexdigest()))
    return payload



# partial_report：未完成任务仅出阶段报告，UNKNOWN 既不是 PASS 也不是 FAIL。
def partial_report(store, cfg):
    """A transparent offline report for incomplete/unknown runs.

    Never calls external services. UNKNOWN is not counted as ERROR/FAIL.
    The full historical result evidence is kept in a private run directory.
    """
    from lib.v2_repository import _dir_secure
    snap = store.snapshot()
    states = store.plan()
    cases = {str(c["case_id"]): c for c in snap["payload"]["cases"]}
    saved = []
    for row in store.rows():
        if row[2] != "CAPTURED":
            continue
        envelope, raw = store.evidence(row)
        judged = judge_case(cases[row[1]], envelope, raw,
                            assertions=cfg.get("assertions"))
        saved.append(judged)
    counts = {name: sum(1 for result in saved if result["status"] == name)
              for name in ("PASS", "FAIL", "ERROR")}
    total = len(cases)
    if len(saved) + len(states["unknown"]) + len(states["pending"]) != total:
        raise ValueError("partial_accounting_mismatch")
    unique = str(uuid.uuid4())
    summary = {
        "expected_total": total, "captured": len(saved),
        "pass": counts["PASS"], "fail": counts["FAIL"],
        "error": counts["ERROR"], "unknown_count": len(states["unknown"]),
        "pending_count": len(states["pending"]),
        "confirmed_pass_rate_pct": round(100 * counts["PASS"] / len(saved), 2) if saved else None,
        "full_run_complete": not states["unknown"] and not states["pending"],
    }
    payload = {
        "report_type": "INCOMPLETE_RUN_NOT_OFFICIAL_FINAL",
        "run_id": snap["run_id"], "report_id": unique,
        "snapshot_sha256": snap["snapshot_sha256"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "unknown_case_ids": states["unknown"],
        "automatic_retry_history": store.retry_history(),
        "pending_case_ids": states["pending"],
        "results": saved,
    }
    folder = _dir_secure(store.directory / "reports")
    output = folder / ("partial-" + unique + ".json")
    md = folder / ("partial-" + unique + ".md")
    _atomic_private(output, canonical(payload) + b"\n")
    lines = [
        "# API 拨测：未完成批次（非正式最终报告）",
        "",
        "运行 ID：" + snap["run_id"],
        "原始用例数：" + str(total),
        "有证据用例：" + str(len(saved)),
        "业务 PASS：" + str(counts["PASS"]),
        "业务 FAIL：" + str(counts["FAIL"]),
        "执行 ERROR：" + str(counts["ERROR"]),
        "状态未知：" + ", ".join(states["unknown"]) if states["unknown"] else "状态未知：无",
        "待执行：" + ", ".join(states["pending"]) if states["pending"] else "待执行：无",
        "",
        "注意：UNKNOWN 没有可靠响应证据，不能计入业务 PASS、FAIL 或 ERROR；仅新建运行且显式启用策略者可自动重试一次，第二次未知必须人工对账。",
        "",
        "## 已确认失败及异常编号",
    ]
    for result in saved:
        if result["status"] in ("FAIL", "ERROR"):
            lines.append("- #" + str(result["case_id"]) + " " + result["status"]
                         + "：" + str(result.get("reason", "unknown"))
                         + "（具体私有证据见同目录 JSON 和 run evidence）")
    _atomic_private(md, ("\n".join(lines) + "\n").encode("utf8"))
    return {"summary": summary, "json_report": str(output),
            "markdown_report": str(md), "unknown_case_ids": states["unknown"],
            "failed_case_ids": [str(r["case_id"]) for r in saved
                                if r["status"] in ("FAIL", "ERROR")],
            "external_network_calls": 0}

def report(store, cfg, evaluation_id=None):
    from lib.report_document import build_report_document
    from lib.outputs.registry import dispatch_report_outputs
    snap = store.snapshot()
    if store.plan()["unknown"] or store.plan()["pending"]:
        raise ValueError("run_incomplete_no_report")
    row = (store.db.execute("SELECT output_file FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
           if evaluation_id else store.db.execute("SELECT output_file FROM evaluations ORDER BY created_at DESC LIMIT 1").fetchone())
    if not row:
        raise ValueError("evaluation_required")
    evaluation_path = Path(row[0])
    if evaluation_path.is_symlink() or evaluation_path.parent != store.directory / "evaluations":
        raise ValueError("evaluation_path_invalid")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf8"))
    check = store.db.execute("SELECT sha256 FROM evaluation_digests WHERE evaluation_id=?",
                             (evaluation["evaluation_id"],)).fetchone()
    if not check:
        raise ValueError("evaluation_digest_missing")
    if __import__("hashlib").sha256(canonical(evaluation)).hexdigest() != check[0]:
        raise ValueError("evaluation_digest_mismatch")
    if evaluation.get("run_id") != snap["run_id"] or evaluation.get("case_snapshot_sha256") != snap["snapshot_sha256"]:
        raise ValueError("evaluation_run_identity_mismatch")
    results = [dict(x) for x in evaluation["results"]]
    cases = snap["payload"]["cases"]
    evidence_by_id = {row[1]: row for row in store.rows()}
    for result in results:
        if result.get("status") in ("FAIL", "ERROR"):
            envelope, raw = store.evidence(evidence_by_id[str(result["case_id"])])
            if not result.get("failure_assertions"):
                from probe import _failure_assertion_details
                matching_case = next(x for x in cases if str(x["case_id"]) == str(result["case_id"]))
                result["failure_assertions"] = _failure_assertion_details(
                    result, matching_case, cfg["api"], envelope.get("capture_metadata"))
            result["raw_sse_text"] = raw.decode("utf-8", errors="backslashreplace")
            result["raw_sse_encoding"] = "UTF-8 (with invalid bytes escaped)"
            result["raw_evidence"] = {
                "path": str(store.directory / "evidence" / (envelope["attempt_id"] + ".json")),
                "received_bytes": len(raw),
                "sha256": envelope["raw_sha256"],
                "capture_state": (envelope.get("capture_metadata", {}).get("response") or {}).get("capture_state", "unknown"),
                "http_status": result.get("http_status"),
                "content_type": (envelope.get("capture_metadata", {}).get("response") or {}).get("content_type")
            }
    local_cfg = dict(cfg)
    local_cfg["reports"] = dict(cfg.get("reports", {}), dir=str(store.directory / "reports"))
    wall = store.db.execute("SELECT value FROM run_state WHERE key='capture_wall_ms'").fetchone()
    doc = build_report_document(snap["payload"]["source"].get("type"),
                                local_cfg, results, cases,
                                int(wall[0]) if wall else None)
    doc["run_id"] = snap["run_id"]
    doc["evaluation_id"] = evaluation["evaluation_id"]
    doc["snapshot_sha256"] = snap["snapshot_sha256"]
    doc["automatic_retry_history"] = store.retry_history()
    receipts = dispatch_report_outputs(doc, local_cfg, probe.ROOT)
    return doc, receipts



def archive_partial(store, cfg, partial_data=None):
    if cfg.get('feishu_archive',{}).get('enabled') is not True:
        return {'status':'DISABLED'}
    if not (store.plan()['unknown'] or store.plan()['pending']):
        raise ValueError('partial_archive_requires_unresolved_run')
    from lib.feishu_archive import publish,source_labels
    partial_data=partial_data or partial_report(store,cfg)
    summary=partial_data['summary']
    snap=store.snapshot()
    partial_results=json.loads(Path(partial_data['json_report']).read_text(encoding='utf8'))['results']
    doc={'run_id':snap['run_id'],'summary':{
        'total':summary['captured'],'pass':summary['pass'],
        'fail':summary['fail'],'error':summary['error'],
        'pass_rate':summary['confirmed_pass_rate_pct']},
        'results':partial_results,
        'unknown_case_ids':partial_data['unknown_case_ids'],
        'pending_case_ids':store.plan()['pending'],
        'automatic_retry_history':store.retry_history()}
    label=source_labels(snap['payload']['source'],cfg)
    archived=publish(store,cfg,doc,*label,partial=True)
    if archived.get('status')!='VERIFIED':
        raise ValueError('partial_archive_unverified')
    return archived


def notify_partial(store, cfg, supplement_existing=False):
    """Send an explicitly INCOMPLETE one-shot warning, never a final card."""
    from lib.feishu_transport import validate_feishu_settings, send_card_payload
    from lib.feishu import FeishuDeliveryError
    states = store.plan()
    if states["pending"]:
        raise ValueError("pending_cases_must_finish_before_partial_notice")
    if not states["unknown"]:
        raise ValueError("complete_run_use_regular_notice")
    settings = cfg.get("feishu", {})
    if not settings.get("enabled", True):
        return {"state": "DISABLED"}
    validate_feishu_settings(settings)
    report_data = partial_report(store, cfg)
    archive=archive_partial(store,cfg,report_data)
    summary = report_data["summary"]
    name = str(settings.get("keyword") or "API拨测")
    # Preserve group bot trigger despite the new document-sheet card title.
    # All dynamic fields below are computed counts and case IDs, not utterances.
    detail = (
        "**{} · 未完成批次（不能作为全量结果）**\n"
        "冻结用例：{}；已获得证据：{}\n"
        "✅ PASS {}　❌ FAIL {}　⚠️ ERROR {}\n"
        "❔ 状态未知 {}：#{}\n"
        "待执行 {}；已确认用例通过率 {}%（仅以有证据用例为分母）\n"
        "已确认失败/异常 ID：{}\n"
        "任务外层超时导致未知请求，不自动重发；请核查服务端请求日志。"
    ).format(name,summary["expected_total"], summary["captured"],
             summary["pass"], summary["fail"], summary["error"],
             summary["unknown_count"], ", #".join(report_data["unknown_case_ids"]),
             summary["pending_count"], summary["confirmed_pass_rate_pct"],
             ", ".join("#" + cid for cid in report_data["failed_case_ids"]) or "无")
    from lib.feishu_archive import source_labels,notification_title
    labels=source_labels(store.snapshot()['payload']['source'],cfg)
    title=notification_title(*labels)+'（未完成）'
    if archive.get('status')=='VERIFIED':
        detail += "\n**本次拨测记录：**[查看未完成云文档]({})".format(archive['url'])
    if supplement_existing:
        detail="**补充归档链接：原未完成提醒已发送**\n"+detail
    payload = {"msg_type":"interactive","card":{
        "config":{"wide_screen_mode":True},
        "header":{"title":{"tag":"plain_text","content":title},
                  "template":"orange"},
        "elements":[{"tag":"markdown","content":detail}]}}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 19000:
        raise ValueError("partial_notice_too_large")
    with exclusive_run_lock(store.directory):
        if supplement_existing:
            existing=store.db.execute('SELECT state FROM partial_notification WHERE id=1').fetchone()
            if existing!=('SENT',):
                raise ValueError('supplement_requires_sent_partial_notice')
            from lib.v2_repository import _atomic_private
            corrected = store.directory/'partial-link-supplement-keyword-ledger.json'
            first = store.directory/'partial-link-supplement-receipt.json'
            if first.exists():
                first_state=json.loads(first.read_text()).get('state')
                if first_state!='FAILED':
                    raise ValueError('supplement_already_sent_or_unknown')
                # Previously rejected by Feishu; one corrected delivery only.
                selected=corrected
            else:
                selected=store.directory/'partial-link-supplement-ledger.json'
            _atomic_private(selected,b'{"state":"DISPATCH_INTENT"}\n')
        else:
            with store.db:
                store.db.execute("INSERT INTO partial_notification(id,state,details) VALUES(1,'DISPATCH_INTENT','{}')")
        try:
            receipt = send_card_payload(settings, payload)
            state, info = "SENT", {"receipt": receipt}
        except FeishuDeliveryError as exc:
            state = "FAILED" if str(exc).startswith("feishu_rejected_") else "DELIVERY_UNKNOWN"
            info = {"error_type":type(exc).__name__}
        except Exception as exc:
            state = "DELIVERY_UNKNOWN"
            info = {"error_type":type(exc).__name__}
        if supplement_existing:
            from lib.v2_repository import _atomic_private
            receipt_path=(store.directory/'partial-link-supplement-keyword-receipt.json'
                          if (store.directory/'partial-link-supplement-keyword-ledger.json').exists()
                          else store.directory/'partial-link-supplement-receipt.json')
            _atomic_private(receipt_path,
                            canonical({'state':state,'details':info,
                                       'archive_url':archive.get('url')})+b'\n')
        else:
            with store.db:
                store.db.execute("UPDATE partial_notification SET state=?,details=? WHERE id=1",
                                 (state,json.dumps(info)))
        return {"state":state,"automatic_retry":False,
                "notice_scope":"INCOMPLETE_RUN","archive":archive}

def notify(store, cfg, doc, archive=None, card_title=""):
    """At-most-one attempt per run. Unknown outcomes cannot be retried."""
    from lib.feishu_notification_router import (
        send_probe_notice, prepare_probe_cards, PagedDeliveryError, validate_probe_notification)
    from lib.report_document import public_projection
    from lib.feishu import FeishuDeliveryError
    settings = cfg.get("feishu", {})
    if not settings.get("enabled", True):
        return {"status": "disabled"}
    if cfg.get('feishu_archive',{}).get('enabled') is True and (
            not isinstance(archive,dict) or archive.get('status')!='VERIFIED'):
        raise ValueError('archive_required_before_notification')
    validate_probe_notification(settings)
    safe = public_projection(doc)
    # No delivery intent until deterministic size/content preflight succeeds.
    prepare_probe_cards(settings, doc["summary"], doc["source_type"],
                        failure_cases=safe["failure_cases"],
                        slow_token_cases=safe["slow_token_cases"],
                        source_config=store.snapshot()["payload"]["source"],
                        archive_url=(archive or {}).get('url',''), card_title=card_title)
    with exclusive_run_lock(store.directory):
        with store.db:
            store.db.execute("INSERT INTO notification(id,state,details) VALUES (1,'DISPATCH_INTENT','{}')")
        try:
            receipt = send_probe_notice(settings, doc["summary"], safe["results"],
                                        doc["source_type"],
                                        "PASS" if doc["summary"]["pass"] == doc["summary"]["total"] else "FAIL",
                                        failure_cases=safe["failure_cases"],
                                        slow_token_cases=safe["slow_token_cases"],
                                        source_config=store.snapshot()["payload"]["source"],
                                        archive_url=(archive or {}).get('url',''), card_title=card_title)
            state = "SENT"
            detail = json.dumps({"receipt":receipt},ensure_ascii=False,default=str)
        except PagedDeliveryError as exc:
            receipt_details = exc.details()
            if receipt_details.get("failed_page_outcome") == "rejected":
                state = "PARTIAL" if receipt_details.get("cards_confirmed") else "FAILED"
            else:
                state = "DELIVERY_UNKNOWN"
            detail = json.dumps(receipt_details, ensure_ascii=False)
        except FeishuDeliveryError as exc:
            # Ambiguous webhook delivery is never automatically repeated.
            state = "FAILED" if str(exc).startswith("feishu_rejected_") else "DELIVERY_UNKNOWN"
            detail = json.dumps({"error_type":type(exc).__name__})
        except Exception as exc:
            state = "DELIVERY_UNKNOWN"
            detail = json.dumps({"error_type":type(exc).__name__})
        with store.db:
            store.db.execute("UPDATE notification SET state=?,details=? WHERE id=1", (state, detail))
        return {"state":state,"automatic_retry":False}


# 供统一工作流调用的内部阶段 CLI；对外启动仍从 scripts/probe.py 进入。
def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("prepare", "capture", "plan", "rejudge", "report", "partial", "partial-notify", "notify"))
    p.add_argument("--config", default="config.local.json")
    p.add_argument("--snapshot")
    p.add_argument("--run-dir")
    p.add_argument("--max-cases", type=int, default=0,
                   help="Process at most N pending cases per capture invocation")
    p.add_argument("--max-seconds", type=int, default=0,
                   help="Stop before the next case after elapsed seconds")
    p.add_argument("--evaluation-id")
    p.add_argument("--rules-file", help="Private JSON case_id->rule overrides for offline rejudge only")
    args = p.parse_args(argv)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = probe.ROOT / config_path
    cfg = json.loads(config_path.read_text(encoding="utf8"))
    if args.action == "prepare":
        if not args.snapshot:
            p.error("prepare requires --snapshot")
        reports = Path(cfg.get("reports", {}).get("dir", "reports"))
        if not reports.is_absolute():
            reports = probe.ROOT / reports
        store = RunStore.create(reports / "v2-runs", args.snapshot, cfg["api"],
                                retry_policy=cfg.get('unknown_retry',{'enabled':False}))
    else:
        if not args.run_dir:
            p.error("requires --run-dir")
        store = RunStore(args.run_dir)
    try:
        if args.action in ("prepare", "plan"):
            out = {"run_dir": str(store.directory), "plan": store.plan()}
        elif args.action == "capture":
            out = capture(store, cfg, args.max_cases, args.max_seconds)
        elif args.action == "partial":
            out = partial_report(store, cfg)
        elif args.action == "partial-notify":
            out = notify_partial(store, cfg)
        elif args.action == "rejudge":
            overrides = (json.loads(Path(args.rules_file).read_text(encoding="utf8"))
                         if args.rules_file else None)
            out = {"evaluation_id": rejudge(store, cfg, overrides)["evaluation_id"]}
        elif args.action == "report":
            doc, receipts = report(store, cfg, args.evaluation_id)
            out = {"summary": doc["summary"], "report_outputs": receipts,
                   "evaluation_id": doc["evaluation_id"]}
        else:
            doc, receipts = report(store, cfg, args.evaluation_id)
            out = {"notification": notify(store, cfg, doc)}
        print(json.dumps(out, ensure_ascii=False, default=str))
    finally:
        store.close()
    return 0