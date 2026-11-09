#!/usr/bin/env python3
"""Read approved source and freeze a private case snapshot; zero business POST."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe
from lib.case_snapshot import freeze_cases
from lib.source_override import apply_tencent_source_url


def main(argv=None):
    parser = argparse.ArgumentParser(description="Freeze authorized cases, no business POST/Feishu")
    parser.add_argument("--config", default="config.local.json")
    parser.add_argument("--source-url")
    args = parser.parse_args(argv)
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = probe.ROOT / cfg_path
    cfg = json.loads(cfg_path.read_text(encoding="utf8"))
    source = dict(cfg.get("source") or {})
    if args.source_url:
        source = apply_tencent_source_url(source, args.source_url)
    stats = {}
    cases = probe.read_cases(source, cfg.get("tencent_docs_auth"), stats=stats)
    settings = cfg.get("reports") or {}
    root = Path(settings.get("dir", "reports"))
    if not root.is_absolute():
        root = probe.ROOT / root
    path, record = freeze_cases(cases, source=source, source_stats=stats,
                                directory=root / "v2-case-snapshots")
    print(json.dumps({"snapshot_path": str(path), "run_id": record["run_id"],
                      "sha256": record["snapshot_sha256"],
                      "case_count": len(cases), "source_type": source.get("type"),
                      "skipped_rows": stats.get("skipped_rows", 0),
                      "business_posts": 0, "feishu_messages": 0},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())