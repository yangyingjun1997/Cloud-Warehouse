"""根据中文姓名生成登录账号（拼音）。"""
from __future__ import annotations

import re

from django.contrib.auth import get_user_model

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - 依赖缺失时降级为纯英文
    lazy_pinyin = None
    Style = None


_NON_ALNUM = re.compile(r'[^a-z0-9]')


def _pinyin_of(chinese_name: str) -> str:
    if not chinese_name:
        return ''
    if lazy_pinyin is None:
        return ''
    parts = lazy_pinyin(chinese_name, style=Style.NORMAL, errors='ignore')
    return ''.join(parts)


def suggest_username(chinese_name: str) -> str:
    """把「杨俊杰」转成 `yangjunjie`。

    规则：
    - 中文转全拼小写，去掉非字母数字；
    - 纯英文名（如外包同事用英文名）直接小写字母数字；
    - 结果为空时退回 `user`，交给调用方再补后缀。

    不在这里保证唯一：唯一性由 `unique_username` 负责。
    """
    raw = (chinese_name or '').strip()
    if not raw:
        return 'user'
    candidate = _NON_ALNUM.sub('', _pinyin_of(raw).lower())
    if not candidate:
        # 已经是英文/数字名，或拼音库不可用时直接清洗原文
        candidate = _NON_ALNUM.sub('', raw.lower())
    if not candidate:
        return 'user'
    # Django 用户名上限 150，留出后缀空间
    return candidate[:140]


def unique_username(chinese_name: str) -> str:
    """生成不重复的登录账号：重名时追加 2、3、4……"""
    User = get_user_model()
    base = suggest_username(chinese_name)
    if not User.objects.filter(username=base).exists():
        return base
    for index in range(2, 1000):
        candidate = f'{base}{index}'
        if not User.objects.filter(username=candidate).exists():
            return candidate
    # 极端情况：加随机后缀，避免建号被卡死
    import secrets
    return f'{base}{secrets.randbelow(9000) + 1000}'
