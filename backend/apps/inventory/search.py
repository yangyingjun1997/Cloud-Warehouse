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


def asset_search_query(value: str) -> Q:
    """Search all supported user-facing asset identifiers and text aliases."""

    value = normalize_scan_value(value)
    if not value:
        # 空查询不应命中任何记录，否则扫码枪误触发会返回全表
        return Q(pk__in=[])

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
