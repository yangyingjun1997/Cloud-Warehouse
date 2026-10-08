#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_GROUP=warehouse
APP_DIR=/opt/after-sales-warehouse
DATA_DIR=/srv/after-sales-warehouse
RELEASE_ROOT="$APP_DIR/releases"
DB_NAME=warehouse
DB_USER=warehouse_app
SERVER_NAME=_
IMPORT_SQLITE=0
OFFLINE_UPDATE=0
# Web 入口按用途区分：
#   日常访问走 Wi-Fi 内网地址和 Tunnel 域名（current-url 记录的地址）；
#   调试链路（例如 192.168.123.101）不一定处于连接状态，用 --extra-host 常驻白名单；
#   只用于 git 的网口不提供 Web 访问，用 --exclude-iface 排除，避免被登记成用户入口。
EXCLUDED_IFACES=()
EXTRA_HOSTS=()
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE_BACKEND="$REPO_ROOT/backend"

usage() {
  cat <<'EOF'
Usage: sudo bash deploy/ubuntu22/install.sh [options]

  --host server-ip-or-dns   主访问地址，例如 Wi-Fi 内网地址或内网域名
  --import-sqlite           首次部署时导入 backend/db.sqlite3
  --offline-update          不下载系统包与 Python 包，只更新代码和依赖校验
  --exclude-iface name      该网卡的地址不作为 Web 入口（可重复，例如只用 git 的网口）
  --extra-host host         额外登记的访问地址（可重复，例如调试地址 192.168.123.101）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) SERVER_NAME="$2"; shift 2 ;;
    --import-sqlite) IMPORT_SQLITE=1; shift ;;
    --offline-update) OFFLINE_UPDATE=1; shift ;;
    --exclude-iface) EXCLUDED_IFACES+=("$2"); shift 2 ;;
    --extra-host) EXTRA_HOSTS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run this script with sudo."
  exit 1
fi
if [[ ! -f "$SOURCE_BACKEND/manage.py" ]]; then
  echo "Cannot find backend/manage.py next to the deployment script."
  exit 1
fi

# ---------------------------------------------------------------------------
# 出口地址白名单
# 服务器通常同时具备有线、WiFi、内网专线等多个出口，客户端从任意一条都能访问。
# 若白名单只登记单一地址，经由其他网卡访问时 Django 会直接以 400 拒绝。
# 因此每次部署都重新收集本机对外 IPv4，并幂等写回 .env 与 Nginx。
#
# 例外：明确只用于 git 的网口不提供 Web 访问，用 --exclude-iface 排除；
# 调试链路可能部署时没有连线，用 --extra-host 常驻登记。
# ---------------------------------------------------------------------------
is_ipv4() {
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]
}

collect_lan_ipv4() {
  local excluded_csv
  excluded_csv="$(IFS=,; printf '%s' "${EXCLUDED_IFACES[*]:-}")"
  ip -4 -o addr show scope global 2>/dev/null | awk -v excluded_csv="$excluded_csv" '
    BEGIN {
      split(excluded_csv, items, ",")
      for (i in items) if (items[i] != "") excluded[items[i]] = 1
    }
    $2 ~ /^(docker|br-|veth|virbr|tun|tap)/ { next }
    ($2 in excluded) { next }
    { split($4, a, "/"); print a[1] }' | sort -u
}

get_env_value() {
  awk -v key="$2" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$1"
}

# 原地重写某个键，保持 inode 与文件权限不变；键不存在时追加到末尾。
set_env_value() {
  local env_file="$1" key="$2" value="$3" tmp_file
  tmp_file="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' "$env_file" > "$tmp_file"
  cat "$tmp_file" > "$env_file"
  rm -f "$tmp_file"
}

