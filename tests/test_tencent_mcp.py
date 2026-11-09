"""Tencent Docs MCP adapter tests using mocked official JSON-RPC tools/call responses."""
import csv
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
from lib import tencent_mcp as mcp

TOKEN = "ONLY-A-FAKE-TEST-TOKEN"
CFG = {"doc_url": "https://docs.qq.com/sheet/Dsample",
       "sheet_name": "拨测用例", "range": "A1:H5", "header_row": 1,
       "columns": {"case_id": "序号", "content": "语料",
                   "deviceId": "deviceId", "expected_keywords": "判断依据（公共）",
                   "special_keywords": "特殊判断依据"}}
HEADER = ["序号", "场景", "类别", "语料", "判断依据（公共）",
          "特殊判断依据", "研发负责人", "deviceId"]
ROWS = [HEADER, ["1","直播","电视","打开湖南卫视","视频技能","湖南卫视","张某","dev-01"]]

class FakeHttpResponse:
    status=200
    headers={"Content-Type": "application/json"}
    def __init__(self, body):
        self.body=json.dumps(body).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, n):
        return self.body[:n]

class FakeOpener:
    def __init__(self, body):
        self.body=body
        self.request=None
    def open(self, request, timeout):
        self.request=request
        return FakeHttpResponse(self.body)

class MCPTests(unittest.TestCase):
    def test_token_only_from_private_config(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(mcp.resolve_token({"token":TOKEN}),TOKEN)
        with patch.dict(os.environ,{"TENCENT_DOCS_TOKEN":"ENV"},clear=True):
            self.assertEqual(mcp.resolve_token({"token":TOKEN}),TOKEN)
            self.assertEqual(mcp.resolve_token({}),"ENV")

    def test_missing_or_invalid_token_no_leak(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(mcp.TencentMCPError,"token_missing"):
                mcp.resolve_token({})
        with self.assertRaises(mcp.TencentMCPError):
            mcp.resolve_token({"token":"something\nAuthorization: secret"})
        with self.assertRaises(mcp.TencentMCPError):
            mcp.resolve_token({"token":17})

    def test_document_identity_and_range(self):
        self.assertEqual(mcp.document_identity(CFG),("Dsample",""))
        self.assertEqual(mcp.parse_range("A1:H5"),(0,0,4,7))
        self.assertEqual(mcp.parse_range("B6:D7"),(5,1,6,3))
        with self.assertRaises(mcp.TencentMCPError):
            mcp.parse_range("A1:H100000")

    def test_bad_url_rejected(self):
        with self.assertRaises(mcp.TencentMCPError):
            mcp.document_identity(dict(CFG,doc_url="https://docs.qq.com.evil.test/sheet/Dsample"))

    def test_response_unwrap_text_json_and_structured(self):
        obj={"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":json.dumps(
            {"sheets":[{"sheet_id":"ONE","sheet_name":"拨测用例"}]})}]}}
        self.assertEqual(mcp._unpack_tool_response(obj)["sheets"][0]["sheet_id"],"ONE")
        obj["result"]={"structuredContent":{"csv_data":"A,B\n"}}
        self.assertEqual(mcp._unpack_tool_response(obj)["csv_data"],"A,B\n")

    def test_mcp_business_error_code_only(self):
        with self.assertRaisesRegex(mcp.TencentMCPError,"mcp_rpc_error_-32603") as cm:
            mcp._unpack_tool_response({"error":{"code":-32603,"message":"SECRET INTERNAL CONTENT"}})
        self.assertNotIn("SECRET",str(cm.exception))
        with self.assertRaisesRegex(mcp.TencentMCPError,"business_error"):
            mcp._unpack_tool_response({"result":{"isError":True}})

    def test_realistic_tool_call_request_structure(self):
        response={"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":json.dumps(
            {"sheets":[{"sheet_id":"ABC","sheet_name":"拨测用例"}]})}]}}
        fake=FakeOpener(response)
        result=mcp.call_tool(TOKEN,"sheet.get_sheet_info",{"file_id":"Dsample"},opener=fake)
        self.assertEqual(result["sheets"][0]["sheet_id"],"ABC")
        request=json.loads(fake.request.data)
        self.assertEqual(request["params"]["name"],"sheet.get_sheet_info")
        self.assertEqual(request["method"],"tools/call")
        self.assertEqual(fake.request.get_header("Authorization"),TOKEN)
        self.assertNotIn(TOKEN,json.dumps(request))
        self.assertIn("/openapi/mcp",fake.request.full_url)

    def test_two_calls_and_eight_column_mapping(self):
        calls=[]
        def fake(_token, tool, args):
            calls.append((tool,args))
            if tool=="sheet.get_sheet_info":
                return {"sheets":[{"sheet_id":"S1","sheet_name":"拨测用例"}]}
            buf=io.StringIO()
            csv.writer(buf).writerows(ROWS)
            return {"csv_data":buf.getvalue()}
        values=mcp.read_values(CFG,{"token":TOKEN},caller=fake)
        self.assertEqual(values[0][:8],HEADER)
        self.assertEqual(values[1][3],"打开湖南卫视")
        self.assertEqual([x[0] for x in calls],["sheet.get_sheet_info","sheet.get_cell_data"])
        self.assertEqual(calls[1][1]["sheet_id"],"S1")
        self.assertTrue(calls[1][1]["return_csv"])
        with patch("lib.tencent_mcp.read_values",return_value=values):
            cases=probe.read_cases({"type":"tencent","tencent":CFG},{"token":TOKEN})
        self.assertEqual(cases[0]["content"],"打开湖南卫视")
        self.assertEqual(cases[0]["deviceId"],"dev-01")
        self.assertEqual(cases[0]["expected_keywords"],["视频技能","湖南卫视"])

    def test_sheet_name_mismatch_fails(self):
        with self.assertRaises(mcp.TencentMCPError):
            mcp.resolve_sheet_id({"sheets":[{"sheet_id":"S1","sheet_name":"other"}]},"拨测用例")
        with self.assertRaises(mcp.TencentMCPError):
            mcp.resolve_sheet_id({"sheets":[{"sheet_id":"S1","sheet_name":"拨测用例"}]},"拨测用例","DIFFERENT")

    def test_sparse_structured_cells(self):
        values=mcp._to_rows({"cells":[
            {"row":5,"col":1,"string_value":"hello"},
            {"row":6,"col":3,"number_value":7}
        ]},5,1,6,3)
        self.assertEqual(values,[["hello","",""],["","","7"]])

    def test_tool_list_only(self):
        with self.assertRaises(mcp.TencentMCPError):
            mcp.call_tool(TOKEN,"sheet.set_cell_value",{"file_id":"X"},opener=FakeOpener({}))

if __name__=="__main__":
    unittest.main()