"""Private onboarding: no network requests and no secret values in diagnostic output."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("private_setup", ROOT / "scripts/setup.py")
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        shutil.copyfile(ROOT / "config.example.json", self.folder / "config.example.json")
        (self.folder / "assets").mkdir()
        (self.folder / "assets/sample_cases.csv").write_text("序号,语料,判断依据（公共）,deviceId\n1,问候,HTTP 200,fixture\n")
        self.root_patch = patch.object(wizard, "ROOT", self.folder)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_csv_onboarding_private_and_no_overwrite(self):
        with patch.object(wizard, "secret", side_effect=[
            "https://unit-test.invalid/api/sse", "fixture-key", "fixture-sign",
        ]), patch.object(wizard, "answer", side_effect=[
            "assets/sample_cases.csv", "N",
        ]):
            path = wizard.configure("csv", "config.local.json")
        self.assertTrue(path.is_file())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        data = json.loads(path.read_text())
        self.assertFalse(data["feishu"]["enabled"])
        self.assertNotIn("feishu_docs_auth", data)
        out = io.StringIO()
        with redirect_stdout(out), patch("urllib.request.urlopen",
                                          side_effect=AssertionError("network")) as net:
            self.assertEqual(wizard.main(["doctor"]), 0)
        net.assert_not_called()
        self.assertNotIn("fixture-key", out.getvalue())
        self.assertNotIn("fixture-sign", out.getvalue())
        self.assertNotIn("unit-test.invalid", out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["status"], "READY_OFFLINE")
        with self.assertRaises(ValueError):
            wizard.configure("csv", "config.local.json")
        self.assertEqual(json.loads(path.read_text()), data)

    def test_tencent_official_token_saved_only_locally(self):
        with patch.object(wizard, "secret", side_effect=[
            "https://unit-test.invalid/api/sse", "fixture-key", "fixture-sign",
            "https://docs.qq.com/sheet/EXAMPLEDOC0001?tab=TAB01", "private-fixture-token",
        ]), patch.object(wizard, "answer", return_value="N"):
            wizard.configure("tencent", "config.local.json")
        report = wizard.doctor()
        self.assertEqual(report["status"], "READY_OFFLINE", report)
        self.assertEqual(report["source"], "tencent")
        saved = json.loads((self.folder / "config.local.json").read_text())
        self.assertEqual(saved["tencent_docs_auth"]["token"], "private-fixture-token")
        self.assertEqual(saved["source"]["tencent"]["columns"]["expected_keywords"],
                         "判断依据（公共）")

    def test_feishu_sheet_app_credentials_private(self):
        with patch.object(wizard, "secret", side_effect=[
            "https://unit-test.invalid/api/sse", "fixture-key", "fixture-sign",
            "https://test.feishu.cn/sheets/EXAMPLEFILE0001?sheet=TAB01",
            "fixture-app-id", "fixture-app-secret",
        ]), patch.object(wizard, "answer", return_value="N"):
            wizard.configure("feishu", "config.local.json")
        self.assertEqual(wizard.doctor()["status"], "READY_OFFLINE")

    def test_invalid_and_unsafe_paths_rejected(self):
        for bad in ("../config.local.json", "/tmp/config.local.json", "config.json",
                    "config.local.json/child"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                wizard.config_path(bad)
        self.assertEqual(wizard.doctor()["status"], "INCOMPLETE")
        target = self.folder / "config.local.json"
        target.symlink_to(self.folder / "config.example.json")
        self.assertIn("private_config_missing", wizard.doctor()["issues"])
        with self.assertRaises(ValueError):
            wizard.private_create(target, {"fixture": "value"})


if __name__ == "__main__":
    unittest.main()