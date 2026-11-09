"""v1.6.0: multi-scenario sheet parsing and typed output assertions."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe
from lib.assertion_rules import evaluate_assertions
from test_probe import API, CASE, FakeOpener, response_lines

CFG = {
    "range": "A1:H200", "header_row": 1,
    "columns": {
        "case_id": "A", "scene": "B", "category": "C", "content": "D",
        "expected_keywords": "E", "special_keywords": "F",
        "owner": "G", "deviceId": "H",
    }
}

def row(number, public, special, device="test-device", scene="媒资"):
    return [str(number), scene, "问答", "最近热播的电视剧有哪些",
            public, special, "研发", device]

def frame(data):
    return {"choices": [{"delta": {"content": {"type": "1", "data": data}}}]}


class BatchV16Tests(unittest.TestCase):
    def setUp(self):
        self.expected = [
            "HTTP 200", "衍生问题", "文本回复", '文本回复 包含“电视剧”'
        ]
        self.base = dict(
            CASE, expected_keywords=self.expected,
            forbidden_keywords=[], match_mode="all",
        )

    def run_case(self, frames):
        return probe.run_case(
            self.base, API, opener=FakeOpener(response_lines(*frames)))

    def test_split_slash_but_not_url(self):
        self.assertEqual(
            probe.split_words("HTTP 200 / 衍生问题 / 文本回复"),
            ["HTTP 200", "衍生问题", "文本回复"])
        self.assertEqual(
            probe.split_words("HTTP 200/衍生问题／文本回复"),
            ["HTTP 200", "衍生问题", "文本回复"])
        self.assertEqual(
            probe.split_words("https://docs.qq.com/sheet/abc"),
            ["https://docs.qq.com/sheet/abc"])

    def test_text_reply_and_contains_real_answer(self):
        r = self.run_case([
            frame({"questions": ["有没有别的电视剧？"]}),
            frame({"text": "最近推荐的电视剧有"}), frame({"text": "热门悬疑剧。"}),
        ])
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["matched_keywords"], self.expected)

    def test_question_or_metadata_alone_is_not_text_reply(self):
        r = self.run_case([frame({
            "questions": ["最近热播电视剧有哪些？"],
            "title": "电视剧",
            "jsonBody": '{"text":"电视剧"}'
        })])
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("文本回复", r["missing_keywords"])
        self.assertIn('文本回复 包含“电视剧”', r["missing_keywords"])

    def test_text_reply_exists_but_does_not_contain_target(self):
        r = self.run_case([
            frame({"questions": ["其他推荐？"]}),
            frame({"text": "为您推荐近期热门电影"}),
        ])
        self.assertEqual(r["status"], "FAIL")
        self.assertNotIn("文本回复", r["missing_keywords"])
        self.assertIn('文本回复 包含“电视剧”', r["missing_keywords"])

    def test_multiple_quoted_terms_require_all(self):
        requirement = ['文本回复 包含“电视剧”“热门”']
        data = {"answer_text_fragments": ["最近热门电视剧。"],
                "valid_followup_questions": True}
        _, missing, _ = evaluate_assertions(
            requirement, ["电视剧", "热门"], 200, data)
        self.assertEqual(missing, [])
        data["answer_text_fragments"] = ["这是电视剧。"]
        _, missing, _ = evaluate_assertions(requirement, ["热门"], 200, data)
        self.assertEqual(missing, requirement)

    def test_sheet_skips_examples_and_group_title_and_inherits(self):
        values = [["序号","场景","类别","语料","判断依据（公共）",
                   "特殊判断依据","研发负责人","deviceId"]]
        values += [row("示例", "HTTP 200", "电视剧", device="") for _ in range(9)]
        values.append(row(1, "HTTP 200 / 衍生问题 / 文本回复",
                          '文本回复 包含“电视剧”'))
        values.append(row(2, "同上", "同上"))
        for i in range(3, 166):
            values.append(row(i, "HTTP 200 / 文本回复", ""))
        values.append(["半双工连续对话", "", "", "", "", "", "", ""])
        self.assertEqual(len(values), 176)
        stats = {}
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases = probe.read_cases(
                {"type":"tencent", "tencent":CFG}, stats=stats)
        self.assertEqual(len(cases), 165)
        self.assertEqual(cases[0]["case_id"], "1")
        self.assertEqual(cases[-1]["case_id"], "165")
        self.assertEqual(cases[0]["expected_keywords"], self.expected)
        self.assertEqual(cases[1]["expected_keywords"], self.expected)
        self.assertEqual(stats["skipped_missing_device_id"], 10)
        self.assertEqual(stats["source_rows"], 175)
        self.assertEqual(stats["skipped_rows"], 10)
        self.assertEqual(stats["enabled_cases"], 165)

    def test_same_column_inheritance_and_chain(self):
        values = [["A","B","C","D","E","F","G","H"],
                  row(1, "HTTP 200 / 文本回复", '文本回复 包含“电视剧”'),
                  row(2, "同上", "同上"),
                  row(3, "同上", "同上")]
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases = probe.read_cases({"type":"tencent","tencent":CFG})
        self.assertEqual(cases[0]["expected_keywords"],
                         cases[2]["expected_keywords"])
        self.assertNotIn("同上", cases[2]["expected_keywords"])

    def test_inheritance_without_previous_valid_row_is_error(self):
        values = [["A","B","C","D","E","F","G","H"],
                  row("示例", "HTTP 200", "", device=""),
                  row(1, "同上", "")]
        with patch("lib.tencent_mcp.read_values", return_value=values):
            with self.assertRaisesRegex(probe.ProbeError, "同上"):
                probe.read_cases({"type":"tencent","tencent":CFG})

    def test_no_skipping_missing_device_on_csv_or_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/"cases.json"
            p.write_text(json.dumps([{
                "case_id":"1","content":"abc",
                "deviceId":"","expected_keywords":"HTTP 200"}]),encoding="utf8")
            with self.assertRaisesRegex(probe.ProbeError, "deviceId"):
                probe.read_cases({"type":"json","path":str(p)})

    def test_missing_device_never_silently_passes(self):
        values = [["A","B","C","D","E","F","G","H"],
                  row(1, "HTTP 200","",device=""),
                  row(2, "HTTP 200","",device="test-device")]
        with patch("lib.tencent_mcp.read_values", return_value=values):
            st={}
            cases=probe.read_cases({"type":"tencent","tencent":CFG},stats=st)
        self.assertEqual(st["skipped_missing_device_id"],1)
        self.assertEqual([x["case_id"] for x in cases],["2"])


if __name__=="__main__":
    unittest.main()