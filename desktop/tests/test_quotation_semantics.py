"""Regression cases transcribed from the supplied error and existing OCR text."""
import io
import zipfile
from unittest.mock import patch
from openpyxl import load_workbook
from workbench.domain import clone, evidence, requirement
from workbench.extraction import analyze
from workbench.extraction_quality import field_issue
from workbench.quotation import extract_quotation
from workbench.quotation_layout import quotation_rows
from workbench.exporting import export_bundle
from workbench.ingest import sha256
from tests.test_workbench import Fixture
from tests.test_quotation_workspace import grounded_fact, add_grounded


class QuotationSemanticTests(Fixture):
    def row(self, doc, key): return next(f for f in quotation_rows(doc) if f['key']==key)

    def test_responsibility_sentence_is_never_a_customer_even_if_value_was_trimmed(self):
        for raw,value in [
            ('Customer 456289 is design responsible.','456289 is design responsible.'),
            ('Customer 101363 is design rèsponsible:','Customer 101363'),
            ('Customer 456289 is design responsibte.','456289'),
            ('Customer: 456289 is manufacturing responsible.','456289'),
            ('客戶：456289 負責設計','456289')]:
            for key in ('customer','customer.name'):
                with self.subTest(raw=raw,key=key):
                    d=clone(self.doc); grounded_fact(d,key,value,label='客戶',raw=raw)
                    before=clone(d); row=self.row(d,'customer.name')
                    self.assertIsNone(row['source']); self.assertEqual(row['status'],'需判定')
                    self.assertIn('責任',row['rejected_candidates'][0]['reason'])
                    self.assertEqual(d,before)

    def test_customer_part_number_recipient_and_placeholder_are_not_customer_names(self):
        for raw,value in [('CUSTOMER-PART-#: TBE','PART-#: TBE'),
                          ('TO: 台灣耐落 王先生','台灣耐落 王先生'),
                          ('您好：下述需求請惠賜報價，感謝。','下述需求'),
                          ('三價黑鋅鎳8u(未指定規範)','未指定')]:
            with self.subTest(raw=raw):
                d=clone(self.doc); grounded_fact(d,'customer.name',value,raw=raw)
                self.assertIsNone(self.row(d,'customer.name')['source'])
                self.assertTrue(field_issue('customer',value,raw))

    def test_real_customer_name_or_customer_code_is_still_supported(self):
        for value in ['試用教學有限公司','A244','ACME Manufacturing LLC','Responsible Manufacturing LLC']:
            with self.subTest(value=value):
                d=clone(self.doc); r=grounded_fact(d,'customer.name',value,raw='Customer: '+value)
                self.assertEqual(self.row(d,'customer.name')['source']['id'],r['id'])

    def test_orthography_and_date_normalization_keep_real_evidence(self):
        d=clone(self.doc)
        customer=grounded_fact(d,'customer.name','测试甲公司',raw='客 户 ： 测试甲公司')
        dated=grounded_fact(d,'quotation.date','2026-09-16',raw='報價日期：2026/9/16')
        self.assertEqual(self.row(d,'customer.name')['source']['id'],customer['id'])
        self.assertEqual(self.row(d,'quotation.date')['source']['id'],dated['id'])

    def test_thread_finish_grade_and_heading_are_not_material(self):
        for raw,value in [('M8x1.25-6g / .25','M8×1.25-6g'),
                          ('Button Head Torx Screw Flanged-M6x12','M6x12按钮头螺丝带法兰'),
                          ('MATERIAL, DIMENSION, TYPE, ETC.',', DIMENSION, TYPE, ETC.'),
                          ('未標示公差：','未標示'),
                          ('Material: 10.9','10.9'),
                          ('ZINC PLLATED CR+3, MIN.5MU','ZINC PLLATED CR+3'),
                          ('W/Nylon','W/Nylon'),
                          ('SEE NOTES / MATERIAL / MATERIAL NOTES:','NOTES'),
                          ('WAS [1.9: MAY O2I.7 WAS C24.: NOTES I','C24')]:
            with self.subTest(value=value):
                d=clone(self.doc); grounded_fact(d,'material',value,raw=raw)
                self.assertIsNone(self.row(d,'material')['source'])

    def test_materials_need_printed_support_and_valid_material_context(self):
        for raw,value in [('Material: S45C','S45C'),('材質：10B33 10.9級','10B33 10.9級'),
                          ('Material: Nylon Torg-Patch Tuflok','Nylon'),('STEEL PER ISO 898-1','STEEL')]:
            with self.subTest(raw=raw):
                d=clone(self.doc); r=grounded_fact(d,'material',value,raw=raw)
                self.assertEqual(self.row(d,'material')['source']['id'],r['id'])
        d=clone(self.doc); grounded_fact(d,'material','S45C',raw='Material: SUS304')
        self.assertIsNone(self.row(d,'material')['source'])
        self.assertIn('未支持',self.row(d,'material')['rejected_candidates'][0]['reason'])

    def test_temperature_is_not_a_named_adhesive(self):
        for value in ['(最高工作溫度可達135℃)','（耐溫135℃）','135℃','Temperature 135 C']:
            with self.subTest(value=value):
                d=clone(self.doc); grounded_fact(d,'adhesive.product',value,label='膠種')
                self.assertIsNone(self.row(d,'adhesive.product')['source'])
        d=clone(self.doc); r=grounded_fact(d,'adhesive.product','precote 80',label='膠種')
        self.assertEqual(self.row(d,'adhesive.product')['source']['id'],r['id'])

    def test_same_box_finish_color_cannot_become_coating_color(self):
        d=clone(self.doc)
        grounded_fact(d,'coating.color','black',raw='Surface treatment: black Zn; Color: black')
        self.assertIsNone(self.row(d,'coating.color')['source'])
        self.assertIsNone(next(f for f in quotation_rows(d,include_proposals=True) if f['key']=='coating.color')['source'])
        d=clone(self.doc)
        r=grounded_fact(d,'coating.color','red',raw='Coating Color: red; Finish: black Zn')
        self.assertEqual(self.row(d,'coating.color')['source']['id'],r['id'])

    def test_numbers_must_match_exactly_and_cannot_come_from_another_field(self):
        for key,value,raw in [('quotation.quantity','90','數量：390 PCS'),
                              ('quotation.price','12','報價：12.50 TWD/PCS'),
                              ('quotation.quantity','160','螺紋：M160'),
                              ('quotation.price','160','數量：160 PCS')]:
            with self.subTest(raw=raw):
                d=clone(self.doc); grounded_fact(d,key,value,raw=raw)
                self.assertIsNone(self.row(d,key)['source'])

    def test_unit_currency_and_date_cannot_be_borrowed_from_neighboring_fields(self):
        for key,value,unit,raw in [
            ('quotation.quantity','160','PCS','數量：160 M'),
            ('quotation.price','12.50','TWD/PCS','報價：12.50 USD/EA'),
            ('quotation.quantity','90','PCS','數量：390 PCS  報價：90 TWD/PCS'),
            ('quotation.date','2026-10-16','','報價日期：2026/9/16  報價有效日期：2026/10/16'),
            ('customer.name','ACME','','Customer: ACME Manufacturing LLC')]:
            with self.subTest(key=key,raw=raw):
                d=clone(self.doc); grounded_fact(d,key,value,unit=unit,raw=raw)
                self.assertIsNone(self.row(d,key)['source'])
        d=clone(self.doc)
        r=grounded_fact(d,'quotation.quantity','160',unit='M',raw='數量：160 M')
        self.assertEqual(self.row(d,'quotation.quantity')['source']['id'],r['id'])

    def test_legacy_generic_quantity_label_does_not_invent_an_annual_scope(self):
        d=clone(self.doc)
        r=grounded_fact(d,'quantity','160',unit='PCS',label='需求／計畫數量',raw='Quantity: 160 PCS')
        self.assertEqual(self.row(d,'quotation.quantity')['source']['id'],r['id'])
        d=clone(self.doc)
        grounded_fact(d,'quantity','45000支',label='需求／計畫數量',raw='45000支')
        row=self.row(d,'quotation.quantity')
        self.assertIsNone(row['source']); self.assertNotIn('年度',row['rejected_candidates'][0]['reason'])

    def test_editing_an_auto_source_retains_reference_and_records_the_choice(self):
        d,r=add_grounded(self.store,self.doc,'material','S45C')
        d=self.store.edit_requirement(d['id'],d['version'],r['id'],{'value':'SUS304'},'Synthetic OCR correction')
        self.assertEqual(d['quotation_mapping']['material'],r['id'])
        self.assertEqual(self.row(d,'material')['source']['value'],'SUS304')
        self.assertEqual(self.store.history(d['id'])[0]['detail']['quotation_fields'],['material'])
        self.assertTrue(self.store.verify_audit(d['id']))

    def test_quotation_parser_preserves_bad_customer_as_pending_candidate(self):
        regions=[evidence(1,[.1,.1,.9,.14],'報價單號：Q-SYNTHETIC'),
                 evidence(1,[.1,.2,.9,.24],'Customer: 456289 is design responsible.')]
        r=next(r for r in extract_quotation(regions) if r['key']=='customer.name')
        self.assertEqual(r['value'],''); self.assertEqual(r['state'],'pending_decision')
        self.assertIn('456289',r['candidates'][0]['value'])
        self.assertIn('責任',r['extraction_notes'][0])

    def test_fresh_ocr_analysis_does_not_create_a_customer_from_responsibility_text(self):
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        text='Customer 456289 is design responsible.'
        ocr=OCRResult(text,[TextRegion(text,(20,40,360,70),.95,'PDF文字層')],'PDF文字層')
        with patch('drawing_intelligence.ocr.recognize_page',return_value=ocr):
            result=analyze(self.doc,self.store.root)
        self.assertFalse(any(r['key'] in ('customer','customer.name') and r['value'] for r in result['requirements']))
        self.assertTrue(any(r['key']=='note' and text in r['value'] for r in result['requirements']))
        self.assertTrue(any('責任' in x['reason'] for run in result['tool_runs'] for x in run.get('rejected_candidates',[])))

    def test_export_does_not_publish_the_misclassified_customer(self):
        d,_=add_grounded(self.store,self.doc,'customer.name','456289 is design responsible.',
                         raw='Customer 456289 is design responsible.')
        path=export_bundle(self.store,d['id'])
        with zipfile.ZipFile(path) as z:
            book=load_workbook(io.BytesIO(z.read('knowledge.xlsx')))
            row=next(r for r in book['報價資訊'].values if r[1]=='customer.name')
            self.assertIsNone(row[2]); self.assertEqual(row[4],'需判定')
            self.assertIn('456289',z.read('requirements.csv').decode('utf-8-sig'))

    def test_changed_quote_projection_does_not_reuse_an_old_draft_at_same_version(self):
        d,r=add_grounded(self.store,self.doc,'customer.name','456289 is design responsible.',
                         raw='Customer 456289 is design responsible.')
        obsolete=quotation_rows(d)
        old_customer=next(x for x in obsolete if x['key']=='customer.name')
        old_customer.update(source=r,status='待覆核')
        with patch('workbench.quotation_layout.quotation_rows',return_value=obsolete):
            old=export_bundle(self.store,d['id'])
        old_hash=sha256(old); corrected=export_bundle(self.store,d['id'])
        self.assertNotEqual(old,corrected); self.assertEqual(sha256(old),old_hash)
        self.assertEqual(export_bundle(self.store,d['id']),corrected)
        self.assertEqual(self.store.get(d['id']),d)
