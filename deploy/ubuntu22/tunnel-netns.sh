#!/usr/bin/env bash
# 隧道网络隔离：用 network namespace 把外接 USB 无线网卡（移动热点）单独隔离，
# 只让 cloudflared 使用它，其它进程完全看不到这张网卡。
#
# 结构：
#   主 netns: enp87s0(公司git) / enp88s0(SSH) / wlp89s0(公司WiFi) / veth-tunnel 10.200.0.2
#   tunnel ns: wlx*(移动热点) / tunnel0 10.200.0.1 → 只有 cloudflared 在里面
#
# 用法：
#   sudo bash tunnel-netns.sh up|down|status|test|dhcp
set -Eeuo pipefail

NS=tunnel
VETH_HOST=veth-tunnel
VETH_NS=tunnel0
HOST_IP=10.200.0.2
NS_IP=10.200.0.1
USB_IFACE="${USB_IFACE:-wlx90de800bb7bf}"

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行。' >&2
  exit 1
fi

# 网卡可能随时被拔出，所有查询都要容错
iface_in_main() { ip link show "$USB_IFACE" >/dev/null 2>&1; }
iface_in_ns()   { ip netns exec "$NS" ip link show "$USB_IFACE" >/dev/null 2>&1; }

do_up() {
  if ! iface_in_main && ! iface_in_ns; then
    echo "[错误] 找不到网卡 $USB_IFACE；可用网卡：" >&2
    ip -br link show | awk '{print "  " $1}' >&2
    exit 1
  fi

  # 1) NetworkManager 先撒手，否则它会把网卡重新拉回主 netns
  nmcli dev set "$USB_IFACE" managed no >/dev/null 2>&1 || true

  # 2) 网卡若在主 netns，必须先 down 再移入（up 状态无法迁移）
  if iface_in_main; then
    echo "把 $USB_IFACE 从主网络移入 $NS ..."
    ip link set "$USB_IFACE" down || true
    ip link set "$USB_IFACE" netns "$NS"
  fi

  # 3) netns 本身
  ip netns list | grep -qx "$NS" || ip netns add "$NS"

  # 4) veth 打通：让 netns 内的 cloudflared 能访问主 netns 的 nginx
  if ! ip link show "$VETH_HOST" >/dev/null 2>&1; then
    ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
  fi
  ip link show "$VETH_NS" >/dev/null 2>&1 && ip link set "$VETH_NS" netns "$NS" || true

  ip addr replace "$HOST_IP/24" dev "$VETH_HOST"
  ip link set "$VETH_HOST" up

  ip netns exec "$NS" ip addr replace "$NS_IP/24" dev "$VETH_NS"
  ip netns exec "$NS" ip link set "$VETH_NS" up
  ip netns exec "$NS" ip link set lo up

  # 5) netns 里的网卡拉起
  ip netns exec "$NS" ip link set "$USB_IFACE" up

  echo '隔离建立完成。下一步执行：sudo bash '"$0"' dhcp'
}

do_dhcp() {
  if ! iface_in_ns; then
    echo "[错误] $USB_IFACE 不在 $NS 内，请先执行 up" >&2
    exit 1
  fi
  echo "在 $NS 内为 $USB_IFACE 申请 DHCP 地址..."
  ip netns exec "$NS" dhclient -r "$USB_IFACE" >/dev/null 2>&1 || true
  ip netns exec "$NS" dhclient -1 -v "$USB_IFACE" 2>&1 | tail -8 || true
  echo
  echo '=== 隧道 ns 网络 ==='
  ip netns exec "$NS" ip -br addr show
  ip netns exec "$NS" ip route show
}

do_down() {
  if ip netns list | grep -qx "$NS"; then
    ip netns exec "$NS" dhclient -r "$USB_IFACE" >/dev/null 2>&1 || true
    ip netns exec "$NS" ip link set "$USB_IFACE" down >/dev/null 2>&1 || true
    ip netns exec "$NS" ip link set "$USB_IFACE" netns 1 >/dev/null 2>&1 || true
    ip netns del "$NS" >/dev/null 2>&1 || true
  fi
  ip link del "$VETH_HOST" >/dev/null 2>&1 || true
  nmcli dev set "$USB_IFACE" managed yes >/dev/null 2>&1 || true
  nmcli dev connect "$USB_IFACE" >/dev/null 2>&1 || true
  echo '已拆除隧道隔离，网卡归还主网络。'
}

do_status() {
  echo "=== netns 列表 ==="
  ip netns list || echo '(空)'
  if ip netns list | grep -qx "$NS"; then
    echo "=== $NS 内网卡 ==="
    ip netns exec "$NS" ip -br addr show
    echo "=== $NS 路由 ==="
    ip netns exec "$NS" ip route show
  fi
  echo "=== 主 ns veth ==="
  ip -br addr show "$VETH_HOST" 2>/dev/null || echo '(无)'
  echo "=== USB 网卡位置 ==="
  if iface_in_main; then echo "主 netns"; elif iface_in_ns; then echo "$NS"; else echo "不存在"; fi
}

do_test() {
  echo '=== netns 内 DNS ==='
  ip netns exec "$NS" sh -c 'cat /etc/resolv.conf 2>/dev/null | head -4' || true
  echo '=== netns 内公网可达性 ==='
  ip netns exec "$NS" timeout 8 curl -4 -s -o /dev/null -w 'cloudflare=%{http_code}\n' \
    https://www.cloudflare.com/cdn-cgi/trace || echo 'cloudflare FAILED'
  ip netns exec "$NS" timeout 8 curl -4 -s -o /dev/null -w 'trycloudflare_api=%{http_code}\n' \
    -X POST https://api.trycloudflare.com/tunnel || echo 'trycloudflare FAILED'
  echo '=== netns 访问主 ns nginx（关键）==='
  ip netns exec "$NS" timeout 5 curl -s -o /dev/null -w 'nginx_via_veth=%{http_code}\n' \
    http://10.200.0.2/health/ || echo 'nginx FAILED'
}

case "${1:-status}" in
  up) do_up ;;
  dhcp) do_dhcp ;;
  down) do_down ;;
  status) do_status ;;
  test) do_test ;;
  *) echo "用法：sudo bash $0 {up|dhcp|down|status|test}"; exit 1 ;;
esac
