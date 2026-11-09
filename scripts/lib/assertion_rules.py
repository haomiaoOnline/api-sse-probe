"""Typed assertions for Tencent Docs natural-language test expectations.

Recognized field predicates are evaluated against parsed assistant business
JSON. Unknown prose uses conservative literal matching, never LLM guessing.
"""
# 【断言核心】先提取助手业务 data，再逐条运行已编译的断言，最后生成结构化判定证据。
# 不把用户输入、请求头、顶层 reqId 等非业务数据误当作响应命中。
import json
import re
from lib.structured_assertions import (
    capture_data_fields, check_field_rule, unquote_outer,
)
from lib.expression_assertions import capture_record, parse_expression, expression_observation
from lib.natural_assertions import check_natural_rule

_QUOTED = re.compile(r"""“([^”]+)”|"([^"]+)"|‘([^’]+)’|'([^']+)'|「([^」]+)」|『([^』]+)』""")
_TEXT_CONTAINS = re.compile(r'^文本回复\s*包含\s*(.+)$')
_VIDEO_COMPOUND = re.compile(
    r'^包含\s*(?P<terms>.+?)\s*[，,]\s*skillId\s*(?:是|为|等于)\s*'
    r'(?P<skill>.+?)\s*[。；;]?$', re.I)
_TITLE_CONTENT = re.compile(
    r'^title\s*(?:是|为|等于)\s*(?P<title>.+?)\s*[，,]\s*'
    r'内容\s*(?:包含|含有)\s*(?P<terms>.+?)\s*[。；;]?$', re.I)

_TRIP_CONTEXT = r'(?:出行|出门|外出|出游|通勤|带伞|雨伞|遮阳|防晒|穿衣|保暖)'
_DIRECTIVE = r'(?:建议|适合|宜|最好|注意|记得|可以|尽量|避免|别忘了|请)'
_EXPLICIT = re.compile(
    _DIRECTIVE + r'[^。！？\n]{0,40}' + _TRIP_CONTEXT
    + r'|' + _TRIP_CONTEXT + r'[^。！？\n]{0,40}' + _DIRECTIVE
)


def _quoted_terms(text):
    return [next(value for value in match.groups() if value is not None)
            for match in _QUOTED.finditer(text)]


def _quoted_list(text):
    terms = _quoted_terms(text)
    if terms and not _QUOTED.sub('', text).strip(' ，,、;；\t'):
        return [unquote_outer(term) for term in terms if unquote_outer(term)]
    return []

# “文本回复包含”规则：引号词组内多个词默认 AND；词组之间以“或”连接为 OR。
# 例：“暂停”或“停止”任选其一；“暂停”“停止”需要两个词均出现。
# 只做字面检查，不识别否定：“无法暂停播放”确实包含“暂停”。
def _text_rule_expression(requirement):
    match = _TEXT_CONTAINS.fullmatch(requirement)
    if match is None:
        return None
    clauses = re.split(r'[，,]\s*title(?:中)?\s*(?:包含|含有)\s*', match.group(1), maxsplit=1)
    alternatives = re.split(r'''(?<=[”"’'])\s*(?:或者|或是|或)\s*(?=[“"‘'])''', clauses[0])
    groups = [_quoted_list(part.strip()) for part in alternatives]
    if not all(groups):
        return None
    expression = {'any': [{'all': [{'scope': 'text', 'op': 'contains', 'value': term}
                                   for term in group]} for group in groups]}
    if len(clauses) == 2:
        titles = _quoted_list(clauses[1])
        if not titles:
            return None
        expression = {'all': [expression] + [{'scope': 'title', 'op': 'contains', 'value': title}
                                             for title in titles]}
    return expression


def expand_expected_keywords(raw):
    """Split wholly quoted lists; normalize quotes around one literal token."""
    result = []
    for item in raw:
        text = str(item).strip()
        # Ignore terminal punctuation after a fully quoted standalone term.
        if text[:1] in ('“', '‘', '"', "'", '「', '『', '《'):
            text = text.rstrip('。；;！!').strip()
        groups = _quoted_list(text)
        if len(groups) >= 2:
            result.extend(groups)
        elif text:
            result.append(unquote_outer(text))
    return list(dict.fromkeys(result))


