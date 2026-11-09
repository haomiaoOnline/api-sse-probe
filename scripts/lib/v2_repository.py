"""Private per-run SQLite checkpoint store; unknown dispatches are never retried."""
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

from lib.case_snapshot import read_snapshot, canonical, _dir_secure

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
 ordinal INTEGER PRIMARY KEY,
 case_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('PENDING','DISPATCH_INTENT','CAPTURED')),
 attempt_id TEXT UNIQUE,
 result_json TEXT,
 evidence_file TEXT,
 evidence_sha256 TEXT,
 evidence_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS retry_history (
 case_id TEXT NOT NULL,
 prior_attempt_id TEXT NOT NULL UNIQUE,
 retry_attempt_id TEXT NOT NULL UNIQUE,
 retry_ordinal INTEGER NOT NULL,
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(case_id,retry_ordinal)
);
CREATE TABLE IF NOT EXISTS run_state (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
 evaluation_id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 assertions_sha256 TEXT NOT NULL,
 output_file TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_digests (
 evaluation_id TEXT PRIMARY KEY,
 sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification (
 id INTEGER PRIMARY KEY CHECK(id=1),
 state TEXT NOT NULL CHECK(state IN ('DISPATCH_INTENT','SENT','FAILED','PARTIAL','DELIVERY_UNKNOWN')),
 details TEXT
);
CREATE TABLE IF NOT EXISTS partial_notification (
 id INTEGER PRIMARY KEY CHECK(id=1),
 state TEXT NOT NULL CHECK(state IN ('DISPATCH_INTENT','SENT','FAILED','DELIVERY_UNKNOWN')),
 details TEXT
);
"""

def _atomic_private(path, value):
    """Exclusive private write, sync before SQLite state commit."""
    path = Path(path)
    if path.is_symlink() or path.exists():
        raise ValueError("evidence_exists_no_overwrite")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        # On POSIX, sync the directory entry before the SQLite state commit.
        # Otherwise a power failure could preserve CAPTURED but lose the file.
        if os.name != "nt":
            dirfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

class RunStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("run_directory_invalid")
        # Evidence references are absolute, so normalized relative CLI run
        # directories must compare against the same canonical run directory.
        self.directory = self.directory.resolve()
        self.db_path = self.directory / "state.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("run_database_symlink")
        self.db = sqlite3.connect(str(self.db_path), timeout=1, isolation_level="IMMEDIATE")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        if os.name != "nt":
            os.chmod(self.db_path, 0o600)
            for extension in ("-wal", "-shm"):
                sidecar = Path(str(self.db_path) + extension)
                if sidecar.exists() and not sidecar.is_symlink():
                    os.chmod(sidecar, 0o600)

    @classmethod
    def create(cls, parent, snapshot, api=None, retry_policy=None):
        frozen = read_snapshot(snapshot)
        run_id = frozen["run_id"]
        directory = _dir_secure(parent) / run_id
        directory.mkdir(mode=0o700)
        store = cls(directory)
        with store.db:
            store.db.execute("INSERT INTO run_state VALUES (?,?)", ("snapshot_sha256", frozen["snapshot_sha256"]))
            store.db.execute("INSERT INTO run_state VALUES (?,?)", ("snapshot_file", str(Path(snapshot).resolve())))
            store.db.execute("INSERT INTO run_state VALUES (?,?)", ("run_id", run_id))
            if api is not None:
                store.db.execute("INSERT INTO run_state VALUES (?,?)",
                                 ("api_fingerprint", hashlib.sha256(canonical(api)).hexdigest()))
                store.db.execute("INSERT INTO run_state VALUES (?,?)",
                                 ("require_done", "1" if api.get("require_done",True) else "0"))
                store.db.execute("INSERT INTO run_state VALUES (?,?)",
                                 ("expected_http_status", str(api.get("expected_http_status",200))))
            if retry_policy is not None:
                enabled = retry_policy.get('enabled') is True
                limit = retry_policy.get('max_retries_per_case',1)
                if type(limit) is not int or limit != 1:
                    raise ValueError('unknown_retry_max_one_only')
                store.db.execute('INSERT INTO run_state VALUES (?,?)',
                    ('unknown_auto_retry_enabled', '1' if enabled else '0'))
            for case in frozen["payload"]["cases"]:
                store.db.execute("INSERT INTO cases(ordinal,case_id,state) VALUES (?,?,?)",
                                 (case["ordinal"], str(case["case_id"]), "PENDING"))
        return store

    def close(self):
        self.db.close()

    def snapshot(self):
        path = self.db.execute("SELECT value FROM run_state WHERE key='snapshot_file'").fetchone()[0]
        snap = read_snapshot(path)
        saved = self.db.execute("SELECT value FROM run_state WHERE key='snapshot_sha256'").fetchone()[0]
        if snap["snapshot_sha256"] != saved:
            raise ValueError("frozen_snapshot_changed")
        return snap

    def validate_api(self, api):
        row = self.db.execute("SELECT value FROM run_state WHERE key='api_fingerprint'").fetchone()
        if row and hashlib.sha256(canonical(api)).hexdigest() != row[0]:
            raise ValueError("api_profile_changed_since_prepare")

    def rows(self):
        return list(self.db.execute("SELECT ordinal,case_id,state,attempt_id,result_json,evidence_file,evidence_sha256,evidence_bytes FROM cases ORDER BY ordinal"))

    def plan(self):
        rows = self.rows()
        return {"pending": [row[1] for row in rows if row[2] == "PENDING"],
                "unknown": [row[1] for row in rows if row[2] == "DISPATCH_INTENT"],
                "captured": [row[1] for row in rows if row[2] == "CAPTURED"]}

    def intent(self, cid):
        attempt = str(uuid.uuid4())
        with self.db:
            cur = self.db.execute("UPDATE cases SET state='DISPATCH_INTENT',attempt_id=? WHERE case_id=? AND state='PENDING'",
                                  (attempt, cid))
            if cur.rowcount != 1:
                raise ValueError("case_not_pending_no_auto_replay")
        return attempt

    def automatic_retry_allowed(self):
        # Existing runs are NOT silently opted in to changed replay policy.
        row = self.db.execute('SELECT value FROM run_state WHERE key=?',
                              ('unknown_auto_retry_enabled',)).fetchone()
        return row is not None and row[0]=='1'

    def queue_unknown_retry(self, cid):
        # Caller holds the exclusive run lock. The old attempt is recorded
        # in an append-only ledger in the same transaction as the new intent.
        if not self.automatic_retry_allowed():
            raise ValueError('unknown_auto_retry_not_enabled_for_run')
        row = self.db.execute('SELECT state,attempt_id FROM cases WHERE case_id=?',
                              (cid,)).fetchone()
        if row is None or row[0]!='DISPATCH_INTENT' or not row[1]:
            raise ValueError('unknown_retry_requires_dispatch_intent')
        old_attempt = row[1]
        path=self.directory/'evidence'/(old_attempt+'.json')
        if path.exists() or path.is_symlink():
            raise ValueError('unknown_retry_possible_committed_evidence_reconcile')
        count=self.db.execute('SELECT count(*) FROM retry_history WHERE case_id=?',
                              (cid,)).fetchone()[0]
        if count>=1:
            raise ValueError('unknown_auto_retry_limit_reached')
        new_attempt=str(uuid.uuid4())
        from datetime import datetime,timezone
        timestamp=datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute('INSERT INTO retry_history VALUES(?,?,?,?,?)',
                (cid,old_attempt,new_attempt,count+1,timestamp))
            cur=self.db.execute('UPDATE cases SET attempt_id=? WHERE case_id=? AND state=? AND attempt_id=?',
                (new_attempt,cid,'DISPATCH_INTENT',old_attempt))
            if cur.rowcount != 1:
                raise ValueError('unknown_retry_state_change_race')
        return new_attempt

    def retry_history(self):
        return [dict(case_id=row[0],prior_attempt_id=row[1],retry_attempt_id=row[2],
                     retry_ordinal=row[3],created_at_utc=row[4])
                for row in self.db.execute('SELECT case_id,prior_attempt_id,retry_attempt_id,retry_ordinal,created_at_utc FROM retry_history ORDER BY created_at_utc')]

    def captured(self, cid, attempt, result, raw, metadata):
        if not isinstance(raw, bytes):
            raise ValueError("raw_evidence_bytes_required")
        if not isinstance(result, dict) or result.get("status") not in ("CAPTURED", "PASS", "FAIL", "ERROR"):
            raise ValueError("result_invalid")
        row = self.db.execute("SELECT state,attempt_id FROM cases WHERE case_id=?", (cid,)).fetchone()
        if row != ("DISPATCH_INTENT", attempt):
            raise ValueError("attempt_state_mismatch")
        evidence_folder = _dir_secure(self.directory / "evidence")
        name = attempt
        # Preserve the complete original JSON objects, including unknown fields;
        # malformed frames are retained as raw strings and exact bytes separately.
        from probe import sse_events
        parsed_events = []
        try:
            for kind, data in sse_events(raw.splitlines(keepends=True)):
                try:
                    decoded = json.loads(data) if data.strip() != "[DONE]" else None
                except (TypeError, ValueError):
                    decoded = None
                parsed_events.append({"event": kind, "data": data, "json": decoded})
        except (ValueError, UnicodeError):
            parsed_events.append({"parse_error": "invalid_sse_bytes"})
        envelope = {"case_id": cid, "attempt_id": attempt, "result": result,
                    "parsed_events": parsed_events,
                    "capture_metadata": metadata, "raw_hex": raw.hex(),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "received_bytes": len(raw)}
        path = evidence_folder / (name + ".json")
        _atomic_private(path, canonical(envelope) + b"\n")
        with self.db:
            cur = self.db.execute("""UPDATE cases SET state='CAPTURED',result_json=?,evidence_file=?,evidence_sha256=?,evidence_bytes=?
                WHERE case_id=? AND state='DISPATCH_INTENT' AND attempt_id=?""",
                (json.dumps(result, ensure_ascii=False), str(path), envelope["raw_sha256"], len(raw), cid, attempt))
            if cur.rowcount != 1:
                raise ValueError("attempt_commit_race")
        return path

    def evidence(self, row):
        if row[2] != "CAPTURED":
            raise ValueError("uncaptured_case_cannot_read")
        path = Path(row[5])
        if path.is_symlink() or not path.is_file() or path.parent != self.directory / "evidence":
            raise ValueError("evidence_path_invalid")
        envelope = json.loads(path.read_text(encoding="utf8"))
        raw = bytes.fromhex(envelope["raw_hex"])
        if (len(raw) != row[7] or hashlib.sha256(raw).hexdigest() != row[6]
                or envelope.get("raw_sha256") != row[6]
                or envelope.get("received_bytes") != row[7]):
            raise ValueError("evidence_checksum_mismatch")
        if envelope.get("case_id") != row[1] or envelope.get("attempt_id") != row[3]:
            raise ValueError("evidence_identity_mismatch")
        return envelope, raw