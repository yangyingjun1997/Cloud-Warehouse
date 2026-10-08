#!/usr/bin/env python3
"""Send an operations message through the configured DingTalk group robot."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


CONFIG_PATH = Path('/etc/after-sales-warehouse/quick-tunnel-dingtalk.env')


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def signed_webhook(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time() * 1000))
    sign = base64.b64encode(hmac.new(
        secret.encode('utf-8'),
        f'{timestamp}\n{secret}'.encode('utf-8'),
        hashlib.sha256,
    ).digest()).decode('utf-8')
    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({'timestamp': timestamp, 'sign': sign})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main() -> int:
    if len(sys.argv) < 3:
        print('用法：dingtalk-notify.py 标题 消息正文', file=sys.stderr)
        return 2
    title, message = sys.argv[1].strip(), sys.argv[2].strip()
    config = load_config(CONFIG_PATH)
    webhook = config.get('DINGTALK_WEBHOOK', '').strip()
    if not webhook:
        print('未配置钉钉机器人，跳过运维通知。')
        return 0
    if not webhook.startswith('https://'):
        print('钉钉 Webhook 必须使用 HTTPS。', file=sys.stderr)
        return 2
    mobiles = [
        item.strip()
        for item in config.get('DINGTALK_AT_MOBILE', '').replace('，', ',').split(',')
        if item.strip()
    ]
    at_all = config.get('DINGTALK_AT_ALL', '0').lower() in {'1', 'true', 'yes', 'on'}
    payload = json.dumps({
        'msgtype': 'markdown',
        'markdown': {'title': title, 'text': f'### {title}\n\n{message}'},
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
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
