"""v1.5 monitoring report: structured statistics, details, slow first-frame, privacy."""
import io
import json
import math
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.reporting import build_analysis, slow_threshold
from lib import feishu

CASES = [
    {"case_id": "001", "scene": "电视直播", "content": "打开湖南卫视", "deviceId": "DEV-SECRET"},
    {"case_id": "002", "scene": "视频点播", "content": "帮我播放<三国演义>|全集\n第二行", "deviceId": "DEV-SECRET"},
    {"case_id": "003", "scene": "业务检索", "content": "查询周末新片", "deviceId": "DEV-SECRET"},
]
RESULTS = [
    {"case_id": "001", "status": "PASS", "reason": "ok",
     "duration_ms": 1500, "first_event_ms": 100, "first_token_ms": 240,
     "matched_keywords": [], "missing_keywords": [], "forbidden_found": []},
    {"case_id": "002", "status": "FAIL", "reason": "assertions_failed",
     "duration_ms": 4000, "first_event_ms": 800, "first_token_ms": 3000,
     "matched_keywords": [], "missing_keywords": [], "forbidden_found": []},
    {"case_id": "003", "status": "ERROR", "reason": "timeout",
     "duration_ms": 8000, "first_event_ms": None, "first_token_ms": None,
     "matched_keywords": [], "missing_keywords": [], "forbidden_found": []},
]

