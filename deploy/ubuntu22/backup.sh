#!/usr/bin/env bash
# 售后仓库备份：数据库全量 + media 硬链接增量。
#
# 设计要点（2026-11-18 按用户决策 A/B/C 改造）：
#   A 增量  —— media 用 rsync --link-dest 硬链接去重，30 份备份只占约 1.5 倍空间，
#              而不是 30 倍。旧的 `tar -czf media.tar.gz` 全量打包在 media 涨到几十 GB
#              后会直接爆盘，是必须修的隐患。
#   B 解耦  —— 数据库 pg_dump 与 media 同步是两个独立步骤，各自校验，
#              一个失败不会让另一个的产物被当成可用备份。
#   C 降频  —— media 每日同步但只在内容有变化时写入新快照；无变化直接跳过，
#              避免堆积一堆完全相同的目录。
set -Eeuo pipefail

APP_DATA_DIR=/srv/after-sales-warehouse
BACKUP_ROOT="$APP_DATA_DIR/backups"
DATABASE_NAME=warehouse
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date +%F_%H%M%S)"
TARGET="$BACKUP_ROOT/$STAMP"
NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify

# media 增量快照单独放一个根，便于按 --link-dest 找上一份
MEDIA_SNAPSHOT_ROOT="$BACKUP_ROOT/media-snapshots"
LATEST_MEDIA_LINK="$BACKUP_ROOT/media-latest"

notify_failure() {
  local status="$?" line="${BASH_LINENO[0]:-未知}"
  trap - ERR
  if [[ -x "$NOTIFIER" ]]; then
    "$NOTIFIER" '售后仓库备份失败' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n失败行：$line  \n退出码：$status\n\n请执行：\`sudo journalctl -u after-sales-warehouse-backup.service -n 100\`" || true
  fi
  exit "$status"
}
trap notify_failure ERR

mkdir -p "$TARGET" "$MEDIA_SNAPSHOT_ROOT"

# ── 步骤 1：数据库全量（体积小，保持每日全量最稳） ────────────────────────
# 定时器以 root 运行；从 root shell 重定向 stdout，让 postgres 用户不必对时间戳目录有写权限。
cd /
sudo -u postgres pg_dump --format=custom "$DATABASE_NAME" > "$TARGET/database.dump"
sudo -u postgres pg_restore --list "$TARGET/database.dump" >/dev/null
echo "数据库备份完成并通过完整性检查：$TARGET/database.dump"

# ── 步骤 2：media 硬链接增量（A + C） ─────────────────────────────────
MEDIA_DIR="$APP_DATA_DIR/media"
MEDIA_SNAPSHOT="$MEDIA_SNAPSHOT_ROOT/$STAMP"
MEDIA_CHANGED=0

if [[ -d "$MEDIA_DIR" ]]; then
  LINK_DEST_ARGS=()
  if [[ -L "$LATEST_MEDIA_LINK" || -d "$LATEST_MEDIA_LINK" ]]; then
    PREVIOUS="$(readlink -f "$LATEST_MEDIA_LINK" 2>/dev/null || true)"
    if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
      # 硬链接去重：未变化的文件直接指向上一次快照的 inode，不占新空间
      LINK_DEST_ARGS=(--link-dest="$PREVIOUS")
    fi
  fi

  # --dry-run 先判断有没有**文件内容**变化，避免每天产生大量完全相同的快照（C）。
  # 注意不能直接 grep '^[<>ch]'：目录的 mtime 会随文件增删变化，输出 `.d..t` 之类的
  # 记录，导致「只是目录时间戳变了」也被当成有变化，每天都建一份空快照。
  # 只认以 `>f` / `cf` 开头的行（真正的文件内容变更或新建文件）。
  if rsync -a --dry-run --itemize-changes "${LINK_DEST_ARGS[@]}" \
       "$MEDIA_DIR/" "$MEDIA_SNAPSHOT/" 2>/dev/null | grep -qE '^([<>]f|cf|hf)'; then
    MEDIA_CHANGED=1
    rsync -a --delete "${LINK_DEST_ARGS[@]}" "$MEDIA_DIR/" "$MEDIA_SNAPSHOT/"
    # 原子切换 latest 指针，下一次备份以它为 --link-dest
    ln -sfn "$MEDIA_SNAPSHOT" "$LATEST_MEDIA_LINK.tmp"
    mv -Tf "$LATEST_MEDIA_LINK.tmp" "$LATEST_MEDIA_LINK"
    echo "media 增量快照完成：$MEDIA_SNAPSHOT"
  else
    rmdir "$MEDIA_SNAPSHOT" 2>/dev/null || true
    echo "media 自上次备份以来无变化，跳过快照。"
  fi
else
  echo "media 目录不存在，跳过 media 备份。"
fi

# ── 步骤 3：清单与校验 ────────────────────────────────────────────────
{
  echo "# 数据库"
  sha256sum "$TARGET/database.dump"
  if [[ "$MEDIA_CHANGED" == "1" ]]; then
    echo "# media 快照（硬链接增量，未变化文件指向历史快照的 inode）"
    echo "$MEDIA_SNAPSHOT"
    find "$MEDIA_SNAPSHOT" -type f -print0 | sort -z | xargs -0 -r sha256sum | \
      sed "s#$MEDIA_SNAPSHOT#$STAMP/media#"
  fi
} > "$TARGET/SHA256SUMS"

# media 恢复校验：抽样确认快照可读，不做全量重算（大目录会很慢）
if [[ "$MEDIA_CHANGED" == "1" ]]; then
  find "$MEDIA_SNAPSHOT" -type f | head -1 | while read -r sample; do
    [[ -n "$sample" ]] && cmp -s "$sample" "$MEDIA_DIR/${sample#$MEDIA_SNAPSHOT/}" \
      || echo "警告：media 抽样校验不一致 $sample"
  done
fi

# ── 步骤 4：保留策略（A：媒体快照与时间戳备份各自按天清理） ──────────────
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$RETENTION_DAYS" -exec rm -rf {} +
find "$MEDIA_SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -exec rm -rf {} +

KEPT_COUNT="$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' | wc -l)"
MEDIA_KEPT="$(find "$MEDIA_SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d | wc -l)"
DUMP_SIZE="$(du -h "$TARGET/database.dump" | cut -f1)"
# 硬链接下 du 统计的是真实占用；--apparent-size 会重复计算共享 inode
MEDIA_SIZE="$(du -sh "$MEDIA_SNAPSHOT_ROOT" 2>/dev/null | cut -f1 || echo '0')"
DISK_FREE="$(df -h "$BACKUP_ROOT" | awk 'NR==2{print $4}')"

echo "备份完成：$TARGET"
echo "保留：时间戳备份 ${KEPT_COUNT} 份、media 快照 ${MEDIA_KEPT} 份（各保留 ${RETENTION_DAYS} 天）"
echo "占用：数据库 ${DUMP_SIZE}、media 快照合计 ${MEDIA_SIZE}、磁盘可用 ${DISK_FREE}"

# 成功通知：每周一播报，避免每天一条噪音；失败仍然每次都报（上面的 trap）
if [[ "$(date +%u)" == "1" ]] && [[ -x "$NOTIFIER" ]]; then
  "$NOTIFIER" '售后仓库备份周报' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n数据库：$DUMP_SIZE  \nmedia 快照合计：$MEDIA_SIZE  \n保留：${KEPT_COUNT} 份数据库备份 / ${MEDIA_KEPT} 份 media 快照（各 ${RETENTION_DAYS} 天）  \n磁盘可用：$DISK_FREE" || true
fi
