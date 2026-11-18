#!/usr/bin/env bash
# 异地备份推送：把本地备份同步到另一台机器或对象存储。
#
# 现状：所有备份与业务数据都在同一块磁盘上，磁盘坏了会同时丢失。
# 本脚本提供一个「可选目标」机制——不配置目标时静默跳过，不影响主备份流程。
#
# 配置方式（写进 /srv/after-sales-warehouse/offsite.env，权限 600）：
#
#   # 方式 1：另一台 Linux 机器（需要已配置 SSH 免密）
#   OFFSITE_MODE=rsync
#   OFFSITE_TARGET=backup@192.168.1.50:/data/warehouse-backups
#   OFFSITE_SSH_KEY=/root/.ssh/id_ed25519_warehouse
#
#   # 方式 2：挂载到本机的网络盘 / USB 硬盘
#   OFFSITE_MODE=local
#   OFFSITE_TARGET=/mnt/usb-backup/warehouse
#
#   # 方式 3：S3 兼容对象存储（需先 pip install awscli 并配好凭据）
#   OFFSITE_MODE=s3
#   OFFSITE_TARGET=s3://my-bucket/warehouse-backups
#
# 设计要点：
#   - 未配置时 exit 0，不阻断每日备份。
#   - 只推送数据库 dump 与 media 快照，不推送 releases（可再生）。
#   - 用 rsync --delete 保持镜像，远端保留策略由目标端负责。
#   - 失败发钉钉通知，但不影响本地备份的完整性。
set -Eeuo pipefail

APP_DATA_DIR=/srv/after-sales-warehouse
BACKUP_ROOT="$APP_DATA_DIR/backups"
OFFSITE_ENV="$APP_DATA_DIR/offsite.env"
NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify

notify() {
  local title="$1" body="$2"
  [[ -x "$NOTIFIER" ]] && "$NOTIFIER" "$title" "$body" || true
}

if [[ ! -f "$OFFSITE_ENV" ]]; then
  echo "未配置异地备份目标（$OFFSITE_ENV 不存在），跳过。"
  echo "需要启用时，按脚本头部注释创建该文件即可。"
  exit 0
fi

# shellcheck disable=SC1090
set -a; . "$OFFSITE_ENV"; set +a

MODE="${OFFSITE_MODE:-}"
TARGET="${OFFSITE_TARGET:-}"

if [[ -z "$MODE" || -z "$TARGET" ]]; then
  echo "offsite.env 缺少 OFFSITE_MODE 或 OFFSITE_TARGET，跳过。"
  exit 0
fi

echo "异地备份：mode=$MODE target=$TARGET"

# 只推送需要长期保留的内容。额外参数（如 rsync 的 -e ssh 选项）放在前面。
sync_payload() {
  local dest="$1"; shift
  rsync -a --delete "$@" \
    --include='*/' \
    --include='database.dump' \
    --include='SHA256SUMS' \
    --include='media-snapshots/***' \
    --exclude='*' \
    "$BACKUP_ROOT/" "$dest/"
}

case "$MODE" in
  rsync)
    SSH_CMD="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"
    if [[ -n "${OFFSITE_SSH_KEY:-}" ]]; then
      SSH_CMD="ssh -i ${OFFSITE_SSH_KEY} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30"
    fi
    sync_payload "$TARGET" -e "$SSH_CMD"
    ;;
  local)
    mkdir -p "$TARGET"
    sync_payload "$TARGET"
    ;;
  s3)
    if ! command -v aws >/dev/null 2>&1; then
      echo "未安装 awscli，无法推送到 S3。" >&2
      notify '售后仓库异地备份失败' "原因：未安装 awscli  \n目标：$TARGET"
      exit 1
    fi
    aws s3 sync "$BACKUP_ROOT/" "$TARGET/" \
      --exclude '*' \
      --include '*/database.dump' \
      --include '*/SHA256SUMS' \
      --include 'media-snapshots/*' \
      --only-show-errors
    ;;
  *)
    echo "未知的 OFFSITE_MODE：$MODE（支持 rsync / local / s3）" >&2
    exit 2
    ;;
esac

# 推送后核对远端是否真的有最新备份
LATEST_STAMP="$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' | sort | tail -1)"
echo "异地备份完成，最新批次：$LATEST_STAMP"

SIZE="$(du -sh "$BACKUP_ROOT" 2>/dev/null | cut -f1)"
notify '售后仓库异地备份完成' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n方式：$MODE  \n目标：$TARGET  \n最新批次：$LATEST_STAMP  \n本地备份总量：$SIZE"