# 向逗号列表追加尚未包含的项，已有项保持原样。
append_missing_items() {
  local current="$1" item
  for item in ${2//,/ }; do
    [[ -z "$item" ]] && continue
    [[ ",$current," == *",$item,"* ]] && continue
    if [[ -z "$current" ]]; then
      current="$item"
    else
      current="$current,$item"
    fi
  done
  printf '%s' "$current"
}

mapfile -t LAN_ADDRESSES < <(collect_lan_ipv4)

# Web 入口地址统一在这里登记一次，供 ALLOWED_HOSTS、Nginx 和结尾提示复用。
# 顺序：主访问地址 -> 当前在线的出口地址 -> 调试地址 -> 本机回环。
WEB_ENTRY_ADDRESSES=()
for candidate in ${LAN_ADDRESSES[@]:-} ${EXTRA_HOSTS[@]:-}; do
  [[ -z "$candidate" ]] && continue
  WEB_ENTRY_ADDRESSES+=("$candidate")
done

# _ 是 Nginx 的 catch-all 记号，不是真实主机名，不能进入 Django 白名单。
ALLOWED_HOSTS_CSV=""
for candidate in "$SERVER_NAME" ${WEB_ENTRY_ADDRESSES[@]:-} 127.0.0.1 localhost; do
  [[ -z "$candidate" || "$candidate" == "_" ]] && continue
  ALLOWED_HOSTS_CSV="$(append_missing_items "$ALLOWED_HOSTS_CSV" "$candidate")"
done

# CSRF 校验比对的完整 Origin，需要提供 scheme。
# 裸 IP 调试入口走 http；主机名形式的地址同时登记 http 和 https，
# 便于内网 HTTPS 或 Tunnel 域名直接可用。
CSRF_ORIGINS_CSV=""
for candidate in ${WEB_ENTRY_ADDRESSES[@]:-}; do
  if is_ipv4 "$candidate"; then
    CSRF_ORIGINS_CSV="$(append_missing_items "$CSRF_ORIGINS_CSV" "http://$candidate")"
  else
    CSRF_ORIGINS_CSV="$(append_missing_items "$CSRF_ORIGINS_CSV" "http://$candidate")"
    CSRF_ORIGINS_CSV="$(append_missing_items "$CSRF_ORIGINS_CSV" "https://$candidate")"
  fi
done

# Nginx server_name 用空格分隔，同样登记全部 Web 入口地址。
RESOLVED_SERVER_NAME="$SERVER_NAME"
if [[ -n "${WEB_ENTRY_ADDRESSES[*]:-}" ]]; then
  RESOLVED_SERVER_NAME="$SERVER_NAME ${WEB_ENTRY_ADDRESSES[*]:-}"
fi

snapshot_existing_backend() {
  if [[ ! -d "$APP_DIR/backend" || ! -f "$APP_DIR/backend/manage.py" ]]; then
    return 0
  fi
  local stamp snapshot_dir
  stamp="$(date +%Y%m%d_%H%M%S)"
  snapshot_dir="$RELEASE_ROOT/$stamp"
  install -d -m 755 "$snapshot_dir"
  tar -C "$APP_DIR" \
    --exclude='backend/.env' \
    --exclude='backend/db.sqlite3' \
    --exclude='backend/media' \
    --exclude='backend/staticfiles' \
    --exclude='*/__pycache__' \
    --exclude='*.pyc' \
    -czf "$snapshot_dir/backend.tar.gz" backend
  sha256sum "$snapshot_dir/backend.tar.gz" > "$snapshot_dir/SHA256SUMS"
  cat > "$snapshot_dir/metadata.txt" <<EOF
created_at=$(date --iso-8601=seconds)
source=pre_deploy_snapshot
host=$(hostname)
EOF
  echo "已保存当前程序代码快照：$snapshot_dir"

  mapfile -t old_snapshots < <(find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | awk 'NR > 10 {print $2}')
  if (( ${#old_snapshots[@]} > 0 )); then
    rm -rf "${old_snapshots[@]}"
  fi
}

if [[ $OFFLINE_UPDATE -eq 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3-venv python3-dev build-essential libpq-dev postgresql postgresql-contrib nginx rsync curl openssl
else
  for command_name in python3 psql nginx rsync curl openssl; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "Offline update cannot continue: missing command $command_name."
      exit 1
    }
  done
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
# 部署/更新前对生产数据库做一次即时快照，避免代码更新失败时只能回退到昨夜备份。
# 只在数据库已存在时执行（首次部署无库可备），失败不阻断部署但明确提示。
snapshot_existing_database() {
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null | grep -q 1; then
    return 0
  fi
  local stamp target
  stamp="$(date +%Y%m%d_%H%M%S)"
  target="$DATA_DIR/backups/pre-deploy-$stamp"
  install -d -m 755 "$target"
  if sudo -u postgres pg_dump --format=custom "$DB_NAME" > "$target/database.dump"; then
    sha256sum "$target/database.dump" > "$target/SHA256SUMS"
    echo "已保存部署前数据库快照：$target"
  else
    echo "[警告] 部署前数据库快照失败，继续部署（请尽快人工确认 pg_dump 可用）。"
    rm -rf "$target"
  fi
  # 部署前快照保留最近 20 份，超出后清掉最旧的。
  mapfile -t old_predeploy < <(find "$DATA_DIR/backups" -mindepth 1 -maxdepth 1 -type d -name 'pre-deploy-*' -printf '%T@ %p\n' | sort -rn | awk 'NR > 20 {print $2}')
  if (( ${#old_predeploy[@]} > 0 )); then
    rm -rf "${old_predeploy[@]}"
  fi
}

install -d -o "$APP_USER" -g "$APP_GROUP" "$APP_DIR" "$DATA_DIR/media" "$DATA_DIR/static" "$DATA_DIR/backups"
install -d -m 755 "$RELEASE_ROOT"
snapshot_existing_backend
snapshot_existing_database
rsync -a --delete --exclude '.env' --exclude 'db.sqlite3' --exclude '__pycache__' --exclude '*.pyc' "$SOURCE_BACKEND/" "$APP_DIR/backend/"

python3 -m venv "$APP_DIR/venv"
if [[ $OFFLINE_UPDATE -eq 0 ]]; then
  "$APP_DIR/venv/bin/pip" install --upgrade pip
  "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
else
  "$APP_DIR/venv/bin/python" -c "import corsheaders, django, dotenv, openpyxl, PIL, psycopg, pypinyin, qrcode, rest_framework; print('Existing Python dependencies are available.')"
  "$APP_DIR/venv/bin/pip" check
fi

DB_PASSWORD="$(openssl rand -base64 36 | tr -d '\n')"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi

if [[ ! -f "$APP_DIR/backend/.env" ]]; then
  SECRET_KEY="$(openssl rand -hex 48)"
  cat > "$APP_DIR/backend/.env" <<EOF
DEBUG=0
SECRET_KEY=$SECRET_KEY
ALLOWED_HOSTS=$ALLOWED_HOSTS_CSV
TIME_ZONE=Asia/Shanghai
DB_ENGINE=django.db.backends.postgresql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=127.0.0.1
DB_PORT=5432
MEDIA_ROOT=$DATA_DIR/media
STATIC_ROOT=$DATA_DIR/static
CORS_ALLOW_ALL_ORIGINS=0
USE_HTTPS=0
SECURE_HSTS_SECONDS=0
SECURE_HSTS_INCLUDE_SUBDOMAINS=0
CSRF_TRUSTED_ORIGINS=$CSRF_ORIGINS_CSV
RESERVATION_EXPIRY_HOURS=72
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
DINGTALK_AGENT_ID=
CONSISTENCY_STATE_FILE=$DATA_DIR/consistency-check.state.json
EOF
  chmod 640 "$APP_DIR/backend/.env"
fi

# .env 可能来自更早的部署，其中的白名单缺少后来新增的出口地址。
# 这里补齐而不是重建文件，以免丢失 SECRET_KEY、数据库口令等运行期数据。
ENV_FILE="$APP_DIR/backend/.env"
env_allowed="$(get_env_value "$ENV_FILE" ALLOWED_HOSTS)"
for candidate in "$SERVER_NAME" ${WEB_ENTRY_ADDRESSES[@]:-} 127.0.0.1 localhost; do
  [[ -z "$candidate" || "$candidate" == "_" ]] && continue
  env_allowed="$(append_missing_items "$env_allowed" "$candidate")"
done
set_env_value "$ENV_FILE" ALLOWED_HOSTS "$env_allowed"

env_csrf="$(get_env_value "$ENV_FILE" CSRF_TRUSTED_ORIGINS)"
for candidate in ${WEB_ENTRY_ADDRESSES[@]:-}; do
  if is_ipv4 "$candidate"; then
    env_csrf="$(append_missing_items "$env_csrf" "http://$candidate")"
  else
    env_csrf="$(append_missing_items "$env_csrf" "http://$candidate")"
    env_csrf="$(append_missing_items "$env_csrf" "https://$candidate")"
  fi
done
set_env_value "$ENV_FILE" CSRF_TRUSTED_ORIGINS "$env_csrf"
set_env_value "$ENV_FILE" CONSISTENCY_STATE_FILE "$DATA_DIR/consistency-check.state.json"

chmod 640 "$ENV_FILE"
chown "$APP_USER:$APP_GROUP" "$ENV_FILE"

if [[ $IMPORT_SQLITE -eq 1 && -f "$SOURCE_BACKEND/db.sqlite3" && ! -f "$DATA_DIR/.sqlite-imported" ]]; then
  echo "Exporting existing SQLite data for one-time PostgreSQL import..."
  DB_ENGINE=django.db.backends.sqlite3 DB_NAME="$SOURCE_BACKEND/db.sqlite3" "$APP_DIR/venv/bin/python" "$APP_DIR/backend/manage.py" dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.Permission --exclude sessions > /tmp/after-sales-warehouse-initial.json
fi

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR" "$DATA_DIR"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" "$APP_DIR/backend/manage.py" migrate --noinput
if [[ -f /tmp/after-sales-warehouse-initial.json && ! -f "$DATA_DIR/.sqlite-imported" ]]; then
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" "$APP_DIR/backend/manage.py" loaddata /tmp/after-sales-warehouse-initial.json
  touch "$DATA_DIR/.sqlite-imported"
  rm -f /tmp/after-sales-warehouse-initial.json
fi
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" "$APP_DIR/backend/manage.py" collectstatic --noinput

install -m 644 "$SCRIPT_DIR/after-sales-warehouse.service" /etc/systemd/system/after-sales-warehouse.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-low-stock-summary.service" /etc/systemd/system/after-sales-warehouse-low-stock-summary.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-low-stock-summary.timer" /etc/systemd/system/after-sales-warehouse-low-stock-summary.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-return-reminder.service" /etc/systemd/system/after-sales-warehouse-return-reminder.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-return-reminder.timer" /etc/systemd/system/after-sales-warehouse-return-reminder.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-reservation-expiry.service" /etc/systemd/system/after-sales-warehouse-reservation-expiry.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-reservation-expiry.timer" /etc/systemd/system/after-sales-warehouse-reservation-expiry.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-backup.service" /etc/systemd/system/after-sales-warehouse-backup.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-backup.timer" /etc/systemd/system/after-sales-warehouse-backup.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-health.service" /etc/systemd/system/after-sales-warehouse-health.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-health.timer" /etc/systemd/system/after-sales-warehouse-health.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-consistency.service" /etc/systemd/system/after-sales-warehouse-consistency.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-consistency.timer" /etc/systemd/system/after-sales-warehouse-consistency.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-security-cleanup.service" /etc/systemd/system/after-sales-warehouse-security-cleanup.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-security-cleanup.timer" /etc/systemd/system/after-sales-warehouse-security-cleanup.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-logrotate" /etc/logrotate.d/after-sales-warehouse
install -d -m 755 /etc/systemd/journald.conf.d
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-journald.conf" /etc/systemd/journald.conf.d/after-sales-warehouse.conf
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-tmpfiles.conf" /etc/tmpfiles.d/after-sales-warehouse.conf
install -m 750 "$SCRIPT_DIR/backup.sh" /usr/local/sbin/after-sales-warehouse-backup
install -m 750 "$SCRIPT_DIR/runtime-status.sh" /usr/local/sbin/after-sales-warehouse-status
install -m 750 "$SCRIPT_DIR/health-monitor.sh" /usr/local/sbin/after-sales-warehouse-health
install -m 750 "$SCRIPT_DIR/restore-drill.sh" /usr/local/sbin/after-sales-warehouse-restore-drill
install -m 750 "$SCRIPT_DIR/rollback.sh" /usr/local/sbin/after-sales-warehouse-rollback
install -m 750 "$SCRIPT_DIR/security-check.sh" /usr/local/sbin/after-sales-warehouse-security-check
install -d -m 755 /usr/local/libexec
install -m 750 "$SCRIPT_DIR/dingtalk-notify.py" /usr/local/libexec/after-sales-warehouse-dingtalk-notify
sed "s/__SERVER_NAME__/$RESOLVED_SERVER_NAME/g" "$SCRIPT_DIR/nginx-after-sales-warehouse.conf" > /etc/nginx/sites-available/after-sales-warehouse
ln -sfn /etc/nginx/sites-available/after-sales-warehouse /etc/nginx/sites-enabled/after-sales-warehouse
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now postgresql
systemctl enable after-sales-warehouse
# Reloaded source and templates are only used by newly started Gunicorn workers.
systemctl restart after-sales-warehouse
systemctl enable --now after-sales-warehouse-low-stock-summary.timer
systemctl enable --now after-sales-warehouse-return-reminder.timer
systemctl enable --now after-sales-warehouse-reservation-expiry.timer
systemctl enable --now after-sales-warehouse-backup.timer
systemctl enable --now after-sales-warehouse-health.timer
systemctl enable --now after-sales-warehouse-consistency.timer
systemctl enable --now after-sales-warehouse-security-cleanup.timer
systemctl enable nginx
# `enable --now` does not reload an already-running Nginx process.
systemctl reload nginx

rm -f /etc/cron.d/after-sales-warehouse-backup
systemd-tmpfiles --create /etc/tmpfiles.d/after-sales-warehouse.conf
systemctl restart systemd-journald
systemctl restart after-sales-warehouse-health.timer after-sales-warehouse-backup.timer

cat > "$APP_DIR/current-release" <<EOF
deployed_at=$(date --iso-8601=seconds)
server_name=$SERVER_NAME
source_backend=$SOURCE_BACKEND
host=$(hostname)
EOF
chown "$APP_USER:$APP_GROUP" "$APP_DIR/current-release"

echo "Deployment completed. Web entry points:"
for candidate in ${WEB_ENTRY_ADDRESSES[@]:-}; do
  echo "  http://$candidate/"
done
if [[ "$SERVER_NAME" != "_" && "$SERVER_NAME" != "${WEB_ENTRY_ADDRESSES[*]:-}" ]]; then
  echo "  http://$SERVER_NAME/ (declared --host)"
fi
if (( ${#EXCLUDED_IFACES[@]} > 0 )); then
  echo "未登记为 Web 入口的网口：${EXCLUDED_IFACES[*]}（例如只用于 git 的网口）"
fi
if systemctl cat after-sales-warehouse-quick-tunnel >/dev/null 2>&1; then
  echo "临时域名入口：sudo /usr/local/sbin/after-sales-warehouse-quick-tunnel-url"
fi
echo "Verify with: systemctl status after-sales-warehouse nginx"
echo "Security check: sudo /usr/local/sbin/after-sales-warehouse-security-check"
echo "Rollback list: sudo /usr/local/sbin/after-sales-warehouse-rollback --list"
