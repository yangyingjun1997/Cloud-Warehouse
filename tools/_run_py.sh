#!/bin/bash
# 用法: bash _run_py.sh <python脚本路径>
cd /opt/after-sales-warehouse/backend
sudo -u warehouse /opt/after-sales-warehouse/venv/bin/python manage.py shell <<PYEOF
exec(open('$1').read())
PYEOF
