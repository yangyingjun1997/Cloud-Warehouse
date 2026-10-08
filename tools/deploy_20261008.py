"""部署 2026-10-08 最新版本到 NUC（192.168.61.169），不触碰原有数据库。

安全保证：
  - 部署包已排除 .env / db.sqlite3 / media / staticfiles
  - install.sh 部署前自动做数据库快照（pre-deploy-时间戳）+ 代码快照
  - 使用 --offline-update：只同步代码、跑迁移（migrate 只增不改）、收集静态文件
  - 数据库用户/库仅在不存在时才创建（幂等），不重建不导入

用法：.venv312/Scripts/python.exe tools/deploy_20261008.py
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
ZIP = ROOT / 'dist' / 'after-sales-warehouse-20261008c.zip'
SHA = ROOT / 'dist' / 'after-sales-warehouse-20261008c.zip.sha256'

REMOTE_TMP = '/tmp/after-sales-update-20261008c'
REMOTE_ZIP = '/tmp/after-sales-warehouse-20261008c.zip'


def run(client, cmd, timeout=900, sudo=False):
    if sudo:
        cmd = f"echo '{PASSWORD}' | sudo -S {cmd}"
    transport = client.get_transport()
    channel = transport.open_session()
    channel.get_pty()
    channel.settimeout(timeout)
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
        # Windows 写出的 .sha256 带 \r\n，Linux sha256sum 会把 \r 当文件名片段，上传前统一转 LF。
        content = SHA.read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'')
        with sftp.open(REMOTE_ZIP + '.sha256', 'wb') as fh:
            fh.write(content)
    sftp.close()
    print('上传完成')

    steps = [
        ('清理旧解压目录', f'rm -rf {REMOTE_TMP} && mkdir -p {REMOTE_TMP}', False),
        ('解压更新包', f'cd {REMOTE_TMP} && unzip -oq {REMOTE_ZIP}', False),
        ('SHA256 校验', f'cd /tmp && sha256sum -c after-sales-warehouse-20261008c.zip.sha256', False),
        ('确认包结构', f'test -f {REMOTE_TMP}/backend/manage.py && echo 包结构OK', False),
        ('执行离线更新', f'bash {REMOTE_TMP}/deploy/ubuntu22/install.sh --host {HOST} --offline-update', True),
    ]
    for title, cmd, sudo in steps:
        print(f'\n===== {title} =====')
        rc, _ = run(client, cmd, sudo=sudo)
        if rc != 0:
            print(f'\n[失败] {title} 退出码 {rc}，中止部署')
            client.close()
            return rc

    # 部署后验证
    verify = [
        ('服务状态', 'systemctl is-active after-sales-warehouse nginx', False),
        ('健康检查', 'curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1/health/', False),
        ('数据库连通', 'sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python /opt/after-sales-warehouse/backend/manage.py check --database default 2>&1 | tail -3', True),
    ]
    for title, cmd, sudo in verify:
        print(f'\n===== 验证：{title} =====')
        run(client, cmd, sudo=sudo)

    client.close()
    print('\n部署完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
