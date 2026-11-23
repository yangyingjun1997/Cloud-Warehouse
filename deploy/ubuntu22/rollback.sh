#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_GROUP=warehouse
APP_DIR=/opt/after-sales-warehouse
DATA_DIR=/srv/after-sales-warehouse
RELEASE_ROOT="$APP_DIR/releases"
CURRENT_LINK="$APP_DIR/current"
BACKUP_COMMAND=/usr/local/sbin/after-sales-warehouse-backup

usage() {
  cat <<'EOF'
Usage:
  sudo after-sales-warehouse-rollback --list
  sudo after-sales-warehouse-rollback previous
  sudo after-sales-warehouse-rollback <release-directory-name>
  sudo after-sales-warehouse-rollback <release-directory-name> --allow-unknown-schema

说明：
  该命令只原子切换已构建完成的程序 release，不回滚 PostgreSQL 数据或 schema。
  目标 release 必须与当前数据库结构兼容；历史 release 缺少迁移元数据时默认拒绝，
  只有完成人工兼容确认后才允许使用 --allow-unknown-schema。
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

ALLOW_UNKNOWN_SCHEMA=0
if [[ "${2:-}" == "--allow-unknown-schema" ]]; then
  ALLOW_UNKNOWN_SCHEMA=1
  set -- "$1"
elif [[ "${1:-}" == "--allow-unknown-schema" ]]; then
  echo "--allow-unknown-schema 必须跟在目标 release 后。" >&2
  exit 2
fi

release_is_complete() {
  local release_dir="$1"
  [[ -f "$release_dir/backend/manage.py" \
    && -x "$release_dir/venv/bin/python" \
    && -d "$release_dir/static" \
    && -f "$release_dir/release-info" ]]
}

list_releases() {
  local release_dir
  while IFS= read -r release_dir; do
    release_is_complete "$release_dir" && basename "$release_dir"
  done < <(find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -print | sort -r)
}

if [[ ! -d "$RELEASE_ROOT" ]]; then
  echo "尚未发现 release 目录：$RELEASE_ROOT"
  exit 1
fi

if [[ "${1:-}" == "--list" || $# -eq 0 ]]; then
  echo "可切换程序 release："
  list_releases || true
  echo
  echo "当前指向：$(readlink -f "$CURRENT_LINK" 2>/dev/null || echo '未设置')"
  exit 0
fi

CURRENT_RELEASE="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
target="${1:-previous}"
if [[ "$target" == "previous" ]]; then
  target="$(list_releases | while IFS= read -r name; do
    candidate="$RELEASE_ROOT/$name"
    [[ "$(readlink -f "$candidate")" == "$CURRENT_RELEASE" ]] || { echo "$name"; break; }
  done)"
fi

TARGET_DIR="$RELEASE_ROOT/$target"
if [[ -z "$target" || ! -d "$TARGET_DIR" ]] || ! release_is_complete "$TARGET_DIR"; then
  echo "未找到完整 release：${target:-空}"
  exit 1
fi
TARGET_DIR="$(readlink -f "$TARGET_DIR")"
if [[ "$TARGET_DIR" == "$CURRENT_RELEASE" ]]; then
  echo "目标已是当前 release，无需切换。"
  exit 0
fi

check_release_schema_compatibility() {
  local release_dir="$1" migration_check migration_state_sha256 plan_file
  migration_check="$(awk -F= '$1 == "database_migration_check" { print $2; exit }' "$release_dir/release-info")"
  migration_state_sha256="$(awk -F= '$1 == "database_migration_state_sha256" { print $2; exit }' "$release_dir/release-info")"
  if [[ "$migration_check" != "passed" || -z "$migration_state_sha256" ]]; then
    if [[ $ALLOW_UNKNOWN_SCHEMA -ne 1 ]]; then
      echo "目标 release 缺少已验证的迁移元数据，已拒绝回滚。" >&2
      echo "完成人工兼容确认后，可使用 --allow-unknown-schema 重试。" >&2
      return 1
    fi
    echo "警告：目标 release 的迁移元数据未知，继续前请确认 schema 兼容性。" >&2
  fi

  plan_file="$(mktemp "$DATA_DIR/rollback-migration-plan.XXXXXX")"
  if ! sudo -u "$APP_USER" "$release_dir/venv/bin/python" "$release_dir/backend/manage.py" showmigrations --plan > "$plan_file"; then
    rm -f "$plan_file"
    echo "目标 release 无法读取当前数据库迁移状态，已拒绝回滚。" >&2
    return 1
  fi
  if grep -q '^\[ \]' "$plan_file"; then
    rm -f "$plan_file"
    echo "目标 release 需要当前数据库未应用的迁移，已拒绝回滚。" >&2
    return 1
  fi
  rm -f "$plan_file"
}

check_release_schema_compatibility "$TARGET_DIR"

if [[ -x "$BACKUP_COMMAND" ]]; then
  echo "回滚前执行数据库和媒体备份..."
  "$BACKUP_COMMAND"
else
  echo "未找到必需的备份命令：$BACKUP_COMMAND" >&2
  exit 1
fi

sudo -u "$APP_USER" "$TARGET_DIR/venv/bin/python" "$TARGET_DIR/backend/manage.py" check

restore_current() {
  local status="$?"
  trap - ERR
  if [[ -n "$CURRENT_RELEASE" && -d "$CURRENT_RELEASE" ]]; then
    ln -sfn "$CURRENT_RELEASE" "$APP_DIR/current.rollback"
    mv -Tf "$APP_DIR/current.rollback" "$CURRENT_LINK"
    systemctl restart after-sales-warehouse 2>/dev/null || true
    systemctl reload nginx 2>/dev/null || true
  fi
  echo "回滚验证失败，已恢复原 current 指针。" >&2
  exit "$status"
}
trap restore_current ERR

ln -sfn "$TARGET_DIR" "$APP_DIR/current.next"
mv -Tf "$APP_DIR/current.next" "$CURRENT_LINK"
systemctl restart after-sales-warehouse
systemctl reload nginx
curl --silent --show-error --fail --max-time 15 http://127.0.0.1/health/ >/dev/null
trap - ERR

cat >> "$TARGET_DIR/release-info" <<EOF
last_activated_at=$(date --iso-8601=seconds)
activated_by=rollback
previous_release=$CURRENT_RELEASE
database_rollback=not_performed
EOF
chown "$APP_USER:$APP_GROUP" "$TARGET_DIR/release-info"

echo "程序 release 切换完成：$target"
echo "数据库未回滚；已验证 Django check 与 /health/。"
