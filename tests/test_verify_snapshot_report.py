"""Offline snapshot/report reconciliation, no business POST."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib.case_snapshot import freeze_cases
from verify_snapshot_report import verify


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.snapshot, _ = freeze_cases(
            [{"case_id": "1", "content": "private"}, {"case_id": "2", "content": "private"}],
            source={"type": "tencent"}, source_stats={}, directory=self.base / "snaps")
        self.report = self.base / "report.json"
        self.data = {"run_id": "report-run",
                     "results": [{"case_id": "1", "status": "PASS"},
                                 {"case_id": "2", "status": "FAIL"}],
                     "summary": {"total": 2, "pass": 1, "fail": 1, "error": 0}}

    def _check(self):
        self.report.write_text(json.dumps(self.data))
        return verify(self.snapshot, self.report)

    def test_matching_result_and_summary(self):
        result = self._check()
        self.assertTrue(result["verified"])
        self.assertEqual(result["fail"], 1)
        self.assertEqual(result["business_posts"], 0)

    def test_mismatched_order_rejected(self):
        self.data["results"].reverse()
        with self.assertRaisesRegex(ValueError, "case_sequence_mismatch"):
            self._check()

    def test_summary_mismatch_rejected(self):
        self.data["summary"]["pass"] = 2
        with self.assertRaisesRegex(ValueError, "summary_mismatch"):
            self._check()

    def test_unknown_status_rejected(self):
        self.data["results"][0]["status"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "unknown_status"):
            self._check()