#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME=after-sales-warehouse-quick-tunnel
URL_FILE=/var/lib/cloudflared/current-url

if [[ -s "$URL_FILE" ]]; then
  cat "$URL_FILE"
  # 生成时间走 stderr，避免污染 stdout（调用方常把 stdout 直接当地址使用）。
  echo "该地址生成于 $(date -r "$URL_FILE" '+%Y-%m-%d %H:%M:%S')，Tunnel 重启后可能已变化。" >&2
  exit 0
fi

# 回退到日志时必须使用与 runner 相同的排除规则，否则会把建隧道失败日志里的
# https://api.trycloudflare.com/tunnel 当成访问地址返回给用户。
# 不限本次启动：地址往往是在更早的一次运行里拿到的。
url="$(journalctl -u "$SERVICE_NAME" --no-pager 2>/dev/null \
  | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' \
  | grep -oE 'https://[A-Za-z0-9][A-Za-z0-9.-]*\.trycloudflare\.com' \
  | grep -vE '^https://(api|cp|regionarg|cfargotunnel|probe)\.trycloudflare\.com$' \
  | tail -n 1 || true)"

if [[ -n "$url" ]]; then
  echo "$url"
  exit 0
fi

echo '暂未找到临时访问地址。请检查 Tunnel 状态和日志：'
echo "sudo systemctl status $SERVICE_NAME --no-pager"
echo "sudo journalctl -u $SERVICE_NAME -n 80 --no-pager"
exit 1
