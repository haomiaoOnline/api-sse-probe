import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe

class FakeResponse:
    status = 200
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    fp = None
    def __init__(self, lines):
        self.lines = iter(part for chunk in lines for part in chunk.splitlines(keepends=True))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def readline(self, limit):
        try:
            return next(self.lines)
        except StopIteration:
            return b""

class FakeOpener:
    def __init__(self, lines):
        self.lines = lines
        self.request = None
    def open(self, req, timeout):
        self.request = req
        return FakeResponse(self.lines)

HEADERS = {"powerkey": "redacted-test", "signature": "redacted-test"}
API = {"url": "https://test.example/chat", "headers": HEADERS,
       "require_done": True}
CASE = {"case_id": "TV001", "content": "打开湖南卫视直播", "deviceId": "device",
        "expected_keywords": ["视频技能", "VIDEO30BFF29645B2B2E2EB6C44AE7"],
        "forbidden_keywords": [], "match_mode": "all", "endpoint": {}, "session": {}}

def response_lines(*frames, done=True):
    lines = []
    for frame in frames:
        payload = json.dumps(frame, ensure_ascii=False).encode("utf-8")
        lines.append(b"data: " + payload + b"\n\n")
    if done:
        lines.append(b"data: [DONE]\n\n")
    return lines

SUCCESS = {"choices": [{"delta": {"content": {
    "type": "3", "data": {"jsonHeader": '{"skillId":"VIDEO30BFF29645B2B2E2EB6C44AE7"}',
                         "jsonBody": '{"params":{"videoStr":"视频技能"}}'}}}}]}
OTHER = {"choices": [{"delta": {"content": {"type": "1", "data": {"text": "hello"}}}}]}

