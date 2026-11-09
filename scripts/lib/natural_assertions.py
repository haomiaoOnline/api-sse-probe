"""Explicit human-readable text/title/skillId predicates, without inferred intent."""
# 【中文语法解析】支持明确的“回复中包含/不包含”、“title 是”、“skillId 是”。
# 仅将自然语言变成规则树；绝不通过大模型推断同义词或业务成功含义。
import re
from lib.expression_assertions import expression_observation, path_values

_QUOTES = {'“': '”', '‘': '’', '"': '"', "'": "'", '「': '」', '『': '』', '《': '》'}
_SKILL_PATHS = ('skillId', 'jsonHeader.skillId', 'jsonBody.skillId', 'jsonBody.params.skillId')
_PREFIX = re.compile(r'^(?:(回复中|回复|文本回复|title(?:字段|中)?)\s*(包含|含有|有|不包含|不含|是|为|等于)|(?:有\s*)?skillId\s*(?:字段)?\s*(是|为|等于))', re.I)


# 引号内是待匹配的原始文字：“和/与/且/、/相邻引号”→ AND；“或”→ OR。
# 括号用于组合及指定优先级，无法解析的条件直接报错。
def _parse_values(text, scope, operation):
    """Parse quoted values joined by AND/OR, protecting punctuation in quotes."""
    tokens, index = [], 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in _QUOTES:
            end = text.find(_QUOTES[char], index + 1)
            if end < 0 or not text[index + 1:end].strip():
                raise ValueError('判断依据需要成对引号及非空检查值')
            if tokens and tokens[-1][0] in ('term', ')'):
                tokens.append(('and', ''))  # Existing adjacent quoted lists mean AND.
            tokens.append(('term', text[index + 1:end]))
            index = end + 1
        elif char in '（(）)':
            tokens.append(('(' if char in '（(' else ')', ''))
            index += 1
        else:
            word = next((word for word in ('或者', '或是', '并且', '以及', '而且', '或', '且', '和', '与', '、', '，', ',')
                         if text.startswith(word, index)), None)
            if word is None:
                raise ValueError('判断依据使用引号值、和/或及括号；不接受宽泛意图描述')
            tokens.append(('or' if word in ('或者', '或是', '或') else 'and', ''))
            index += len(word)
        if len(tokens) > 128:
            raise ValueError('判断条件过多，请拆分为多条')
    position = 0

    def atom(depth):
        nonlocal position
        if depth > 16 or position >= len(tokens):
            raise ValueError('判断依据缺少条件或括号嵌套过深')
        kind, value = tokens[position]
        position += 1
        if kind == 'term':
            return {'scope': scope, 'op': operation, 'value': value}
        if kind == '(':
            result = group('or', depth + 1)
            if position >= len(tokens) or tokens[position][0] != ')':
                raise ValueError('判断依据括号不成对')
            position += 1
            return result
        raise ValueError('连接词两侧需要引号值')

    def group(operator, depth):
        nonlocal position
        children = [group('and', depth) if operator == 'or' else atom(depth)]
        while position < len(tokens) and tokens[position][0] == operator:
            position += 1
            children.append(group('and', depth) if operator == 'or' else atom(depth))
        return children[0] if len(children) == 1 else {'all' if operator == 'and' else 'any': children}

    expression = group('or', 0)
    if position != len(tokens):
        raise ValueError('判断依据缺少连接词或存在多余括号')
    return expression


def _split_clauses(text):
    clauses, start, quote, depth = [], 0, None, 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in _QUOTES:
            quote = _QUOTES[char]
        elif char in '（(':
            depth += 1
        elif char in '）)':
            depth -= 1
        elif char in '，,' and depth == 0 and re.match(
                r'\s*(?:title|回复|文本回复|有\s*skillId|skillId|有追问信息|衍生问题)', text[index + 1:], re.I):
            clauses.append(text[start:index].strip())
            start = index + 1
    clauses.append(text[start:].strip())
    return clauses


