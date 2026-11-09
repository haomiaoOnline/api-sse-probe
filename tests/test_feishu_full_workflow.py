"""Full offline Feishu sheet -> 2-case durable run -> report, with no live POST."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import probe
from lib import feishu_sheet
from create_feishu_test_sheet import ROWS


class FeishuFullWorkflow(unittest.TestCase):
    def test_sheet_source_into_durable_report(self):
        def fake_transport(case,api,assertions=None,raw_capture=None,capture_metadata=None,
                           defer_assertions=False,request_id=None,**kwargs):
            self.assertTrue(defer_assertions)
            content={'text':'音乐播放已开始'} if case['case_id']=='1' else {'text':'提醒设置完成','title':'灵犀定时任务'}
            frame={'choices':[{'delta':{'content':{'data':content}}}]}
            payload=('data: '+json.dumps(frame,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
            raw_capture.write(payload)
            capture_metadata.update({'response':{'status':200,'content_type':'text/event-stream',
                                                  'body_kind':'sse','capture_state':'protocol_done'}})
            return {'case_id':case['case_id'],'status':'CAPTURED','reason':'capture_only',
                    'http_status':200,'done_received':True,'json_frame_count':1,
                    'first_token_ms':80,'duration_ms':120}

        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'private.json'
            fixture={
              'api':{'url':'https://example.test/probe','headers':{'powerkey':'fake','signature':'fake'}},
              'source':{'type':'feishu','feishu':{
                  'doc_url':'https://workspace.feishu.cn/sheets/shtFixture0123?sheet=abc123',
                  'range':'A1:I20','header_row':1,
                  'columns':{'case_id':'序号','scene':'场景','category':'类别',
                             'content':'语料','expected_keywords':'判断依据（公共）',
                             'special_keywords':'特殊判断依据','owner':'研发负责人',
                             'deviceId':'deviceId'}}},
              'feishu_docs_auth':{'app_id':'fake-id','app_secret':'fake-secret'},
              'feishu':{'enabled':False},'reports':{'dir':str(Path(tmp)/'reports')}
            }
            cfg.write_text(json.dumps(fixture,ensure_ascii=False))
            with patch.object(feishu_sheet,'read_values',return_value=ROWS) as sheet_get, \
                 patch.object(probe,'run_case',side_effect=fake_transport) as api_post:
                text=io.StringIO()
                with contextlib.redirect_stdout(text):
                    rc=probe.main(['--config',str(cfg)])
            self.assertEqual(rc,0)
            answer=json.loads(text.getvalue())
            self.assertEqual(answer['workflow_status'],'COMPLETE')
            self.assertEqual(answer['summary']['total'],2)
            self.assertEqual(answer['summary']['pass'],2)
            self.assertEqual(answer['summary']['fail'],0)
            self.assertEqual(sheet_get.call_count,1)
            self.assertEqual(api_post.call_count,2)
            report=Path(answer['report_outputs'][0]['json_report'])
            self.assertTrue(report.is_file())
            self.assertEqual(answer['notification']['status'],'disabled')
            from lib.v2_repository import RunStore
            store=RunStore(Path(answer['run_dir']))
            try:
                self.assertEqual(len(store.plan()['captured']),2)
                for row in store.rows():
                    _envelope,data=store.evidence(row)
                    self.assertTrue(data)
            finally:store.close()
            private_snapshot='\n'.join(f.read_text() for f in (Path(tmp)/'reports').rglob('*.json') if 'v2-case-snapshots' in str(f))
            self.assertNotIn('fake-secret',private_snapshot)

if __name__=='__main__':unittest.main()