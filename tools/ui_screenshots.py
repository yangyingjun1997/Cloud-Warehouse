"""登录后截取核心页面，用于 UI 走查。

输出到 artifacts/ui-review/，每个页面桌面 1280 宽 + 移动 390 宽各一张。
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get('QA_BASE_URL', 'http://127.0.0.1:8000')
USERNAME = os.environ.get('QA_TEST_USER', 'qa_admin')
PASSWORD = os.environ.get('QA_TEST_PASSWORD', '').strip()
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ui-review')

PAGES = [
    ('home', '/'),
    ('request_create', '/requests/new/'),
    ('approval_queue', '/approvals/'),
    ('warehouse', '/warehouse/'),
    ('composite_units', '/warehouse/composite-units/'),
    ('transactions', '/transactions/'),
    ('reports', '/reports/'),
]


def main() -> int:
    if not PASSWORD:
        print('请先设置环境变量 QA_TEST_PASSWORD（QA 测试账号密码）。')
        print('PowerShell 示例：$env:QA_TEST_PASSWORD = "你的密码"')
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        # 登录
        page.goto(BASE + '/login/')
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state('networkidle')
        if '/login/' in page.url:
            print('登录失败，当前仍在登录页')
            browser.close()
            return 1
        print('登录成功')

        for name, path in PAGES:
            # 桌面
            page.set_viewport_size({'width': 1280, 'height': 900})
            page.goto(BASE + path)
            page.wait_for_load_state('networkidle')
            desktop_path = os.path.join(OUT_DIR, f'{name}-desktop.png')
            page.screenshot(path=desktop_path, full_page=True)
            # 移动
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(BASE + path)
            page.wait_for_load_state('networkidle')
            mobile_path = os.path.join(OUT_DIR, f'{name}-mobile.png')
            page.screenshot(path=mobile_path, full_page=True)
            print(f'已截 {name}: 桌面+移动')

        browser.close()
    print(f'全部截图输出到 {OUT_DIR}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
