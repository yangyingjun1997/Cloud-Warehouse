"""持有人单位与客户资料的匹配辅助。

设计取舍（2026-11-19 与用户确认）：
- 「holder单位」字段保留原名：它还服务外借第三方、退供应商、送外修等场景，
  改名为「客户单位」会让这些场景语义矛盾。
- 保持纯文本存储：不自动创建客户档案。手写的单位名常有错别字、简称全称混用，
  自动建档会产出重复、脏的主数据，比没有更糟。
- 只在「精确匹配到已有客户」时自动关联 customer_ref；匹配不到就只存文本，
  并在页面提示可稍后手工建档。

匹配规则刻意严格（同名且启用），避免「华为」误配到「华为技术有限公司」。
"""

from __future__ import annotations

from .models import Customer


def match_customer_by_name(name: str):
    """按名称精确匹配启用中的客户资料。

    返回 (customer, status)：
    - status='matched'  唯一命中，可安全关联
    - status='none'     无命中或名称为空
    - status='ambiguous' 命中多条（同名），不自动关联，交由人工判断
    """
    normalized = (name or '').strip()
    if not normalized:
        return None, 'none'

    matches = list(
        Customer.objects.filter(name=normalized, is_active=True)[:2]
    )
    if not matches:
        return None, 'none'
    if len(matches) > 1:
        return None, 'ambiguous'
    return matches[0], 'matched'
