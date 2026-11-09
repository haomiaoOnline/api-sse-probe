"""Partial report cloud archive and one-shot link delivery, offline only."""
import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.case_snapshot import freeze_cases
from lib.v2_repository import RunStore
from lib.v2_engine import archive_partial,notify_partial
from lib.feishu_archive import render_blocks,_content_of
from test_v2_reliability import CASE,CASE2,fake_capture
from lib.v2_engine import capture

class PartialCloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        home=Path(self.tmp.name)
        snap,_=freeze_cases([CASE,CASE2],source={'type':'csv','path':'fixture'},source_stats={},directory=home/'snap')
        self.store=RunStore.create(home/'runs',snap)
        self.addCleanup(self.store.close)
        self.store.intent('2')
        self.cfg={'api':{'url':'https://example.invalid','headers':{'powerkey':'fake','signature':'fake'}},
                  'reports':{'dir':str(home/'reports')},
                  'feishu':{'enabled':True,'keyword':'API拨测'},
                  'feishu_archive':{'enabled':True},
                  'report_outputs':[{'type':'file','enabled':True}]}
        with patch('lib.v2_engine.probe.run_case',side_effect=fake_capture):
            capture(self.store,dict(self.cfg,feishu={'enabled':False}))
    def test_partial_report_contains_only_real_evidence_and_unknown_notice(self):
        from lib.v2_engine import partial_report
        report=partial_report(self.store,self.cfg)
        data={'run_id':self.store.snapshot()['run_id'],
              'summary':{'total':1,'pass':1,'fail':0,'error':0,'pass_rate':100.0},
              'results':__import__('json').loads(Path(report['json_report']).read_text())['results'],
              'unknown_case_ids':['2'],'pending_case_ids':[]}
        blocks=render_blocks(self.store,data,'未完成示例',partial=True)
        text=' '.join(_content_of(b) for b in blocks)
        self.assertIn('未完成拨测',text)
        self.assertIn('用例 #2 · UNKNOWN',text)
        self.assertIn('用例 #1 · PASS',text)
        self.assertNotIn('用例 #2 · PASS',text)
        self.assertEqual(sum(b['block_type']==14 for b in blocks),1)
    def test_partial_archive_calls_explicit_partial_publisher(self):
        with patch('lib.feishu_archive.publish',return_value={
            'status':'VERIFIED','url':'https://example.feishu.cn/docx/DoxDoc123'}) as published,\
            patch('lib.feishu_archive.source_labels',return_value=('监控','Sheet')):
            response=archive_partial(self.store,self.cfg)
        self.assertEqual(response['status'],'VERIFIED')
        self.assertTrue(published.call_args.kwargs['partial'])
    def test_partial_card_contains_link_and_incomplete_marker(self):
        with patch('lib.v2_engine.archive_partial',return_value={
                 'status':'VERIFIED','url':'https://example.feishu.cn/docx/DoxDoc123'}),\
             patch('lib.feishu_archive.source_labels',return_value=('监控','Sheet')),\
             patch('lib.feishu_transport.validate_feishu_settings'),\
             patch('lib.feishu_transport.send_card_payload',return_value='sent') as sent:
            outcome=notify_partial(self.store,self.cfg)
        self.assertEqual(outcome['state'],'SENT')
        payload=sent.call_args.args[1]
        self.assertIn('未完成',payload['card']['header']['title']['content'])
        self.assertIn('DoxDoc123',payload['card']['elements'][0]['content'])
        self.assertIn('API拨测',payload['card']['elements'][0]['content'])
        with self.assertRaises(Exception):
            with patch('lib.v2_engine.archive_partial',return_value={'status':'VERIFIED','url':'https://example.feishu.cn/docx/DoxDoc123'}),patch('lib.feishu_archive.source_labels',return_value=('监控','Sheet')),patch('lib.feishu_transport.validate_feishu_settings'),patch('lib.feishu_transport.send_card_payload') as again:
                notify_partial(self.store,self.cfg)
                again.assert_not_called()
    def test_no_card_if_archive_fails(self):
        with patch('lib.v2_engine.archive_partial',side_effect=RuntimeError('cloud_failed')),\
             patch('lib.feishu_transport.validate_feishu_settings'),\
             patch('lib.feishu_transport.send_card_payload') as sender:
            with self.assertRaisesRegex(RuntimeError,'cloud_failed'):
                notify_partial(self.store,self.cfg)
        sender.assert_not_called()