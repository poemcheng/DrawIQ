"""Regression coverage for source navigation and multiple method outputs."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from workbench.domain import evidence, requirement
from workbench.nailok_methods_ui import MethodResultsPanel, method_results


def drawing():
    first = evidence(1, [.1, .1, .3, .2], 'M8×1.25', 'PDF文字層')
    first['methods'] = ['PDF文字層', 'RapidOCR']
    second = evidence(2, [.4, .4, .7, .5], '35 mm', 'RapidOCR')
    unknown = evidence(2, [.5, .6, .7, .7], '備註原文', '')
    model_label = requirement('product.specification', 'M8×1.25 × 35', label='規格',
                              evidence_ids=[first['id'], second['id']],
                              candidates=[{'source': 'VLM:local:model', 'value': 'M8×1.25 × 35'}])
    model_unlocated = requirement('observation.model', '未知規格', label='來源待補',
                                  evidence_ids=[first['id']],
                                  candidates=[{'source': 'VLM:other', 'value': '未知規格',
                                               'unlocated_output': {'value': '未知規格'}}])
    return {'pages': [{'number': 1, 'recognition_status': 'complete', 'ocr_text': 'M8×1.25'},
                      {'number': 2, 'recognition_status': 'partial_timeout'}],
            'regions': [first, second, unknown], 'requirements': [model_label, model_unlocated],
            'tool_runs': [{'page': 1, 'ocr_engine': 'PDF文字層 + RapidOCR', 'status': 'complete',
                           'model': 'local:model', 'actual_model': 'actual-model', 'vlm_status': 'incomplete'},
                          {'page': 2, 'type': 'page_budget', 'status': 'partial_timeout'},
                          {'page': 2, 'model': 'other', 'status': 'failed', 'vlm_status': 'failed'}]}


class MethodResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preserves_multiple_method_sources_and_unassigned_evidence(self):
        doc = drawing()
        rows = method_results(doc)
        first_id, _, unknown_id = [row['id'] for row in doc['regions']]
        self.assertEqual({row.method for row in rows if row.source_ids == (first_id,)},
                         {'PDF 文字層', 'RapidOCR'})
        self.assertEqual({row.method for row in rows if row.source_ids == (unknown_id,)}, {'來源未分類'})
        self.assertTrue(any(row.method == '頁面全文' and row.text == 'M8×1.25' for row in rows))

    def test_keeps_each_method_status_and_unlocated_model_result(self):
        rows = method_results(drawing())
        self.assertTrue(any(row.method == 'RapidOCR' and row.text == '完成' for row in rows))
        self.assertTrue(any(row.method == 'VLM · local:model' and '部分完成' in row.text
                            and 'actual-model' in row.text for row in rows))
        self.assertTrue(any(row.method == 'VLM · other' and row.text == '失敗' for row in rows))
        self.assertTrue(any(row.method == '頁面辨識' and row.page == 2 and '逾時' in row.text for row in rows))
        model = next(row for row in rows if row.label == '來源待補')
        self.assertEqual(model.text, '未知規格')
        self.assertEqual(model.source_ids, ())

    def test_browse_each_source_of_a_multi_page_result(self):
        doc = drawing()
        panel = MethodResultsPanel()
        selected = []
        panel.source_selected.connect(selected.append)
        panel.refresh(doc)
        panel.method_filter.setCurrentIndex(panel.method_filter.findData('VLM · local:model'))
        group = panel.tree.topLevelItem(0)
        self.assertEqual(panel.tree.topLevelItemCount(), 1)
        value = next(group.child(i) for i in range(group.childCount())
                     if group.child(i).data(0, Qt.ItemDataRole.UserRole).label == '規格')
        panel.tree.setCurrentItem(value)
        self.assertEqual(panel.sources.count(), 2)
        panel.sources.setCurrentIndex(1)
        self.assertEqual(selected, [doc['regions'][0]['id'], doc['regions'][1]['id']])
        panel.refresh(None)
        self.assertEqual(panel.tree.topLevelItemCount(), 0)
        self.assertEqual(panel.sources.count(), 0)
        panel.close()

    def test_canvas_selection_reveals_exact_source_without_emitting_a_focus_request(self):
        doc = drawing()
        first, second, _ = doc['regions']
        panel = MethodResultsPanel()
        self.addCleanup(panel.close)
        panel.refresh(doc)
        focus_requests = []
        filters = []
        panel.source_selected.connect(focus_requests.append)
        panel.source_filter_changed.connect(filters.append)

        self.assertTrue(panel.select_source(first['id']))
        self.assertEqual(panel.tree.currentItem().data(0, Qt.ItemDataRole.UserRole).method, 'PDF 文字層')
        self.assertEqual(filters, [])
        panel.method_filter.setCurrentIndex(panel.method_filter.findData('PDF 文字層'))
        panel.set_page(1)
        panel.tabs.setCurrentWidget(panel.json_view)
        filters.clear()

        self.assertTrue(panel.select_source(second['id']))
        self.assertIs(panel.tabs.currentWidget(), panel.tree.parentWidget())
        self.assertEqual(panel.method_filter.currentData(), 'RapidOCR')
        self.assertEqual(panel.page_filter.currentData(), 2)
        self.assertEqual(panel.sources.currentData(), second['id'])
        self.assertEqual(filters, [{second['id']}])
        self.assertEqual(focus_requests, [])
        self.assertFalse(panel.select_source('missing'))
        panel.refresh(None)
        self.assertIsNone(filters[-1])

    def test_layout_and_evidence_export_the_same_filtered_snapshot_as_the_result_tabs(self):
        doc = drawing()
        doc.update(id='test-doc', name='Fixture.pdf', version=7, source_sha256='a' * 64)
        region = evidence(2, [.15, .2, .5, .6], 'M6×1', 'MinerU Basic')
        region['layout_type'] = 'text'
        doc['regions'].append(region)
        marker = {'id': 'table-2', 'page': 2, 'box': [.1, .2, .9, .7],
                  'source': 'MinerU Basic', 'layout_type': 'table'}
        doc['layout_regions'] = [marker, dict(marker, id='invalid', box=[0, 0, 1, 2])]
        panel = MethodResultsPanel()
        self.addCleanup(panel.close)
        panel.refresh(doc)
        panel.method_filter.setCurrentIndex(panel.method_filter.findData('MinerU Basic'))
        panel.set_page(2)
        self.assertTrue(panel.select_source('table-2'))
        self.assertEqual(panel.tree.currentItem().data(0, Qt.ItemDataRole.UserRole).kind, 'layout')
        self.assertFalse(panel.select_source('invalid'))

        panel.tabs.setCurrentWidget(panel.json_view)
        snapshot = json.loads(panel.json_view.toPlainText())
        self.assertEqual(snapshot['schema'], 'drawiq-recognition-results/1')
        self.assertEqual(snapshot['filter'], {'method': 'MinerU Basic', 'page': 2})
        by_kind = {result['kind']: result for result in snapshot['results']}
        self.assertEqual(set(by_kind), {'evidence', 'layout'})
        self.assertEqual(by_kind['layout']['sources'][0]['bbox'], marker['box'])
        self.assertEqual(by_kind['evidence']['sources'][0]['bbox'], region['box'])
        self.assertEqual(by_kind['evidence']['sources'][0]['layout_type'], 'text')

        with TemporaryDirectory() as folder:
            json_path = Path(folder) / 'filtered.json'
            with patch('workbench.nailok_methods_ui.QFileDialog.getSaveFileName',
                       return_value=(str(json_path), 'JSON (*.json)')):
                panel.export_results()
            self.assertEqual(json.loads(json_path.read_text(encoding='utf-8')), snapshot)

            panel.tabs.setCurrentWidget(panel.markdown_view)
            self.assertIn('DrawIQ 辨識結果', panel.markdown_view.toPlainText())
            self.assertIn('版面 · 表格', panel.markdown_view.toPlainText())
            markdown_path = Path(folder) / 'filtered.md'
            with patch('workbench.nailok_methods_ui.QFileDialog.getSaveFileName',
                       return_value=(str(markdown_path), 'Markdown (*.md)')):
                panel.export_results()
            content = markdown_path.read_text(encoding='utf-8')
            self.assertIn('MinerU Basic', content)
            self.assertIn('`0.1, 0.2, 0.9, 0.7`', content)
            self.assertNotIn('RapidOCR', content)

    def test_page_filter_limits_cross_page_result_source_boxes_to_the_selected_page(self):
        doc = drawing()
        first, second, _ = doc['regions']
        panel = MethodResultsPanel()
        self.addCleanup(panel.close)
        panel.refresh(doc)
        panel.method_filter.setCurrentIndex(panel.method_filter.findData('VLM · local:model'))
        panel.set_page(2)
        result = next(row for row in panel.snapshot()['results'] if row['label'] == '規格')
        self.assertEqual(result['page'], 2)
        self.assertEqual([source['id'] for source in result['sources']], [second['id']])
        self.assertNotIn(first['id'], panel.source_items)


if __name__ == '__main__':
    unittest.main()
