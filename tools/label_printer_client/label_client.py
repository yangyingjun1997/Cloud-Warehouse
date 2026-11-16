"""八维通仓库 · 资产标签打印客户端（30×20 mm 四联版）。

部署在连接标签打印机的那台 Windows 电脑上。工作方式：

1. 每 N 秒向仓库服务器请求一条待打印作业；
2. 按打印机的实际 DPI 把标签渲染成位图；
3. 一张 A4 大小的 60×40 mm 版面切成 4 个 30×20 mm 小标签；
4. 调用 Windows 打印队列出纸；
5. 把成功或失败结果回报给服务器。

为什么用图片而不是 PDF：贴纸打印机的驱动对 PDF 支持经常不一致，
而灰度位图是几乎所有 Windows 驱动程序都能正确处理的格式。

用法（命令行）：

    python label_client.py --server http://192.168.123.101 --token <API_TOKEN>

用法（图形界面，双击 exe 即可）：

    python label_client.py --gui

配置文件默认保存在 `%APPDATA%\\BwtWarehouse\\label_client.json`，
记录服务器地址、Token、打印机名、DPI 和版式，改一次即可长期使用。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    print('缺少依赖 Pillow，请先执行：pip install pillow', file=sys.stderr)
    raise

try:
    import win32print
    import win32ui
    from PIL import ImageWin
except ImportError:  # pragma: no cover
    win32print = None
    win32ui = None
    ImageWin = None


LOG = logging.getLogger('label-client')


def safe_print(text: str) -> None:
    """在中文 Windows 控制台打印，避免 GBK 编码不了的字符导致崩溃。

    打包成 exe 后 stdout 默认走系统代码页（GBK），
    遇到 `✓`、`→` 这类符号会抛 UnicodeEncodeError 直接退出。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = (getattr(sys.stdout, 'encoding', None) or 'utf-8')
        print(text.encode(encoding, errors='replace').decode(encoding, errors='replace'))


CONFIG_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'BwtWarehouse')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'label_client.json')

DEFAULT_CONFIG = {
    'server': 'http://192.168.123.101',
    'token': '',
    'printer': '',
    'dpi': 600,
    'sheet_width_mm': 60,
    'sheet_height_mm': 40,
    'columns': 2,
    'rows': 2,
    'gap_mm': 0.0,
    'margin_mm': 0.0,
    # 整体偏移补偿：打印机机械原点与纸张边缘总有误差，
    # 正值表示内容往右/往下移。单位 mm，可用 0.1 的精度微调。
    'offset_x_mm': 0.0,
    'offset_y_mm': 0.0,
    # ZT610 优先使用 RAW ZPL，GDI 仅作为兼容通道。
    'print_channel': 'zpl',
    'client_name': '',
    'interval': 5.0,
}


# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as handle:
                config.update(json.load(handle))
        except (OSError, ValueError):
            LOG.warning('配置文件无法读取，使用默认值：%s', CONFIG_PATH)
    if not config.get('client_name'):
        config['client_name'] = os.environ.get('COMPUTERNAME', 'windows-client')
    return config


