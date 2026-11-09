"""Safe typed business assertions for the real weather sheet row."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import probe
from lib.assertion_rules import expand_expected_keywords, evaluate_assertions, accumulate_signals
from test_probe import API, CASE, FakeOpener, response_lines

WEATHER_EXPECTED=['HTTP 200','衍生问题','“灵犀服务”“天气”，“出行建议”']

def f(data):
    return {'choices':[{'delta':{'content':{'type':'1','data':data}}}]}

class TypedAssertionTests(unittest.TestCase):
    def setUp(self):
        self.weather=dict(CASE, expected_keywords=WEATHER_EXPECTED)
        self.success=[
            f({'title':'灵犀服务天气'}),
            f({'questions':['今天适合出门吗？','会下雨吗？']}),
            f({'text':'今天建议出门带伞，注意保暖。'}),
        ]

    def run_weather(self, frames=None):
        return probe.run_case(self.weather,API,opener=FakeOpener(response_lines(
            *(self.success if frames is None else frames))))

    def test_expand_wholly_quoted_list_only(self):
        self.assertEqual(expand_expected_keywords(WEATHER_EXPECTED),
                         ['HTTP 200','衍生问题','灵犀服务','天气','出行建议'])
        self.assertEqual(expand_expected_keywords(['说明：“天气”正常']),
                         ['说明：“天气”正常'])
        self.assertEqual(expand_expected_keywords(['视频技能','视频技能']),['视频技能'])

    def test_actual_typed_expectations_pass(self):
        result=self.run_weather()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['reason'],'ok')
        self.assertEqual(result['matched_keywords'],
                         ['HTTP 200','衍生问题','灵犀服务','天气','出行建议'])
        self.assertEqual(result['missing_keywords'],[])
        self.assertTrue(result['done_received'])

    def test_question_only_is_not_travel_advice(self):
        frames=[f({'title':'灵犀服务天气'}),
                f({'questions':['出门需要带伞吗？','有没有出行建议？']}),
                f({'text':'今天多云，气温适中。'})]
        result=self.run_weather(frames)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('出行建议',result['missing_keywords'])
        self.assertNotIn('衍生问题',result['missing_keywords'])

    def test_no_structured_questions_fails_even_with_prose(self):
        frames=[f({'title':'灵犀服务天气'}),
                f({'text':'有衍生问题提示：建议出门带伞。'})]
        result=self.run_weather(frames)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('衍生问题',result['missing_keywords'])

    def test_empty_question_and_unrelated_suggestions_fail(self):
        frames=[f({'title':'灵犀服务天气'}),
                f({'questions':['','  ']}),
                f({'text':'建议多喝水，保持健康。'})]
        result=self.run_weather(frames)
        self.assertIn('衍生问题',result['missing_keywords'])
        self.assertIn('出行建议',result['missing_keywords'])

    def test_http_status_is_structural(self):
        signals={'valid_followup_questions':True,
                 'answer_text_fragments':['建议出门带伞']}
        found,missing,_=evaluate_assertions(WEATHER_EXPECTED,
                   ['灵犀服务','天气'],201,signals)
        self.assertIn('HTTP 200',missing)
        self.assertNotIn('HTTP 200',found)
        found,missing,_=evaluate_assertions(WEATHER_EXPECTED,
                   ['灵犀服务','天气','HTTP 200'],201,signals)
        self.assertIn('HTTP 200',missing)

    def test_split_streaming_advice_fragments(self):
        frames=[f({'title':'灵犀服务天气'}),
                f({'questions':['有什么天气提醒？']}),
                f({'text':'今天建议'}),
                f({'text':'出门带伞。'})]
        self.assertEqual(self.run_weather(frames)['status'],'PASS')

    def test_old_keyword_matching_unchanged(self):
        result=probe.run_case(CASE,API,opener=FakeOpener(response_lines(
            {'choices':[{'delta':{'content':{'data':{'text':'视频技能 VIDEO30BFF29645B2B2E2EB6C44AE7'}}}}]})))
        self.assertEqual(result['status'],'PASS')

    def test_require_done_still_enforced(self):
        frames=response_lines(*self.success,done=False)
        result=probe.run_case(self.weather,API,opener=FakeOpener(frames))
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['reason'],'done_missing')

if __name__=='__main__':
    unittest.main()