"""Feishu payload, signing, and CLI delivery semantics; no external network."""
import base64
import hashlib
import hmac
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
from lib import feishu

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook-id"
CFG = {"enabled": True, "webhook_url": WEBHOOK,
       "secret": "DUMMY_PRIVATE_SECRET", "keyword": "灵犀拨测"}
SUMMARY = {"total": 2, "pass": 1, "fail": 1, "error": 0}
RESULTS = [
    {"case_id": "TC01", "status": "PASS", "reason": "ok", "duration_ms": 183,
     "deviceId":"device-PRIVATE", "content":"private question"},
    {"case_id": "TC02", "status": "FAIL", "reason": "assertions_failed", "duration_ms": 522},
]

class Response:
    status = 200
    def __init__(self, body=b'{"code":0,"msg":"success"}'):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.body[:size]

class Opener:
    def __init__(self, body=b'{"code":0,"msg":"success"}'):
        self.body = body
        self.requests = []
    def open(self, req, timeout):
        self.requests.append(req)
        return Response(self.body)

class FeishuTests(unittest.TestCase):
    def test_official_sign_vector(self):
        timestamp = "1599360473"
        secret = "hello"
        expected = base64.b64encode(hmac.new(
            (timestamp + "\n" + secret).encode(), b"", hashlib.sha256
        ).digest()).decode()
        self.assertEqual(feishu.generate_sign(secret, timestamp), expected)

    def test_success_body_seconds_signature_and_keyword(self):
        opener = Opener()
        with patch.object(feishu.time, "time", return_value=1790000000):
            result = feishu.send_notification(CFG, SUMMARY, RESULTS, "tencent", "FAIL",
                                              opener=opener)
        self.assertEqual(result, "sent")
        payload = json.loads(opener.requests[0].data)
        self.assertEqual(payload["timestamp"], "1790000000")
        self.assertEqual(len(payload["timestamp"]), 10)
        self.assertEqual(payload["sign"], feishu.generate_sign("DUMMY_PRIVATE_SECRET",
                                                                "1790000000"))
        self.assertEqual(payload["msg_type"], "interactive")
        content = payload["card"]["elements"][0]["content"]
        self.assertIn("灵犀拨测", content)
        self.assertNotIn("TC01", content)
        self.assertNotIn("逐条状态", content)
        self.assertIn("TC02", content)
        self.assertIn("assertions_failed", content)
        self.assertIn("0.522s", content)
        self.assertNotIn("device-PRIVATE", content)
        self.assertNotIn("private question", content)
        self.assertNotIn("DUMMY_PRIVATE_SECRET", json.dumps(payload))
        self.assertNotIn("/bot/v2/hook", json.dumps(payload))

    def test_uppercase_config_webhook_secret(self):
        settings = {"enabled": True, "WEBHOOK_URL": WEBHOOK,
                    "SECRET": "DUMMY_PRIVATE_SECRET", "keyword": "API拨测"}
        opener = Opener()
        self.assertEqual(feishu.send_notification(
            settings, SUMMARY, RESULTS, "csv", "FAIL", opener=opener), "sent")
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["sign"], feishu.generate_sign(
            "DUMMY_PRIVATE_SECRET", body["timestamp"]))

    def test_all_success_card(self):
        payload = feishu._card({"total":1,"pass":1,"fail":0,"error":0},
                               RESULTS[:1], "csv", "PASS", "监控")
        self.assertEqual(payload["card"]["header"]["template"], "green")

    def test_error_when_document_read_fails(self):
        payload = feishu._card({"total":0,"pass":0,"fail":0,"error":0},
                               [], "tencent", "ERROR", "测试")
        self.assertIn("配置、用例读取", payload["card"]["elements"][0]["content"])

    def test_rejected_with_code(self):
        with self.assertRaisesRegex(feishu.FeishuDeliveryError, "feishu_rejected_19007"):
            feishu.send_notification(CFG, SUMMARY, RESULTS, "csv", "FAIL",
                                     opener=Opener(b'{"code":19007,"msg":"Bot Not Enabled"}'))

    def test_rejected_without_code(self):
        with self.assertRaisesRegex(feishu.FeishuDeliveryError, "feishu_rejected_unknown"):
            feishu.send_notification(CFG, SUMMARY, RESULTS, "csv", "PASS",
                                     opener=Opener(b'{"msg":"ok"}'))

    def test_missing_webhook(self):
        with self.assertRaisesRegex(feishu.FeishuDeliveryError, "webhook_url_missing"):
            feishu.send_notification(dict(CFG, webhook_url=""), SUMMARY, RESULTS,
                                     "csv", "FAIL")

    def test_missing_secret(self):
        with self.assertRaisesRegex(feishu.FeishuDeliveryError, "secret_missing"):
            feishu.send_notification(dict(CFG, secret=""), SUMMARY, RESULTS,
                                     "csv", "FAIL")

    def test_reject_foreign_url(self):
        for url in (
            "http://open.feishu.cn/open-apis/bot/v2/hook/xyz",
            "https://open.feishu.cn.evil.org/open-apis/bot/v2/hook/xyz",
            "https://open.feishu.cn:8888/open-apis/bot/v2/hook/xyz",
            "https://open.feishu.cn/open-apis/bot/v2/hook/xyz?q=token",
            "https://open.feishu.cn/open-apis/bot/v2/hook/xyz#data",
            "https://evil.example/hook/secret",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                    feishu.FeishuDeliveryError, "invalid_webhook_url"):
                feishu.send_notification(dict(CFG, webhook_url=url), SUMMARY, RESULTS,
                                         "csv", "PASS")

    def test_explicit_disabled(self):
        self.assertEqual(feishu.send_notification({"enabled":False}, SUMMARY, RESULTS,
                                                  "csv", "PASS"), "disabled")

    def test_many_rows_truncation(self):
        rows = [{"case_id":"TC-" + str(i),"status":"FAIL",
                 "reason":"assertions_failed","duration_ms":i} for i in range(999)]
        payload = feishu._card({"total":999,"pass":0,"fail":999,"error":0},
                               rows, "tencent", "FAIL", "测试")
        b = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(b), 18000)
        content = payload["card"]["elements"][0]["content"]
        self.assertIn("999",content)
        self.assertIn("显示 ",content)

    def _config_file(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "cfg.json"
        path.write_text(json.dumps({
            "api":{"url":"https://test.com","headers":{}},
            "source":{"type":"csv","path":"assets/sample_cases.csv"},
            "feishu":CFG
        }), encoding="utf-8")
        return tmp, path

    def test_cli_one_notice_after_fail(self):
        tmp,path = self._config_file()
        try:
            with (patch.object(probe, "run_case", return_value={
                    "case_id":"TV001","status":"FAIL","reason":"assertions_failed",
                    "duration_ms":30,"http_status":200,"json_frame_count":1,
                    "done_received":True,"matched_keywords":[],
                    "missing_keywords":[],"forbidden_found":[]}),
                  patch.object(probe, "write_reports", return_value=(
                      {"total":1,"pass":0,"fail":1,"error":0},Path("a"),Path("b"))),
                  patch("lib.feishu_notification_router.send_probe_notice",return_value="sent") as send,
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                code = probe._legacy_main(["--config",str(path)])
            self.assertEqual(code, 1)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[4], "FAIL")
        finally:
            tmp.cleanup()

    def test_cli_one_notice_when_source_fails(self):
        tmp,path = self._config_file()
        try:
            with (patch.object(probe, "read_cases", side_effect=probe.ProbeError("source failure")),
                  patch("lib.feishu_notification_router.send_probe_notice",return_value="sent") as send,
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                code=probe._legacy_main(["--config",str(path)])
            self.assertEqual(code,2)
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[4],"ERROR")
            self.assertEqual(send.call_args.args[2], [])
        finally:
            tmp.cleanup()

    def test_cli_one_notice_when_all_pass(self):
        tmp,path = self._config_file()
        try:
            with (patch.object(probe, "run_case", return_value={"case_id":"TV001",
                    "status":"PASS","reason":"ok","duration_ms":15}),
                  patch.object(probe, "write_reports", return_value=(
                      {"total":1,"pass":1,"fail":0,"error":0},Path("a"),Path("b"))),
                  patch("lib.feishu_notification_router.send_probe_notice",return_value="sent") as send,
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                code=probe._legacy_main(["--config",str(path)])
            self.assertEqual(code,0)
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[4],"PASS")
        finally:
            tmp.cleanup()

    def test_cli_delivery_failure_exit_code_three(self):
        tmp,path = self._config_file()
        try:
            with (patch.object(probe, "run_case", return_value={"case_id":"TV001",
                    "status":"PASS","reason":"ok","duration_ms":15}),
                  patch.object(probe, "write_reports", return_value=(
                      {"total":1,"pass":1,"fail":0,"error":0},Path("a"),Path("b"))),
                  patch("lib.feishu_notification_router.send_probe_notice",
                        side_effect=feishu.FeishuDeliveryError("feishu_rejected_19007")),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err):
                code=probe._legacy_main(["--config",str(path)])
            self.assertEqual(code,3)
            self.assertIn("feishu_rejected_19007",err.getvalue())
        finally:
            tmp.cleanup()

    def test_cli_check_does_not_notify(self):
        tmp,path = self._config_file()
        try:
            with (patch("lib.feishu_notification_router.send_probe_notice") as send,
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                code=probe._legacy_main(["--config",str(path),"--check"])
            self.assertEqual(code,0)
            send.assert_not_called()
        finally:
            tmp.cleanup()

if __name__ == "__main__":
    unittest.main()