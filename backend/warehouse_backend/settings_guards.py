"""生产环境配置校验：把 settings 里的硬保护抽成可单测的纯函数。

放在独立模块的理由：settings.py 顶层一旦 raise 就无法在测试里断言，
而用 subprocess 重新加载 settings 又会重复初始化 Django 并干扰测试进程
（SQLite 内存库会互相踩踏，表现为整个测试进程静默崩溃）。
这里做成纯函数，settings 调用它，测试也调用它，两边共用同一套规则。
"""


class ProductionConfigError(Exception):
    """生产环境配置不合法。settings.py 会把它转成 ImproperlyConfigured。"""


def validate_production_config(*, debug, allowed_hosts, cors_allow_all):
    """DEBUG=0 时校验三处高危配置；DEBUG=1 时全部放行。

    - ALLOWED_HOSTS 不能含 '*'：否则任意 Host 头都能通过校验（Host 头注入）。
    - ALLOWED_HOSTS 不能为空：Django 会拒绝所有请求，表现为整站 400。
    - CORS_ALLOW_ALL_ORIGINS 不能开：任何网站都能跨域调 API。
    """
    if debug:
        return

    hosts = [str(h).strip() for h in (allowed_hosts or []) if str(h).strip()]
    if '*' in hosts:
        raise ProductionConfigError(
            '生产环境 ALLOWED_HOSTS 不能包含 *，请填写服务器内网 IP 与对外域名。'
        )
    if not hosts:
        raise ProductionConfigError('生产环境 ALLOWED_HOSTS 不能为空。')
    if cors_allow_all:
        raise ProductionConfigError(
            '生产环境不能开启 CORS_ALLOW_ALL_ORIGINS，请按需列出具体来源。'
        )
