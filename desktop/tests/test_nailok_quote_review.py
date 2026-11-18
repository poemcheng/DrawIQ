import unittest

from tests.test_nailok_flow import Fixture
from workbench.domain import evidence
from workbench.nailok_flow import alternatives, annotation_text
from workbench.store import ConflictError


class QuoteReviewTests(Fixture, unittest.TestCase):
    def fields(self, doc, *, include_legacy=False):
        return {row['key']: row for row in self.service.prefill(doc,include_legacy=include_legacy)}

    def add_reading(self, doc, text):
        region = evidence(1, [.1, .25, .7, .3], text, 'synthetic OCR')
        return self.store.save_region(doc['id'], doc['version'], region, None, 'synthetic reading'), region

    def test_all_features_have_a_display_value_and_raw_boxes_are_selectable(self):
        doc, region = self.add_reading(self.doc, '報價單號: Q-TEST-734')
        rows = self.fields(doc)
        self.assertEqual([row['label'] for row in rows.values()],
                         '報價單號 專案代號 報價人員 報價日期 報價有效日期 客戶 材質 報價膠別 顏色 塗佈方式 '
                         '規格 數量 報價 品質要求 包裝方式 注意事項 聯絡電話 客戶傳真'.split())
        self.assertTrue(all(row['display_value'] for row in rows.values()))
        number = rows['quotation.number']
        self.assertEqual(number['value'], 'Q-TEST-734')
        self.assertIsNone(number['candidate_id'])
        self.assertEqual(number['evidence_ids'], [region['id']])
        self.assertEqual(rows['packaging.instructions']['display_value'], '待補資料')
        self.assertEqual(rows['packaging.instructions']['value'], '')

    def test_annual_context_does_not_become_a_batch_by_trimming(self):
        doc, region = self.add_reading(self.doc, '每年用量 120000 PCS')
        row = {'key': 'quotation.quantity', 'evidence_ids': [region['id']], 'value': '120000', 'unit': 'PCS'}
        with self.assertRaisesRegex(ValueError, '年度'):
            self.service.confirm_many(doc['id'], doc['version'], [row], human_checked=True)
        self.assertEqual(self.store.get(doc['id'])['version'], doc['version'])

    def test_explicit_quantity_keeps_unclear_magnitude_without_conversion(self):
        source = self.doc['requirements'][2]
        doc = self.store.edit_requirement(self.doc['id'], self.doc['version'], source['id'],
                                          {'state': 'pending_decision', 'value': '100', 'unit': 'M'},
                                          'synthetic ambiguous quantity')
        quantity = self.fields(doc)['quotation.quantity']
        self.assertEqual(quantity['value'], '100')
        self.assertEqual(quantity['unit'], 'M')
        self.assertFalse(quantity['confirmed'])
        self.assertIsNone(quantity['candidate_id'])
        self.assertTrue(any('M／K' in issue for issue in quantity['issues']))

    def test_patch_range_stays_with_coverage_and_method_keeps_its_own_reading(self):
        doc, scope = self.add_reading(self.doc, 'Patch: 6 mm ± 1 mm')
        self.assertFalse(any(row['prefill_allowed'] and scope['id'] in row['evidence_ids']
                             for row in alternatives(doc, 'coating.method')))
        self.assertEqual(self.fields(doc,include_legacy=True)['nailok.coverage']['evidence_ids'], [scope['id']])
        doc, method = self.add_reading(doc, 'Coating: Nylon patch')
        row = self.fields(doc)['coating.method']
        self.assertEqual(row['value'], 'Nylon patch')
        self.assertEqual(row['evidence_ids'], [method['id']])

    def test_batch_rolls_back_if_any_row_fails_and_checks_document_version(self):
        doc, region = self.add_reading(self.doc, 'Drawing No: XY-734')
        first = {'key': 'drawing_number', 'evidence_ids': [region['id']], 'value': 'XY-734'}
        invalid = {'key': 'nailok.coverage', 'evidence_ids': [region['id']], 'value': '8'}
        with self.assertRaisesRegex(ValueError, '單位'):
            self.service.confirm_many(doc['id'], doc['version'], [first, invalid], human_checked=True)
        self.assertEqual(self.store.get(doc['id']), doc)
        second = {'key': 'packaging.instructions', 'state': 'missing', 'reason': '圖面未提供'}
        updated = self.service.confirm_many(doc['id'], doc['version'], [first, second], human_checked=True)
        self.assertEqual(updated['version'], doc['version'] + 1)
        self.assertEqual(self.store.history(doc['id'])[0]['action'], 'nailok_confirm_batch')
        self.assertEqual(self.fields(updated,include_legacy=True)['drawing_number']['status'], '已確認')
        self.assertEqual(self.fields(updated)['packaging.instructions']['status'], '圖面未提供')
        with self.assertRaises(ConflictError):
            self.service.confirm_many(doc['id'], doc['version'], [first], human_checked=True)

    def test_editing_one_annotation_preserves_ocr_and_invalidates_direct_confirmation(self):
        doc, region = self.add_reading(self.doc, 'Drawing No: XY-734')
        row = {'key': 'drawing_number', 'evidence_ids': [region['id']], 'value': 'XY-734'}
        doc = self.service.confirm_many(doc['id'], doc['version'], [row], human_checked=True)
        self.assertTrue(self.fields(doc,include_legacy=True)['drawing_number']['confirmed'])
        edited = self.service.correct_annotation(doc['id'], doc['version'], region['id'], 'Drawing No: XY-784')
        changed = self.fields(edited,include_legacy=True)['drawing_number']
        self.assertFalse(changed['confirmed'])
        self.assertEqual(changed['value'], 'XY-784')
        self.assertEqual(edited['regions'], doc['regions'])
        self.assertEqual(annotation_text(edited, region), 'Drawing No: XY-784')
        self.assertEqual(self.service.report(edited)['annotation_corrections'][0]['original_text'], 'Drawing No: XY-734')

    def test_multibox_confirmation_links_originals_instead_of_rewriting_each_box(self):
        doc, first = self.add_reading(self.doc, '10 mm')
        doc, second = self.add_reading(doc, '15 mm')
        row = {'key': 'nailok.dimensions', 'evidence_ids': [first['id'], second['id']], 'value': '10 mm\n15 mm'}
        confirmed = self.service.confirm_many(doc['id'], doc['version'], [row], human_checked=True)
        source = self.fields(confirmed,include_legacy=True)['nailok.dimensions']['source']
        self.assertEqual(source['evidence_ids'], [first['id'], second['id']])
        self.assertEqual(confirmed['regions'], doc['regions'])
        self.assertNotIn('nailok.dimensions', [row['key'] for row in self.service.report(confirmed)['features']])
        self.assertEqual(self.service.report(confirmed)['confirmed_engineering_features'][0]['value'], '10 mm\n15 mm')

    def test_form_values_preserve_price_format_and_skip_obscured_contact(self):
        text = ('報價單號：Q-TEST-120\n專案代號：\n報價人員：測試甲\n報價日期：2026/7/1\n'
                '報價有效日期：2026/7/31\n客　戶：……\n材　質：低碳鋼\n報價膠別：Test Patch X\n'
                '顏　色：紫色\n塗佈方式：全周\n規　格：M6x1x20\n數　量：17 M\n'
                '報　價：/ TWD / M\n品質要求：需材證\n包裝方式：每 50 個裝袋\n'
                '注意事項：首批確認\n聯絡電話：0900-111-222（分機05）\n客戶傳真：■■')
        doc, source = self.add_reading(self.doc, text)
        rows = self.fields(doc)
        self.assertEqual(rows['quotation.project']['value'], '')
        self.assertEqual(rows['customer.name']['value'], '')
        self.assertEqual(rows['contact.fax']['value'], '')
        self.assertTrue(rows['contact.phone']['value'].startswith('0900-111-222'))
        self.assertEqual(rows['material']['value'], '低碳鋼')
        self.assertEqual(rows['coating.color']['value'], '紫色')
        self.assertEqual(rows['coating.method']['value'], '全周')
        self.assertEqual(rows['quotation.date']['value'], '2026-07-01')
        self.assertEqual(rows['quotation.valid_until']['value'], '2026-07-31')
        self.assertEqual((rows['quotation.quantity']['value'],rows['quotation.quantity']['unit']), ('17','M'))
        price = rows['quotation.price']
        self.assertEqual(price['value'], '')
        self.assertEqual(price['unit'], 'TWD/M')
        self.assertEqual(price['display_value'], '待補金額 · TWD/M')
        self.assertEqual(price['evidence_ids'], [source['id']])
        self.assertEqual(rows['packaging.instructions']['value'], '每 50 個裝袋')
        self.assertEqual(rows['quotation.notes']['value'], '首批確認')
        report = self.service.report(doc)
        self.assertEqual(report['quotation_price_format']['unit'], 'TWD/M')
        self.assertFalse(report['confirmed_features'])

    def test_separate_label_and_value_boxes_and_real_printed_price(self):
        from workbench.domain import clone
        doc=clone(self.doc)
        label=evidence(1,[.1,.4,.21,.44],'報價：','synthetic OCR')
        price=evidence(1,[.22,.4,.55,.44],'42.75 / TWD / PCS','synthetic OCR')
        doc['regions'] += [label,price]
        row=self.fields(doc)['quotation.price']
        self.assertEqual((row['value'],row['unit']),('42.75','TWD/PCS'))
        self.assertEqual(row['evidence_ids'],[label['id'],price['id']])

    def test_layout_ocr_can_put_template_labels_in_one_box_without_spaces(self):
        text=('報價標籤報價單號:QTEST01材質:SUS304規格:M6x1x20數量:25 PCS'
              '報價:/ TWD / M品質要求:包裝方式:每50個裝袋客戶傳真:TF-TA-014-05-01')
        doc,source=self.add_reading(self.doc,text)
        rows=self.fields(doc)
        for key,value in [('quotation.number','QTEST01'),('material','SUS304'),
                          ('product.specification','M6x1x20'),('quotation.quantity','25'),
                          ('packaging.instructions','每50個裝袋')]:
            self.assertEqual(rows[key]['value'],value)
            self.assertEqual(rows[key]['evidence_ids'],[source['id']])
        self.assertFalse(rows['quotation.price']['value'])
        self.assertEqual(rows['quotation.price']['unit'],'TWD/M')
        self.assertFalse(rows['quality.requirements']['value'])
        self.assertFalse(rows['contact.fax']['value'])

    def test_price_template_is_not_quality_and_complete_quantity_wins(self):
        from workbench.domain import clone,requirement
        doc=clone(self.doc)
        number=evidence(1,[.1,.24,.34,.26],'報價單號：Q-TEST-912','synthetic OCR')
        partial=evidence(1,[.1,.3,.35,.33],'量：27','synthetic OCR')
        qty_label=evidence(1,[.1,.35,.2,.38],'數','synthetic OCR')
        qty_value=evidence(1,[.22,.35,.4,.38],'量：27','synthetic OCR')
        qty_unit=evidence(1,[.42,.35,.45,.38],'M','synthetic OCR')
        quality=evidence(1,[.1,.55,.4,.58],'品質要求：/ TWD / M','synthetic OCR')
        doc['regions'] += [number,partial,qty_label,qty_value,qty_unit,quality]
        doc['requirements'].append(requirement('quality.requirements','/ TWD / M',
                                               evidence_ids=[quality['id']],origin='spatial_form'))
        rows=self.fields(doc)
        self.assertFalse(rows['quality.requirements']['value'])
        self.assertEqual((rows['quotation.quantity']['value'],rows['quotation.quantity']['unit']),('27','M'))
        self.assertEqual(rows['quotation.quantity']['evidence_ids'],[qty_label['id'],qty_value['id'],qty_unit['id']])

    def test_generic_drawing_roles_are_not_quote_fields(self):
        from workbench.domain import clone,requirement
        doc=clone(self.doc)
        label=evidence(1,[.1,.5,.54,.55],'RFQ Date: 2026/8/9','synthetic OCR')
        fax=evidence(1,[.1,.6,.54,.65],'FAX: 090000001','synthetic OCR')
        doc['regions'] += [label,fax]
        doc['requirements'] += [requirement('quotation.date','2026-08-09',evidence_ids=[label['id']]),
                                requirement('contact.fax','090000001',evidence_ids=[fax['id']])]
        rows=self.fields(doc)
        self.assertEqual(rows['quotation.date']['value'],'')
        self.assertEqual(rows['contact.fax']['value'],'')


if __name__ == '__main__':
    unittest.main()
