"""Safe default v2 workflow orchestrator for scripts/probe.py.

One invocation processes all frozen pending cases by default. Optional caller
limits may yield CONTINUE_REQUIRED; unknown dispatches never replay.
"""
import argparse
import hashlib
import json
import os
import shlex
import sys
from contextlib import contextmanager
from pathlib import Path

from lib.case_snapshot import canonical, freeze_cases, _dir_secure
from lib.outputs.registry import validate_report_outputs
from lib.source_override import apply_tencent_source_url
from lib.feishu_sheet import identity as feishu_identity
from lib.v2_repository import RunStore


def _source(cfg, args):
    source = dict(cfg.get("source") or {})
    if args.source_url and (args.cases or (args.source and args.source not in ("tencent", "feishu"))):
        raise ValueError("conflicting_source_overrides")
    if args.source_url:
        from urllib.parse import urlsplit
        host = urlsplit(args.source_url).hostname or ''
        if host == 'feishu.cn' or host.endswith('.feishu.cn') or host == 'larksuite.com' or host.endswith('.larksuite.com'):
            if args.source and args.source != 'feishu':
                raise ValueError('source_url_source_type_mismatch')
            # Preserve existing header/range mappings from Tencent when a new
            # Feishu link overrides the user's existing source for this run.
            old = source.get('feishu') or source.get('tencent') or {}
            config = {k:v for k,v in old.items() if k in ('range','header_row','columns')}
            config['doc_url'] = args.source_url
            spreadsheet_token, sheet_id = feishu_identity(config)
            config.update(spreadsheet_token=spreadsheet_token, sheet_id=sheet_id)
            source = {'type':'feishu', 'feishu':config}
            # Credentials are loaded ONLY from the private main config.
        else:
            if args.source and args.source != 'tencent':
                raise ValueError('source_url_source_type_mismatch')
            source = apply_tencent_source_url(source, args.source_url)
    if args.source:
        source["type"] = args.source
    if args.cases:
        source["path"] = args.cases
    if args.case_id:
        source["_selected_case_id"] = args.case_id
    if source.get('type') == 'feishu':
        config = source.get('feishu') or {}
        allowed = {'doc_url','spreadsheet_token','sheet_id','range','header_row','columns'}
        if not isinstance(config,dict) or set(config)-allowed:
            raise ValueError('feishu_source_unknown_fields_credentials_must_be_private')
        token, sheet = feishu_identity(config)
        sanitized = dict(config,spreadsheet_token=token)
        if sanitized.get('doc_url'):
            from urllib.parse import urlsplit
            parsed = urlsplit(sanitized['doc_url'])
            sanitized['doc_url'] = 'https://{}/sheets/{}{}'.format(
                parsed.hostname,token,'?sheet='+sheet if sheet else '')
        if sheet:
            sanitized['sheet_id'] = sheet
        # Never freeze unrelated legacy source profiles or app credentials.
        source = {'type':'feishu','feishu':sanitized,
                  **({'_selected_case_id':source['_selected_case_id']} if args.case_id else {})}
    return source


def _report_root(cfg, probe):
    root = Path((cfg.get("reports") or {}).get("dir", "reports"))
    return root if root.is_absolute() else probe.ROOT / root



@contextmanager
def _launch_lock(parent):
    """Serialize discover-or-create so concurrent agents cannot start duplicate runs."""
    if os.name == "nt":
        raise ValueError("v2_windows_not_verified")
    import fcntl
    parent = _dir_secure(parent)
    lockfile = parent / ".unified-launch.lock"
    if lockfile.is_symlink():
        raise ValueError("run_launch_lock_symlink")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(lockfile), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _resumable(parent, source, api):
    """Resume exactly one matching incomplete frozen run, fail closed on ambiguity."""
    if not parent.exists():
        return None
    matches = []
    for item in parent.iterdir():
        if not item.is_dir() or item.is_symlink() or not (item / "state.sqlite3").is_file():
            continue
        store = RunStore(item)
        try:
            snap = store.snapshot()
            if snap["payload"]["source"] != source:
                continue
            store.validate_api(api)
            plan = store.plan()
            # A crash after the final evidence commit but before the first
            # offline evaluation must resume reporting, not send requests anew.
            unevaluated = (not plan["pending"] and not plan["unknown"] and
                           store.db.execute("SELECT 1 FROM evaluations LIMIT 1").fetchone() is None)
            if plan["pending"] or plan["unknown"] or unevaluated:
                matches.append(item)
        finally:
            store.close()
    if len(matches) > 1:
        raise ValueError("multiple_unresolved_runs_require_manual_selection")
    return matches[0] if matches else None