class MonitoringReportTests(unittest.TestCase):
    def test_monitor_summary_values(self):
        sm, items, failures, slow = build_analysis(RESULTS, CASES, 3000, 13560)
        self.assertEqual(sm["total"], 3)
        self.assertEqual(sm["pass"], 1)
        self.assertEqual(sm["fail"], 1)
        self.assertEqual(sm["error"], 1)
        self.assertEqual(sm["failed_total"], 2)
        self.assertEqual(sm["pass_rate"], 33.33)
        self.assertEqual(sm["average_latency_ms"], 1620.0)
        self.assertEqual(sm["latency_metric"], "first_token_ms")
        self.assertEqual(sm["first_token_sample_count"], 2)
        self.assertEqual(sm["average_latency_s"], 1.62)
        self.assertEqual(sm["fastest_s"], 0.24)
        self.assertEqual(sm["slowest_s"], 3.0)
        self.assertEqual(sm["total_duration_s"], 13.5)
        self.assertEqual(sm["wall_duration_s"], 13.56)
        self.assertEqual(sm["slow_token_threshold_s"], 3.0)
        self.assertEqual(sm["total_duration_ms"], 13500)
        self.assertEqual(sm["wall_duration_ms"], 13560)
        self.assertEqual(sm["fastest_ms"], 240)
        self.assertEqual(sm["slowest_ms"], 3000)
        self.assertEqual(sm["slow_token_count"], 1)
        self.assertEqual([x["scene"] for x in items], [c["scene"] for c in CASES])
        self.assertEqual(items[0]["duration_ms"], 1500)
        self.assertEqual(items[0]["latency_ms"], 240)
        self.assertIsNone(items[2]["latency_ms"])
        self.assertEqual(items[1]["first_token_ms"], 3000)
        self.assertEqual([x["case_id"] for x in failures], ["002", "003"])
        self.assertEqual(failures[0]["content"], CASES[1]["content"])
        self.assertEqual(failures[0]["scene"], "视频点播")
        self.assertEqual(failures[1]["failure_brief"], "请求超时")
        self.assertEqual(failures[0]["latency_ms"], 3000)
        self.assertIsNone(failures[1]["latency_ms"])
        self.assertEqual([x["case_id"] for x in slow], ["002"])
        self.assertNotIn("content", items[0])
        self.assertNotIn("deviceId", json.dumps(items, ensure_ascii=False))

    def test_stage_diagnostic_fields_in_json_markdown_and_public_projection(self):
        from lib.report_document import build_report_document, render_markdown, public_projection
        result={"case_id":"X7","status":"ERROR","reason":"timeout",
                "http_status":None,"done_received":False,"first_token_ms":None,
                "duration_ms":182,"error_phase":"response_headers",
                "timeout_kind":"response_header_timeout",
                "exception_type":"PhaseTimeout",
                "exception_chain":["PhaseTimeout","socket.timeout"],
                "exception_errno":None,
                "http_headers_received":False,
                "response_bytes_received":0,
                "stage_timings_ms":{"dns_ms":1,"tcp_connect_ms":2,"response_headers_ms":179}}
        case={"case_id":"X7","scene":"业务调用","content":"PRIVATE_MESSAGE",
              "deviceId":"PRIVATE_DEVICE","expected_keywords":["abc"]}
        doc=build_report_document("csv",{"reports":{}},[result],[case],182)
        entry=doc["results"][0]
        failed=doc["failure_cases"][0]
        self.assertEqual(entry["error_phase"],"response_headers")
        self.assertFalse(failed["http_headers_received"])
        self.assertEqual(failed["response_bytes_received"],0)
        self.assertEqual(failed["exception_chain"][0],"PhaseTimeout")
        md=render_markdown(doc)
        self.assertIn("尚未收到 HTTP 响应头或响应体",md)
        self.assertIn("等待 HTTP 响应头超时",md)
        self.assertNotIn("response_headers",md)
        self.assertNotIn("PhaseTimeout",md)
        # Remote reporting gets only approved phase codes, not private text.
        shared=json.dumps(public_projection(doc),ensure_ascii=False)
        self.assertIn("response_headers",shared)
        self.assertNotIn("PRIVATE_MESSAGE",shared)
        self.assertNotIn("PRIVATE_DEVICE",shared)

    def test_failure_brief_with_counts_without_raw_keywords(self):
        from lib.reporting import failure_brief
        brief = failure_brief({"reason":"assertions_failed",
                               "missing_keywords":["PRIVATE_ONE","PRIVATE_TWO"],
                               "forbidden_found":["PRIVATE_THREE"]})
        self.assertIn("缺失 2 项", brief)
        self.assertIn("命中 1 项", brief)
        self.assertNotIn("PRIVATE", brief)
        self.assertIn("HTTP 503", failure_brief({
            "reason":"unexpected_http_status", "http_status":503}))

    def test_missing_duration_and_no_first_token(self):
        rows = [{"case_id":"1", "status":"ERROR", "reason":"network_error",
                 "duration_ms":None, "first_token_ms":None},
                {"case_id":"2", "status":"PASS", "reason":"ok",
                 "duration_ms":0, "first_token_ms":0}]
        s, _, failed, slow = build_analysis(rows, [], 1, None)
        self.assertEqual(s["average_latency_ms"], 0)
        self.assertEqual(s["first_token_sample_count"], 1)
        self.assertEqual(s["total_duration_ms"], 0)
        self.assertEqual(s["fastest_ms"], 0)
        self.assertEqual(s["slowest_ms"], 0)
        self.assertEqual(s["wall_duration_ms"], None)
        self.assertEqual(s["pass_rate"], 50)
        self.assertEqual(len(failed), 1)
        self.assertEqual(slow, [])

    def test_no_business_frame_has_no_primary_latency_even_with_long_total_time(self):
        rows = [{"case_id":"T1","status":"ERROR","reason":"timeout",
                 "duration_ms":50000,"first_token_ms":None}]
        summary, entries, failures, slow = build_analysis(rows, [], 3000, 50010)
        self.assertEqual(summary["first_token_sample_count"], 0)
        self.assertIsNone(summary["average_latency_ms"])
        self.assertIsNone(summary["fastest_ms"])
        self.assertIsNone(summary["slowest_ms"])
        self.assertEqual(summary["total_duration_ms"], 50000)
        self.assertIsNone(entries[0]["latency_ms"])
        self.assertIsNone(failures[0]["latency_ms"])
        self.assertEqual(failures[0]["duration_ms"], 50000)
        self.assertEqual(slow, [])
        card = feishu._card(summary, entries, "tencent", "FAIL", "拨测",
                            failure_cases=failures)
        message = card["card"]["elements"][0]["content"]
        self.assertIn("首业务帧耗时", message)
        self.assertIn("首帧 - / 总 50.000s", message)
        self.assertNotIn("首帧 0.000s", message)

    def test_seconds_helper_units_and_none(self):
        from lib.reporting import seconds, display_seconds, public_summary
        self.assertEqual(display_seconds(1615), "1.615s")
        self.assertEqual(display_seconds(1), "0.001s")
        self.assertEqual(display_seconds(0), "0.000s")
        self.assertEqual(display_seconds(29144), "29.144s")
        self.assertEqual(display_seconds(None), "-")
        self.assertIsNone(seconds(float("nan")))
        self.assertIsNone(seconds(-1))
        self.assertEqual(public_summary({"total":1,"pass":1,"average_latency_ms":1615,
                "total_duration_ms":29144,"slow_token_threshold_ms":3000})["average_latency_s"],1.615)
        self.assertNotIn("average_latency_ms",public_summary(
            {"average_latency_ms":1615}))

    def test_empty_summary_and_invalid_threshold(self):
        s, _, _, _ = build_analysis([], [], 3000)
        self.assertEqual(s["pass_rate"], 0)
        self.assertIsNone(s["average_latency_ms"])
        self.assertIsNone(s["fastest_ms"])
        for val in (0, -1, "three", None, True, float("nan"), float("inf"), 4000000):
            with self.subTest(val=val), self.assertRaises(ValueError):
                slow_threshold({"slow_token_threshold_ms":val})
        self.assertEqual(slow_threshold({}), 3000)
        self.assertEqual(slow_threshold({"slow_token_threshold_ms":2500}), 2500)

    def test_slow_token_sort_descending(self):
        rows = [{"case_id":"A","status":"PASS","first_token_ms":3600,"duration_ms":4000},
                {"case_id":"B","status":"PASS","first_token_ms":6000,"duration_ms":7000},
                {"case_id":"C","status":"PASS","first_token_ms":2999,"duration_ms":8000}]
        _, _, _, slow = build_analysis(rows, [], 3000)
        self.assertEqual([x["case_id"] for x in slow], ["B", "A"])

    def test_full_json_markdown_report_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            sm, jf, mf = probe.write_reports(
                "tencent", {"reports":{"dir":d, "slow_token_threshold_ms":3000}},
                RESULTS, CASES, 13600)
            self.assertEqual(sm["pass_rate"], 33.33)
            # On Windows os.chmod reports read-only bits (typically 0666),
            # not POSIX ACLs. Don't confuse this with failed report output.
            if os.name != "nt":
                self.assertEqual(jf.stat().st_mode & 0o777, 0o600)
                self.assertEqual(mf.stat().st_mode & 0o777, 0o600)
            else:
                self.assertTrue(jf.is_file())
                self.assertTrue(mf.is_file())
            doc = json.loads(jf.read_text(encoding="utf-8"))
            self.assertEqual(doc["version"], 6)
            self.assertEqual(doc["summary"]["total_duration_ms"], 13500)
            self.assertEqual(doc["results"][1]["scene"], "视频点播")
            self.assertEqual(doc["summary"]["average_latency_ms"], 1620.0)
            self.assertEqual(doc["results"][0]["latency_ms"], 240)
            self.assertEqual(doc["results"][0]["duration_ms"], 1500)
            self.assertEqual(doc["results"][0]["latency_s"], 0.24)
            self.assertEqual(doc["results"][0]["duration_s"], 1.5)
            self.assertIsNone(doc["failure_cases"][1]["latency_s"])
            self.assertEqual(doc["slow_token_cases"][0]["first_token_s"], 3.0)
            self.assertIsNone(doc["failure_cases"][1]["first_token_ms"])
            self.assertNotIn("content", doc["results"][1])
            self.assertEqual(doc["failure_cases"][0]["content"], CASES[1]["content"])
            self.assertEqual(doc["slow_token_cases"][0]["first_token_ms"], 3000)
            md = mf.read_text(encoding="utf-8")
            for text in ("总计 3 条", "通过 1 条", "失败 1 条", "异常 1 条",
                         "场景：视频点播", "语料", "失败判断原因",
                         "完整 SSE 响应参数", "请求超时"):
                self.assertIn(text, md)
            self.assertIn("帮我播放<三国演义>|全集", md)
            self.assertNotIn("DEV-SECRET", md)
            self.assertNotIn("总监控数", md)
            self.assertNotIn("慢 Token 用例", md)
            self.assertNotIn("序号 | 场景", md)
            self.assertNotIn("## 每条用例结果", md)
            self.assertNotIn("| 001 | 电视直播 | PASS | 0.240s | 1.500s |", md)
            self.assertNotIn("| 003 | 业务检索 | ERROR | - | 8.000s |", md)

    def test_first_business_frame_timing_and_invalid_frame(self):
        from test_probe import API, CASE, SUCCESS, FakeOpener, response_lines
        success = probe.run_case(CASE, API, opener=FakeOpener(response_lines(SUCCESS)))
        self.assertIsInstance(success["first_token_ms"], int)
        self.assertIsInstance(success["first_event_ms"], int)
        self.assertGreaterEqual(success["first_token_ms"], success["first_event_ms"])
        bad = probe.run_case(CASE, API,
                             opener=FakeOpener([b"data: {not_json}\n\n"]))
        self.assertIsNone(bad["first_token_ms"])
        self.assertEqual(bad["status"], "FAIL")

    def test_feishu_stats_failures_slow_and_default_private(self):
        s, entries, failures, slow = build_analysis(RESULTS, CASES, 3000, 13600)
        sanitized = [{k:v for k,v in x.items() if k!="content"} for x in failures]
        card = feishu._card(s,entries,"tencent","FAIL","API拨测",
                            failure_cases=sanitized, slow_token_cases=slow)
        msg = card["card"]["elements"][0]["content"]
        for val in ("33.33", "1.620s", "13.500s", "慢 Token",
                    "视频点播", "请求超时", "3.000s", "视频点播"):
            self.assertIn(val,msg)
        self.assertNotIn(CASES[1]["content"],msg)
        self.assertNotIn("DEV-SECRET",msg)
        self.assertIn("首业务帧耗时",msg)
        self.assertNotIn("首帧 0.240s / 总 1.500s",msg)
        self.assertNotIn("逐条状态",msg)
        self.assertIn("首帧 - / 总 8.000s",msg)

    def test_feishu_never_includes_failure_utterance_even_with_legacy_opt_in(self):
        from test_feishu_notification import WEBHOOK, Opener
        s, entries, failures, slow = build_analysis(RESULTS, CASES, 3000, 13600)
        base={"enabled":True,"webhook_url":WEBHOOK,
              "secret":"TESTSECRET","keyword":"API拨测"}
        opener=Opener()
        feishu.send_notification(base,s,entries,"tencent","FAIL",opener=opener,
                                 failure_cases=failures,slow_token_cases=slow)
        self.assertNotIn("帮我播放",opener.requests[0].data.decode("utf-8"))
        enabled=Opener()
        feishu.send_notification(dict(base,include_failure_content=True),
                                 s,entries,"tencent","FAIL",opener=enabled,
                                 failure_cases=failures,slow_token_cases=slow)
        self.assertNotIn("帮我播放",enabled.requests[0].data.decode("utf-8"))

    def test_end_to_end_cli_csv_report_and_notification_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cases=root/"cases.json"
            data=[
              {"case_id":"001","scene":"电视直播","content":"看中央一台",
               "deviceId":"SECRETDEVICE","expected_keywords":"ok"},
              {"case_id":"002","scene":"视频点播","content":"想看三国演义",
               "deviceId":"SECRETDEVICE","expected_keywords":"ok"}]
            cases.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            cfg=root/"cfg.json"
            cfg.write_text(json.dumps({
                "api":{"url":"https://test.invalid","headers":{}},
                "source":{"type":"json","path":str(cases)},
                "reports":{"dir":str(root/"reports"),"slow_token_threshold_ms":2000},
                "feishu":{"enabled":True,"keyword":"API拨测",
                          "WEBHOOK_URL":"https://open.feishu.cn/open-apis/bot/v2/hook/FAKE_NO_NETWORK",
                          "SECRET":"FAKE_NO_NETWORK"}
            }),encoding="utf-8")
            def fake_run(case, api, assertions=None, raw_capture=None,
                         capture_metadata=None):
                if raw_capture is not None:
                    raw_capture.write(b"data: simulated FAIL raw SSE\\n\\n")
                which=case["case_id"]
                return {"case_id":which,"status":"PASS" if which=="001" else "FAIL",
                        "reason":"ok" if which=="001" else "assertions_failed",
                        "duration_ms":500 if which=="001" else 4200,
                        "first_token_ms":400 if which=="001" else 3200,
                        "first_event_ms":350,
                        "matched_keywords":[],"missing_keywords":[],
                        "forbidden_found":[]}
            stdout=io.StringIO()
            with patch.object(probe,"run_case",side_effect=fake_run), patch(
                "lib.feishu_notification_router.send_probe_notice",return_value="sent") as notice, redirect_stdout(stdout),redirect_stderr(io.StringIO()):
                rc=probe._legacy_main(["--config",str(cfg)])
            self.assertEqual(rc,1)
            self.assertEqual(notice.call_count,1)
            n=notice.call_args
            self.assertEqual(n.kwargs["failure_cases"][0]["scene"],"视频点播")
            self.assertNotIn("content",n.kwargs["failure_cases"][0])
            self.assertEqual(n.kwargs["slow_token_cases"][0]["case_id"],"002")
            report_path=next((root/"reports").glob("*.json"))
            output=json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(output["summary"]["pass_rate"],50)
            self.assertEqual(output["summary"]["average_latency_ms"],1800)
            self.assertEqual(output["summary"]["first_token_sample_count"],2)
            self.assertEqual(output["summary"]["total_duration_ms"],4700)
            self.assertEqual(output["failure_cases"][0]["content"],"想看三国演义")
            self.assertEqual(output["slow_token_cases"][0]["first_token_ms"],3200)
            self.assertEqual(output["results"][0]["scene"],"电视直播")
            self.assertEqual(output["results"][1]["duration_ms"],4200)
            self.assertEqual(output["failure_cases"][0]["device_id"],"SECRETDEVICE")
            self.assertNotIn("SECRETDEVICE",json.dumps(n.kwargs["failure_cases"],ensure_ascii=False))
            self.assertNotIn("想看三国演义",stdout.getvalue())

if __name__=="__main__":
    unittest.main()