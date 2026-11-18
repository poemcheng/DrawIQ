"""Uploaded skill rules must govern the actual three-pane quotation projection."""
import json
import unittest
import zipfile
from workbench.domain import evidence,requirement
from workbench.engineering import catalogue_candidates
from workbench.quotation_layout import quotation_rows
from workbench.exporting import export_bundle
from tests.test_workbench import Fixture


def drawing(lines):
    regions=[]
    for i,(text,score) in enumerate(lines):
        e=evidence(1,[.1,.1+i*.06,.9,.14+i*.06],text,'OCR:test')
        e['ocr_confidence']=score; regions.append(e)
    return {'id':'synthetic-skill','version':1,'name':'rule-case','status':'needs_review',
            'pages':[{'number':1,'width':1000,'height':1000}],
            'regions':regions,'requirements':[],'tool_runs':[],'warnings':[]}


def rows(doc):
    doc['requirements'].extend(catalogue_candidates(doc))
    return {row['key']:row for row in quotation_rows(doc)}


class SkillProjectionTests(unittest.TestCase):
    def test_uploaded_coating_alias_keeps_product_suffix_in_actual_quotation(self):
        d=drawing([('報價單號：DQ26004001',.99),('報價級別：precoat 80-8(紅)',.97)])
        source=rows(d)['adhesive.product']['source']
        self.assertIsNotNone(source)
        self.assertEqual(source['value'],'precoat 80-8(紅)')

    def test_coating_grades_do_not_merge_when_color_is_equivalent(self):
        d=drawing([('報價級別：precoat 80-8(紅)',.99),('報價級別：precoat 80(紅色)',.99)])
        row=rows(d)['adhesive.product']
        self.assertIsNone(row['source']); self.assertGreaterEqual(len(row['candidate_ids']),2)

    def test_known_low_ocr_confidence_cannot_bypass_skill_through_legacy_candidate(self):
        d=drawing([('材質：S45C',.45)])
        d['requirements']=[requirement('material','S45C',evidence_ids=[d['regions'][0]['id']])]
        row=rows(d)['material']
        self.assertIsNone(row['source']); self.assertTrue(row['rejected_candidates'])
        self.assertIn('信心',row['rejected_candidates'][0]['reason'])

    def test_verified_human_source_is_not_replaced_by_low_score_rule(self):
        d=drawing([('材質：S45C',.45)])
        r=requirement('material','S45C',evidence_ids=[d['regions'][0]['id']],status='verified',support_verified=True)
        d['requirements']=[r]; d['quotation_mapping']={'material':r['id']}
        self.assertEqual(rows(d)['material']['source']['id'],r['id'])

    def test_model_cannot_hide_a_low_confidence_measured_anchor(self):
        d=drawing([('Material: S45C',.45)])
        measured=d['regions'][0]
        generated=evidence(1,measured['box'],measured['text'],'VLM:test')
        d['regions'].append(generated)
        d['requirements']=[requirement('material','S45C',evidence_ids=[generated['id']])]
        row=rows(d)['material']
        self.assertIsNone(row['source'])
        self.assertTrue(any('信心' in x['reason'] for x in row['rejected_candidates']))

    def test_expiry_before_quotation_is_held_for_review(self):
        d=drawing([('報價日期：2026/5/16',.99),('報價有效日期：2026/4/16',.99)])
        row=rows(d)['quotation.valid_until']
        self.assertIsNone(row['source'])
        self.assertTrue(row['candidate_ids'])

    def test_split_date_label_value_cannot_bypass_chronology(self):
        d=drawing([('報價日期',.99),('2026/5/16',.99),('報價有效日期：2026/4/16',.99)])
        d['regions'][0]['box']=[.1,.1,.3,.14]
        d['regions'][1]['box']=[.32,.1,.55,.14]
        e=d['regions'][2]
        r=requirement('quotation.valid_until','2026-04-16',evidence_ids=[e['id']])
        d['requirements']=[r]
        self.assertIsNone(rows(d)['quotation.valid_until']['source'])

    def test_other_supplier_quote_number_is_warning_not_rejection(self):
        d=drawing([('報價單號：OTHER-2026-001',.99)])
        self.assertEqual(rows(d)['quotation.number']['source']['value'],'OTHER-2026-001')

    def test_unanchored_thread_is_retained_as_candidate_without_length_guess(self):
        d=drawing([('M10-6g',.99)])
        row=rows(d)['product.specification']
        self.assertIsNone(row['source'])
        candidates=[r for r in d['requirements'] if r.get('skill_normalized',{}).get('tolerance')=='6g']
        self.assertTrue(candidates)
        self.assertIsNone(candidates[0]['skill_normalized']['pitch_mm'])
        self.assertIsNone(candidates[0]['skill_normalized']['length_mm'])

    def test_price_separator_spaces_are_not_a_unit_conflict(self):
        d=drawing([('報價：12.50 TWD / PCS',.99)])
        d['requirements']=[requirement('quotation.price','12.50',unit='TWD/PCS',
                                      evidence_ids=[d['regions'][0]['id']])]
        row=rows(d)['quotation.price']; self.assertIsNotNone(row['source'])
        self.assertEqual(row['source']['value'],'12.50')

    def test_unknown_quantity_units_remain_distinct(self):
        d=drawing([('數量：10 M',.99),('數量：10 K',.99)])
        self.assertIsNone(rows(d)['quotation.quantity']['source'])