def _weather_advice(text):
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_EXPLICIT.search(text))


# 视频复合断言既要满足文字 AND/OR，还必须检查真实视频 skillId，而非文字描述。
def parse_video_compound(requirement):
    """Strict AND/OR keyword expression plus one exact video skillId.

    Supports both legacy adjacent quoted keywords and nested quoted Boolean
    terms. Never infers intent, converts arbitrary prose to a literal, or
    silently accepts malformed expressions.
    """
    match = _VIDEO_COMPOUND.fullmatch(requirement)
    if match is None:
        return None
    skill_ids = _quoted_list(match.group('skill'))
    if len(skill_ids) != 1:
        raise ValueError('视频复合断言必须指定唯一且完整的引号 skillId')
    from lib.natural_assertions import _parse_values
    terms = _parse_values(match.group('terms').strip(), 'text', 'contains')
    return {'skill_id': skill_ids[0], 'terms': terms}


def _match_video_text_terms(expr, content):
    if 'all' in expr:
        return all(_match_video_text_terms(item, content) for item in expr['all'])
    if 'any' in expr:
        return any(_match_video_text_terms(item, content) for item in expr['any'])
    if expr.get('scope') != 'text' or expr.get('op') != 'contains':
        raise ValueError('video_compound_unrecognized_ast')
    return expr['value'] in content


def _missing_video_terms(expr, content):
    """Small deterministic failure explanation, preserving AND/OR groups."""
    if _match_video_text_terms(expr, content):
        return []
    if 'all' in expr:
        return [item for part in expr['all'] for item in _missing_video_terms(part, content)]
    if 'any' in expr:
        return ['至少满足其中一项：'+ ' 或 '.join(_describe_video_terms(item) for item in expr['any'])]
    return [expr['value']]


def _describe_video_terms(expr):
    if 'all' in expr:
        return '（'+' 且 '.join(_describe_video_terms(item) for item in expr['all'])+'）'
    if 'any' in expr:
        return '（'+' 或 '.join(_describe_video_terms(item) for item in expr['any'])+'）'
    return '“'+expr['value']+'”'


# 视频场景的 code=4145 指令必须在同一条记录里同时匹配 jsonHeader/jsonBody skillId。
# 关键词只从助手文本和匹配的视频参数中取，禁止跨无关指令拼接假证据。
def check_video_compound(requirement, signals):
    """Require exact header/body skillId in the same code=4145 record.

    The quoted Boolean terms apply only to real assistant text or video
    parameters belonging to a matched control record, never to user input,
    questions, metadata or a nonmatching video control payload.
    """
    compiled = parse_video_compound(requirement)
    if compiled is None:
        return None
    skill_id = compiled['skill_id']
    instructions = signals.get('video_instructions', [])
    matching = [item for item in instructions if
                item['header_skill_id'] == skill_id and item['body_skill_id'] == skill_id]
    content = ''.join(signals.get('answer_text_fragments', [])) + '\n' + '\n'.join(
        item['payload_text'] for item in matching)
    passed = bool(matching) and _match_video_text_terms(compiled['terms'], content)
    observed = ', '.join('header={}, body={}'.format(
        item['header_skill_id'] or '缺失', item['body_skill_id'] or '缺失')
        for item in instructions[:3]) or '未收到 code=4145 视频指令'
    if len(instructions)>3:
        observed += ' …'
    if matching and not passed:
        observed += '；业务内容缺少：' + '、'.join(_missing_video_terms(compiled['terms'], content))
    return {'operation':'video_compound',
            'path':'code=4145/jsonHeader.skillId/jsonBody.params.skillId',
            'expected':'视频指令 skillId={}，业务内容满足{}'.format(
                skill_id,_describe_video_terms(compiled['terms'])),
            'actual':observed, 'passed':passed}

