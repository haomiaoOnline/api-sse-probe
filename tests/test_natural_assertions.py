"""Explicit natural assertions bind types, values and boolean conditions."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import probe
from test_probe import API, CASE, FakeOpener, response_lines


def frame(data, **extra):
    return {'choices': [{'delta': {'content': {'data': data}}}], **extra}


def run(rule, *responses):
    case = dict(CASE, expected_keywords=['HTTP 200'] + probe.split_words(rule))
    return probe.run_case(case, API, opener=FakeOpener(response_lines(*responses)))


class ExplicitNaturalTests(unittest.TestCase):
    def test_reply_and_or_are_literal_and_independent_of_word_order(self):
        rule = '文本回复 / 回复中包含“杭州”和（“天气”或“气温”）'
        for text in ('明天杭州天气晴，27度。', '气温降低，杭州有雨。'):
            self.assertEqual(run(rule, frame({'text': text}))['status'], 'PASS')
        self.assertEqual(run(rule, frame({'text': '杭州适合游玩。'}))['status'], 'FAIL')
        self.assertEqual(run('回复中有“音量”或“指令已下达”', frame({'text': '指令已下达'}))['status'], 'PASS')
        self.assertEqual(run('回复中有“音量”和“指令已下达”', frame({'text': '指令已下达'}))['status'], 'FAIL')

    def test_title_and_reply_must_come_from_correct_fields(self):
        rule = 'title是“灵犀服务”，回复中包含“小猫”和“喂食”'
        self.assertEqual(run(rule, frame({'background': {'title': '灵犀服务'}, 'text': '喂食小猫'}))['status'], 'PASS')
        self.assertEqual(run(rule, frame({'title': '你可以继续问我', 'questions': ['灵犀服务 小猫 喂食'], 'text': '正常'}))['status'], 'FAIL')
        self.assertEqual(run('回复中包含“外卖”', frame({'background': {'title': '外卖'}, 'text': '正常'}))['status'], 'FAIL')
        self.assertEqual(run('title包含“外卖”', frame({'text': 'title 外卖'}))['status'], 'FAIL')

    def test_skill_id_requires_actual_business_fields(self):
        rule = '有skillId是“VIDEO_EXPECTED”'
        for data in ({'jsonHeader': '{"skillId":"VIDEO_EXPECTED"}'},
                     {'jsonBody': {'params': {'skillId': 'VIDEO_EXPECTED'}}},
                     {'skillId': 'VIDEO_EXPECTED'}):
            self.assertEqual(run(rule, frame(data))['status'], 'PASS')
        bad = frame({'text': 'VIDEO_EXPECTED', 'questions': ['VIDEO_EXPECTED']}, skillId='VIDEO_EXPECTED')
        self.assertEqual(run(rule, bad)['status'], 'FAIL')
        missing = run(rule, frame({'jsonHeader': {'skillId': 'OTHER'}}))
        self.assertEqual(missing['status'], 'FAIL')
        self.assertIn('OTHER', missing['failure_assertions'][0]['actual'])

    def test_present_header_and_body_skill_ids_must_agree(self):
        rule = 'skillId是“VIDEO_EXPECTED”'
        mismatch = {'jsonHeader': {'skillId': 'VIDEO_EXPECTED'}, 'jsonBody': {'params': {'skillId': 'OTHER'}}}
        self.assertEqual(run(rule, frame(mismatch))['status'], 'FAIL')
        self.assertEqual(run(rule, frame({'jsonHeader': {'skillId': 'VIDEO_EXPECTED'},
                                         'jsonBody': {'params': {'skillId': 'VIDEO_EXPECTED'}}}))['status'], 'PASS')

    def test_followup_information_is_structured_not_echoed_prose(self):
        self.assertEqual(run('有追问信息', frame({'questions': ['想看哪一部？']}))['status'], 'PASS')
        for data in ({'questions': ['', '  ']}, {'text': '有追问信息：想看哪部？'}):
            self.assertEqual(run('有追问信息', frame(data))['status'], 'FAIL')

    def test_public_types_do_not_substitute_for_each_other(self):
        rule = '文本回复 / SDK / 衍生问题'
        data = {'text': '正在打开', 'sdk': {'package': 'app.package'}, 'questions': ['继续？']}
        self.assertEqual(run(rule, frame(data))['status'], 'PASS')
        for key in ('text', 'sdk', 'questions'):
            self.assertEqual(run(rule, frame({k: v for k, v in data.items() if k != key}))['status'], 'FAIL')
        self.assertEqual(run('SDK', frame({'sdk': {}}))['status'], 'FAIL')

    def test_title_alternatives_and_punctuation_inside_quotes(self):
        self.assertEqual(run('title包含“健康”或“关爱”', frame({'background': {'title': '灵犀关爱，健康守护'}}))['status'], 'PASS')
        self.assertEqual(run('title是“灵犀外卖，点单无忧”，回复中有“想吃”或“吃什么”',
                             frame({'background': {'title': '灵犀外卖，点单无忧'}, 'text': '吃什么？'}))['status'], 'PASS')
        self.assertEqual(run('回复中包含“甲/乙;丙|丁”', frame({'text': '甲/乙;丙|丁'}))['status'], 'PASS')

    def test_streamed_fragments_and_forbidden_text(self):
        self.assertEqual(run('回复中有“湖南卫视”', frame({'text': '湖南'}), frame({'text': '卫视'}))['status'], 'PASS')
        self.assertEqual(run('回复中不包含“取消收藏”', frame({'text': '已经取消收藏'}))['status'], 'FAIL')
        self.assertEqual(run('回复中有“收藏”；回复中不包含“取消收藏”', frame({'text': '已收藏'}))['status'], 'PASS')

    def test_vague_rules_and_malformed_conditions_fail_before_requests(self):
        for bad in ('回复围绕“外卖”', '回复能体现“暂停”意图', '有有效业务回复',
                    '回复中包含杭州', '回复中包含“天气”和', '回复中包含（“天气”', 'skillId是'):
            with self.subTest(rule=bad), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'cases.csv'
                with path.open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=['case_id', 'content', 'deviceId', 'expected_keywords'])
                    writer.writeheader()
                    writer.writerow({'case_id': '1', 'content': '测试', 'deviceId': 'device', 'expected_keywords': bad})
                with self.assertRaises(ValueError):
                    probe.read_cases({'type': 'csv', 'path': str(path)})


if __name__ == '__main__':
    unittest.main()