"""Deterministic assertion contract: typed grammar or explicit literal only.

Unknown sentence-like rules are errors before any business POST, rather than
being silently interpreted as an intent or used as a literal requirement.
"""
# 【断言入口】仅确定性识别语法，不联网、不使用模型推断意图。
# 例如：HTTP 200、文本回复、文本回复包含“暂停”或“停止”、title是“视频”。
# 编译结果包括 kind（类型）、body（可执行条件）、sha256（规则指纹）。
import re
from lib.expression_assertions import parse_expression
from lib.natural_assertions import parse_natural_rule
from lib.structured_assertions import parse_field_rule

PUBLIC = {'文本回复','衍生问题','title','sdk','deeplink','skillId','出行建议'}
RISKY = re.compile(r'(?:回复|文本|正文|标题|title|skillId|sdk|deeplink|衍生|追问|字段|必须|应该|应当|意图|语义|围绕|合理|正确|符合|包含|等于|不为空|HTTP)',re.I)
HTTP = re.compile(r'HTTP\s+[1-5]\d\d\Z',re.I)
QUOTES = ('“','”','「','」','‘','’')


# 判定语法优先级：公共规则 → JSON → 显式中文 → 字段规则 → 兼容复合规则 → 安全短词。
# 不可解释的含糊语句报错，而不是悄悄退化成简单关键词或 HTTP 检查。
def validate_rule(requirement):
    if not isinstance(requirement,str) or not requirement.strip():
        raise ValueError('断言必须为非空字符串')
    text=requirement.strip()
    if len(text)>2048:
        raise ValueError('单条断言不得超过2048字符')
    if text in PUBLIC or HTTP.fullmatch(text):
        return 'public'
    if parse_expression(text) is not None:
        return 'json_expression'
    if parse_natural_rule(text) is not None:
        return 'explicit_natural'
    if parse_field_rule(text) is not None:
        return 'field'
    # Compound legacy grammars must be preserved, but incomplete predicates
    # may never accidentally fall back to a vague substring match.
    from lib.assertion_rules import (_text_rule_expression,
                                     parse_video_compound,
                                     check_title_content_compound)
    if _text_rule_expression(text) is not None:
        return 'text_expression'
    if parse_video_compound(text) is not None:
        return 'video_compound'
    if check_title_content_compound(text,{}) is not None:
        return 'title_content_compound'
    # Existing explicit quoted terms and plain business keywords remain
    # backwards-compatible. Sentence-like or conditional rules are rejected.
    from lib.structured_assertions import unquote_outer
    if text.startswith(QUOTES) and unquote_outer(text)!=text and not RISKY.search(text):
        return 'literal'
    if not RISKY.search(text) and '\n' not in text and len(text)<=50:
        return 'literal'
    raise ValueError('无法确定性解释断言；请写 HTTP 200、文本回复、title是“...”、回复中包含“...”或 JSON 结构化规则')

# A durable, versioned representation of each assertion. Compilation does not
# consume live response text or model-generated interpretations.
RULE_ENGINE_VERSION = 'deterministic-1'


# 将原始判断依据转成固定类型的条件对象，规范化计算哈希，供逐项判定及审计。
def compile_rule(requirement):
    """Compile a validated requirement into a closed set of execution kinds.

    Rules that would previously fall through to implicit literal matching are
    accepted only when they pass the explicit, short literal contract. Legacy
    compound parsers are preserved as typed deterministic adapters.
    """
    import hashlib
    import json
    from lib.structured_assertions import parse_field_rule
    from lib.assertion_rules import (_text_rule_expression, parse_video_compound,
                                     _TITLE_CONTENT, _quoted_list)
    kind = validate_rule(requirement)
    text = requirement.strip()
    # Preserve original execution priority: title/content compound must win
    # over a broad field-rule prefix match.
    from lib.assertion_rules import check_title_content_compound
    if check_title_content_compound(text,{}) is not None:
        kind = 'title_content_compound'
    if kind == 'json_expression':
        body = parse_expression(text)
    elif kind == 'explicit_natural':
        body = parse_natural_rule(text)
    elif kind == 'field':
        body = parse_field_rule(text)
    elif kind == 'text_expression':
        body = _text_rule_expression(text)
    elif kind == 'video_compound':
        body = parse_video_compound(text)
    elif kind == 'title_content_compound':
        match = _TITLE_CONTENT.fullmatch(text)
        if match is None:
            raise ValueError('title_content_parse_failure')
        body = {'titles':_quoted_list(match.group('title')),
                'terms':_quoted_list(match.group('terms'))}
    elif kind == 'literal':
        from lib.structured_assertions import unquote_outer
        body = {'scope':'business_payload', 'value':unquote_outer(text)}
    else:
        body = {'token':text}
    payload = {'version':RULE_ENGINE_VERSION,'kind':kind,'body':body}
    digest = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
                                       separators=(',',':')).encode('utf8')).hexdigest()
    return dict(payload, sha256=digest)


# 对一条用例的全部预期条件进行纯离线预编译：此函数没有任何网络副作用。
def compile_case_rules(case):
    """Pure preflight; no network, no request context or changing model output."""
    return [compile_rule(rule) for rule in case.get('expected_keywords',[])]