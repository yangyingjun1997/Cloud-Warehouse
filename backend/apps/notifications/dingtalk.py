import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)

_access_token = ''
_access_token_expires_at = 0.0
_DINGTALK_API_HOST = 'oapi.dingtalk.com'


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    channel: str
    reason: str = ''


# 通知渠道。当前只配一个群（全部消息进同一个群），
# 但保留渠道概念，将来要把运维告警拆到独立群时，只需加环境变量：
#   DINGTALK_WEBHOOK_OPS / DINGTALK_SECRET_OPS
# 未配置的渠道自动回落到 DINGTALK_WEBHOOK，行为与单群完全一致。
CHANNEL_DEFAULT = 'default'
CHANNEL_OPS = 'ops'

_SUPPORTED_CHANNELS = (CHANNEL_DEFAULT, CHANNEL_OPS)


def _channel_settings(channel: str) -> tuple[str, str]:
    """返回 (webhook, secret)。指定渠道未配置时回落到默认渠道。"""
    channel = (channel or CHANNEL_DEFAULT).strip().lower()
    if channel not in _SUPPORTED_CHANNELS:
        channel = CHANNEL_DEFAULT
    if channel == CHANNEL_DEFAULT:
        return (
            getattr(settings, 'DINGTALK_WEBHOOK', '').strip(),
            getattr(settings, 'DINGTALK_SECRET', '').strip(),
        )
    suffix = channel.upper()
    webhook = getattr(settings, f'DINGTALK_WEBHOOK_{suffix}', '').strip()
    secret = getattr(settings, f'DINGTALK_SECRET_{suffix}', '').strip()
    if not webhook:
        # 该渠道没配独立群，回落到默认群，避免消息直接丢掉
        return _channel_settings(CHANNEL_DEFAULT)
    return webhook, secret or getattr(settings, 'DINGTALK_SECRET', '').strip()


def _validated_dingtalk_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme.lower() != 'https'
        or (parts.hostname or '').lower() != _DINGTALK_API_HOST
        or parts.username
        or parts.password
    ):
        return ''
    return url


def dingtalk_configuration_status() -> dict:
    app_key = getattr(settings, 'DINGTALK_APP_KEY', '').strip()
    app_secret = getattr(settings, 'DINGTALK_APP_SECRET', '').strip()
    agent_id = str(getattr(settings, 'DINGTALK_AGENT_ID', '')).strip()
    return {
        'group_robot_configured': bool(getattr(settings, 'DINGTALK_WEBHOOK', '').strip()),
        'personal_message_configured': bool(app_key and app_secret and agent_id),
        'missing_personal_settings': [
            label for value, label in (
                (app_key, 'AppKey'), (app_secret, 'AppSecret'), (agent_id, 'AgentId'),
            ) if not value
        ],
    }


def _get_access_token() -> tuple[str, str]:
    global _access_token, _access_token_expires_at
    now = time.time()
    if _access_token and now < _access_token_expires_at:
        return _access_token, ''
    app_key = getattr(settings, 'DINGTALK_APP_KEY', '').strip()
    app_secret = getattr(settings, 'DINGTALK_APP_SECRET', '').strip()
    if not app_key or not app_secret:
        return '', '企业内部应用 AppKey 或 AppSecret 未配置'
    url = _validated_dingtalk_url('https://oapi.dingtalk.com/gettoken?' + urlencode({
        'appkey': app_key,
        'appsecret': app_secret,
    }))
    try:
        with urlopen(url, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:  # nosec B310 -- fixed validated HTTPS DingTalk host
            result = json.loads(response.read().decode('utf-8'))
    except Exception:
        logger.exception('Unable to obtain DingTalk access token')
        return '', '获取钉钉访问令牌失败'
    if result.get('errcode', 0) != 0 or not result.get('access_token'):
        logger.warning('DingTalk access token rejected: %s', result)
        return '', result.get('errmsg', '钉钉未返回访问令牌')
    _access_token = result['access_token']
    _access_token_expires_at = now + max(int(result.get('expires_in', 7200)) - 300, 60)
    return _access_token, ''


def send_user_message(user, title: str, content: str) -> DeliveryResult:
    status = dingtalk_configuration_status()
    if not status['personal_message_configured']:
        return DeliveryResult(False, 'dingtalk_personal', '企业内部应用未配置完整')
    try:
        profile = user.profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if not profile or not profile.is_dingtalk_bound or not profile.dingtalk_user_id.strip():
        return DeliveryResult(False, 'dingtalk_personal', '用户尚未绑定钉钉账号')
    token, error = _get_access_token()
    if not token:
        return DeliveryResult(False, 'dingtalk_personal', error)
    url = _validated_dingtalk_url('https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?' + urlencode({
        'access_token': token,
    }))
    payload = json.dumps({
        'agent_id': str(getattr(settings, 'DINGTALK_AGENT_ID', '')).strip(),
        'userid_list': profile.dingtalk_user_id.strip(),
        'msg': {
            'msgtype': 'text',
            'text': {'content': f'{title}\n{content}'},
        },
    }, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=payload, headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urlopen(request, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:  # nosec B310 -- fixed validated HTTPS DingTalk host
            result = json.loads(response.read().decode('utf-8'))
    except Exception:
        logger.exception('Unable to send DingTalk personal notification to user %s', user.pk)
        return DeliveryResult(False, 'dingtalk_personal', '发送请求失败')
    if result.get('errcode', 0) != 0:
        logger.warning('DingTalk personal notification rejected for user %s: %s', user.pk, result)
        return DeliveryResult(False, 'dingtalk_personal', result.get('errmsg', '钉钉拒绝发送'))
    return DeliveryResult(True, 'dingtalk_personal')


def send_users_or_group(users, title: str, content: str, *, channel: str = CHANNEL_DEFAULT) -> list[DeliveryResult]:
    results = [send_user_message(user, title, content) for user in users if user and user.is_active]
    if not results or not all(item.delivered for item in results):
        delivered = send_group_message(title, content, channel=channel)
        results.append(DeliveryResult(delivered, 'dingtalk_group', '' if delivered else '群机器人未配置或发送失败'))
    return results


def send_group_message(title: str, content: str, *, channel: str = CHANNEL_DEFAULT) -> bool:
    """Send a warehouse event to a configured DingTalk group robot.

    channel 默认走 DINGTALK_WEBHOOK；传入 'ops' 时优先用 DINGTALK_WEBHOOK_OPS，
    未配置则回落默认群。
    """

    webhook, secret = _channel_settings(channel)
    if not webhook:
        return False
    webhook = _validated_dingtalk_url(webhook)
    if not webhook:
        logger.error('DingTalk webhook must use the official HTTPS API host')
        return False

    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if secret:
        timestamp = str(int(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        signature = base64.b64encode(
            hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')
        query.update({'timestamp': timestamp, 'sign': signature})
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    payload = json.dumps({
        'msgtype': 'text',
        'text': {'content': f'{title}\n{content}'},
    }, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=payload, headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urlopen(request, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:  # nosec B310 -- validated HTTPS DingTalk host
            result = json.loads(response.read().decode('utf-8'))
        if result.get('errcode', 0) != 0:
            logger.warning('DingTalk robot rejected warehouse notification: %s', result)
            return False
    except Exception:
        logger.exception('Unable to send DingTalk warehouse notification')
        return False
    return True
