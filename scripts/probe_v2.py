#!/usr/bin/env python3
"""Compatibility command entry for historical v2 subcommands.

The implementation lives in lib.v2_engine; new callers use scripts/probe.py.
"""
import probe  # historical monkeypatch surface shared with lib.v2_engine
from lib.v2_engine import (capture, rejudge, partial_report, report,
                           notify_partial, notify, main)

if __name__ == "__main__":
    raise SystemExit(main())