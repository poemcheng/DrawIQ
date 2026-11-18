"""Source-role regressions: positive coverage and false-assignment boundaries."""
import unittest
from unittest.mock import patch
from workbench.domain import evidence,requirement,checks
from workbench.engineering import catalogue_candidates,refresh_composites
from workbench.engineering_semantics import semantic_requirements
from workbench.quotation import extract_quotation,grouped_lines
from workbench.quotation_layout import quotation_rows
from workbench.ingest import locate_evidence


def document(regions,requirements=()):
    return {'id':'synthetic-accuracy','version':1,'pages':[{'number':1}],
            'regions':regions,'requirements':list(requirements),'tool_runs':[],
            'quotation_mapping':{},'status':'needs_review'}


class AccuracyTests(unittest.TestCase):
    def rows(self,d): return {r['key']:r for r in quotation_rows(d)}

    def test_named_threadlock_product_not_base_polymer(self):
        e=[evidence(1,[.6,.1,.95,.13],'Theeadlock application:','ocr'),
           evidence(1,[.6,.14,.95,.17],'Malerial: Nylon Torg-Patch Tuflok','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        self.assertEqual(self.rows(d)['adhesive.product']['source']['value'],'Tuflok')
        self.assertIsNone(self.rows(d)['material']['source'])

    def test_for_is_not_an_alternative_product_marker(self):
        e=[evidence(1,[.1,.1,.9,.14],'Adhesive for precote 80','ocr')]
        self.assertEqual(semantic_requirements(e)[0]['value'],'precote 80')

    def test_product_choices_are_not_a_chosen_product(self):
        e=[evidence(1,[.1,.1,.9,.14],'Adhesive: precote 80 or loctite 243','ocr')]
        self.assertEqual(semantic_requirements(e),[])

    def test_full_thread_decimal_comma_is_a_supported_specification(self):
        e=[evidence(1,[.2,.23,.4,.25],'Full Thread','ocr'),
           evidence(1,[.2,.25,.4,.27],'M6x1,0-6g','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        self.assertEqual(self.rows(d)['product.specification']['source']['value'],'M6x1,0-6g')
        d=document([e[1]]); d['requirements']=catalogue_candidates(d)
        self.assertIsNone(self.rows(d)['product.specification']['source'])

    def test_standard_heading_supports_quality(self):
        e=[evidence(1,[.6,.15,.95,.18],'Standard: KL acc. to DIN 267-28','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        self.assertIn('DIN 267-28',self.rows(d)['quality.requirements']['source']['value'])

    def test_quality_keeps_batch_conditions(self):
        e=[evidence(1,[.5,.2,.9,.24],'須符合規範 DIN 267-27 MK','ocr'),
           evidence(1,[.5,.3,.9,.34],'首批需附膠種成分證明，每批需附檢測報告','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        r=self.rows(d)['quality.requirements']['source']
        self.assertIn('首批需附膠種成分證明',r['value']); self.assertIn('每批需附檢測報告',r['value'])
        component=next(x for x in d['requirements'] if x['key']=='document.every_batch')
        component['value']='材料證明'; refresh_composites(d)
        self.assertIn('每批需附材料證明',r['value'])

    def test_distinct_torque_conditions_and_operations_do_not_conflict(self):
        e=[]
        for y,text in [(.1,'有負載：'),(.13,'鎖緊扭力：23Nm'),(.16,'破壞扭力：20.7Nm以上'),
                       (.19,'鬆脫扭力：26Nm以下'),(.23,'沒有負载：'),(.26,'鎖入扭力：1.5Nm以下'),
                       (.29,'破壞扭力：4Nm以上'),(.32,'鬆脫扭力：26Nm以下')]:
            e.append(evidence(1,[.1,y,.4,y+.025],text,'ocr'))
        rows=semantic_requirements(e)
        self.assertEqual(len(rows),6); self.assertEqual(len({r['key'] for r in rows}),6)
        self.assertTrue(all(r['value'].replace('.','').isdigit() for r in rows))
        self.assertFalse(any(x['code'] in ('numeric','cross_source_conflict') for x in checks(document(e,rows))))

    def test_rfq_issuer_requires_three_measured_anchors(self):
        e=[evidence(1,[.1,.1,.6,.13],'甲方工業股份有限公司','ocr'),
           evidence(1,[.2,.16,.7,.19],'委外加工詢價單','ocr'),
           evidence(1,[.1,.21,.7,.24],'此致：台灣耐落王經理','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        r=self.rows(d)['customer.name']['source']
        self.assertEqual(r['value'],'甲方工業股份有限公司'); self.assertEqual(len(r['evidence_ids']),3)
        for absent in range(3):
            subset=[x for i,x in enumerate(e) if i!=absent]; d=document(subset)
            d['requirements']=catalogue_candidates(d); self.assertIsNone(self.rows(d)['customer.name']['source'])

    def test_competing_issuer_names_require_selection(self):
        e=[evidence(1,[.1,.1,.6,.13],'甲方有限公司','ocr'),evidence(1,[.6,.1,.9,.13],'乙方有限公司','ocr'),
           evidence(1,[.2,.16,.7,.19],'詢價單','ocr'),evidence(1,[.1,.21,.7,.24],'TO: 台灣耐落','ocr')]
        d=document(e); d['requirements']=catalogue_candidates(d)
        self.assertIsNone(self.rows(d)['customer.name']['source'])

    def test_model_authored_text_does_not_self_certify(self):
        e=evidence(1,[.1,.2,.9,.24],'Quantity: 160 PCS','VLM:test')
        r=requirement('quotation.quantity','160',unit='PCS',evidence_ids=[e['id']])
        d=document([e],[r]); self.assertIsNone(self.rows(d)['quotation.quantity']['source'])
        self.assertIn('實測',self.rows(d)['quotation.quantity']['rejected_candidates'][0]['reason'])
        d['regions'].append(evidence(1,[.1,.2,.9,.24],e['text'],'ocr'))
        self.assertIsNone(self.rows(d)['quotation.quantity']['source'])
        d['regions'][-1]['ocr_confidence']=.99
        self.assertEqual(self.rows(d)['quotation.quantity']['source']['id'],r['id'])

    def test_model_same_text_at_other_location_is_not_an_anchor(self):
        e=evidence(1,[.1,.2,.9,.24],'Material: S45C','VLM:test')
        r=requirement('material','S45C',evidence_ids=[e['id']])
        d=document([e,evidence(1,[.1,.6,.9,.64],e['text'],'ocr')],[r])
        self.assertIsNone(self.rows(d)['material']['source'])

    def test_repair_applies_to_label_only_and_remains_grounded(self):
        e=evidence(1,[.1,.2,.9,.24],'専案代號：TRIAL-001','ocr')
        rows=extract_quotation([e],detected=True,include_missing=False)
        self.assertEqual(self.rows(document([e],rows))['quotation.project']['source']['value'],'TRIAL-001')
        rows[0]['value']='TRIAL-009'; self.assertIsNone(self.rows(document([e],rows))['quotation.project']['source'])

    def test_repeated_readings_are_not_concatenated(self):
        e=[evidence(1,[.1,.2,.9,.24],'客戶：甲公司','ocr'),evidence(1,[.1,.201,.9,.241],'客戶：乙公司','ocr:reread')]
        self.assertEqual(len(grouped_lines(e)),2)
        self.assertEqual([r['value'] for r in extract_quotation(e,detected=True,include_missing=False)],['甲公司','乙公司'])

    def test_multi_field_row_retains_specific_fragment_evidence(self):
        e=[evidence(1,[.1,.2,.25,.24],'數量：','ocr'),evidence(1,[.26,.2,.39,.24],'160 PCS','ocr'),
           evidence(1,[.5,.2,.65,.24],'報價：','ocr'),evidence(1,[.66,.2,.9,.24],'12.50 TWD/PCS','ocr')]
        rows=extract_quotation(e,detected=True,include_missing=False)
        self.assertEqual(next(r for r in rows if r['key']=='quotation.quantity')['evidence_ids'],[e[0]['id'],e[1]['id']])
        self.assertEqual(next(r for r in rows if r['key']=='quotation.price')['evidence_ids'],[e[2]['id'],e[3]['id']])

    def test_ambiguous_numeric_evidence_stays_unassigned(self):
        e=[evidence(1,[.1,.2,.3,.24],'26','ocr'),evidence(1,[.1,.3,.3,.34],'26','ocr')]
        self.assertEqual(locate_evidence(document(e),1,'26'),[])

    def test_date_and_decimal_format_duplicates_do_not_prevent_fill(self):
        for key,values,raw in [('quotation.date',['2026/9/16','2026-09-16'],'報價日期：2026/9/16'),
                               ('quotation.price',['12.5','12.50'],'報價：12.50 TWD/PCS')]:
            e=evidence(1,[.1,.2,.9,.24],raw,'ocr')
            d=document([e],[requirement(key,x,evidence_ids=[e['id']]) for x in values])
            self.assertIsNotNone(self.rows(d)[key]['source'])

    def test_model_inventory_queries_capabilities_from_show(self):
        from workbench.extraction import models
        with patch('workbench.extraction.local_request',side_effect=[{'models':[{'name':'actual'}]}, {'capabilities':['vision']}]) as request:
            self.assertEqual(models()[0]['capabilities'],['vision'])
            self.assertTrue(request.call_args.args[0].endswith('/api/show'))


if __name__=='__main__': unittest.main()
