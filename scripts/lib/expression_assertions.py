"""Validated boolean assertions over assistant output; no metadata/request search."""
# 【布尔表达式引擎】all=AND，any=OR，not=NOT，same_frame=必须同一条 data 记录。
# scope=text/title/content/data 决定从哪里取值；禁止扫描用户请求或响应元数据。
# contains 是纯子串包含：“无法暂停播放”包含“暂停”即 True，与句子语义无关。
import json
import math
import re

_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\[(?:\d+|\*)\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[(?:\d+|\*)\])?)*\Z")
_OPS = {'exists', 'nonempty', 'equals', 'contains', 'prefix', 'in', 'gte', 'lte', 'count_gte'}


def parse_expression(text):
    if not isinstance(text, str) or not text.lstrip().startswith('{'):
        return None
    try:
        rule = json.loads(text)
    except ValueError as exc:
        raise ValueError('断言 JSON 格式错误') from exc
    validate_expression(rule)
    return rule


# 对 AST 的运算符、字段路径和嵌套深度做严格校验，未知节点不得静默跳过。
def validate_expression(rule, depth=0, in_frame=False):
    if depth > 16 or not isinstance(rule, dict):
        raise ValueError('断言应为对象且嵌套不超过16层')
    groups = set(rule) & {'all', 'any', 'not', 'same_frame'}
    if groups:
        if len(rule) != 1 or len(groups) != 1:
            raise ValueError('逻辑节点只能包含一个 all/any/not/same_frame')
        key = next(iter(groups))
        children = [rule[key]] if key == 'not' else rule[key]
        if not isinstance(children, list) or not children or len(children) > 100:
            raise ValueError('逻辑分组应包含1到100个条件')
        if in_frame and key == 'same_frame':
            raise ValueError('same_frame 不允许嵌套')
        for child in children:
            validate_expression(child, depth + 1, in_frame or key == 'same_frame')
        return
    scope, op = rule.get('scope'), rule.get('op')
    if not isinstance(scope, str) or not isinstance(op, str) or scope not in {'text', 'title', 'content', 'data'} or op not in _OPS:
        raise ValueError('未知断言 scope 或 op')
    if in_frame and scope != 'data':
        raise ValueError('same_frame 仅允许 data 条件')
    expected_keys = {'scope', 'op'}
    if scope == 'data':
        expected_keys.add('path')
        if not isinstance(rule.get('path'), str) or not _PATH.fullmatch(rule['path']):
            raise ValueError('data.path 格式错误')
    if op not in {'exists', 'nonempty'}:
        expected_keys.add('value')
        value = rule.get('value')
        if op in {'contains', 'prefix'} and (not isinstance(value, str) or not value):
            raise ValueError('contains/prefix 需要非空字符串')
        if op == 'in' and (not isinstance(value, list) or not value):
            raise ValueError('in 需要非空数组')
        if op in {'gte', 'lte', 'count_gte'} and (
                type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('数值比较需要有限数字')
        if op == 'count_gte' and (type(value) is not int or value < 0):
            raise ValueError('count_gte 需要非负整数')
    if set(rule) != expected_keys:
        raise ValueError('断言缺少必要字段或包含未知字段')


def _decode(value, depth=0):
    if depth > 24:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ('jsonBody', 'jsonHeader') and isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    pass
            result[key] = _decode(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_decode(item, depth + 1) for item in value]
    return value


# 收集单条助手业务记录，同时保留 choice_index，避免跨候选答案错误拼接文本。
def capture_record(data, choice_index, signals):
    record = _decode(data)
    signals.setdefault('business_records', []).append(record)
    texts = signals.setdefault('choice_texts', {})
    if isinstance(data.get('text'), str):
        texts.setdefault(choice_index, []).append(data['text'])


def path_values(record, path):
    values = [record]
    for segment in path.split('.'):
        match = re.fullmatch(r'([^\[]+)(?:\[(\d+|\*)\])?', segment)
        key, index = match.groups()
        values = [obj[key] for obj in values if isinstance(obj, dict) and key in obj]
        if index is not None:
            values = [item for obj in values if isinstance(obj, list)
                      for item in (obj if index == '*' else obj[int(index):int(index) + 1])]
    return values


def _equal(left, right):
    # bool is a subclass of int; JSON true must not satisfy equals 1.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, list) or isinstance(right, list):
        return isinstance(left, list) and isinstance(right, list) and len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) or isinstance(right, dict):
        return isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys() and all(
            _equal(left[key], right[key]) for key in left)
    return left == right


