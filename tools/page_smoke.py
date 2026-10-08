"""页面冒烟测试：登录后依次访问手册要求的关键页面，验证均可打开。

对应手册「测试通过标准 - 页面冒烟：登录、工作台、申请、审批、出入库、报表可打开」。
"""
from __future__ import annotations

import os
import sys
import urllib.request
import urllib.parse
import http.cookiejar

BASE = os.environ.get('QA_BASE_URL', 'http://127.0.0.1:8000')
USERNAME = os.environ.get('QA_TEST_USER', 'qa_admin')
PASSWORD = os.environ.get('QA_TEST_PASSWORD', '').strip()

# 手册要求的关键页面（登录后应返回 200）
PAGES = [
    ('工作台', '/'),
    ('健康检查', '/health/'),
    ('新建申请', '/requests/new/'),
    ('我的审批', '/approvals/'),
    ('出入库记录', '/transactions/'),
    ('报表', '/reports/'),
    ('资产台账', '/warehouse/'),
    ('成品设备', '/warehouse/composite-units/'),
    ('补货建议', '/warehouse/replenishment/'),
    ('采购草稿', '/warehouse/purchase-drafts/'),
    ('采购订单', '/warehouse/purchase-orders/'),
    ('价格历史', '/reports/supplier-prices/'),
    ('供应商对账', '/reports/supplier-reconciliation/'),
]


def build_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get_csrf(opener):
    resp = opener.open(BASE + '/login/', timeout=10)
    html = resp.read().decode('utf-8', 'ignore')
    for line in html.splitlines():
        if 'csrfmiddlewaretoken' in line and 'value=' in line:
            return line.split('value="')[1].split('"')[0]
    return None


def main() -> int:
    if not PASSWORD:
        print('请先设置环境变量 QA_TEST_PASSWORD（QA 测试账号密码）。')
        print('PowerShell 示例：$env:QA_TEST_PASSWORD = "你的密码"')
        return 1
    opener = build_opener()
    token = get_csrf(opener)
    if not token:
        print('[失败] 无法获取登录 CSRF token（服务是否正常？）')
        return 1

    login_data = urllib.parse.urlencode({
        'csrfmiddlewaretoken': token,
        'username': USERNAME,
        'password': PASSWORD,
    }).encode()
    resp = opener.open(urllib.request.Request(BASE + '/login/', data=login_data), timeout=10)
    # 登录成功会跳转或返回工作台
    if resp.geturl().endswith('/login/') and '工作台' not in resp.read().decode('utf-8', 'ignore'):
        pass  # 仍靠后续页面判断

    print(f'账号 {USERNAME} 登录后页面冒烟：')
    failed = 0
    for name, path in PAGES:
        try:
            r = opener.open(BASE + path, timeout=10)
            ok = r.status == 200
            print(f'  [{"OK" if ok else r.status}] {name} {path}')
            if not ok:
                failed += 1
        except Exception as exc:
            print(f'  [失败] {name} {path} -> {exc}')
            failed += 1
    print('冒烟结果:', '全部通过' if failed == 0 else f'{failed} 个失败')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
