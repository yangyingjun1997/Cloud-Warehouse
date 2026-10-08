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


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    channel: str
    reason: str = ''


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
    url = 'https://oapi.dingtalk.com/gettoken?' + urlencode({
        'appkey': app_key,
        'appsecret': app_secret,
    })
    try:
        with urlopen(url, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:
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
    url = 'https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?' + urlencode({
        'access_token': token,
    })
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
        with urlopen(request, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:
            result = json.loads(response.read().decode('utf-8'))
    except Exception:
        logger.exception('Unable to send DingTalk personal notification to user %s', user.pk)
        return DeliveryResult(False, 'dingtalk_personal', '发送请求失败')
    if result.get('errcode', 0) != 0:
        logger.warning('DingTalk personal notification rejected for user %s: %s', user.pk, result)
        return DeliveryResult(False, 'dingtalk_personal', result.get('errmsg', '钉钉拒绝发送'))
    return DeliveryResult(True, 'dingtalk_personal')


def send_users_or_group(users, title: str, content: str) -> list[DeliveryResult]:
    results = [send_user_message(user, title, content) for user in users if user and user.is_active]
    if not results or not all(item.delivered for item in results):
        delivered = send_group_message(title, content)
        results.append(DeliveryResult(delivered, 'dingtalk_group', '' if delivered else '群机器人未配置或发送失败'))
    return results


def send_group_message(title: str, content: str) -> bool:
    """Send a warehouse event to a configured DingTalk group robot."""

    webhook = getattr(settings, 'DINGTALK_WEBHOOK', '').strip()
    if not webhook:
        return False

    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    secret = getattr(settings, 'DINGTALK_SECRET', '').strip()
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
        with urlopen(request, timeout=getattr(settings, 'DINGTALK_TIMEOUT_SECONDS', 5)) as response:
            result = json.loads(response.read().decode('utf-8'))
        if result.get('errcode', 0) != 0:
            logger.warning('DingTalk robot rejected warehouse notification: %s', result)
            return False
    except Exception:
        logger.exception('Unable to send DingTalk warehouse notification')
        return False
    return True
