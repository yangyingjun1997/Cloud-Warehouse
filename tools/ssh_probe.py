"""SSH 探测服务器部署现状（一次性工具脚本）。

用法：先设置环境变量 NUC_SSH_PASSWORD，再运行本脚本。
"""
import os
import sys
import time

import paramiko

HOST = '192.168.61.169'
USER = 'robot'
PASSWORD = os.environ.get('NUC_SSH_PASSWORD', '').strip()

CMDS = [
    'ls /opt/after-sales-warehouse/ 2>/dev/null | head -15',
    'df -h /opt | tail -1',
    'free -m | head -2',
    'ls -t /opt/after-sales-warehouse/releases/ 2>/dev/null | head -3',
    'systemctl list-units --type=service --no-pager | grep -iE "warehouse|gunicorn|django" | head -5',
    'ls /home/robot/ | head -20',
]


def main():
    if not PASSWORD:
        print('请先设置环境变量 NUC_SSH_PASSWORD（服务器 SSH 密码）。')
        print('PowerShell 示例：$env:NUC_SSH_PASSWORD = "你的密码"')
        return 1
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for attempt in range(3):
        try:
            client.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=30)
            print(f'CONNECTED on attempt {attempt + 1}')
            break
        except Exception as exc:  # noqa: BLE001
            print(f'attempt {attempt + 1} failed: {exc}')
            time.sleep(3)
    else:
        print('SSH CONNECT FAILED')
        return 1

    for cmd in CMDS:
        _, stdout, stderr = client.exec_command(cmd, timeout=30)
        print(f'## {cmd}')
        out = stdout.read().decode(errors='replace')
        err = stderr.read().decode(errors='replace')
        if out:
            print(out)
        if err:
            print('STDERR:', err)
    client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
