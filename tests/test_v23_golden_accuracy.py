"""Human-labeled synthetic golden set for deterministic judgement accuracy.

No production utterances, network calls, credentials, model services, or
historical private SSE bytes are included. Every example has an explicit label.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.v2_rejudge import judge_case
from lib.rule_contract import compile_rule, compile_case_rules, RULE_ENGINE_VERSION


def frame(data, **extra):
    return {'choices':[{'delta':{'content':{'data':data}}}], **extra}


def judge(rules, frames, *, forbidden=(), http=200, done=True):
    raw = ''.join('event: message\ndata: '+json.dumps(f,ensure_ascii=False)+'\n\n'
                  for f in frames)
    if done:
        raw+='data: [DONE]\n\n'
    case={'case_id':'synthetic','content':'SYNTHETIC_REQUEST','deviceId':'SYNTHETIC_DEVICE',
          'expected_keywords':rules,'forbidden_keywords':list(forbidden),
          'match_mode':'all'}
    envelope={'result':{'case_id':'synthetic','status':'CAPTURED','reason':'capture_only',
                         'http_status':http},
              'capture_metadata':{'response':{'status':http,'content_type':'text/event-stream',
                                               'body_kind':'sse','capture_state':'protocol_done'}}}
    return judge_case(case,envelope,raw.encode())


class GoldenRules(unittest.TestCase):
    @staticmethod
    def cases():
        return [
            # Name, typed rule, response frames, human-verified outcome.
            ('text_conjunction_pass',['回复中包含“飞机”和（“500”或“预算”）'],
             [frame({'text':'乘坐飞机，预算为480元。'})],'PASS'),
            ('text_conjunction_missing_budget',['回复中包含“飞机”和（“500”或“预算”）'],
             [frame({'text':'乘坐飞机很方便。'})],'FAIL'),
            ('text_conjunction_missing_flight',['回复中包含“飞机”和（“500”或“预算”）'],
             [frame({'text':'车票预算500元。'})],'FAIL'),
            ('title_not_same_as_text',['title是“日程服务”'],
             [frame({'text':'日程服务已经完成','title':'其他标题'})],'FAIL'),
            ('title_exact_pass',['title是“日程服务”'],
             [frame({'text':'已经设置提醒','title':'日程服务'})],'PASS'),
            ('metadata_is_not_business_text',['回复中包含“目标词”'],
             [frame({'text':'没有相关内容'},reqId='目标词')],'FAIL'),
            ('questions_not_answer_text',['回复中包含“黄金词”'],
             [frame({'text':'没有该信息','questions':['黄金词是什么？']})],'FAIL'),
            ('questions_structural_pass',['衍生问题'],
             [frame({'text':'询问后续问题','questions':['需要更多帮助吗？']})],'PASS'),
            ('questions_structural_empty',['衍生问题'],
             [frame({'text':'询问后续问题','questions':[]})],'FAIL'),
            ('same_frame_split_false',['{"same_frame":[{"scope":"data","path":"foo","op":"equals","value":"A"},{"scope":"data","path":"bar","op":"equals","value":"B"}]}'],
             [frame({'foo':'A'}),frame({'bar':'B'})],'FAIL'),
            ('same_frame_both_pass',['{"same_frame":[{"scope":"data","path":"foo","op":"equals","value":"A"},{"scope":"data","path":"bar","op":"equals","value":"B"}]}'],
             [frame({'foo':'A','bar':'B'})],'PASS'),
            ('numeric_under_limit',['{"scope":"data","path":"price","op":"lte","value":500}'],
             [frame({'price':499})],'PASS'),
            ('numeric_outside_limit',['{"scope":"data","path":"price","op":"lte","value":500}'],
             [frame({'price':501})],'FAIL'),
            ('numeric_string_not_implicit_float',['{"scope":"data","path":"price","op":"lte","value":500}'],
             [frame({'price':'490'})],'FAIL'),
            ('absence_true',['{"not":{"scope":"data","path":"redirect","op":"exists"}}'],
             [frame({'text':'完成'})],'PASS'),
            ('absence_false',['{"not":{"scope":"data","path":"redirect","op":"exists"}}'],
             [frame({'redirect':'target'})],'FAIL'),
            ('forbidden_literal',['HTTP 200'],[frame({'text':'包含禁止词'})],'FAIL'),
            ('http_mismatch',['HTTP 201'],[frame({'text':'完成'})],'FAIL'),
            ('text_not_nonempty',['文本回复'],[frame({'text':''})],'FAIL'),
            ('text_nonempty',['文本回复'],[frame({'text':'完成'})],'PASS'),
        ]

    def test_accuracy_reproducibility_and_audit(self):
        tp=tn=fp=fn=0
        for name,rules,frames,label in self.cases():
            with self.subTest(name=name):
                kwargs={'forbidden':['禁止词']} if name=='forbidden_literal' else {}
                first=judge(rules,frames,**kwargs)
                second=judge(copy.deepcopy(rules),copy.deepcopy(frames),**kwargs)
                self.assertEqual(first['status'],label)
                self.assertEqual((first['status'],first.get('assertion_trace')),
                                 (second['status'],second.get('assertion_trace')))
                self.assertEqual(first['assertion_engine_version'],RULE_ENGINE_VERSION)
                self.assertEqual(first['assertion_evaluation_status'],'EVALUATED')
                self.assertEqual(len(first['assertion_trace']),len(rules))
                for trace in first['assertion_trace']:
                    self.assertEqual(len(trace['rule_sha256']),64)
                    self.assertIn(trace['passed'],(True,False))
                    self.assertNotIn('SYNTHETIC_REQUEST',str(trace))
                verdict=first['status']=='PASS'
                expected=label=='PASS'
                tp+=int(verdict and expected)
                tn+=int(not verdict and not expected)
                fp+=int(verdict and not expected)
                fn+=int(not verdict and expected)
        self.assertEqual(fp,0)
        self.assertEqual(fn,0)
        self.assertEqual(tp+tn,len(self.cases()))

    def test_no_sse_done_returns_not_evaluated_not_false_pass(self):
        res=judge(['文本回复'],[frame({'text':'完整回答'})],done=False)
        self.assertEqual(res['status'],'FAIL') # v2-compatible transport outcome
        self.assertEqual(res['reason'],'done_missing')
        self.assertEqual(res['assertion_evaluation_status'],'NOT_EVALUATED')
        self.assertEqual(res['assertion_trace'],[])

    def test_unrecognized_rule_never_falls_back_to_pass(self):
        result=judge(['HTTP 200','回复应理解用户意图'],[frame({'text':'回复应理解用户意图'})])
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['reason'],'invalid_assertion')
        self.assertEqual(result['assertion_evaluation_status'],'INVALID_RULE')
        self.assertEqual([node['kind'] for node in result['assertion_trace']],
                         ['public','invalid'])

    def test_invalid_rule_in_any_mode_cannot_be_hidden_by_http_pass(self):
        frames=[frame({'text':'测试用语'})]
        raw=('data: '+json.dumps(frames[0],ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
        case={'case_id':'synthetic-any','content':'request','deviceId':'safe',
              'expected_keywords':['HTTP 200','回复应理解用户意图'],
              'forbidden_keywords':[],'match_mode':'any'}
        env={'result':{'case_id':'synthetic-any','status':'CAPTURED',
                       'reason':'capture_only','http_status':200},
             'capture_metadata':{'response':{'body_kind':'sse','capture_state':'protocol_done'}}}
        result=judge_case(case,env,raw)
        self.assertEqual(result['status'],'FAIL')
        self.assertEqual(result['reason'],'invalid_assertion')
        self.assertEqual(result['assertion_evaluation_status'],'INVALID_RULE')

    def test_rule_compilation_hash_stable_and_ambiguous_rejected(self):
        rules=['回复中包含“飞机”和（“500”或“预算”）',
               '{"scope":"data","path":"price","op":"lte","value":500}']
        left=compile_case_rules({'expected_keywords':rules})
        right=compile_case_rules({'expected_keywords':copy.deepcopy(rules)})
        self.assertEqual(left,right)
        for rule in ('回复应理解用户意图','回复中包含飞机'):
            with self.assertRaises(ValueError):compile_rule(rule)

if __name__=='__main__': unittest.main()