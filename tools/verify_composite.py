"""查询成品组件总数 vs 在装数，确认组件数口径 bug 的具体数据。

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

sql = (
    "SELECT cu.name, COUNT(*) AS total, "
    "COUNT(*) FILTER (WHERE cl.disassembled_at IS NULL) AS active "
    "FROM inventory_compositeunit cu "
    "JOIN inventory_componentlink cl ON cl.composite_unit_id=cu.id "
    "GROUP BY cu.name ORDER BY cu.name;"
)
cmd = f"echo '{PASSWORD}' | sudo -S -u postgres psql -d warehouse -tAc '" + sql + "'"
stdin, stdout, stderr = c.exec_command(cmd)
print(stdout.read().decode(errors='replace'))
err = stderr.read().decode(errors='replace')
if err.strip() and 'could not change' not in err and 'sudo' not in err:
    print('err:', err[:200])
c.close()
