"""Quotation references, source review and synchronized UI; isolated test documents."""
import os
import io
import json
import zipfile
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from workbench.domain import requirement, clone, evidence
from workbench.quotation_layout import quotation_rows, QUOTE_FIELDS
from workbench.extraction import apply_analysis
from workbench.store import ConflictError
from workbench.ui import MainWindow
from workbench.exporting import export_bundle, verify_bundle
from openpyxl import load_workbook
from tests.test_workbench import Fixture


def grounded_fact(doc, key, value, *, label=None, unit='', raw=None, **extra):
    label = label or QUOTE_FIELDS.get(key, key)
    ev = evidence(1,[.1,.25,.9,.3],raw if raw is not None else f'{label}：{value} {unit}'.strip())
    options = {'label':label,'unit':unit,'evidence_ids':[ev['id']]}; options.update(extra)
    r = requirement(key,value,**options)
    doc['regions'].append(ev); doc['requirements'].append(r)
    return r


def add_grounded(store, doc, key, value, **options):
    fixture = clone(doc); r = grounded_fact(fixture,key,value,**options)
    d = store.mutate(doc['id'],doc['version'],'test_fixture','Synthetic printed evidence',
                    lambda x:x.update(regions=fixture['regions'],requirements=fixture['requirements']))
    return d,r


