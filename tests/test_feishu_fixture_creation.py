"""Simulates app-owned Feishu folder/sheet provisioning; no cloud writes."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from create_feishu_test_sheet import provision,ROWS

class FeishuFixtureTests(unittest.TestCase):
    def test_create_folder_sheet_write_then_readback(self):
        requests=[]
        def fake_request(method,path,payload=None,bearer=None,opener=None):
            requests.append((method,path,payload))
            if path.endswith('create_folder'):
                return {'token':'folder123','url':'https://workspace.feishu.cn/drive/folder/folder123'}
            if path.endswith('/sheets/v3/spreadsheets'):
                return {'spreadsheet':{'spreadsheet_token':'sheetabc123','url':'https://workspace.feishu.cn/sheets/sheetabc123'}}
            if path.endswith('/metainfo'):
                return {'sheets':[{'sheetId':'abc123'}]}
            return {}
        with patch('create_feishu_test_sheet._auth',return_value='fake-token'):
            with patch('create_feishu_test_sheet._request',side_effect=fake_request):
                with patch('create_feishu_test_sheet.read_values',return_value=ROWS):
                    result=provision({'app_id':'fake','app_secret':'fake'},'独立测试文件夹')
        self.assertTrue(result['readback_verified'])
        self.assertEqual(result['business_posts'],0)
        self.assertEqual(result['feishu_group_messages'],0)
        self.assertEqual([m for m,_,_ in requests],['POST','POST','GET','PUT'])
        self.assertEqual(requests[-1][2]['valueRange']['values'],ROWS)
        self.assertEqual(result['source_example']['feishu']['columns']['content'],'语料')

if __name__=='__main__':unittest.main()