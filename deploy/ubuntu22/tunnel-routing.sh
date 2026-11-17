#!/usr/bin/env bash
# 隧道出口隔离（UID 路由）：让 cloudflared 专用账号的流量只从移动热点网卡出去，
# 其它进程一律走主路由（公司 WiFi）。
#
# 为什么不用 network namespace：
#   Linux 内核的 wireless phy 是全局对象，WiFi 网卡无法移入 netns
#   （ip link set wlan0 netns xxx 会返回 "RTNETLINK answers: Invalid argument"）。
#   因此改用 iptables owner 匹配 + fwmark 策略路由，隔离强度同样由内核保证。
#
# 注意：只用 OUTPUT 的 MARK，**不要**加 CONNMARK save/restore。
#   本机进程的 mark 只需在 OUTPUT 打上即可选路；给 INPUT 加 CONNMARK restore
#   会把回程包的 mark 也恢复成同样的值，导致回程被再次查进 tunnel 表而断链。
#
# 用法：
#   sudo bash tunnel-routing.sh up|down|status|test
set -Eeuo pipefail

TUNNEL_USER=cloudflared
USB_IFACE="${USB_IFACE:-wlx90de800bb7bf}"
TABLE_NAME=tunnel
TABLE_ID=100
MARK=100
RULE_PRIO=900

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行。' >&2
  exit 1
fi

tunnel_uid() {
  id -u "$TUNNEL_USER" 2>/dev/null || { echo "[错误] 用户 $TUNNEL_USER 不存在" >&2; exit 1; }
}

ensure_table_name() {
  grep -qE "^${TABLE_ID}[[:space:]]+${TABLE_NAME}$" /etc/iproute2/rt_tables \
    || echo "${TABLE_ID} ${TABLE_NAME}" >> /etc/iproute2/rt_tables
}

do_up() {
  local uid gw addr
  uid="$(tunnel_uid)"

  ip link show "$USB_IFACE" >/dev/null 2>&1 || {
    echo "[错误] 找不到网卡 $USB_IFACE（移动热点是否已连接？）" >&2; exit 1; }
  gw="$(ip route show dev "$USB_IFACE" default 2>/dev/null | awk '/default/ {print $3; exit}')"
  [[ -n "$gw" ]] || { echo "[错误] $USB_IFACE 没有默认网关，热点未连上。" >&2; exit 1; }
  addr="$(ip -o -f inet addr show "$USB_IFACE" | awk '{print $4}' | head -1)"

  ensure_table_name

  # tunnel 表：本网段直连 + 默认走热点
  [[ -n "$addr" ]] && ip route replace "$(echo "$addr" | sed 's|\.[0-9]*/|.0/|')" \
    dev "$USB_IFACE" scope link table "$TABLE_NAME" 2>/dev/null || true
  ip route replace default via "$gw" dev "$USB_IFACE" table "$TABLE_NAME"

  # 只给 cloudflared 的出站包打 mark（按 uid，内核强制，其它进程拿不到）
  iptables -t mangle -C OUTPUT -m owner --uid-owner "$uid" -j MARK --set-mark "$MARK" 2>/dev/null \
    || iptables -t mangle -A OUTPUT -m owner --uid-owner "$uid" -j MARK --set-mark "$MARK"

  ip rule show | grep -q "fwmark $(printf '0x%x' "$MARK")" \
    || ip rule add fwmark "$MARK" table "$TABLE_NAME" priority "$RULE_PRIO"

  # 热点网卡不做反向路径过滤，否则回程会被丢
  sysctl -w net.ipv4.conf."$USB_IFACE".rp_filter=0 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.conf.all.src_valid_mark=1 >/dev/null 2>&1 || true

  echo "隔离已建立：uid=$uid -> mark $(printf '0x%x' "$MARK") -> $TABLE_NAME -> $USB_IFACE (gw $gw, addr $addr)"
}

do_down() {
  local uid
  uid="$(tunnel_uid)"
  iptables -t mangle -D OUTPUT -m owner --uid-owner "$uid" -j MARK --set-mark "$MARK" 2>/dev/null || true
  iptables -t mangle -D OUTPUT -m owner --uid-owner "$uid" -j CONNMARK --save-mark 2>/dev/null || true
  iptables -t mangle -D INPUT -j CONNMARK --restore-mark 2>/dev/null || true
  while ip rule show | grep -q "fwmark $(printf '0x%x' "$MARK")"; do
    ip rule del fwmark "$MARK" table "$TABLE_NAME" priority "$RULE_PRIO" 2>/dev/null || break
  done
  ip route flush table "$TABLE_NAME" 2>/dev/null || true
  echo '已拆除隧道出口隔离。'
}

do_status() {
  local uid
  uid="$(tunnel_uid)"
  echo "=== 账号: $TUNNEL_USER uid=$uid ==="
  echo "=== 热点网卡 ==="
  ip -br addr show "$USB_IFACE" 2>/dev/null || echo '  (不存在)'
  echo "=== tunnel 路由表 ==="
  ip route show table "$TABLE_NAME" 2>/dev/null || echo '  (空)'
  echo "=== ip rule ==="
  ip rule show | grep -E "fwmark" || echo '  (无)'
  echo "=== mangle OUTPUT ==="
  iptables -t mangle -S OUTPUT 2>/dev/null | grep -E "owner" || echo '  (无)'
  echo "=== mangle INPUT（应无 CONNMARK）==="
  iptables -t mangle -S INPUT 2>/dev/null | grep -E "CONNMARK" && echo '  [警告] INPUT 仍有 CONNMARK，会导致回程断链' || echo '  (干净)'
}

do_test() {
  local uid
  uid="$(tunnel_uid)"
  echo '=== cloudflared 身份出网 ==='
  sudo -u "$TUNNEL_USER" -- timeout 12 curl -4 -s -o /dev/null -w 'cloudflare=%{http_code}\n' \
    https://www.cloudflare.com/cdn-cgi/trace || echo 'FAILED'
  sudo -u "$TUNNEL_USER" -- timeout 12 curl -4 -s -o /dev/null -w 'trycloudflare=%{http_code}\n' \
    -X POST https://api.trycloudflare.com/tunnel || echo 'FAILED'
  echo '=== 出口 IP 对比 ==='
  echo -n '  cloudflared: '
  sudo -u "$TUNNEL_USER" -- timeout 12 curl -4 -s https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null \
    | grep -E '^ip=' || echo '(取不到)'
  echo -n '  root(公司网): '
  timeout 12 curl -4 -s https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep -E '^ip=' || echo '(取不到)'
  echo '=== 访问本机 nginx ==='
  sudo -u "$TUNNEL_USER" -- timeout 6 curl -s -o /dev/null -w '  nginx=%{http_code}\n' \
    http://127.0.0.1/health/ || echo '  FAILED'
}

case "${1:-status}" in
  up) do_up ;;
  down) do_down ;;
  status) do_status ;;
  test) do_test ;;
  *) echo "用法：sudo bash $0 {up|down|status|test}"; exit 1 ;;
esac
