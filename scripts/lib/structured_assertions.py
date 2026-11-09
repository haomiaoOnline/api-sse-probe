"""Strict, extensible structured predicates for SSE assistant output data only.

Natural-language rules are recognized by an anchored, deliberately limited
grammar. Unknown prose remains a literal expected substring: no LLM guessing,
no matching request parameters or top-level response metadata.
"""
# 【字段匹配】只读助手输出的 data 字段，支持 exists/nonempty/equals/contains/prefix。
# 例如 deeplink 前缀、title 精确值等，必须命中真实结构化字段。
import json
import re

_FIELD_PATH = r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|中的|里面的|里的|的)[A-Za-z_][A-Za-z0-9_]*)*"
_FIELD_RULE = re.compile(
    r"^(?:要求)?\s*(?P<path>" + _FIELD_PATH + r")(?:字段)?\s*(?:必须|应当|应该|应)?\s*"
    r"(?:以\s*(?P<prefix>.+?)\s*开头|(?:是|为|等于|==|=)\s*(?P<equals>.+?)"
    r"|(?:包含|含有)\s*(?P<contains>.+?)|(?P<exists>存在|不为空))\s*$",
    re.I,
)
_QUOTE_PAIRS = (('“', '”'), ('‘', '’'), ('"', '"'), ("'", "'"),
                ('「', '」'), ('『', '』'), ('《', '》'), ('｢', '｣'))


def unquote_outer(value):
    """Strip *paired outer quotes*, never alter inner dots, slashes or URLs."""
    value = str(value).strip()
    while len(value) >= 2:
        match = next(((a, b) for a, b in _QUOTE_PAIRS
                      if value.startswith(a) and value.endswith(b)), None)
        if match is None:
            break
        value = value[1:-1].strip()
    return value


# 将中文字段规则编译为 path + operation + expected 三个确定值。
def parse_field_rule(requirement):
    text = unquote_outer(requirement).rstrip('。；;！!').strip()
    match = _FIELD_RULE.fullmatch(text)
    if not match:
        return None
    path = tuple(part.lower() for part in re.split(r"\.|中的|里面的|里的|的", match.group('path')))
    for group, operation in (('prefix', 'prefix'), ('equals', 'equals'),
                             ('contains', 'contains'), ('exists', 'exists')):
        raw = match.group(group)
        if raw is not None:
            wanted = None if group == 'exists' else unquote_outer(raw)
            if operation != 'exists' and not wanted:
                return None
            return {'path': path, 'operation': operation, 'expected': wanted}
    return None


# 展开 jsonBody/jsonHeader 等嵌套 JSON，排除请求参数和顶层元数据。
# 默认跨多帧汇总字段；需要“多个字段在同一帧”时用 same_frame。
def capture_data_fields(data, signals):
    """Flatten all scalar values from delta.content.data, including nested JSON.

    Paths have no choice/frame prefix, so the same rule may match any response
    event. Parsed JSON strings under jsonBody/jsonHeader are treated as nested
    business data; no top-level reqId, session or request payload is included.
    """
    values = signals.setdefault('data_fields', [])

    def visit(obj, path, depth):
        if depth > 24:
            return
        if path and isinstance(obj, (dict, list)):
            values.append((path, obj))
        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    continue
                next_path = path + (key.lower(),)
                visit(value, next_path, depth + 1)
                if key in ('jsonBody', 'jsonHeader') and isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except (ValueError, TypeError):
                        continue
                    visit(parsed, next_path, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                visit(item, path, depth + 1)
        elif obj is None or isinstance(obj, (str, int, float, bool)):
            values.append((path, obj))

    visit(data, (), 0)


# 对实际字段值判断前缀、包含、相等或非空，返回值及失败证据。
def check_field_rule(requirement, signals):
    """Return None for unrecognized syntax, otherwise a factual observation."""
    rule = parse_field_rule(requirement)
    if rule is None:
        return None
    suffix = rule['path']
    observed = [value for path, value in signals.get('data_fields', [])
                if len(path) >= len(suffix) and path[-len(suffix):] == suffix]
    if suffix == ('title',):
        observed = signals.get('answer_titles', [])
    operation, expected = rule['operation'], rule['expected']
    if operation == 'exists':
        passed = bool(observed) if '不为空' not in str(requirement) else any(
            value is not None and (bool(value.strip()) if isinstance(value, str)
                                   else bool(value) if isinstance(value, (dict, list)) else True)
            for value in observed)
    else:
        strings = [str(value) for value in observed if isinstance(value, (str, int, float, bool))]
        if operation == 'prefix':
            passed = any(value.startswith(expected) for value in strings)
        elif operation == 'contains':
            passed = any(expected in value for value in strings)
        else:
            passed = any(value == expected for value in strings)
    preview = ', '.join(repr(value) for value in observed[:3])
    if len(observed) > 3:
        preview += ' …'
    return {
        'path': '.'.join(suffix), 'operation': operation, 'expected': expected,
        'actual': preview[:400] if observed else '未收到该字段',
        'found': bool(observed), 'passed': passed,
    }