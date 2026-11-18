#!/usr/bin/env bash
# media 孤儿文件清理：删除数据库已不引用、且超过保留期的磁盘文件。
#
# 为什么需要：删除资产附件、替换站点 logo 后，Django 只删数据库行，
# 磁盘文件仍然留在 media/ 里。长期运行会积累成看不见的空间占用。
#
# 安全约束（重要）：
#   1. 只处理「数据库里已无任何引用」的文件，且有宽限期（默认 7 天），
#      避免误删正在上传但尚未落库的临时文件。
#   2. 默认 dry-run，加 --apply 才真的删除。定时任务用 --apply。
#   3. 只扫描白名单子目录（attachments/ branding/ 等），不做全盘 find，
#      避免将来新增的目录被误伤。
set -Eeuo pipefail

APP_DATA_DIR=/srv/after-sales-warehouse
MEDIA_DIR="$APP_DATA_DIR/media"
DATABASE_NAME=warehouse
GRACE_DAYS="${MEDIA_GC_GRACE_DAYS:-7}"
APPLY=0
NOTIFIER=/usr/local/libexec/after-sales-warehouse-dingtalk-notify

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --grace-days=*) GRACE_DAYS="${arg#*=}" ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

if [[ ! -d "$MEDIA_DIR" ]]; then
  echo "media 目录不存在，无需清理：$MEDIA_DIR"
  exit 0
fi

# 以 root 运行时 sudo -u postgres 会提示「could not change directory」，
# 切到 / 消除该噪音（postgres 用户对当前目录无权限时 psql 会警告）。
cd /

# 从数据库导出所有被引用的媒体相对路径。
REFERENCED_LIST="$(mktemp)"
trap 'rm -f "$REFERENCED_LIST"' EXIT

sudo -u postgres psql -d "$DATABASE_NAME" -tAc "
  SELECT file FROM inventory_assetattachment WHERE file IS NOT NULL AND file <> ''
  UNION
  SELECT logo FROM common_sitesettings WHERE logo IS NOT NULL AND logo <> ''
  UNION
  SELECT label_logo FROM common_sitesettings WHERE label_logo IS NOT NULL AND label_logo <> ''
  UNION
  SELECT label_logo_mono FROM common_sitesettings WHERE label_logo_mono IS NOT NULL AND label_logo_mono <> ''
" | sed '/^$/d' | sort -u > "$REFERENCED_LIST"

echo "数据库引用文件数：$(wc -l < "$REFERENCED_LIST")"
echo "宽限期：${GRACE_DAYS} 天　　模式：$([[ "$APPLY" == "1" ]] && echo '实际删除' || echo 'dry-run')"

CANDIDATES=0
FREED_BYTES=0

# 只扫描已知的业务目录，新目录不会被误删
while IFS= read -r -d '' candidate; do
  relative="${candidate#"$MEDIA_DIR"/}"
  # 数据库里仍被引用 → 跳过
  grep -qxF "$relative" "$REFERENCED_LIST" && continue
  # 在宽限期内 → 跳过（可能是正在上传的临时文件）
  if [[ -n "$(find "$candidate" -mtime -"$GRACE_DAYS" 2>/dev/null)" ]]; then
    continue
  fi
  size="$(stat -c %s "$candidate" 2>/dev/null || echo 0)"
  CANDIDATES=$((CANDIDATES + 1))
  FREED_BYTES=$((FREED_BYTES + size))
  if [[ "$APPLY" == "1" ]]; then
    rm -f "$candidate"
  else
    echo "  可删除：$relative ($(numfmt --to=iec "$size" 2>/dev/null || echo "${size}B"))"
  fi
done < <(find "$MEDIA_DIR/attachments" "$MEDIA_DIR/branding" -type f -print0 2>/dev/null)

FREED_HUMAN="$(numfmt --to=iec "$FREED_BYTES" 2>/dev/null || echo "${FREED_BYTES}B")"
echo "孤儿文件：${CANDIDATES} 个，合计 ${FREED_HUMAN}"
# 清掉删空的目录
if [[ "$APPLY" == "1" && "$CANDIDATES" -gt 0 ]]; then
  find "$MEDIA_DIR" -mindepth 1 -type d -empty -delete 2>/dev/null || true
fi

# 实际删除并且量较大时通知一次，便于发现问题（例如误删）
if [[ "$APPLY" == "1" && "$CANDIDATES" -ge 20 ]] && [[ -x "$NOTIFIER" ]]; then
  "$NOTIFIER" '售后仓库 media 清理' "主机：$(hostname)  \n时间：$(date --iso-8601=seconds)  \n清理孤儿文件：${CANDIDATES} 个，释放 ${FREED_HUMAN}  \n宽限期：${GRACE_DAYS} 天\n\n如数量异常，请检查是否误删：\`sudo journalctl -u after-sales-warehouse-media-gc.service -n 100\`" || true
fi
