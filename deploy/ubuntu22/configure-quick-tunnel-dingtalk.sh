#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR=/etc/after-sales-warehouse
CONFIG_FILE="$CONFIG_DIR/quick-tunnel-dingtalk.env"
SERVICE_NAME=after-sales-warehouse-quick-tunnel

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行此脚本。'
  exit 1
fi

if ! id -u cloudflared >/dev/null 2>&1; then
  echo '未找到 cloudflared 系统用户，请先执行 enable-quick-tunnel.sh。'
  exit 1
fi

read -r -p 'Tunnel 专用钉钉机器人 Webhook：' webhook
if [[ -z "$webhook" || "$webhook" != https://* ]]; then
  echo 'Webhook 不能为空且必须使用 HTTPS。'
  exit 1
fi
read -r -s -p '钉钉机器人加签密钥（未启用加签可直接回车）：' secret
echo
read -r -p '需要 @ 的钉钉手机号（多个用英文逗号分隔，可回车跳过）：' at_mobile
read -r -p '是否 @ 全体成员？[0]：' at_all
at_all="${at_all:-0}"

install -d -o root -g cloudflared -m 750 "$CONFIG_DIR"
temporary_file="$(mktemp)"
trap 'rm -f "$temporary_file"' EXIT
cat > "$temporary_file" <<EOF
DINGTALK_WEBHOOK=$webhook
DINGTALK_SECRET=$secret
DINGTALK_AT_MOBILE=$at_mobile
DINGTALK_AT_ALL=$at_all
EOF
install -o root -g cloudflared -m 640 "$temporary_file" "$CONFIG_FILE"

echo
echo "钉钉配置已保存到 $CONFIG_FILE"
echo '重启 Tunnel 后会识别新地址并发送钉钉消息。'
systemctl restart "$SERVICE_NAME"
echo '已请求重启 Tunnel。查看日志：'
echo "sudo journalctl -u $SERVICE_NAME -f"
