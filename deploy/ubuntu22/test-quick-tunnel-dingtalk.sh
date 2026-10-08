#!/usr/bin/env bash
set -Eeuo pipefail

URL_FILE=/var/lib/cloudflared/current-url
DINGTALK_SENDER=/usr/local/libexec/after-sales-warehouse-quick-tunnel-dingtalk

if [[ ! -s "$URL_FILE" ]]; then
  echo '当前还没有 Tunnel 地址，请先启动 after-sales-warehouse-quick-tunnel。'
  exit 1
fi
if [[ ! -x "$DINGTALK_SENDER" ]]; then
  echo "找不到钉钉发送程序：$DINGTALK_SENDER"
  exit 1
fi
"$DINGTALK_SENDER" "$(cat "$URL_FILE")"
