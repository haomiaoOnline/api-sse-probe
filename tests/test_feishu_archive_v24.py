"""Offline contract tests: never make a real Feishu request."""
import json
import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.feishu_archive import (archive_title,notification_title,sanitized,
                                 _permission,ArchiveError,render_blocks)
from lib.feishu_notification_router import prepare_probe_cards

class CloudArchiveContracts(unittest.TestCase):
    def test_render_blocks_contains_sanitized_full_response_with_sha(self):
        import hashlib
        from lib.feishu_archive import render_blocks
        raw=b'data: {"deviceId":"SECRET_DEVICE","text":"hello"}\n\n'
        class FrozenStore:
            def snapshot(self):return {'run_id':'RUN-SYNTH','payload':{'cases':[{'case_id':'1','scene':'test'}]}}
            def rows(self):return [(1,'1','CAPTURED')]
            def evidence(self,row):return ({'capture_metadata':{}},raw)
        report={'run_id':'RUN-SYNTH','summary':{'total':1,'pass':1,'fail':0,'error':0,'pass_rate':100},
                'results':[{'case_id':'1','status':'PASS','reason':'ok','http_status':200,
                            'first_token_ms':100,'duration_ms':400}]}
        blocks=render_blocks(FrozenStore(),report,'demo')
        text=json.dumps(blocks,ensure_ascii=False)
        self.assertIn(hashlib.sha256(raw).hexdigest(),text)
        self.assertIn('hello',text)
        self.assertNotIn('SECRET_DEVICE',text)
        self.assertIn('RUN-SYNTH',text)

    def test_oversized_public_archive_is_rejected_not_truncated(self):
        from lib.feishu_archive import render_blocks
        class FrozenStore:
            def snapshot(self):return {'run_id':'RUN-SYNTH','payload':{'cases':[{'case_id':'1','scene':'test'}]}}
            def rows(self):return [(1,'1','CAPTURED')]
            def evidence(self,row):return ({'capture_metadata':{}},b'A'*1200001)
        report={'run_id':'RUN-SYNTH','summary':{'total':1,'pass':1,'fail':0,'error':0,'pass_rate':100},
                'results':[{'case_id':'1','status':'PASS','reason':'ok','http_status':200,
                            'first_token_ms':100,'duration_ms':400}]}
        with self.assertRaisesRegex(ArchiveError,'archive_too_large'):
            render_blocks(FrozenStore(),report,'demo')

    def test_failures_first_success_second_native_code_blocks(self):
        from lib.feishu_archive import render_blocks,_content_of
        captures={'1':b'data: {"text":"ok"}\n\n',
                  '2':b'data: {"text":"failed"}\n\n',
                  '3':b'data: {"text":"error"}\n\n'}
        class Store:
            def snapshot(self):
                return {'run_id':'RUN-123','payload':{'cases':[
                  {'case_id':cid,'content':'语料'+cid,'scene':'synthetic'}
                  for cid in ('1','2','3')]}}
            def rows(self):return [(i,cid,'CAPTURED') for i,cid in enumerate(('1','2','3'),1)]
            def evidence(self,row):return ({},captures[row[1]])
        results=[{'case_id':cid,'status':status,'reason':reason,
                  'http_status':200,'first_token_ms':100,'duration_ms':300}
                 for cid,status,reason in [('1','PASS','ok'),('2','FAIL','assertions_failed'),
                                           ('3','ERROR','request_timeout')]]
        report={'summary':{'total':3,'pass':1,'fail':1,'error':1,'pass_rate':33.33},
                'results':results}
        blocks=render_blocks(Store(),report,'synthetic')
        heads=[(_content_of(b),b['block_type']) for b in blocks if b['block_type'] in (3,4,5)]
        self.assertEqual(heads[:4], [('一、运行概览',3),('二、拨测完整信息',3),
                                     ('失败用例列表（2 条）',4),('用例 #2 · FAIL',5)])
        titles=[name for name,_ in heads]
        self.assertLess(titles.index('用例 #2 · FAIL'),titles.index('用例 #3 · ERROR'))
        self.assertLess(titles.index('用例 #3 · ERROR'),titles.index('成功用例列表（1 条）'))
        self.assertLess(titles.index('成功用例列表（1 条）'),titles.index('用例 #1 · PASS'))
        codes=[b for b in blocks if b['block_type']==14]
        self.assertEqual(len(codes),3)
        self.assertTrue(all(b['code']['elements'][0]['text_run']['content'].startswith('data: ') for b in codes))
        text='\n'.join(_content_of(b) for b in blocks)
        for token in ['语料1','语料2','语料3','失败原因：assertions_failed',
                      '失败原因：request_timeout','原始证据 SHA-256']:
            self.assertIn(token,text)
        self.assertNotIn('失败原因：ok',text)

    def test_document_title_and_card_title_are_distinct(self):
        source='api-sse-probe 监控';sheet='灵犀屏-272-适配验收（bak）'
        title=archive_title({},source,sheet,datetime(2026,11,7,tzinfo=timezone.utc))
        self.assertEqual(title,source+'-'+sheet+' 丨 拨测报告丨20261107-000000')
        self.assertEqual(notification_title(source,sheet),source+'-'+sheet+' 丨 拨测报告')

    def test_redact_credentials_and_identifiers_not_business_text(self):
        raw=('Authorization: Bearer TOP_SECRET\\n'
             '{"deviceId":"SECRET_DEVICE","reqId":"SECRET_ID",'
             '"access_token":"SECRET_ACCESS","text":"安全的响应内容"}')
        output=sanitized(raw)
        for token in ('TOP_SECRET','SECRET_DEVICE','SECRET_ID','SECRET_ACCESS'):
            self.assertNotIn(token,output)
        self.assertIn('安全的响应内容',output)

    def test_permission_requires_live_readback_of_anyone_readable(self):
        requests=[]
        def fake(method,path,payload=None,bearer=None):
            requests.append((method,path,payload))
            if method=='GET':return {'permission_public':{'link_share_entity':'anyone_readable'}}
            return {}
        with patch('lib.feishu_archive._request',side_effect=fake):
            self.assertTrue(_permission('DoxTest988','token'))
        self.assertEqual([x[0] for x in requests],['PATCH','GET'])
        self.assertEqual(requests[0][2]['link_share_entity'],'anyone_readable')
        self.assertTrue(requests[0][2]['external_access'])
        with patch('lib.feishu_archive._request',side_effect=[{}, {'permission_public':{'link_share_entity':'tenant_readable'}}]):
            with self.assertRaises(ArchiveError):_permission('DoxTest988','token')

    def test_cards_include_only_verified_archive_link(self):
        summary={'total':1,'pass':0,'fail':1,'error':0,'pass_rate':0.0,
                 'first_token_sample_count':0,'average_latency_ms':None,
                 'total_duration_ms':125,'fastest_ms':None,'slowest_ms':None}
        link='https://lcn0xo7qynjm.feishu.cn/docx/DoxSafeAB123'
        cards=prepare_probe_cards({'keyword':'API拨测'},summary,'tencent',
            failure_cases=[{'case_id':'1','status':'FAIL','scene':'测试','reason':'assertions_failed',
                           'missing_keywords':['title是“电视”']}],
            archive_url=link,card_title='监控表-测试Sheet 丨 拨测报告')
        self.assertTrue(cards)
        text=json.dumps(cards[0],ensure_ascii=False)
        self.assertIn(link,text)
        self.assertIn('监控表-测试Sheet 丨 拨测报告',text)
        self.assertEqual(cards[0]['card']['header']['title']['content'],'监控表-测试Sheet 丨 拨测报告')
        with self.assertRaises(Exception):
            prepare_probe_cards({'keyword':'API拨测'},summary,'tencent',
                archive_url='https://evil.example/docx/DoxSafeAB123')

if __name__=='__main__':unittest.main()