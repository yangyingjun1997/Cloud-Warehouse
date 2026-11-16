#!/usr/bin/env bash
# 自动把本机当前的所有 IPv4 地址登记进 Django 白名单和 nginx server_name。
#
# 背景：服务器 WiFi 走 DHCP，网段切换或租约变化后 IP 会变。
# 变了之后如果没登记，Django 会返回 400 DisallowedHost，客户端直接用不了。
# 这个脚本做成定时任务，网段变了自动跟上，不用人工改配置。
#
# 用法：
#   sudo ./auto-register-host.sh          # 检查并登记
#   sudo ./auto-register-host.sh --dry-run # 只看会改什么

set -euo pipefail

ENV_FILE="/srv/after-sales-warehouse/app.env"
NGINX_SITE="/etc/nginx/sites-available/after-sales-warehouse"
SERVER_NAME_SCRIPT="/opt/after-sales-warehouse/auto-register-host.py"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

if [[ ! -f "$ENV_FILE" ]]; then
    echo "找不到 $ENV_FILE" >&2
    exit 1
fi

# 收集本机所有内网 IPv4（排除回环、docker、VPN 虚拟网卡）
# utun/tun/tap 是 VPN 隧道，br-/veth 是 docker 网桥，都不该进白名单。
mapfile -t IPS < <(
    for dev in $(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | sed 's/@.*//'); do
        case "$dev" in
            lo|utun*|tun*|tap*|docker*|br-*|veth*|virbr*)
                continue
                ;;
        esac
        ip -4 -o addr show dev "$dev" scope global 2>/dev/null \
            | awk '{print $4}' | cut -d/ -f1
    done \
    | grep -vE '^(127\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[01]\.)' \
    | sort -u
)

if [[ ${#IPS[@]} -eq 0 ]]; then
    echo "没有找到内网 IPv4 地址，跳过。"
    exit 0
fi

echo "本机内网 IPv4: ${IPS[*]}"

if [[ $DRY_RUN -eq 1 ]]; then
    echo "（预览模式，不写文件）"
fi

# 注意：脚本开头有 set -e，python 用退出码 10 表示"有变更"，
# 必须用 || rc=$? 接住，否则 set -e 会让脚本在这里直接退出，
# 后面的重启逻辑永远执行不到。
rc=0
python3 - "$ENV_FILE" "$NGINX_SITE" "$DRY_RUN" "${IPS[@]}" <<'PY' || rc=$?
import re
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
nginx_path = Path(sys.argv[2])
dry_run = sys.argv[3] == '1'
new_ips = sys.argv[4:]

changed = []

# ---------- 1. Django .env ----------
lines = env_path.read_text(encoding='utf-8').splitlines()
out = []
for line in lines:
    if line.startswith('ALLOWED_HOSTS='):
        items = [x.strip() for x in line.split('=', 1)[1].split(',') if x.strip()]
        added = [ip for ip in new_ips if ip not in items]
        items.extend(added)
        if added:
            changed.append(f'ALLOWED_HOSTS += {",".join(added)}')
        out.append('ALLOWED_HOSTS=' + ','.join(items))
    elif line.startswith('CSRF_TRUSTED_ORIGINS='):
        items = [x.strip() for x in line.split('=', 1)[1].split(',') if x.strip()]
        added = []
        for ip in new_ips:
            origin = f'http://{ip}'
            if origin not in items:
                items.append(origin)
                added.append(origin)
        if added:
            changed.append(f'CSRF += {",".join(added)}')
        out.append('CSRF_TRUSTED_ORIGINS=' + ','.join(items))
    else:
        out.append(line)

if changed and not dry_run:
    env_path.write_text('\n'.join(out) + '\n', encoding='utf-8')

# ---------- 2. nginx server_name ----------
if nginx_path.exists():
    text = nginx_path.read_text(encoding='utf-8')

    def fix(match):
        items = match.group(1).split()
        added = [ip for ip in new_ips if ip not in items]
        if added:
            changed.append(f'nginx server_name += {",".join(added)}')
        return 'server_name ' + ' '.join(items + added) + ';'

    new_text = re.sub(r'server_name\s+([^;]+);', fix, text, count=1)
    if changed and not dry_run and new_text != text:
        nginx_path.write_text(new_text, encoding='utf-8')

if not changed:
    print('所有地址已登记，无需变更。')
    sys.exit(0)

for item in changed:
    print('  ' + item)

# 退出码 10 表示"有变更"，外层据此决定是否重启服务
sys.exit(10)
PY

if [[ $rc -eq 10 && $DRY_RUN -eq 0 ]]; then
    echo "配置有变更，重启服务..."
    systemctl restart after-sales-warehouse
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
    fi
    echo "完成。"
elif [[ $rc -eq 10 ]]; then
    echo "（预览：正式运行时会重启服务）"
elif [[ $rc -ne 0 ]]; then
    echo "登记脚本异常退出，退出码 $rc" >&2
    exit "$rc"
fi
exit 0
