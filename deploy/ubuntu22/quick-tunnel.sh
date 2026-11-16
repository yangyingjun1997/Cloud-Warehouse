#!/usr/bin/env bash
# 临时域名（Cloudflare Quick Tunnel）登记与撤销。
#
# 用途：当打印电脑等设备无法访问内网 IP 时，用临时公网域名打通。
# 注意：这属于内网穿透。公司网络安全规定禁止穿透时，不应使用本脚本。
#
# 用法：
#   sudo bash deploy/ubuntu22/quick-tunnel.sh start    # 启动隧道并登记域名
#   sudo bash deploy/ubuntu22/quick-tunnel.sh status   # 查看当前域名
#   sudo bash deploy/ubuntu22/quick-tunnel.sh stop     # 停止并撤下白名单
set -Eeuo pipefail

ENV_FILE=/srv/after-sales-warehouse/app.env
LOG_FILE=/var/log/cloudflared-quick-tunnel.log
PID_FILE=/run/cloudflared-quick-tunnel.pid
SERVICE_NAME=after-sales-warehouse

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行。'
  exit 1
fi

current_domain() {
  grep -oE 'https://[A-Za-z0-9][A-Za-z0-9.-]*\.trycloudflare\.com' "$LOG_FILE" 2>/dev/null \
    | grep -vE 'api|cp|regionarg|cfargotunnel|probe' \
    | tail -1 || true
}

edit_env_csv() {
  local key="$1" item="$2" remove="$3"
  python3 - "$ENV_FILE" "$key" "$item" "$remove" <<'PY'
import sys
path, key, item, remove = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == '1'
lines = open(path, encoding='utf-8').read().splitlines()
out = []
for line in lines:
    if line.startswith(key + '='):
        items = [x.strip() for x in line.split('=', 1)[1].split(',') if x.strip()]
        if remove:
            items = [x for x in items if x.lower() != item.lower()]
        elif item.lower() not in {x.lower() for x in items}:
            items.append(item)
        out.append(f'{key}=' + ','.join(items))
    else:
        out.append(line)
open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
PY
}

case "${1:-start}" in
  start)
    install -d -m 755 "$(dirname "$LOG_FILE")"
    : > "$LOG_FILE"
    nohup /usr/local/bin/cloudflared tunnel --no-autoupdate \
      --url http://127.0.0.1:80 --http-host-header 127.0.0.1 \
      >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "隧道进程 PID=$(cat "$PID_FILE")，等待分配域名..."

    domain=''
    for _ in $(seq 1 40); do
      sleep 2
      domain="$(current_domain)"
      [[ -n "$domain" ]] && break
    done

    if [[ -z "$domain" ]]; then
      echo '未能获取域名，请检查：cat '"$LOG_FILE"
      exit 1
    fi

    host="${domain#https://}"
    edit_env_csv ALLOWED_HOSTS ".$host" 0
    edit_env_csv CSRF_TRUSTED_ORIGINS "$domain" 0
    systemctl restart "$SERVICE_NAME"
    sleep 4

    echo
    echo '临时域名已就绪：'
    echo "  $domain"
    echo
    echo '验证：'
    curl -s -o /dev/null -w "  /health/ -> %{http_code}\n" --max-time 25 "$domain/health/" || true
    ;;

  status)
    domain="$(current_domain)"
    if [[ -z "$domain" ]]; then
      echo '当前没有运行中的临时域名。'
      exit 1
    fi
    echo "当前临时域名：$domain"
    curl -s -o /dev/null -w "  /health/ -> %{http_code}\n" --max-time 25 "$domain/health/" || true
    ;;

  stop)
    if [[ -f "$PID_FILE" ]]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
      rm -f "$PID_FILE"
    fi
    pkill -f 'cloudflared tunnel' 2>/dev/null || true

    domain="$(current_domain)"
    if [[ -n "$domain" ]]; then
      host="${domain#https://}"
      edit_env_csv ALLOWED_HOSTS ".$host" 1
      edit_env_csv CSRF_TRUSTED_ORIGINS "$domain" 1
      systemctl restart "$SERVICE_NAME"
      echo "已停止隧道并撤销白名单：$domain"
    else
      echo '已停止隧道。'
    fi
    ;;

  *)
    echo "用法：sudo bash $0 {start|status|stop}"
    exit 1
    ;;
esac
