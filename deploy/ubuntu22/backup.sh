#!/usr/bin/env bash
set -Eeuo pipefail

APP_DATA_DIR=/srv/after-sales-warehouse
BACKUP_ROOT="$APP_DATA_DIR/backups"
DATABASE_NAME=warehouse
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date +%F_%H%M%S)"
TARGET="$BACKUP_ROOT/$STAMP"
NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify

notify_failure() {
  local status="$?" line="${BASH_LINENO[0]:-未知}"
  trap - ERR
  if [[ -x "$NOTIFIER" ]]; then
    "$NOTIFIER" '售后仓库备份失败' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n失败行：$line  \n退出码：$status\n\n请执行：\`sudo journalctl -u after-sales-warehouse-backup.service -n 100\`" || true
  fi
  exit "$status"
}
trap notify_failure ERR

mkdir -p "$TARGET"
# The timer runs as root. Redirect pg_dump's stdout from the root shell so the
# postgres process does not need write permission on the timestamp directory.
cd /
sudo -u postgres pg_dump --format=custom "$DATABASE_NAME" > "$TARGET/database.dump"
tar -C "$APP_DATA_DIR" -czf "$TARGET/media.tar.gz" media
sha256sum "$TARGET/database.dump" "$TARGET/media.tar.gz" > "$TARGET/SHA256SUMS"
sudo -u postgres pg_restore --list "$TARGET/database.dump" >/dev/null
tar -tzf "$TARGET/media.tar.gz" >/dev/null
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$RETENTION_DAYS" -exec rm -rf {} +
echo "备份完成并通过完整性检查：$TARGET"

# 保留策略执行完后再统计，反映清理后的真实占用
KEPT_COUNT="$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' | wc -l)"
DUMP_SIZE="$(du -h "$TARGET/database.dump" | cut -f1)"
MEDIA_SIZE="$(du -h "$TARGET/media.tar.gz" | cut -f1)"

# 成功通知：只在每周一播报，避免每天一条噪音，同时留下「备份还在跑」的证据。
# 失败仍然每次都报（上面的 trap）。
if [[ "$(date +%u)" == "1" ]] && [[ -x "$NOTIFIER" ]]; then
  "$NOTIFIER" '售后仓库备份周报' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n本次备份：$TARGET  \n数据库：$DUMP_SIZE  \n媒体文件：$MEDIA_SIZE  \n当前保留：$KEPT_COUNT 份（保留 ${RETENTION_DAYS} 天）" || true
fi

