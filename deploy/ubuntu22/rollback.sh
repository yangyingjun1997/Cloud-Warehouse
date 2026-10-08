#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_GROUP=warehouse
APP_DIR=/opt/after-sales-warehouse
DATA_DIR=/srv/after-sales-warehouse
BACKEND_DIR="$APP_DIR/backend"
RELEASE_ROOT="$APP_DIR/releases"
BACKUP_COMMAND=/usr/local/sbin/after-sales-warehouse-backup

usage() {
  cat <<'EOF'
Usage:
  sudo after-sales-warehouse-rollback --list
  sudo after-sales-warehouse-rollback latest
  sudo after-sales-warehouse-rollback 20260920_153000

说明：
  该命令只回滚程序代码和静态文件，不回滚 PostgreSQL 数据。
  如果数据库结构也需要回退，请先用备份目录执行正式数据恢复流程。
EOF
}

if [[ $EUID -ne 0 ]]; then
  echo "Run this script with sudo."
  exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -d "$RELEASE_ROOT" ]]; then
  echo "尚未发现代码快照目录：$RELEASE_ROOT"
  exit 1
fi

list_snapshots() {
  find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r
}

if [[ "${1:-}" == "--list" || $# -eq 0 ]]; then
  echo "可回滚代码快照："
  list_snapshots || true
  echo
  echo "执行示例：sudo after-sales-warehouse-rollback latest"
  exit 0
fi

target="${1:-latest}"
if [[ "$target" == "latest" ]]; then
  target="$(list_snapshots | head -n 1)"
fi

if [[ -z "$target" || ! -f "$RELEASE_ROOT/$target/backend.tar.gz" ]]; then
  echo "未找到快照：$target"
  exit 1
fi

SNAPSHOT_DIR="$RELEASE_ROOT/$target"
STAMP="$(date +%Y%m%d_%H%M%S)"
PRE_ROLLBACK_DIR="$RELEASE_ROOT/pre_rollback_$STAMP"
ENV_BACKUP="$(mktemp)"
HAD_ENV=0

cleanup() {
  rm -f "$ENV_BACKUP"
}
trap cleanup EXIT

echo "准备回滚到代码快照：$target"

if [[ -x "$BACKUP_COMMAND" ]]; then
  echo "回滚前先执行一次数据库和媒体备份..."
  "$BACKUP_COMMAND"
else
  echo "提醒：未找到 $BACKUP_COMMAND，跳过自动数据备份。"
fi

if [[ -f "$SNAPSHOT_DIR/SHA256SUMS" ]]; then
  (cd "$SNAPSHOT_DIR" && sha256sum -c SHA256SUMS)
fi

if [[ -d "$BACKEND_DIR" && -f "$BACKEND_DIR/manage.py" ]]; then
  install -d -m 755 "$PRE_ROLLBACK_DIR"
  tar -C "$APP_DIR" \
    --exclude='backend/.env' \
    --exclude='backend/db.sqlite3' \
    --exclude='backend/media' \
    --exclude='backend/staticfiles' \
    --exclude='*/__pycache__' \
    --exclude='*.pyc' \
    -czf "$PRE_ROLLBACK_DIR/backend.tar.gz" backend
  sha256sum "$PRE_ROLLBACK_DIR/backend.tar.gz" > "$PRE_ROLLBACK_DIR/SHA256SUMS"
  echo "已保存回滚前代码快照：$PRE_ROLLBACK_DIR"
fi

if [[ -f "$BACKEND_DIR/.env" ]]; then
  cp "$BACKEND_DIR/.env" "$ENV_BACKUP"
  HAD_ENV=1
fi

systemctl stop after-sales-warehouse || true
rm -rf "$BACKEND_DIR"
tar -C "$APP_DIR" -xzf "$SNAPSHOT_DIR/backend.tar.gz"

if [[ $HAD_ENV -eq 1 ]]; then
  install -o "$APP_USER" -g "$APP_GROUP" -m 640 "$ENV_BACKUP" "$BACKEND_DIR/.env"
fi

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR" "$DATA_DIR"

if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
  "$APP_DIR/venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt"
  "$APP_DIR/venv/bin/pip" check
fi

sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" "$BACKEND_DIR/manage.py" check
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" "$BACKEND_DIR/manage.py" collectstatic --noinput

cat > "$APP_DIR/current-release" <<EOF
rolled_back_at=$(date --iso-8601=seconds)
rollback_snapshot=$target
pre_rollback_snapshot=$(basename "$PRE_ROLLBACK_DIR")
database_rollback=not_performed
EOF
chown "$APP_USER:$APP_GROUP" "$APP_DIR/current-release"

systemctl restart after-sales-warehouse
systemctl reload nginx || true
curl --silent --show-error --fail --max-time 10 http://127.0.0.1/health/ >/dev/null

echo "代码回滚完成。当前快照：$target"
echo "注意：数据库未回滚；若此次问题涉及迁移或数据，请按备份恢复手册处理。"
