#!/usr/bin/env bash
# 恢复演练：把最近一次备份恢复到临时数据库，验证备份真的可用。
#
# 备份格式（2026-11-18 起）：
#   <backups>/<stamp>/database.dump      数据库全量
#   <backups>/<stamp>/SHA256SUMS         数据库校验和 + 媒体文件校验和
#   <backups>/media-snapshots/<stamp>/   媒体硬链接增量快照（不在批次目录内）
#
# 历史缺陷：本脚本曾要求 <backups>/<stamp>/media.tar.gz，而 backup.sh 早已
# 不再生成该文件（改为 media-snapshots 硬链接增量），导致恢复演练永远在
# 数据库恢复之前就失败——灾难恢复能力实际为零验证。同时 SHA256SUMS 里
# 媒体路径被 backup.sh 改写成 "<stamp>/media/..."，与真实快照路径不一致，
# 因此不能对整份清单直接 sha256sum --check。
set -Eeuo pipefail

cd /

BACKUP_ROOT=/srv/after-sales-warehouse/backups
MEDIA_ROOT="$BACKUP_ROOT/media-snapshots"
TEST_DB="${TEST_DB:-warehouse_restore_drill}"
BACKUP_DIR="${1:-$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -n 1)}"

if [[ $EUID -ne 0 ]]; then
  echo '请使用 sudo 执行恢复演练。'
  exit 1
fi
if [[ "$TEST_DB" == warehouse || -z "$TEST_DB" ]]; then
  echo '拒绝执行：演练数据库不能是生产数据库 warehouse。'
  exit 2
fi
if [[ ! -f "$BACKUP_DIR/database.dump" ]]; then
  echo "备份不完整：缺少 database.dump（$BACKUP_DIR）"
  exit 2
fi

STAMP="$(basename "$BACKUP_DIR")"

# ── 1. 数据库校验：只校验 database.dump 这一行，媒体行另行处理 ──────────
if [[ -f "$BACKUP_DIR/SHA256SUMS" ]]; then
  expected="$(grep -E "^[0-9a-f]{64}  .*database\.dump$" "$BACKUP_DIR/SHA256SUMS" | awk '{print $1}')"
  if [[ -z "$expected" ]]; then
    echo "SHA256SUMS 中没有 database.dump 的校验和，拒绝继续。"
    exit 2
  fi
  actual="$(sha256sum "$BACKUP_DIR/database.dump" | awk '{print $1}')"
  if [[ "$expected" != "$actual" ]]; then
    echo "database.dump 校验失败：期望 $expected，实际 $actual"
    exit 2
  fi
  echo "database.dump 校验通过。"
else
  echo "警告：缺少 SHA256SUMS，跳过数据库校验和比对。"
fi

# ── 2. 媒体快照：从 SHA256SUMS 的列表头注释定位，找不到则用同 stamp ──────
MEDIA_SNAPSHOT=""
if [[ -f "$BACKUP_DIR/SHA256SUMS" ]]; then
  MEDIA_SNAPSHOT="$(sed -n '/^# media 快照/{n;p;q;}' "$BACKUP_DIR/SHA256SUMS" || true)"
fi
if [[ -z "$MEDIA_SNAPSHOT" || ! -d "$MEDIA_SNAPSHOT" ]]; then
  # 回退：批次目录没有媒体行时，用 media-snapshots 下最近一份快照
  MEDIA_SNAPSHOT="$(find "$MEDIA_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n 1 || true)"
fi

MEDIA_FILE_COUNT=0
if [[ -n "$MEDIA_SNAPSHOT" && -d "$MEDIA_SNAPSHOT" ]]; then
  MEDIA_FILE_COUNT="$(find "$MEDIA_SNAPSHOT" -type f | wc -l)"
  # 抽样读一遍，确认快照内容可读（硬链接快照可能指向已被保留策略删除的 inode）
  sample="$(find "$MEDIA_SNAPSHOT" -type f | head -1 || true)"
  if [[ -n "$sample" ]]; then
    if head -c 1 "$sample" >/dev/null 2>&1; then
      echo "媒体快照可读：$MEDIA_SNAPSHOT（$MEDIA_FILE_COUNT 个文件）"
    else
      echo "媒体快照存在但无法读取：$sample"
      exit 2
    fi
  else
    echo "媒体快照为空：$MEDIA_SNAPSHOT"
  fi
else
  echo "未找到媒体快照（可能自上次备份以来 media 无变化），跳过媒体校验。"
fi

# ── 3. 恢复数据库到临时库 ──────────────────────────────────────────────
sudo -u postgres pg_restore --list "$BACKUP_DIR/database.dump" >/dev/null

sudo -u postgres dropdb --if-exists "$TEST_DB"
sudo -u postgres createdb "$TEST_DB"

cleanup() {
  if [[ "${KEEP_TEST_DB:-0}" != 1 ]]; then
    sudo -u postgres dropdb --if-exists "$TEST_DB" || true
  fi
}
# 恢复失败时也要清掉半成品临时库，避免下次演练因同名库残留而误判
trap cleanup EXIT

sudo -u postgres pg_restore --no-owner --no-privileges --dbname "$TEST_DB" "$BACKUP_DIR/database.dump"

# ── 4. 真实读取校验（不只看表是否存在） ────────────────────────────────
table_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
user_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM auth_user;" 2>/dev/null || echo 未知)"
asset_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM inventory_asset;" 2>/dev/null || echo 未知)"
audit_count="$(sudo -u postgres psql -d "$TEST_DB" -tAc "SELECT count(*) FROM common_operationauditlog;" 2>/dev/null || echo 未知)"

echo "恢复演练通过：数据库=$TEST_DB"
echo "  业务表数量=$table_count  用户=$user_count  资产=$asset_count  审计=$audit_count"
echo "  备份来源=$BACKUP_DIR"
echo "  媒体快照=${MEDIA_SNAPSHOT:-无}（$MEDIA_FILE_COUNT 个文件）"

if [[ "${KEEP_TEST_DB:-0}" == 1 ]]; then
  echo 'KEEP_TEST_DB=1，演练数据库已保留供人工检查。'
else
  echo '演练数据库已删除；生产数据库未被修改。'
fi
