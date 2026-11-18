"""Fixtures added before integrating the supplied quotation skill rules."""
import unittest

from workbench.domain import clone, evidence
from workbench.quote_skill import analyze, parse_spec


def document(texts, *, document_type=None, confidence=.96):
    regions = [evidence(1, [.03, .02+i*.042, .94, .048+i*.042], text, 'synthetic_annotated_ocr')
               for i, text in enumerate(texts)]
    for region in regions:
        if confidence is not None:
            region['ocr_confidence'] = confidence
    doc = {'id': 'synthetic-quote-skill', 'pages': [{'number': 1, 'width': 1000, 'height': 1400}],
           'regions': regions, 'requirements': []}
    if document_type:
        doc['document_type'] = document_type
    return doc


def fields(report):
    return {field['key']: field for field in report['fields']}


class SpecificationRulesTests(unittest.TestCase):
    def test_metric_length_only_uses_explicit_quote_convention_or_unit(self):
        parsed, issues = parse_spec('M8 x 1.25 × 50', document_type='quote_label')
        self.assertEqual((parsed['diameter_mm'], parsed['pitch_mm'], parsed['length_mm']), (8, 1.25, 50))
        self.assertEqual(parsed['length_unit_basis'], 'quote_label_convention')
        self.assertEqual(issues, [])
        parsed, issues = parse_spec('M8x1.25x50', document_type='engineering_drawing')
        self.assertEqual(parsed['length_value'], 50)
        self.assertIsNone(parsed['length_mm']); self.assertIsNone(parsed['length_unit'])
        self.assertIn('length_unit_unverified', issues)
        parsed, issues = parse_spec('M8x1.25x50', length_unit='mm')
        self.assertEqual(parsed['length_mm'], 50); self.assertEqual(issues, [])

    def test_tolerance_is_not_length_and_pitch_is_never_invented(self):
        for raw, pitch, tolerance in [('M8x1.25-6h', 1.25, '6h'), ('M10-6g', None, '6g')]:
            with self.subTest(raw=raw):
                parsed, issues = parse_spec(raw)
                self.assertEqual(issues, [])
                self.assertEqual(parsed['pitch_mm'], pitch)
                self.assertEqual(parsed['tolerance'], tolerance)
                self.assertIsNone(parsed['length_value']); self.assertIsNone(parsed['length_mm'])

    def test_decimal_comma_is_only_numeric_formatting_and_raw_notation_stays(self):
        parsed, issues = parse_spec('M6x1,0-6g')
        self.assertEqual(parsed['pitch_mm'], 1)
        self.assertEqual(parsed['normalized'], 'M6x1,0-6g')
        self.assertIn('decimal_comma_numeric_conversion', parsed['normalization_notes'])
        self.assertEqual(issues, [])

    def test_unified_series_stays_exact_and_length_unit_is_not_assumed(self):
        for series in ('UNC', 'UNF', 'UNEF'):
            parsed, issues = parse_spec('7/16-20'+series+'x25.4', document_type='quote_label')
            self.assertEqual(parsed['diameter_inch'], 7/16)
            self.assertEqual(parsed['tpi'], 20); self.assertEqual(parsed['series'], series)
            self.assertIsNone(parsed['length_unit']); self.assertEqual(parsed['length_value'], 25.4)
            self.assertIn('length_unit_unverified', issues)
        parsed, issues = parse_spec('7/16-20UNCx25.4 mm')
        self.assertEqual(parsed['length_unit'], 'mm'); self.assertEqual(issues, [])

    def test_bad_numbers_unknown_syntax_and_separate_numeric_tokens_are_not_fixed(self):
        for raw in ('7/0-20UNC', 'M0x1.25', 'M8x0', 'M8x1.25x0', 'M8x1.25x50 extra', 'M1 0-6g'):
            with self.subTest(raw=raw):
                parsed, issues = parse_spec(raw)
                self.assertIsNone(parsed); self.assertTrue(issues)

    def test_numbered_unified_size_has_no_guessed_diameter(self):
        parsed, issues = parse_spec('#10-32UNF')
        if parsed is None:
            self.assertTrue(issues)
        else:
            self.assertIsNone(parsed['diameter_inch'])
            self.assertEqual(parsed['size_designation'], '#10')