class ProbeTests(unittest.TestCase):
    def test_normalized_csv(self):
        cases = probe.read_cases({"type": "csv", "path": "assets/sample_cases.csv"})
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_id"], "TV001")
        self.assertEqual(len(cases[0]["expected_keywords"]), 2)

    def test_resolve_literal_headers(self):
        h = probe.resolve_headers(API)
        self.assertEqual(h["signature"], "redacted-test")
        self.assertEqual(len(h["timestamp"]), 13)
        self.assertTrue(h["timestamp"].isdigit())
        self.assertNotIn("timestamp", API["headers"])

    def test_timestamp_unix_milliseconds_and_legacy_override(self):
        api = {"headers": dict(HEADERS, timestamp="stale-hardcoded-value")}
        with patch.object(probe.time, "time_ns", return_value=1757493494408123456):
            resolved = probe.resolve_headers(api)
        self.assertEqual(resolved["timestamp"], "1757493494408")
        self.assertEqual(api["headers"]["timestamp"], "stale-hardcoded-value")

    def test_timestamp_new_for_each_post(self):
        with patch.object(probe.time, "time_ns", side_effect=[
            1757493494408123456, 1757493494508123456
        ]):
            first, second = (FakeOpener(response_lines(SUCCESS)),
                             FakeOpener(response_lines(SUCCESS)))
            self.assertEqual(probe.run_case(CASE, API, opener=first)["status"], "PASS")
            self.assertEqual(probe.run_case(CASE, API, opener=second)["status"], "PASS")
        self.assertEqual(first.request.get_header("Timestamp"), "1757493494408")
        self.assertEqual(second.request.get_header("Timestamp"), "1757493494508")
        self.assertEqual(first.request.get_header("Powerkey"), "redacted-test")
        self.assertEqual(second.request.get_header("Signature"), "redacted-test")
        self.assertNotIn("timestamp", API["headers"])

    def test_public_config_does_not_contain_timestamp(self):
        cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertNotIn("timestamp", cfg["api"]["headers"])


    def test_missing_headers_fail(self):
        with self.assertRaises(probe.ProbeError):
            probe.resolve_headers({"headers": dict(HEADERS, signature="")})

    def test_env_secret(self):
        with patch.dict(os.environ, {"MY_PROBE_SECRET": "secrettest"}):
            cfg = {"headers": dict(HEADERS, signature={"env": "MY_PROBE_SECRET"})}
            self.assertEqual(probe.resolve_headers(cfg)["signature"], "secrettest")

    def test_sse_multiline(self):
        events = list(probe.sse_events([b":ping\n", b"event: message\n", b"data: one\n", b"data: two\n", b"\n"]))
        self.assertEqual(events, [("message", "one\ntwo")])

    def test_sse_eof_flush(self):
        self.assertEqual(list(probe.sse_events([b"data: [DONE]\n"])), [("message", "[DONE]")])

    def test_chunked_sse_read1_matches_streaming_installed_behavior(self):
        # Regression: source and deployed Skill must handle fragmented read1
        # chunks without waiting for a full remote close.
        chunks=b"".join(response_lines(SUCCESS))
        pieces=iter(chunks[i:i+7] for i in range(0,len(chunks),7))
        class ChunkResponse:
            status=200
            headers={"Content-Type":"text/event-stream; charset=utf-8"}
            fp=None
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read1(self,n):return next(pieces,b"")
            def readline(self,n):raise AssertionError("expected read1 branch")
        class ChunkOpener:
            def open(self,req,timeout):return ChunkResponse()
        result=probe.run_case(CASE,API,opener=ChunkOpener())
        self.assertEqual(result["status"],"PASS")
        self.assertTrue(result["done_received"])
        self.assertGreaterEqual(result["json_frame_count"],1)


    def test_success(self):
        opener = FakeOpener(response_lines(SUCCESS))
        result = probe.run_case(CASE, API, opener=opener)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["done_received"])
        payload = json.loads(opener.request.data)
        self.assertEqual(payload["messages"][0]["content"], CASE["content"])
        self.assertEqual(payload["deviceId"], CASE["deviceId"])
        self.assertTrue(payload["reqId"])
        self.assertTrue(payload["stream"])
        self.assertNotIn("redacted-test", json.dumps(result))

    def test_keyword_missing(self):
        r = probe.run_case(CASE, API, opener=FakeOpener(response_lines(OTHER)))
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(r["reason"], "assertions_failed")

    def test_no_done(self):
        r = probe.run_case(CASE, API, opener=FakeOpener(response_lines(SUCCESS, done=False)))
        self.assertEqual(r["reason"], "done_missing")

    def test_invalid_json(self):
        r = probe.run_case(CASE, API, opener=FakeOpener([b"data: {oops}\n\n", b"data: [DONE]\n\n"]))
        self.assertEqual(r["reason"], "invalid_sse_json")

    def test_upstream_error(self):
        r = probe.run_case(CASE, API, opener=FakeOpener(response_lines({"error": "bad"})))
        self.assertEqual(r["reason"], "upstream_business_error")

    def test_sse_error_event(self):
        r = probe.run_case(CASE, API, opener=FakeOpener([b"event: error\n", b"data: something\n\n"]))
        self.assertEqual(r["reason"], "sse_error_event")

    def test_report_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sm, jf, mf = probe.write_reports("csv", {"reports": {"dir": d}}, [{
                "case_id":"TV001", "status":"PASS", "reason":"ok", "http_status":200,
                "json_frame_count":1,"done_received":True, "duration_ms":10,
                "matched_keywords":["视频技能"],"missing_keywords":[],"forbidden_found":[]
            }])
            self.assertEqual(sm["pass"], 1)
            self.assertTrue(jf.is_file())
            self.assertTrue(mf.is_file())

    def test_no_http_remote(self):
        r = probe.run_case(CASE, dict(API, url="http://example.org/test"), opener=FakeOpener([]))
        self.assertEqual(r["reason"], "configuration_error")

    def test_tencent_missing_credentials(self):
        from lib.tencent import TencentError, read_values
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TencentError):
                read_values({"file_id":"file1","sheet_id":"sheet1"})

if __name__ == "__main__":
    unittest.main()