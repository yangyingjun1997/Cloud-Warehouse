"""部署后数据库数据量抽查，确认原有数据未受影响。

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
    "SELECT 'assets', COUNT(*) FROM inventory_asset "
    "UNION ALL SELECT 'users', COUNT(*) FROM auth_user "
    "UNION ALL SELECT 'requests', COUNT(*) FROM workflow_workflowrequest "
    "UNION ALL SELECT 'transactions', COUNT(*) FROM workflow_inventorytransaction "
    "UNION ALL SELECT 'purchase_orders', COUNT(*) FROM workflow_purchaseorder "
    "UNION ALL SELECT 'purchase_requests', COUNT(*) FROM workflow_purchaserequest "
    "UNION ALL SELECT 'audit_logs', COUNT(*) FROM common_operationauditlog "
    "UNION ALL SELECT 'stock_items', COUNT(*) FROM inventory_stockitem "
    "ORDER BY 1;"
)
stdin, stdout, stderr = c.exec_command(
    f"echo '{PASSWORD}' | sudo -S -u postgres psql -d warehouse -tAc \"" + sql + "\""
)
print(stdout.read().decode(errors='replace'))
err = stderr.read().decode(errors='replace')
if err.strip():
    print('err:', err[:200])
c.close()
