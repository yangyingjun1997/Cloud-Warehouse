#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/after-sales-warehouse
ENV_FILE="$APP_DIR/backend/.env"
SERVICE_NAME=after-sales-warehouse-quick-tunnel
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行此脚本。'
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "找不到应用配置：$ENV_FILE"
  echo '请先完成 deploy/ubuntu22/install.sh 部署。'
  exit 1
fi

if ! id -u cloudflared >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/cloudflared --create-home --shell /usr/sbin/nologin cloudflared
fi

install_cloudflared() {
  local architecture download_url temporary_file
  architecture="$(dpkg --print-architecture)"
  case "$architecture" in
    amd64) download_url='https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64' ;;
    arm64) download_url='https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64' ;;
    *)
      echo "暂不支持的 CPU 架构：$architecture"
      exit 1
      ;;
  esac

  temporary_file="$(mktemp)"
  trap 'rm -f "$temporary_file"' RETURN
  echo "正在从 Cloudflare 官方发布地址下载 cloudflared（$architecture）..."
  curl --fail --location --retry 3 --connect-timeout 10 --output "$temporary_file" "$download_url"
  install -o root -g root -m 0755 "$temporary_file" /usr/local/bin/cloudflared
  rm -f "$temporary_file"
  trap - RETURN
}

if ! command -v cloudflared >/dev/null 2>&1; then
  install_cloudflared
fi

env_changed=0

# 保证某个环境变量键包含指定片段：值不存在则追加，键不存在则整行写入。
ensure_env_value() {
  local key="$1" needle="$2" append="$3" fallback="$4"
  if grep -q "^${key}=" "$ENV_FILE"; then
    if ! grep -q "^${key}=.*${needle}" "$ENV_FILE"; then
      sed -i "/^${key}=/ s#\$#${append}#" "$ENV_FILE"
      env_changed=1
    fi
  else
    printf "\n${key}=${fallback}\n" >> "$ENV_FILE"
    env_changed=1
  fi
}

# CSRF 需要完整 origin；ALLOWED_HOSTS 只匹配主机部分，使用后缀通配形式。
ensure_env_value CSRF_TRUSTED_ORIGINS 'trycloudflare\.com' ',https://*.trycloudflare.com' 'https://*.trycloudflare.com'
ensure_env_value ALLOWED_HOSTS 'trycloudflare\.com' ',.trycloudflare.com' '127.0.0.1,localhost,.trycloudflare.com'

if [[ $env_changed -eq 1 ]]; then
  chown warehouse:warehouse "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  systemctl restart after-sales-warehouse
fi

install -o root -g root -m 0644 \
  "$SCRIPT_DIR/after-sales-warehouse-quick-tunnel.service" \
  "/etc/systemd/system/$SERVICE_NAME.service"
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/show-quick-tunnel-url.sh" \
  /usr/local/sbin/after-sales-warehouse-quick-tunnel-url
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/quick-tunnel-runner.sh" \
  /usr/local/sbin/after-sales-warehouse-quick-tunnel-runner
install -d -o root -g root -m 755 /usr/local/libexec
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/quick-tunnel-dingtalk.py" \
  /usr/local/libexec/after-sales-warehouse-quick-tunnel-dingtalk
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/configure-quick-tunnel-dingtalk.sh" \
  /usr/local/sbin/configure-after-sales-warehouse-dingtalk
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/test-quick-tunnel-dingtalk.sh" \
  /usr/local/sbin/after-sales-warehouse-quick-tunnel-dingtalk-test
install -d -o cloudflared -g cloudflared -m 750 /var/lib/cloudflared

 systemctl daemon-reload
 systemctl enable "$SERVICE_NAME"
# 重复执行本脚本时必须重启服务，否则运行中的进程仍会用旧脚本提取地址。
 systemctl restart "$SERVICE_NAME"

echo
echo '临时 Tunnel 已启动。查看当前访问地址：'
echo "sudo /usr/local/sbin/after-sales-warehouse-quick-tunnel-url"
echo
echo '查看 Tunnel 日志：'
echo "sudo journalctl -u $SERVICE_NAME -f"
echo
echo '注意：trycloudflare.com 地址可能在服务重启后变化，不要用于正式小程序域名配置。'
