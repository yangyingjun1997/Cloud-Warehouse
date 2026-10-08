"""部署后验证 192.168.61.169 服务状态。

用法：先设置环境变量 NUC_SSH_PASSWORD，再运行本脚本。
"""
import os
import sys

import paramiko

PASSWORD = os.environ.get('NUC_SSH_PASSWORD', '').strip()
if not PASSWORD:
    print('请先设置环境变量 NUC_SSH_PASSWORD（服务器 SSH 密码）。')
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.61.169', username='robot', password=PASSWORD, timeout=30, banner_timeout=30)


def run(cmd, sudo=False):
    if sudo:
        cmd = f"echo '{PASSWORD}' | sudo -S " + cmd
    stdin, stdout, stderr = c.exec_command(cmd)
    rc = stdout.channel.recv_exit_status()
    return rc, stdout.read().decode(errors='replace').strip(), stderr.read().decode(errors='replace').strip()


checks = [
    ('主服务状态', 'systemctl is-active after-sales-warehouse', False),
    ('Nginx 状态', 'systemctl is-active nginx', False),
    ('本机健康检查', 'curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1/health/', False),
    ('内网健康检查', 'curl -s -o /dev/null -w "HTTP %{http_code}" http://192.168.61.169/health/', False),
    ('数据库连通', 'sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python /opt/after-sales-warehouse/backend/manage.py check --database default 2>&1 | tail -2', True),
    ('定时器数量', 'systemctl list-timers --no-pager | grep -c after-sales', False),
    ('当前版本', 'cat /opt/after-sales-warehouse/current-release 2>/dev/null || echo 无记录', False),
]
for title, cmd, sudo in checks:
    rc, out, err = run(cmd, sudo)
    print(f'[{title}] rc={rc}')
    if out:
        print('  ', out[:300])
    if err and rc != 0:
        print('  err:', err[:200])
c.close()
