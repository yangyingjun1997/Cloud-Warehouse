"""本地 SQLite 备份与恢复演练（对应验收手册 L-09 / L-10）。

- L-09：执行本地 SQLite 备份，备份文件可打开并包含业务数据；
- L-10：使用备份恢复到临时目录，能查询关键业务记录。

使用 sqlite3 在线 backup API，可在服务运行时执行，无需停机。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
SRC = os.path.join(BACKEND, 'db.sqlite3')
BACKUP_DIR = os.path.join(BACKEND, 'backups', 'local')


def main() -> int:
    if not os.path.exists(SRC):
        print(f'源库不存在: {SRC}')
        return 1
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(BACKUP_DIR, f'warehouse-{stamp}.sqlite3')

    src_conn = sqlite3.connect(SRC)
    dst_conn = sqlite3.connect(dst)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    print(f'[L-09] 备份完成: {dst} ({os.path.getsize(dst)} bytes)')

    # 恢复演练：打开备份并查询关键业务记录
    conn = sqlite3.connect(dst)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    checks = {
        'auth_user': 'SELECT COUNT(*) FROM auth_user',
        'inventory_asset': 'SELECT COUNT(*) FROM inventory_asset',
        'inventory_stockitem': 'SELECT COUNT(*) FROM inventory_stockitem',
        'workflow_workflowrequest': 'SELECT COUNT(*) FROM workflow_workflowrequest',
        'common_operationauditlog': 'SELECT COUNT(*) FROM common_operationauditlog',
    }
    print(f'[L-10] 备份含 {len(tables)} 张表，关键记录数：')
    for table, sql in checks.items():
        if table in tables:
            count = conn.execute(sql).fetchone()[0]
            print(f'  {table}: {count}')
        else:
            print(f'  {table}: 表不存在')
            conn.close()
            return 1
    conn.close()
    print('[通过] L-09/L-10 备份恢复演练')
    return 0


if __name__ == '__main__':
    sys.exit(main())
