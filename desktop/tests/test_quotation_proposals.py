"""Suggestion ranking fixtures; a filled suggestion never attests correctness."""
import unittest

from workbench.domain import clone, evidence, requirement
from workbench.quotation import FIELDS
from workbench.quotation_proposals import choose_proposal


def candidate(key, value, text, *, unit='', confidence=.95, source='OCR:test',
              state='present', subject='document', box=None, **extra):
    e = evidence(1, box or [.1, .2, .8, .24], text, source)
    e['ocr_confidence'] = confidence
    r = requirement(key, value, unit=unit, label=FIELDS.get(key, (key, ''))[0],
                    scope='page:1', evidence_ids=[e['id']], state=state, subject=subject, **extra)
    return r, e


def choose(rows, key):
    return choose_proposal([r for r, _ in rows], key, {e['id']: e for _, e in rows})


class QuotationProposalTests(unittest.TestCase):
    def test_valid_low_confidence_value_is_visible_as_a_suggestion(self):
        r, e = candidate('material', 'S45C', 'Material: S45C', confidence=.44)
        result = choose([(r, e)], 'material')
        self.assertEqual(result['source']['value'], 'S45C')
        self.assertEqual(result['confidence'], .44)
        self.assertIn('low_confidence', result['issues'])
        self.assertFalse(result['source']['support_verified'])
        self.assertEqual(result['source']['status'], 'needs_review')

    def test_missing_and_bad_scores_are_not_fabricated(self):
        for confidence in (None, float('nan'), True, 1.2):
            r, e = candidate('material', 'S45C', 'Material: S45C', confidence=confidence)
            result = choose([(r, e)], 'material')
            self.assertIsNotNone(result['source'])
            self.assertIsNone(result['confidence'])
            self.assertTrue(result['issues'])

    def test_conflicting_printed_values_choose_best_but_retain_alternatives(self):
        low = candidate('material', 'S45C', 'Material: S45C', confidence=.55, state='conflict')
        high = candidate('material', 'SUS304', 'Material: SUS304', confidence=.92, state='conflict')
        result = choose([low, high], 'material')
        self.assertEqual(result['source']['value'], 'SUS304')
        self.assertIn('conflict_candidate', result['issues'])
        self.assertEqual({a['value'] for a in result['alternatives']}, {'S45C', 'SUS304'})
        self.assertTrue(all(r['state'] == 'conflict' for r, _ in (low, high)))

    def test_complete_labeled_specification_precedes_loose_thread_token(self):
        complete = candidate('product.specification', 'M8x1.25x50', 'Specification: M8x1.25x50', confidence=.62)
        loose = candidate('product.specification', 'M8-6h', 'M8-6h', confidence=.99, subject='unassigned_feature')
        result = choose([loose, complete], 'product.specification')
        self.assertEqual(result['source']['value'], 'M8x1.25x50')
        self.assertEqual(result['source']['unit'], '')
        self.assertEqual(len(result['alternatives']), 2)

    def test_only_unassigned_thread_is_suggested_with_an_object_warning(self):
        row = candidate('dimension.thread', 'M10-6g', 'M10-6g', subject='unassigned_feature')
        result = choose([row], 'product.specification')
        self.assertEqual(result['source']['value'], 'M10-6g')
        self.assertIn('unassigned_object', result['issues'])

    def test_specification_tolerance_case_remains_distinct(self):
        rows = [candidate('product.specification', value, 'Specification: '+value, state='conflict')
                for value in ('M6x1.0-6g', 'M6x1.0-6G')]
        result = choose(rows, 'product.specification')
        self.assertEqual({a['value'] for a in result['alternatives']}, {'M6x1.0-6g', 'M6x1.0-6G'})

    def test_model_text_cannot_support_itself(self):
        row = candidate('material', 'S45C', 'Material: S45C', source='VLM:test')
        self.assertIsNone(choose([row], 'material')['source'])

    def test_model_can_suggest_a_value_only_with_measured_support_even_if_low(self):
        r, v = candidate('material', 'S45C', 'Material: S45C', source='VLM:test')
        _, measured = candidate('material', 'S45C', 'Material: S45C', confidence=.43)
        result = choose_proposal([r], 'material', {v['id']: v, measured['id']: measured})
        self.assertEqual(result['source']['value'], 'S45C')
        self.assertEqual(result['confidence'], .43)
        self.assertIn('model_interpretation', result['issues'])
        self.assertIn('low_confidence', result['issues'])

    def test_a_distant_measured_copy_does_not_ground_model_coordinates(self):
        r, v = candidate('material', 'S45C', 'Material: S45C', source='VLM:test')
        _, measured = candidate('material', 'S45C', 'Material: S45C', box=[.1, .5, .8, .54])
        self.assertIsNone(choose_proposal([r], 'material', {v['id']: v, measured['id']: measured})['source'])

    def test_coating_polymer_is_not_suggested_as_fastener_base_material(self):
        r, e = candidate('material', 'Nylon', 'Material: Nylon', box=[.1, .18, .8, .22])
        heading = evidence(1, [.1, .13, .8, .16], 'Threadlock application:', 'OCR:test')
        self.assertIsNone(choose_proposal([r], 'material', {e['id']: e, heading['id']: heading})['source'])

    def test_recipient_responsibility_and_customer_part_number_are_not_customer(self):
        for value, text in [('Nailok', 'TO: Nailok'), ('456289 is design responsible.', 'Customer: 456289 is design responsible.'),
                            ('AB-123', 'CUSTOMER-PART-#: AB-123')]:
            self.assertIsNone(choose([candidate('customer.name', value, text)], 'customer.name')['source'])

    def test_customer_company_name_cannot_be_truncated(self):
        row = candidate('customer.name', 'ACME', 'Customer: ACME Manufacturing LLC')
        self.assertIsNone(choose([row], 'customer.name')['source'])

    def test_annual_quantity_and_iso_identifier_remain_rejected(self):
        for value, text, unit in [('160000', 'Quantity: 160000 PCS/year', 'PCS'), ('4032', 'ISO 4032', '')]:
            self.assertIsNone(choose([candidate('quotation.quantity', value, text, unit=unit)], 'quotation.quantity')['source'])

    def test_quantity_literal_M_and_price_denominator_stay_unchanged(self):
        for key, value, unit, text in [('quotation.quantity', '160', 'M', 'Quantity: 160 M'),
                                       ('quotation.price', '12.50', 'TWD / PCS', 'Unit Price: 12.50 TWD / PCS')]:
            result = choose([candidate(key, value, text, unit=unit)], key)
            self.assertEqual((result['source']['value'], result['source']['unit']), (value, unit))
        result = choose([candidate('quotation.quantity', '160', 'Quantity: 160 M', unit='M')], 'quotation.quantity')
        self.assertIn('quantity_unit_ambiguous', result['issues'])

    def test_converted_or_replaced_units_are_not_proposals(self):
        for value, unit in [('160000', 'PCS'), ('160', 'PCS')]:
            self.assertIsNone(choose([candidate('quotation.quantity', value, 'Quantity: 160 M', unit=unit)], 'quotation.quantity')['source'])

    def test_blank_missing_and_empty_pending_are_not_zero_values(self):
        rows = [candidate('quotation.price', '', 'Unit Price: / TWD / M', state=state)
                for state in ('blank', 'missing', 'pending_decision')]
        self.assertIsNone(choose(rows, 'quotation.price')['source'])
        result = choose([candidate('quotation.price', '0', 'Unit Price: 0 TWD / PCS', unit='TWD/PCS')], 'quotation.price')
        self.assertEqual(result['source']['value'], '0')

    def test_verified_human_correction_precedes_machine_reading(self):
        manual = candidate('material', 'SUS304', 'Material: S45C', confidence=.45,
                           status='verified', support_verified=True)
        machine = candidate('material', 'S45C', 'Material: S45C', confidence=.99)
        self.assertEqual(choose([machine, manual], 'material')['source']['id'], manual[0]['id'])

    def test_calendar_invalid_date_is_rejected_but_chronology_warning_is_retained(self):
        self.assertIsNone(choose([candidate('quotation.date', '2026-02-30', 'Quote Date: 2026/2/30')], 'quotation.date')['source'])
        row = candidate('quotation.valid_until', '2026-04-16', 'Valid Until: 2026/4/16', skill_issues=['invalid_date_order'])
        result = choose([row], 'quotation.valid_until')
        self.assertEqual(result['source']['value'], '2026-04-16')
        self.assertIn('invalid_date_order', result['issues'])

    def test_malformed_specification_and_unknown_glue_requirement_are_not_values(self):
        for key, value, text in [('product.specification', 'M1 0-6g', 'Specification: M1 0-6g'),
                                 ('adhesive.product', '符合規範之膠種', '膠種：符合規範之膠種')]:
            self.assertIsNone(choose([candidate(key, value, text)], key)['source'])

    def test_complete_engineering_caption_is_suggested_without_shortening_it(self):
        value = 'Button Head Screw M6x1.0x10'
        row = candidate('product.specification', value, value, role_binding='printed_part_caption')
        self.assertEqual(choose([row], 'product.specification')['source']['value'], value)

    def test_method_agreement_helps_equal_printed_values_without_duplicating_facts(self):
        a = candidate('material', 'S45C', 'Material: S45C', confidence=.84, source='OCR:one')
        b = candidate('material', 'S45C', 'Material: S45C', confidence=.84, source='OCR:two', box=[.1, .4, .8, .44])
        single = choose([a], 'material')
        agreement = choose([a, b], 'material')
        self.assertGreater(agreement['score'], single['score'])
        self.assertIn('方法', agreement['reason'])
        self.assertEqual(len(agreement['alternatives']), 2)

    def test_ties_are_stable_by_page_box_and_id_independent_of_input_order(self):
        a = candidate('material', 'S45C', 'Material: S45C', box=[.1, .4, .8, .44])
        b = candidate('material', 'SUS304', 'Material: SUS304', box=[.1, .2, .8, .24])
        self.assertEqual(choose([a, b], 'material')['source']['id'], choose([b, a], 'material')['source']['id'])
        self.assertEqual(choose([a, b], 'material')['source']['id'], b[0]['id'])

    def test_ranking_does_not_change_any_candidate_or_source_region(self):
        rows = [candidate('material', 'S45C', 'Material: S45C', confidence=.51, state='conflict')]
        before = clone(rows)
        choose(rows, 'material')
        self.assertEqual(rows, before)

    def test_printed_coating_process_is_a_tentative_method_without_extent_guess(self):
        row = candidate('coating.method', '塗膠(CT91)', '加工製程名稱：塗膠(CT91)',
            role_binding='printed_coating_process', suggestion_only=True,
            proposal_issues=['coating_extent_unconfirmed'])
        result = choose([row], 'coating.method')
        self.assertEqual(result['source']['value'], '塗膠(CT91)')
        self.assertIn('coating_extent_unconfirmed', result['issues'])

    def test_coating_role_cannot_inject_extent_or_an_unrelated_process(self):
        for value, text in [('全周', '加工製程名稱：塗膠(CT91)'), ('鍛造', '加工製程名稱：鍛造')]:
            row = candidate('coating.method', value, text, role_binding='printed_coating_process', suggestion_only=True)
            self.assertIsNone(choose([row], 'coating.method')['source'])

    def test_unresolved_standard_keeps_damaged_glyph_as_visible_raw_proposal(self):
        value = 'KL acc. to DIN 2ć7-28'
        row = candidate('quality.requirements', value, 'Standard: '+value,
            role_binding='unresolved_printed_standard', suggestion_only=True,
            proposal_issues=['standard_number_requires_review'])
        result = choose([row], 'quality.requirements')
        self.assertEqual(result['source']['value'], value)
        self.assertIn('standard_number_requires_review', result['issues'])

    def test_standard_binding_does_not_repair_glyphs_or_invent_a_heading(self):
        for value, text in [('KL acc. to DIN 267-28', 'Standard: KL acc. to DIN 2ć7-28'),
                            ('DIN 2ć7-28', 'unrelated note: DIN 2ć7-28')]:
            row = candidate('quality.requirements', value, text, role_binding='unresolved_printed_standard', suggestion_only=True)
            self.assertIsNone(choose([row], 'quality.requirements')['source'])

    def test_nonpresent_readings_keep_their_reason_for_review(self):
        for state in ('pending_decision', 'unreadable'):
            row = candidate('quality.requirements', 'Standard: KL acc. to DIN 2ć7-28',
                            'Standard: KL acc. to DIN 2ć7-28', state=state)
            result = choose([row], 'quality.requirements')
            self.assertIsNotNone(result['source'])
            self.assertIn(state+'_candidate', result['issues'])
            self.assertIn('覆核', result['reason'])

    def test_unmarked_distinct_values_are_reported_as_competing_alternatives(self):
        rows = [candidate('material', 'S45C', 'Material: S45C'), candidate('material', 'SUS304', 'Material: SUS304')]
        result = choose(rows, 'material')
        self.assertIn('conflicting_alternatives', result['issues'])

    def test_customer_name_material_word_cannot_become_base_material(self):
        row = candidate('material', 'Handling LLC', 'Customer: Material Handling LLC')
        self.assertIsNone(choose([row], 'material')['source'])

    def test_arbitrary_note_cannot_become_quality_requirement(self):
        row = candidate('quality.requirements', 'Orange', 'Notes: Orange')
        self.assertIsNone(choose([row], 'quality.requirements')['source'])

    def test_split_label_values_receive_explicit_role_ranking(self):
        r, value = candidate('material', 'S45C', 'S45C')
        label = evidence(1, [.03, .2, .09, .24], 'Material', 'OCR:test')
        label['ocr_confidence'] = .95
        r['evidence_ids'] = [label['id'], value['id']]
        result = choose_proposal([r], 'material', {label['id']: label, value['id']: value})
        self.assertIn('明確欄位標籤', result['reason'])

    def test_inline_threadlock_material_is_not_base_material(self):
        row = candidate('material', 'Nylon Torq-Patch', 'Threadlock Material: Nylon Torq-Patch')
        self.assertIsNone(choose([row], 'material')['source'])

    def test_complete_spec_with_printed_mm_is_kept_literal(self):
        value = 'M8x1.25x50 mm'
        result = choose([candidate('product.specification', value, 'Specification: '+value)], 'product.specification')
        self.assertEqual(result['source']['value'], value)

    def test_different_numbered_sizes_do_not_count_as_method_agreement(self):
        rows = [candidate('product.specification', value, 'Specification: '+value, source=engine)
                for value, engine in [('#10-32UNF', 'OCR:one'), ('#12-32UNF', 'OCR:two')]]
        result = choose(rows, 'product.specification')
        self.assertIn('conflicting_alternatives', result['issues'])
        self.assertNotIn('方法讀值一致', result['reason'])

    def test_measured_rfq_issuer_can_be_suggested_when_its_score_is_low(self):
        company = evidence(1, [.1, .03, .8, .06], '測試工業有限公司', 'OCR:test')
        title = evidence(1, [.1, .08, .8, .12], '委外加工詢價單', 'OCR:test')
        recipient = evidence(1, [.1, .14, .8, .18], '此致：台灣耐落', 'OCR:test')
        sources = {e['id']: e for e in (company, title, recipient)}
        for e in sources.values(): e['ocr_confidence'] = .44
        r = requirement('customer.name', company['text'], evidence_ids=list(sources), role_binding='rfq_issuer_to_nailok')
        result = choose_proposal([r], 'customer.name', sources)
        self.assertEqual(result['source']['value'], company['text'])
        self.assertIn('interpreted_customer_role', result['issues'])

    def test_inline_surface_treatment_color_is_not_coating_color(self):
        row = candidate('coating.color', 'black', 'Surface treatment: black Zn; Color: black')
        self.assertIsNone(choose([row], 'coating.color')['source'])

    def test_explicit_coating_color_is_separate_from_inline_finish(self):
        row = candidate('coating.color', 'red', 'Coating Color: red; Finish: black Zn')
        self.assertEqual(choose([row], 'coating.color')['source']['value'], 'red')


if __name__ == '__main__':
    unittest.main()
