#!/usr/bin/env python3
"""Send a new temporary tunnel URL to a DingTalk group robot."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


CONFIG_PATH = Path('/etc/after-sales-warehouse/quick-tunnel-dingtalk.env')
LOGGER = logging.getLogger('after-sales-warehouse-quick-tunnel')


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def signed_webhook(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    sign = base64.b64encode(
        hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({'timestamp': timestamp, 'sign': sign})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main() -> int:
    if len(sys.argv) < 2:
        print('缺少临时 Tunnel 地址。', file=sys.stderr)
        return 2
    url = sys.argv[1].strip()
    config = load_config(CONFIG_PATH)
    webhook = config.get('DINGTALK_WEBHOOK', '').strip()
    if not webhook:
        LOGGER.info('未配置 Tunnel 专用钉钉机器人，跳过地址通知。')
        return 0
    if not webhook.startswith('https://'):
        print('拒绝发送：钉钉 Webhook 必须使用 HTTPS。', file=sys.stderr)
        return 2

    mobiles = [item.strip() for item in config.get('DINGTALK_AT_MOBILE', '').replace('，', ',').split(',') if item.strip()]
    at_all = config.get('DINGTALK_AT_ALL', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    title = '售后仓库临时访问地址已更新'
    health_url = url.rstrip('/') + '/health/'
    markdown = (
        f'### {title}\n\n'
        f'- [打开仓库系统]({url})\n'
        f'- [健康检查]({health_url})\n'
        '- Tunnel 重启后地址可能变化，请以本消息中的最新地址为准。'
    )
    payload = json.dumps({
        'msgtype': 'markdown',
        'markdown': {'title': title, 'text': markdown},
        'at': {'atMobiles': mobiles, 'isAtAll': at_all},
    }, ensure_ascii=False).encode('utf-8')
    request = Request(
        signed_webhook(webhook, config.get('DINGTALK_SECRET', '').strip()),
        data=payload,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f'钉钉通知发送失败：{exc}', file=sys.stderr)
        return 1
    if result.get('errcode', 0) != 0:
        print(f'钉钉机器人拒绝消息：{result}', file=sys.stderr)
        return 1
    print('Tunnel 地址已发送到钉钉群。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
