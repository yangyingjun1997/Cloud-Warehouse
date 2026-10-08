#!/usr/bin/env bash
set -Eeuo pipefail

URL_FILE=/var/lib/cloudflared/current-url
DINGTALK_SENDER=/usr/local/libexec/after-sales-warehouse-quick-tunnel-dingtalk

mkdir -p "$(dirname "$URL_FILE")"

# 注意：这里刻意不删除 $URL_FILE。
# cloudflared 会在网络异常时反复重试，服务随之重启；若每次都清空地址文件，
# 上一次已经生效的访问入口就会消失，而网络恢复前又写不进新地址，
# 结果是整个故障期间都查不到任何可用入口。
# 地址只在 cloudflared 输出新地址时被 handle_line 覆盖。

extract_url() {
  # cloudflared 各版本输出格式不一：URL 可能紧跟 "Visit it at" 同行、独占一行，
  # 或落在表格框线 "|" 内；运行久了还会带 ANSI 颜色码。
  #
  # 原先要求 URL 之后必须是空白、"|" 或行尾，这带来两个代价：
  #   1. URL 紧跟引号、斜杠、ANSI 序列或 CRLF 之外的内容时，整行漏匹配，地址提取不出来；
  #   2. 排除 api.trycloudflare.com 依赖其后的 "/tunnel" 路径，判断依据脆弱。
  #
  # 改为：先剥离 ANSI 颜色码，再取出全部 trycloudflare.com 地址，
  # 最后按主机名直接排除 Cloudflare 自身基础设施域名（它们不是临时访问地址）。
  sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' \
    | grep -oE 'https://[A-Za-z0-9][A-Za-z0-9.-]*\.trycloudflare\.com' \
    | grep -vE '^https://(api|cp|regionarg|cfargotunnel|probe)\.trycloudflare\.com$' \
    | tail -n 1
}

handle_line() {
  local line="$1" url previous temporary_file
  printf '%s\n' "$line"
  url="$(printf '%s\n' "$line" | extract_url || true)"
  [[ -n "$url" ]] || return 0

  previous="$(cat "$URL_FILE" 2>/dev/null || true)"
  [[ "$url" != "$previous" ]] || return 0

  temporary_file="$(mktemp "${URL_FILE}.XXXXXX")"
  printf '%s\n' "$url" > "$temporary_file"
  chmod 640 "$temporary_file"
  mv -f "$temporary_file" "$URL_FILE"
  logger -t after-sales-warehouse-quick-tunnel "新的临时访问地址：$url"

  if [[ -x "$DINGTALK_SENDER" ]]; then
    if ! "$DINGTALK_SENDER" "$url"; then
      logger -t after-sales-warehouse-quick-tunnel "临时地址钉钉通知发送失败：$url"
    fi
  fi
}

set +e
/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:80 --http-host-header 127.0.0.1 2>&1 \
  | while IFS= read -r line; do handle_line "$line"; done
cloudflared_status="${PIPESTATUS[0]}"
set -e
exit "$cloudflared_status"
