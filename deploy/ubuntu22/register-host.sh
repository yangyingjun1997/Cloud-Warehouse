#!/usr/bin/env bash
# 登记一个访问入口（IP 或域名）到服务器配置。
#
# 用法：
#   sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local
#   sudo bash deploy/ubuntu22/register-host.sh 192.168.123.101
#   sudo bash deploy/ubuntu22/register-host.sh wms.bwt.local --remove
#
# 作用：
#   1. 把主机名追加到 .env 的 ALLOWED_HOSTS（Django 只接受名单内的 Host）
#   2. 同时登记 CSRF_TRUSTED_ORIGINS（表单提交需要完整 origin）
#   3. 更新 nginx 的 server_name
#   4. 重载 nginx 并重启应用
#
# 域名的前提：DNS 或 hosts 已能把该域名解析到本机 IP。
# 本脚本不修改 DNS。
set -Eeuo pipefail

APP_DIR=/opt/after-sales-warehouse
ENV_FILE="$APP_DIR/backend/.env"
NGINX_AVAILABLE=/etc/nginx/sites-available/after-sales-warehouse
SERVICE_NAME=after-sales-warehouse

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行此脚本。'
  exit 1
fi

HOST="${1:-}"
REMOVE=0
for argument in "${@:2}"; do
  case "$argument" in
    --remove) REMOVE=1 ;;
    *) echo "未知参数：$argument"; exit 1 ;;
  esac
done

if [[ -z "$HOST" ]]; then
  echo "用法：sudo bash $0 <IP或域名> [--remove]"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "找不到配置：$ENV_FILE"
  exit 1
fi

# 去掉用户可能带上的协议前缀和路径，只保留 host[:port]
HOST="${HOST#http://}"
HOST="${HOST#https://}"
HOST="${HOST%%/*}"
HOST="$(echo "$HOST" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"

if [[ -z "$HOST" ]]; then
  echo '主机名为空。'
  exit 1
fi

read_env() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true
}

write_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # 用 python 处理，避免 sed 在含特殊字符时出错
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding='utf-8').read().splitlines()
out, done = [], False
for line in lines:
    if line.startswith(key + '='):
        out.append(f'{key}={value}')
        done = True
    else:
        out.append(line)
if not done:
    out.append(f'{key}={value}')
open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# 在逗号分隔列表里增加/删除一项
edit_csv() {
  local csv="$1" item="$2" remove="$3"
  python3 - "$csv" "$item" "$remove" <<'PY'
import sys
csv, item, remove = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
items = [x.strip() for x in csv.split(',') if x.strip()]
if remove:
    items = [x for x in items if x.lower() != item.lower()]
else:
    if item.lower() not in {x.lower() for x in items}:
        items.append(item)
# 保持 127.0.0.1 / localhost 在最前
priority = ['127.0.0.1', 'localhost']
head = [x for x in items if x in priority]
tail = [x for x in items if x not in priority]
print(','.join(head + tail))
PY
}

echo "处理入口：$HOST  (操作：$([[ $REMOVE -eq 1 ]] && echo 移除 || echo 添加))"

# ---- 1. ALLOWED_HOSTS ----
current_allowed="$(read_env ALLOWED_HOSTS)"
new_allowed="$(edit_csv "$current_allowed" "$HOST" "$REMOVE")"
write_env ALLOWED_HOSTS "$new_allowed"
echo "  ALLOWED_HOSTS=$new_allowed"

# ---- 2. CSRF_TRUSTED_ORIGINS ----
current_csrf="$(read_env CSRF_TRUSTED_ORIGINS)"
for scheme in http https; do
  origin="${scheme}://${HOST}"
  new_csrf="$(edit_csv "$current_csrf" "$origin" "$REMOVE")"
  current_csrf="$new_csrf"
done
write_env CSRF_TRUSTED_ORIGINS "$current_csrf"
echo "  CSRF_TRUSTED_ORIGINS=$current_csrf"

# ---- 3. nginx server_name ----
if [[ -f "$NGINX_AVAILABLE" ]]; then
  python3 - "$NGINX_AVAILABLE" "$HOST" "$REMOVE" <<'PY'
import re
import sys
path, host, remove = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
text = open(path, encoding='utf-8').read()
match = re.search(r'^\s*server_name\s+([^;]+);', text, re.MULTILINE)
if not match:
    sys.exit(0)
names = [x for x in match.group(1).split() if x]
if remove:
    names = [x for x in names if x.lower() != host.lower()]
else:
    if host.lower() not in {x.lower() for x in names}:
        names.append(host)

# 去重但保持顺序，避免历史上多处登记同一地址时反复堆积
seen = set()
unique = []
for name in names:
    low = name.lower()
    if low in seen:
        continue
    seen.add(low)
    unique.append(name)
names = unique

new_line = '    server_name ' + ' '.join(names) + ';'
text = text[:match.start()] + new_line + text[match.end():]
open(path, 'w', encoding='utf-8').write(text)
print('  nginx server_name:', ' '.join(names))
PY
fi

# ---- 4. 重载服务 ----
if systemctl is-active --quiet nginx; then
  nginx -t >/dev/null 2>&1 && systemctl reload nginx && echo '  nginx 已重载'
fi
if systemctl is-active --quiet "$SERVICE_NAME"; then
  systemctl restart "$SERVICE_NAME" && echo '  应用已重启'
fi

echo
echo '完成。请用浏览器访问以下地址确认：'
echo "  http://$HOST/"
echo
echo '如果域名打不开，先确认解析：'
echo "  getent hosts $HOST"
echo '没有结果说明 DNS 或 hosts 还没配好，域名需要指向本机 IP。'
echo
echo '数据库里已有的资产记录本身不受影响，本脚本只改访问白名单。'