class QuoteSkillRulesTests(unittest.TestCase):
    def test_three_supplied_family_suffix_quantity_examples(self):
        for adhesive, quantity, spec in [('precoat 85(土耳其藍)', '800 M', '7/16-20UNCx30.5'),
                                          ('precoat 80-8(紅)', '160 M', 'M6x1.0x35'),
                                          ('precoat 80(紅色)', '215 M', 'M8x1.25x40')]:
            with self.subTest(adhesive=adhesive):
                doc = document(['報價單號：DQ26004001', '報價日期：2026/4/16', '報價有效日期：2026/5/16',
                                '報價級別：'+adhesive, '數量：'+quantity, '規格：'+spec, '報價：/ TWD / M'])
                report = analyze(doc); found = fields(report)
                self.assertEqual(report['schema'], 'drawiq-quote-skill/1')
                self.assertEqual(report['document_type'], 'quote_label')
                self.assertEqual(found['adhesive.product']['normalized']['code'], adhesive.split()[1].split('(')[0])
                self.assertEqual(found['quotation.quantity']['normalized']['value'], int(quantity.split()[0]))
                self.assertEqual(found['quotation.quantity']['normalized']['unit_raw'], 'M')
                self.assertIsNone(found['quotation.price']['normalized'])
                self.assertEqual(found['quotation.price']['status'], 'missing')
                self.assertEqual(found['quotation.date']['normalized'], '2026-04-16')

    def test_raw_spaces_punctuation_case_and_family_are_preserved(self):
        raw = '  precote 80-8(紅)  '
        doc = document(['報 價 單 號： DQ26004001 ', '報價膠別：'+raw])
        before = clone(doc); found = fields(analyze(doc)); self.assertEqual(doc, before)
        self.assertEqual(found['adhesive.product']['raw'], raw)
        self.assertEqual(found['adhesive.product']['normalized']['family'], 'precote')
        self.assertEqual(found['adhesive.product']['normalized']['code'], '80-8')
        self.assertEqual(found['adhesive.product']['sources'][0]['text'], '報價膠別：'+raw)
        self.assertEqual(found['quotation.number']['raw'], ' DQ26004001 ')

    def test_price_and_number_prefixes_do_not_swallow_adjacent_labels(self):
        doc = document(['報價單號：DQ-DEMO-20260916   報價：12.50 TWD / PCS',
                        '客戶：ACME Manufacturing LLC   材質：S45C'])
        found = fields(analyze(doc))
        self.assertEqual(found['quotation.number']['normalized'], 'DQ-DEMO-20260916')
        self.assertEqual(found['quotation.price']['normalized']['amount'], 12.5)
        self.assertEqual(found['customer.name']['normalized'], 'ACME Manufacturing LLC')
        self.assertEqual(found['material']['normalized'], 'S45C')
        self.assertNotIn('單號', found['quotation.price']['raw'])

    def test_missing_values_are_null_and_not_zero(self):
        found = fields(analyze(document(['報價單號：DQ26004001', '單價：/ TWD / M'])))
        self.assertIsNone(found['quotation.price']['normalized'])
        self.assertIsNone(found['material']['normalized'])
        self.assertIsNone(found['material']['raw']); self.assertEqual(found['material']['sources'], [])
        self.assertIsNone(found['material']['confidence'])
        self.assertEqual(found['material']['status'], 'missing')

    def test_conflict_retains_every_distinct_value_and_source(self):
        doc = document(['報價單號：DQ26004001', '報價單號：DQ26004002', '材質：S45C', '材質：SUS304'])
        found = fields(analyze(doc))
        for key, wanted in [('quotation.number', {'DQ26004001', 'DQ26004002'}), ('material', {'S45C', 'SUS304'})]:
            field = found[key]
            self.assertEqual(field['status'], 'conflict'); self.assertIsNone(field['normalized'])
            self.assertEqual({alternative['normalized'] for alternative in field['alternatives']}, wanted)
            self.assertEqual(len(field['sources']), 2)

    def test_equal_values_combine_sources_without_losing_raw_variants(self):
        found = fields(analyze(document(['報價單號：DQ26004001', '材質： S45C ', '材質：S45C'])))
        self.assertEqual(found['material']['normalized'], 'S45C')
        self.assertEqual(len(found['material']['sources']), 2)
        self.assertEqual({a['raw'] for a in found['material']['alternatives']}, {' S45C ', 'S45C'})

    def test_confidence_none_bad_or_low_cannot_become_extracted(self):
        for confidence in (None, .65, 1.2, -1, float('nan'), True):
            with self.subTest(confidence=confidence):
                found = fields(analyze(document(['報價單號：DQ26004001', '材質：S45C'], confidence=confidence)))
                self.assertEqual(found['material']['status'], 'needs_review')
                if confidence is None or confidence != .65:
                    self.assertIsNone(found['material']['confidence'])

    def test_unlocated_ocr_values_are_preserved_as_review_only(self):
        doc = document([], document_type='quote_label')
        doc['unlocated_ocr'] = [{'id': 'external-1', 'page': 1, 'box': None,
                                 'text': '報價级別：precoat 80-8(紅)', 'source': 'provided_ocr', 'ocr_confidence': .99}]
        field = fields(analyze(doc))['adhesive.product']
        self.assertEqual(field['normalized']['code'], '80-8')
        self.assertEqual(field['status'], 'needs_review')
        self.assertIsNone(field['source']['box'])
        self.assertIn('missing_bbox', field['issues'])
        self.assertEqual(field['source']['region_id'], 'external-1')

    def test_dates_reverse_order_and_valid_other_number_are_warnings(self):
        report = analyze(document(['報價單號：DQ-VALID-2026-A', '報價日期：2026/5/16', '有效日期：2026/4/16']))
        found = fields(report)
        self.assertEqual(found['quotation.number']['normalized'], 'DQ-VALID-2026-A')
        self.assertNotEqual(found['quotation.number']['status'], 'missing')
        self.assertEqual(found['quotation.valid_until']['status'], 'needs_review')
        self.assertIn('invalid_date_order', found['quotation.valid_until']['issues'])
        self.assertIn('unusual_quote_no', found['quotation.number']['issues'])

    def test_generic_drawing_specifications_are_candidates_only(self):
        report = analyze(document(['公差依圖面', 'M8x1.25-6h', 'M10-6g', '7/16-20UNFx25.4']))
        self.assertEqual(report['document_type'], 'engineering_drawing')
        self.assertEqual(fields(report)['product.specification']['status'], 'missing')
        self.assertEqual(len(report['specification_candidates']), 3)
        self.assertTrue(all(candidate['status'] == 'needs_review' for candidate in report['specification_candidates']))

    def test_unknown_document_and_generated_model_sources_require_review(self):
        report = analyze(document(['材質：S45C']))
        self.assertEqual(report['document_type'], 'unknown')
        self.assertEqual(fields(report)['material']['status'], 'needs_review')
        doc = document(['報價單號：DQ26004001', '材質：S45C'])
        doc['regions'][1]['source'] = 'VLM:qwen2.5vl:7b'
        self.assertEqual(fields(analyze(doc))['material']['status'], 'needs_review')

    def test_quality_and_packaging_paragraphs_preserve_newlines_and_stop_at_next_label(self):
        doc = document(['報價單號：DQ26004001', '品質要求：DIN 267-27 MK', '每批需附檢測報告',
                        '包裝方式：紙箱包裝', '每箱不超過25kg', '注意事項：先打樣'])
        found = fields(analyze(doc))
        self.assertEqual(found['quality.requirements']['raw'], 'DIN 267-27 MK\n每批需附檢測報告')
        self.assertEqual(found['packaging.instructions']['raw'], '紙箱包裝\n每箱不超過25kg')
        self.assertNotIn('先打樣', found['packaging.instructions']['raw'])

    def test_roles_do_not_turn_recipient_or_responsibility_into_customer(self):
        report = analyze(document(['報價單號：DQ26004001', 'Customer: 456289 is design responsible.', 'TO: 台灣耐落']))
        self.assertEqual(fields(report)['customer.name']['status'], 'needs_review')
        self.assertIn('invalid_field_role', fields(report)['customer.name']['issues'])

    def test_alias_word_inside_customer_name_is_not_another_field(self):
        found = fields(analyze(document(['報價單號：DQ26004001', 'Customer: Material Handling LLC'])))
        self.assertEqual(found['customer.name']['normalized'], 'Material Handling LLC')
        self.assertEqual(found['material']['status'], 'missing')

    def test_same_typed_spec_different_decimal_notation_is_not_a_conflict(self):
        found = fields(analyze(document(['報價單號：DQ26004001', '規格：M6x1,0-6g', '規格：M6x1.0-6g'])))
        self.assertNotEqual(found['product.specification']['status'], 'conflict')
        self.assertEqual(len(found['product.specification']['alternatives']), 2)

    def test_tolerance_case_difference_is_a_real_conflict(self):
        found = fields(analyze(document(['報價單號：DQ26004001', '規格：M6x1.0-6g', '規格：M6x1.0-6G'])))
        self.assertEqual(found['product.specification']['status'], 'conflict')

    def test_quality_does_not_collect_the_next_row_in_another_column(self):
        doc = document(['報價單號：DQ26004001', '品質要求：DIN 267-27 MK', 'BOM 42'])
        doc['regions'][1]['box'] = [.03, .15, .36, .18]
        doc['regions'][2]['box'] = [.65, .19, .8, .22]
        found = fields(analyze(doc))
        self.assertEqual(found['quality.requirements']['raw'], 'DIN 267-27 MK')

    def test_external_ocr_pixel_coordinates_are_converted_only_with_measured_dimensions(self):
        doc = document([], document_type='quote_label')
        doc['pages'][0]['provided_ocr_lines'] = [
            {'text': '材質：S45C', 'pixel_bbox': [30, 20, 500, 50], 'confidence': .98}]
        found = fields(analyze(doc))
        self.assertEqual(found['material']['source']['box'], [.03, 20/1400, .5, 50/1400])
        self.assertEqual(found['material']['source']['pixel_bbox_raw'], [30, 20, 500, 50])
        del doc['pages'][0]['width']
        found = fields(analyze(doc))
        self.assertIsNone(found['material']['source']['box'])
        self.assertEqual(found['material']['status'], 'needs_review')

    def test_provided_line_region_id_reuses_existing_evidence_once(self):
        doc = document(['報價單號：DQ26004001', '材質：S45C'])
        doc['pages'][0]['provided_ocr_lines'] = [{'region_id': doc['regions'][1]['id'],
            'text': '材質：S45C', 'pixel_bbox_raw': [30, 90, 940, 130], 'confidence': .96}]
        field = fields(analyze(doc))['material']
        self.assertEqual(len(field['sources']), 1)
        self.assertEqual(field['alternatives'], [])
        self.assertEqual(field['source']['pixel_bbox_raw'], [30, 90, 940, 130])

    def test_ocr_contract_source_bbox_survives_unlocated_and_measured_sources(self):
        for measured in (True, False):
            doc = document(['報價單號：DQ26004001', '材質：S45C'])
            region = doc['regions'][1]
            region['source_bbox'] = [30, 90, 940, 130]
            region['source_contract'] = 'provided_transcription'
            if not measured:
                region['box'] = None
            field = fields(analyze(doc))['material']
            self.assertEqual(field['source']['pixel_bbox_raw'], [30, 90, 940, 130])
            self.assertEqual(field['source']['source_contract'], 'provided_transcription')
            if not measured:
                self.assertEqual(field['status'], 'needs_review')

    def test_high_score_generated_box_cannot_certify_a_field(self):
        doc = document(['報價單號：DQ26004001', '材質：S45C'], confidence=.99)
        doc['regions'][1]['source'] = 'VLM:qwen2.5vl:7b'
        field = fields(analyze(doc))['material']
        self.assertEqual(field['normalized'], 'S45C')
        self.assertEqual(field['status'], 'needs_review')
        self.assertIn('generated_model_evidence', field['issues'])
        self.assertEqual(field['source']['method'], 'VLM:qwen2.5vl:7b')

    def test_unknown_method_is_preserved_as_an_issue_without_a_crash(self):
        doc = document(['報價單號：DQ26004001', '材質：S45C'])
        doc['regions'][1]['source'] = None
        field = fields(analyze(doc))['material']
        self.assertEqual(field['normalized'], 'S45C')
        self.assertEqual(field['status'], 'needs_review')
        self.assertIn('missing_method', field['issues'])

    def test_far_same_baseline_dimensions_are_not_thread_values(self):
        # Actual Drawing001 re-read coordinates; the drawing dimension is
        # in another block, although its tall OCR box overlaps this baseline.
        doc = document(['Thread', '2.08', 'M6x1.0-6g'], document_type='engineering_drawing')
        doc['regions'][0]['box'] = [.2481531229, .2319563153, .3163196776, .2516619183]
        doc['regions'][1]['box'] = [.541, .218, .566, .260]
        doc['regions'][2]['box'] = [.2139019476, .2457264957, .3166554735, .2689933523]
        report = analyze(doc); field = fields(report)['product.specification']
        self.assertIsNone(field['normalized'])
        self.assertNotIn('2.08', field['raw'] or '')
        self.assertFalse(any('2.08' in (a['raw'] or '') for a in field['alternatives']))

    def test_far_same_baseline_text_does_not_extend_a_material_cell(self):
        doc = document(['報價單號：DQ26004001', '材質：S45C', 'BOM 42'])
        doc['regions'][1]['box'] = [.03, .15, .30, .18]
        doc['regions'][2]['box'] = [.65, .15, .80, .18]
        self.assertEqual(fields(analyze(doc))['material']['normalized'], 'S45C')
