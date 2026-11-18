#!/usr/bin/env bash
# 隧道健康自愈：cloudflared 断连时自动重启。
#
# 为什么需要：WiFi 环境下外网 DNS 被阻断，只靠内网 DNS 解析 argotunnel.com。
# 内网 DNS 抖动时 cloudflared 会报 "no more connections active and exiting"
# 然后进程退出，但 systemd 的 Restart=always 只在进程退出时生效；
# 某些情况下进程活着但 0 条连接（日志里 ERR 刷屏），表现为「域名打不开但服务显示 active」。
#
# 判定逻辑：
#   1. 进程不在 → 直接启动
#   2. 进程在但近 10 分钟无 "Registered tunnel connection" → 重启
# 连续失败会记录日志并钉钉告警，避免无声循环重启。
set -Eeuo pipefail

NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify
SERVICE=cloudflared
LOOKBACK="10 minutes"
STATE_FILE=/var/lib/after-sales-warehouse/cloudflared-selfheal.count
mkdir -p "$(dirname "$STATE_FILE")"

notify() {
  [[ -x "$NOTIFIER" ]] && "$NOTIFIER" "$1" "$2" || true
}

# 1) 进程不活跃 → 尝试启动
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "cloudflared 未运行，尝试启动。"
  systemctl start "$SERVICE" || true
  sleep 12
fi

# 2) 进程在但有连接吗
CONNECTIONS="$(journalctl -u "$SERVICE" --since "$LOOKBACK" --no-pager 2>/dev/null \
  | grep -c 'Registered tunnel connection' || true)"

if [[ "${CONNECTIONS:-0}" -gt 0 ]]; then
  # 健康：清空失败计数
  [[ -f "$STATE_FILE" ]] && rm -f "$STATE_FILE"
  echo "隧道健康：近 ${LOOKBACK} 内有 ${CONNECTIONS} 次连接注册。"
  exit 0
fi

# 3) 无连接 → 重启
echo "近 ${LOOKBACK} 内无隧道连接（当前 $CONNECTIONS 次），重启 cloudflared。"
systemctl restart "$SERVICE" || true
sleep 15

NEW_CONNECTIONS="$(journalctl -u "$SERVICE" --since '1 minute ago' --no-pager 2>/dev/null \
  | grep -c 'Registered tunnel connection' || true)"

FAILS=0
[[ -f "$STATE_FILE" ]] && FAILS="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"

if [[ "${NEW_CONNECTIONS:-0}" -gt 0 ]]; then
  echo "重启后恢复：${NEW_CONNECTIONS} 条连接。"
  rm -f "$STATE_FILE"
  notify '售后仓库隧道已自动恢复' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n重启 cloudflared 后恢复 ${NEW_CONNECTIONS} 条连接。  \n如频繁出现，请检查 WiFi DNS 是否能解析 argotunnel.com。"
  exit 0
fi

FAILS=$((FAILS + 1))
echo "$FAILS" > "$STATE_FILE"
echo "重启后仍无连接（连续失败 ${FAILS} 次）。"

# 连续失败 3 次才告警，避免一次网络抖动就吵
if [[ "$FAILS" -ge 3 ]]; then
  notify '售后仓库隧道持续无法连接' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n连续 ${FAILS} 次自愈失败，cloudflared 无法建立连接。  \n\n排查：\n\`sudo journalctl -u cloudflared -n 100\`\n\`getent ahostsv4 region1.v2.argotunnel.com\`\n\n若 DNS 解析失败，说明当前网络的 DNS 无法解析 Cloudflare 隧道域名，需切换网络或改用可靠 DNS。"
fi
