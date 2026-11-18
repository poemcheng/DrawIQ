import unittest
from unittest.mock import patch

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from tests.test_nailok_flow import Fixture


class SourceCanvasTests(Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from workbench.ui import MainWindow

        def add_layout(doc):
            doc['pages'].append(dict(doc['pages'][0], number=2))
            large, small, remote, _ = doc['regions']
            large.update(box=[.10, .10, .82, .82], source='MinerU Studio')
            small.update(box=[.22, .23, .27, .26], source='RapidOCR')
            remote.update(page=2, box=[.61, .66, .70, .70], source='MinerU Studio')
            doc['layout_regions'] = [{
                'id': 'layout_synthetic', 'page': 1, 'box': [.04, .04, .90, .90],
                'source': 'MinerU Studio', 'layout_type': 'image_body',
            }]
            return {}
        self.doc = self.store.mutate(self.doc['id'], self.doc['version'], 'fixture_layout',
                                     'isolated layout sample', add_layout)
        self.window = MainWindow(self.store.root)
        self.window.load_documents(self.doc['id'])
        self.window.show()
        self.page = self.window.simple_page
        self.canvas = self.page.canvas
        self.canvas.fit()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)

    def test_layout_and_large_selected_box_leave_small_evidence_clickable(self):
        large, small, _, _ = self.doc['regions']
        self.page.locate_source(large['id'])
        point = QPointF(.245 * self.canvas.width_px, .245 * self.canvas.height_px)
        marker = self.canvas.itemAt(self.canvas.mapFromScene(point))
        self.assertEqual(marker.region['id'], small['id'])
        QTest.mouseClick(self.canvas.viewport(), Qt.MouseButton.LeftButton, pos=self.canvas.mapFromScene(point))
        self.app.processEvents()
        self.assertEqual(self.page.source_id, small['id'])
        self.assertEqual(self.page.results.sources.currentData(), small['id'])

        self.page.focus_source_result('layout_synthetic')
        layout = self.canvas.region_items['layout_synthetic']
        self.assertEqual(layout.pen().style(), Qt.PenStyle.DashLine)
        self.assertFalse(self.page.edit_box.isEnabled())
        self.assertFalse(self.page.use_box.isEnabled())
        self.assertIn('版面', self.page.source_text.toPlainText())
        self.assertNotIn('layout_synthetic', self.page.regions)
        self.assertTrue(all('layout_synthetic' not in row['evidence_ids'] for row in self.page.rows))

    def test_right_result_locates_and_zooms_without_switching_original_mode_or_draft(self):
        _, _, remote, _ = self.doc['regions']
        self.page.value.setPlainText('本次手動測試')
        field = self.page.field
        draft = self.page.edits[field].copy()
        self.page.overlays.setChecked(False)
        self.canvas.fit()
        scale = self.canvas.transform().m11()
        row = self.page.results.source_items[remote['id']][0]
        self.page.results.tree.setCurrentItem(row)
        self.assertEqual(self.page.page_select.currentData(), 2)
        self.assertEqual(self.page.source_id, remote['id'])
        self.assertFalse(self.page.overlays.isChecked())
        self.assertFalse(self.canvas.region_items)
        self.assertGreater(self.canvas.transform().m11(), scale)
        self.assertEqual(self.page.field, field)
        self.assertEqual(self.page.edits[field], draft)
        self.canvas.fit()
        self.assertAlmostEqual(self.canvas.transform().m11(), scale, places=1)

    def test_right_filter_limits_overlays_and_reread_options_pass_through(self):
        large, small, _, _ = self.doc['regions']
        filters = self.page.results.method_filter
        filters.setCurrentIndex(filters.findData('RapidOCR'))
        self.assertIn(small['id'], self.canvas.region_items)
        self.assertNotIn(large['id'], self.canvas.region_items)
        self.assertNotIn('layout_synthetic', self.canvas.region_items)
        filters.setCurrentIndex(0)
        self.assertIn('layout_synthetic', self.canvas.region_items)

        self.page.page_select.setCurrentIndex(self.page.page_select.findData(2))
        with patch.object(self.window, 'quick_analyze') as worker_entry:
            self.page.reread()
            worker_entry.assert_called_once_with(current_page=None, force_ocr=False)
            worker_entry.reset_mock()
            self.page.reread_scope.setCurrentIndex(self.page.reread_scope.findData('current'))
            self.page.force_ocr.setChecked(True)
            self.page.reread()
            worker_entry.assert_called_once_with(current_page=2, force_ocr=True)
