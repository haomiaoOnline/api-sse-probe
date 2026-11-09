"""Consumer-visible boolean, field, source and stream assertion regressions."""
import copy
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import probe
from lib.expression_assertions import parse_expression
from test_probe import API, CASE, FakeOpener, response_lines


def frame(data, index=0, role=None):
    delta = {'content': {'data': data}}
    if role:
        delta['role'] = role
    return {'choices': [{'index': index, 'delta': delta}]}


def predicate(path, op='equals', value=1):
    return {'scope': 'data', 'path': path, 'op': op, **({} if op in ('exists', 'nonempty') else {'value': value})}


def run(rule, *frames):
    case = copy.deepcopy(CASE)
    case['expected_keywords'] = [json.dumps(rule, ensure_ascii=False)]
    return probe.run_case(case, API, opener=FakeOpener(response_lines(*frames)))


class ExpressionTests(unittest.TestCase):
    def test_nested_boolean_and_negated_failure_response(self):
        rule = {'all': [{'any': [{'scope': 'text', 'op': 'contains', 'value': '调大'},
                                 {'scope': 'text', 'op': 'contains', 'value': '增大'}]},
                        {'not': {'scope': 'text', 'op': 'contains', 'value': '失败'}}]}
        self.assertEqual(run(rule, frame({'text': '已经增大音量'}))['status'], 'PASS')
        failed = run(rule, frame({'text': '调大音量失败'}))
        self.assertEqual(failed['status'], 'FAIL')
        self.assertIn('失败', failed['failure_assertions'][0]['actual'])

    def test_same_frame_prevents_cross_instruction_matches(self):
        rule = {'same_frame': [predicate('code', value=4145), predicate('jsonHeader.skillId', value='video'),
                               predicate('jsonBody.params.channel', value='湖南卫视')]}
        wrong = [frame({'code': 4145, 'jsonHeader': '{"skillId":"video"}'}),
                 frame({'code': 4133, 'jsonBody': {'params': {'channel': '湖南卫视'}}})]
        self.assertEqual(run(rule, *wrong)['status'], 'FAIL')
        self.assertEqual(run(rule, frame({'code': 4145, 'jsonHeader': {'skillId': 'video'},
                         'jsonBody': '{"params":{"channel":"湖南卫视"}}'}))['status'], 'PASS')

    def test_same_frame_failure_reports_received_control_value(self):
        rule = {'same_frame': [predicate('code', value=4145), predicate('jsonHeader.skillId', value='video')]}
        result = run(rule, frame({'text': '正在下发'}), frame({'code': 4149}))
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('4149', result['failure_assertions'][0]['actual'])

    def test_empty_containers_null_zero_and_boolean_types(self):
        for value in ('', '  ', None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(run(predicate('field', 'nonempty'), frame({'field': value}))['status'], 'FAIL')
                self.assertEqual(run(predicate('field', 'exists'), frame({'field': value}))['status'], 'PASS')
        self.assertEqual(run(predicate('field'), frame({'field': True}))['status'], 'FAIL')
        self.assertEqual(run(predicate('field', value=0), frame({'field': 0}))['status'], 'PASS')
        self.assertEqual(run(predicate('field', 'exists'), frame({}))['status'], 'FAIL')
        for expected, actual in (([1], [True]), ({'code': 1}, {'code': True})):
            self.assertEqual(run(predicate('field', value=expected), frame({'field': actual}))['status'], 'FAIL')

    def test_array_paths_and_numeric_bounds(self):
        rule = {'all': [predicate('items', 'count_gte', 2), predicate('items[1].id', value='b'),
                        predicate('items[*].score', 'gte', 10), predicate('count', 'lte', 3)]}
        self.assertEqual(run(rule, frame({'items': [{'id': 'a', 'score': 5}, {'id': 'b', 'score': 10}], 'count': 3}))['status'], 'PASS')
        self.assertEqual(run(predicate('count', 'gte', 10), frame({'count': '20'}))['status'], 'FAIL')

    def test_fragment_join_excludes_other_choices_questions_and_user_role(self):
        rule = {'scope': 'text', 'op': 'contains', 'value': '湖南卫视'}
        self.assertEqual(run(rule, frame({'text': '湖南'}), frame({'text': '卫视'}))['status'], 'PASS')
        self.assertEqual(run(rule, frame({'text': '湖南'}, 0), frame({'text': '卫视'}, 1))['status'], 'FAIL')
        self.assertEqual(run(rule, frame({'questions': ['湖南卫视']}), frame({'text': '湖南卫视'}, role='user'))['status'], 'FAIL')
        title = {'scope': 'title', 'op': 'contains', 'value': '外卖'}
        self.assertEqual(run(title, frame({'questions': ['吃什么'], 'title': '外卖'}))['status'], 'FAIL')

    def test_legacy_or_and_quoted_delimiters(self):
        case = dict(CASE, expected_keywords=['文本回复 包含“音量”或者“指令已下达”'])
        self.assertEqual(probe.run_case(case, API, opener=FakeOpener(response_lines(frame({'text': '音量已调大'}))))['status'], 'PASS')
        self.assertEqual(probe.split_words('HTTP 200 / 文本回复包含“甲/乙;丙|丁”'), ['HTTP 200', '文本回复包含“甲/乙;丙|丁”'])
        self.assertEqual(probe.split_words('deeplink以cmcc://host/path开头'), ['deeplink以cmcc://host/path开头'])

    def test_invalid_expressions_rejected_during_case_load(self):
        invalid = [{'any': []}, {'all': [predicate('code')], 'extra': True},
                   {'scope': 'data', 'path': 'bad..path', 'op': 'exists'},
                   {'scope': 'text', 'op': 'contains', 'value': ''},
                   {'same_frame': [{'scope': 'text', 'op': 'exists'}]},
                   {'scope': 'text', 'op': 'nonempty', 'ignored': 1}]
        invalid.extend([{'scope': [], 'op': 'exists'}, {'scope': 'text', 'op': {}}])
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                parse_expression(json.dumps(rule))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cases.csv'
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['序号', '语料', 'deviceId', '判断依据（公共）', '特殊判断依据'])
                writer.writeheader()
                writer.writerow({'序号': '1', '语料': '测试', 'deviceId': 'device',
                                 '判断依据（公共）': 'HTTP 200', '特殊判断依据': '{"any":[]}'})
            with self.assertRaises(ValueError):
                probe.read_cases({'type': 'csv', 'path': str(path)})

    def test_chinese_nine_column_csv_is_direct_probe_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cases.csv'
            with path.open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['序号', '场景', '类别', '语料', '判断依据（公共）', '特殊判断依据', '研发负责人', 'deviceId', '响应结果'])
                writer.writerow(['41', '听书', '音乐', '听书', 'HTTP 200 / 文本回复', json.dumps(predicate('deeplink', 'prefix', 'cmcc://host/path')), '研发', 'device', '历史响应不得当作实际响应'])
            cases = probe.read_cases({'type': 'csv', 'path': str(path)})
            result = probe.run_case(cases[0], API, opener=FakeOpener(response_lines(frame({'text': '听书', 'deeplink': 'cmcc://host/path?id=1'}))))
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(cases[0]['scene'], '听书')

    def test_literal_expectation_cannot_match_metadata_or_questions(self):
        case = dict(CASE, expected_keywords=['唯一业务词'])
        response = frame({'questions': ['唯一业务词'], 'text': '正常回答'})
        response['model'] = '唯一业务词'
        self.assertEqual(probe.run_case(case, API, opener=FakeOpener(response_lines(response)))['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()