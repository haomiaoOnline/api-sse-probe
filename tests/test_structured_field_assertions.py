"""Offline regression fixtures for quoted keywords and structured SSE predicates."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import probe
from lib.assertion_rules import accumulate_signals, evaluate_assertions, expand_expected_keywords
from lib.structured_assertions import parse_field_rule
from test_probe import CASE, API, FakeOpener, response_lines


def frame(data):
    return {"choices": [{"delta": {"content": {"type": "3", "data": data}}}]}


def run(expected, *frames):
    case = copy.deepcopy(CASE)
    case["case_id"] = "song"
    case["content"] = "播放林俊杰的江南"
    case["expected_keywords"] = expected
    return probe.run_case(case, API, opener=FakeOpener(response_lines(*frames)))


VIDEO_SKILL = 'VIDEO30BFF29645B2B2E2EB6C44AE7'
NEWS_RULE = '包含“新闻联播”“CCTV1”，skillId 是"{}"'.format(VIDEO_SKILL)
SICHUAN_RULE = '包含“四川卫视”，skillId 是"{}"'.format(VIDEO_SKILL)


def video_control(header_skill=VIDEO_SKILL, body_skill=VIDEO_SKILL, channel='四川卫视'):
    return frame({'code': 4145,
                  'jsonHeader': json.dumps({'skillId': header_skill}),
                  'jsonBody': json.dumps({'params': {'skillId': body_skill,
                      'sdkplay': {'video': {'channelname': channel}}}})})


class StructureAndQuoteTests(unittest.TestCase):
    def test_single_quoted_keyword_matches_without_quotes_in_response(self):
        for keyword in ('“机票”', '"机票"', "'机票'", "‘机票’", "「机票」", "《机票》"):
            with self.subTest(keyword=keyword):
                result = run([keyword], frame({"text": "机票找到了"}))
                self.assertEqual(result['status'], 'PASS')
                self.assertIn('机票', result['matched_keywords'])
        punctuated = run(['“机票”。'], frame({"text": "机票找到了"}))
        self.assertEqual(punctuated['status'], 'PASS')
        bad = run(['“机票”'], frame({"text": "仅查询酒店"}))
        self.assertEqual(bad['status'], 'FAIL')
        self.assertEqual(bad['missing_keywords'], ['机票'])
        self.assertEqual(bad['failure_assertions'][0]['condition'], '机票')

    def test_forbidden_quoted_keyword_is_not_ignored(self):
        case = copy.deepcopy(CASE)
        case['expected_keywords'] = ['文本回复']
        case['forbidden_keywords'] = ['“机票”']
        result = probe.run_case(case, API, opener=FakeOpener(response_lines(
            frame({'text': '这边有机票推荐'}))))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['forbidden_found'], ['机票'])
        self.assertEqual(result['failure_assertions'][0]['condition'], '机票')

    def test_quoted_lists_and_text_contains_are_backward_compatible(self):
        self.assertEqual(expand_expected_keywords(['“灵犀”“天气”']), ['灵犀', '天气'])
        self.assertEqual(expand_expected_keywords(['说明：“天气”正常']), ['说明：“天气”正常'])
        ok = run(["文本回复包含‘机票’"], frame({"text": "有直飞机票"}))
        self.assertEqual(ok['status'], 'PASS')
        self.assertEqual(run(["文本回复包含‘机票’"], frame({"questions": ["机票哪里买？"]}))['status'], 'FAIL')

    def test_deeplink_prefix_from_business_data_only(self):
        deep = "midmusic://migumusic?action=%7B%22type%22%3A%22JUMP_DETAIL_PAGE%22%7D"
        ok = run(['deeplink以midmusic开头',
                  'deeplink字段以midmusic://migumusic开头',
                  'deeplink字段以“midmusic://migumusic”开头'],
                 frame({"text": "为您播放江南"}), frame({"deeplink": deep, "issueMode": 1}))
        self.assertEqual(ok['status'], 'PASS')
        self.assertEqual(len(ok['matched_keywords']), 3)
        self.assertEqual(ok['missing_keywords'], [])
        missing = run(['deeplink以midmusic开头'], frame({"text": "deeplink以midmusic开头"}))
        self.assertEqual(missing['status'], 'FAIL')
        self.assertEqual(missing['failure_assertions'][0]['rule_type'], 'json_field_prefix')
        self.assertEqual(missing['failure_assertions'][0]['actual'], '未收到该字段')
        self.assertEqual(missing['failure_assertions'][0]['field_path'], 'deeplink')

    def test_field_prefix_diagnostic_includes_actual_value(self):
        failed = run(['deeplink以midmusic开头'], frame({"deeplink": "midvideo://player?id=8"}))
        self.assertEqual(failed['status'], 'FAIL')
        detail = failed['failure_assertions'][0]
        self.assertEqual(detail['expected'], 'midmusic')
        self.assertIn('midvideo://player', detail['actual'])
        self.assertEqual(detail['failure_reason'], 'structured_field_condition_not_met')

    def test_nested_sdk_package_exact_and_no_false_positive(self):
        rule = 'sdk的package是com.czly.tabletmusic'
        data = {"msgtxt": "", "sdk": {"package": "com.czly.tabletmusic",
                "name": "咪咕音乐", "extraParam": {"data": [], "issueMode": 1}}}
        self.assertEqual(run([rule, 'sdk.package等于com.czly.tabletmusic'], frame(data))['status'], 'PASS')
        missing = run([rule], frame({"text": rule, "sdk": {"package": "com.other.app"}}))
        self.assertEqual(missing['status'], 'FAIL')
        detail = missing['failure_assertions'][0]
        self.assertEqual(detail['field_path'], 'sdk.package')
        self.assertIn('com.other.app', detail['actual'])
        self.assertEqual(detail['rule_type'], 'json_field_equals')

    def test_nested_json_string_and_other_field_operators(self):
        result = run(['jsonBody.params.musicIds包含60058',
                      'sdk的package不为空',
                      'deeplink字段存在'],
                     frame({"jsonBody": json.dumps({"params": {"musicIds": ["60058600213"]}}),
                            "sdk": {"package": "com.czly.tabletmusic"},
                            "deeplink": "midmusic://migumusic"}))
        self.assertEqual(result['status'], 'PASS')
        self.assertIsNone(parse_field_rule('请根据理解判断音乐是否好听'))
        self.assertEqual(run(['sdk中的package应该是com.czly.tabletmusic。'],
                             frame({'sdk': {'package': 'com.czly.tabletmusic'}}))['status'], 'PASS')
        self.assertEqual(run(['deeplink字段应该以midmusic://migumusic开头。'],
                             frame({'deeplink': 'midmusic://migumusic?action=1'}))['status'], 'PASS')
        bad = run(['sdk.package不为空'], frame({"sdk": {"package": ""}}))
        self.assertEqual(bad['status'], 'FAIL')

    def test_missing_data_and_unrelated_metadata_cannot_pass(self):
        result = run(['deeplink以midmusic开头'], {'reqId': 'midmusic',
            'session': {'deeplink': 'midmusic://migumusic'},
            'choices': [{'delta': {'content': {'data': {'text': '普通回复'}}}}]})
        self.assertEqual(result['status'], 'FAIL')
        missing = run(['sdk的package是com.czly.tabletmusic'], frame({"deeplink": "com.czly.tabletmusic"}))
        self.assertEqual(missing['status'], 'FAIL')

    def test_compound_video_rule_accepts_answer_text_and_video_parameters(self):
        news = run(['HTTP 200', NEWS_RULE],
                   frame({'text': '已为您播放昨天CCTV1的《新闻联播》。'}),
                   video_control(channel='CCTV1综合频道'))
        sichuan = run(['HTTP 200', SICHUAN_RULE],
                      video_control(channel='四川卫视'))
        self.assertEqual(news['status'], 'PASS')
        self.assertEqual(sichuan['status'], 'PASS')

    def test_compound_video_rule_requires_real_video_instruction(self):
        questions = run([NEWS_RULE], frame({'questions': ['新闻联播 CCTV1']}))
        other_code = run([SICHUAN_RULE], frame({'text': '四川卫视'}),
                         frame({'code': 4133, 'jsonHeader': json.dumps({'skillId': VIDEO_SKILL}),
                                'jsonBody': json.dumps({'params': {'skillId': VIDEO_SKILL}})}))
        for result in (questions, other_code):
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['failure_assertions'][0]['rule_type'],
                             'json_field_video_compound')
            self.assertIn('未收到 code=4145', result['failure_assertions'][0]['actual'])

    def test_compound_video_rule_requires_exact_matching_header_and_body(self):
        for header_id, body_id in (('OTHER', VIDEO_SKILL), (VIDEO_SKILL, 'OTHER'),
                                   (None, VIDEO_SKILL), (VIDEO_SKILL, None)):
            with self.subTest(header=header_id, body=body_id):
                result = run([SICHUAN_RULE], video_control(header_id, body_id))
                self.assertEqual(result['status'], 'FAIL')
                self.assertIn('structured_field_condition_not_met',
                              result['failure_assertions'][0]['failure_reason'])

    def test_compound_video_rule_excludes_suggestions_and_unrelated_video(self):
        missing = run([NEWS_RULE], frame({'questions': ['新闻联播 CCTV1']}),
                      video_control(channel='四川卫视'))
        self.assertEqual(missing['status'], 'FAIL')
        self.assertIn('新闻联播', missing['failure_assertions'][0]['actual'])
        different_frame = run([SICHUAN_RULE], video_control('OTHER', 'OTHER'),
                              frame({'text': '四川卫视 VIDEO30BFF29645B2B2E2EB6C44AE7'}))
        self.assertEqual(different_frame['status'], 'FAIL')

    def test_title_content_compound_checks_real_answer_fields(self):
        rule = 'title是“灵犀服务”，内容包含“小猫”“喂食”'
        good = run([rule], frame({'background': {'title': '灵犀服务', 'content': '小猫'},
                                   'text': '记得喂食'}))
        self.assertEqual(good['status'], 'PASS')
        suggestions = run([rule], frame({'title': '你可以继续问我',
                                          'questions': ['小猫什么时候喂食']}),
                          frame({'background': {'title': '灵犀服务', 'content': ''},
                                 'text': '今天正常'}))
        self.assertEqual(suggestions['status'], 'FAIL')
        self.assertEqual(suggestions['failure_assertions'][0]['rule_type'],
                         'json_field_title_content')
        self.assertIn('小猫、喂食', suggestions['failure_assertions'][0]['actual'])
        wrong_title = run([rule], frame({'background': {'title': '陪伴闲聊'},
                                          'text': '小猫已经喂食'}))
        self.assertEqual(wrong_title['status'], 'FAIL')
        self.assertIn('陪伴闲聊', wrong_title['failure_assertions'][0]['actual'])

    def test_response_header_default_20_seconds_and_explicit_override(self):
        client = FakeOpener(response_lines(frame({"text": "机票"})))
        ok = probe.run_case({**CASE, 'expected_keywords': ['“机票”']}, API, opener=client)
        self.assertEqual(ok['status'], 'PASS')
        self.assertGreater(client.request is not None, False)
        # A separate injected opener spies on the actual header wait budget.
        class TimeoutSpy(FakeOpener):
            last_timeout = None
            def open(self, request, timeout):
                self.last_timeout = timeout
                return super().open(request, timeout)
        with_default = TimeoutSpy(response_lines(frame({"text": "机票"})))
        probe.run_case({**CASE, 'expected_keywords': ['机票']}, API, opener=with_default)
        self.assertGreater(with_default.last_timeout, 19.0)
        self.assertLessEqual(with_default.last_timeout, 20.0)
        override = TimeoutSpy(response_lines(frame({"text": "机票"})))
        probe.run_case({**CASE, 'expected_keywords': ['机票']},
                       {**API, 'response_header_timeout_seconds': 8}, opener=override)
        self.assertGreater(override.last_timeout, 7.0)
        self.assertLessEqual(override.last_timeout, 8.0)


if __name__ == '__main__':
    unittest.main()