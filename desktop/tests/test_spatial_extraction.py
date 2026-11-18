"""Isolated source/geometry regressions for separate OCR form boxes."""
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from workbench.domain import clone, evidence
from workbench.form_extraction import extract_form_fields
from workbench.quotation import extract_quotation
from workbench.quotation_ocr import combine_candidates, reread_rows
from workbench.engineering_ocr import reread_engineering_regions, _targets


def region(text, x0, y0, x1, y1, page=1):
    return evidence(page, [x0, y0, x1, y1], text, 'synthetic')


class SpatialFormTests(unittest.TestCase):
    def fields(self, regions):
        output, _ = extract_form_fields(regions, 1)
        return {record['key']: record for record in output}

    def test_bare_labels_and_values_on_the_same_row_preserve_local_sources(self):
        customer = region('Customer', .03, .10, .16, .13)
        name = region('ACME Manufacturing LLC', .18, .10, .44, .13)
        material = region('Material', .51, .10, .64, .13)
        steel = region('S45C', .66, .10, .75, .13)
        fields = self.fields([customer, name, material, steel])
        self.assertEqual(fields['customer.name']['value'], name['text'])
        self.assertEqual(fields['material']['value'], 'S45C')
        self.assertEqual(fields['customer.name']['evidence_ids'], [customer['id'], name['id']])
        self.assertEqual(fields['material']['evidence_ids'], [material['id'], steel['id']])

    def test_fragmented_number_label_is_not_price(self):
        parts = [region('報價', .03, .1, .08, .13), region('單號', .084, .1, .134, .13),
                 region('：', .137, .1, .15, .13), region('DQ-TEST-001', .165, .1, .4, .13)]
        fields = self.fields(parts)
        self.assertEqual(set(fields), {'quotation.number'})
        self.assertEqual(fields['quotation.number']['value'], 'DQ-TEST-001')
        self.assertEqual(fields['quotation.number']['evidence_ids'], [part['id'] for part in parts])

    def test_label_above_value_and_two_columns_stay_separate(self):
        parts = [region('材質', .1, .1, .25, .13), region('數量', .4, .1, .55, .13),
                 region('SUS304', .12, .15, .24, .18), region('1,600 PCS', .42, .15, .54, .18)]
        fields = self.fields(parts)
        self.assertEqual(fields['material']['value'], 'SUS304')
        self.assertEqual((fields['quantity']['value'], fields['quantity']['unit']), ('1600', 'PCS'))

    def test_single_explicit_label_accepts_compact_value_below(self):
        parts = [region('Material', .1, .1, .24, .13), region('S45C', .1, .14, .21, .17)]
        self.assertEqual(self.fields(parts)['material']['value'], 'S45C')

    def test_quantity_does_not_borrow_price_or_its_denominator(self):
        parts = [region('數量', .03, .1, .11, .13), region('160', .13, .1, .2, .13),
                 region('M', .205, .1, .23, .13), region('報價', .35, .1, .43, .13),
                 region('12.50 TWD/PCS', .45, .1, .75, .13)]
        fields = self.fields(parts)
        self.assertEqual((fields['quantity']['value'], fields['quantity']['unit']), ('160', 'M'))
        self.assertEqual((fields['quotation.price']['value'], fields['quotation.price']['unit']), ('12.50', 'TWD/PCS'))
        self.assertNotIn(parts[-1]['id'], fields['quantity']['evidence_ids'])

    def test_rfqs_recipient_and_dates_do_not_gain_quotation_roles(self):
        parts = [region('詢價日期', .03, .1, .18, .13), region('2026/5/22', .2, .1, .43, .13),
                 region('此致', .03, .2, .12, .23), region('台灣耐落 王經理', .2, .2, .43, .23)]
        self.assertEqual(self.fields(parts), {})

    def test_sample_order_and_annual_scope_are_not_interchanged(self):
        parts = [region('數量', .03, .1, .11, .13), region('160 PCS', .13, .1, .28, .13),
                 region('樣品', .4, .1, .48, .13), region('300 pcs', .5, .1, .65, .13),
                 region('數量', .03, .2, .11, .23), region('72,000 PCS/year', .13, .2, .35, .23)]
        output, _ = extract_form_fields(parts, 1)
        self.assertEqual([(r['key'], r['value']) for r in output], [('quantity', '160'), ('sample.quantity', '300')])

    def test_distant_or_other_page_values_are_not_borrowed(self):
        parts = [region('材質', .03, .1, .11, .13), region('S45C', .7, .1, .8, .13),
                 region('SUS304', .13, .1, .28, .13, page=2), region('S45C', .03, .3, .11, .33)]
        self.assertEqual(self.fields(parts), {})

    def test_date_normalization_retains_original_text_and_invalid_dates_are_rejected(self):
        parts = [region('報價日期', .03, .1, .18, .13), region('2026/9/16', .2, .1, .43, .13),
                 region('有效日期', .03, .2, .18, .23), region('2026/2/30', .2, .2, .43, .23)]
        fields = self.fields(parts)
        self.assertEqual(fields['quotation.date']['value'], '2026-09-16')
        self.assertEqual(fields['quotation.date']['raw_value'], '2026/9/16')
        self.assertNotIn('quotation.valid_until', fields)

    def test_quality_heading_keeps_only_its_adjacent_cell(self):
        parts = [region('品質要求', .03, .1, .18, .13), region('DIN 267-27 MK', .2, .1, .43, .13),
                 region('材質', .51, .1, .64, .13), region('S45C', .66, .1, .75, .13)]
        fields = self.fields(parts)
        self.assertEqual(fields['quality.requirements']['value'], 'DIN 267-27 MK')
        self.assertNotIn(parts[-1]['id'], fields['quality.requirements']['evidence_ids'])

    def test_unknown_inline_neighbor_label_stops_the_current_cell(self):
        parts = [region('Material', .03, .1, .16, .13), region('S45C', .18, .1, .28, .13),
                 region('Drawing No: ABC-123', .30, .1, .64, .13)]
        record = self.fields(parts)['material']
        self.assertEqual(record['value'], 'S45C')
        self.assertEqual(record['evidence_ids'], [parts[0]['id'], parts[1]['id']])

    def test_isolated_quote_heading_does_not_borrow_a_later_amount(self):
        parts = [region('報價', .03, .1, .15, .13), region('160', .03, .15, .12, .18)]
        self.assertEqual(self.fields(parts), {})


