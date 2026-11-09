"""Portable team package and safe installer tests (no real network)."""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("team_installer", ROOT/"scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class TeamPackageTests(unittest.TestCase):
    def setUp(self):
        self.manifest=(ROOT/"TEAM_MANIFEST.txt").read_text(encoding="utf8")
        self.entries=[l.strip() for l in self.manifest.splitlines() if l.strip() and not l.startswith("#")]

    def test_manifest_complete_safe_and_references_exist(self):
        self.assertGreaterEqual(len(self.entries),45)
        self.assertIn("TEAM_MANIFEST.txt",self.entries)
        self.assertIn("SKILL.md",self.entries)
        self.assertIn("config.annotated.jsonc",self.entries)
        self.assertIn("scripts/probe.py",self.entries)
        self.assertIn("scripts/build_release.py",self.entries)
        self.assertIn("scripts/lib/failed_sse.py",self.entries)
        self.assertIn("scripts/lib/diagnostic_context.py",self.entries)
        self.assertIn("scripts/lib/structured_assertions.py",self.entries)
        self.assertIn("tests/test_full_failure_evidence.py",self.entries)
        self.assertIn("tests/test_structured_field_assertions.py",self.entries)
        self.assertIn("scripts/lib/phase_transport.py",self.entries)
        self.assertIn("tests/test_phase_timeouts.py",self.entries)
        self.assertIn("references/timeout-and-diagnostics.md",self.entries)
        self.assertIn("scripts/inspect_failed_sse.py",self.entries)
        self.assertIn("tests/test_failed_sse_capture.py",self.entries)
        self.assertIn("scripts/lib/feishu_transport.py",self.entries)
        for path in self.entries:
            self.assertFalse("config.local" in path or path.startswith("reports/"))
            self.assertTrue((ROOT/path).is_file(),path)
        for doc in ("architecture.md", "assertions.md", "configuration.md",
                    "feishu-notification.md", "troubleshooting.md",
                    "timeout-and-diagnostics.md"):
            self.assertIn("references/"+doc,self.entries)


    def test_installer_rejects_private_data_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            source=base/"untrusted-copy"
            source.mkdir()
            for name in self.entries:
                target=source/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,target)
            # Runtime secrets/reports are deliberately present, but forbidden
            # by the *reviewed* manifest.
            (source/"config.someone.local.json").write_text("PRIVATE_SENTINEL")
            (source/"config.local.json").write_text("PRIVATE_SENTINEL")
            (source/"reports").mkdir()
            (source/"reports/probe-real.json").write_text("PRIVATE_SENTINEL")
            (source/".git").mkdir()
            (source/".git/config").write_text("PRIVATE_SENTINEL")
            skills=base/"skills"
            skills.mkdir()
            with patch.object(installer,"ROOT",source):
                with contextlib.redirect_stdout(io.StringIO()):
                    installer.main(["--target",str(skills)])
            deployed=skills/"api-sse-probe"
            self.assertTrue((deployed/"SKILL.md").is_file())
            self.assertFalse((deployed/"config.local.json").exists())
            self.assertTrue((deployed/"scripts/setup.py").exists())
            self.assertFalse((deployed/"config.someone.local.json").exists())
            self.assertFalse((deployed/"reports").exists())
            self.assertFalse((deployed/".git").exists())
            (deployed/"config.local.json").write_text("PRESERVE_THIS")
            with patch.object(installer,"ROOT",source):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        installer.main(["--target",str(skills)])
            self.assertEqual((deployed/"config.local.json").read_text(),"PRESERVE_THIS")
            with (source/"TEAM_MANIFEST.txt").open("a",encoding="utf8") as out:
                out.write("config.someone.local.json\n")
            with patch.object(installer,"ROOT",source):
                with self.assertRaises(ValueError):
                    installer.package_paths()

    def test_release_zip_contains_only_manifest_and_has_valid_sha(self):
        import hashlib
        import json
        import subprocess
        import sys
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/"team.zip"
            p=subprocess.run([sys.executable,str(ROOT/"scripts/build_release.py"),
                              "--output",str(out)],cwd=ROOT,
                              text=True,capture_output=True,timeout=20)
            self.assertEqual(p.returncode,0,p.stderr)
            meta=json.loads(p.stdout)
            self.assertEqual(meta["file_count"],len(self.entries))
            self.assertEqual(meta["sha256"],hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertTrue(out.with_suffix(".zip.sha256").is_file())
            with zipfile.ZipFile(out) as archive:
                names=archive.namelist()
                expected=["api-sse-probe/"+x for x in self.entries]
                self.assertEqual(sorted(names),sorted(expected))
                self.assertNotIn("config.local.json"," ".join(names))
                self.assertNotIn("reports/"," ".join(names))
                skill_text=archive.read("api-sse-probe/SKILL.md").decode("utf8")
                self.assertEqual(skill_text,(ROOT/"SKILL.md").read_text(encoding="utf8"))

    def test_jsonc_reference_has_comment_for_each_config_field(self):
        import json
        import re
        reference=(ROOT/"config.annotated.jsonc").read_text(encoding="utf8")
        lines=reference.splitlines()
        pairs=0
        for i,line in enumerate(lines):
            if re.match(r'^\s+"[^"]+"\s*:',line):
                self.assertGreater(i,0)
                self.assertTrue(lines[i-1].lstrip().startswith("//"),
                                "missing_comment_above_setting")
                pairs+=1
        self.assertGreaterEqual(pairs,30)
        parsed=json.loads("\n".join(
            x for x in lines if not x.lstrip().startswith("//")))
        self.assertEqual(parsed["source"]["type"],"tencent")
        self.assertFalse(parsed["feishu"]["enabled"])
        self.assertEqual(parsed["report_outputs"][0]["type"],"file")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(reference)
        self.assertNotIn("/Users/",reference)

    def test_example_disables_feishu_until_user_opts_in(self):
        import json
        c=json.loads((ROOT/"config.example.json").read_text(encoding="utf8"))
        self.assertFalse(c["feishu"]["enabled"])
        self.assertEqual(c["feishu"]["notification_style"],"v2")
        self.assertEqual(c["source"]["type"],"csv")

if __name__=="__main__":
    unittest.main()