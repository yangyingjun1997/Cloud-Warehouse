CACHE_RESET_COOKIE = 'warehouse-cache-reset'
CACHE_RESET_VERSION = '3'


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
