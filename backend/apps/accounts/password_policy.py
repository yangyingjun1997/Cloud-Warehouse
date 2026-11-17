"""密码强度策略。

设计取舍（2026-11-17 与用户确认）：

1. **只在新设密码时校验**——建账号、重置密码、自助改密码。存量账号不强制改，
   否则一上线就会把除 admin / label_print 之外的所有人挡在门外。
2. **服务账号豁免**——`admin` 是运维入口，`label_print` 的密码可能存在打印
   客户端配置里，强制改会导致现场打印中断。两者不做强度校验。
3. 校验规则刻意保守：够用即可，避免为了「复杂度」逼用户把密码写在便签上。
"""

import re

# 服务账号：不参与强度校验
EXEMPT_USERNAMES = frozenset({'admin', 'label_print'})

MIN_LENGTH = 8
MAX_LENGTH = 128

# 常见弱口令（小写比较）。不是穷举，只挡住最容易被猜到的那些。
COMMON_WEAK_PASSWORDS = frozenset({
    'password', 'password1', 'password123', 'passw0rd',
    '12345678', '123456789', '1234567890', '87654321',
    'qwertyui', 'qwerty123', 'abc12345', 'abcd1234',
    'admin123', 'admin1234', 'administrator', 'root1234',
    '11111111', '00000000', '88888888', '66666666',
    'iloveyou', 'welcome1', 'letmein1', 'changeme',
    'warehouse', 'warehouse1', 'warehouse123',
    'woaini1314', '5201314520', 'a1234567', 'aa123456',
})


def is_exempt(username):
    """服务账号豁免强度校验。"""
    return (username or '').strip().lower() in EXEMPT_USERNAMES


def validate_password_strength(password, username=''):
    """返回错误文案；通过则返回 None。

    调用方把这些文案直接展示给用户，所以写中文、说清楚怎么改。
    """
    if is_exempt(username):
        return None

    if not password or len(password) < MIN_LENGTH:
        return f'密码至少需要 {MIN_LENGTH} 个字符。'
    if len(password) > MAX_LENGTH:
        return f'密码不能超过 {MAX_LENGTH} 个字符。'

    lowered = password.lower()

    if lowered in COMMON_WEAK_PASSWORDS:
        return '这个密码太常见了，很容易被猜到，请换一个。'

    # 纯数字 / 纯字母：单独一类熵太低
    if re.fullmatch(r'\d+', password):
        return '密码不能是纯数字，请混合字母或符号。'
    if re.fullmatch(r'[A-Za-z]+', password):
        return '密码不能是纯字母，请加入数字或符号。'

    # 与用户名相同或高度相关
    if username:
        uname = username.strip().lower()
        if uname and (lowered == uname or lowered.startswith(uname) or uname in lowered):
            return '密码不能包含登录账号，请换一个。'

    # 连续或重复字符（12345678 / aaaaaaaa）
    if len(set(password)) == 1:
        return '密码不能是同一个字符重复，请换一个。'
    if _is_sequential(password):
        return '密码不能是连续字符（如 12345678、abcdefgh），请换一个。'

    return None


def _is_sequential(password):
    """判断是否为严格递增/递减的连续字符序列。"""
    if len(password) < 3:
        return False
    codes = [ord(ch) for ch in password]
    asc = all(codes[i + 1] - codes[i] == 1 for i in range(len(codes) - 1))
    desc = all(codes[i] - codes[i + 1] == 1 for i in range(len(codes) - 1))
    return asc or desc
