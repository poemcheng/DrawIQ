import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from workbench.nailok_label_image import DPI, WIDTH, field_text, render_label, write_label_png


class LabelImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_price_keeps_printed_units_and_quantity_avoids_duplicates(self):
        self.assertEqual(field_text('quotation.price', '12.50', 'TWD/M'), ('12.50 / TWD / M', False))
        self.assertEqual(field_text('quotation.price', '12.50 / TWD / M', 'TWD/M'), ('12.50 / TWD / M', False))
        self.assertEqual(field_text('quotation.price', '', 'TWD/M'), ('/ TWD / M', True))
        self.assertEqual(field_text('quotation.quantity', '160', 'M'), ('160 M', False))
        self.assertEqual(field_text('quotation.quantity', '160 M', 'M'), ('160 M', False))
        self.assertEqual(field_text('quotation.quantity', '', ''), ('', True))

    def test_renders_canonical_order_and_expands_for_wrapped_notes(self):
        rows = [
            {'key': 'quotation.number', 'value': 'DEMO-1'},
            {'key': 'quotation.quantity', 'value': 250, 'unit': 'M'},
            {'key': 'quotation.price', 'value': '', 'unit': 'TWD/M'},
        ]
        image = render_label(rows)
        self.assertEqual(image, render_label(reversed(rows)))
        self.assertNotEqual(render_label([]), render_label([{'key': 'quotation.price', 'value': 0}]))
        extended = render_label([*rows, {'key': 'quotation.notes', 'value': '注意項目\n' + '尺寸±0.15公差' * 86}])
        self.assertEqual(image.width(), WIDTH)
        self.assertEqual(extended.width(), image.width())
        self.assertGreater(extended.height(), image.height() + 300)
        self.assertFalse(extended.hasAlphaChannel())

    def test_png_roundtrip_preserves_dpi_and_all_print_margins(self):
        image = render_label([{'key': 'quotation.notes', 'value': '完整註記ABC123' * 130}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '合成報價標籤.png'
            self.assertEqual(write_label_png(image, path), path)
            self.assertEqual(QImage(path.as_posix()).size(), image.size())
            with Image.open(path) as written:
                self.assertEqual(written.format, 'PNG')
                self.assertEqual(written.mode, 'RGB')
                self.assertAlmostEqual(written.info['dpi'][0], DPI, places=1)
                output = written.convert('L')
                ink = ImageChops.difference(output, Image.new('L', written.size, 255))
                left, top, right, bottom = ink.getbbox()
                self.assertGreater(left, 50)
                self.assertGreater(top, 50)
                self.assertGreater(output.width - right, 50)
                self.assertGreater(output.height - bottom, 50)

    def test_rejects_excessive_fields_before_image_allocation(self):
        with self.assertRaisesRegex(ValueError, '注意事項.*過長'):
            render_label([{'key': 'quotation.notes', 'value': '測' * 4200}])
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                write_label_png(render_label([]), Path(folder) / 'missing-directory' / '標籤.png')
