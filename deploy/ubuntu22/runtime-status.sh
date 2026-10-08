#!/usr/bin/env bash
set -Eeuo pipefail

services=(
  postgresql
  nginx
  after-sales-warehouse
  after-sales-warehouse-quick-tunnel
  after-sales-warehouse-low-stock-summary.timer
  after-sales-warehouse-return-reminder.timer
  after-sales-warehouse-reservation-expiry.timer
  after-sales-warehouse-backup.timer
  after-sales-warehouse-health.timer
  after-sales-warehouse-consistency.timer
  after-sales-warehouse-security-cleanup.timer
)

echo '=== 售后仓库 NUC 运行状态 ==='
echo "主机：$(hostname)"
echo "时间：$(date --iso-8601=seconds)"
echo

echo '--- systemd 服务 ---'
for service in "${services[@]}"; do
  if systemctl cat "$service" >/dev/null 2>&1; then
    printf '%-48s active=%-10s enabled=%s\n' \
      "$service" \
      "$(systemctl is-active "$service" 2>/dev/null || true)" \
      "$(systemctl is-enabled "$service" 2>/dev/null || true)"
  else
    printf '%-48s 未安装\n' "$service"
  fi
done
echo

echo '--- 监听端口 ---'
ss -lntp 2>/dev/null | grep -E ':(80|443|5432|8000)[[:space:]]' || echo '未发现仓库相关监听端口。'
echo

echo '--- 版本 ---'
printf 'Python: '; /opt/after-sales-warehouse/venv/bin/python --version 2>/dev/null || echo '未安装'
printf 'Django: '; /opt/after-sales-warehouse/venv/bin/python -m django --version 2>/dev/null || echo '未安装'
printf 'Gunicorn: '; /opt/after-sales-warehouse/venv/bin/gunicorn --version 2>/dev/null || echo '未安装'
printf 'Nginx: '; nginx -v 2>&1 || true
printf 'PostgreSQL client: '; psql --version 2>/dev/null || echo '未安装'
printf 'cloudflared: '; cloudflared --version 2>/dev/null || echo '未安装'
echo

echo '--- 健康检查 ---'
curl --silent --show-error --max-time 5 http://127.0.0.1/health/ || true
echo
echo

echo '--- 已登记的 Web 入口 ---'
ENV_FILE=/opt/after-sales-warehouse/backend/.env
if [[ -r "$ENV_FILE" ]]; then
  awk -F= 'index($0, "ALLOWED_HOSTS=") == 1 { print $2 }' "$ENV_FILE" | tr ',' '\n' | sed -e '/^$/d' -e 's/^/  /'
else
  echo '未找到应用配置，无法读取已登记入口。'
fi
echo

echo '--- 临时域名入口（current-url） ---'
/usr/local/sbin/after-sales-warehouse-quick-tunnel-url 2>/dev/null || echo '尚未生成或未启用 Quick Tunnel。'
echo

echo '--- 下次低库存汇总 ---'
systemctl list-timers after-sales-warehouse-low-stock-summary.timer --no-pager 2>/dev/null || true
echo
echo '--- 下次归还提醒 ---'
systemctl list-timers after-sales-warehouse-return-reminder.timer --no-pager 2>/dev/null || true
echo
echo '--- 下次预约过期处理 ---'
systemctl list-timers after-sales-warehouse-reservation-expiry.timer --no-pager 2>/dev/null || true
echo
echo '--- 下次备份与健康检查 ---'
systemctl list-timers after-sales-warehouse-backup.timer after-sales-warehouse-health.timer --no-pager 2>/dev/null || true
echo
echo '--- 下次数据一致性检查 ---'
systemctl list-timers after-sales-warehouse-consistency.timer --no-pager 2>/dev/null || true
systemctl list-timers after-sales-warehouse-security-cleanup.timer --no-pager 2>/dev/null || true
echo
echo '--- 最近一次健康检查 ---'
systemctl status after-sales-warehouse-health.service --no-pager -l 2>/dev/null || true
echo
echo '--- 最近一次数据一致性检查 ---'
systemctl status after-sales-warehouse-consistency.service --no-pager -l 2>/dev/null || true