def check_title_content_compound(requirement, signals):
    """Require a real assistant title and every term in answer content."""
    match = _TITLE_CONTENT.fullmatch(requirement)
    if match is None:
        return None
    titles = _quoted_list(match.group('title'))
    terms = _quoted_list(match.group('terms'))
    if len(titles) != 1 or not terms:
        return None
    observed_titles = signals.get('answer_titles', [])
    content = ''.join(signals.get('answer_text_fragments', [])) + '\n' + ''.join(
        signals.get('background_contents', []))
    missing = [term for term in terms if term not in content]
    return {'operation': 'title_content', 'path': 'title/content',
            'expected': 'title={}，内容包含{}'.format(titles[0], '、'.join(terms)),
            'actual': 'title 实际值：{}；内容缺少：{}'.format(
                '、'.join(str(t) for t in observed_titles[:3]) or '未收到',
                '、'.join(missing) or '无'),
            'passed': titles[0] in observed_titles and not missing}


# 每个 SSE JSON 帧只采集 choices[].delta.content.data 的 assistant 业务字段。
# 记录 text/questions/title/结构化字段，供后续统一裁决。
def accumulate_signals(frame, signals):
    """Only choices[].delta.content.data may supply business assertions."""
    choices = frame.get('choices')
    if not isinstance(choices, list):
        return
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        delta = choice.get('delta')
        content = delta.get('content') if isinstance(delta, dict) else None
        data = content.get('data') if isinstance(content, dict) else None
        if not isinstance(data, dict):
            continue
        if delta.get('role') not in (None, 'assistant'):
            continue
        index = choice.get('index', position)
        capture_record(data, index if type(index) is int else position, signals)
        capture_data_fields(data, signals)
        background = data.get('background')
        if isinstance(background, dict):
            if isinstance(background.get('title'), str):
                signals.setdefault('answer_titles', []).append(background['title'])
            if isinstance(background.get('content'), str):
                signals.setdefault('background_contents', []).append(background['content'])
        if 'questions' not in data and isinstance(data.get('title'), str):
            signals.setdefault('answer_titles', []).append(data['title'])
        if data.get('code') == 4145:
            try:
                header = data.get('jsonHeader')
                body = data.get('jsonBody')
                header = json.loads(header) if isinstance(header, str) else header
                body = json.loads(body) if isinstance(body, str) else body
            except (TypeError, ValueError):
                header, body = None, None
            params = body.get('params') if isinstance(body, dict) else None
            signals.setdefault('video_instructions', []).append({
                'header_skill_id': header.get('skillId') if isinstance(header, dict) else None,
                'body_skill_id': params.get('skillId') if isinstance(params, dict) else None,
                'payload_text': json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else '',
            })
        questions = data.get('questions')
        if isinstance(questions, list) and any(
            isinstance(q, str) and q.strip() for q in questions
        ):
            signals['valid_followup_questions'] = True
        response_text = data.get('text')
        if isinstance(response_text, str):
            signals['answer_text_fragments'].append(response_text)


