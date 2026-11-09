import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.tencent_mcp import TencentMCPError as TencentError, document_identity

class TencentSourceMappingTests(unittest.TestCase):
    def test_link_extraction(self):
        self.assertEqual(document_identity({
            "doc_url":"https://docs.qq.com/sheet/Dexample123?tab=BB08J2"
        }), ("Dexample123", "BB08J2"))

    def test_explicit_sheet_overrides_url_tab(self):
        self.assertEqual(document_identity({
            "doc_url":"https://docs.qq.com/sheet/Dexample123?tab=BB08J2",
            "sheet_id":"sheet_other"
        }), ("Dexample123", "sheet_other"))

    def test_file_id_only(self):
        self.assertEqual(document_identity({
            "file_id":"Dexample123", "sheet_id":"BB08J2"
        }), ("Dexample123", "BB08J2"))

    def test_mismatched_ids_rejected(self):
        with self.assertRaises(TencentError):
            document_identity({
                "doc_url":"https://docs.qq.com/sheet/Dexample123",
                "file_id":"Ddifferent", "sheet_id":"BB08J2"
            })

    def test_untrusted_link_rejected(self):
        for bad in ("https://docs.qq.com.evil.test/sheet/Dabc",
                    "http://docs.qq.com/sheet/Dabc",
                    "https://evil.test/sheet/Dabc",
                    "https://docs.qq.com/doc/Dabc",
                    "file:///tmp/secrets"):
            with self.subTest(link=bad), self.assertRaises(TencentError):
                document_identity({"doc_url":bad,"sheet_id":"ABC"})

    def test_explicit_mapping_with_header(self):
        values = [["编号", "启用", "问句", "设备编号", "断言关键词"],
                  ["1", "true", "打开湖南卫视", "device-1", "视频技能；VIDEO30"],
                  ["2", "false", "关闭电视", "device-2", "关机"]]
        cfg = {"range":"A1:E3", "header_row":1, "columns":{
            "case_id":"A", "enabled":"B", "content":"C",
            "deviceId":"D", "expected_keywords":"E"}}
        rows = probe.tencent_rows(values, cfg)
        self.assertEqual(rows[0]["content"], "打开湖南卫视")
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases = probe.read_cases({"type":"tencent","tencent":cfg})
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["expected_keywords"], ["视频技能","VIDEO30"])
        self.assertEqual(cases[0]["deviceId"], "device-1")

    def test_relative_range_offset_not_applied_to_columns(self):
        values = [["hello", "device-8", "OK"], ["stop", "device-9", "NO"]]
        cfg = {"range":"B5:D6", "header_row":0,
               "columns":{"content":"B","deviceId":"C","expected_keywords":"D"}}
        rows = probe.tencent_rows(values, cfg)
        self.assertEqual(rows[0]["case_id"],"ROW00005")
        self.assertEqual(rows[1]["case_id"],"ROW00006")
        self.assertEqual(rows[0]["content"],"hello")
        self.assertEqual(rows[1]["expected_keywords"],"NO")

    def test_header_name_mapping(self):
        values = [["编号","业务请求","设备ID","校验词"],["42","看直播","dev","湖南卫视"]]
        cfg = {"range":"A1:D2","header_row":1,
               "columns":{"case_id":"编号","content":"业务请求",
                          "deviceId":"设备ID","expected_keywords":"校验词"}}
        rows = probe.tencent_rows(values,cfg)
        self.assertEqual(rows[0]["case_id"],"42")
        self.assertEqual(rows[0]["content"],"看直播")

    def test_legacy_header_auto_mapping(self):
        values = [["case_id","enabled","content","deviceId","expected_keywords"],
                  ["T1","true","打开","dev","成功"]]
        rows = probe.tencent_rows(values,{"range":"A1:E2","header_row":1})
        self.assertEqual(rows[0]["case_id"],"T1")
        self.assertEqual(rows[0]["deviceId"],"dev")

    def test_bad_column_outside_range(self):
        cfg={"range":"A1:D2", "header_row":0,
             "columns":{"content":"C","deviceId":"D","expected_keywords":"E"}}
        with self.assertRaises(probe.ProbeError):
            probe.tencent_rows([["a","b","c","d"]],cfg)

    def test_bad_mapping_missing_required(self):
        cfg={"range":"A1:D2", "header_row":0,"columns":{"content":"C","deviceId":"D"}}
        with self.assertRaises(probe.ProbeError):
            probe.tencent_rows([["a","b","c","d"]],cfg)

    def test_duplicate_nonempty_header(self):
        cfg={"range":"A1:D2","header_row":1,
             "columns":{"content":"B","deviceId":"C","expected_keywords":"D"}}
        with self.assertRaises(probe.ProbeError):
            probe.tencent_rows([["dup","dup","x","y"],["1","a","b","c"]],cfg)

    def test_field_mapping_runs_into_normal_assertion_check(self):
        values = [["内容","设备","预期"],["最近热门电视剧","devZ","电视剧；热门"]]
        cfg={"range":"C1:E2","header_row":1,
             "columns":{"content":"C","deviceId":"D","expected_keywords":"E"}}
        with patch("lib.tencent_mcp.read_values", return_value=values):
            cases=probe.read_cases({"type":"tencent","tencent":cfg})
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]["content"],"最近热门电视剧")
        self.assertEqual(cases[0]["case_id"],"ROW00002")
        self.assertEqual(cases[0]["expected_keywords"],["电视剧","热门"])

if __name__ == "__main__":
    unittest.main()