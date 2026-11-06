import unicodedata

from django.db.models import Q


def normalize_scan_value(value: str) -> str:
    """归一化扫码枪/手工输入，避免"看着一样却查不到"。

    中文输入法下冒号会变全角（`WMS2：A：AST-00000238`），
    与库里的半角 `WMS2:A:...` 不相等，用户肉眼根本看不出区别。
    NFKC 会把全角标点、全角字母数字统一折回半角，
    顺便去掉零宽字符和首尾空白（扫码枪偶尔会带尾随换行）。
    """

    if not value:
        return ''
    # 去掉不可见字符：零宽空格、BOM、换行、制表符
    cleaned = ''.join(
        ch for ch in value
        if ch not in '\u200b\u200c\u200d\ufeff\r\n\t'
    )
    return unicodedata.normalize('NFKC', cleaned).strip()


def _term_q(value: str) -> Q:
    return (
        Q(system_asset_no__iexact=value)
        | Q(identifiers__normalized_value__iexact=value)
        | Q(asset_code__iexact=value)
        | Q(qr_value__iexact=value)
        | Q(serial_number__iexact=value)
        | Q(manufacturer_barcode__iexact=value)
        | Q(system_asset_no__icontains=value)
        | Q(asset_code__icontains=value)
        | Q(name__icontains=value)
        | Q(item_type__name__icontains=value)
        | Q(warehouse__name__icontains=value)
        | Q(warehouse__code__icontains=value)
        | Q(location__name__icontains=value)
        | Q(location__code__icontains=value)
        | Q(location__description__icontains=value)
        | Q(manufacturer__icontains=value)
        | Q(model__icontains=value)
        | Q(search_aliases__icontains=value)
        | Q(remarks__icontains=value)
        | Q(search_index__icontains=value.lower())
    )


def _negated_q(query: Q) -> Q:
    # 多值关联上的 ~Q 会因 JOIN 产生误判，改用子查询表达“完全不含”。
    from .models import Asset
    return ~Q(pk__in=Asset.objects.filter(query).values('pk'))


def _tokenize(expression: str) -> list:
    tokens = []
    index = 0
    length = len(expression)
    while index < length:
        char = expression[index]
        if char in '&|!()':
            tokens.append(char)
            index += 1
        elif char.isspace():
            index += 1
        elif char == '"':
            end = expression.find('"', index + 1)
            if end == -1:
                raise ValueError('unterminated quote')
            tokens.append(('term', expression[index + 1:end]))
            index = end + 1
        else:
            end = index
            while end < length and not expression[end].isspace() and expression[end] not in '&|!()"':
                end += 1
            tokens.append(('term', expression[index:end]))
            index = end
    return tokens


def _parse_boolean(tokens: list) -> Q:
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def parse_or() -> Q:
        nonlocal position
        query = parse_and()
        while peek() == '|':
            position += 1
            query = query | parse_and()
        return query

    def parse_and() -> Q:
        nonlocal position
        query = parse_unary()
        while True:
            token = peek()
            if token == '&':
                position += 1
                query = query & parse_unary()
            elif token == '!' or token == '(' or (isinstance(token, tuple) and token[0] == 'term'):
                query = query & parse_unary()
            else:
                return query

    def parse_unary() -> Q:
        nonlocal position
        token = peek()
        if token == '!':
            position += 1
            return _negated_q(parse_unary())
        if token == '(':
            position += 1
            query = parse_or()
            if peek() != ')':
                raise ValueError('missing closing parenthesis')
            position += 1
            return query
        if isinstance(token, tuple) and token[0] == 'term' and token[1]:
            position += 1
            return _term_q(token[1])
        raise ValueError('unexpected token')

    query = parse_or()
    if position != len(tokens):
        raise ValueError('unexpected trailing token')
    return query


def _literal_q(value: str) -> Q:
    return _term_q(value)


def asset_search_query(value: str) -> Q:
    """Search all supported user-facing asset identifiers and text aliases.

    普通关键词保持原有“任意字段包含”语义；出现 `! & | ( )` 时按布尔表达式解析。
    优先级：`!` > `&` > `|`，相邻条件视为 `&`。
    """

    value = normalize_scan_value(value)
    if not value:
        # 空查询不应命中任何记录，否则扫码枪误触发会返回全表
        return Q(pk__in=[])

    if not any(char in value for char in '&|!()'):
        return _literal_q(value)

    try:
        query = _parse_boolean(_tokenize(value))
    except ValueError:
        # 表达式不完整时退回普通包含搜索，避免用户看到服务器错误。
        return _literal_q(value)
    return query
