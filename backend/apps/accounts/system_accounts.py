"""系统内置账号清单：这些账号用于系统维护，不应出现在任何业务表单里。

为什么需要集中定义（2026-11-19）：
「指定审批人」下拉里出现了 admin 账号——普通人能看到并且把单子指派给系统管理
账号，这是权限模型上的大忌：既泄露了账号存在，又可能让申请落在没人处理的地方。
排查后发现同一份清单（admin、label_print）在密码策略与上下文处理器里已经各自
硬编码过一次，属于典型的「同一个事实写三遍」。这里收口成唯一来源。

判定原则：账号是否属于「系统维护账号」不看它属于哪个组，而看它是否被标记为
服务/维护用途。系统管理员的 username 也可能不叫 admin（例如建号时用了别的
名字），所以除了按用户名，还要按 is_superuser 兜底吗？——不。
超管可能同时是真实业务人员，一律排除会误伤。这里只排除明确的维护账号。
"""

# 明确不参与业务的维护账号。新增维护账号时只改这里。
SYSTEM_ACCOUNT_USERNAMES = frozenset({
    'admin',        # 系统初始化超管
    'label_print',  # 标签打印专用账号（无交互登录需求）
})


def exclude_system_accounts(queryset):
    """从用户查询集里剔除系统维护账号，用于所有面向业务人员的下拉/列表。"""
    return queryset.exclude(username__in=SYSTEM_ACCOUNT_USERNAMES)


def is_system_account(user):
    username = (getattr(user, 'username', '') or '').strip().lower()
    return username in SYSTEM_ACCOUNT_USERNAMES
