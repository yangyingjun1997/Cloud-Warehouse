#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only deployment readiness check for a clean Ubuntu 22.04 NUC.
REQUIRED_PORTS=(80 443 5432 8000)
MIN_MEMORY_MB=4096
MIN_DISK_GB=10
ERRORS=0
WARNINGS=0

ok() { printf '[通过] %s\n' "$1"; }
warn() { printf '[提醒] %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[阻断] %s\n' "$1"; ERRORS=$((ERRORS + 1)); }
section() { printf '\n==== %s ====\n' "$1"; }

section '操作系统与硬件'
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf '系统：%s %s\n' "${NAME:-未知}" "${VERSION_ID:-未知}"
  if [[ "${ID:-}" == 'ubuntu' && "${VERSION_ID:-}" == '22.04' ]]; then
    ok '符合 Ubuntu 22.04 部署目标。'
  else
    warn '推荐 Ubuntu 22.04；其他版本需由运维确认依赖兼容性。'
  fi
else
  warn '无法读取 /etc/os-release。'
fi

memory_mb=$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo)
printf '内存：%s MB\n' "$memory_mb"
if (( memory_mb >= MIN_MEMORY_MB )); then
  ok "内存不低于 ${MIN_MEMORY_MB} MB。"
else
  fail "内存低于 ${MIN_MEMORY_MB} MB，不建议部署 PostgreSQL、Gunicorn 和 Nginx。"
fi

disk_free_gb=$(df -Pk / | awk 'NR == 2 { print int($4 / 1024 / 1024) }')
printf '系统盘可用空间：%s GB\n' "$disk_free_gb"
if (( disk_free_gb >= MIN_DISK_GB )); then
  ok "系统盘可用空间不低于 ${MIN_DISK_GB} GB。"
else
  fail "系统盘可用空间低于 ${MIN_DISK_GB} GB。"
fi

printf 'CPU：%s 核\n' "$(nproc)"
printf '磁盘概览：\n'
df -hT / /srv 2>/dev/null || df -hT /

section '网络'
ip -4 -o addr show scope global 2>/dev/null | awk '{print "IPv4：" $2 " " $4}' || warn '未获取到 IPv4 地址。'
ip route show default 2>/dev/null | sed 's/^/默认路由：/' || warn '未获取到默认路由。'
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes \
    && ok '系统时间已通过 NTP 同步。' \
    || warn '系统时间尚未确认 NTP 同步，请在部署前校时。'
fi

section '端口占用'
if ! command -v ss >/dev/null 2>&1; then
  fail '缺少 ss 命令，无法检查端口。请安装 iproute2 后重试。'
else
  for port in "${REQUIRED_PORTS[@]}"; do
    listeners=$(ss -ltnpH "sport = :$port" 2>/dev/null || true)
    if [[ -n "$listeners" ]]; then
      fail "端口 $port 已被占用：$listeners"
    else
      ok "端口 $port 未被占用。"
    fi
  done
fi

section '已有服务与防火墙'
for service in nginx postgresql after-sales-warehouse; do
  if systemctl is-active --quiet "$service" 2>/dev/null; then
    fail "服务 $service 已在运行；请先确认是否为旧部署。"
  else
    ok "服务 $service 未运行。"
  fi
done

if command -v ufw >/dev/null 2>&1; then
  ufw status verbose 2>/dev/null | sed 's/^/UFW：/' || warn '无法读取 UFW 状态，请用 sudo 重新执行。'
else
  warn '未安装 UFW；部署后需由运维确认仅向公司内网开放 80/443 端口。'
fi

section '结果'
if (( ERRORS > 0 )); then
  printf '检测完成：%s 个阻断项，%s 个提醒项。请处理阻断项后再运行 install.sh。\n' "$ERRORS" "$WARNINGS"
  exit 1
fi
printf '检测完成：无阻断项，%s 个提醒项。可以继续执行 install.sh。\n' "$WARNINGS"
