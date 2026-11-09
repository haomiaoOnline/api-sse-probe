"""Atomically retain the latest failed-case raw SSE bytes for each probe target.

Private JSON snapshot: case bytes are base64-encoded to preserve framing,
including SSE comments / event / data / blank lines. Reports and Feishu never
embed raw response bodies. One .json and one bounded .pending per target.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
HARD_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def target_key(api, source):
    """Stable 128-bit URL+source fingerprint, without secrets in the filename."""
    stype = str(source.get("type") or "csv")
    info = {"type": stype}
    if stype == "tencent":
        tc = source.get("tencent") or {}
        url = urlsplit(str(tc.get("doc_url") or ""))
        info.update({"file_id": tc.get("file_id") or url.path,
                     "sheet": tc.get("sheet_id") or tc.get("sheet_name") or
                     (parse_qs(url.query).get("tab") or [""])[0]})
    else:
        info["path"] = str(source.get("path") or "")
    value = {"url": api.get("url") or "", "source": info,
             "endpoint": (api.get("body_defaults") or {}).get("endpoint") or {}}
    identity = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",",":"), default=str)
    return hashlib.sha256(identity.encode("utf8")).hexdigest()[:32]


def private_directory(report_dir):
    path = Path(report_dir) / "latest-failed-sse"
    if path.is_symlink():
        raise OSError("failed_sse_private_directory_symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise OSError("failed_sse_private_directory_invalid")
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


class LatestFailureCapture:
    """Write .pending incrementally, publish only after full report succeeds.

    This class is intentionally not multi-writer-safe: callers must ensure a
    single execution per target. A killed process leaves one .pending path,
    overwritten by the next execution of the same target.
    """
    def __init__(self, report_dir, api, source, max_total_bytes=None):
        self.max_total = (DEFAULT_MAX_TOTAL_BYTES if max_total_bytes is None
                          else max_total_bytes)
        if (isinstance(self.max_total, bool) or not isinstance(self.max_total, int)
                or not 1 <= self.max_total <= HARD_MAX_TOTAL_BYTES):
            raise ValueError("reports.failed_sse_max_total_bytes_invalid")
        folder = private_directory(report_dir)
        key = target_key(api, source)
        self.target = folder / (key + ".json")
        self.pending = folder / (key + ".pending")
        if self.target.is_symlink() or self.pending.is_symlink():
            raise OSError("failed_sse_snapshot_symlink")
        self.total_bytes = 0
        self.failure_count = 0
        self.committed = False
        # New run replaces only its stable pending file; previous .json stays.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(self.pending), flags, 0o600)
        if os.name != "nt":
            os.fchmod(fd,0o600)
        self.handle = os.fdopen(fd,"w",encoding="utf8")
        head = {"version": 2, "captured_at_utc":datetime.now(timezone.utc).isoformat(),
                "target_key": key, "encoding": "base64",
                "note": "Private request/response context and exact received response bytes; credentials are restricted to this file."}
        self.handle.write(json.dumps(head, ensure_ascii=False,
                                     separators=(",",":"))[:-1])
        self.handle.write(',"failed_cases":[')

    def add(self, result, raw, capture_metadata=None):
        if result.get("status") == "PASS":
            return None
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError("failed_sse_bytes_required")
        self.total_bytes += len(raw)
        if self.total_bytes > self.max_total:
            raise ValueError("failed_sse_total_byte_limit_exceeded")
        if self.failure_count:
            self.handle.write(",")
        self.failure_count += 1
        response_meta = (capture_metadata or {}).get("response") or {}
        if not raw:
            state = "not_received"
        elif response_meta.get("body_truncated"):
            state = "partial_or_size_limit"
        elif response_meta.get("body_kind") == "http_error_body":
            state = "http_error_body"
        elif response_meta.get("body_kind") == "non_sse_http_body":
            state = "non_sse_http_body"
        elif result.get("reason") == "sse_decode_or_size_error":
            state = "partial_or_size_limit"
        elif result.get("done_received"):
            state = "protocol_done"
        else:
            state = "partial_or_unterminated"
        record = {
            "case_id": str(result.get("case_id") or ""),
            "status": str(result.get("status") or "ERROR"),
            "reason": str(result.get("reason") or "unexpected_error"),
            "http_status": result.get("http_status"),
            "done_received": bool(result.get("done_received")),
            "capture_state": state,
            "received_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_sse_base64": base64.b64encode(raw).decode("ascii"),
            "request": (capture_metadata or {}).get("request"),
            "response": (capture_metadata or {}).get("response"),
        }
        self.handle.write(json.dumps(record, ensure_ascii=False,
                                     separators=(",",":")))
        return {"path": str(self.target), "case_id": record["case_id"],
                "received_bytes": len(raw), "sha256": record["sha256"],
                "capture_state": state, "schema_version": 2,
                "http_status": response_meta.get("status"),
                "content_type": response_meta.get("content_type")}

    def finish(self, total_cases):
        self.handle.write('],"failed_total":' + str(self.failure_count))
        self.handle.write(',"total_cases":' + str(total_cases) + "}\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()

    def commit(self):
        if not self.handle.closed:
            raise ValueError("failed_sse_snapshot_not_finished")
        os.replace(self.pending, self.target)
        self.committed = True
        return self.target

    def close(self):
        if not self.handle.closed:
            self.handle.close()
        # On an ordinary failed run, keep the previously committed snapshot.
        # Remove aborted .pending; abrupt process termination can leave one
        # pending file, replaced at the next run for the same target.
        if not self.committed and self.pending.exists():
            self.pending.unlink()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()