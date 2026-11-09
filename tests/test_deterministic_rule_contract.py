"""Ambiguous prompts fail during case preflight; existing typed rules stay fixed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe
from lib.rule_contract import validate_rule
from lib.assertion_rules import evaluate_assertions

BASE={'case_id':'1','scene':'音乐','content':'测试音乐','deviceId':'dummy','expected_keywords':'HTTP 200;文本回复','special_keywords':'回复中包含“飞机”和（“500”或“预算”）'}

class ContractTests(unittest.TestCase):
    def test_standard_rules_compile_without_llm(self):
        for text in ('HTTP 200','文本回复','衍生问题','title是“灵犀定时任务”','title包含“关爱”或“健康”',
                     '回复中包含“飞机”和（“500”或“预算”）',
                     '{"all":[{"scope":"text","op":"contains","value":"飞机"}]}','机票'):
            with self.subTest(rule=text):self.assertTrue(validate_rule(text))

    def test_vague_and_malformed_rules_reject_before_network(self):
        for text in ('回复应理解用户意图','回复与业务场景语义相关','HTTP 20O','回复中包含飞机和500',
                     'title是“未闭合','回复围绕飞机票','回复应该合理'):
            with self.subTest(rule=text):
                with self.assertRaises(ValueError):validate_rule(text)

    def test_deterministic_and_or_does_not_accept_partial(self):
        requirement='回复中包含“飞机”和（“500”或“预算”）'
        for response,correct in [('飞机出行，预算如下',True),('飞机票约500元',True),
                                 ('飞机票价格如下',False),('车票500元',False)]:
            with self.subTest(response=response):
                signals={'answer_text_fragments':[response], 'business_records':[{'text':response}],
                         'answer_titles':[], 'valid_followup_questions':False}
                matched,missing,_=evaluate_assertions([requirement],[],200,signals)
                self.assertEqual(bool(matched),correct)

    def test_preflight_message_identifies_case_and_specific_column_without_utterance(self):
        row=dict(BASE,case_id='CASE-42',special_keywords='回复应理解用户意图')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.json'
            path.write_text(json.dumps([row],ensure_ascii=False))
            with self.assertRaises(ValueError) as caught:
                probe.read_cases({'type':'json','path':str(path)})
            message=str(caught.exception)
            self.assertIn('CASE-42',message)
            self.assertIn('特殊判断依据',message)
            self.assertNotIn(row['content'],message)

    def test_forbidden_words_are_literal_not_typed_predicates(self):
        row=dict(BASE,forbidden_keywords='回复')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.json'
            path.write_text(json.dumps([row],ensure_ascii=False))
            cases=probe.read_cases({'type':'json','path':str(path)})
            self.assertEqual(cases[0]['forbidden_keywords'],['回复'])

    def test_unsupported_rule_fails_case_import_before_requests(self):
        row=dict(BASE,special_keywords='回复应理解用户意图')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cases.json';path.write_text(json.dumps([row],ensure_ascii=False))
            with self.assertRaisesRegex(ValueError,'无法确定性解释|请改写'):
                probe.read_cases({'type':'json','path':str(path)})

if __name__=='__main__':unittest.main()