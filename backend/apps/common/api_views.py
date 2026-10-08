"""受限的 API 认证入口。"""

from rest_framework.authtoken import serializers as token_serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .api_throttle import client_ip_from_request, consume_token_request
from .audit import record_operation
from .token_services import issue_token, revoke_token, rotate_token


class RateLimitedObtainAuthToken(APIView):
    """对令牌申请入口按 IP 和账号做数据库限流。"""

    authentication_classes = []
    permission_classes = []
    serializer_class = token_serializers.AuthTokenSerializer

    def post(self, request, *args, **kwargs):
        username = request.data.get('username', '')
        allowed, retry_after = consume_token_request(
            username=username,
            client_ip=client_ip_from_request(request),
        )
        if not allowed:
            response = Response(
                {'detail': '请求过于频繁，请稍后再试。'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response['Retry-After'] = str(retry_after)
            return response
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        token, raw_token = issue_token(serializer.validated_data['user'])
        record_operation(
            serializer.validated_data['user'],
            'api_token_issued',
            token,
            summary=f'签发 API 访问令牌（前缀 {token.token_prefix}）',
            after={'expires_at': token.expires_at},
        )
        return Response({
            'token': raw_token,
            'expires_at': token.expires_at,
        })


class RotateApiTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        current = request.auth
        if not hasattr(current, 'user'):
            return Response({'detail': '当前令牌不支持轮换，请重新申请令牌。'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token, raw_token = rotate_token(current)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        record_operation(
            request.user,
            'api_token_rotated',
            token,
            summary=f'轮换 API 访问令牌（前缀 {current.token_prefix} → {token.token_prefix}）',
            after={'expires_at': token.expires_at},
        )
        return Response({'token': raw_token, 'expires_at': token.expires_at})


class RevokeApiTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        current = request.auth
        if not hasattr(current, 'user'):
            return Response({'detail': '当前令牌不支持撤销。'}, status=status.HTTP_400_BAD_REQUEST)
        revoke_token(current)
        record_operation(
            request.user,
            'api_token_revoked',
            current,
            summary=f'撤销 API 访问令牌（前缀 {current.token_prefix}）',
        )
        return Response({'detail': '当前令牌已撤销。'})
