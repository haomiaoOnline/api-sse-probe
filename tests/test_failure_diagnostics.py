"""Failure diagnostics: exact missing assertions, response preview and privacy."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.reporting import build_analysis
from lib import feishu
from test_probe import API, CASE, FakeOpener, response_lines
from test_feishu_notification import WEBHOOK, Opener


def frame(data):
    return {"choices": [{"delta": {"content": {"type": "1", "data": data}}}]}


class FailureDetailTests(unittest.TestCase):
    def test_missing_named_condition_and_actual_assistant_response(self):
        case = dict(CASE, expected_keywords=['HTTP 200', '文本回复 包含“电视剧”'])
        result = probe.run_case(case, API, opener=FakeOpener(response_lines(
            frame({"text": "为您推荐一部电影。",
                   "questions": ["想看电视剧吗？"],
                   "jsonHeader": '{"kind":"film"}'}))))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing_keywords"], ['文本回复 包含“电视剧”'])
        self.assertIn("为您推荐一部电影", result["response_excerpt"])
        self.assertNotIn("想看电视剧吗", result["response_excerpt"])
        self.assertIn("kind", result["response_excerpt"])
        summary, entries, failures, _ = build_analysis([result], [dict(case, scene="媒资")])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["missing_keywords"], ['文本回复 包含“电视剧”'])
        self.assertIn("为您推荐一部电影", failures[0]["response_excerpt"])
        self.assertNotIn("response_excerpt", entries[0])
        with tempfile.TemporaryDirectory() as folder:
            sm, jf, mf = probe.write_reports(
                "tencent", {"reports": {"dir": folder}}, [result], [dict(case, scene="媒资")])
            doc = json.loads(jf.read_text(encoding="utf8"))
            self.assertEqual(doc["version"], 6)
            self.assertEqual(doc["failure_cases"][0]["missing_keywords"],
                             ['文本回复 包含“电视剧”'])
            self.assertIn("为您推荐一部电影", doc["failure_cases"][0]["response_excerpt"])
            self.assertNotIn("response_excerpt", doc["results"][0])
            markdown = mf.read_text(encoding="utf8")
            self.assertIn("失败判断原因", markdown)
            self.assertIn("文本回复 包含“电视剧”", markdown)
            self.assertIn("未关联完整原始响应证据", markdown)
            self.assertNotIn("失败响应摘录", markdown)
            self.assertIn("完整 SSE 响应参数", markdown)

    def test_private_response_is_redacted_and_only_in_failed_cases(self):
        api = dict(API, headers={"powerkey":"PRIVATE_API_PASSWORD", "signature":"PRIVATE_API_SIGNATURE"})
        case = dict(CASE, deviceId="PRIVATE_DEVICE_ID",
                    expected_keywords=["missing term"])
        response = ("您好 PRIVATE_DEVICE_ID，powerkey=PRIVATE_API_PASSWORD "
                    "signature:PRIVATE_API_SIGNATURE token=private-token-value")
        failure = probe.run_case(case, api, opener=FakeOpener(
            response_lines(frame({"text": response}))))
        excerpt = failure["response_excerpt"]
        for secret in ("PRIVATE_DEVICE_ID", "PRIVATE_API_PASSWORD",
                       "PRIVATE_API_SIGNATURE", "private-token-value"):
            self.assertNotIn(secret, excerpt)
        self.assertIn("[REDACTED]", excerpt)
        success = probe.run_case(
            dict(case, expected_keywords=["您好"]),
            api, opener=FakeOpener(response_lines(frame({"text":response}))))
        self.assertEqual(success["status"], "PASS")
        self.assertNotIn("response_excerpt", success)

    def test_excessive_content_is_bounded_and_reported_as_truncated(self):
        case = dict(CASE, expected_keywords=["missing"])
        result = probe.run_case(case, API, opener=FakeOpener(
            response_lines(frame({"text": "A" * 14000}))))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["response_truncated"])
        self.assertLessEqual(len(result["response_excerpt"]), 12000)

    def test_feishu_missing_term_visible_response_private_by_default(self):
        result = {"case_id":"17", "scene":"媒资播放控制", "status":"FAIL",
                  "reason":"assertions_failed", "first_token_ms":1819,
                  "duration_ms":24642, "missing_keywords":["deeplink"],
                  "forbidden_found":[], "matched_keywords":["HTTP 200"],
                  "response_excerpt":"这里是业务系统返回的隐私回复"}
        sm, entries, failures, slow = build_analysis([result])
        cfg = {"enabled": True, "webhook_url": WEBHOOK, "secret": "TEST_SECRET",
               "keyword": "API拨测"}
        sender = Opener()
        self.assertEqual(feishu.send_notification(cfg,sm,entries,"tencent","FAIL",
                                                   opener=sender,
                                                   failure_cases=failures,
                                                   slow_token_cases=slow), "sent")
        payload=json.loads(sender.requests[0].data)
        msg=payload["card"]["elements"][0]["content"]
        self.assertIn("17",msg)
        self.assertIn("缺失：deeplink",msg)
        self.assertIn("1.819s",msg)
        self.assertIn("24.642s",msg)
        self.assertIn("本地报告",msg)
        self.assertNotIn("隐私回复", msg)
        self.assertNotIn("逐条状态", msg)
        self.assertNotIn("逐条状态", json.dumps(payload,ensure_ascii=False))
        allowed=Opener()
        feishu.send_notification(dict(cfg,include_failure_response_preview=True),
                                 sm,entries,"tencent","FAIL",opener=allowed,
                                 failure_cases=failures,slow_token_cases=slow)
        preview = json.loads(allowed.requests[0].data)["card"]["elements"][0]["content"]
        self.assertNotIn("隐私回复", preview)

    def test_feishu_does_not_show_credential_assignments_in_missing_terms(self):
        raw = {"case_id":"17", "scene":"媒资", "status":"FAIL",
               "reason":"assertions_failed", "duration_ms":1234,
               "missing_keywords":["token=hidden-credential", "文本回复 包含“电视剧”"]}
        sm, entries, failures, slow = build_analysis([raw])
        content = feishu._card(sm, entries, "tencent", "FAIL", "API拨测",
                               failure_cases=failures,
                               slow_token_cases=slow)["card"]["elements"][0]["content"]
        self.assertIn("文本回复 包含“电视剧”", content)
        self.assertNotIn("hidden-credential", content)

    def test_large_failure_list_stays_under_card_limit_without_all_case_rows(self):
        results=[{"case_id":str(i+1),"status":"PASS","reason":"ok",
                  "first_token_ms":100,"duration_ms":350} for i in range(165)]
        results[16].update(status="FAIL",reason="assertions_failed",
                            missing_keywords=["文本回复 包含“电视剧”"])
        summary,entries,failures,slow=build_analysis(results)
        card=feishu._card(summary,entries,"tencent","FAIL","API拨测",
                          failure_cases=failures,slow_token_cases=slow)
        body=card["card"]["elements"][0]["content"]
        self.assertLess(len(json.dumps(card,ensure_ascii=False).encode("utf8")),18000)
        self.assertIn("17", body)
        self.assertNotIn("逐条状态", body)
        self.assertNotIn("165 /", body)


if __name__=="__main__":
    unittest.main()