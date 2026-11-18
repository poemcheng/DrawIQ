import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from workbench.domain import evidence
from workbench.ingest import prepare_document
from workbench.nailok_crop import capture_field, draft_from_regions
from workbench.nailok_flow import NailokService
from workbench.region_capture import copy_capture_artifacts
from workbench.store import ConflictError, Store


BOX = [.2, .25, .8, .75]
TOKEN_BOX = [.1, .2, .5, .6]


def recognized(text, *, status='complete'):
    return {'status': status, 'text': text, 'tokens': [
        {'text': text, 'box': TOKEN_BOX, 'confidence': .91},
    ] if text else [], 'elapsed_seconds': .1}


class LabelCropTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        file = self.root / 'synthetic.png'
        Image.new('RGB', (100, 120), 'white').save(file)
        self.store = Store(self.root / 'data', actor=lambda: 'synthetic reviewer')
        self.doc = self.store.add(prepare_document(file, self.store.root))

    def capture(self, first, second, key='quotation.quantity', **kwargs):
        with patch('workbench.nailok_crop.read_capture', return_value=first), patch(
            'workbench.nailok_crop.read_mineru', return_value=second
        ):
            return capture_field(self.store, self.doc, 1, BOX, key, **kwargs)

    def test_dual_methods_persist_distinct_boxes_and_stage_the_selected_label(self):
        captured = self.capture(recognized('250 M'), recognized('260 M'), rotation=90)
        doc = captured['document']
        self.assertTrue(captured['conflict'])
        self.assertEqual(captured['statuses'], {'rapid_v6': 'complete', 'mineru_basic': 'complete'})
        self.assertEqual([r['value'] for r in captured['alternatives']], ['250', '260'])
        self.assertEqual(doc['version'], self.doc['version'] + 1)
        self.assertEqual(doc['pages'], self.doc['pages'])
        self.assertEqual(len(doc['regions']), 2)
        measured = doc['regions'][0]
        for actual, expected in zip(measured['box'], [.32, .5, .56, .7]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(measured['text'], '250 M')
        self.assertEqual(doc['regions'][1]['text'], '260 M')
        self.assertNotEqual(doc['regions'][0]['source'], doc['regions'][1]['source'])
        label = next(row for row in NailokService(self.store).prefill(doc)
                     if row['key'] == 'quotation.quantity')
        self.assertEqual((label['value'], label['unit']), ('250', 'M'))
        self.assertFalse(label['confirmed'])
        history = self.store.history(doc['id'])
        self.assertEqual(history[0]['action'], 'nailok_crop')
        exports = self.root / 'exports'
        self.assertEqual(copy_capture_artifacts(self.store.root, doc, history, exports),
                         [measured['capture']['id']])

    def test_methods_agree_without_losing_ocr_provenance(self):
        captured = self.capture(recognized('數量：250 M'), recognized('250 Ｍ'))
        self.assertFalse(captured['conflict'])
        self.assertEqual((captured['draft']['value'], captured['draft']['unit']), ('250', 'M'))
        self.assertEqual(len(captured['alternatives']), 1)
        self.assertEqual(len(captured['document']['regions']), 2)

    def test_no_value_leaves_a_drawn_frame_available_for_manual_correction(self):
        captured = self.capture(recognized('/ TWD / M'), recognized('', status='partial_timeout'),
                                key='quotation.price')
        self.assertIsNone(captured['draft'])
        self.assertEqual(captured['statuses']['mineru_basic'], 'partial_timeout')
        frame = next(r for r in captured['document']['regions'] if r['id'] == captured['frame_id'])
        self.assertEqual((frame['source'], frame['text'], frame['box']), ('manual_capture', '', BOX))
        self.assertEqual(captured['document']['regions'][0]['text'], '/ TWD / M')

    def test_cancel_does_not_change_the_document(self):
        captured = self.capture(recognized('250 M'), recognized('250 M'), cancelled=lambda: True)
        self.assertTrue(captured['cancelled'])
        self.assertEqual(self.store.get(self.doc['id']), self.doc)

    def test_stale_capture_cannot_overwrite_concurrent_document_edits(self):
        original = evidence(1, [.1, .1, .2, .2], '人工文字')
        newest = self.store.save_region(self.doc['id'], self.doc['version'], original, None, 'isolated edit')
        with self.assertRaises(ConflictError):
            self.capture(recognized('250 M'), recognized('250 M'))
        self.assertEqual(self.store.get(self.doc['id']), newest)

    def test_direct_selection_uses_selected_role_without_fabricating_ambiguous_values(self):
        samples = [
            ('報價：12.50 / TWD / PCS', 'quotation.price', ('12.50', 'TWD/PCS')),
            ('12.50 / TWD / PCS', 'quotation.price', ('12.50', 'TWD/PCS')),
            ('/ TWD / PCS', 'quotation.price', None),
            ('材質：SUS304', 'customer.name', None),
            ('數量：250 M／年', 'quotation.quantity', None),
            ('聯絡電話：02-0000000', 'contact.phone', ('02-0000000', '')),
            ('聯絡電話：12', 'contact.phone', None),
        ]
        for text, key, expected in samples:
            with self.subTest(text=text):
                region = evidence(1, BOX, text, 'RapidOCR')
                row = draft_from_regions({**self.doc, 'regions': [region]}, key, [region['id']])
                actual = (row['value'], row['unit']) if row else None
                self.assertEqual(actual, expected)
                if row:
                    self.assertEqual(row['evidence_ids'], [region['id']])

    def test_split_quantity_and_overlapping_colon_keep_the_real_source_boxes(self):
        from workbench.nailok_crop import field_rows
        parts = [evidence(1, box, text, 'RapidOCR') for box, text in (
            ([.04,.18,.14,.79], '數'), ([.27,.17,.42,.82], '量'),
            ([.39,.16,.62,.82], ':250'), ([.72,.21,.82,.78], 'M'),
        )]
        draft = field_rows('quotation.quantity', parts)[0]
        self.assertEqual((draft['value'],draft['unit']),('250','M'))
        self.assertEqual(draft['evidence_ids'],[part['id'] for part in parts])
        parts[-1]['box']=[.72,.835,.82,.995]
        for row in field_rows('quotation.quantity', parts):
            self.assertEqual(row['unit'],'')
            self.assertNotIn(parts[-1]['id'],row['evidence_ids'])
