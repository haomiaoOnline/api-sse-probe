"""Feishu Sheet transport + nine-column parity, offline only."""
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
from lib import feishu_sheet, unified_workflow

HEAD=['序号','场景','类别','语料','判断依据（公共）','特殊判断依据','研发负责人','deviceId','响应结果']
ROWS=[HEAD,['1','音乐','查询','播放周杰伦的歌','HTTP 200;文本回复','回复中包含“周杰伦”','','DEVICE_TEST',''],['2','提醒','设置','明天提醒开会','HTTP 200','title是“灵犀定时任务”','','DEVICE_TEST','']]
COLS=dict(case_id='序号',scene='场景',category='类别',content='语料',expected_keywords='判断依据（公共）',special_keywords='特殊判断依据',deviceId='deviceId')
CONFIG=dict(doc_url='https://workspace.feishu.cn/sheets/shtTest987?sheet=abc123',range='A1:I20',header_row=1,columns=COLS)

class Reply:
    status=200
    def __init__(self,data):self.data=json.dumps(data).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n=-1):return self.data[:n]

class FeishuSourceTests(unittest.TestCase):
    def test_sheet_url_validation_and_reject_untrusted(self):
        self.assertEqual(feishu_sheet.identity(CONFIG),('shtTest987','abc123'))
        for url in ('http://workspace.feishu.cn/sheets/shtTest987?sheet=abc123','https://other.example.com/sheets/shtTest987?sheet=abc123','https://workspace.feishu.cn/other/shtTest987'):
            with self.subTest(url=url):
                with self.assertRaises(feishu_sheet.FeishuSheetError):
                    feishu_sheet.identity(dict(CONFIG,doc_url=url))

    def test_read_values_uses_official_api_and_preserves_nine_columns(self):
        calls=[]
        class Opener:
            def open(self,req,timeout):
                calls.append((req.full_url,req.get_method()))
                if 'tenant_access_token' in req.full_url:
                    return Reply({'code':0,'tenant_access_token':'test-token'})
                return Reply({'code':0,'data':{'valueRange':{'values':ROWS}}})
        values=feishu_sheet.read_values(CONFIG,auth={'app_id':'id','app_secret':'secret'},opener=Opener())
        self.assertEqual(values,ROWS)
        self.assertIn('/sheets/v2/spreadsheets/shtTest987/values/abc123!A1:I20',calls[1][0])
        src={'type':'feishu','feishu':CONFIG}
        with patch.object(feishu_sheet,'read_values',return_value=ROWS):
            cases=probe.read_cases(src,feishu_docs_auth={'app_id':'id','app_secret':'secret'})
        self.assertEqual([c['case_id'] for c in cases],['1','2'])
        self.assertEqual(cases[0]['expected_keywords'],['HTTP 200','文本回复','回复中包含“周杰伦”'])
        self.assertEqual(cases[1]['scene'],'提醒')

    def test_cloud_blank_rows_are_ignored_not_reported_as_invalid_cases(self):
        padded=ROWS+[[None]*9 for _ in range(497)]
        with patch.object(feishu_sheet,'read_values',return_value=padded):
            stats={}
            cases=probe.read_cases({'type':'feishu','feishu':CONFIG},
                                   feishu_docs_auth={'app_id':'fake','app_secret':'fake'},stats=stats)
        self.assertEqual(len(cases),2)
        self.assertEqual(stats['source_rows'],2)
        self.assertEqual(stats['skipped_rows'],0)
        self.assertEqual(stats['skipped_missing_device_id'],0)

    def test_private_credentials_file_with_fullwidth_colon(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'feishu.md'
            path.write_text('appid：cli_FAKE_TEST_ID\nappSecret：FAKE_TEST_SECRET\n')
            class Opener:
                def open(self,req,timeout):
                    supplied=json.loads(req.data)
                    self_actual=supplied
                    assert self_actual['app_id']=='cli_FAKE_TEST_ID'
                    assert self_actual['app_secret']=='FAKE_TEST_SECRET'
                    return Reply({'code':0,'tenant_access_token':'dummy'})
            token=feishu_sheet._auth({'credentials_file':str(path)},opener=Opener())
            self.assertEqual(token,'dummy')

    def test_check_cli_credentials_path_only_used_for_feishu(self):
        from unittest.mock import ANY
        with tempfile.TemporaryDirectory() as folder:
            cfgfile=Path(folder)/'private.json'
            cfgfile.write_text(json.dumps({'source':{'type':'feishu','feishu':CONFIG}}))
            with patch.object(feishu_sheet,'read_values',return_value=ROWS) as reader:
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    status=probe.main(['--config',str(cfgfile),'--check','--source','feishu',
                                      '--feishu-credentials-file','/tmp/PRIVATE_APP_FILE'])
            self.assertEqual(status,0)
            self.assertEqual(reader.call_args.kwargs['auth']['credentials_file'],'/tmp/PRIVATE_APP_FILE')
            self.assertNotIn('PRIVATE_APP_FILE',out.getvalue())

    def test_no_feishu_credentials_override_for_non_feishu_source(self):
        from test_unified_entry import UnifiedEntryTests
        with tempfile.TemporaryDirectory() as folder:
            cfg=Path(folder)/'config.json'
            cfg.write_text(json.dumps({'api':{'url':'https://example.test/api',
                'headers':{'powerkey':'fake','signature':'fake'}},
                'source':{'type':'csv','path':'assets/sample_cases.csv'},
                'feishu':{'enabled':False},'reports':{'dir':str(Path(folder)/'r')}}))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(probe.main(['--config',str(cfg),
                      '--feishu-credentials-file','/tmp/NON_FEISHU']),2)

    def test_check_cli_reads_feishu_without_business_post_or_notification(self):
        with tempfile.TemporaryDirectory() as folder:
            cfgfile=Path(folder)/'private.json'
            cfgfile.write_text(json.dumps({'source':{'type':'feishu','feishu':CONFIG},
                                           'feishu_docs_auth':{'app_id':'id','app_secret':'secret'}}))
            with patch.object(feishu_sheet,'read_values',return_value=ROWS), \
                 patch.object(probe,'run_case',side_effect=AssertionError('business POST forbidden')):
                out=io.StringIO()
                with contextlib.redirect_stdout(out):
                    status=probe.main(['--config',str(cfgfile),'--check','--source','feishu'])
            self.assertEqual(status,0)
            result=json.loads(out.getvalue())
            self.assertEqual(result['enabled_cases'],2)
            self.assertEqual(result['business_posts'],0)
            self.assertEqual(result['feishu_messages'],0)

    def test_feishu_link_override_reuses_nine_column_mapping(self):
        prior={'source':{'type':'tencent','tencent':{'range':'A1:I20','header_row':1,
                'columns':COLS,'doc_url':'https://docs.qq.com/sheet/Old?tab=old'}},
               'feishu_docs_auth':{'app_id':'private','app_secret':'private'}}
        from argparse import Namespace
        source=unified_workflow._source(prior,Namespace(source=None,cases=None,
                            case_id=None,source_url=CONFIG['doc_url']))
        self.assertEqual(source['type'],'feishu')
        self.assertEqual(source['feishu']['columns'],COLS)
        self.assertEqual(source['feishu']['sheet_id'],'abc123')
        self.assertNotIn('feishu_docs_auth',json.dumps(source))
        self.assertEqual(prior['source']['type'],'tencent')

    def test_private_keys_in_source_are_rejected_before_snapshot(self):
        from argparse import Namespace
        bad={'source':{'type':'feishu','feishu':dict(CONFIG,app_secret='MUST_NOT_FREEZE')}}
        with self.assertRaisesRegex(ValueError,'credentials_must_be_private'):
            unified_workflow._source(bad,Namespace(source=None,cases=None,case_id=None,source_url=None))
        safe={'source':{'type':'feishu','feishu':dict(CONFIG,doc_url=CONFIG['doc_url']+'&access_token=NEVER_SAVE')}}
        normalized=unified_workflow._source(safe,Namespace(source=None,cases=None,case_id=None,source_url=None))
        self.assertNotIn('access_token',json.dumps(normalized))

    def test_reject_source_url_type_conflict(self):
        from argparse import Namespace
        with self.assertRaisesRegex(ValueError,'source_url_source_type_mismatch'):
            unified_workflow._source({'source':{'type':'tencent'}},
                Namespace(source='tencent',cases=None,case_id=None,source_url=CONFIG['doc_url']))

    def test_feishu_source_link_safe_and_card_labels_correct(self):
        from lib.probe_group_report import source_link_from_config,report_label
        from lib.probe_notice_adapter import probe_notice
        from lib.feishu_notifier import render_card_v2
        source={'type':'feishu','feishu':dict(CONFIG,doc_url=CONFIG['doc_url']+'&secret=NEVER_FORWARD')}
        link=source_link_from_config(source)
        self.assertEqual(link,CONFIG['doc_url'])
        self.assertNotIn('secret',link)
        self.assertTrue(report_label('feishu').startswith('飞书电子表格'))
        notice=probe_notice({'total':2,'pass':2,'fail':0,'error':0,'pass_rate':100},[],[],source_url=link)
        card=render_card_v2(notice)['card']
        self.assertIn('打开飞书电子表格',json.dumps(card,ensure_ascii=False))

    def test_private_credential_never_in_frozen_source(self):
        auth={'app_id':'superprivate','app_secret':'evenmoreprivate'}
        with tempfile.TemporaryDirectory() as folder:
            cfgfile=Path(folder)/'private.json'
            cfgfile.write_text(json.dumps({'api':{'url':'https://example.test/api','headers':{'powerkey':'safe','signature':'safe'}},'source':{'type':'feishu','feishu':CONFIG},'feishu_docs_auth':auth,'reports':{'dir':str(Path(folder)/'reports')},'feishu':{'enabled':False}}))
            with patch.object(probe,'read_cases',return_value=[{'case_id':'1','content':'test','deviceId':'device','expected_keywords':['HTTP 200'],'forbidden_keywords':[], 'match_mode':'all','endpoint':{},'session':{}}]) as reader:
                from lib import v2_engine
                with patch.object(v2_engine,'capture',side_effect=lambda store,cfg,*a:{'plan':store.plan(),'processed_this_batch':0}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(probe.main(['--config',str(cfgfile)]),0)
            self.assertEqual(reader.call_args.kwargs['feishu_docs_auth'],auth)
            snapshots=list((Path(folder)/'reports'/'v2-case-snapshots').glob('*.json'))
            self.assertEqual(len(snapshots),1)
            raw=snapshots[0].read_text()
            self.assertNotIn('superprivate',raw)
            self.assertNotIn('evenmoreprivate',raw)

if __name__=='__main__':unittest.main()