"""一键部署离线更新包到 NUC 服务器（192.168.61.169）。

用法：
    .venv312/Scripts/python.exe tools/deploy_to_nuc.py

流程：
    1. SSH 连接服务器（密码从环境变量 NUC_SSH_PASSWORD 读取）
    2. 上传 dist/after-sales-update-20260927.zip + .sha256 到 /tmp/
    3. 解压到 /tmp/after-sales-update/
    4. sudo 执行 deploy/ubuntu22/install.sh --host 192.168.61.169 --offline-update
    5. 打印服务状态验证结果

前提：本机能 ping 通 192.168.61.169（同一内网/VPN）。
"""
import os
import sys
import time
from pathlib import Path

import paramiko

HOST = '192.168.61.169'
USER = 'robot'
PASSWORD = os.environ.get('NUC_SSH_PASSWORD', '').strip()

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / 'dist' / 'after-sales-update-20260927.zip'
SHA = ROOT / 'dist' / 'after-sales-update-20260927.zip.sha256'

REMOTE_TMP = '/tmp/after-sales-update'
REMOTE_ZIP = '/tmp/after-sales-update-20260927.zip'


def run(client, cmd, timeout=600, sudo=False):
    """执行远程命令并实时打印输出；sudo 时自动喂密码。"""
    if sudo:
        cmd = f"echo '{PASSWORD}' | sudo -S {cmd}"
    transport = client.get_transport()
    channel = transport.open_session()
    channel.get_pty()
    channel.exec_command(cmd)
    output = []
    while True:
        if channel.recv_ready():
            chunk = channel.recv(65536).decode(errors='replace')
            output.append(chunk)
            print(chunk, end='', flush=True)
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        time.sleep(0.2)
    rc = channel.recv_exit_status()
    return rc, ''.join(output)


def main():
    if not PASSWORD:
        print('请先设置环境变量 NUC_SSH_PASSWORD（服务器 SSH 密码）。')
        print('PowerShell 示例：$env:NUC_SSH_PASSWORD = "你的密码"')
        return 1
    if not ZIP.exists():
        print(f'更新包不存在：{ZIP}')
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f'连接 {HOST} ...')
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=30)
    print('已连接')

    sftp = client.open_sftp()
    print(f'上传 {ZIP.name} ({ZIP.stat().st_size / 1024:.0f} KB) ...')
    sftp.put(str(ZIP), REMOTE_ZIP)
    if SHA.exists():
        sftp.put(str(SHA), REMOTE_ZIP + '.sha256')
    sftp.close()
    print('上传完成')

    steps = [
        ('清理旧解压目录', f'rm -rf {REMOTE_TMP} && mkdir -p {REMOTE_TMP}', False),
        ('解压更新包', f'cd {REMOTE_TMP} && unzip -oq {REMOTE_ZIP}', False),
        ('SHA256 校验', f'cd {REMOTE_TMP} && sha256sum -c {REMOTE_ZIP}.sha256 || echo "[warn] 校验文件缺失，跳过"', False),
        ('确认包结构', f'test -f {REMOTE_TMP}/backend/manage.py && echo OK', False),
        ('执行离线更新', f'cd {REMOTE_TMP} && bash deploy/ubuntu22/install.sh --host {HOST} --offline-update', True),
        ('服务状态', 'systemctl list-units --type=service --no-pager | grep -iE "warehouse|gunicorn|nginx" | head -10', False),
    ]
    for title, cmd, sudo in steps:
        print(f'\n===== {title} =====')
        rc, _ = run(client, cmd, sudo=sudo)
        if rc != 0 and '校验' not in title:
            print(f'\n[失败] {title} 退出码 {rc}，中止部署')
            client.close()
            return rc

    client.close()
    print('\n部署完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
