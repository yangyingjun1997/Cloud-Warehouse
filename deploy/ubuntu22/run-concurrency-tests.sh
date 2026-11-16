#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER=warehouse
APP_DIR=/opt/after-sales-warehouse
BACKEND_DIR="$APP_DIR/current/backend"
ENV_FILE="$BACKEND_DIR/.env"
PYTHON="$APP_DIR/current/venv/bin/python"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo '请使用 sudo 执行 PostgreSQL 并发专项测试。' >&2
  exit 1
fi
if [[ ! -r "$ENV_FILE" || ! -x "$PYTHON" ]]; then
  echo '未找到服务器环境配置或 Python 虚拟环境。' >&2
  exit 1
fi

db_engine="$(awk -F= '$1 == "DB_ENGINE" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
db_name="$(awk -F= '$1 == "DB_NAME" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
db_user="$(awk -F= '$1 == "DB_USER" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
if [[ "$db_engine" != 'django.db.backends.postgresql' ]]; then
  echo '并发专项只允许在 PostgreSQL 环境运行。' >&2
  exit 1
fi
if [[ ! "$db_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || ! "$db_user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo '数据库名或账号格式不符合安全执行要求。' >&2
  exit 1
fi

test_db="test_${db_name}"
original_createdb="$(runuser -u postgres -- psql -Atqc \
  "SELECT rolcreatedb FROM pg_roles WHERE rolname = '$db_user'")"
if [[ "$original_createdb" != 't' && "$original_createdb" != 'f' ]]; then
  echo '配置中的数据库账号不存在。' >&2
  exit 1
fi

cleanup() {
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$test_db' AND pid <> pg_backend_pid();" \
    >/dev/null
  runuser -u postgres -- dropdb --if-exists "$test_db"
  if [[ "$original_createdb" == 'f' ]]; then
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "ALTER ROLE \"$db_user\" NOCREATEDB;" >/dev/null
  fi
}
trap cleanup EXIT

if [[ "$original_createdb" == 'f' ]]; then
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "ALTER ROLE \"$db_user\" CREATEDB;" >/dev/null
fi

echo "将在隔离测试库 $test_db 执行竞态测试；不会连接业务库写入测试数据。"
cd "$BACKEND_DIR"
runuser -u "$APP_USER" -- "$PYTHON" manage.py test \
  apps.accounts.test_postgresql_concurrency \
  apps.workflow.test_postgresql_concurrency \
  apps.common.test_postgresql_concurrency \
  apps.common.test_postgresql_audit_immutability \
  --noinput -v 2
