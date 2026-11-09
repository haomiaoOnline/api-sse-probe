"""Per-run Tencent spreadsheet URL precedence and notification policy.

All tests use temporary configuration and in-process stubs. No actual remote
Tencent, SSE business endpoint or Feishu access is performed.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.source_override import apply_tencent_source_url, SourceOverrideError

NEW_URL = "https://docs.qq.com/sheet/DFAKE12345?tab=ExampleTab09"
OLD_URL = "https://docs.qq.com/sheet/DoldConfigId?tab=oldtab"
COLS = {"case_id":"A", "enabled":"B", "content":"C",
        "deviceId":"D", "expected_keywords":"E"}
ROWS = [
    ["ID","enabled","content","deviceId","expected_keywords"],
    ["1","true","播放音乐","dev-1","音乐"],
    ["2","false","关闭电视","dev-2","关闭"],
    ["3","true","看直播","dev-3","直播"],
]


class SourceUrlOverrideTests(unittest.TestCase):
    def test_new_link_replaces_all_stale_identity_without_mutation(self):
        source = {"type":"tencent", "path":"assets/old.csv",
                  "tencent":{"doc_url":OLD_URL,"file_id":"DoldConfigId",
                             "sheet_id":"oldtab","sheet_name":"旧场景",
                             "range":"A1:E4","header_row":1,"columns":COLS}}
        source_snapshot = json.dumps(source,sort_keys=True)
        actual = apply_tencent_source_url(source,NEW_URL+"&utm_source=chat")
        self.assertEqual(actual["type"],"tencent")
        self.assertEqual(actual["tencent"]["file_id"],"DFAKE12345")
        self.assertEqual(actual["tencent"]["sheet_id"],"ExampleTab09")
        self.assertEqual(actual["tencent"]["doc_url"],NEW_URL)
        self.assertNotIn("sheet_name",actual["tencent"])
        self.assertNotIn("path",actual)
        self.assertEqual(actual["tencent"]["columns"],COLS)
        self.assertEqual(actual["tencent"]["range"],"A1:E4")
        self.assertEqual(json.dumps(source,sort_keys=True),source_snapshot)

    def test_switch_from_csv_to_tencent_without_source_config(self):
        source = {"type":"csv","path":"assets/sample_cases.csv"}
        actual = apply_tencent_source_url(source,NEW_URL)
        self.assertEqual(actual["type"],"tencent")
        self.assertEqual(actual["tencent"]["sheet_id"],"ExampleTab09")
        self.assertNotIn("path",actual)

    def test_source_section_may_be_absent_with_explicit_url(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"config.json"
            config=json.loads((ROOT/"config.example.json").read_text(encoding="utf8"))
            config.pop("source")
            path.write_text(json.dumps(config),encoding="utf8")
            with patch("lib.tencent_mcp.read_values",return_value=ROWS) as read, \
                 redirect_stdout(io.StringIO()):
                rc=probe._legacy_main(["--config",str(path),"--source-url",NEW_URL,
                               "--check","--no-feishu"])
            self.assertEqual(rc,0)
            self.assertEqual(read.call_args.args[0]["sheet_id"],"ExampleTab09")

    def test_untrusted_or_ambiguous_link_rejected(self):
        bad_links = (
            "https://docs.qq.com.evil.net/sheet/D123?tab=abc",
            "http://docs.qq.com/sheet/D123?tab=abc",
            "https://docs.qq.com:443/sheet/D123?tab=abc",
            "https://docs.qq.com@evil.net/sheet/D123?tab=abc",
            "https://docs.qq.com/doc/D123?tab=abc",
            "https://docs.qq.com/sheet/D123",
            "https://docs.qq.com/sheet/D123?tab=abc&tab=def",
            "https://docs.qq.com/sheet/D123?tab=",
            "https://docs.qq.com/sheet/D123?tab=bad%2Fid",
            "https://docs.qq.com/sheet/D123?tab=abc#fragment",
            "file:///private/secrets",
            "https://docs.qq.com/sheet/D123?tab=abc\nnewarg",
        )
        for link in bad_links:
            with self.subTest(link=link), self.assertRaises(SourceOverrideError):
                apply_tencent_source_url({"type":"tencent"},link)

    def _write_config(self, root, *, feishu_enabled=True):
        cfg = json.loads((ROOT/"config.example.json").read_text(encoding="utf8"))
        cfg["source"] = {"type":"tencent","tencent":{
            "doc_url":OLD_URL,"file_id":"DoldConfigId",
            "sheet_id":"oldtab","sheet_name":"旧表格",
            "range":"A1:E4","header_row":1,"columns":COLS,
        }}
        cfg["tencent_docs_auth"]={"token":"FAKE-TOKEN-NO-NETWORK"}
        cfg["feishu"]={"enabled":feishu_enabled,"notification_style":"v2",
                       "WEBHOOK_URL":"https://open.feishu.cn/open-apis/bot/v2/hook/FAKE_NO_NETWORK",
                       "SECRET":"FAKE_NO_NETWORK"}
        cfg["reports"]["dir"]=str(root/"reports")
        configfile = root/"config.local.json"
        configfile.write_text(json.dumps(cfg,ensure_ascii=False),encoding="utf8")
        return configfile

    def test_explicit_link_executes_all_enabled_cases_and_disables_feishu(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            configfile=self._write_config(root)
            original=configfile.read_bytes()
            called=[]
            def fake_values(config, auth=None):
                called.append(("mcp",dict(config),dict(auth)))
                return ROWS
            def fake_run(case, api, assertions=None, **kwargs):
                called.append(("post",case["case_id"]))
                return {"case_id":case["case_id"],"status":"PASS",
                        "reason":"ok","http_status":200,"duration_ms":18,
                        "json_frame_count":1,"done_received":True,
                        "matched_keywords":case["expected_keywords"],
                        "missing_keywords":[],"forbidden_found":[]}
            out, err=io.StringIO(),io.StringIO()
            with patch("lib.tencent_mcp.read_values",side_effect=fake_values), \
                 patch.object(probe,"run_case",side_effect=fake_run), \
                 patch("lib.feishu_notification_router.send_card_payload") as deliver, \
                 redirect_stdout(out),redirect_stderr(err):
                rc=probe._legacy_main(["--config",str(configfile),"--source-url",NEW_URL,
                               "--no-feishu"])
            self.assertEqual(rc,0,err.getvalue())
            self.assertEqual([entry[1] for entry in called if entry[0]=="post"],["1","3"])
            mcp=next(entry for entry in called if entry[0]=="mcp")
            self.assertEqual(mcp[1]["file_id"],"DFAKE12345")
            self.assertEqual(mcp[1]["sheet_id"],"ExampleTab09")
            self.assertNotIn("sheet_name",mcp[1])
            self.assertEqual(mcp[2]["token"],"FAKE-TOKEN-NO-NETWORK")
            self.assertEqual(configfile.read_bytes(),original)
            self.assertEqual(json.loads(out.getvalue().splitlines()[0])["summary"]["total"],2)
            self.assertTrue((root/"reports").is_dir())
            deliver.assert_not_called()
            self.assertIn('"feishu_notification": "disabled"',out.getvalue())

    def test_real_probe_sends_by_default_with_configured_target(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            configfile=self._write_config(root)
            cfg=json.loads(configfile.read_text(encoding="utf8"))
            cfg["feishu"].pop("enabled")  # Missing flag also means enabled.
            configfile.write_text(json.dumps(cfg),encoding="utf8")
            def fake_run(case, api, assertions=None, **kwargs):
                return {"case_id":case["case_id"],"status":"FAIL",
                        "reason":"assertions_failed","http_status":200,
                        "duration_ms":18,"json_frame_count":1,"done_received":True,
                        "matched_keywords":[],"missing_keywords":["音乐"],
                        "forbidden_found":[]}
            with patch("lib.tencent_mcp.read_values",return_value=ROWS), \
                 patch.object(probe,"run_case",side_effect=fake_run) as post, \
                 patch("lib.feishu_notification_router.send_card_payload",
                       return_value="sent") as deliver, \
                 redirect_stdout(io.StringIO()) as out:
                rc=probe._legacy_main(["--config",str(configfile),"--source-url",NEW_URL])
            self.assertEqual(rc,1)
            self.assertEqual(post.call_count,2)
            self.assertEqual(deliver.call_count,1)
            self.assertIn(NEW_URL,json.dumps(deliver.call_args.args[1],ensure_ascii=False))
            self.assertIn('"feishu_notification": "sent"',out.getvalue())
            self.assertEqual(configfile.read_text(encoding="utf8"),json.dumps(cfg))

    def test_missing_feishu_target_stops_before_business_post(self):
        with tempfile.TemporaryDirectory() as td:
            configfile=self._write_config(Path(td))
            cfg=json.loads(configfile.read_text(encoding="utf8"))
            cfg["feishu"].pop("WEBHOOK_URL")
            configfile.write_text(json.dumps(cfg),encoding="utf8")
            with patch("lib.tencent_mcp.read_values",return_value=ROWS), \
                 patch.object(probe,"run_case") as post, \
                 patch("lib.feishu_notification_router.send_card_payload") as deliver, \
                 redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                rc=probe._legacy_main(["--config",str(configfile),"--source-url",NEW_URL])
            self.assertEqual(rc,2)
            post.assert_not_called()
            deliver.assert_not_called()

    def test_explicit_check_only_reads_new_doc_never_posts(self):
        with tempfile.TemporaryDirectory() as td:
            configfile=self._write_config(Path(td))
            out=io.StringIO()
            with patch("lib.tencent_mcp.read_values",return_value=ROWS) as get, \
                 patch.object(probe,"run_case") as post, \
                 redirect_stdout(out):
                rc=probe._legacy_main(["--config",str(configfile),"--source-url",NEW_URL,"--check"])
            self.assertEqual(rc,0)
            self.assertEqual(json.loads(out.getvalue())["case_ids"],["1","3"])
            self.assertEqual(get.call_args.args[0]["sheet_id"],"ExampleTab09")
            post.assert_not_called()

    def test_without_override_old_source_is_still_used(self):
        with tempfile.TemporaryDirectory() as td:
            configfile=self._write_config(Path(td))
            out=io.StringIO()
            with patch("lib.tencent_mcp.read_values",return_value=ROWS) as get, \
                 redirect_stdout(out):
                rc=probe._legacy_main(["--config",str(configfile),"--check"])
            self.assertEqual(rc,0)
            self.assertEqual(get.call_args.args[0]["sheet_id"],"oldtab")

    def test_bad_url_or_conflicting_flags_fail_before_network(self):
        with tempfile.TemporaryDirectory() as td:
            configfile=self._write_config(Path(td))
            for flags in (
                ["--source-url","https://evil.net/sheet/D?tab=x"],
                ["--source-url",NEW_URL,"--cases","sample.csv"],
                ["--source-url",NEW_URL,"--source","csv"],
            ):
                with self.subTest(flags=flags), \
                     patch("lib.tencent_mcp.read_values") as get, \
                     patch.object(probe,"run_case") as post, \
                     patch("lib.feishu_notification_router.send_probe_notice",
                           return_value="disabled"):
                    with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                        rc=probe._legacy_main(["--config",str(configfile),"--no-feishu"]+flags)
                    self.assertEqual(rc,2)
                    get.assert_not_called()
                    post.assert_not_called()


if __name__ == "__main__":
    unittest.main()