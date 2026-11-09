"""Offline tests for report-document/output-port decoupling."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import probe
from lib.report_document import build_report_document, render_markdown, public_projection
from lib.outputs import registry


def sample_document():
    cfg={"reports":{"slow_token_threshold_ms":3000}}
    cases=[{"case_id":"001","scene":"渠道播放","content":"PRIVATE_USER_UTTERANCE",
            "deviceId":"PRIVATE_DEVICE_ID"}]
    raw_sse = ('data:{"choices":[{"delta":{"content":{"data":{"text":"PRIVATE_ASSISTANT_RESPONSE"},'
               '"type":"1"}}}],"model":"test"}\n\ndata:[DONE]\n\n')
    raw_bytes = raw_sse.encode("utf8")
    results=[{"case_id":"001","status":"FAIL","reason":"assertions_failed",
              "http_status":200,"reqId":"PRIVATE_REQUEST_ID",
              "first_token_ms":1670,"duration_ms":24888,
              "matched_keywords":[],"missing_keywords":["关键字段"],
              "forbidden_found":[],"response_excerpt":"PRIVATE_ASSISTANT_RESPONSE",
              "failure_brief":"缺少关键字段",
              "failure_assertions":[{"condition":"关键字段","expected":"必须出现关键字段",
                                     "actual":"未匹配该判断条件",
                                     "failure_reason":"expected_condition_not_met"}],
              "raw_sse_text":raw_sse,"raw_sse_encoding":"UTF-8",
              "raw_evidence":{"path":"reports/latest-failed-sse/sample.json",
                              "case_id":"001","http_status":200,
                              "content_type":"text/event-stream",
                              "received_bytes":len(raw_bytes),
                              "sha256":hashlib.sha256(raw_bytes).hexdigest(),
                              "capture_state":"protocol_done"}}]
    return build_report_document("csv",cfg,results,cases,24888)

class OutputPortTests(unittest.TestCase):
    def test_stable_private_schema_and_pure_markdown(self):
        doc=sample_document()
        self.assertEqual(doc["version"],6)
        self.assertEqual(len(doc["run_id"]),36)
        self.assertEqual(doc["summary"]["pass"],0)
        self.assertEqual(doc["summary"]["fail"],1)
        self.assertEqual(doc["summary"]["average_latency_s"],1.67)
        self.assertIn("PRIVATE_USER_UTTERANCE",json.dumps(doc,ensure_ascii=False))
        markdown = render_markdown(doc)
        self.assertIn("PRIVATE_ASSISTANT_RESPONSE",markdown)
        self.assertIn("### 场景：渠道播放",markdown)
        self.assertIn("**语料**",markdown)
        self.assertIn("PRIVATE_USER_UTTERANCE",markdown)
        self.assertIn("**失败判断原因**",markdown)
        self.assertIn("关键字段",markdown)
        self.assertIn("**完整 SSE 响应参数**",markdown)
        self.assertIn("data:[DONE]",markdown)
        self.assertNotIn("PRIVATE_DEVICE_ID",markdown)
        self.assertNotIn("设备 ID",markdown)
        self.assertNotIn("PRIVATE_REQUEST_ID",markdown)
        self.assertNotIn("请求头",markdown)
        self.assertNotIn("实际助手响应摘录",markdown)
        self.assertNotIn("慢 Token 用例",markdown)
        self.assertNotIn("失败简介",markdown)
        self.assertNotIn("PRIVATE_DEVICE_ID",json.dumps(public_projection(doc),ensure_ascii=False))
        self.assertIn("raw_sse_text",json.dumps(doc))
        self.assertNotIn("raw_sse_text",json.dumps(public_projection(doc)))
        self.assertNotIn("raw_sse_base64",json.dumps(doc))

    def test_local_file_sink_writes_private_pair_and_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg={"reports":{"dir":folder},"report_outputs":[{"type":"file","enabled":True}]}
            receipts=registry.dispatch_report_outputs(sample_document(),cfg,ROOT)
            self.assertEqual(len(receipts),1)
            receipt=receipts[0]
            self.assertEqual(receipt["type"],"file")
            self.assertEqual(receipt["status"],"saved")
            a,b=Path(receipt["json_report"]),Path(receipt["markdown_report"])
            self.assertTrue(a.is_file() and b.is_file())
            payload=json.loads(a.read_text(encoding="utf8"))
            self.assertEqual(payload["version"],6)
            self.assertIn("PRIVATE_ASSISTANT_RESPONSE",b.read_text(encoding="utf8"))
            self.assertIn("raw_sse_text",payload["failure_cases"][0])
            if os.name!="nt":
                self.assertEqual(a.stat().st_mode&0o777,0o600)
                self.assertEqual(b.stat().st_mode&0o777,0o600)

    def test_future_cloud_sink_gets_only_minimal_public_projection(self):
        received=[]
        class FakeCloudOutput:
            def publish(self,document,context):
                received.append(document)
                return {"type":"fake_cloud","status":"saved",
                        "reference":"TEST_ONLY_NO_NETWORK"}
        registry.register_output("fake_cloud",FakeCloudOutput)
        try:
            cfg={"reports":{"dir":"reports"},
                 "report_outputs":[{"type":"fake_cloud","enabled":True}]}
            receipts=registry.dispatch_report_outputs(sample_document(),cfg,ROOT)
            self.assertEqual(receipts[0]["reference"],"TEST_ONLY_NO_NETWORK")
            self.assertEqual(received[0]["version"],6)
            self.assertEqual(received[0]["summary"]["total"],1)
            dumped=json.dumps(received[0],ensure_ascii=False)
            for secret in ("PRIVATE_USER_UTTERANCE","PRIVATE_ASSISTANT_RESPONSE",
                           "PRIVATE_DEVICE_ID","PRIVATE_REQUEST_ID","raw_sse_text",
                           "raw_sse_base64"):                self.assertNotIn(secret,dumped)
            self.assertIn("关键字段",dumped)
        finally:
            registry._REGISTRY.pop("fake_cloud",None)

    def test_unknown_or_duplicate_output_fails_closed(self):
        for outputs in (
            [{"type":"feishu_bitable","enabled":True}],
            [{"type":"feishu_doc","enabled":True}],
            [{"type":"file","enabled":False}],
            [{"type":"file"},{"type":"file"}],
            [{"type":"file","enabled":"yes"}],
        ):
            with self.assertRaises(ValueError):
                registry.validate_report_outputs({"report_outputs":outputs})
        self.assertEqual(registry.validate_report_outputs({}),["file"])

    def test_unsupported_sink_preflight_does_not_post_business_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg=json.loads((ROOT/"config.example.json").read_text(encoding="utf8"))
            cfg["report_outputs"]=[{"type":"feishu_bitable","enabled":True}]
            cfg["reports"]["dir"]=str(Path(folder)/"reports")
            configfile=Path(folder)/"config.json"
            configfile.write_text(json.dumps(cfg),encoding="utf8")
            with patch.object(probe,"run_case") as api_call:
                # A configuration error is reported without touching API.
                result=probe._legacy_main(["--config",str(configfile)])
            self.assertEqual(result,2)
            api_call.assert_not_called()
            self.assertFalse((Path(folder)/"reports").exists())


if __name__=="__main__":
    unittest.main()