# 【执行中心】每条规则严格计算 True/False，不判断“是否真正暂停成功”。
# 包含“暂停”且回复“无法暂停播放”→该条规则为 True；“已停止”→该条规则为 False。
# 若两种表达均可接受，写“暂停”或“停止”，由解析器构造 OR。
# 本函数输出命中项/缺失项和 trace；整条用例汇总在 v2_rejudge 内处理。
def evaluate_assertions(raw_wanted, response_texts, http_status, signals):
    """Evaluate typed compiled rules only; never infer natural-language intent.

    The legacy short, unambiguous business keyword is explicitly compiled as
    kind=literal. Every other syntax must have a recognized typed executor.
    A private audit trace records rule version, digest, scope and outcome.
    """
    from lib.rule_contract import compile_rule, RULE_ENGINE_VERSION
    wanted = expand_expected_keywords(raw_wanted)
    records = signals.get('business_records')

    def leaf_text(value):
        if isinstance(value, dict):
            return '\n'.join(leaf_text(item) for key, item in value.items()
                             if key not in {'questions', 'reqId', 'token', 'timestamp', 'msgId'}
                             and not (key == 'title' and 'questions' in value))
        if isinstance(value, list):
            return '\n'.join(leaf_text(item) for item in value)
        return str(value) if isinstance(value, (str, int, float)) else ''

    corpus = '\n'.join(leaf_text(record) for record in records) if records is not None else '\n'.join(response_texts)
    signals['assertion_corpus'] = corpus
    advice_corpus = ''.join(signals.get('answer_text_fragments', []))
    nonempty_answer = bool(advice_corpus.strip())
    verdicts, trace = [], []
    for index, requirement in enumerate(wanted, 1):
        try:
            compiled = compile_rule(requirement)
        except ValueError:
            # Standalone legacy callers that skip read_cases preflight cannot
            # convert an invalid or ambiguous rule into a false PASS. The
            # official workflow rejects it before any network dispatch.
            trace.append({'rule_id':'expected-{:02d}'.format(index),
                          'engine_version':RULE_ENGINE_VERSION,
                          'kind':'invalid','scope':None,'passed':False,
                          'error':'assertion_not_compilable'})
            signals.setdefault('invalid_assertions',[]).append(requirement)
            verdicts.append((requirement,False))
            continue
        kind, body = compiled['kind'], compiled['body']
        observation = None
        if kind == 'explicit_natural':
            observation = check_natural_rule(requirement, signals)
            passed = observation['passed']
        elif kind in ('json_expression', 'text_expression'):
            observation = expression_observation(body, signals)
            passed = observation['passed']
        elif kind == 'video_compound':
            observation = check_video_compound(requirement, signals)
            passed = observation['passed']
        elif kind == 'title_content_compound':
            observation = check_title_content_compound(requirement, signals)
            passed = observation['passed']
        elif kind == 'field':
            observation = check_field_rule(requirement, signals)
            passed = observation['passed']
        elif kind == 'literal':
            passed = body['value'] in corpus
        elif kind == 'public':
            if requirement == '文本回复':
                passed = nonempty_answer
            elif requirement == '衍生问题':
                passed = signals.get('valid_followup_questions', False) is True
            elif requirement in {'title', 'sdk', 'deeplink'}:
                observation = check_field_rule(requirement + '不为空', signals)
                if requirement == 'title':
                    passed = any(str(title).strip() for title in signals.get('answer_titles', []))
                else:
                    passed = observation['passed']
                observation = dict(observation, passed=passed)
            elif re.fullmatch(r'HTTP\s+([1-5]\d\d)', requirement, flags=re.I):
                match = re.fullmatch(r'HTTP\s+([1-5]\d\d)', requirement, flags=re.I)
                passed = http_status == int(match.group(1))
            elif requirement == '出行建议':
                passed = _weather_advice(advice_corpus)
            elif requirement == 'skillId':
                observation = check_natural_rule(requirement, signals)
                passed = observation['passed']
            else:
                raise ValueError('no_public_executor')
        else:
            raise ValueError('no_typed_rule_executor')
        passed = bool(passed)
        if observation is not None:
            signals.setdefault('field_rule_observations', {})[requirement] = observation
        # Trace records structural evidence only, never response text or secrets.
        if kind == 'field':
            scope = 'data.' + '.'.join(body['path'])
        elif kind == 'public' and re.fullmatch(r'HTTP\s+[1-5]\d\d',requirement,re.I):
            scope = 'http_status'
        elif kind == 'public':
            scope = {'文本回复':'assistant.text','衍生问题':'assistant.questions',
                     'title':'assistant.title','skillId':'assistant.skillId',
                     'sdk':'data.sdk','deeplink':'data.deeplink',
                     '出行建议':'assistant.text'}[requirement]
        elif kind in ('video_compound','title_content_compound'):
            scope = 'assistant.business_fields'
        elif kind in ('json_expression','text_expression'):
            scope = body.get('scope','boolean_expression')
        elif kind == 'explicit_natural':
            scope = 'typed_assistant_predicate'
        else:
            scope = body['scope']
        trace.append({'rule_id':'expected-{:02d}'.format(index),
                      'engine_version':RULE_ENGINE_VERSION,
                      'rule_sha256':compiled['sha256'],
                      'kind':kind, 'scope':scope,
                      'passed':passed,
                      'observed_field':(observation.get('path') if isinstance(observation,dict) else None)})
        verdicts.append((requirement,passed))
    signals['rule_trace'] = trace
    return ([text for text,ok in verdicts if ok],
            [text for text,ok in verdicts if not ok],wanted)