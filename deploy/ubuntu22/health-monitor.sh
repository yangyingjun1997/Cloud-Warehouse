#!/usr/bin/env bash
set -Eeuo pipefail

cd /

STATE_DIR=/var/lib/after-sales-warehouse-monitor
STATE_FILE="$STATE_DIR/health.state"
NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify
DISK_WARNING_PERCENT="${DISK_WARNING_PERCENT:-85}"
MEMORY_WARNING_PERCENT="${MEMORY_WARNING_PERCENT:-90}"

install -d -m 750 "$STATE_DIR"
problems=()

for service in postgresql nginx after-sales-warehouse; do
  systemctl is-active --quiet "$service" || problems+=("服务 $service 未运行")
done

for port in 80 8000 5432; do
  ss -lnt 2>/dev/null | grep -Eq ":${port}[[:space:]]" || problems+=("端口 $port 未监听")
done

sudo -u postgres pg_isready -q -d warehouse || problems+=("PostgreSQL 数据库 warehouse 不可用")
curl --fail --silent --show-error --max-time 8 http://127.0.0.1/health/ >/dev/null || problems+=("本机 HTTP 健康检查失败")

disk_percent="$(df -P /srv/after-sales-warehouse | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
[[ "$disk_percent" =~ ^[0-9]+$ ]] && (( disk_percent < DISK_WARNING_PERCENT )) || problems+=("数据盘使用率 ${disk_percent:-未知}%（阈值 ${DISK_WARNING_PERCENT}%）")

memory_percent="$(awk '/MemTotal:/ { total=$2 } /MemAvailable:/ { available=$2 } END { if (total > 0) printf "%d", (total - available) * 100 / total }' /proc/meminfo)"
[[ "$memory_percent" =~ ^[0-9]+$ ]] && (( memory_percent < MEMORY_WARNING_PERCENT )) || problems+=("内存使用率 ${memory_percent:-未知}%（阈值 ${MEMORY_WARNING_PERCENT}%）")

current_state=ok
(( ${#problems[@]} == 0 )) || current_state=failed
previous_state="$(cat "$STATE_FILE" 2>/dev/null || true)"
printf '%s\n' "$current_state" > "$STATE_FILE"

if [[ "$current_state" == failed ]]; then
  printf '健康检查失败：\n- %s\n' "${problems[@]}" >&2
  if [[ "$previous_state" != failed && -x "$NOTIFIER" ]]; then
    message="$(printf -- '- %s\n' "${problems[@]}")"
    "$NOTIFIER" '售后仓库服务器异常' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)\n\n${message}" || true
  fi
  exit 1
fi

if [[ "$previous_state" == failed && -x "$NOTIFIER" ]]; then
  "$NOTIFIER" '售后仓库服务器已恢复' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)\n\n全部健康检查已通过。" || true
fi
echo '全部健康检查已通过。'