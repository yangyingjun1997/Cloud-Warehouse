"""安全响应头与 CSP nonce。

CSP 为什么要用 nonce（2026-11-23，P2-14）：
本系统页面有较多内联 <script>/<style>（web_base 的布局脚本、各页面初始化逻辑），
过去只能靠 script-src 'unsafe-inline' 放行，这等于把 XSS 防线拆掉一大半——
攻击者只要能把内容注入 HTML，内联脚本就会被执行。

nonce 方案：每个请求生成一个不可预测的随机串，同时
1) 写进 CSP 头 `script-src 'self' 'nonce-XXX'`
2) 渲染进模板属性 `nonce="XXX"`
浏览器只执行带正确 nonce 的内联脚本。注入者无法预知 nonce，因此注入的内联
脚本不会执行。nonce 随响应变化，不能被复用。

注意：模板里所有内联 <script>/<style> 都必须带 nonce="{{ csp_nonce }}"，
测试 test_csp_nonce.py 会扫描模板确保没有遗漏。
"""

import secrets

CACHE_RESET_COOKIE = 'warehouse-cache-reset'
CACHE_RESET_VERSION = '4'

# 请求级属性名：中间件写入，模板通过 context processor 读取
CSP_NONCE_ATTR = 'csp_nonce'


class SecurityHeadersMiddleware:
    """为每个请求生成 CSP nonce，并下发保守的安全响应头。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 先产生 nonce 并挂到 request，context processor 才能读到
        nonce = secrets.token_urlsafe(16)
        setattr(request, CSP_NONCE_ATTR, nonce)

        response = self.get_response(request)

        csp = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
            "manifest-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        response.setdefault('Content-Security-Policy', csp)
        response.setdefault('Referrer-Policy', 'same-origin')
        response.setdefault(
            'Permissions-Policy',
            'camera=(self), geolocation=(), microphone=(), payment=(), usb=()',
        )
        return response


class NoStoreHtmlMiddleware:
    """禁止浏览器缓存 HTML 页面，并在升级后自动清理一次旧缓存。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if content_type.startswith('text/html'):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            # 旧版本页面可能已经被浏览器缓存且不再回源；升级后让浏览器清一次缓存。
            if request.COOKIES.get(CACHE_RESET_COOKIE) != CACHE_RESET_VERSION:
                response['Clear-Site-Data'] = '"cache"'
                response.set_cookie(
                    CACHE_RESET_COOKIE,
                    CACHE_RESET_VERSION,
                    max_age=60 * 60 * 24 * 365,
                    samesite='Lax',
                )
        return response
