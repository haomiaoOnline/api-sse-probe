"""M2-M5 offline fault injection, exact response bytes and no automatic replay."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe_v2
from lib.case_snapshot import freeze_cases
from lib.v2_repository import RunStore
from lib.v2_rejudge import judge_case

FRAME = {"choices":[{"delta":{"content":{"data":{"text":"北京天气晴朗","title":"天气助手",
                                                  "questions":["需要明天的吗？"],
                                                  "extra":{"keep_nested":[1,{"k":"v"}]}}}}}]}
RAW = ("event: message\n" + "data: " + json.dumps(FRAME,ensure_ascii=False) +
       "\n\n" + "data: [DONE]\n\n").encode("utf-8")
CASE = {"case_id":"1","content":"私人语料","deviceId":"fake","scene":"天气",
        "category":"", "expected_keywords":["HTTP 200","回复中包含“北京”和“天气”"],
        "forbidden_keywords":[], "match_mode":"all", "endpoint":{}, "session":{}}
CASE2 = dict(CASE,case_id="2")


def fake_capture(case, api, assertions=None, raw_capture=None,
                 capture_metadata=None, defer_assertions=False, **_):
    assert defer_assertions
    raw_capture.write(RAW)
    capture_metadata.update({
        "request":{"body_json":{"deviceId":"fake"}},
        "response":{"headers":[["Content-Type","text/event-stream"],["Set-Cookie","private"]],
                    "status":200,"content_type":"text/event-stream","body_kind":"sse",
                    "capture_state":"protocol_done"}})
    return {"case_id":case["case_id"], "status":"CAPTURED","reason":"capture_only",
            "http_status":200,"done_received":True,"json_frame_count":1,
            "first_token_ms":123,"duration_ms":234}


class V2ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot, _ = freeze_cases([CASE, CASE2], source={"type":"csv", "path":"fixture"},
                                        source_stats={}, directory=self.root / "snap")
        self.store = RunStore.create(self.root / "runs", self.snapshot)
        self.addCleanup(self.store.close)
        self.cfg = {"api":{"url":"https://example.invalid",
                             "headers":{"powerkey":"fake-key","signature":"fake-signature"}},
                    "feishu":{"enabled":False},
                    "reports":{"dir":str(self.root / "reports")},
                    "report_outputs":[{"type":"file","enabled":True}]}

    def test_deferred_capture_preserves_original_protocol_failure_for_rejudge(self):
        from test_probe import API, CASE as OLD_CASE, FakeOpener, response_lines
        from lib.v2_rejudge import judge_case
        import copy
        case=copy.deepcopy(OLD_CASE)
        case["expected_keywords"]=["HTTP 200"]
        frame={"choices":[{"delta":{"content":{"data":{"text":"响应"}}}}]}
        capture=io.BytesIO()
        meta={}
        result=probe_v2.probe.run_case(
            case,API,opener=FakeOpener(response_lines(frame,done=False)),
            raw_capture=capture,capture_metadata=meta,defer_assertions=True)
        self.assertEqual(result["status"],"CAPTURED")
        self.assertEqual(result["capture_observation"]["reason"],"done_missing")
        judged=judge_case(case,{"result":result,"capture_metadata":meta},capture.getvalue())
        self.assertEqual(judged["status"],"FAIL")
        self.assertEqual(judged["reason"],"done_missing")

    def test_m2_capture_every_case_and_exact_full_evidence(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture) as requests:
            actual = probe_v2.capture(self.store,self.cfg)
        self.assertEqual(requests.call_count,2)
        self.assertEqual(actual["plan"]["captured"],["1","2"])
        wall = self.store.db.execute("SELECT value FROM run_state WHERE key='capture_wall_ms'").fetchone()
        self.assertIsNotNone(wall)
        self.assertGreaterEqual(int(wall[0]), 0)
        for row in self.store.rows():
            evidence, received = self.store.evidence(row)
            self.assertEqual(received, RAW)
            self.assertEqual(evidence["received_bytes"],len(RAW))
            self.assertEqual(evidence["parsed_events"][0]["json"],FRAME)
            self.assertEqual(evidence["capture_metadata"]["response"]["headers"][1][0],"Set-Cookie")
        import os
        if os.name != "nt":
            self.assertEqual(self.store.db_path.stat().st_mode & 0o777,0o600)

    def test_m2_network_error_preserves_actual_received_partial_bytes(self):
        def mixed(case,api,assertions=None,raw_capture=None,capture_metadata=None,**kwargs):
            if case["case_id"] == "1":
                return fake_capture(case,api,raw_capture=raw_capture,
                                    capture_metadata=capture_metadata,**kwargs)
            raw_capture.write(b"data: partial")
            capture_metadata.update({"response":{"headers":[],"capture_state":"partial_or_unterminated"}})
            return {"case_id":"2", "status":"ERROR", "reason":"timeout",
                    "error_phase":"sse_read","http_status":None}
        with patch.object(probe_v2.probe, "run_case", side_effect=mixed):
            probe_v2.capture(self.store,self.cfg)
        rows=self.store.rows()
        evidence,raw=self.store.evidence(rows[1])
        self.assertEqual(raw,b"data: partial")
        self.assertEqual(evidence["result"]["status"],"ERROR")
        with patch.object(probe_v2.probe, "run_case",side_effect=AssertionError("no calls")):
            judged=probe_v2.rejudge(self.store,self.cfg)
        self.assertEqual([x["status"] for x in judged["results"]],["PASS","ERROR"])

    def test_m4_completed_capture_is_idempotent(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        with patch.object(probe_v2.probe,"run_case",side_effect=AssertionError("NO REPLAY")) as requests:
            result=probe_v2.capture(self.store,self.cfg)
        requests.assert_not_called()
        self.assertEqual(result["plan"]["captured"],["1","2"])

    def test_m3_no_network_rejudge_and_multiple_versioned_evaluations(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        with patch.object(probe_v2.probe, "run_case", side_effect=AssertionError("NETWORK PROHIBITED")):
            first = probe_v2.rejudge(self.store,self.cfg)
            second = probe_v2.rejudge(self.store,self.cfg)
        self.assertNotEqual(first["evaluation_id"],second["evaluation_id"])
        self.assertEqual([x["status"] for x in first["results"]],["PASS","PASS"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM evaluations").fetchone()[0],2)

    def test_m3_changed_rule_yields_fail_without_post(self):
        attempt = self.store.intent("1")
        fake_meta = {}
        raw = io.BytesIO()
        result=fake_capture(CASE, self.cfg["api"], raw_capture=raw,
                            capture_metadata=fake_meta,defer_assertions=True)
        self.store.captured("1",attempt,result,raw.getvalue(),fake_meta)
        row = self.store.rows()[0]
        envelope, body = self.store.evidence(row)
        strict = dict(CASE, expected_keywords=["回复中包含“绝对不存在”"])
        actual = judge_case(strict,envelope,body)
        self.assertEqual(actual["status"],"FAIL")
        self.assertTrue(any("绝对不存在" in v for v in actual["missing_keywords"]))

    def test_m3_rule_override_rejudge_keeps_snapshot_immutable(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        original = self.store.snapshot()["snapshot_sha256"]
        with patch.object(probe_v2.probe, "run_case", side_effect=AssertionError("NETWORK PROHIBITED")):
            newer = probe_v2.rejudge(self.store,self.cfg,
                                     {"2":{"expected_keywords":["回复中包含“绝对不存在”"]}})
        self.assertEqual(newer["results"][0]["status"],"PASS")
        self.assertEqual(newer["results"][1]["status"],"FAIL")
        self.assertEqual(self.store.snapshot()["snapshot_sha256"],original)
        self.assertIsNotNone(newer["rules_override_sha256"])

    def test_m4_api_headers_preflight_before_dispatch(self):
        real = dict(self.cfg)
        real["api"] = {"url":"https://example.invalid", "headers":{"powerkey": "sample"}}
        with patch.object(probe_v2.probe,"run_case") as requests:
            with self.assertRaises(probe_v2.probe.ProbeError):
                probe_v2.capture(self.store, real)
        self.assertEqual(self.store.plan()["pending"],["1","2"])
        requests.assert_not_called()

    def test_m4_prepared_api_change_blocks_business_calls(self):
        # Legacy unpinned test setup is allowed; newly prepared runs pin API profiles.
        extra = RunStore.create(self.root / "extra", self.snapshot, self.cfg["api"])
        self.addCleanup(extra.close)
        wrong = dict(self.cfg, api={"url":"https://other.example.invalid"})
        with patch.object(probe_v2.probe, "run_case") as requests:
            with self.assertRaisesRegex(ValueError,"api_profile_changed"):
                probe_v2.capture(extra,wrong)
        requests.assert_not_called()

    def test_partial_notice_is_explicit_once_and_unknown_not_pass(self):
        self.store.intent("1")
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store, self.cfg, max_cases=1)
        settings = dict(self.cfg,feishu={"enabled":True,"keyword":"API拨测"})
        with patch("lib.feishu_transport.validate_feishu_settings"), patch(
            "lib.feishu_transport.send_card_payload",return_value="sent") as send:
            result=probe_v2.notify_partial(self.store, settings)
            self.assertEqual(result["state"],"SENT")
            with self.assertRaises(Exception):
                probe_v2.notify_partial(self.store, settings)
        self.assertEqual(send.call_count,1)
        card=send.call_args.args[1]
        body=card["card"]["elements"][0]["content"]
        self.assertIn("状态未知 1：#1",body)
        self.assertIn("未完成",body)
        self.assertIn("PASS 1",body)
        self.assertIn("不能作为全量结果",body)
        self.assertIn("已确认失败/异常 ID",body)

    def test_partial_report_does_not_mislabel_unknown(self):
        self.store.intent("1")
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store, self.cfg, max_cases=1)
        with patch.object(probe_v2.probe, "run_case", side_effect=AssertionError("NO POST")):
            doc = probe_v2.partial_report(self.store, self.cfg)
        summary = doc["summary"]
        self.assertEqual(summary["expected_total"], 2)
        self.assertEqual(summary["captured"], 1)
        self.assertEqual(summary["unknown_count"], 1)
        self.assertEqual(summary["pass"], 1)
        self.assertEqual(summary["error"], 0)
        self.assertFalse(summary["full_run_complete"])
        report = json.loads(Path(doc["json_report"]).read_text())
        self.assertEqual(report["unknown_case_ids"], ["1"])
        self.assertNotIn("1", [x["case_id"] for x in report["results"]])
        self.assertIn("非正式最终报告", Path(doc["markdown_report"]).read_text())

    def test_durable_attempt_id_is_request_id_for_future_reconciliation(self):
        from test_probe import FakeOpener, API, CASE as OLD_CASE, response_lines
        from lib.v2_repository import RunStore
        import copy
        saved = self.store.intent("1")
        opener = FakeOpener(response_lines({"choices":[{"delta":{"content":{
            "type":"1","data":{"text":"ok"}}}}]}))
        captured = probe_v2.probe.run_case(
            dict(copy.deepcopy(OLD_CASE),case_id="1",expected_keywords=["HTTP 200"]),
            API, opener=opener, request_id=saved, defer_assertions=True)
        body = json.loads(opener.request.data)
        self.assertEqual(body["reqId"], saved)
        self.assertEqual(captured["reqId"], saved)

    def test_bounded_resume_skips_unknown_and_limits_pending(self):
        # An aborted request stays UNKNOWN, while PENDING can safely be resumed.
        self.store.intent("1")
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture) as requests:
            result=probe_v2.capture(self.store,self.cfg,max_cases=1,max_seconds=60)
        self.assertEqual(requests.call_count,1)
        self.assertEqual(result["processed_this_batch"],1)
        self.assertEqual(result["plan"]["unknown"],["1"])
        self.assertEqual(result["plan"]["captured"],["2"])
        self.assertEqual(result["plan"]["pending"],[])
        self.assertEqual(result["unknown_requests_not_replayed"],1)

    def test_bounded_limit_does_not_dispatch_remaining_cases(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture) as requests:
            result=probe_v2.capture(self.store,self.cfg,max_cases=1,max_seconds=60)
        self.assertEqual(requests.call_count,1)
        self.assertEqual(result["plan"]["captured"],["1"])
        self.assertEqual(result["plan"]["pending"],["2"])
        self.assertFalse(result["plan"]["unknown"])

    def test_m4_crash_after_intent_never_retries_unknown(self):
        self.store.intent("1")
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture) as requests:
            probe_v2.capture(self.store,self.cfg)
        self.assertEqual(requests.call_count,1)
        self.assertEqual(self.store.plan()["unknown"],["1"])
        self.assertEqual(self.store.plan()["captured"],["2"])
        with self.assertRaisesRegex(ValueError,"incomplete"):
            probe_v2.rejudge(self.store,self.cfg)
        with self.assertRaisesRegex(ValueError,"not_pending"):
            self.store.intent("1")

    def test_m4_fsync_error_does_not_replay_or_overwrite(self):
        attempt=self.store.intent("1")
        with patch("lib.v2_repository._atomic_private",side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.captured("1",attempt,{"status":"CAPTURED"},RAW,{})
        self.assertEqual(self.store.plan()["unknown"],["1"])

    def test_relative_run_path_verifies_absolute_evidence(self):
        attempt=self.store.intent("1")
        self.store.captured("1",attempt,{"status":"CAPTURED"},RAW,{})
        import os
        path = Path(os.path.relpath(self.store.directory,Path.cwd()))
        reopened = RunStore(path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.evidence(reopened.rows()[0])[1], RAW)

    def test_m2_corrupted_evidence_rejected(self):
        attempt=self.store.intent("1")
        self.store.captured("1",attempt,{"status":"CAPTURED"},RAW,{})
        row=self.store.rows()[0]
        path=Path(row[5])
        record=json.loads(path.read_text())
        record["raw_hex"]="00"
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,"checksum"):
            self.store.evidence(row)

    def test_m5_report_and_at_most_once_notification(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        probe_v2.rejudge(self.store,self.cfg)
        document, receipts = probe_v2.report(self.store,self.cfg)
        self.assertEqual(document["summary"]["pass"],2)
        self.assertEqual(receipts[0]["status"],"saved")
        cfg=dict(self.cfg,feishu={"enabled":True})
        with patch("lib.feishu_notification_router.validate_probe_notification"), patch("lib.feishu_notification_router.prepare_probe_cards",return_value=[{}]), patch("lib.feishu_notification_router.send_probe_notice",return_value="sent") as send:
            got=probe_v2.notify(self.store,cfg,document)
            self.assertEqual(got["state"],"SENT")
            with self.assertRaises(Exception):
                probe_v2.notify(self.store,cfg,document)
            self.assertEqual(send.call_count,1)

    def test_m5_tampered_evaluation_refuses_report(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        eva=probe_v2.rejudge(self.store,self.cfg)
        source=self.store.db.execute("SELECT output_file FROM evaluations WHERE evaluation_id=?",
                                     (eva["evaluation_id"],)).fetchone()[0]
        path=Path(source)
        data=json.loads(path.read_text())
        data["results"][0]["status"]="FAIL"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,"evaluation_digest_mismatch"):
            probe_v2.report(self.store,self.cfg,eva["evaluation_id"])

    def test_m5_known_rejection_records_failed_no_retry(self):
        from lib.feishu import FeishuDeliveryError
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        probe_v2.rejudge(self.store,self.cfg)
        doc,_=probe_v2.report(self.store,self.cfg)
        cfg=dict(self.cfg,feishu={"enabled":True})
        with patch("lib.feishu_notification_router.validate_probe_notification"), patch("lib.feishu_notification_router.prepare_probe_cards",return_value=[{}]), patch("lib.feishu_notification_router.send_probe_notice",side_effect=FeishuDeliveryError("feishu_rejected_19007")) as send:
            result=probe_v2.notify(self.store,cfg,doc)
        self.assertEqual(result["state"],"FAILED")
        self.assertEqual(send.call_count,1)
        self.assertEqual(self.store.db.execute("SELECT state FROM notification").fetchone()[0],"FAILED")

    def test_m5_partial_rejection_preserves_confirmed_page_count(self):
        from lib.feishu_notification_router import PagedDeliveryError
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        probe_v2.rejudge(self.store,self.cfg)
        doc,_=probe_v2.report(self.store,self.cfg)
        cfg=dict(self.cfg,feishu={"enabled":True})
        failure=PagedDeliveryError("feishu_rejected_19007",3,1,2)
        with patch("lib.feishu_notification_router.validate_probe_notification"), patch("lib.feishu_notification_router.prepare_probe_cards",return_value=[{}, {}, {}]), patch("lib.feishu_notification_router.send_probe_notice",side_effect=failure) as send:
            receipt=probe_v2.notify(self.store,cfg,doc)
        self.assertEqual(receipt["state"],"PARTIAL")
        self.assertEqual(send.call_count,1)
        details=json.loads(self.store.db.execute("SELECT details FROM notification").fetchone()[0])
        self.assertEqual(details["cards_confirmed"],1)
        self.assertEqual(details["failed_page"],2)
        self.assertFalse(details["automatic_retry"])

    def test_m5_failed_preflight_creates_no_delivery_intent(self):
        from lib.feishu import FeishuDeliveryError
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        probe_v2.rejudge(self.store,self.cfg)
        doc,_=probe_v2.report(self.store,self.cfg)
        cfg=dict(self.cfg,feishu={"enabled":True})
        with patch("lib.feishu_notification_router.validate_probe_notification"), patch("lib.feishu_notification_router.prepare_probe_cards",side_effect=FeishuDeliveryError("card_capacity_exceeded")), patch("lib.feishu_notification_router.send_probe_notice") as send:
            with self.assertRaises(FeishuDeliveryError):
                probe_v2.notify(self.store,cfg,doc)
        send.assert_not_called()
        self.assertIsNone(self.store.db.execute("SELECT id FROM notification").fetchone())

    def test_m5_unknown_delivery_not_repeated(self):
        with patch.object(probe_v2.probe, "run_case", side_effect=fake_capture):
            probe_v2.capture(self.store,self.cfg)
        probe_v2.rejudge(self.store,self.cfg)
        doc,_=probe_v2.report(self.store,self.cfg)
        cfg=dict(self.cfg,feishu={"enabled":True})
        with patch("lib.feishu_notification_router.validate_probe_notification"), patch("lib.feishu_notification_router.prepare_probe_cards",return_value=[{}]), patch("lib.feishu_notification_router.send_probe_notice",side_effect=TimeoutError):
            out=probe_v2.notify(self.store,cfg,doc)
            self.assertEqual(out["state"],"DELIVERY_UNKNOWN")
            with self.assertRaises(Exception):
                probe_v2.notify(self.store,cfg,doc)


if __name__=="__main__":
    unittest.main()