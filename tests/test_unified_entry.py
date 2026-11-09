"""No-network acceptance tests for the public v2-by-default entry."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import probe
from lib import v2_engine

CASE = {'case_id':'1','content':'test-utterance','deviceId':'test-device',
        'expected_keywords':['hello'],'forbidden_keywords':[],
        'match_mode':'all','endpoint':{},'session':{},'scene':'test'}

class UnifiedEntryTests(unittest.TestCase):
    def config(self, folder):
        config = folder / 'private.json'
        config.write_text(json.dumps({
            'api':{'url':'https://example.test/api',
                   'headers':{'powerkey':'test','signature':'test'}},
            'source':{'type':'csv','path':'assets/sample_cases.csv'},
            'reports':{'dir':str(folder / 'reports')},
            'feishu':{'enabled':False}
        }), encoding='utf8')
        return str(config)

    def test_public_main_never_uses_legacy_for_real_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            with patch.object(probe, '_legacy_main', side_effect=AssertionError('legacy route')):
                with patch.object(probe, 'read_cases', return_value=[CASE]):
                    with patch.object(v2_engine, 'capture', side_effect=lambda store,cfg,*args: {
                            'plan':store.plan(),'processed_this_batch':0}):
                        out = io.StringIO()
                        with contextlib.redirect_stdout(out):
                            rc = probe.main(['--config',cfg])
            self.assertEqual(rc, 0)
            result = json.loads(out.getvalue().strip())
            self.assertEqual(result['workflow_status'], 'CONTINUE_REQUIRED')
            self.assertEqual(result['plan_counts']['pending'],1)
            self.assertTrue((Path(result['run_dir'])/'state.sqlite3').exists())

    def test_repeat_same_cli_resumes_snapshot_without_rereading_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            with patch.object(v2_engine,'capture', side_effect=lambda store,cfg,*args: {
                    'plan':store.plan(), 'processed_this_batch':0}):
                with patch.object(probe,'read_cases',return_value=[CASE]) as read:
                    outs=[]
                    for _ in range(2):
                        stream=io.StringIO()
                        with contextlib.redirect_stdout(stream):
                            self.assertEqual(probe.main(['--config',cfg]),0)
                        outs.append(json.loads(stream.getvalue()))
                self.assertEqual(read.call_count,1)
                self.assertEqual(outs[0]['run_dir'],outs[1]['run_dir'])

    def test_unknown_intent_never_automatically_redispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            def unknown(store,cfg,*args):
                if store.plan()['pending']:
                    store.intent('1')
                return {'plan':store.plan(),'processed_this_batch':0}
            with patch.object(probe,'read_cases',return_value=[CASE]),patch.object(v2_engine,'capture',side_effect=unknown):
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,2)
            result=json.loads(out.getvalue().strip())
            self.assertEqual(result['workflow_status'],'BLOCKED_UNKNOWN_REQUIRES_RECONCILIATION')
            self.assertEqual(result['unknown_case_ids'],['1'])
            self.assertFalse(result['automatic_retry'])

    def test_unknown_without_pending_auto_sends_linked_partial_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp))
            private=json.loads(Path(cfg).read_text())
            private['feishu']={'enabled':True}
            private['feishu_archive']={'enabled':True}
            Path(cfg).write_text(json.dumps(private))
            def one_unknown(store,config,*args):
                if store.plan()['pending']:store.intent('1')
                return {'plan':store.plan(),'processed_this_batch':0}
            with patch.object(probe,'read_cases',return_value=[CASE]), \
                 patch('lib.feishu_notification_router.validate_probe_notification'), \
                 patch.object(v2_engine,'capture',side_effect=one_unknown), \
                 patch.object(v2_engine,'notify_partial',
                    return_value={'state':'SENT','archive':{'status':'VERIFIED','url':'https://example.feishu.cn/docx/DoxTest123'}}) as notify:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,2)
            notice=json.loads(output.getvalue())
            self.assertEqual(notice['notification']['state'],'SENT')
            self.assertEqual(notice['archive']['status'],'VERIFIED')
            notify.assert_called_once()

    def test_unknown_with_pending_defers_cloud_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp))
            private=json.loads(Path(cfg).read_text())
            private['feishu']={'enabled':True}
            private['feishu_archive']={'enabled':True}
            Path(cfg).write_text(json.dumps(private))
            def one_unknown(store,config,*args):
                if store.plan()['pending']:store.intent('1')
                return {'plan':store.plan(),'processed_this_batch':0}
            with patch.object(probe,'read_cases',return_value=[CASE,dict(CASE,case_id='2')]), \
                 patch('lib.feishu_notification_router.validate_probe_notification'), \
                 patch.object(v2_engine,'capture',side_effect=one_unknown), \
                 patch.object(v2_engine,'notify_partial') as notify:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,2)
            notice=json.loads(output.getvalue())
            self.assertEqual(notice['archive']['status'],'DEFERRED_UNTIL_PENDING_ZERO')
            notify.assert_not_called()

    def test_full_pipeline_uses_frozen_evidence_and_offline_judge(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            case = dict(CASE, expected_keywords=['HTTP 200', '回复中包含“北京”和“天气”'])
            with patch.object(probe, 'read_cases', return_value=[case]), \
                 patch.object(probe, 'run_case', side_effect=fake_capture) as post:
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,0)
            self.assertEqual(post.call_count,1)
            out=json.loads(stream.getvalue())
            self.assertEqual(out['workflow_status'],'COMPLETE')
            self.assertEqual(out['summary']['pass'],1)
            self.assertTrue(out['report_outputs'])
            self.assertEqual(out['notification']['status'],'disabled')

    def test_eight_cases_capture_five_then_resume_three_without_duplicates(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            cases=[dict(CASE,case_id=str(i), expected_keywords=['HTTP 200',
                    '回复中包含“北京”和“天气”']) for i in range(1,9)]
            with patch.object(probe,'read_cases',return_value=cases) as read, \
                 patch.object(probe,'run_case',side_effect=fake_capture) as posts:
                streams=[]
                for _ in range(2):
                    out=io.StringIO()
                    with contextlib.redirect_stdout(out):
                        rc=probe.main(['--config',cfg,'--max-cases','5'])
                    streams.append(json.loads(out.getvalue()))
            self.assertEqual(rc,0)
            self.assertEqual(read.call_count,1)
            self.assertEqual(posts.call_count,8)
            self.assertEqual(streams[0]['workflow_status'],'CONTINUE_REQUIRED')
            self.assertIn('--max-cases 5',streams[0]['resume_command'])
            self.assertEqual(streams[0]['plan_counts']['captured'],5)
            self.assertEqual(streams[1]['workflow_status'],'COMPLETE')
            self.assertEqual(streams[1]['summary']['total'],8)
            self.assertEqual(streams[0]['run_dir'],streams[1]['run_dir'])

    def test_live_source_url_override_uses_tencent_tab_without_config_edit(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            before=Path(cfg).read_bytes()
            case=dict(CASE,expected_keywords=['HTTP 200','回复中包含“北京”和“天气”'])
            url='https://docs.qq.com/sheet/Dexample123?tab=caseTab'
            with patch.object(probe,'read_cases',return_value=[case]) as source_reader, \
                 patch.object(probe,'run_case',side_effect=fake_capture):
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(probe.main(['--config',cfg,'--source-url',url,'--no-feishu']),0)
            self.assertEqual(json.loads(out.getvalue())['workflow_status'],'COMPLETE')
            source=source_reader.call_args.args[0]
            self.assertEqual(source['type'],'tencent')
            self.assertEqual(source['tencent']['doc_url'],url)
            self.assertEqual(Path(cfg).read_bytes(),before)

    def test_default_runs_all_eight_cases_in_one_invocation(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            cases = [dict(CASE, case_id=str(i), expected_keywords=[
                'HTTP 200','回复中包含“北京”和“天气”']) for i in range(1,9)]
            with patch.object(probe, 'read_cases', return_value=cases) as source, \
                 patch.object(probe, 'run_case', side_effect=fake_capture) as post:
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    rc = probe.main(['--config',cfg])
            self.assertEqual(rc,0)
            out=json.loads(stream.getvalue())
            self.assertEqual(out['workflow_status'],'COMPLETE')
            self.assertEqual(out['processed_this_batch'],8)
            self.assertEqual(out['summary']['total'],8)
            self.assertEqual(post.call_count,8)
            self.assertEqual(source.call_count,1)

    def test_default_runs_165_cases_in_one_invocation(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            cases = [dict(CASE,case_id=str(i),expected_keywords=[
                'HTTP 200','回复中包含“北京”和“天气”']) for i in range(1,166)]
            with patch.object(probe,'read_cases',return_value=cases), \
                 patch.object(probe,'run_case',side_effect=fake_capture) as requests:
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,0)
            out=json.loads(stream.getvalue())
            self.assertEqual(out['workflow_status'],'COMPLETE')
            self.assertEqual(out['processed_this_batch'],165)
            self.assertEqual(out['plan_counts'],{'pending':0,'unknown':0,'captured':165})
            self.assertEqual(out['summary']['total'],165)
            self.assertEqual(requests.call_count,165)

    def test_explicit_max_seconds_resume_command_retains_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp))
            with patch.object(probe,'read_cases',return_value=[CASE]), \
                 patch.object(v2_engine,'capture',side_effect=lambda store,cfg,*args:{
                    'plan':store.plan(),'processed_this_batch':0}):
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(probe.main(['--config',cfg,'--max-seconds','3600']),0)
            out=json.loads(stream.getvalue())
            self.assertIn('--max-seconds 3600',out['resume_command'])

    def test_restarts_after_last_capture_without_reposting(self):
        from test_v2_reliability import fake_capture
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp))
            case=dict(CASE,expected_keywords=['HTTP 200','回复中包含“北京”和“天气”'])
            with patch.object(probe,'read_cases',return_value=[case]) as source, \
                 patch.object(probe,'run_case',side_effect=fake_capture) as requests:
                with patch.object(v2_engine,'rejudge',side_effect=RuntimeError('simulated interruption')):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(probe.main(['--config',cfg]),2)
                self.assertEqual(requests.call_count,1)
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream):
                    rc=probe.main(['--config',cfg])
            self.assertEqual(rc,0)
            self.assertEqual(source.call_count,1)
            self.assertEqual(requests.call_count,1)
            self.assertEqual(json.loads(stream.getvalue())['workflow_status'],'COMPLETE')

    def test_read_only_check_preserves_old_parameters(self):
        with patch.object(probe,'_legacy_main',return_value=0) as old:
            self.assertEqual(probe.main(['--check','--source-url','https://docs.qq.com/sheet/Demo?tab=abc']),0)
            self.assertIn('--check',old.call_args.args[0])

if __name__ == '__main__':
    unittest.main()