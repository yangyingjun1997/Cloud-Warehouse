"""低频同步已认证浏览器会话，避免每个请求都写数据库。"""

from django.conf import settings
from django.utils import timezone

from .session_services import register_account_session


class AccountSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            now = int(timezone.now().timestamp())
            last_sync = int(request.session.get('_account_session_synced_at', 0) or 0)
            interval = max(0, int(getattr(settings, 'ACCOUNT_SESSION_SYNC_SECONDS', 300)))
            if now - last_sync >= interval:
                register_account_session(request, user)
        return self.get_response(request)
