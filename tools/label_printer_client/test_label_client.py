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


if __name__ == '__main__':
    unittest.main()
