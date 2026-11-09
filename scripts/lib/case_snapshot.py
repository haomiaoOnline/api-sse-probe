"""Private immutable case snapshot (v2 M1 slice), separate from live execution."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _dir_secure(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("snapshot_directory_symlink_forbidden")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def freeze_cases(cases, *, source, source_stats, directory, version="2.4.3"):
    """Create a new private snapshot; never modify historical reports.

    No network, API request, notification, or source reread occurs here.
    """
    if not isinstance(cases, list) or not cases:
        raise ValueError("snapshot_requires_nonempty_cases")
    ids = [str(c["case_id"]) for c in cases]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        raise ValueError("snapshot_case_ids_invalid")
    if not isinstance(source, dict) or not isinstance(source_stats, dict):
        raise ValueError("snapshot_source_invalid")
    frozen = [{"ordinal": i + 1, **json.loads(canonical(case))}
              for i, case in enumerate(cases)]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": version,
        "source": json.loads(canonical(source)),
        "source_stats": json.loads(canonical(source_stats)),
        "cases": frozen,
    }
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    record = {
        "run_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": digest,
        "payload": payload,
    }
    directory = _dir_secure(directory)
    path = directory / ("snapshot-" + record["run_id"] + ".json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(canonical(record) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        check = read_snapshot(path)
        if check["snapshot_sha256"] != digest:
            raise ValueError("snapshot_write_integrity_failure")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, record


def read_snapshot(path):
    """Verify sha256 over the exact canonical frozen payload."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("snapshot_symlink_forbidden")
    data = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
        raise ValueError("snapshot_invalid_format")
    if data["payload"].get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot_unknown_schema")
    actual = hashlib.sha256(canonical(data["payload"])).hexdigest()
    if actual != data.get("snapshot_sha256"):
        raise ValueError("snapshot_digest_mismatch")
    return data