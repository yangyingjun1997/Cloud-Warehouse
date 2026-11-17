"""人员显示相关的模板标签。

背景：单据流转以 `username`（登录账号）为唯一真相，`username` 不可变；
中文姓名（`first_name`）可由本人修改，仅用于展示。若单据只显示 `username`，
审批人看到的是拼音或工号，辨识度差；若只显示中文姓名，改名后历史单据会"换人"。

因此统一显示为"中文姓名（登录账号）"，例如「张三（zhangsan）」：
- 中文姓名便于人眼识别；
- 括号内的登录账号保证可追溯，且不会因改名而失真。
"""

from django import template

register = template.Library()


def format_person(user) -> str:
    """把用户对象格式化成「中文姓名（登录账号）」。

    没有中文姓名时只显示登录账号；用户为空时显示"已删除账号"，
    避免历史单据因账号被删除而出现空白。
    """
    if not user:
        return '已删除账号'
    username = (user.get_username() or '').strip()
    display_name = ''
    try:
        display_name = (user.get_full_name() or '').strip()
    except AttributeError:
        display_name = ''
    if display_name and username and display_name != username:
        return f'{display_name}（{username}）'
    return display_name or username


@register.filter(name='person_label')
def person_label(user) -> str:
    return format_person(user)


@register.simple_tag(name='person')
def person_tag(user) -> str:
    return format_person(user)