def _emit(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def main(argv=None):
    import probe
    from lib import v2_engine
    from lib.feishu_notification_router import validate_probe_notification
    parser = argparse.ArgumentParser(description="API SSE Probe (durable v2 default)")
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source", choices=("csv", "json", "tencent", "feishu"))
    parser.add_argument("--cases")
    parser.add_argument("--case-id")
    parser.add_argument("--source-url")
    parser.add_argument("--feishu-credentials-file", help="Private Feishu app config path, never saved in run snapshots")
    parser.add_argument("--no-feishu", action="store_true")
    parser.add_argument("--run-dir", help="Explicitly resume one frozen durable run")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="Optional maximum cases (0=unlimited, default)")
    parser.add_argument("--max-seconds", type=int, default=0,
                        help="Optional soft time limit between cases (0=unlimited, default)")
    args = parser.parse_args(argv)
    if args.max_cases < 0 or args.max_seconds < 0:
        parser.error("optional limits must be nonnegative (0=unlimited)")
    if args.check and args.run_dir:
        parser.error("--check cannot resume a run")
    # Read-only case inspection: Feishu auth calls are allowed, but no business
    # POST, report writer, run snapshot, or notification is reached.
    if args.check:
        from urllib.parse import urlsplit
        url_host = urlsplit(args.source_url).hostname if args.source_url else ''
        if args.source == 'feishu' or (url_host and (url_host == 'feishu.cn' or url_host.endswith('.feishu.cn') or url_host == 'larksuite.com' or url_host.endswith('.larksuite.com'))):
            try:
                path = Path(args.config)
                if not path.is_absolute():
                    path = probe.ROOT / path
                config = json.loads(path.read_text(encoding='utf8'))
                source = _source(config, args)
                auth = dict(config.get('feishu_docs_auth') or {})
                if args.feishu_credentials_file:
                    auth['credentials_file'] = args.feishu_credentials_file
                stats = {}
                cases = probe.read_cases(source,stats=stats,feishu_docs_auth=auth)
                if args.case_id:
                    cases = [c for c in cases if c['case_id'] == args.case_id]
                if not cases:
                    raise ValueError('selected_cases_empty')
                _emit({'case_schema_valid': True, 'enabled_cases':len(cases),
                       'case_ids':[c['case_id'] for c in cases],
                       'source':'feishu','source_rows':stats.get('source_rows'),
                       'skipped_rows':stats.get('skipped_rows',0),
                       'business_posts':0,'feishu_messages':0})
                return 0
            except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
                print(json.dumps({'check_status':'ERROR','error_type':type(exc).__name__}),file=sys.stderr)
                return 2
        check_args = ["--config", args.config, "--check"]
        for flag, value in (("--source", args.source), ("--cases", args.cases),
                            ("--case-id", args.case_id), ("--source-url", args.source_url)):
            if value:
                check_args += [flag, value]
        return probe._legacy_main(check_args)
    try:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = probe.ROOT / config_path
        cfg = json.loads(config_path.read_text(encoding="utf8"))
        if not isinstance(cfg, dict) or not isinstance(cfg.get("api"), dict):
            raise ValueError("api_config_required")
        cfg["source"] = _source(cfg, args)
        if args.feishu_credentials_file:
            if cfg["source"].get("type") != "feishu":
                raise ValueError("feishu_credentials_file_requires_feishu_source")
            cfg["feishu_docs_auth"] = dict(cfg.get("feishu_docs_auth") or {})
            cfg["feishu_docs_auth"]["credentials_file"] = args.feishu_credentials_file
        if args.no_feishu:
            cfg["feishu"] = dict(cfg.get("feishu") or {}, enabled=False)
            cfg['feishu_archive']=dict(cfg.get('feishu_archive') or {},enabled=False)
        validate_report_outputs(cfg)
        validate_probe_notification(cfg.get("feishu", {}))
        probe.resolve_headers(cfg["api"])
        if os.name == "nt":
            raise ValueError("v2_windows_not_verified")
        parent = _report_root(cfg, probe) / "v2-runs"
        with _launch_lock(parent):
            selected = Path(args.run_dir) if args.run_dir else _resumable(parent, cfg["source"], cfg["api"])
            if selected is not None:
                store = RunStore(selected)
                try:
                    snap = store.snapshot()
                    store.validate_api(cfg["api"])
                    # An explicit --run-dir is a frozen run identity: the original
                    # selected tab and case subset are immutable across retries.
                    if args.run_dir and not any((args.source_url, args.source, args.cases, args.case_id)):
                        cfg["source"] = snap["payload"]["source"]
                    if snap["payload"]["source"] != cfg["source"]:
                        raise ValueError("resume_source_mismatch")
                except BaseException:
                    store.close()
                    raise
            else:
                stats = {}
                if cfg["source"].get("type") == "feishu":
                    cases = probe.read_cases(cfg["source"], stats=stats,
                                             feishu_docs_auth=cfg.get("feishu_docs_auth"))
                else:
                    cases = probe.read_cases(cfg["source"], cfg.get("tencent_docs_auth"), stats=stats)
                if args.case_id:
                    cases = [case for case in cases if case["case_id"] == args.case_id]
                if not cases:
                    raise ValueError("selected_cases_empty")
                snap_path, _ = freeze_cases(cases, source=cfg["source"], source_stats=stats,
                                            directory=_report_root(cfg, probe) / "v2-case-snapshots")
                store = RunStore.create(parent, snap_path, cfg["api"],
                                        retry_policy=cfg.get('unknown_retry',{'enabled':False}))
        try:
            captured = v2_engine.capture(store, cfg, args.max_cases, args.max_seconds)
            plan = captured["plan"]
            base = {"run_dir": str(store.directory), "run_id": store.snapshot()["run_id"],
                    "automatic_retry_history_count": len(store.retry_history()),
                    "plan_counts": {key: len(value) for key, value in plan.items()},
                    "processed_this_batch": captured["processed_this_batch"]}
            if plan["unknown"]:
                partial = v2_engine.partial_report(store, cfg)
                incomplete_notice = {'state':'DEFERRED_UNTIL_PENDING_ZERO'}
                archive = {'status':'DEFERRED_UNTIL_PENDING_ZERO'}
                if not plan["pending"]:
                    old_notice = store.db.execute(
                        "SELECT state FROM partial_notification WHERE id=1").fetchone()
                    if cfg.get("feishu",{}).get("enabled",True):
                        if old_notice:
                            incomplete_notice = {'state':old_notice[0],
                                                 'reason':'previous_partial_notification_not_resent'}
                            if cfg.get('feishu_archive',{}).get('enabled') is True:
                                archive=v2_engine.archive_partial(store,cfg,partial)
                        else:
                            incomplete_notice=v2_engine.notify_partial(store,cfg)
                            archive=incomplete_notice.get('archive',{'status':'DISABLED'})
                    elif cfg.get('feishu_archive',{}).get('enabled') is True:
                        archive=v2_engine.archive_partial(store,cfg,partial)
                _emit(dict(base, workflow_status="BLOCKED_UNKNOWN_REQUIRES_RECONCILIATION",
                           unknown_case_ids=plan["unknown"], partial_report=partial["json_report"],
                           archive=archive, notification=incomplete_notice,
                           automatic_retry=False))
                return 2
            if plan["pending"]:
                _emit(dict(base, workflow_status="CONTINUE_REQUIRED",
                           resume_command="python3 scripts/probe.py --config " + shlex.quote(args.config) +
                           " --run-dir " + shlex.quote(str(store.directory)) +
                           (" --max-cases " + str(args.max_cases) if args.max_cases else "") +
                           (" --max-seconds " + str(args.max_seconds) if args.max_seconds else "") +
                           (" --no-feishu" if args.no_feishu else "")))
                return 0
            evaluation = v2_engine.rejudge(store, cfg)
            doc, receipts = v2_engine.report(store, cfg, evaluation["evaluation_id"])
            archive = {'status':'DISABLED'}
            card_title = ''
            if cfg.get('feishu_archive',{}).get('enabled') is True:
                from lib.feishu_archive import publish, source_labels, notification_title
                source_label,sheet_label=source_labels(store.snapshot()['payload']['source'],cfg)
                card_title=notification_title(source_label,sheet_label)
                archive=publish(store,cfg,doc,source_label,sheet_label)
                if archive.get('status')!='VERIFIED':
                    raise ValueError('feishu_archive_not_verified')
            notice = v2_engine.notify(store, cfg, doc, archive=archive, card_title=card_title) if cfg.get("feishu", {}).get("enabled", True) else {"status":"disabled"}
            _emit(dict(base, workflow_status="COMPLETE", summary=doc["summary"],
                       report_outputs=receipts, evaluation_id=evaluation["evaluation_id"],
                       archive=archive, notification=notice))
            if notice.get("state") not in (None, "SENT") and notice.get("status") != "disabled":
                return 3 if doc["summary"]["pass"] == doc["summary"]["total"] else 1
            return 0 if doc["summary"]["pass"] == doc["summary"]["total"] else 1
        finally:
            store.close()
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        # Errors may contain private data; expose stable exception class only.
        print(json.dumps({"workflow_status":"ERROR", "error_type":type(exc).__name__,
                          "detail":"执行前检查、用例来源、运行状态或输出失败；检查私有日志与配置"},
                         ensure_ascii=False),file=sys.stderr)
        return 2