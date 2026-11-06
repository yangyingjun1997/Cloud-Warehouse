"""导航高亮等通用模板标签。

背景：基类模板里原先用

    {% if request.resolver_match.url_name == 'warehouse_management' or
          request.resolver_match.url_name == 'inventory_import' or ... %}active{% endif %}

逐个比较 URL name。一个导航项往往关联十几个页面，条件写得极长，
新增页面时必须记得回来改，容易漏。这里把“哪些页面属于哪个导航”收进
标签内部，模板里只写一行：

    {% nav_active 'warehouse_management' 'inventory_import' as on %}
    <a class="nav-link {% if on %}active{% endif %}" ...>
"""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def nav_active(context, *url_names: str) -> bool:
    """当前请求的 url_name 是否在给定列表里。"""
    request = context.get('request')
    if request is None:
        return False
    match = getattr(request, 'resolver_match', None)
    if match is None:
        return False
    current = match.url_name
    return bool(current) and current in url_names


@register.simple_tag(takes_context=True)
def nav_active_prefix(context, *prefixes: str) -> bool:
    """当前 url_name 是否以给定前缀开头，适合成组页面。"""
    request = context.get('request')
    if request is None:
        return False
    match = getattr(request, 'resolver_match', None)
    if match is None:
        return False
    current = match.url_name or ''
    return any(current.startswith(prefix) for prefix in prefixes)