class SkillBundleTests(Fixture):
    def test_reanalysis_keeps_price_raw_and_remaps_model_crop_sources(self):
        from workbench.domain import clone
        from workbench.extraction import apply_analysis
        current=clone(self.doc)
        ev=current['regions'][0]; ev['text']='報價：12.50 TWD / PCS'
        r=current['requirements'][0]; r.update(key='quotation.price',value='12.50',unit='TWD/PCS')
        current=self.store.mutate(current['id'],1,'analysis','price fixture',lambda d:d.update(current))
        output=clone(current); output['regions'][0]['id']='incoming-region'; output['requirements'][0]['id']='incoming-req'
        output['requirements'][0]['unit']='TWD / PCS'
        output['requirements'][0]['evidence_ids']=['incoming-region']
        output['requirements'][0]['candidates']=[{'source':'test','value':'12.50','evidence_ids':['incoming-region']}]
        output['warnings']=[]
        output['tool_runs']=[{'id':'roi-test','vlm_input_region_ids':['incoming-region'],
                             'vlm_region_plans':[{'region_ids':['incoming-region'],'box':[.1,.1,.4,.4]}]}]
        result=apply_analysis(self.store,current['id'],current['version'],output)
        actual=next(x for x in result['requirements'] if x['id']==r['id'])
        self.assertEqual(actual['state'],'present'); self.assertEqual(actual['unit'],'TWD/PCS')
        self.assertEqual(actual['candidates'][0]['evidence_ids'],[ev['id']])
        run=result['tool_runs'][-1]
        self.assertEqual(run['vlm_input_region_ids'],[ev['id']])
        self.assertEqual(run['input_region_ids_before_persistence'],['incoming-region'])
        self.assertEqual(run['vlm_region_plans'][0]['region_ids'],[ev['id']])

    def test_draft_bundle_contains_machine_records_and_review_queue(self):
        path=export_bundle(self.store,self.doc['id'])
        with zipfile.ZipFile(path) as archive:
            names=set(archive.namelist())
            for expected in ('skill/results.jsonl','skill/review_queue.jsonl','skill/summary.json'):
                self.assertIn(expected,names)
            result=json.loads(archive.read('skill/results.jsonl').decode('utf-8').splitlines()[0])
        self.assertEqual(result['document_id'],self.doc['id'])
        self.assertIsNone(result['fields']['quotation.price']['normalized'])
        self.assertEqual(self.store.get(self.doc['id']),self.doc)


if __name__=='__main__': unittest.main()