def _matches(value, op, expected):
    if op == 'nonempty':
        return value is not None and (bool(value.strip()) if isinstance(value, str)
                                      else len(value) > 0 if isinstance(value, (dict, list)) else True)
    if op == 'equals':
        return _equal(value, expected)
    if op == 'in':
        return any(_equal(value, option) for option in expected)
    if op in {'contains', 'prefix'}:
        return isinstance(value, str) and (expected in value if op == 'contains' else value.startswith(expected))
    if op == 'count_gte':
        return isinstance(value, (list, dict, str)) and len(value) >= expected
    if op in {'gte', 'lte'}:
        return type(value) in (int, float) and math.isfinite(value) and (
            value >= expected if op == 'gte' else value <= expected)
    return False


# 递归求值：优先处理 same_frame，再处理 AND/OR/NOT，最后匹配字段叶子条件。
# 词“暂停”与“停止”并不自动同义；必须在用例条件中显式声明 OR。
def evaluate_expression(rule, signals, records=None):
    records = signals.get('business_records', []) if records is None else records
    if 'same_frame' in rule:
        attempts = [evaluate_expression({'all': rule['same_frame']}, signals, [record]) for record in records]
        passed = any(item['passed'] for item in attempts)
        def evidence_score(item):
            children = item.get('children', [])
            if children:
                return sum(evidence_score(child) for child in children)
            return 2 * int(item['passed']) + int(item.get('actual') != '未收到该字段')
        candidates = ([next(item for item in attempts if item['passed'])] if passed else
                      sorted(attempts, key=evidence_score, reverse=True)[:3])
        return {'passed': passed, 'node': 'same_frame',
                'actual': '同一 data 记录' if passed else '没有单条 data 记录满足全部条件；以下为最接近的最多3条候选',
                'children': candidates}
    for key in ('all', 'any', 'not'):
        if key in rule:
            children = [rule[key]] if key == 'not' else rule[key]
            results = [evaluate_expression(child, signals, records) for child in children]
            states = [item['passed'] for item in results]
            passed = all(states) if key == 'all' else any(states) if key == 'any' else not states[0]
            return {'passed': passed, 'node': key, 'children': results}
    scope, op = rule['scope'], rule['op']
    if scope == 'data':
        values = [value for record in records for value in path_values(record, rule['path'])]
    elif scope == 'text':
        choices = signals.get('choice_texts')
        values = [''.join(parts) for parts in choices.values()] if choices is not None else [''.join(signals.get('answer_text_fragments', []))]
        values = [value for value in values if value.strip()]
    elif scope == 'title':
        values = signals.get('answer_titles', [])
    else:
        values = [''.join(signals.get('answer_text_fragments', []))] + signals.get('background_contents', [])
        values = [value for value in values if value.strip()]
    passed = bool(values) if op == 'exists' else any(_matches(value, op, rule.get('value')) for value in values)
    return {'passed': passed, 'node': scope + '.' + rule.get('path', ''), 'op': op,
            'expected': rule.get('value', op), 'actual': repr(values[:3])[:400] if values else '未收到该字段'}


# 形成可追溯的真实字段观测结果和最多量预览，不调用 AI 做二次解释。
def expression_observation(rule, signals):
    result = evaluate_expression(rule, signals)
    def describe(node):
        text = node.get('actual', '')
        children = node.get('children', [])
        if children:
            text += ' [' + '; '.join(describe(child) for child in children
                                     if node.get('node') == 'not' or child['passed'] == node['passed']) + ']'
        label = node.get('node', '')
        if 'op' in node:
            label += ' {} {!r}'.format(node['op'], node['expected'])
        return (label + ': ' + text).strip()
    return {'operation': 'expression', 'path': 'assistant.data', 'expected': rule,
            'actual': describe(result)[:2000], 'passed': result['passed'], 'details': result}