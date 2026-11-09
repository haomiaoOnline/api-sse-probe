"""Explicit opt-in retry-once of indeterminate dispatches, no live API."""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.v2_repository import RunStore
from lib.case_snapshot import freeze_cases
from lib.v2_engine import capture
from test_v2_reliability import CASE,CASE2,fake_capture

class RetryOnce(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)
        self.snapshot,_=freeze_cases([CASE,CASE2],source={'type':'csv','path':'fixture'},
                                    source_stats={},directory=p/'snap')
        self.api={'url':'https://example.invalid','headers':{'powerkey':'fake','signature':'fake'}}
        self.cfg={'api':self.api,'feishu':{'enabled':False},
                  'reports':{'dir':str(p/'reports')},
                  'report_outputs':[{'type':'file','enabled':True}]}
        self.parent=p/'runs'

    def store(self,policy):
        store=RunStore.create(self.parent,self.snapshot,self.api,retry_policy=policy)
        self.addCleanup(store.close)
        return store

    def test_one_retry_preserves_old_attempt_and_new_response(self):
        store=self.store({'enabled':True,'max_retries_per_case':1})
        previous=store.intent('1')
        with patch('lib.v2_engine.probe.run_case',side_effect=fake_capture) as post:
            result=capture(store,self.cfg,max_cases=1)
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['plan']['captured'],['1'])
        self.assertEqual(result['plan']['pending'],['2'])
        self.assertEqual(result['plan']['unknown'],[])
        history=store.retry_history()
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['prior_attempt_id'],previous)
        self.assertNotEqual(history[0]['retry_attempt_id'],previous)
        envelope,raw=store.evidence(store.rows()[0])
        self.assertEqual(envelope['attempt_id'],history[0]['retry_attempt_id'])
        self.assertTrue(raw)

    def test_second_unknown_not_retried(self):
        store=self.store({'enabled':True,'max_retries_per_case':1})
        store.intent('1')
        store.queue_unknown_retry('1')
        with patch('lib.v2_engine.probe.run_case',side_effect=fake_capture) as post:
            result=capture(store,self.cfg)
        self.assertEqual(post.call_count,1) # only pending #2
        self.assertEqual(result['plan']['unknown'],['1'])
        self.assertEqual(len(store.retry_history()),1)
        self.assertEqual(result['unknown_requests_not_replayed'],1)

    def test_existing_run_without_optin_still_no_retry(self):
        store=self.store(None)
        previous=store.intent('1')
        with patch('lib.v2_engine.probe.run_case',side_effect=fake_capture) as post:
            result=capture(store,self.cfg)
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['plan']['unknown'],['1'])
        self.assertEqual(store.rows()[0][3],previous)
        self.assertFalse(store.retry_history())

    def test_disallow_retry_if_evidence_from_prior_intent_exists(self):
        store=self.store({'enabled':True,'max_retries_per_case':1})
        previous=store.intent('1')
        folder=store.directory/'evidence';folder.mkdir(exist_ok=True)
        (folder/(previous+'.json')).write_text('preserved')
        with self.assertRaisesRegex(ValueError,'possible_committed_evidence'):
            store.queue_unknown_retry('1')
        self.assertEqual(store.rows()[0][3],previous)
        self.assertEqual(store.retry_history(),[])

    def test_invalid_retry_count_rejected(self):
        with self.assertRaisesRegex(ValueError,'max_one'):
            self.store({'enabled':True,'max_retries_per_case':2})

if __name__=='__main__':unittest.main()