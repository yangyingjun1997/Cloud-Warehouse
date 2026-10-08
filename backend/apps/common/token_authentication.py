"""兼容 ``Authorization: Token <令牌>`` 的可过期认证。"""

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiAccessToken
from .token_services import hash_token


class ExpiringTokenAuthentication(BaseAuthentication):
    keyword = b'token'

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth:
            return None
        if auth[0].lower() != self.keyword:
            return None
        if len(auth) != 2:
            raise AuthenticationFailed('认证令牌格式不正确。')

        try:
            token = ApiAccessToken.objects.select_related('user').get(
                token_hash=hash_token(auth[1].decode('utf-8')),
            )
        except (ApiAccessToken.DoesNotExist, UnicodeDecodeError):
            raise AuthenticationFailed('认证令牌无效或已过期。')

        if not token.is_active or not token.user.is_active:
            raise AuthenticationFailed('认证令牌无效或已过期。')
        return token.user, token

    def authenticate_header(self, request):
        return self.keyword.decode('ascii')
