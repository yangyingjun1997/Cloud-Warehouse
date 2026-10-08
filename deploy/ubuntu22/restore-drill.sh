#!/usr/bin/env bash
set -Eeuo pipefail

cd /

BACKUP_ROOT=/srv/after-sales-warehouse/backups
TEST_DB="${TEST_DB:-warehouse_restore_drill}"
BACKUP_DIR="${1:-$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)}"

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行恢复演练。'
  exit 1
fi
if [[ "$TEST_DB" == warehouse || -z "$TEST_DB" ]]; then
  echo '拒绝执行：演练数据库不能是生产数据库 warehouse。'
  exit 2
fi
if [[ ! -f "$BACKUP_DIR/database.dump" || ! -f "$BACKUP_DIR/media.tar.gz" ]]; then
  echo "备份不完整：$BACKUP_DIR"
  exit 2
fi

(cd "$BACKUP_DIR" && sha256sum --check SHA256SUMS)
sudo -u postgres pg_restore --list "$BACKUP_DIR/database.dump" >/dev/null
tar -tzf "$BACKUP_DIR/media.tar.gz" >/dev/null

sudo -u postgres dropdb --if-exists "$TEST_DB"
sudo -u postgres createdb "$TEST_DB"
sudo -u postgres pg_restore --no-owner --no-privileges --dbname "$TEST_DB" "$BACKUP_DIR/database.dump"

table_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
user_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM auth_user;" 2>/dev/null || echo 未知)"
echo "恢复演练通过：数据库=$TEST_DB，业务表数量=$table_count，用户数量=$user_count"
echo "备份来源：$BACKUP_DIR"
if [[ "${KEEP_TEST_DB:-0}" != 1 ]]; then
  sudo -u postgres dropdb "$TEST_DB"
  echo '演练数据库已删除；生产数据库未被修改。'
else
  echo 'KEEP_TEST_DB=1，演练数据库已保留供人工检查。'
fi