class QuotationMappingTests(Fixture):
    def rows(self, doc): return {f['key']: f for f in quotation_rows(doc)}

    def test_empty_quotation_overview_never_adds_fake_facts(self):
        before=clone(self.doc); rows=self.rows(self.doc)
        self.assertEqual(len(rows), 16); self.assertEqual(self.doc, before)
        self.assertTrue(all(f['source'] is None for f in rows.values()))

    def test_unique_fact_auto_fills_but_conflicting_values_need_source_selection(self):
        d,r=add_grounded(self.store,self.doc,'material','S45C')
        before=clone(d)
        self.assertEqual(self.rows(d)['material']['source']['id'],r['id'])
        self.assertTrue(self.rows(d)['material']['automatic'])
        self.assertEqual(self.rows(d)['material']['status'],'待覆核')
        self.assertEqual(d,before)
        self.assertEqual(self.rows(d)['material']['candidate_ids'],[r['id']])
        d,_=add_grounded(self.store,d,'material','SUS304')
        self.assertIsNone(self.rows(d)['material']['source'])
        self.assertEqual(self.rows(d)['material']['status'], '請選擇來源')

    def test_legacy_labelled_results_auto_fill_suitable_fields(self):
        d=clone(self.doc)
        for key,label,value,target in [
            ('custom_material','材料','S45C','material'),
            ('custom_thread','螺紋','M8X1.25-6h','product.specification'),
            ('custom_standard','須符合規範','DIN 267-27 MK','quality.requirements'),
            ('custom_adhesive','膠種','precote 80','adhesive.product'),
            ('quantity','數量','160','quotation.quantity'),
            ('custom_price','單價','12.50 TWD / PCS','quotation.price'),
            ('custom_packaging','包裝要求','紙箱包裝','packaging.instructions')]:
            with self.subTest(label=label):
                r=grounded_fact(d,key,value,label=label)
                self.assertEqual(self.rows(d)[target]['source']['id'],r['id'])

    def test_quote_form_all_sixteen_labelled_fields_fill_without_manual_mapping(self):
        values=['DQ-DEMO-20260916','TRIAL-001','教學人員','2026/9/16','2026/10/16','試用教學有限公司',
                'S45C','precote 80','紅色 (red)','全周','M8 x 1.25 x 50','160 PCS','12.50 TWD / PCS',
                '依圖面規格確認','紙箱包裝','此為合成教學資料']
        d=clone(self.doc)
        for (key,label),value in zip(QUOTE_FIELDS.items(),values):
            grounded_fact(d,key,value,label=label)
        rows=self.rows(d)
        self.assertEqual([f['source']['value'] for f in rows.values()],values)
        self.assertTrue(all(f['automatic'] and f['status']=='待覆核' for f in rows.values()))

    def test_auto_fill_does_not_confuse_rfq_supplier_or_annual_demand_with_quote(self):
        d=clone(self.doc)
        for key,label,value in [('quantity','需求／計畫數量','7.2萬PCS/年'),
                                ('quotation.quantity','數量','72000 / year'),
                                ('custom_date','詢價日期','2026年5月22日'),
                                ('supplier','此致','台灣耐落 王經理'),
                                ('custom_coordinator','經辦人','謝惇鈺#587'),
                                ('finish','表面處理','三價鉻鋅鐵合金+黑色封孔')]:
            grounded_fact(d,key,value,label=label)
        self.assertTrue(all(f['source'] is None for f in self.rows(d).values()))

    def test_headers_units_ungrounded_and_unresolved_facts_are_not_auto_filled(self):
        for key,label,value,extra in [
            ('quotation.price','單價','元/支(不含稅)',{}),
            ('quotation.notes','注意事項','：',{}),
            ('quality.requirements','品質要求','品質要求：',{}),
            ('adhesive.product','膠種','符合規範之膠種',{}),
            ('table.0.0.0','材料','S45C',{}),
            ('material','材質','S45C',{'evidence_ids':[]}),
            ('material','材質','S45C',{'state':'conflict'}),
            ('material','材質','S45C',{'state':'unreadable'}),
            ('material','材質','S45C',{'state':'pending_decision'}),
            ('material','材質','',{'state':'blank'})]:
            with self.subTest(value=value,state=extra.get('state')):
                d=clone(self.doc); grounded_fact(d,key,value,label=label,**extra)
                self.assertTrue(all(f['source'] is None for f in self.rows(d).values()))

    def test_matching_duplicates_can_fill_but_different_objects_need_choice(self):
        d=clone(self.doc)
        a=grounded_fact(d,'material','S45C')
        b=grounded_fact(d,'custom_material','S４５C',label='材質')
        self.assertEqual(self.rows(d)['material']['source']['id'],a['id'])
        b['subject']='component:B'
        self.assertIsNone(self.rows(d)['material']['source'])

    def test_unassigned_partial_dimensions_do_not_hide_complete_thread_source(self):
        d=clone(self.doc)
        r=grounded_fact(d,'custom_thread','M8X1.25-6h',label='螺紋')
        for value in ['M8X1.25-6h','M8X1.25']:
            d['requirements'].append(requirement('dimension.公制螺紋',value,label='公制螺紋',
                subject='unassigned_feature',evidence_ids=[self.ev['id']]))
        self.assertEqual(self.rows(d)['product.specification']['source']['id'],r['id'])
        d['requirements'].remove(r)
        self.assertIsNone(self.rows(d)['product.specification']['source'])

    def test_manual_override_wins_and_source_verification_stays_synchronized(self):
        d,_=add_grounded(self.store,self.doc,'material','S45C')
        d=self.store.set_quotation_source(d['id'],d['version'],'material',self.req['id'],'test')
        d=self.verify(d)
        row=self.rows(d)['material']
        self.assertEqual(row['source']['id'],self.req['id']); self.assertFalse(row['automatic'])
        self.assertEqual(row['status'],'已確認')

    def test_export_includes_automatic_value_and_evidence_reference(self):
        d,r=add_grounded(self.store,self.doc,'custom_material','S45C',label='材質'); path=export_bundle(self.store,d['id'])
        self.assertTrue(verify_bundle(path))
        with zipfile.ZipFile(path) as archive:
            book=load_workbook(io.BytesIO(archive.read('knowledge.xlsx')))
            row=next(x for x in book['報價資訊'].values if x[1]=='material')
            self.assertEqual(row[2:6],('S45C',None,'待覆核',r['id']))
            self.assertIn(r['evidence_ids'][0],row[6])

    def test_mapping_is_persisted_reference_and_invalidates_confirmation(self):
        d=self.approved()
        d=self.store.set_quotation_source(d['id'], d['version'], 'quality.requirements', self.req['id'], 'test')
        self.assertEqual(d['status'], 'needs_review'); self.assertNotIn('approval', d)
        self.assertEqual(d['requirements'][0]['status'], 'needs_review')
        d=self.store.edit_requirement(d['id'], d['version'], self.req['id'], {'value':'12'}, 'test')
        self.assertEqual(self.rows(self.store.get(d['id']))['quality.requirements']['source']['value'], '12')
        self.assertTrue(self.store.verify_audit(d['id']))

    def test_clear_mapping_suppresses_default_without_deleting_source(self):
        r=requirement('material', 'S45C', evidence_ids=[self.ev['id']])
        d=self.store.add_requirement(self.doc['id'], 1, r, 'test')
        d=self.store.set_quotation_source(d['id'], d['version'], 'material', None, 'test')
        self.assertEqual(self.rows(d)['material']['status'], '未帶入')
        self.assertFalse(next(x for x in d['requirements'] if x['id']==r['id'])['deleted'])

    def test_excluded_mapping_never_silently_switches_to_another_candidate(self):
        d=self.store.set_quotation_source(self.doc['id'], 1, 'material', self.req['id'], 'test')
        d=self.store.review_requirements(d['id'], d['version'], [self.req['id']], 'test', action='exclude')
        d=self.store.add_requirement(d['id'], d['version'], requirement('material', 'OTHER'), 'test')
        self.assertIsNone(self.rows(d)['material']['source'])
        self.assertEqual(self.rows(d)['material']['status'], '來源已排除')

    def test_new_quote_field_is_atomic_missing_and_cannot_be_confirmed_without_evidence(self):
        d=self.store.set_quotation_source(self.doc['id'], 1, 'quotation.price', None, 'test', create=True)
        row=self.rows(d)['quotation.price']['source']
        self.assertEqual(row['state'], 'missing'); self.assertEqual(row['evidence_ids'], [])
        d=self.store.edit_requirement(d['id'], d['version'], row['id'], {'value':'12.50', 'state':'present'}, 'test')
        with self.assertRaises(ValueError): self.store.verify_requirement(d['id'], d['version'], row['id'], 'test')
        self.assertEqual(len(self.store.history(d['id'])), 3)

    def test_invalid_and_stale_mapping_writes_are_atomic(self):
        for key,rid in [('bad', self.req['id']), ('material', 'nonexistent')]:
            with self.assertRaises(ValueError): self.store.set_quotation_source(self.doc['id'], 1, key, rid, 'test')
        self.assertEqual(self.store.get(self.doc['id']), self.doc)
        d=self.store.set_quotation_source(self.doc['id'], 1, 'material', self.req['id'], 'test')
        with self.assertRaises(ConflictError): self.store.set_quotation_source(d['id'], 1, 'material', None, 'test')
        self.assertEqual(self.store.get(d['id']), d)

    def test_reanalysis_preserves_explicit_source(self):
        r=requirement('material', 'S45C', origin='extraction', scope='page:1', evidence_ids=[self.ev['id']])
        d=self.store.add_requirement(self.doc['id'], 1, r, 'test')
        d=self.store.set_quotation_source(d['id'], d['version'], 'material', r['id'], 'test')
        d=apply_analysis(self.store, d['id'], d['version'], {
            'replace_generated_pages':[1], 'regions':[], 'requirements':[], 'pages':[], 'warnings':[], 'tool_runs':[]}, 'test')
        self.assertEqual(self.rows(d)['material']['source']['id'], r['id'])

    def test_export_contains_custom_quote_labels_and_source_references(self):
        d=self.store.set_quotation_source(self.doc['id'],1,'quality.requirements',self.req['id'],'test')
        path=export_bundle(self.store,d['id']); self.assertTrue(verify_bundle(path))
        with zipfile.ZipFile(path) as archive:
            payload=json.loads(archive.read('knowledge.json'))
            self.assertEqual(payload['document']['quotation_mapping'],d['quotation_mapping'])
            book=load_workbook(io.BytesIO(archive.read('knowledge.xlsx')))
            rows=list(book['報價資訊'].values)
            row=next(x for x in rows if x[1]=='quality.requirements')
            self.assertEqual(row[0],'品質要求'); self.assertEqual(row[2:6],('9','threads','待覆核',self.req['id']))
            self.assertIn(self.req['id'],archive.read('quotation.csv').decode('utf-8-sig'))


