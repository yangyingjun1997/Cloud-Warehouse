#!/usr/bin/env bash
# 隧道健康自愈：cloudflared 无可用边缘连接时自动重启。
#
# ── 为什么重写（2026-11-18）─────────────────────────────────────────
# 第一版用 `journalctl --since "10 minutes ago" | grep -c 'Registered tunnel connection'`
# 判断健康，这是错的：隧道稳定运行时**只在启动时注册一次连接**，
# 之后靠 QUIC 长连接维持，日志里不会持续出现 "Registered tunnel connection"。
# 于是每过 10 分钟窗口内必然为空 → 判定失败 → 重启 → 重启后又注册一次
# → 再过 10 分钟再次误判…… 形成「每 5 分钟往钉钉群报一次自动恢复」的刷屏。
#
# 现在改为直接读 cloudflared 自己的 metrics 端点（127.0.0.1:20241），
# 看 cloudflared_tunnel_ha_connections 的真实数值，这是连接状态本身，
# 不是事件日志，不会随时间窗口失效。
set -Eeuo pipefail

NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify
SERVICE=cloudflared
METRICS_URL="http://127.0.0.1:20241/metrics"
STATE_FILE=/var/lib/after-sales-warehouse/cloudflared-selfheal.count
mkdir -p "$(dirname "$STATE_FILE")"

notify() {
  [[ -x "$NOTIFIER" ]] && "$NOTIFIER" "$1" "$2" || true
}

# 读取当前活跃的边缘连接数。取不到时输出 -1（区别于「取到 0」）。
read_connections() {
  local metrics
  metrics="$(curl -s -m 5 "$METRICS_URL" 2>/dev/null || true)"
  if [[ -z "$metrics" ]]; then
    echo "-1"
    return
  fi
  # 实际格式是 `cloudflared_tunnel_ha_connections 4`（无标签），
  # 但不同版本可能带 `{...}` 标签，所以两种都匹配：取每行最后一个字段求和。
  # 早先只匹配带花括号的写法，导致稳定 4 条连接被解析成 0 而反复误重启。
  local count
  count="$(echo "$metrics" \
    | awk '/^cloudflared_tunnel_ha_connections([ {]|$)/ {sum += $NF} END {printf "%d", (sum == "" ? 0 : sum)}')"
  echo "${count:-0}"
}

# 1) 进程不活跃 → 启动
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "cloudflared 未运行，尝试启动。"
  systemctl start "$SERVICE" || true
  sleep 15
fi

# 2) 先查 DNS：cloudflared 依赖解析 argotunnel.com 才能连边缘。
#    2026-11-19 的实际故障是 systemd-resolved 报 "server misbehaving"，
#    隧道反复崩溃重启（NRestarts=26），metrics 端口时有时无——
#    旧逻辑「metrics 读不到就跳过」正好漏掉这种场景。所以 DNS 单独判定。
DNS_OK=1
if ! timeout 6 getent ahostsv4 region1.v2.argotunnel.com >/dev/null 2>&1; then
  DNS_OK=0
  echo "DNS 无法解析 region1.v2.argotunnel.com，判定为异常。"
fi

# 3) 读真实连接数
CONNECTIONS="$(read_connections)"

