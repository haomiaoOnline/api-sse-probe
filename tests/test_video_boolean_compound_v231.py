"""Real sheet rule shape, synthetic evidence: exact video ID + nested AND/OR."""
import sys
import json
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.rule_contract import compile_rule
from lib.assertion_rules import accumulate_signals,evaluate_assertions

SKILL = 'VIDEO_TEST_FIXTURE_001'
RULE = '包含“四川”和（“频道”或“卫视”或“台”），skillId 是“'+SKILL+'”'


def record(data):
    return {'choices':[{'delta':{'content':{'data':data}}}]}


def video(header=SKILL, body=SKILL, channel='四川卫视'):
    return record({'code':4145,'jsonHeader':json.dumps({'skillId':header}),
                   'jsonBody':json.dumps({'params':{'skillId':body,
                     'sdkplay':{'video':{'channelname':channel}}}},ensure_ascii=False)})


def check(*frames):
    signals={'answer_text_fragments':[],'answer_titles':[],'background_contents':[]}
    for frame in frames:accumulate_signals(frame,signals)
    found,missing,_=evaluate_assertions([RULE],[],200,signals)
    return bool(found),missing,signals

class NestedVideoRules(unittest.TestCase):
    def test_compiles_exact_boolean_video_rule(self):
        compiled=compile_rule(RULE)
        self.assertEqual(compiled['kind'],'video_compound')
        self.assertEqual(compiled['body']['skill_id'],SKILL)
        self.assertIn('all',compiled['body']['terms'])
        self.assertEqual(len(compiled['sha256']),64)

    def test_matching_video_and_any_channel_synonym_pass(self):
        for channel in ('四川频道','四川卫视','四川台'):
            with self.subTest(channel=channel):
                ok,missing,signals=check(video(channel=channel))
                self.assertTrue(ok);self.assertEqual(missing,[])
                self.assertEqual(signals['rule_trace'][0]['kind'],'video_compound')

    def test_no_matching_video_control_cannot_pass(self):
        for frames in ((record({'text':'四川卫视频道'}),),
                       (video(header='OTHER'),),
                       (video(body='OTHER'),),
                       (record({'text':'四川卫视频道'}),video(header='OTHER',body='OTHER'))):
            with self.subTest(frames=str(frames)[:40]):
                ok,_,_=check(*frames);self.assertFalse(ok)

    def test_nested_logic_raises_on_missing_terms(self):
        for channel in ('湖南频道','四川电视','四川新闻'):
            ok,missing,_=check(video(channel=channel))
            self.assertFalse(ok)
            self.assertIn(RULE,missing)

    def test_skill_must_match_in_same_control(self):
        ok,_,_=check(video(header=SKILL,body='OTHER'),video(header='OTHER',body=SKILL))
        self.assertFalse(ok)

    def test_does_not_match_user_question_or_unrelated_video_payload(self):
        ok,_,_=check(record({'questions':['四川卫视频道']}),video(channel='湖南卫视'))
        self.assertFalse(ok)
        ok,_,_=check(video(channel='湖南卫视'),video(header='OTHER',body='OTHER',channel='四川卫视'))
        self.assertFalse(ok)

    def test_rejects_unsafe_or_badly_quoted_compounds(self):
        for condition in ('包含“四川”和（“频道”或卫视），skillId 是“'+SKILL+'”',
                          '包含“四川”和（“频道”或“卫视”），skillId 是VIDEO',
                          '包含“四川”和（“频道”或“卫视”，skillId 是“'+SKILL+'”'):
            with self.subTest(rule=condition):
                with self.assertRaises(ValueError):compile_rule(condition)

if __name__=='__main__':unittest.main()