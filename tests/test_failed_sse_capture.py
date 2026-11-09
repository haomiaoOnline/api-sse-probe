"""Offline fail-only raw SSE retention: exact bytes, privacy, atomic overwrite."""
import base64
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import probe
from lib.failed_sse import LatestFailureCapture, target_key
from test_probe import API, CASE, OTHER, SUCCESS, FakeOpener, response_lines


class FailedSSETests(unittest.TestCase):
    def test_failed_sse_bytes_preserved_exactly(self):
        raw = b":heartbeat\n\nevent: message\ndata: {oops}\n\n"
        sink = io.BytesIO()
        result = probe.run_case(CASE, API, opener=FakeOpener([raw]),raw_capture=sink)
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["reason"],"invalid_sse_json")
        self.assertEqual(sink.getvalue(),raw)
        self.assertNotIn("raw_sse",result)

    def test_success_not_retained_and_failure_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder,API,{"type":"csv","path":"tests/a.csv"}) as snap:
                raw=response_lines(OTHER)
                a=probe.run_case(CASE,API,opener=FakeOpener(raw),raw_capture=io.BytesIO())
                self.assertEqual(a["status"],"FAIL")
                snap.add(a,b"".join(raw))
                passing=probe.run_case(CASE,API,opener=FakeOpener(response_lines(SUCCESS)))
                self.assertEqual(passing["status"],"PASS")
                snap.add(passing,b"data: SECRET_PASS_RESPONSE\n")
                snap.finish(2)
                path=snap.commit()
            doc=json.loads(path.read_text(encoding="utf8"))
            self.assertEqual(doc["failed_total"],1)
            self.assertEqual(doc["total_cases"],2)
            item=doc["failed_cases"][0]
            self.assertEqual(base64.b64decode(item["raw_sse_base64"]),b"".join(raw))
            self.assertEqual(item["capture_state"],"protocol_done")
            self.assertEqual(item["sha256"],hashlib.sha256(b"".join(raw)).hexdigest())
            self.assertNotIn("SECRET_PASS_RESPONSE",path.read_text())
            if os.name!="nt":
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                self.assertEqual(path.parent.stat().st_mode&0o777,0o700)

    def test_overwrite_same_target_and_isolate_other_target(self):
        source={"type":"tencent","tencent":{
            "doc_url":"https://docs.qq.com/sheet/EXAMPLE123?tab=tab1&token=SECRET_TOKEN",
            "sheet_id":"tab1"}}
        other={"type":"tencent","tencent":{
            "doc_url":"https://docs.qq.com/sheet/EXAMPLE123?tab=tab2",
            "sheet_id":"tab2"}}
        self.assertNotEqual(target_key(API,source),target_key(API,other))
        self.assertEqual(target_key(API,source),target_key(API,{
            "type":"tencent","tencent":{"doc_url":
            "https://docs.qq.com/sheet/EXAMPLE123?tab=tab1","sheet_id":"tab1"}}))
        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder,API,source) as snap:
                snap.add({"status":"ERROR","case_id":"17","reason":"timeout"},b"data: raw secret\n")
                snap.finish(1)
                first=snap.commit()
            with LatestFailureCapture(folder,API,other) as snap:
                snap.add({"status":"FAIL","case_id":"18","reason":"done_missing"},b"data: partial\n")
                snap.finish(1)
                second=snap.commit()
            self.assertNotEqual(first,second)
            with LatestFailureCapture(folder,API,source) as snap:
                snap.add({"status":"PASS","case_id":"17","reason":"ok"},b"NOT_SAVED")
                snap.finish(1)
                replaced=snap.commit()
            self.assertEqual(first,replaced)
            record=json.loads(first.read_text())
            self.assertEqual(record["failed_total"],0)
            self.assertEqual(record["failed_cases"],[])
            self.assertTrue(second.exists())
            self.assertEqual(len(list(first.parent.glob("*.json"))),2)
            self.assertEqual(len(list(first.parent.glob("*.pending"))),0)
            self.assertNotIn("SECRET_TOKEN",str(first))
            self.assertNotIn("raw secret",first.read_text())

    def test_on_abort_keep_prior_committed_snapshot_and_clean_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder,API,{"type":"csv","path":"x"}) as snap:
                snap.add({"status":"FAIL","case_id":"1"},b"original")
                snap.finish(1);old=snap.commit()
            oldbytes=old.read_bytes()
            with self.assertRaisesRegex(RuntimeError,"simulated"):
                with LatestFailureCapture(folder,API,{"type":"csv","path":"x"}) as snap:
                    snap.add({"status":"ERROR","case_id":"1"},b"partial")
                    raise RuntimeError("simulated")
            self.assertEqual(old.read_bytes(),oldbytes)
            self.assertFalse(old.with_suffix(".pending").exists())

    def test_explicit_private_sse_export_with_integrity_check(self):
        from inspect_failed_sse import read_snapshot,case_bytes,main as inspect
        with tempfile.TemporaryDirectory() as folder:
            source={"type":"csv","path":"test"}
            payload=b"event: test\\ndata: {\"a\":1}\\n\\ndata: [DONE]\\n\\n"
            with LatestFailureCapture(folder,API,source) as snap:
                snap.add({"case_id":"17","status":"FAIL","reason":"assertions_failed",
                          "done_received":True},payload)
                snap.finish(1)
                path=snap.commit()
            data=read_snapshot(path)
            body,meta=case_bytes(data,"17")
            self.assertEqual(body,payload)
            self.assertEqual(meta["capture_state"],"protocol_done")
            export=Path(folder)/"authorized-case17.sse"
            with contextlib.redirect_stdout(io.StringIO()):
                rc=inspect(["--report",str(path),"--case-id","17",
                            "--output",str(export)])
            self.assertEqual(rc,0)
            self.assertEqual(export.read_bytes(),payload)
            if os.name!="nt":
                self.assertEqual(export.stat().st_mode&0o777,0o600)
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(FileExistsError):
                    inspect(["--report",str(path),"--case-id","17",
                             "--output",str(export)])
            self.assertEqual(export.read_bytes(),payload)
            data["failed_cases"][0]["sha256"]="bad"
            with self.assertRaisesRegex(ValueError,"failed_sse_integrity_mismatch"):
                case_bytes(data,"17")

    def test_size_limit_explicit_and_preserves_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            with LatestFailureCapture(folder,API,{"type":"csv","path":"x"}) as s:
                s.add({"status":"FAIL","case_id":"1"},b"before")
                s.finish(1);p=s.commit()
            prev=p.read_bytes()
            with self.assertRaisesRegex(ValueError,"failed_sse_total_byte_limit_exceeded"):
                with LatestFailureCapture(folder,API,{"type":"csv","path":"x"}, max_total_bytes=3) as s:
                    s.add({"status":"FAIL","case_id":"1"},b"payload longer")
            self.assertEqual(p.read_bytes(),prev)

    def test_main_end_to_end_fake_network_creates_raw_file_and_normal_report(self):
        import copy
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            cfg=json.loads((ROOT/"config.example.json").read_text())
            cfg["reports"]["dir"]=str(directory/"reports")
            cfg["source"]={"type":"csv","path":"assets/sample_cases.csv"}
            cfg["feishu"]["enabled"]=False
            private=directory/"config.local.json"
            private.write_text(json.dumps(cfg))
            original=probe.run_case
            def fake_run(case,api,assertions=None,opener=None,raw_capture=None,
                         capture_metadata=None):
                return original(case,api,assertions,
                                opener=FakeOpener(response_lines(OTHER)),
                                raw_capture=raw_capture,
                                capture_metadata=capture_metadata)
            output=io.StringIO()
            with patch.object(probe,"run_case",side_effect=fake_run),contextlib.redirect_stdout(output):
                exitcode=probe._legacy_main(["--config",str(private)])
            self.assertEqual(exitcode,1)
            lines=[json.loads(line) for line in output.getvalue().splitlines()]
            report=next(x for x in lines if "json_report" in x)
            raw_path=Path(report["failed_sse_report"])
            self.assertTrue(raw_path.exists())
            self.assertEqual(report["failed_sse_cases"],1)
            self.assertEqual(json.loads(raw_path.read_text())["failed_total"],1)
            normal_report=Path(report["json_report"]).read_text()
            self.assertNotIn("raw_sse_base64",normal_report)
            self.assertNotIn("data: ",normal_report)
            self.assertTrue(Path(report["markdown_report"]).exists())


if __name__=="__main__":
    unittest.main()