if [[ "$DNS_OK" == "0" ]]; then
  # DNS 坏了，重启 cloudflared 没用，要重启解析服务并清缓存。
  echo "尝试修复 DNS（重启 systemd-resolved 并清缓存）。"
  systemctl restart systemd-resolved || true
  sleep 4
  resolvectl flush-caches 2>/dev/null || true
  sleep 3
  if timeout 6 getent ahostsv4 region1.v2.argotunnel.com >/dev/null 2>&1; then
    echo "DNS 已恢复，重启 cloudflared 重新建立连接。"
    systemctl restart "$SERVICE" || true
    sleep 18
    RECOVERED_DNS="$(read_connections)"
    if [[ "$RECOVERED_DNS" != "-1" && "$RECOVERED_DNS" -gt 0 ]]; then
      echo "恢复完成：${RECOVERED_DNS} 条连接。"
      rm -f "$STATE_FILE"
      notify '售后仓库 DNS 故障已自动修复' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n检测到无法解析 argotunnel.com，重启 systemd-resolved 后恢复 ${RECOVERED_DNS} 条隧道连接。  \n若反复出现，建议给服务器配置可靠的 DNS 上游或改用固定 resolv.conf。"
      exit 0
    fi
    echo "DNS 已恢复但隧道仍未连上，交回下一次检查。"
    exit 0
  fi
  # DNS 仍不可用：累计失败次数，连续 3 次告警
  FAILS_DNS=0
  [[ -f "$STATE_FILE" ]] && FAILS_DNS="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
  FAILS_DNS=$((FAILS_DNS + 1))
  echo "$FAILS_DNS" > "$STATE_FILE"
  echo "DNS 仍不可用（连续 ${FAILS_DNS} 次）。"
  if [[ "$FAILS_DNS" -ge 3 ]]; then
    notify '售后仓库 DNS 持续故障' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n连续 ${FAILS_DNS} 次无法解析 argotunnel.com，域名访问会中断。  \n\n排查：\n\`resolvectl status\`\n\`systemctl status systemd-resolved\`\n\`getent ahostsv4 region1.v2.argotunnel.com\`\n\n内网 IP 访问不受影响。"
  fi
  exit 0
fi

if [[ "$CONNECTIONS" == "-1" ]]; then
  # metrics 取不到但 DNS 正常：可能是进程刚起还没监听。
  # 仍然计一次失败——连续多次读不到本身就说明进程不稳定。
  FAILS_M=0
  [[ -f "$STATE_FILE" ]] && FAILS_M="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
  FAILS_M=$((FAILS_M + 1))
  echo "$FAILS_M" > "$STATE_FILE"
  echo "无法读取 metrics 端点（连续 ${FAILS_M} 次），本次不重启。"
  if [[ "$FAILS_M" -ge 3 ]]; then
    echo "连续 ${FAILS_M} 次读不到 metrics，重启 cloudflared。"
    systemctl restart "$SERVICE" || true
  fi
  exit 0
fi

if [[ "$CONNECTIONS" -gt 0 ]]; then
  [[ -f "$STATE_FILE" ]] && rm -f "$STATE_FILE"
  echo "隧道健康：当前 ${CONNECTIONS} 条边缘连接。"
  exit 0
fi

# 3) 真实 0 连接 → 重启
echo "当前 0 条边缘连接，重启 cloudflared。"
systemctl restart "$SERVICE" || true
sleep 18

RECOVERED="$(read_connections)"

FAILS=0
[[ -f "$STATE_FILE" ]] && FAILS="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"

if [[ "$RECOVERED" != "-1" && "$RECOVERED" -gt 0 ]]; then
  echo "重启后恢复：${RECOVERED} 条连接。"
  rm -f "$STATE_FILE"
  # 只在「确实发生过 0 连接」时通知，且同一天最多一次，避免刷屏。
  TODAY_MARK="/var/lib/after-sales-warehouse/cloudflared-selfheal.notified-$(date +%F)"
  if [[ ! -f "$TODAY_MARK" ]]; then
    notify '售后仓库隧道已自动恢复' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n检测到 0 条边缘连接，重启后恢复 ${RECOVERED} 条。  \n（同一天只通知一次）"
    touch "$TODAY_MARK"
  fi
  exit 0
fi

FAILS=$((FAILS + 1))
echo "$FAILS" > "$STATE_FILE"
echo "重启后仍无连接（连续失败 ${FAILS} 次）。"

if [[ "$FAILS" -ge 3 ]]; then
  notify '售后仓库隧道持续无法连接' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n连续 ${FAILS} 次自愈失败，cloudflared 无法建立边缘连接。  \n\n排查：\n\`sudo journalctl -u cloudflared -n 100\`\n\`getent ahostsv4 region1.v2.argotunnel.com\`\n\n若 DNS 解析失败，说明当前网络无法解析 Cloudflare 隧道域名。"
fi