# 只接受有限的显式句式；“文本回复包含”由旧语法兼容解析器负责。
# “意图正确/语义合理”这种无法复现的语义描述应拒绝。
def parse_natural_rule(requirement):
    text = requirement.strip().rstrip('。').strip()
    if text.startswith(('回复围绕', '回复能体现', '回复或业务参数涉及')) or text == '有有效业务回复':
        raise ValueError('请改写为明确类型及条件，例如 HTTP 200 / 文本回复；回复中包含“暂停”或“无播放内容”')
    if text in ('有追问信息', '有衍生问题'):
        return {'kind': 'questions'}
    if text.lower() in ('sdk', 'deeplink'):
        return {'kind': 'expression', 'expression': {'scope': 'data', 'path': text.lower(), 'op': 'nonempty'}}
    if text.lower() == 'skillid':
        return {'kind': 'skill_presence'}
    # Preserve the established title/background-content compound contract.
    if re.match(r'^title\s*(?:是|为|等于).+?[，,]\s*内容\s*(?:包含|含有)', text, re.I):
        return None
    clauses = _split_clauses(text)
    if len(clauses) > 1:
        rules = [parse_natural_rule(clause) for clause in clauses]
        if any(rule is None for rule in rules):
            return None
        return {'kind': 'all', 'rules': rules}
    match = _PREFIX.match(text)
    if match is None:
        return None
    field, operator, skill_operator = match.groups()
    values = text[match.end():].strip()
    if skill_operator:
        return {'kind': 'skill', 'expression': _parse_values(values, 'data', 'equals')}
    # Existing 文本回复 syntax has its own backwards-compatible parser.
    if field == '文本回复':
        return None
    title = field.lower().startswith('title')
    if not title and operator in ('是', '为', '等于'):
        raise ValueError('回复文本使用包含/有；title 可以使用是/等于')
    expression = _parse_values(values, 'title' if title else 'text',
                               'equals' if operator in ('是', '为', '等于') else 'contains')
    if operator in ('不包含', '不含'):
        expression = {'not': expression}
    return {'kind': 'expression', 'expression': expression}


def _skill_values(record):
    return [value for path in _SKILL_PATHS for value in path_values(record, path)
            if isinstance(value, str) and value.strip()]


# skillId 从实际业务字段提取；同一记录中的多个 ID 应当一致，不从响应正文猜测。
def _check_skill(expression, records):
    if 'value' not in expression:
        states = [_check_skill(child, records) for child in expression.get('all', expression.get('any', []))]
        return all(states) if 'all' in expression else any(states)
    expected = expression['value']
    # At least one true field is required; if header/body are both present in
    # the candidate record, all present IDs must agree. No code is inferred.
    return any(values and all(value == expected for value in values)
               for values in (_skill_values(record) for record in records))


def _observe(rule, signals):
    kind = rule['kind']
    if kind == 'questions':
        passed = signals.get('valid_followup_questions') is True
        return passed, 'questions 数组有非空问题' if passed else '未收到非空的 questions 问题数组'
    if kind == 'all':
        results = [_observe(child, signals) for child in rule['rules']]
        return all(state for state, _ in results), '；'.join(actual for _, actual in results)
    if kind in ('skill', 'skill_presence'):
        records = signals.get('business_records', [])
        observed = [values for values in (_skill_values(record) for record in records) if values]
        passed = bool(observed) if kind == 'skill_presence' else _check_skill(rule['expression'], records)
        return passed, 'skillId 实际值：' + (repr(observed[:5]) if observed else '未收到真实 skillId 字段')
    observation = expression_observation(rule['expression'], signals)
    return observation['passed'], observation['actual']


def check_natural_rule(requirement, signals):
    rule = parse_natural_rule(requirement)
    if rule is None:
        return None
    passed, actual = _observe(rule, signals)
    return {'operation': 'explicit_condition', 'path': '助手正文/title/questions/skillId',
            'expected': requirement, 'actual': actual, 'passed': bool(passed)}