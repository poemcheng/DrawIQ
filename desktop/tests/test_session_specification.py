import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io,json,zipfile
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from openpyxl import load_workbook
from tests.test_workbench import Fixture
from workbench.domain import clone,requirement
from workbench.business import BusinessService,calculate_quote,COST_FIELDS
from workbench.specification import (default_spec,spec_status,spec_issues,quote_issues,template_items,PROFILES)
from workbench.session_files import save_result_set,load_result_set
from workbench.store import Store,ConflictError
from workbench.ui import MainWindow

class SessionTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def test_startup_does_not_query_existing_documents_or_strategies(self):
        with patch.object(Store,'list_documents',side_effect=AssertionError('startup must not load documents')):
            w=MainWindow(self.store.root); self.assertIsNone(w.doc); self.assertEqual(w.documents.count(),0)
            self.assertIs(w.content_stack.currentWidget(),w.home); self.assertEqual(w.loaded_document_ids,set()); w.close()
    def test_load_expands_source_catalogue_and_unload_preserves_original_facts(self):
        w=MainWindow(self.store.root); before=self.store.get(self.doc['id']); events=self.store.history(self.doc['id'])
        w.load_documents(document_ids={self.doc['id']}); self.assertEqual(w.doc['id'],self.doc['id']); self.assertEqual(w.documents.count(),1)
        w.unload_documents(); self.assertIsNone(w.doc); self.assertIsNone(w.business_page.case); self.assertEqual(w.documents.count(),0)
        after=self.store.get(self.doc['id'])
        self.assertEqual(after['regions'],before['regions'])
        self.assertEqual(after['requirements'][:len(before['requirements'])],before['requirements'])
        self.assertEqual(self.store.history(self.doc['id'])[-len(events):],events)
        self.assertEqual(self.store.history(self.doc['id'])[0]['action'],'candidate_catalogue')
        self.assertTrue(self.store.verify_audit(self.doc['id']))
        w.close(); again=MainWindow(self.store.root); self.assertIsNone(again.doc); again.close()
    def test_dirty_review_and_business_prevent_unload(self):
        w=MainWindow(self.store.root); w.load_documents(); w.value.setPlainText('UNSAVED')
        with self.assertRaises(ValueError): w.unload_documents()
        w.reset_form(); w.business_page.rfq_fields['quantity'].setText('20')
        with self.assertRaises(ValueError): w.unload_documents()
        w.business_page.reset(); w.close()
    def test_bookmark_loads_latest_without_reinterpreting_or_mutating(self):
        path=self.root/'results.drawiq.json'; save_result_set(self.store,[self.doc['id']],path)
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'10'},'synthetic correction')
        self.assertEqual(load_result_set(self.store,path),[d['id']]); self.assertEqual(self.store.get(d['id']),d)
    def test_bookmark_mismatched_source_missing_id_and_future_version_are_rejected(self):
        path=self.root/'set.json'; baseline=save_result_set(self.store,[self.doc['id']],path)
        for field,value in [('source_sha256','bad'),('id','missing'),('version',999)]:
            data=clone(baseline); data['documents'][0][field]=value; path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(ValueError): load_result_set(self.store,path)
    def test_bookmark_cannot_contain_no_documents(self):
        with self.assertRaises(ValueError): save_result_set(self.store,[],self.root/'empty.json')
    def test_import_navigation_does_not_load_unselected_library(self):
        r=clone(self.doc); r['id']='different'; r['name']='different.pdf'; self.store.add(r)
        w=MainWindow(self.store.root); w.load_documents(select_id='different')
        self.assertEqual(w.documents.count(),1); self.assertEqual(w.loaded_document_ids,{'different'}); w.close()