class QuotationWorkspaceUiTests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')

    def test_small_screen_keeps_quote_and_review_controls_visible_with_cjk_font(self):
        w=self.window; w.resize(1280,800); self.app.processEvents()
        for widget in (w.quotation_table,w.value,w.unit,w.value_state,w.next_btn):
            self.assertFalse(widget.visibleRegion().isEmpty())
        table_bottom=w.quotation_table.mapTo(w,w.quotation_table.rect().bottomLeft()).y()
        target_top=w.quotation_target.mapTo(w,w.quotation_target.rect().topLeft()).y()
        self.assertLess(table_bottom,target_top)

    def setUp(self):
        super().setUp(); self.window=MainWindow(self.store.root)
        self.window.load_documents(); self.window.open_review_queue(); self.window.show(); self.app.processEvents()

    def tearDown(self):
        self.window.business_page.reset(); self.window.reset_form(); self.window.close()
        self.window.deleteLater(); self.app.processEvents(); super().tearDown()

    def choose(self, key):
        w=self.window
        row=list(QUOTE_FIELDS).index(key); w.quotation_table.selectRow(row)
        return row

    def test_all_results_are_default_including_verified_sources(self):
        w=self.window; w.verify_current()
        self.assertEqual(w.review_state.currentData(), 'all')
        self.assertFalse(w.requirements.isRowHidden(0))

    def test_prefill_all_sixteen_cells_have_content_or_missing_prompt(self):
        w=self.window; before=clone(w.doc)
        self.assertEqual(w.quotation_table.rowCount(),16)
        self.assertTrue(all(w.quotation_table.item(i,1).text().strip() for i in range(16)))
        self.assertEqual(w.doc,before)

    def test_low_confidence_prefill_locates_edits_and_confirms_the_same_source(self):
        w=self.window; w.doc,r=add_grounded(self.store,w.doc,'material','S45C')
        ev=r['evidence_ids'][0]
        def lower_score(doc): next(e for e in doc['regions'] if e['id']==ev)['ocr_confidence']=.45
        w.doc=self.store.mutate(w.doc['id'],w.doc['version'],'test_fixture','synthetic low OCR score',lower_score)
        w.refresh(); row=self.choose('material')
        self.assertEqual(w.quotation_table.item(row,1).text(),'S45C')
        self.assertIn('待確認',w.quotation_table.item(row,2).text())
        self.assertIn('首選建議',w.quotation_table.item(row,1).toolTip())
        w.navigate_quotation_source(row); self.assertEqual(w.current_region,ev)
        w.value.setPlainText('SUS304'); self.assertEqual(w.quotation_table.item(row,2).text(),'未儲存')
        w.reset_form(); self.assertEqual(w.quotation_table.item(row,1).text(),'S45C')
        w.verify_quotation_field(); self.assertEqual(w.quotation_table.item(row,2).text(),'已確認')
        self.assertEqual(w.doc['quotation_mapping']['material'],r['id'])
        self.assertTrue(w.store.verify_audit(w.doc['id']))

    def test_conflict_prefill_is_a_proposal_without_review_state_change(self):
        w=self.window; w.doc,a=add_grounded(self.store,w.doc,'material','S45C')
        w.doc,b=add_grounded(self.store,w.doc,'material','SUS304')
        before=clone(w.doc); w.refresh(); row=self.choose('material')
        self.assertIn(w.quotation_table.item(row,1).text(),('S45C','SUS304'))
        self.assertIn('待確認',w.quotation_table.item(row,2).text())
        self.assertEqual(w.doc,before)

    def test_automatic_source_edit_verify_and_cancel_sync_without_manual_link(self):
        w=self.window; w.doc,r=add_grounded(self.store,w.doc,'custom_material','S45C',label='材質'); w.refresh()
        row=self.choose('material')
        self.assertEqual(w.quotation_table.item(row,1).text(),'S45C')
        self.assertEqual(w.quotation_table.item(row,2).text(),'待覆核')
        self.assertIn('自動帶入',w.quotation_table.item(row,1).toolTip())
        self.assertTrue(w.quote_unlink_btn.isEnabled())
        w.navigate_quotation_source(row); self.assertEqual(w.current_region,r['evidence_ids'][0])
        w.value.setPlainText('SUS304'); self.assertEqual(w.quotation_table.item(row,1).text(),'SUS304')
        self.assertEqual(w.quotation_table.item(row,2).text(),'未儲存')
        w.save_current(); w.verify_current(); self.assertEqual(w.quotation_table.item(row,2).text(),'已確認')
        w.unlink_quotation_source(); self.assertIn('人工取消帶入',w.quotation_table.item(row,1).text())
        w.refresh(); self.assertEqual(w.quotation_table.item(row,2).text(),'未帶入')
        self.assertFalse(w.quote_unlink_btn.isEnabled())
        self.assertEqual(w.current_requirement()['value'],'SUS304')

    def test_supplement_auto_filled_field_locates_source_without_duplicate(self):
        w=self.window; w.doc,r=add_grounded(self.store,w.doc,'material','S45C'); w.refresh()
        self.choose('material'); before=clone(w.doc); w.add_quotation_field()
        self.assertEqual(w.doc,before); self.assertEqual(w.current_rid,r['id'])

    def test_rejected_customer_shows_reason_and_can_locate_without_mapping_it(self):
        w=self.window; w.doc,r=add_grounded(self.store,w.doc,'customer.name','456289 is design responsible.',
                                          raw='Customer 456289 is design responsible.')
        w.refresh(); row=self.choose('customer.name'); before=clone(w.doc)
        self.assertNotIn('456289',w.quotation_table.item(row,1).text())
        self.assertIn('責任',w.quotation_table.item(row,1).text())
        self.assertEqual(w.quotation_table.item(row,2).text(),'需判定')
        w.navigate_quotation_source(row)
        self.assertEqual(w.current_rid,r['id']); self.assertEqual(w.current_region,r['evidence_ids'][0])
        self.assertEqual(w.doc,before); self.assertFalse(w.quote_unlink_btn.isEnabled())

    def test_bidirectional_edit_preview_save_reset_and_confirmation(self):
        w=self.window; row=self.choose('quality.requirements'); w.link_quotation_source()
        w.value.setPlainText('12')
        self.assertEqual(w.quotation_table.item(row,1).text(), '12 threads')
        self.assertEqual(w.requirements.item(0,1).text(), '12 threads')
        self.assertEqual(w.quotation_table.item(row,2).text(), '未儲存')
        self.assertEqual(self.store.get(w.doc['id'])['requirements'][0]['value'], '9')
        w.reset_form(); self.assertEqual(w.quotation_table.item(row,1).text(), '9 threads')
        w.value.setPlainText('12'); w.verify_next()
        self.assertEqual(w.quotation_table.item(row,2).text(), '已確認')
        self.assertEqual(w.requirements.item(0,2).text(), '已確認')

    def test_quote_double_click_reveals_filtered_source_and_locates_evidence(self):
        w=self.window; row=self.choose('quality.requirements'); w.link_quotation_source()
        w.req_filter.setText('NO MATCH'); self.assertIsNone(w.current_rid)
        w.quotation_table.selectRow(row)
        w.quotation_table.cellClicked.emit(row,0)
        self.assertIsNone(w.current_rid)
        w.quotation_table.cellDoubleClicked.emit(row,0)
        self.assertEqual(w.current_rid, self.req['id']); self.assertEqual(w.current_region, self.ev['id'])
        self.assertEqual(w.checked_region_ids(), [self.ev['id']]); self.assertEqual(w.checked_review_ids(), [])

    def test_destination_stays_when_left_selection_changes(self):
        w=self.window
        r=requirement('material', 'S45C', evidence_ids=[self.ev['id']])
        w.doc=self.store.add_requirement(w.doc['id'], w.doc['version'], r, 'test'); w.refresh()
        self.choose('quotation.notes'); w.select_review_id(r['id'])
        self.assertFalse(w.form_dirty())
        self.assertEqual(w.quotation_key(), 'quotation.notes')
        w.link_quotation_source()
        self.assertEqual(w.doc['quotation_mapping']['quotation.notes'], r['id'])

    def test_destination_can_change_without_discarding_dirty_source(self):
        w=self.window; self.choose('quality.requirements'); w.link_quotation_source()
        w.value.setPlainText('12'); self.choose('material')
        self.assertEqual(w.quotation_key(), 'material')
        self.assertEqual(w.value.toPlainText(), '12')
        self.assertEqual(w.current_rid,self.req['id'])
        self.assertFalse(w.quote_link_btn.isEnabled())
        with self.assertRaises(ValueError): w.unlink_quotation_source()

    def test_selecting_existing_quote_target_keeps_different_left_source(self):
        w=self.window; self.choose('quality.requirements'); w.link_quotation_source()
        r=requirement('material','S45C',evidence_ids=[self.ev['id']])
        w.doc=self.store.add_requirement(w.doc['id'],w.doc['version'],r,'test'); w.refresh()
        w.select_review_id(r['id']); self.choose('quality.requirements')
        self.assertEqual(w.current_rid,r['id']); self.assertEqual(w.value.toPlainText(),'S45C')
        w.link_quotation_source()
        self.assertEqual(w.doc['quotation_mapping']['quality.requirements'],r['id'])

    def test_left_selection_never_changes_quotation_target(self):
        w=self.window; self.choose('quality.requirements'); w.link_quotation_source()
        self.choose('customer.name'); w.select_review_id(self.req['id'])
        self.assertEqual(w.quotation_key(),'customer.name')
        self.assertEqual(w.quotation_table.currentRow(),list(QUOTE_FIELDS).index('customer.name'))

    def test_missing_quote_field_can_be_created_and_linked_using_existing_review(self):
        w=self.window; row=self.choose('quotation.price'); w.add_quotation_field()
        w.value.setPlainText('12.50'); w.unit.setText('TWD/PCS')
        w.value_state.setCurrentIndex(w.value_state.findData('present'))
        for i in range(w.links.count()): w.links.item(i).setCheckState(Qt.CheckState.Checked)
        w.verify_next()
        self.assertEqual(w.quotation_table.item(row,1).text(), '12.50 TWD/PCS')
        self.assertEqual(w.quotation_table.item(row,2).text(), '已確認')

    def test_supplement_existing_field_opens_source_without_duplicate(self):
        w=self.window; self.choose('quotation.price'); w.add_quotation_field()
        before=clone(w.doc); rid=w.current_rid; w.add_quotation_field()
        self.assertEqual(w.doc,before); self.assertEqual(w.current_rid,rid)

    def test_document_switch_does_not_carry_mapping_or_target(self):
        w=self.window; self.choose('quotation.notes'); w.link_quotation_source()
        other=clone(self.doc); other['id']='another-document'; other['name']='another.pdf'
        self.store.add(other); w.load_documents(other['id'])
        self.assertIsNone(w._quotation_destination)
        self.assertTrue(all(f['source'] is None for f in w.quotation_data))

    def test_import_completion_opens_simple_workflow_with_full_readings(self):
        w=self.window; w.workspace_tabs.setCurrentIndex(0)
        w.job_done({'document_ids':[self.doc['id']], 'errors':[], 'show_review':True})
        self.assertIs(w.workspace_tabs.currentWidget(), w.simple_page)
        self.assertEqual(w.simple_page.columns.count(),3)
        w.advanced_mode.setChecked(True)
        w.job_done({'document_ids':[self.doc['id']], 'errors':[], 'show_review':True})
        self.assertIs(w.workspace_tabs.currentWidget(), w.review_workspace)
        self.assertTrue(w.review_queue_widget.isVisible())

    def test_new_document_load_shows_all_sources_instead_of_old_search(self):
        w=self.window; w.req_filter.setText('NO MATCH')
        other=clone(self.doc); other['id']='new-document'; other['name']='new.pdf'
        self.store.add(other); w.load_documents(other['id'])
        self.assertEqual(w.req_filter.text(),''); self.assertEqual(w.review_state.currentData(),'all')
        self.assertEqual(w.current_rid,self.req['id']); self.assertFalse(w.requirements.isRowHidden(0))