def save_config(config: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def normalize_server(value: str, *, default_scheme: str = 'http') -> str:
    """把用户填的服务器地址补全成可用的 base URL。

    允许这几种写法，都归一化成 `scheme://host[:port]`：

        192.168.123.101          → http://192.168.123.101
        192.168.123.101:8080     → http://192.168.123.101:8080
        wms.bwt.com              → http://wms.bwt.com
        wms.bwt.com:8080         → http://wms.bwt.com:8080
        http://wms.bwt.com       → http://wms.bwt.com
        https://wms.bwt.com      → https://wms.bwt.com
        http://wms.bwt.com/      → http://wms.bwt.com   （去掉尾部斜杠）
        192.168.123.101/warehouse → http://192.168.123.101/warehouse

    域名访问时要求 `ALLOWED_HOSTS` 里已登记该域名，否则 Django 会返回 400。
    """
    text = (value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = f'{default_scheme}://{text}'
    scheme, _, rest = text.partition('://')
    scheme = scheme.lower()
    if scheme not in ('http', 'https'):
        scheme = default_scheme
    # 去掉路径部分（只保留 host[:port]），客户端始终请求固定 API 前缀
    rest = rest.split('/', 1)[0].split('?', 1)[0].strip()
    if not rest:
        return ''
    return f'{scheme}://{rest}'


def server_display_name(base_url: str) -> str:
    """从 base URL 提取主机名，用于日志和界面展示。"""
    if not base_url:
        return ''
    rest = base_url.split('://', 1)[-1]
    return rest.split('/', 1)[0]


def probe_server(base_url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """检查服务器是否可达，返回 (是否成功, 说明文字)。

    只请求 `/health/`，不涉及登录，用来在配置界面上给仓管一个明确结论。
    """
    if not base_url:
        return False, '请先填写服务器地址'
    url = f'{base_url}/health/'
    try:
        request = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(200).decode('utf-8', errors='replace')
            return True, f'连接正常（HTTP {response.status}）{body[:60]}'
    except urllib.error.HTTPError as exc:
        # 400 通常是 ALLOWED_HOSTS 没登记该域名
        if exc.code == 400:
            return False, (
                f'服务器可达，但拒绝了域名 {server_display_name(base_url)}。'
                ' 需要把该域名加入服务器的 ALLOWED_HOSTS。'
            )
        return False, f'服务器返回 HTTP {exc.code}'
    except urllib.error.URLError as exc:
        reason = getattr(exc, 'reason', exc)
        return False, f'无法连接：{reason}'
    except OSError as exc:
        return False, f'无法连接：{exc}'


def detect_printer_dpi(printer_name: str = '') -> int | None:
    """从打印机驱动读取水平 DPI。读不到时返回 None。"""
    if win32print is None:
        return None
    try:
        if not printer_name:
            printer_name = win32print.GetDefaultPrinter()

        # 驱动 DC 的 LOGPIXELSX/Y 才是位图 1:1 输出时的真实分辨率。
        # 只信 PrintQuality 时，若配置 600 而驱动实际 203，60×40mm 会被
        # 放大近 3 倍，整张标签会溢出到下一格。
        if win32ui is not None:
            hdc = win32ui.CreateDC()
            try:
                hdc.CreatePrinterDC(printer_name)
                dpi_x = int(hdc.GetDeviceCaps(88))
                dpi_y = int(hdc.GetDeviceCaps(90))
                if 150 <= dpi_x <= 2400 and abs(dpi_x - dpi_y) <= 2:
                    LOG.info('打印机 %s 驱动 DPI=%s', printer_name, dpi_x)
                    return dpi_x
            finally:
                hdc.DeleteDC()

        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
            # pDevMode 里带有驱动报告的打印分辨率
            devmode = info.get('pDevMode')
            if devmode is not None and getattr(devmode, 'PrintQuality', 0) > 0:
                quality = devmode.PrintQuality
                if quality in (600, 1200, 300, 203, 240, 360, 720):
                    return quality
        finally:
            win32print.ClosePrinter(handle)
    except Exception:  # noqa: BLE001 - 读不到就退回配置值
        LOG.debug('无法自动检测 DPI，使用配置值', exc_info=True)
    return None


def list_printers() -> list[str]:
    if win32print is None:
        return []
    try:
        printers = win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        )
        return [item[2] for item in printers]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 渲染


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


def load_font(size: int, bold: bool = False):
    """挑一个能显示中文的字体。

    贴纸机那台机器通常是中文 Windows，微软雅黑一定存在；这里按优先级回退，
    避免字体缺失时整张标签变成方块。
    """
    candidates = [
        r'C:\Windows\Fonts\msyhbd.ttc' if bold else r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\msyhbd.ttf' if bold else r'C:\Windows\Fonts\msyh.ttf',
        r'C:\Windows\Fonts\simhei.ttf',
        r'C:\Windows\Fonts\simsun.ttc',
        r'C:\Windows\Fonts\arialbd.ttf' if bold else r'C:\Windows\Fonts\arial.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(size, 6))
            except OSError:
                continue
    return ImageFont.load_default()


def _trim_white(image: Image.Image, threshold: int = 245) -> Image.Image:
    """裁掉二维码四周的白边，只保留码点区域。"""
    gray = image.convert('L')
    width, height = gray.size
    pixels = gray.load()
    left, top, right, bottom = width, height, 0, 0
    for y in range(height):
        for x in range(width):
            if pixels[x, y] < threshold:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if left > right or top > bottom:
        return image
    return image.crop((left, top, right + 1, bottom + 1))


def _qr_image(label: dict, target_px: int) -> Image.Image | None:
    qr_b64 = label.get('qr_image') or ''
    if not qr_b64:
        return None
    raw = base64.b64decode(qr_b64)
    image = Image.open(io.BytesIO(raw)).convert('L')
    image = _trim_white(image)
    # 裁边后重新补静区。二维码四周没有留白时，贴纸边缘、文字或打印机
    # 裁切都会干扰定位；约 8% 的静区在 30x20 mm 小标签上兼顾识别率和尺寸。
    quiet = max(2, int(round(target_px * 0.08)))
    inner_px = max(1, target_px - quiet * 2)
    canvas = Image.new('L', (target_px, target_px), 255)
    canvas.paste(image.resize((inner_px, inner_px), Image.NEAREST), (quiet, quiet))
    return canvas


def get_logo_image() -> Image.Image | None:
    """读取随包分发的单色 logo。没有就返回 None，退回纯文字。

    两种来源都支持：
    1. PyInstaller 打包后解到 `sys._MEIPASS` 里的资源
    2. 源码目录旁边的 assets/label-logo-mono.png
    """
    candidates = []
    base = getattr(sys, '_MEIPASS', None)
    if base:
        candidates.append(os.path.join(base, 'label-logo-mono.png'))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, 'assets', 'label-logo-mono.png'))
    candidates.append(os.path.join(here, 'label-logo-mono.png'))
    for path in candidates:
        if os.path.exists(path):
            try:
                image = Image.open(path).convert('L')
                # 裁掉四周白边，否则 logo 会因为留白显得特别小
                bbox = ImageChops.invert(image).getbbox()
                return image.crop(bbox) if bbox else image
            except OSError:
                continue
    return None


def render_cell(label: dict, width: int, height: int, dpi: int) -> Image.Image:
    """渲染单个 30×20 mm 小标签。

    版式（前缀在值上方，各自独立一行）：

        ┌──────────────────────────┐
        │ [LOGO]            ┌────┐ │
        │ 资产名            │ QR │ │
        │ 资产编码：        │    │ │
        │ SC-XXX-0001       └────┘ │
        │ 序列号：                 │
        │ XXX                      │
        └──────────────────────────┘

    版面必须**撑满** 30×20mm：内容只用到一半高度时，四联之间会留下大片空白，
    看起来像没对齐。这里先量出每块的实际高度，再把剩余空间按比例分配给行距。
    """
    image = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(image)

    # 30×20 mm 是标准四联格；1×1 整张 60×40 时按同一比例放大字号，
    # 否则文字会缩在左上角，二维码反而显得特别大。
    base_w = mm_to_px(30, dpi)
    base_h = mm_to_px(20, dpi)
    scale = max(1.0, min(width / base_w, height / base_h))

    pad = max(2, int(round(mm_to_px(0.6 * scale, dpi))))

    # 二维码只承担定位，编码和 SN 才是人工核对重点。
    # 30mm 宽时控制在约 6mm，底边与“资产名”区域对齐，不再压到编码行。
    qr_px = min(
        int(height * 0.30),
        int(width * 0.30),
        mm_to_px(6.0 * scale, dpi),
    )

    logo_font = load_font(mm_to_px(1.5 * scale, dpi), bold=True)
    name_font = load_font(mm_to_px(1.7 * scale, dpi), bold=True)
    label_font = load_font(mm_to_px(1.1 * scale, dpi))
    value_font = load_font(mm_to_px(1.5 * scale, dpi), bold=True)

    qr = _qr_image(label, qr_px)
    # 右侧留出机械误差余量，二维码左侧紧贴文字列。
    qr_right_inset = mm_to_px(3.0 * scale, dpi)
    qr_x = max(pad, width - qr_right_inset - qr_px)
    top_text_width = max(mm_to_px(8.0 * scale, dpi), qr_x - pad - mm_to_px(0.8 * scale, dpi))
    full_text_width = width - pad * 2

    def clip(text: str, font, limit: int) -> str:
        if not text:
            return ''
        if draw.textlength(text, font=font) <= limit:
            return text
        while text and draw.textlength(text + '..', font=font) > limit:
            text = text[:-1]
        return (text + '..') if text else ''

    def line_height(font) -> int:
        """字体的实际行高，用于精确排布。"""
        bbox = draw.textbbox((0, 0), '资产编码A1', font=font)
        return bbox[3] - bbox[1]

    def fit_font(text: str, base_mm: float, limit: int):
        """按可用宽度自动缩小字号，保证长编码不被截断。

        DZSB 行政编码有 22 个字符，固定字号放不进 30mm 的标签，
        缩字号比截断安全：宁可小一点也必须能读全。
        """
        size_px = mm_to_px(base_mm, dpi)
        for shrink in (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60):
            font = load_font(int(size_px * shrink), bold=True)
            if draw.textlength(text, font=font) <= limit:
                return font
        return load_font(int(size_px * 0.60), bold=True)

    # ---- 先决定要画哪些行
    serial = str(label.get('manufacture_sn') or '')
    rows: list[tuple[str, object, str]] = []   # (类型, 字体, 文本)

    logo = get_logo_image()
    logo_img = None
    if logo is not None:
        logo_w = min(int(top_text_width * 0.72), mm_to_px(9.0 * scale, dpi))
        ratio = logo.size[1] / logo.size[0]
        logo_h = max(1, int(logo_w * ratio))
        max_h = mm_to_px(2.8 * scale, dpi)
        if logo_h > max_h:
            logo_h = max_h
            logo_w = max(1, int(logo_h / ratio))
        logo_img = (logo.resize((logo_w, logo_h), Image.LANCZOS), logo_h)
    else:
        rows.append(('text', logo_font, clip(str(label.get('logo_text') or ''), logo_font, top_text_width)))

    rows.append(('text', name_font, clip(str(label.get('title') or ''), name_font, top_text_width)))
    rows.append(('text', label_font, '资产编码：'))

    # 编码和 SN 用自适应字号：DZSB 编码有 22 字符，固定字号会截断
    code_text = str(label.get('primary_code') or '')
    code_font = fit_font(code_text, 1.5, full_text_width) if code_text else value_font
    rows.append(('text', code_font, code_text))

    if serial:
        rows.append(('text', label_font, '序列号：'))
        sn_font = fit_font(serial, 1.5, full_text_width)
        rows.append(('text', sn_font, serial))

    # ---- 计算固定占用
    logo_block_h = (logo_img[1] if logo_img else 0)
    content_h = sum(line_height(font) for _, font, _ in rows)
    used = logo_block_h + content_h

    # ---- 剩余空间当作行距，均分到每行之间（撑满整格）
    avail = height - pad * 2
    gaps = len(rows) + (1 if logo_img else 0)
    slack = max(0, avail - used)
    gap = slack / gaps if gaps else 0
    # 不设硬上限：无序列号的标签行数少，若此处截断就会留下大片空白，
    # 和有条码的标签高度不一致，四联看起来就是"没对齐"。
    gap = min(gap, mm_to_px(3.2 * scale, dpi))

    y = float(pad)
    if logo_img:
        image.paste(logo_img[0], (pad, int(y)))
        y += logo_img[1] + gap
    for kind, font, text in rows:
        h = line_height(font)
        baseline = draw.textbbox((0, 0), text or 'A', font=font)
        draw.text((pad, int(y) - baseline[1]), text, fill=0 if kind == 'text' else 90, font=font)
        y += h + gap

    if qr is not None:
        # 顶部对齐 logo，底边不超过“资产名”下方，避免压住编码行。
        image.paste(qr, (qr_x, pad))

    return image


def render_sheet(labels: list[dict], config: dict, dpi: int | None = None) -> Image.Image:
    """把若干标签拼成一张 60×40 mm（2×2=4 联）的整页。

    dpi 传 None 时用配置值；传入则按传入值渲染。
    打印前应传入打印机驱动的真实 DPI，否则图与设备分辨率不一致会导致物理尺寸偏移。
    """
    dpi = int(dpi or config.get('dpi') or 600)
    sheet_w = mm_to_px(float(config.get('sheet_width_mm') or 60), dpi)
    sheet_h = mm_to_px(float(config.get('sheet_height_mm') or 40), dpi)
    cols = max(1, int(config.get('columns') or 2))
    rows = max(1, int(config.get('rows') or 2))
    gap = mm_to_px(float(config.get('gap_mm') or 0), dpi)
    margin = mm_to_px(float(config.get('margin_mm') or 0), dpi)

    # 偏移只改变固定纸张画布内的内容原点，绝不压缩单元格或改变版式比例。
    # 超出画布的部分按打印机实际裁切；纸张尺寸始终由 canvas 决定。
    off_x = mm_to_px(float(config.get('offset_x_mm') or 0), dpi)
    off_y = mm_to_px(float(config.get('offset_y_mm') or 0), dpi)

    cell_w = (sheet_w - margin * 2 - gap * (cols - 1)) // cols
    cell_h = (sheet_h - margin * 2 - gap * (rows - 1)) // rows

    per_sheet = cols * rows
    canvas = Image.new('L', (sheet_w, sheet_h), 255)

    for index, label in enumerate(labels):
        slot = index % per_sheet
        r, c = divmod(slot, cols)
        x = off_x + margin + c * (cell_w + gap)
        y = off_y + margin + r * (cell_h + gap)
        cell = render_cell(label, cell_w, cell_h, dpi)
        canvas.paste(cell, (x, y))

        # 满一页就出纸；或者这是最后一张也出纸
        if slot == per_sheet - 1 or index == len(labels) - 1:
            yield canvas
            canvas = Image.new('L', (sheet_w, sheet_h), 255)


def render_calibration_sheet(config: dict, dpi: int | None = None) -> Image.Image:
    """生成固定物理尺寸的 60×40 mm 打印校准页。

    外框用于确认整张纸尺寸，中线把纸分成四个 30×20 mm 单元。偏移值只
    平移图形，不改变画布像素数，因此尺量结果可以直接用于修正打印驱动。
    """
    dpi = int(dpi or config.get('dpi') or 600)
    width_mm = float(config.get('sheet_width_mm') or 60)
    height_mm = float(config.get('sheet_height_mm') or 40)
    sheet_w = mm_to_px(width_mm, dpi)
    sheet_h = mm_to_px(height_mm, dpi)
    off_x = mm_to_px(float(config.get('offset_x_mm') or 0), dpi)
    off_y = mm_to_px(float(config.get('offset_y_mm') or 0), dpi)
    line_width = max(1, mm_to_px(0.25, dpi))

    image = Image.new('L', (sheet_w, sheet_h), 255)
    draw = ImageDraw.Draw(image)
    left, top = off_x, off_y
    right, bottom = off_x + sheet_w - 1, off_y + sheet_h - 1
    draw.rectangle((left, top, right, bottom), outline=0, width=line_width)
    draw.line((off_x + sheet_w // 2, top, off_x + sheet_w // 2, bottom), fill=0, width=line_width)
    draw.line((left, off_y + sheet_h // 2, right, off_y + sheet_h // 2), fill=0, width=line_width)

    tick_length = mm_to_px(1.5, dpi)
    for mm in range(5, int(width_mm), 5):
        x = off_x + mm_to_px(mm, dpi)
        draw.line((x, top, x, top + tick_length), fill=0, width=max(1, line_width // 2))
    for mm in range(5, int(height_mm), 5):
        y = off_y + mm_to_px(mm, dpi)
        draw.line((left, y, left + tick_length, y), fill=0, width=max(1, line_width // 2))

    title_font = load_font(mm_to_px(1.8, dpi), bold=True)
    note_font = load_font(mm_to_px(1.15, dpi))
    pad = mm_to_px(2.0, dpi)
    draw.text((off_x + pad, off_y + pad), f'{width_mm:g} x {height_mm:g} mm', fill=0, font=title_font)
    draw.text(
        (off_x + pad, off_y + pad + mm_to_px(3.0, dpi)),
        f'DPI {dpi}  X {float(config.get("offset_x_mm") or 0):+.1f} mm  '
        f'Y {float(config.get("offset_y_mm") or 0):+.1f} mm',
        fill=0,
        font=note_font,
    )
    cell_text = f'{width_mm / 2:g} x {height_mm / 2:g} mm'
    for row in range(2):
        for column in range(2):
            x = off_x + column * (sheet_w // 2) + pad
            y = off_y + row * (sheet_h // 2) + sheet_h // 2 - mm_to_px(3.0, dpi)
            draw.text((x, y), cell_text, fill=0, font=note_font)
    return image


def build_test_labels(count: int) -> list[dict]:
    labels = []
    for index in range(max(1, count)):
        number = index + 1
        label = {
            'logo_text': 'BWT',
            'title': f'TEST {number}',
            'primary_code': 'AST-00000196' if number == 1 else f'SC-TEST-{number:04d}',
            'manufacture_sn': f'SN-{number:04d}',
            'qr_image': '',
        }
        try:
            import qrcode

            qr = qrcode.QRCode(border=2)
            qr.add_data('WMS2:A:AST-00000196' if number == 1 else f'TEST-LABEL-{number}')
            qr.make(fit=True)
            buffer = io.BytesIO()
            qr.make_image().save(buffer, format='PNG')
            label['qr_image'] = base64.b64encode(buffer.getvalue()).decode('ascii')
        except Exception:  # noqa: BLE001 - 没有 qrcode 也能测物理版式
            pass
        labels.append(label)
    return labels


# ---------------------------------------------------------------- 打印


def send_to_printer(image: Image.Image, printer_name: str = '', dpi: int = 600) -> None:
    """把位图按 1:1 物理尺寸送进 Windows 打印队列。

    这里**绝对不能**把图拉伸铺满 DC 的可打印区域：
    ZT610 的可打印宽度是 4 英寸（约 104mm），60×40mm 的图被拉到 104mm 宽后
    会横向放大 1.7 倍，四联标签随之整体错位、内容溢出到相邻单元格，
    表现出来就是"排版全乱、既不是 60×40 也不是 30×20"。

    正确做法是保持图片像素 → 设备像素 1:1，用 MM_TEXT 映射模式，
    让 Windows 按打印机自身分辨率还原真实物理尺寸。

    偏移已在 render_sheet 里画进位图内部（内容平移、画布固定为纸张尺寸），
    因此这里固定从 (0,0) 贴图。这样无论偏移多少，位图尺寸永远等于纸张，
    走纸长度固定，不会因位图底边超出纸张而多走纸（多吐纸）。
    """
    if win32print is None or win32ui is None:
        raise RuntimeError('未安装 pywin32，无法调用 Windows 打印机。请执行：pip install pywin32')

    if not printer_name:
        printer_name = win32print.GetDefaultPrinter()

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_name)
    try:
        hdc.StartDoc('资产标签')
        hdc.StartPage()
        # 位图尺寸本身就是按目标 DPI 算好的像素数，从原点 (0,0) 贴图。
        dib = ImageWin.Dib(image)
        dib.draw(hdc.GetHandleOutput(), (0, 0, image.width, image.height))
        hdc.EndPage()
        hdc.EndDoc()
    finally:
        hdc.DeleteDC()


def _zpl_hex(value: object) -> str:
    """把 UTF-8 文本编码为 ZPL ^FH 可接受的十六进制字段。"""
    return ''.join(f'_{byte:02X}' for byte in str(value or '').encode('utf-8'))


def render_zpl_sheet(labels: list[dict], config: dict, dpi: int | None = None) -> str:
    """生成一张固定 60×40 mm 的 ZPL 版面。

    ZPL 直接告诉 ZT610 纸张宽度和长度，避免 Windows GDI 根据驱动可打印区
    拉伸位图或把底部溢出内容当成额外走纸。每张物理标签按列/行切成固定格。
    """
    dpi = int(dpi or config.get('dpi') or 300)
    sheet_w = mm_to_px(float(config.get('sheet_width_mm') or 60), dpi)
    sheet_h = mm_to_px(float(config.get('sheet_height_mm') or 40), dpi)
    columns = max(1, int(config.get('columns') or 2))
    rows = max(1, int(config.get('rows') or 2))
    gap = mm_to_px(float(config.get('gap_mm') or 0), dpi)
    margin = mm_to_px(float(config.get('margin_mm') or 0), dpi)
    offset_x = mm_to_px(float(config.get('offset_x_mm') or 0), dpi)
    offset_y = mm_to_px(float(config.get('offset_y_mm') or 0), dpi)
    cell_w = (sheet_w - margin * 2 - gap * (columns - 1)) // columns
    cell_h = (sheet_h - margin * 2 - gap * (rows - 1)) // rows
    qr_mag = max(2, min(8, round(mm_to_px(6, dpi) / 30)))
    per_sheet = columns * rows

    commands = [
        '^XA', '^CI28', f'^PW{sheet_w}', f'^LL{sheet_h}',
        '^LH0,0', '^LT0', '^LS0', '^MNY',
    ]
    for index, label in enumerate(labels[:per_sheet]):
        slot = index % per_sheet
        row, column = divmod(slot, columns)
        x = offset_x + margin + column * (cell_w + gap)
        y = offset_y + margin + row * (cell_h + gap)
        pad = max(4, mm_to_px(0.7, dpi))
        text_w = max(mm_to_px(11, dpi), cell_w - pad * 2 - mm_to_px(8, dpi))
        line = max(mm_to_px(1.1, dpi), int(dpi * 0.95 / 25.4))
        value_line = max(mm_to_px(1.5, dpi), int(dpi * 1.25 / 25.4))
        qr_x = x + cell_w - pad - mm_to_px(6.5, dpi)
        qr_y = y + pad

        def text_field(px, py, value, height=line, width=text_w):
            commands.extend([
                f'^FO{px},{py}', f'^A0N,{height},{height}',
                f'^FB{max(width, 1)},1,0,L,0', '^FH', f'^FD{_zpl_hex(value)}^FS',
            ])

        text_field(x + pad, y + pad, label.get('logo_text') or '仓')
        text_field(x + pad, y + pad + line + 3, label.get('title') or '', value_line, text_w)
        text_field(x + pad, y + pad + (line + 3) * 2, f'资产编码：{label.get("primary_code") or ""}', value_line, text_w)
        serial = str(label.get('manufacture_sn') or '')
        if serial:
            text_field(x + pad, y + pad + (line + 3) * 3, f'序列号：{serial}', value_line, text_w)

        qr_payload = label.get('qr_payload') or label.get('primary_code') or ''
        commands.extend([
            f'^FO{qr_x},{qr_y}', f'^BQN,2,{qr_mag}', '^FH',
            f'^FDLA,{_zpl_hex(qr_payload)}^FS',
        ])
    commands.append('^XZ')
    return ''.join(commands)


def send_zpl_to_printer(zpl: str, printer_name: str = '') -> None:
    """通过 Windows RAW 队列发送 ZPL，不经过 GDI 页面布局。"""
    if win32print is None:
        raise RuntimeError('未安装 pywin32，无法发送 ZPL。请执行：pip install pywin32')
    printer_name = printer_name or win32print.GetDefaultPrinter()
    handle = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(handle, 1, ('仓库资产标签 ZPL', None, 'RAW'))
        try:
            win32print.StartPagePrinter(handle)
            try:
                win32print.WritePrinter(handle, zpl.encode('utf-8'))
            finally:
                win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


class LabelClient:
    def __init__(self, config: dict):
        self.config = config
        self.server = normalize_server(str(config.get('server') or ''))
        self.token = str(config.get('token') or '')
        self.printer = str(config.get('printer') or '')
        self.name = str(config.get('client_name') or 'windows-client')
        if not self.server:
            raise ValueError('服务器地址为空，请在配置里填写 IP 或域名。')

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f'{self.server}{path}'
        data = None
        method = 'GET'
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            method = 'POST'
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header('Authorization', f'Token {self.token}')
        request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'HTTP {exc.code}: {body}') from exc

    def run_once(self) -> bool:
        """取一条作业并打印。返回 False 表示当前没有待打印作业。"""
        result = self._request('/api/inventory/label-jobs/claim/', {'client_name': self.name})
        job = result.get('job')
        if not job:
            return False

        job_id = job['id']
        labels = (job.get('payload') or {}).get('labels') or []
        printer = job.get('printer_name') or self.printer
        LOG.info('领取作业 %s，共 %d 张', job_id, len(labels))

        try:
            # 每次打印前热加载配置文件中的偏移：界面上改了 XY 但没重启
            # 客户端时，下一张标签仍会用最新值，避免"调了没反应"。
            try:
                fresh = load_config()
                self.config['offset_x_mm'] = fresh.get('offset_x_mm', self.config.get('offset_x_mm'))
                self.config['offset_y_mm'] = fresh.get('offset_y_mm', self.config.get('offset_y_mm'))
            except Exception:  # noqa: BLE001 - 读配置失败沿用当前值
                pass
            # 用驱动报告的真实 DPI 渲染，避免配置值与设备分辨率不一致导致物理尺寸偏移
            actual_dpi = detect_printer_dpi(printer) or int(self.config.get('dpi') or 600)
            if self.config.get('print_channel', 'zpl') == 'zpl':
                per_sheet = max(1, int(self.config.get('columns') or 2) * int(self.config.get('rows') or 2))
                for start in range(0, len(labels), per_sheet):
                    send_zpl_to_printer(render_zpl_sheet(labels[start:start + per_sheet], self.config, actual_dpi), printer)
            else:
                for sheet in render_sheet(labels, self.config, actual_dpi):
                    send_to_printer(sheet, printer, actual_dpi)
            self._request(
                f'/api/inventory/label-jobs/{job_id}/finish/',
                {'success': True, 'claim_token': job['claim_token']},
            )
            LOG.info('作业 %s 打印完成', job_id)
        except Exception as exc:  # noqa: BLE001 - 打印失败必须上报而不是崩掉客户端
            LOG.exception('作业 %s 打印失败', job_id)
            try:
                self._request(
                    f'/api/inventory/label-jobs/{job_id}/finish/',
                    {'success': False, 'message': str(exc), 'claim_token': job['claim_token']},
                )
            except Exception:  # noqa: BLE001
                LOG.error('上报失败结果时出错，作业 %s 会停留在已领取状态', job_id)
        return True

    def loop(self, stop_event=None) -> None:
        interval = float(self.config.get('interval') or 5.0)
        LOG.info('打印客户端已启动：%s → %s', self.name, self.server)
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                while self.run_once():
                    pass
            except Exception:  # noqa: BLE001 - 网络抖动不应让客户端退出
                LOG.exception('轮询出错，%.0f 秒后重试', interval)
            for _ in range(int(max(interval, 1))):
                if stop_event is not None and stop_event.is_set():
                    return
                time.sleep(1)


# ---------------------------------------------------------------- 图形界面


def run_gui() -> int:
    import threading
    import tkinter as tk
    from tkinter import messagebox, ttk

    config = load_config()
    worker: dict[str, object] = {'thread': None, 'stop_event': None, 'handler': None}

    root = tk.Tk()
    root.title('八维通仓库 · 标签打印客户端')
    root.geometry('640x520')
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=14)
    frame.pack(fill='both', expand=True)

    vars_: dict[str, tk.StringVar] = {
        'server': tk.StringVar(value=config['server']),
        'token': tk.StringVar(value=config['token']),
        'printer': tk.StringVar(value=config['printer']),
        'dpi': tk.StringVar(value=str(config['dpi'])),
        'layout': tk.StringVar(value=f"{config['columns']}x{config['rows']}"),
        'offset_x': tk.StringVar(value=str(config.get('offset_x_mm') or 0)),
        'offset_y': tk.StringVar(value=str(config.get('offset_y_mm') or 0)),
    }

    def row(index: int, text: str, widget) -> None:
        ttk.Label(frame, text=text).grid(row=index, column=0, sticky='w', pady=5)
        widget.grid(row=index, column=1, sticky='ew', pady=5)

    frame.columnconfigure(1, weight=1)

    # 服务器地址：IP 或域名都可以，可不带 http://
    server_entry = ttk.Entry(frame, textvariable=vars_['server'])
    row(0, '服务器地址', server_entry)
    ttk.Label(
        frame,
        text='填 IP 或域名均可，例如 192.168.123.101 / wms.bwt.local；'
             '带 http:// 或不带都行',
        foreground='#777',
        wraplength=380,
    ).grid(row=1, column=1, sticky='w', pady=(0, 4))

    token_entry = ttk.Entry(frame, textvariable=vars_['token'], show='*')
    row(2, 'API Token', token_entry)

    printers = list_printers()
    printer_box = ttk.Combobox(frame, textvariable=vars_['printer'], values=printers)
    row(3, '打印机', printer_box)

    dpi_box = ttk.Combobox(
        frame, textvariable=vars_['dpi'], values=['203', '300', '600', '1200'], width=10
    )
    row(4, 'DPI', dpi_box)

    layout_box = ttk.Combobox(
        frame, textvariable=vars_['layout'], values=['2x2', '1x1'], width=10
    )
    row(5, '版式（联数）', layout_box)

    # 对位微调：打印机机械误差各不相同，让用户自己拧，不用每次改代码重打包
    fine = ttk.Frame(frame)
    fine.grid(row=6, column=1, sticky='w', pady=2)
    ttk.Label(fine, text='左移←').pack(side='left')
    ttk.Entry(fine, textvariable=vars_['offset_x'], width=6).pack(side='left', padx=2)
    ttk.Label(fine, text='mm  上移↑').pack(side='left', padx=(8, 0))
    ttk.Entry(fine, textvariable=vars_['offset_y'], width=6).pack(side='left', padx=2)
    ttk.Label(fine, text='mm').pack(side='left')
    ttk.Label(frame, text='对位微调', foreground='#555').grid(row=6, column=0, sticky='w')
    ttk.Label(
        frame,
        text='打出来整体偏右就把 X 填负数，偏下就把 Y 填负数；0.5 一档试',
        foreground='#777',
        wraplength=380,
    ).grid(row=7, column=1, sticky='w', pady=(0, 4))

    status = ttk.Label(frame, text='未启动', foreground='#666')
    status.grid(row=8, column=0, columnspan=2, sticky='w', pady=(10, 4))

    log_box = tk.Text(frame, height=7, width=64, state='disabled')
    log_box.grid(row=9, column=0, columnspan=2, sticky='ew')

    def append_log(text: str) -> None:
        log_box.configure(state='normal')
        log_box.insert('end', text + '\n')
        log_box.see('end')
        log_box.configure(state='disabled')

    def collect() -> dict:
        cols, _, rows = vars_['layout'].get().partition('x')

        def as_float(key: str) -> float:
            try:
                return float(vars_[key].get().strip() or 0)
            except (TypeError, ValueError):
                return 0.0

        return {
            **config,
            'server': normalize_server(vars_['server'].get()),
            'token': vars_['token'].get().strip(),
            'printer': vars_['printer'].get().strip(),
            'dpi': int(vars_['dpi'].get() or 600),
            'columns': int(cols or 2),
            'rows': int(rows or 2),
            'offset_x_mm': as_float('offset_x'),
            'offset_y_mm': as_float('offset_y'),
        }

    def test_connection() -> None:
        base = normalize_server(vars_['server'].get())
        if not base:
            messagebox.showwarning('缺少配置', '请先填写服务器地址。')
            return
        append_log(f'正在测试 {base}/health/ ...')
        ok, message = probe_server(base)
        append_log(('[OK] ' if ok else '[FAIL] ') + message)
        if not ok:
            messagebox.showerror('连接失败', message)

    def start() -> None:
        # 运行中再次点击 = 应用新配置并重启。旧逻辑是直接 return，
        # 改了 XY 后配置既不保存也不生效——这就是"调了没反应"的根源之一。
        old_thread = worker.get('thread')
        old_event = worker.get('stop_event')
        if old_thread and old_thread.is_alive():
            append_log('检测到正在运行，应用新配置并重启…')
            old_event.set()
            # HTTP 请求最长 30 秒；必须确认旧线程退出后才能启动新线程。
            old_thread.join(timeout=35)
            if old_thread.is_alive():
                append_log('旧打印线程未能在 35 秒内退出，已取消重启，避免重复领取作业。')
                messagebox.showwarning('无法重启', '旧打印线程仍在等待服务器响应，请稍后再试。')
                return
        old_handler = worker.get('handler')
        if old_handler:
            LOG.removeHandler(old_handler)
        cfg = collect()
        if not cfg['server'] or not cfg['token']:
            messagebox.showwarning('缺少配置', '请填写服务器地址和 API Token。')
            return
        save_config(cfg)
        stop_event = threading.Event()
        client = LabelClient(cfg)

        def target() -> None:
            append_log(
                f'启动：{server_display_name(cfg["server"])}  DPI={cfg["dpi"]}  '
                f'{cfg["columns"]}x{cfg["rows"]}'
            )
            client.loop(stop_event)

        # 把日志接到界面
        class UiHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    root.after(0, append_log, self.format(record))
                except Exception:  # noqa: BLE001
                    pass

        handler = UiHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s  %(message)s', '%H:%M:%S'))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)

        thread = threading.Thread(target=target, daemon=True)
        worker['thread'] = thread
        worker['stop_event'] = stop_event
        worker['handler'] = handler
        thread.start()
        status.configure(text='运行中 ●', foreground='#0a7d28')

    def stop() -> None:
        active_event = worker.get('stop_event')
        if active_event:
            active_event.set()
        status.configure(text='已停止', foreground='#999')
        append_log('已请求停止。')

    def test_print() -> None:
        cfg = collect()
        try:
            actual_dpi = detect_printer_dpi(cfg['printer']) or int(cfg.get('dpi') or 600)
            label_count = max(1, int(cfg['columns']) * int(cfg['rows']))
            labels = build_test_labels(label_count)
            if cfg.get('print_channel', 'zpl') == 'zpl':
                send_zpl_to_printer(render_zpl_sheet(labels, cfg, actual_dpi), cfg['printer'])
            else:
                for sheet in render_sheet(labels, cfg, actual_dpi):
                    send_to_printer(sheet, cfg['printer'], actual_dpi)
            append_log(f'四格测试页已发送到打印机（DPI={actual_dpi}，版式 {cfg["columns"]}x{cfg["rows"]}）。')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror('测试打印失败', str(exc))

    def print_calibration() -> None:
        cfg = collect()
        try:
            actual_dpi = detect_printer_dpi(cfg['printer']) or int(cfg.get('dpi') or 600)
            # 校准页已把偏移画进图形用于尺量，贴图原点固定 0 避免二次平移。
            image = render_calibration_sheet(cfg, actual_dpi)
            send_to_printer(image, cfg['printer'], actual_dpi)
            append_log(
                f'60x40 mm 校准页已发送（DPI={actual_dpi}，'
                f'X={cfg["offset_x_mm"]:+.1f} mm，Y={cfg["offset_y_mm"]:+.1f} mm）。'
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror('校准页打印失败', str(exc))

    def preview_labels() -> None:
        """弹出预览窗口，按当前配置渲染一页标签（含偏移）供肉眼核对。

        render_sheet 已把偏移画进位图，预览与打印效果一致；可用于在
        不消耗纸张的情况下确认 XY 微调方向。
        """
        cfg = collect()
        try:
            from PIL import ImageTk

            actual_dpi = detect_printer_dpi(cfg['printer']) or int(cfg.get('dpi') or 600)
            label_count = max(1, int(cfg['columns']) * int(cfg['rows']))
            # render_sheet 已把偏移画进位图，直接显示即可，与打印效果一致。
            shifted = next(iter(render_sheet(build_test_labels(label_count), cfg, actual_dpi)))
            sheet = shifted

            preview = tk.Toplevel(root)
            preview.title('标签预览（含当前偏移）')
            preview.configure(bg='#dddddd')
            info = ttk.Label(
                preview,
                text=(
                    f'DPI={actual_dpi}  版式 {cfg["columns"]}x{cfg["rows"]}  '
                    f'X={float(cfg.get("offset_x_mm") or 0):+.1f} mm  '
                    f'Y={float(cfg.get("offset_y_mm") or 0):+.1f} mm'
                ),
            )
            info.pack(padx=10, pady=(10, 4))

            # 屏幕显示缩放到合适大小，保持宽高比。
            max_w, max_h = 640, 460
            scale = min(max_w / sheet.width, max_h / sheet.height, 1.0)
            disp = shifted if scale >= 1.0 else shifted.resize(
                (max(1, int(sheet.width * scale)), max(1, int(sheet.height * scale))),
                Image.LANCZOS,
            )
            photo = ImageTk.PhotoImage(disp)
            canvas_lbl = tk.Label(preview, image=photo, bd=1, relief='solid')
            canvas_lbl.image = photo  # 防 GC
            canvas_lbl.pack(padx=10, pady=10)
            ttk.Button(preview, text='关闭', command=preview.destroy).pack(pady=(0, 10))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror('预览失败', str(exc))

    buttons = ttk.Frame(frame)
    buttons.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(10, 0))
    ttk.Button(buttons, text='测试连接', command=test_connection).pack(side='left')
    ttk.Button(buttons, text='保存并启动', command=start).pack(side='left', padx=6)
    ttk.Button(buttons, text='停止', command=stop).pack(side='left')
    ttk.Button(buttons, text='预览标签', command=preview_labels).pack(side='left', padx=(6, 0))
    ttk.Button(buttons, text='打印校准页', command=print_calibration).pack(side='left', padx=6)
    ttk.Button(buttons, text='四格测试打印', command=test_print).pack(side='left', padx=6)

    def on_close() -> None:
        active_event = worker.get('stop_event')
        active_thread = worker.get('thread')
        if active_event:
            active_event.set()
        if active_thread and active_thread.is_alive():
            active_thread.join(timeout=35)
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    root.mainloop()
    return 0


# ---------------------------------------------------------------- 入口


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8，避免中文 Windows 的 GBK 编码崩溃。

    打包成 exe 后 stdout 默认跟随系统代码页（cp936/GBK），
    任何 GBK 表示不了的字符都会抛 UnicodeEncodeError 让程序直接退出。
    这里优先用 reconfigure，老版本解释器退回包装一层。
    """
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            try:
                import io as _io

                setattr(
                    sys, stream_name,
                    _io.TextIOWrapper(stream.buffer, encoding='utf-8', errors='replace'),
                )
            except Exception:  # noqa: BLE001 - 兜底失败也不能影响主流程
                pass


def main() -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description='八维通仓库资产标签打印客户端')
    parser.add_argument('--gui', action='store_true', help='打开图形界面')
    parser.add_argument('--server', help='服务器地址，IP 或域名均可，例如 192.168.123.101 或 wms.bwt.local')
    parser.add_argument('--token', help='API Token')
    parser.add_argument('--printer', help='打印机名称，留空用系统默认')
    parser.add_argument('--dpi', type=int, help='打印机 DPI，默认 600')
    parser.add_argument('--interval', type=float, help='轮询间隔秒数')
    parser.add_argument('--once', action='store_true', help='只跑一轮后退出')
    parser.add_argument('--check', action='store_true', help='只检查服务器连通性后退出')
    parser.add_argument('--detect-dpi', action='store_true', help='检测打印机 DPI 后退出')
    parser.add_argument('--list-printers', action='store_true', help='列出打印机后退出')
    args = parser.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        handlers.append(
            logging.FileHandler(os.path.join(CONFIG_DIR, 'label_client.log'), encoding='utf-8')
        )
    except OSError:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=handlers,
    )

    if args.list_printers:
        printers = list_printers()
        if not printers:
            safe_print('未找到打印机（可能未安装 pywin32 或系统无打印机）。')
            return 1
        for name in printers:
            safe_print(name)
        return 0

    if args.detect_dpi:
        safe_print(f'检测到 DPI: {detect_printer_dpi(args.printer or "")}')
        return 0

    if args.gui or len(sys.argv) == 1:
        return run_gui()

    config = load_config()
    if args.server:
        config['server'] = args.server

    if args.check:
        base = normalize_server(str(config.get('server') or ''))
        safe_print(f'目标地址: {base or "(空)"}')
        ok, message = probe_server(base)
        safe_print(('[OK] ' if ok else '[FAIL] ') + message)
        return 0 if ok else 1

    if args.token:
        config['token'] = args.token
    if args.printer is not None:
        config['printer'] = args.printer
    if args.dpi:
        config['dpi'] = args.dpi
    if args.interval:
        config['interval'] = args.interval

    client = LabelClient(config)
    if args.once:
        printed = client.run_once()
        LOG.info('单次运行结束，%s', '已打印 1 条作业' if printed else '没有待打印作业')
        return 0
    client.loop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - 任何未捕获异常都要看得懂，不能弹 traceback 乱码
        import traceback

        detail = traceback.format_exc()
        try:
            safe_print('程序异常退出：')
            safe_print(detail)
        except Exception:  # noqa: BLE001
            pass
        # 打包成 --windowed 时没有控制台，用弹窗兜底
        try:
            import tkinter.messagebox as _mb

            _mb.showerror('打印客户端异常', detail[-1500:])
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