class SpecificationTests(Fixture):
    def case(self,approved=True):
        if approved: self.approved()
        service=BusinessService(self.store); case=service.get(self.doc['id'])
        case['rfq'].update(customer='SYNTHETIC CUSTOMER',project='TEST',quantity='100',unit='PCS',due_date='2099-12-01')
        spec=case['specification']; spec.update(form_number='TEST-001',product_name='TEST PART',drawing_number='DRAW-TEST',drawing_revision='A')
        for item in spec['items']: item.update(state='confirmed',value='Test requirement',acceptance='Test inspection method and limit')
        case['quote'].update(currency='TWD',unit_costs={k:'1' for k in COST_FIELDS},setup_cost='100',margin_percent='20')
        case['commercial'].update(quote_number='Q-001',quote_date=date.today().isoformat(),valid_until=(date.today()+timedelta(days=30)).isoformat(),payment_terms='TEST NET 30',delivery_terms='TEST LOCATION',lead_time='30 days after confirmation',tax_percent='5',price_basis_qty='100',moq='10')
        return service,case
    def test_all_four_templates_require_explicit_decisions(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                spec=default_spec(self.doc,profile)
                self.assertTrue(all(row['state']=='pending' for row in spec['items']))
                self.assertGreaterEqual(len(spec['items']),6)
        quote_doc=clone(self.doc); source=clone(self.req)
        source.update(key='product.specification',label='規格',value='M8x1.25x50',unit='')
        quote_doc['requirements']=[source]
        dimension=next(row for row in template_items(quote_doc,'manufacturing') if row['key']=='dimensions')
        self.assertIn('M8x1.25x50',dimension['value']); self.assertEqual(dimension['requirement_ids'],[source['id']])
        self.assertEqual(dimension['state'],'pending')
    def test_draft_cannot_self_assert_internal_or_customer_confirmation(self):
        service,case=self.case(); case['specification']['review']={'fingerprint':'forged'}; case['specification']['customer_confirmation']={'name':'forged'}
        c=service.save(case); self.assertIsNone(c['specification']['review']); self.assertIsNone(c['specification']['customer_confirmation'])
    def test_confirmation_requires_approved_source(self):
        service,case=self.case(approved=False); c=service.save(case)
        with self.assertRaisesRegex(ValueError,'尚未核准'): service.confirm_specification(c['document_id'],c['version'])
    def test_pending_open_issue_and_na_without_reason_block_confirmation(self):
        service,case=self.case(); base=clone(case)
        for variant in ('pending','issue','na'):
            c=clone(base); c['version']=case['version']
            if variant=='pending': c['specification']['items'][0]['state']='pending'
            if variant=='issue': c['specification']['open_issues']='Need customer clarification'
            if variant=='na': c['specification']['items'][0].update(state='not_applicable',reason='')
            c=service.save(c)
            message={'pending':'尚待確認','issue':'待確認事項','na':'不適用但未填原因'}[variant]
            with self.assertRaisesRegex(ValueError,message): service.confirm_specification(c['document_id'],c['version'])
            case=c
    def test_required_template_items_cannot_be_removed(self):
        service,c=self.case(); c['specification']['items'].pop()
        with self.assertRaises(ValueError): service.save(c)
    def test_acceptance_criteria_are_required_not_only_a_checkbox(self):
        service,c=self.case(); c['specification']['items'][0]['acceptance']=''; c=service.save(c)
        with self.assertRaisesRegex(ValueError,'驗收'): service.confirm_specification(c['document_id'],c['version'])
    def test_confirm_and_record_customer_reference_then_noop_save_preserves_both(self):
        service,c=self.case(); c=service.save(c); c=service.confirm_specification(c['document_id'],c['version'])
        self.assertEqual(spec_status(c),'內部規格已確認')
        c=service.confirm_specification(c['document_id'],c['version'],customer='Test Customer',reference='TEST REPLY 001',confirmed_on=date.today().isoformat())
        self.assertEqual(spec_status(c),'已記錄客戶確認'); self.assertEqual(service.save(c),c)
    def test_customer_requires_internal_review_reference_and_nonfuture_date(self):
        service,c=self.case(); c=service.save(c)
        with self.assertRaises(ValueError): service.confirm_specification(c['document_id'],c['version'],customer='Test',reference='mail',confirmed_on=date.today().isoformat())
        c=service.confirm_specification(c['document_id'],c['version'])
        for name,reference,day in [('', 'mail',date.today().isoformat()),('test','',date.today().isoformat()),('test','mail','2099-01-01')]:
            with self.assertRaises(ValueError): service.confirm_specification(c['document_id'],c['version'],customer=name,reference=reference,confirmed_on=day)
    def test_rfq_quantity_change_invalidates_spec_review_cost_change_does_not(self):
        service,c=self.case(); c=service.save(c); c=service.confirm_specification(c['document_id'],c['version'])
        c['quote']['unit_costs']['material']='2'; c=service.save(c); self.assertEqual(spec_status(c),'內部規格已確認')
        c['rfq']['quantity']='200'; c=service.save(c); self.assertEqual(spec_status(c),'規格草稿／待確認')
    def test_source_revision_and_stale_business_version_block_confirmation(self):
        service,c=self.case(); c=service.save(c)
        with self.assertRaises(ConflictError): service.confirm_specification(c['document_id'],c['version']-1)
        doc=self.store.get(c['document_id']); self.store.edit_requirement(doc['id'],doc['version'],self.req['id'],{'value':'10'},'changed')
        with self.assertRaises(ValueError): service.confirm_specification(c['document_id'],c['version'])
        c=service.rebase(c['document_id']); self.assertTrue(all(row['state']=='pending' for row in c['specification']['items'])); self.assertIsNone(c['specification']['review'])
    def test_invalid_source_link_is_not_saved(self):
        service,c=self.case(); c['specification']['items'][0]['requirement_ids']=['missing']
        with self.assertRaises(ValueError): service.save(c)
    def test_price_basis_and_tax_do_not_change_per_unit_cost_math(self):
        service,c=self.case(); result=calculate_quote(c)
        self.assertEqual(result['unit_quote'],'7.5000'); self.assertEqual(result['basis_quote'],'750.0000')
        self.assertEqual(result['total_quote'],'750.00'); self.assertEqual(result['tax_amount'],'37.50'); self.assertEqual(result['total_with_tax'],'787.50')
        c['rfq']['unit']='M'; self.assertEqual(calculate_quote(c)['quantity'],'100'); self.assertEqual(calculate_quote(c)['unit'],'M')
    def test_unknown_tax_is_not_treated_as_zero(self):
        service,c=self.case(); c['commercial']['tax_percent']=''; result=calculate_quote(c)
        self.assertIsNone(result['tax_amount']); self.assertIsNone(result['total_with_tax'])
        self.assertTrue(any('稅率' in issue for issue in quote_issues(c,self.store.get(c['document_id']))))
    def test_invalid_dates_tax_and_basis_rejected_on_save(self):
        service,case=self.case()
        for key,value in [('quote_date','bad'),('valid_until','2000-01-01'),('tax_percent','101'),('price_basis_qty','0'),('moq','-1')]:
            c=clone(case); c['commercial'][key]=value
            with self.assertRaises(ValueError): service.save(c)
    def test_quote_readiness_requires_spec_review_and_moq_validity(self):
        service,c=self.case(); doc=self.store.get(c['document_id']); self.assertTrue(quote_issues(c,doc))
        c=service.save(c); c=service.confirm_specification(c['document_id'],c['version']); self.assertEqual(quote_issues(c,doc),[])
        c['commercial']['moq']='200'; self.assertTrue(any('最低' in x for x in quote_issues(c,doc)))
        c['commercial']['valid_until']='2001-01-01'; self.assertTrue(any('過期' in x for x in quote_issues(c,doc)))
    def test_export_contains_specification_sources_criteria_and_safe_text_cells(self):
        service,c=self.case(); c['specification']['product_name']='<script>alert(1)</script>'; c['specification']['items'][0]['value']='=1+1'; c=service.save(c)
        path=service.export(c['document_id'])
        with zipfile.ZipFile(path) as z:
            sheet=z.read('規格確認單.html').decode(); self.assertIn('&lt;script&gt;',sheet); self.assertNotIn('<script>alert',sheet)
            data=json.loads(z.read('specification.json')); self.assertEqual(data['status'],'規格草稿／待確認'); self.assertTrue(data['quote_readiness_issues'])
            book=load_workbook(io.BytesIO(z.read('business_workbook.xlsx'))); self.assertEqual(book['規格確認單']['C5'].data_type,'s'); self.assertEqual(book['規格確認單']['C5'].value,'=1+1')
            self.assertIn('Test inspection method',sheet); self.assertIn(self.ev['text'],sheet)

class SpecificationUiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def test_ui_persists_specs_and_commercial_fields_without_automatic_confirmation(self):
        w=MainWindow(self.store.root); w.load_documents(); page=w.business_page
        page.spec_panel.fields['form_number'].setText('SPEC-UI-TEST'); page.commercial_panel.fields['tax_percent'].setText('5')
        self.assertTrue(page.is_dirty()); page.save(); self.assertFalse(page.is_dirty())
        case=page.service.get(self.doc['id']); self.assertEqual(case['specification']['form_number'],'SPEC-UI-TEST'); self.assertEqual(case['commercial']['tax_percent'],'5')
        self.assertIsNone(case['specification']['review']); w.close()
    def test_template_change_adds_new_checks_without_deleting_old_text(self):
        w=MainWindow(self.store.root); w.load_documents(); panel=w.business_page.spec_panel
        panel.items.item(0,2).setText('keep my scope'); panel.profile.setCurrentIndex(panel.profile.findData('coating')); panel.merge_template()
        values=panel.values(); self.assertEqual(values['items'][0]['value'],'keep my scope'); self.assertTrue(any(row['key']=='coverage' for row in values['items']))
        w.business_page.reset(); w.close()
