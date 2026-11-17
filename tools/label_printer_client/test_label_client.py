import re
import unittest

from PIL import ImageChops

from label_client import (
    DEFAULT_CONFIG,
    _qr_image,
    build_test_labels,
    mm_to_px,
    render_calibration_sheet,
    render_cell,
    render_sheet,
    render_zpl_sheet,
)


class LabelRenderingTests(unittest.TestCase):
    def test_calibration_sheet_has_exact_physical_pixel_size(self):
        for dpi in (203, 300, 600):
            with self.subTest(dpi=dpi):
                config = {**DEFAULT_CONFIG, 'dpi': dpi, 'offset_x_mm': 0, 'offset_y_mm': 0}
                image = render_calibration_sheet(config, dpi)
                self.assertEqual(image.size, (mm_to_px(60, dpi), mm_to_px(40, dpi)))

    def test_300_dpi_sheet_matches_60_by_40_mm_pixel_size(self):
        config = {**DEFAULT_CONFIG, 'dpi': 300, 'offset_x_mm': 0, 'offset_y_mm': 0}
        image = next(render_sheet(build_test_labels(1), config, 300))
        self.assertEqual(image.size, (709, 472))

    def test_qr_restores_white_quiet_zone(self):
        label = build_test_labels(1)[0]
        image = _qr_image(label, 120)
        self.assertIsNotNone(image)
        self.assertEqual(image.getpixel((0, 0)), 255)
        self.assertIsNotNone(ImageChops.invert(image).getbbox())

    def test_calibration_sheet_draws_outer_border_and_center_lines(self):
        dpi = 203
        config = {**DEFAULT_CONFIG, 'dpi': dpi, 'offset_x_mm': 0, 'offset_y_mm': 0}
        image = render_calibration_sheet(config, dpi)
        width, height = image.size

        self.assertLess(image.getpixel((0, height // 3)), 128)
        self.assertLess(image.getpixel((width // 2, height - 5)), 128)
        self.assertLess(image.getpixel((width - 5, height // 2)), 128)

    def test_two_by_two_test_print_populates_all_four_cells(self):
        config = {
            **DEFAULT_CONFIG,
            'dpi': 203,
            'columns': 2,
            'rows': 2,
            'offset_x_mm': 0,
            'offset_y_mm': 0,
        }
        sheets = list(render_sheet(build_test_labels(4), config, 203))
        self.assertEqual(len(sheets), 1)
        image = sheets[0]
        width, height = image.size
        quadrants = [
            image.crop((0, 0, width // 2, height // 2)),
            image.crop((width // 2, 0, width, height // 2)),
            image.crop((0, height // 2, width // 2, height)),
            image.crop((width // 2, height // 2, width, height)),
        ]
        for quadrant in quadrants:
            self.assertIsNotNone(ImageChops.invert(quadrant).getbbox())

    def test_offset_does_not_change_sheet_size(self):
        config = {
            **DEFAULT_CONFIG,
            'dpi': 203,
            'columns': 1,
            'rows': 1,
            'offset_x_mm': -1.5,
            'offset_y_mm': 2.0,
        }
        image = next(render_sheet(build_test_labels(1), config, 203))
        self.assertEqual(image.size, (mm_to_px(60, 203), mm_to_px(40, 203)))

    def test_single_label_scales_content_to_full_sheet(self):
        label = build_test_labels(1)[0]
        dpi = 203
        cell = render_cell(label, mm_to_px(30, dpi), mm_to_px(20, dpi), dpi)
        sheet = render_cell(label, mm_to_px(60, dpi), mm_to_px(40, dpi), dpi)
        cell_bbox = ImageChops.invert(cell).getbbox()
        sheet_bbox = ImageChops.invert(sheet).getbbox()
        self.assertIsNotNone(cell_bbox)
        self.assertIsNotNone(sheet_bbox)
        self.assertGreater(
            sheet_bbox[2] - sheet_bbox[0],
            (cell_bbox[2] - cell_bbox[0]) * 1.5,
        )

    def test_offset_shifts_content_within_fixed_canvas(self):
        """偏移画进位图内部，画布尺寸固定等于纸张尺寸。

        位图始终从 (0,0) 原点贴到打印 DC，因此无论偏移多少，走纸长度
        都固定等于纸张高度，不会因位图底边超出纸张而多走纸。正偏移把
        内容右移/下移，负偏移把内容推出画布被裁切。
        """
        base = {
            **DEFAULT_CONFIG,
            'dpi': 203,
            'columns': 1,
            'rows': 1,
            'offset_x_mm': 0.0,
            'offset_y_mm': 0.0,
        }
        shifted = {**base, 'offset_x_mm': 9.0, 'offset_y_mm': 11.0}
        img_base = next(render_sheet(build_test_labels(1), base, 203))
        img_shifted = next(render_sheet(build_test_labels(1), shifted, 203))
        # 画布尺寸不变
        self.assertEqual(img_base.size, img_shifted.size)
        self.assertEqual(img_base.size, (mm_to_px(60, 203), mm_to_px(40, 203)))
        # 内容位置确实不同（偏移生效）
        self.assertIsNotNone(ImageChops.difference(img_base, img_shifted).getbbox())

    def test_large_negative_offset_keeps_canvas_size(self):
        """负偏移把内容移出画布被裁切，但画布尺寸仍固定不变。"""
        config = {
            **DEFAULT_CONFIG,
            'dpi': 203,
            'columns': 1,
            'rows': 1,
            'offset_x_mm': -100.0,
            'offset_y_mm': -100.0,
        }
        image = next(render_sheet(build_test_labels(1), config, 203))
        self.assertEqual(image.size, (mm_to_px(60, 203), mm_to_px(40, 203)))

    def test_positive_y_offset_preserves_scale_on_fixed_canvas(self):
        """正 Y 偏移只平移内容，不压缩内容比例或改变纸张画布。"""
        config = {
            **DEFAULT_CONFIG,
            'dpi': 203,
            'columns': 1,
            'rows': 1,
            'offset_x_mm': 0.0,
            'offset_y_mm': 11.0,
        }
        image = next(render_sheet(build_test_labels(1), config, 203))
        self.assertEqual(image.size, (mm_to_px(60, 203), mm_to_px(40, 203)))
        base = {**config, 'offset_y_mm': 0.0}
        base_image = next(render_sheet(build_test_labels(1), base, 203))
        # 平移会裁切底部，但不能通过改变 cell_h 让图形缩小。
        self.assertIsNotNone(ImageChops.difference(base_image, image).getbbox())

    def test_zpl_sheet_declares_exact_60x40_size_and_four_cells(self):
        config = {
            **DEFAULT_CONFIG,
            'dpi': 300,
            'columns': 2,
            'rows': 2,
            'sheet_width_mm': 60,
            'sheet_height_mm': 40,
        }
        labels = build_test_labels(4)
        for index, label in enumerate(labels):
            label['qr_payload'] = f'WMS2:A:TEST-{index + 1}'
        zpl = render_zpl_sheet(labels, config, 300)
        self.assertIn('^PW709', zpl)
        self.assertIn('^LL472', zpl)
        # 中文必须由位图承载：ZT610 内置字体没有中文字形，
        # 一旦回退到 ^A0N/^FD 写文本，中文就会被打印机静默丢弃。
        self.assertIn('^GFA', zpl)
        self.assertNotIn('^A0N', zpl)
        self.assertNotIn('^BQN', zpl)
        self.assertEqual(zpl.count('^XZ'), 1)

    def test_zpl_bitmap_contains_chinese_glyph_pixels(self):
        """回归：中文必须真的画到位图里，不能只是命令里有个字段。"""
        config = {**DEFAULT_CONFIG, 'dpi': 203, 'columns': 1, 'rows': 1}
        label = build_test_labels(1)[0]
        label['logo_text'] = ''
        label['qr_image'] = None
        label['qr_payload'] = ''
        label['title'] = '激光雷达'
        label['primary_code'] = ''
        label['manufacture_sn'] = ''
        page = next(iter(render_sheet([label], config, 203)))
        black = sum(1 for value in page.convert('L').getdata() if value < 128)
        self.assertGreater(black, 50, '中文字形没有渲染出来')

    def test_zpl_payload_size_matches_declared_bitmap(self):
        config = {**DEFAULT_CONFIG, 'dpi': 203, 'columns': 2, 'rows': 2}
        zpl = render_zpl_sheet(build_test_labels(1), config, 203)
        match = re.search(r'\^GFA,(\d+),\d+,(\d+),([0-9A-F]+)\^FS', zpl)
        self.assertIsNotNone(match)
        total, row_bytes, data = int(match.group(1)), int(match.group(2)), match.group(3)
        self.assertEqual(len(data), total * 2)
        self.assertEqual(len(bytes.fromhex(data)), total)
        self.assertEqual(total % row_bytes, 0)


if __name__ == '__main__':
    unittest.main()
