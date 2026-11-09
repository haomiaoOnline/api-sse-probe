"""Read-only Tencent MCP and current Chinese sheet mapping regressions."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib import tencent_mcp as tencent

HEADER = ["序号", "场景", "类别", "语料", "判断依据（公共）",
          "特殊判断依据", "研发负责人", "deviceId"]
COL = {"case_id": "序号", "scene": "场景", "category": "类别",
       "content": "语料", "expected_keywords": "判断依据（公共）",
       "special_keywords": "特殊判断依据", "owner": "研发负责人",
       "deviceId": "deviceId"}
CFG = {"doc_url": "https://docs.qq.com/sheet/Dsample123",
       "sheet_name": "示例表", "range": "A1:H500",
       "header_row": 1, "columns": COL}


class UserSheetFormatTests(unittest.TestCase):
    def test_eight_column_mapping_and_merge(self):
        values = [
            HEADER,
            [101, "直播", "视频", "打开湖南卫视", "视频技能；VIDEO",
             "湖南卫视|101&G_HUNAN", "示例负责", "dev-12"],
            [102, "问答", "AI", "最近热播", "电视剧", "", "示例负责", "dev-13"],
            [103, "问答", "AI", "没有断言", "", "", "", "dev-14"],
        ]
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases = probe.read_cases({"type": "tencent", "tencent": CFG})
        self.assertEqual([c["case_id"] for c in cases], ["101", "102", "103"])
        self.assertEqual(cases[0]["expected_keywords"],
                         ["视频技能", "VIDEO", "湖南卫视", "101&G_HUNAN"])

    def test_header_order_changes(self):
        values = [["deviceId", "语料", "特殊判断依据", "序号",
                   "判断依据（公共）", "场景", "类别", "研发负责人"],
                  ["d9", "看直播", "湖南卫视", "9",
                   "视频技能", "直播", "视频", "示例"]]
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases = probe.read_cases({"type": "tencent", "tencent": CFG})
        self.assertEqual(cases[0]["content"], "看直播")
        self.assertEqual(cases[0]["expected_keywords"], ["视频技能", "湖南卫视"])

    def test_missing_header_fails(self):
        bad = [["序号", "场景", "类别", "内容", "判断依据（公共）",
                "特殊判断依据", "研发负责人", "deviceId"]]
        with patch("lib.tencent_mcp.read_values", return_value=bad):
            with self.assertRaises(probe.ProbeError):
                probe.read_cases({"type": "tencent", "tencent": CFG})

    def test_sheet_name_lookup_and_conflict(self):
        file_id, tab = tencent.document_identity(CFG)
        self.assertEqual((file_id, tab), ("Dsample123", ""))
        metadata = {"sheets": [{"sheetId": "B1", "title": "别的"},
                               {"sheetId": "BB08J2", "title": "示例表"}]}
        self.assertEqual(tencent.resolve_sheet_id(metadata, "示例表"), "BB08J2")
        with self.assertRaises(tencent.TencentMCPError):
            tencent.resolve_sheet_id({"sheets": []}, "不存在")
        with self.assertRaises(tencent.TencentMCPError):
            tencent.resolve_sheet_id({"sheets": [
                {"title": "重复", "sheetId": "A"},
                {"title": "重复", "sheetId": "B"}]}, "重复")

    def test_only_mcp_read_methods_without_real_network(self):
        csv_text = (",".join(HEADER) + "\n"
                    + "1,直播,视频,打开,视频技能,湖南卫视,,D1\n")
        caller = Mock(side_effect=[
            {"sheets": [{"title": "示例表", "sheetId": "BB08J2"}]},
            {"csv_data": csv_text},
        ])
        with patch.dict(os.environ, {"TENCENT_DOCS_TOKEN": "FAKE_TEST"}, clear=True):
            rows = tencent.read_values(CFG, caller=caller)
        self.assertEqual(len(rows), 2)
        self.assertEqual(caller.call_args_list[0].args[1], "sheet.get_sheet_info")
        self.assertEqual(caller.call_args_list[1].args[1], "sheet.get_cell_data")

    def test_conflicting_sheet_id_rejected(self):
        bad = dict(CFG, sheet_id="WRONG")
        caller = Mock(return_value={"sheets": [
            {"title": "示例表", "sheetId": "BB08J2"}]})
        with patch.dict(os.environ, {"TENCENT_DOCS_TOKEN": "FAKE_TEST"}, clear=True):
            with self.assertRaises(tencent.TencentMCPError):
                tencent.read_values(bad, caller=caller)


if __name__ == "__main__":
    unittest.main()