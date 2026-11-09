"""Private case snapshot integrity; offline, zero remote requests."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib.case_snapshot import freeze_cases, read_snapshot


CASES = [{"case_id": "1", "scene": "示例", "content": "私人测试语料",
          "deviceId": "private-device", "expected_keywords": ["文本回复"]},
         {"case_id": "2", "scene": "示例", "content": "另一条",
          "deviceId": "private-device", "expected_keywords": ["HTTP 200"]}]
SRC = {"type": "tencent", "tencent": {"file_id": "Demo", "sheet_id": "SheetA"}}


class SnapshotTests(unittest.TestCase):
    def test_private_immutable_roundtrip_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "private"
            path, record = freeze_cases(CASES, source=SRC,
                                        source_stats={"source_rows": 2},
                                        directory=folder)
            self.assertEqual(read_snapshot(path), record)
            self.assertEqual([x["ordinal"] for x in record["payload"]["cases"]], [1, 2])
            self.assertEqual([x["case_id"] for x in record["payload"]["cases"]], ["1", "2"])
            self.assertEqual(record["payload"]["cases"][0]["content"], "私人测试语料")
            if os.name != "nt":
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_tampering_detected_and_original_inputs_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            prior = json.dumps(CASES, ensure_ascii=False)
            path, _ = freeze_cases(CASES, source=SRC, source_stats={},
                                   directory=td)
            self.assertEqual(json.dumps(CASES, ensure_ascii=False), prior)
            item = json.loads(path.read_text())
            item["payload"]["cases"][0]["content"] = "改动"
            path.write_text(json.dumps(item))
            with self.assertRaisesRegex(ValueError, "snapshot_digest_mismatch"):
                read_snapshot(path)

    def test_duplicate_cases_rejected_without_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "snapshot_case_ids_invalid"):
                freeze_cases([CASES[0], CASES[0]], source=SRC, source_stats={},
                             directory=Path(td) / "private")
            self.assertFalse((Path(td) / "private").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_directory_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "actual").mkdir()
            (base / "link").symlink_to(base / "actual", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                freeze_cases(CASES, source=SRC, source_stats={},
                             directory=base / "link")