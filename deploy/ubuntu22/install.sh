#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_GROUP=warehouse
APP_DIR=/opt/after-sales-warehouse
DATA_DIR=/srv/after-sales-warehouse
RELEASE_ROOT="$APP_DIR/releases"
CURRENT_LINK="$APP_DIR/current"
ENV_FILE="$DATA_DIR/app.env"
DB_NAME=warehouse
DB_USER=warehouse_app
SERVER_NAME=_
IMPORT_SQLITE=0
OFFLINE_UPDATE=0
# Web 入口按用途区分：
#   日常访问走 Wi-Fi 内网地址和 Tunnel 域名（current-url 记录的地址）；
#   调试链路（例如临时调试网段地址）不一定处于连接状态，用 --extra-host 常驻白名单；
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
  --extra-host host         额外登记的访问地址（可重复，例如调试网段地址）
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

# 发布包在任何服务器变更前先完成身份校验。手工源码部署没有 manifest 时
# 明确标记为未版本化，不能被误认成可追溯的正式构建。
RELEASE_VERSION=unversioned
SOURCE_REVISION=unknown
SOURCE_REVISION_SHORT=unknown
WORKING_TREE_DIRTY=unknown
SOURCE_CONTENT_SHA256=unknown
if [[ -f "$REPO_ROOT/release-manifest.json" ]]; then
  mapfile -t release_metadata < <(python3 - "$REPO_ROOT/release-manifest.json" <<'PY'
import json
import sys

manifest_path = sys.argv[1]
with open(manifest_path, encoding="utf-8-sig") as manifest_file:
    manifest = json.load(manifest_file)

fields = (
    ("version", "unversioned"),
    ("source_revision", "unknown"),
    ("source_revision_short", "unknown"),
    ("working_tree_dirty", "unknown"),
    ("source_content_sha256", "unknown"),
)
for key, default in fields:
    value = manifest.get(key, default)
    if isinstance(value, bool):
        value = str(value).lower()
    value = str(value).replace("\r", " ").replace("\n", " ")
    print(value)
PY
  )
  if (( ${#release_metadata[@]} != 5 )); then
    echo "Invalid release-manifest.json: expected five release metadata values."
    exit 1
  fi
  RELEASE_VERSION="${release_metadata[0]}"
  SOURCE_REVISION="${release_metadata[1]}"
  SOURCE_REVISION_SHORT="${release_metadata[2]}"
  WORKING_TREE_DIRTY="${release_metadata[3]}"
  SOURCE_CONTENT_SHA256="${release_metadata[4]}"
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

# 对外域名：优先取调用方传入的 --public-domain，其次读现有 app.env 的 ALLOWED_HOSTS
# 里第一个非 IP、非 localhost 的条目（即真实域名）。都没有时留空，
# 模板会把 __PUBLIC_DOMAIN__ 替换成空字符串，不产生多余空格。
#
# 注意 app.env 在 /srv/after-sales-warehouse（DATA_DIR），不是 APP_DIR（代码目录）。
RESOLVED_PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-}"
if [[ -z "$RESOLVED_PUBLIC_DOMAIN" && -f "$ENV_FILE" ]]; then
  RESOLVED_PUBLIC_DOMAIN="$(grep -E '^ALLOWED_HOSTS=' "$ENV_FILE" 2>/dev/null \
    | cut -d= -f2- | tr ',' '\n' \
    | grep -vE '^[0-9.]+$|^localhost$|^\*$|^$' | head -1 || true)"
fi
if [[ -n "$RESOLVED_PUBLIC_DOMAIN" ]]; then
  RESOLVED_SERVER_NAME="$RESOLVED_SERVER_NAME $RESOLVED_PUBLIC_DOMAIN"
fi

# server_name 去重：--host 传入的地址也会被网卡探测再次收集，
# 不去重会出现「192.168.x.x 192.168.x.x domain domain」这种重复项。
# Nginx 能接受重复但可读性差，且掩盖配置来源问题。
RESOLVED_SERVER_NAME="$(echo "$RESOLVED_SERVER_NAME" | tr ' ' '\n' | awk 'NF && !seen[$0]++' | tr '\n' ' ' | sed 's/ $//')"

if [[ $OFFLINE_UPDATE -eq 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y software-properties-common
  # Ubuntu 22.04 default python3 is 3.10; Pillow 13 requires Python 3.12.
  if ! command -v python3.12 >/dev/null 2>&1; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
  fi
  apt-get install -y python3.12 python3.12-venv python3.12-dev build-essential libpq-dev postgresql postgresql-contrib nginx rsync curl openssl
else
  for command_name in python3.12 psql nginx rsync curl openssl; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "Offline update cannot continue: missing command $command_name."
      exit 1
    }
  done
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
# 部署/更新前对生产数据库做一次即时快照，避免代码更新失败时只能回退到昨夜备份。
# 只在数据库已存在时执行（首次部署无库可备）；已有数据库快照失败时立即停止部署。
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
    echo "[错误] 部署前数据库快照失败，已停止部署；现有程序和数据库均未覆盖。" >&2
    rm -rf "$target"
    return 1
  fi
  # 部署前快照保留最近 20 份，超出后清掉最旧的。
  mapfile -t old_predeploy < <(find "$DATA_DIR/backups" -mindepth 1 -maxdepth 1 -type d -name 'pre-deploy-*' -printf '%T@ %p\n' | sort -rn | awk 'NR > 20 {print $2}')
  if (( ${#old_predeploy[@]} > 0 )); then
    rm -rf "${old_predeploy[@]}"
  fi
}

install -d -o "$APP_USER" -g "$APP_GROUP" "$APP_DIR" "$DATA_DIR/media" "$DATA_DIR/backups"
install -d -m 755 "$RELEASE_ROOT"
snapshot_existing_database

RELEASE_STAMP="$(date +%Y%m%d_%H%M%S)"
SAFE_RELEASE_VERSION="$(printf '%s' "$RELEASE_VERSION" | tr -c 'A-Za-z0-9._-' '-')"
CANDIDATE_DIR="$RELEASE_ROOT/${RELEASE_STAMP}-${SAFE_RELEASE_VERSION}"
PREVIOUS_RELEASE=""
RELEASE_SWITCHED=0

# Preserve the old in-place layout as the first selectable atomic release.
if [[ ! -L "$CURRENT_LINK" && -f "$APP_DIR/backend/manage.py" && -x "$APP_DIR/venv/bin/python" ]]; then
  LEGACY_DIR="$RELEASE_ROOT/${RELEASE_STAMP}-legacy"
  install -d -m 755 "$LEGACY_DIR"
  cp -a "$APP_DIR/backend" "$LEGACY_DIR/backend"
  cp -a "$APP_DIR/venv" "$LEGACY_DIR/venv"
  if [[ -d "$DATA_DIR/static" ]]; then
    cp -a "$DATA_DIR/static" "$LEGACY_DIR/static"
  else
    install -d -m 755 "$LEGACY_DIR/static"
  fi
  cat > "$LEGACY_DIR/release-info" <<EOF
release_version=legacy-in-place
created_at=$(date --iso-8601=seconds)
database_compatibility=pre-atomic-layout
EOF
  ln -sfn "$LEGACY_DIR" "$CURRENT_LINK"
fi
if [[ -L "$CURRENT_LINK" ]]; then
  PREVIOUS_RELEASE="$(readlink -f "$CURRENT_LINK")"
fi

deployment_failed() {
  local status="$?"
  trap - ERR
  if [[ $RELEASE_SWITCHED -eq 1 && -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE" ]]; then
    ln -sfn "$PREVIOUS_RELEASE" "$APP_DIR/current.rollback"
    mv -Tf "$APP_DIR/current.rollback" "$CURRENT_LINK"
    systemctl restart after-sales-warehouse 2>/dev/null || true
    systemctl reload nginx 2>/dev/null || true
    echo "[错误] 发布失败，已恢复 current 指针：$PREVIOUS_RELEASE" >&2
  else
    echo "[错误] 候选版本构建失败，current 指针未改变。" >&2
  fi
  if [[ -d "$CANDIDATE_DIR" && "$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)" != "$CANDIDATE_DIR" ]]; then
    rm -rf "$CANDIDATE_DIR"
  fi
  exit "$status"
}
trap deployment_failed ERR

install -d -o "$APP_USER" -g "$APP_GROUP" -m 755 "$CANDIDATE_DIR/backend"
rsync -a --delete --exclude '.env' --exclude 'db.sqlite3' --exclude '__pycache__' --exclude '*.pyc' "$SOURCE_BACKEND/" "$CANDIDATE_DIR/backend/"

if [[ $OFFLINE_UPDATE -eq 0 ]]; then
  python3.12 -m venv "$CANDIDATE_DIR/venv"
  "$CANDIDATE_DIR/venv/bin/python" -m pip install --upgrade pip
  "$CANDIDATE_DIR/venv/bin/python" -m pip install -r "$CANDIDATE_DIR/backend/requirements-linux.txt"
else
  EXISTING_VENV="${PREVIOUS_RELEASE:+$PREVIOUS_RELEASE/venv}"
  if [[ -z "$EXISTING_VENV" || ! -x "$EXISTING_VENV/bin/python" ]]; then
    echo "Offline update requires an existing atomic release with a working virtual environment."
    exit 1
  fi
  # ── venv 复用前的兼容性闸门（2026-11-19 补）────────────────────────
  #
  # 历史坑：离线更新直接 `cp -a` 旧 venv，导致 venv 永远不会重建。
  # 2026-11-19 实测发现生产 venv 停留在 Python 3.10.12 + Django 5.2.17 +
  # Pillow 12.3.0，而锁文件要求 Python >= 3.11 的 Pillow 13，累积 65 条已知漏洞。
  # 根因就是这个分支从不校验 venv 是否仍满足当前锁文件。
  #
  # 现在复用前做两项检查，任一不满足就改为用 python3.12 重建：
  #   1) venv 的解释器版本 >= 3.11（Pillow 13 的硬性要求）
  #   2) 锁文件声明的 Python 相关包版本与 venv 实际一致
  REUSE_VENV=1
  VENV_PY_VERSION="$("$EXISTING_VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo '0.0')"
  if ! "$EXISTING_VENV/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "现有 venv 的 Python 为 ${VENV_PY_VERSION}，低于锁文件要求的 3.11；将用 python3.12 重建。"
    REUSE_VENV=0
  fi

  if [[ $REUSE_VENV -eq 1 ]]; then
    # 逐项比对锁文件与已装版本，任一处不一致就重建，避免依赖长期漂移。
    if ! "$EXISTING_VENV/bin/python" - "$CANDIDATE_DIR/backend/requirements-lock.txt" <<'PYCHECK'
import subprocess, sys

lock_path = sys.argv[1]
locked = {}
with open(lock_path, encoding='utf-8') as handle:
    for line in handle:
        line = line.strip()
        if '==' in line and not line.startswith('#'):
            name, version = line.split('==', 1)
            locked[name.strip().lower()] = version.strip()

out = subprocess.run(
    [sys.executable, '-m', 'pip', 'list', '--format=freeze'],
    capture_output=True, text=True,
).stdout
actual = {}
for line in out.splitlines():
    if '==' in line:
        name, version = line.split('==', 1)
        actual[name.strip().lower()] = version.strip()

mismatch = [f'{k}: 锁={v} 实={actual.get(k)}' for k, v in locked.items() if actual.get(k) != v]
if mismatch:
    print('  依赖与锁文件不一致: ' + '; '.join(mismatch[:6]))
    raise SystemExit(1)
print('  依赖与锁文件一致')
PYCHECK
    then
      echo "现有 venv 依赖与锁文件不一致；将用 python3.12 重建。"
      REUSE_VENV=0
    fi
  fi

  if [[ $REUSE_VENV -eq 1 ]]; then
    cp -a "$EXISTING_VENV" "$CANDIDATE_DIR/venv"
    "$CANDIDATE_DIR/venv/bin/python" -c "import corsheaders, django, dotenv, gunicorn, openpyxl, PIL, psycopg, pypinyin, qrcode, rest_framework; print('Copied Python dependencies are available.')"
    "$CANDIDATE_DIR/venv/bin/python" -m pip check
  else
    # 重建需要能访问 PyPI 或本地 wheel 缓存；离线环境若失败则明确报错，不静默降级。
    python3.12 -m venv "$CANDIDATE_DIR/venv"
    "$CANDIDATE_DIR/venv/bin/python" -m pip install --quiet --upgrade pip
    if ! "$CANDIDATE_DIR/venv/bin/python" -m pip install \
         -r "$CANDIDATE_DIR/backend/requirements-linux.txt"; then
      echo "用 python3.12 重建 venv 时安装依赖失败。" >&2
      echo "请确认该主机可访问 PyPI，或把 wheel 放入本地目录后用 --find-links 安装。" >&2
      exit 1
    fi
    "$CANDIDATE_DIR/venv/bin/python" -m pip check
  fi
fi

DB_PASSWORD="$(openssl rand -base64 36 | tr -d '\n')"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi

if [[ ! -f "$ENV_FILE" && -f "$APP_DIR/backend/.env" ]]; then
  install -o "$APP_USER" -g "$APP_GROUP" -m 640 "$APP_DIR/backend/.env" "$ENV_FILE"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  SECRET_KEY="$(openssl rand -hex 48)"
  cat > "$ENV_FILE" <<EOF
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
STATIC_ROOT=$CURRENT_LINK/static
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
  chmod 640 "$ENV_FILE"
fi

# .env 可能来自更早的部署，其中的白名单缺少后来新增的出口地址。
# 这里补齐而不是重建文件，以免丢失 SECRET_KEY、数据库口令等运行期数据。
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
ln -sfn "$ENV_FILE" "$CANDIDATE_DIR/backend/.env"

if [[ $IMPORT_SQLITE -eq 1 && -f "$SOURCE_BACKEND/db.sqlite3" && ! -f "$DATA_DIR/.sqlite-imported" ]]; then
  echo "Exporting existing SQLite data for one-time PostgreSQL import..."
  DB_ENGINE=django.db.backends.sqlite3 DB_NAME="$SOURCE_BACKEND/db.sqlite3" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.Permission --exclude sessions > /tmp/after-sales-warehouse-initial.json
fi

install -d -o "$APP_USER" -g "$APP_GROUP" -m 755 "$CANDIDATE_DIR/static"
chown -R "$APP_USER:$APP_GROUP" "$CANDIDATE_DIR" "$DATA_DIR"
sudo -u "$APP_USER" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" check
sudo -u "$APP_USER" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" migrate --plan
sudo -u "$APP_USER" env STATIC_ROOT="$CANDIDATE_DIR/static" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" collectstatic --noinput
sudo -u "$APP_USER" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" migrate --noinput
if [[ -f /tmp/after-sales-warehouse-initial.json && ! -f "$DATA_DIR/.sqlite-imported" ]]; then
  sudo -u "$APP_USER" "$CANDIDATE_DIR/venv/bin/python" "$CANDIDATE_DIR/backend/manage.py" loaddata /tmp/after-sales-warehouse-initial.json
  touch "$DATA_DIR/.sqlite-imported"
  rm -f /tmp/after-sales-warehouse-initial.json
fi

cat > "$CANDIDATE_DIR/release-info" <<EOF
deployed_at=$(date --iso-8601=seconds)
server_name=$SERVER_NAME
host=$(hostname)
release_version=$RELEASE_VERSION
source_revision=$SOURCE_REVISION
source_revision_short=$SOURCE_REVISION_SHORT
working_tree_dirty=$WORKING_TREE_DIRTY
source_content_sha256=$SOURCE_CONTENT_SHA256
previous_release=$PREVIOUS_RELEASE
database_snapshot=required_before_migrate
EOF
chown "$APP_USER:$APP_GROUP" "$CANDIDATE_DIR/release-info"

ln -sfn "$CANDIDATE_DIR" "$APP_DIR/current.next"
mv -Tf "$APP_DIR/current.next" "$CURRENT_LINK"
RELEASE_SWITCHED=1

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
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-dingtalk-outbox.service" /etc/systemd/system/after-sales-warehouse-dingtalk-outbox.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-dingtalk-outbox.timer" /etc/systemd/system/after-sales-warehouse-dingtalk-outbox.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-media-gc.service" /etc/systemd/system/after-sales-warehouse-media-gc.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-media-gc.timer" /etc/systemd/system/after-sales-warehouse-media-gc.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-offsite-backup.service" /etc/systemd/system/after-sales-warehouse-offsite-backup.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-offsite-backup.timer" /etc/systemd/system/after-sales-warehouse-offsite-backup.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-cloudflared-selfheal.service" /etc/systemd/system/after-sales-warehouse-cloudflared-selfheal.service
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-cloudflared-selfheal.timer" /etc/systemd/system/after-sales-warehouse-cloudflared-selfheal.timer
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-logrotate" /etc/logrotate.d/after-sales-warehouse
install -d -m 755 /etc/systemd/journald.conf.d
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-journald.conf" /etc/systemd/journald.conf.d/after-sales-warehouse.conf
install -m 644 "$SCRIPT_DIR/after-sales-warehouse-tmpfiles.conf" /etc/tmpfiles.d/after-sales-warehouse.conf
install -m 750 "$SCRIPT_DIR/backup.sh" /usr/local/sbin/after-sales-warehouse-backup
install -m 750 "$SCRIPT_DIR/media-gc.sh" /usr/local/sbin/after-sales-warehouse-media-gc
install -m 750 "$SCRIPT_DIR/offsite-backup.sh" /usr/local/sbin/after-sales-warehouse-offsite-backup
install -m 750 "$SCRIPT_DIR/cloudflared-selfheal.sh" /usr/local/sbin/after-sales-warehouse-cloudflared-selfheal
install -m 750 "$SCRIPT_DIR/runtime-status.sh" /usr/local/sbin/after-sales-warehouse-status
install -m 750 "$SCRIPT_DIR/health-monitor.sh" /usr/local/sbin/after-sales-warehouse-health
install -m 750 "$SCRIPT_DIR/restore-drill.sh" /usr/local/sbin/after-sales-warehouse-restore-drill
install -m 750 "$SCRIPT_DIR/rollback.sh" /usr/local/sbin/after-sales-warehouse-rollback
install -m 750 "$SCRIPT_DIR/security-check.sh" /usr/local/sbin/after-sales-warehouse-security-check
install -m 750 "$SCRIPT_DIR/run-concurrency-tests.sh" /usr/local/sbin/after-sales-warehouse-concurrency-test
install -d -m 755 /usr/local/libexec
install -m 750 "$SCRIPT_DIR/dingtalk-notify.py" /usr/local/libexec/after-sales-warehouse-dingtalk-notify
sed -e "s/__SERVER_NAME__/$RESOLVED_SERVER_NAME/g" \
    -e "s/__PUBLIC_DOMAIN__/$RESOLVED_PUBLIC_DOMAIN/g" \
    "$SCRIPT_DIR/nginx-after-sales-warehouse.conf" > /etc/nginx/sites-available/after-sales-warehouse
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
systemctl enable --now after-sales-warehouse-dingtalk-outbox.timer
systemctl enable --now after-sales-warehouse-media-gc.timer
systemctl enable --now after-sales-warehouse-offsite-backup.timer
# 隧道自愈依赖 cloudflared 已安装；未装时 enable 会失败，用 || true 跳过
systemctl enable --now after-sales-warehouse-cloudflared-selfheal.timer || true
systemctl enable nginx
# `enable --now` does not reload an already-running Nginx process.
systemctl reload nginx

rm -f /etc/cron.d/after-sales-warehouse-backup
systemd-tmpfiles --create /etc/tmpfiles.d/after-sales-warehouse.conf
systemctl restart systemd-journald
systemctl restart after-sales-warehouse-health.timer after-sales-warehouse-backup.timer

curl --silent --show-error --fail --max-time 15 http://127.0.0.1/health/ >/dev/null
trap - ERR

# 只保留最近 3 个原子 release（当前 + 2 个回滚点）。
# 每个 release 是完整代码副本 + venv，约 130 MB；留 5 个会稳定占用 650 MB 以上。
# 2 个回滚点足以覆盖「新版本有问题 → 退回上一版 → 再退回上上版」的常见场景。
# 旧式 tar 快照不匹配此规则，不会被误删。
mapfile -t old_releases < <(find "$RELEASE_ROOT" -mindepth 2 -maxdepth 2 -type f -name release-info -printf '%T@ %h\n' | sort -rn | awk 'NR > 3 {print $2}')
if (( ${#old_releases[@]} > 0 )); then
  for old_release in "${old_releases[@]}"; do
    [[ "$old_release" == "$(readlink -f "$CURRENT_LINK")" ]] || rm -rf "$old_release"
  done
fi

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
echo "登记域名或其它 IP：sudo bash deploy/ubuntu22/register-host.sh <IP或域名>"
echo "Verify with: systemctl status after-sales-warehouse nginx"
echo "Security check: sudo /usr/local/sbin/after-sales-warehouse-security-check"
echo "Concurrency test: sudo /usr/local/sbin/after-sales-warehouse-concurrency-test"
echo "Rollback list: sudo /usr/local/sbin/after-sales-warehouse-rollback --list"