class RowOCRTests(unittest.TestCase):
    def parsed(self, text):
        return extract_quotation([region('報價單號：DQ-TEST-001', .02, .03, .8, .07),
                                  region(text, .02, .13, .8, .17)])

    def test_equal_amount_different_currency_or_denominator_remains_conflict(self):
        for first, second in [('報價：12.50 TWD/PCS', '報價：12.50 USD/PCS'),
                              ('報價：12.50 TWD/PCS', '報價：12.50 TWD/M'),
                              ('數量：160 M', '數量：160 PCS')]:
            with self.subTest(first=first, second=second):
                key = 'quotation.quantity' if first.startswith('數量') else 'quotation.price'
                record = next(r for r in combine_candidates(self.parsed(first), self.parsed(second)) if r['key'] == key)
                self.assertEqual(record['state'], 'conflict')
                self.assertIn('單位', ' '.join(record['extraction_notes']))

    def test_row_parser_does_not_mutate_original_or_reread_requirements(self):
        original = self.parsed('報價：12.50 TWD/PCS'); reread = self.parsed('報價：12.50 USD/PCS')
        before_a = clone(original); before_b = clone(reread)
        combine_candidates(original, reread)
        self.assertEqual(original, before_a); self.assertEqual(reread, before_b)

    def test_omitted_price_denominator_can_be_added_and_cannot_be_lost(self):
        first = self.parsed('報價：12.50 TWD'); second = self.parsed('報價：12.50 TWD/PCS')
        for a, b in [(first, second), (second, first)]:
            record = next(r for r in combine_candidates(a, b) if r['key'] == 'quotation.price')
            self.assertEqual((record['state'], record['unit']), ('present', 'TWD/PCS'))

    def test_reread_maps_actual_regions_through_padding_and_resize(self):
        # Import extraction to install the legacy module path, no model run.
        from workbench import extraction
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        image = Image.new('RGB', (500, 300), 'white')
        ImageDraw.Draw(image).rectangle((40, 100, 220, 119), fill='black')
        result = OCRResult('報價：12.50 TWD/PCS', [TextRegion('報價：12.50 TWD/PCS',
                          (116, 20, 316, 40), .96, 'RapidOCR')], 'RapidOCR')
        initial = [region('報價單號：DQ-TEST-001', .02, .03, .8, .07)]
        with patch('drawing_intelligence.ocr._run_rapidocr', return_value=result):
            rows, warnings = reread_rows(image, 1, initial)
        self.assertEqual(warnings, []); self.assertEqual(len(rows), 1)
        for actual, expected in zip(rows[0]['box'], [.11, 100/300, .31, 110/300]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(rows[0]['ocr_confidence'], .96)
        self.assertEqual(rows[0]['text'], result.regions[0].text)
        self.assertLess(rows[0]['box'][2]-rows[0]['box'][0], .3)

    def test_unmeasured_row_text_is_not_saved_as_a_page_wide_box(self):
        from workbench import extraction
        from drawing_intelligence.ocr import OCRResult
        image = Image.new('RGB', (500, 300), 'white')
        ImageDraw.Draw(image).rectangle((40, 100, 220, 119), fill='black')
        initial = [region('報價單號：DQ-TEST-001', .02, .03, .8, .07)]
        with patch('drawing_intelligence.ocr._run_rapidocr', return_value=OCRResult('S45C', [], 'RapidOCR')):
            rows, warnings = reread_rows(image, 1, initial)
        self.assertEqual(rows, [])
        self.assertTrue(any('實測文字框' in warning for warning in warnings))

    def test_batched_row_reread_keeps_distinct_original_boxes_and_recovers_missing_strip(self):
        from workbench import extraction
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        image=Image.new('RGB',(500,300),'white')
        draw=ImageDraw.Draw(image)
        for y in (100,150,200):
            draw.rectangle((40,y,220,y+19),fill='black')
        first=TextRegion('材質：SUS304',(116,20,316,40),.96,'RapidOCR')
        second=TextRegion('數量：10 PCS',(116,100,316,120),.96,'RapidOCR')
        rescued=TextRegion('包裝方式：分袋',(116,20,316,40),.96,'RapidOCR')
        result=OCRResult('材質：SUS304\n數量：10 PCS',[first,second],'RapidOCR')
        single=OCRResult(rescued.text,[rescued],'RapidOCR')
        initial=[region('報價單號：DQ-TEST-001',.02,.03,.8,.07)]
        with patch('drawing_intelligence.ocr._run_rapidocr',side_effect=[result,single]) as run:
            rows,warnings=reread_rows(image,1,initial)
        self.assertEqual(run.call_count,2)
        self.assertEqual(warnings,[])
        self.assertEqual([row['text'] for row in rows],[first.text,second.text,rescued.text])
        for row,page_y in zip(rows,(100,150,200)):
            self.assertAlmostEqual(row['box'][1],page_y/300)
            self.assertLess(row['box'][3],row['ocr_crop'][3])


class EngineeringRereadTests(unittest.TestCase):
    def test_target_dense_engineering_context_and_skip_unrelated_dimensions(self):
        rows = [region('Threadlock application:', .6, .3, .83, .32),
                region('Material: Nylon Torq-Patch Tuflok', .6, .325, .9, .345),
                region('Standard: KL acc. to DIN 267-28', .6, .35, .9, .37),
                region('10 ± 0.29', .3, .3, .4, .4)]
        targets = _targets(rows, 1, 8)
        self.assertEqual(len(targets), 1)
        self.assertEqual({r['id'] for r in targets[0]['members']}, {r['id'] for r in rows[:3]})

    def test_reread_budget_and_cancel_do_not_start_a_model(self):
        import time
        image = Image.new('RGB', (500, 300), 'white')
        initial = [region('M6x1,0-6g', .2, .2, .4, .24)]
        for kwargs in [{'cancelled': lambda: True}, {'deadline': time.monotonic()-1}]:
            rows, warnings = reread_engineering_regions(image, 1, initial, **kwargs)
            self.assertEqual(rows, []); self.assertTrue(warnings)

    def test_reread_retains_measured_alternative_and_original_is_unchanged(self):
        from workbench import extraction
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        image = Image.new('RGB', (500, 500), 'white')
        initial = [region('M6x1,0-6g', .2, .2, .4, .24)]
        before = clone(initial)
        def fake(crop, scale):
            # Input crop padding 3px, upsample3 then border16; these measured
            # pixels map back to the actual original text rectangle.
            return OCRResult('M6x1.0-6g', [TextRegion('M6x1.0-6g', (25, 25, 325, 85), .97, 'RapidOCR')], 'RapidOCR')
        with patch('drawing_intelligence.ocr._run_rapidocr', side_effect=fake):
            rows, warnings = reread_engineering_regions(image, 1, initial)
        self.assertEqual(initial, before); self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], 'M6x1.0-6g')
        self.assertEqual(rows[0]['ocr_parent_ids'], [initial[0]['id']])
        self.assertEqual(rows[0]['ocr_method'], 'engineering-context-reread/0.8.0')
        for actual, expected in zip(rows[0]['box'], initial[0]['box']):
            self.assertAlmostEqual(actual, expected)

    def test_reread_maximum_crops_is_bounded(self):
        rows = [region('Standard: DIN 267-28', .05, .02+i*.07, .25, .04+i*.07) for i in range(12)]
        self.assertEqual(len(_targets(rows, 1, 8)), 8)
