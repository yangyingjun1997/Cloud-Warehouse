"""UI 客观指标审计：不靠肉眼，直接量化关键设计指标。

对照 WCAG AA 与通用设计规范检查：
- 文本对比度（4.5:1）
- 触控目标尺寸（移动端 >= 44px）
- 字号（正文 >= 16px 基准，看是否有大量过小文字）
- 横向溢出（移动端不应有）
- 颜色变量一致性（主色/中性色数量）
"""
from __future__ import annotations

import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get('QA_BASE_URL', 'http://127.0.0.1:8000')
USERNAME = os.environ.get('QA_TEST_USER', 'qa_admin')
PASSWORD = os.environ.get('QA_TEST_PASSWORD', '').strip()

PAGES = [
    ('工作台', '/'),
    ('新建申请', '/requests/new/'),
    ('仓库管理', '/warehouse/'),
    ('成品设备', '/warehouse/composite-units/'),
]

AUDIT_JS = r"""
() => {
  const results = { tinyText: [], lowContrast: [], smallTargets: [], overflowX: false, colors: new Set() };

  function luminance(r, g, b) {
    const a = [r, g, b].map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function parseRGB(str) {
    const m = str.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    return m ? [+m[1], +m[2], +m[3]] : null;
  }
  function contrastRatio(fg, bg) {
    if (!fg || !bg) return null;
    const l1 = luminance(...fg), l2 = luminance(...bg);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return (hi + 0.05) / (lo + 0.05);
  }
  function effectiveBg(el) {
    let node = el;
    while (node && node !== document.documentElement) {
      const bg = getComputedStyle(node).backgroundColor;
      const rgb = parseRGB(bg);
      if (rgb && !bg.includes('rgba(0, 0, 0, 0)') && bg !== 'transparent') return rgb;
      node = node.parentElement;
    }
    return [255, 255, 255];
  }

  const isMobile = window.innerWidth < 500;

  // 文本检查
  document.querySelectorAll('body *').forEach(el => {
    const text = (el.innerText || '').trim();
    if (!text || el.children.length > 0) return; // 只看叶子文本节点
    const style = getComputedStyle(el);
    const fontSize = parseFloat(style.fontSize);
    if (fontSize < 11 && text.length > 0) {
      results.tinyText.push({ text: text.slice(0, 30), size: fontSize, tag: el.tagName });
    }
    const fg = parseRGB(style.color);
    const bg = effectiveBg(el);
    const ratio = contrastRatio(fg, bg);
    if (ratio !== null && ratio < 4.5 && fontSize < 18) {
      results.lowContrast.push({ text: text.slice(0, 30), ratio: Math.round(ratio * 100) / 100, fg: style.color });
    }
  });

  // 触控目标（移动端）
  if (isMobile) {
    document.querySelectorAll('button, a, input, select, [onclick]').forEach(el => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)) {
        results.smallTargets.push({ tag: el.tagName, text: (el.innerText || el.value || '').slice(0, 20), w: Math.round(rect.width), h: Math.round(rect.height) });
      }
    });
  }

  // 横向溢出
  results.overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;

  // 颜色使用统计
  document.querySelectorAll('body *').forEach(el => {
    const c = getComputedStyle(el).color;
    if (c) results.colors.add(c);
  });
  results.colors = Array.from(results.colors).slice(0, 40);

  // 去重、限量
  results.tinyText = results.tinyText.slice(0, 10);
  results.lowContrast = results.lowContrast.slice(0, 10);
  results.smallTargets = results.smallTargets.slice(0, 15);
  return results;
}
"""


def main() -> int:
    if not PASSWORD:
        print('请先设置环境变量 QA_TEST_PASSWORD（QA 测试账号密码）。')
        print('PowerShell 示例：$env:QA_TEST_PASSWORD = "你的密码"')
        return 1
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.goto(BASE + '/login/')
        page.fill('input[name="username"]', USERNAME)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state('networkidle')
        if '/login/' in page.url:
            print('登录失败')
            browser.close()
            return 1

        for name, path in PAGES:
            for label, vw in (('desktop', 1280), ('mobile', 390)):
                page.set_viewport_size({'width': vw, 'height': 900})
                page.goto(BASE + path)
                page.wait_for_load_state('networkidle')
                key = f'{name}-{label}'
                report[key] = page.evaluate(AUDIT_JS)
        browser.close()

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ui-review', 'audit.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # 打印摘要（避免 emoji 触发 GBK 编码错误）
    def safe(s):
        return str(s).encode('gbk', errors='replace').decode('gbk')

    for key, data in report.items():
        print(f'\n=== {key} ===')
        print(f"  横向溢出: {'是(问题!)' if data['overflowX'] else '否'}")
        print(f"  过小文字(<11px): {len(data['tinyText'])} 处")
        for t in data['tinyText'][:5]:
            print(f"    - {t['size']}px [{t['tag']}] {safe(t['text'])}")
        print(f"  低对比度(<4.5): {len(data['lowContrast'])} 处")
        for t in data['lowContrast'][:5]:
            print(f"    - {t['ratio']}:1 {t['fg']} | {safe(t['text'])}")
        if 'mobile' in key:
            print(f"  触控目标(<44px): {len(data['smallTargets'])} 处")
            for t in data['smallTargets'][:8]:
                print(f"    - {t['w']}x{t['h']} [{t['tag']}] {safe(t['text'])}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
