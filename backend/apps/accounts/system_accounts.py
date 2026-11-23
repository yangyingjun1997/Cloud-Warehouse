"""系统内置账号清单：这些账号用于系统维护，不应出现在任何业务表单里。

为什么需要集中定义（2026-11-19）：
「指定审批人」下拉里出现了 admin 账号——普通人能看到并且把单子指派给系统管理
账号，这是权限模型上的大忌：既泄露了账号存在，又可能让申请落在没人处理的地方。

教训（同日修正）：
最初把清单写成 'label_print'（下划线），而线上真实账号是 'label-printer'
（连字符），导致打印账号从未被排除。这类「差一个字符就静默失效」的清单，
必须用多种写法兜住，不能只依赖一处字面量。
"""

# 明确不参与业务的维护账号。
# 同时收录常见的连字符/下划线两种写法，避免因命名风格不一致而漏排除。
SYSTEM_ACCOUNT_USERNAMES = frozenset({
    'admin',          # 系统初始化超管
    'label_print',    # 标签打印专用账号（下划线写法）
    'label-printer',  # 标签打印专用账号（连字符写法，线上实际使用）
    'labelprinter',   # 无分隔符写法，兜底
    'label-printer-account',
})

# 这些前缀的账号一律视为维护账号（打印、服务、机器人等专用号）。
# 用前缀匹配而不是精确列举，避免以后新增打印/服务账号时又漏配。
SYSTEM_ACCOUNT_PREFIXES = (
    'label_print',
    'label-printer',
    'labelprinter',
    'service-',
    'robot-',
)


def _normalize(username):
    return (username or '').strip().lower()


def is_system_account(user):
    username = _normalize(getattr(user, 'username', ''))
    if not username:
        return False
    if username in SYSTEM_ACCOUNT_USERNAMES:
        return True
    return any(username.startswith(prefix) for prefix in SYSTEM_ACCOUNT_PREFIXES)


def exclude_system_accounts(queryset):
    """从用户查询集里剔除系统维护账号。

    先按精确用户名在数据库层排除（走索引、保持惰性），
    再用前缀规则兜底——这部分需要 Python 层判断，因此对结果做一次过滤再
    重新包装成查询集。调用方都是下拉候选（量级为几十），性能可接受。
    """
    model = queryset.model
    excluded = set(
        queryset.filter(
            username__in=SYSTEM_ACCOUNT_USERNAMES,
        ).values_list('pk', flat=True)
    )
    # 前缀兜底：把命中的 id 也加进去
    for pk, username in queryset.values_list('pk', 'username'):
        if any(_normalize(username).startswith(prefix) for prefix in SYSTEM_ACCOUNT_PREFIXES):
            excluded.add(pk)
    return queryset.exclude(pk__in=excluded)
