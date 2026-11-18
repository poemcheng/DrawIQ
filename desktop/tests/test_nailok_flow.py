import json,os,tempfile,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from workbench.domain import evidence,requirement,checks,clone
from workbench.ingest import prepare_document
from workbench.store import Store,ConflictError
from workbench.nailok_flow import NailokService,features,alternatives,coverage

class Fixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        Image.new('RGB',(500,600),'white').save(self.root/'fixture.png')
        self.store=Store(self.root/'isolated',actor=lambda:'synthetic reviewer');self.service=NailokService(self.store)
        d=prepare_document(self.root/'fixture.png',self.store.root,tenant='synthetic')
        d['pages'][0].update(processing='complete',recognition_status='complete',ocr_text='QTY 100M\n110+0/-0.36\nNylon patch')
        for text,key,value in [('110+0/-0.36','dimension.linear','110'),('M6x1x35','product.specification','M6x1x35'),('QTY:100M','quantity','100M'),('Nylon patch','adhesive.product','Nylon')]:
            e=evidence(1,[.1,.1,.7,.2],text,'synthetic OCR');e['ocr_confidence']=.9;d['regions'].append(e)
            d['requirements'].append(requirement(key,value,evidence_ids=[e['id']]))
        self.doc=self.store.add(d)
    def tearDown(self):self.temp.cleanup()
    def confirm(self,key='nailok.coverage',candidate=0,value='10+0/-0.36',**kw):
        return self.service.confirm(self.doc['id'],self.doc['version'],key,self.doc['requirements'][candidate]['id'],value,
            unit='mm' if key=='nailok.coverage' else '',human_checked=True,**kw)
class FlowTests(Fixture,unittest.TestCase):
    def test_full_expression_does_not_repeat_tolerances(self):
        from workbench.nailok_flow import candidate_value
        r=requirement('dimension.linear','10+0/-0.36',upper_tolerance='0',lower_tolerance='-0.36')
        self.assertEqual(candidate_value(r),'10+0/-0.36')
    def test_dimension_candidates_keep_the_tolerance_and_invalidate_on_tolerance_edit(self):
        self.doc=self.store.edit_requirement(self.doc['id'],self.doc['version'],self.doc['requirements'][0]['id'],
            {'upper_tolerance':'0','lower_tolerance':'-0.36'},'synthetic typed tolerance')
        rows=alternatives(self.doc,'nailok.dimensions')
        self.assertTrue(any('110' in r['value'] and '-0.36' in r['value'] for r in rows))
        d=self.confirm();d=self.store.edit_requirement(d['id'],d['version'],self.doc['requirements'][0]['id'],{'lower_tolerance':'-0.30'},'synthetic tolerance correction')
        self.assertFalse(next(r for r in features(d) if r['key']=='nailok.coverage')['confirmed'])
    def test_multiple_sources_preserved_and_any_changed_source_invalidates(self):
        ids=[r['id'] for r in self.doc['requirements'][:2]]
        d=self.service.confirm(self.doc['id'],self.doc['version'],'nailok.coverage',ids,'10+0/-0.36\nM6x1x35',unit='mm',human_checked=True)
        row=next(r for r in features(d) if r['key']=='nailok.coverage')
        self.assertEqual(len(row['source']['evidence_ids']),2);self.assertTrue(row['confirmed'])
        d=self.store.edit_requirement(d['id'],d['version'],ids[1],{'value':'M6x1x40'},'synthetic change')
        self.assertFalse(next(r for r in features(d) if r['key']=='nailok.coverage')['confirmed'])
    def test_numeric_coverage_requires_unit(self):
        with self.assertRaises(ValueError):self.service.confirm(self.doc['id'],self.doc['version'],'nailok.coverage',self.doc['requirements'][0]['id'],'10±.3',human_checked=True)
    def test_annual_quantity_cannot_be_confirmed_as_batch(self):
        e=self.doc['regions'][2];e['text']='Annual QTY:100M'
        self.doc=self.store.save_region(self.doc['id'],self.doc['version'],e,None,'synthetic annual source',replace_id=e['id'])
        with self.assertRaises(ValueError):self.confirm('quotation.quantity',2,'100M')
    def test_explicit_attestation_required(self):
        with self.assertRaises(ValueError):self.service.confirm(self.doc['id'],1,'nailok.coverage',self.doc['requirements'][0]['id'],'10')
    def test_corrected_reading_preserves_original_ocr(self):
        before=clone(self.doc['regions']);d=self.confirm()
        self.assertEqual(d['regions'][:len(before)],before)
        row=next(r for r in features(d) if r['key']=='nailok.coverage')
        self.assertTrue(row['confirmed']);self.assertEqual(row['value'],'10+0/-0.36')
        self.assertEqual(row['source']['nailok_raw_text'],'110+0/-0.36')
        self.assertEqual(self.store.history(d['id'])[0]['action'],'nailok_confirm')
    def test_source_change_invalidates_feature_and_blocks_release(self):
        d=self.confirm();rid=self.doc['requirements'][0]['id']
        d=self.store.edit_requirement(d['id'],d['version'],rid,{'value':'new OCR'},'synthetic reread')
        self.assertFalse(next(r for r in features(d) if r['key']=='nailok.coverage')['confirmed'])
        self.assertTrue(any(i['code']=='nailok_source_changed' for i in checks(d)))
        self.assertNotIn('nailok.coverage',[r['key'] for r in self.service.report(d)['confirmed_features']])
    def test_edit_replaces_selection_but_keeps_prior_value_in_history(self):
        d=self.confirm();old=d['nailok_mapping']['nailok.coverage'];self.doc=d
        d=self.confirm(value='10+0/-0.30')
        self.assertTrue(next(r for r in d['requirements'] if r['id']==old)['deleted'])
        self.assertEqual(next(r for r in d['requirements'] if r['id']==old)['value'],'10+0/-0.36')
        self.assertFalse(any(i['code']=='cross_source_conflict' and i['requirement_id']==old for i in checks(d)))
    def test_original_quote_field_uses_same_review_gate(self):
        d=self.confirm('quotation.quantity',2,'100M')
        row=next(r for r in features(d) if r['key']=='quotation.quantity')
        self.assertTrue(row['confirmed']);self.assertEqual(row['value'],'100M')
        self.assertEqual(d['quotation_mapping']['quotation.quantity'],row['source']['id'])
    def test_missing_is_recorded_but_not_a_confirmed_fact(self):
        d=self.service.confirm(self.doc['id'],self.doc['version'],'adhesive.product',None,'',state='missing',reason='原圖未提供',human_checked=True)
        report=self.service.report(d)
        self.assertIn('報價膠別',report['missing_required']);self.assertNotIn('adhesive.product',[r['key'] for r in report['confirmed_features']])
    def test_unanchored_candidate_cannot_be_confirmed(self):
        with self.assertRaises(ValueError):self.service.confirm(self.doc['id'],self.doc['version'],'nailok.coverage','invented','10',human_checked=True)
    def test_stale_version_rolls_back(self):
        d=self.confirm()
        with self.assertRaises(ConflictError):self.confirm(value='11')
        self.assertEqual(self.store.get(d['id'])['version'],d['version'])
    def test_all_readings_and_annual_warning(self):
        self.assertEqual(coverage(self.doc)['readings'],4)
        rows=alternatives(self.doc,'quotation.quantity');self.assertTrue(any('M／K' in str(r['issues']) for r in rows))
        self.assertGreaterEqual(len(alternatives(self.doc,'quotation.quantity',all_readings=True)),4)
    def test_learning_uses_only_current_verified_examples_and_never_copies_values(self):
        other=clone(self.doc);other.update(id='synthetic-other',source_sha256='f'*64);other=self.store.add(other)
        self.assertEqual(self.service.examples(other),[])
        d=self.confirm('adhesive.product',3,'Nylon 6');examples=self.service.examples(other);self.assertEqual(len(examples),1)
        rows=alternatives(other,'adhesive.product',examples)
        self.assertTrue(any(r['learned_from']==d['id'] for r in rows))
        self.assertTrue(any(r['value']=='Nylon' for r in rows));self.assertFalse(any(r['value']=='Nylon 6' for r in rows))
        self.store.edit_requirement(d['id'],d['version'],self.doc['requirements'][3]['id'],{'value':'changed'},'synthetic source change')
        self.assertEqual(self.service.examples(other),[])

class SimpleUiTests(Fixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp()
        from workbench.ui import MainWindow
        self.window=MainWindow(self.store.root)
        self.window.load_documents(self.doc['id'])
        self.page=self.window.simple_page
        self.addCleanup(self.window.deleteLater)

    def choose_field(self,key):
        self.page.feature_table.setCurrentCell(next(i for i,r in enumerate(self.page.rows) if r['key']==key),1)

    def approve_through_dialog(self, *, changes=None, reopen=False, copy=False):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from workbench.nailok_label_ui import FinalLabelDialog, LabelImageDialog

        paths=[]
        errors=[]

        def close_output():
            dialog=QApplication.activeModalWidget()
            try:
                if not isinstance(dialog,LabelImageDialog) or dialog.saved_path is None:
                    raise AssertionError('核准後未顯示已儲存的 PNG')
                paths.append(dialog.saved_path)
                if copy:
                    dialog.copy_image()
                    if QApplication.clipboard().image().size()!=dialog.image.size():
                        raise AssertionError('剪貼簿沒有核准的標籤圖片')
            except Exception as error:
                errors.append(error)
            finally:
                if dialog:
                    dialog.reject()

        def finish_editor():
            dialog=QApplication.activeModalWidget()
            try:
                if not isinstance(dialog,FinalLabelDialog):
                    raise AssertionError('核准前未顯示最後輸入視窗')
                for key,(value,unit) in (changes or {}).items():
                    entry,unit_entry=dialog.inputs[key]
                    entry.setText(value)
                    if unit_entry is not None:
                        unit_entry.setText(unit)
                dialog.finish_button.click()
                if dialog.updated_doc is None:
                    raise AssertionError(dialog.status.text())
                QTimer.singleShot(0,close_output)
            except Exception as error:
                errors.append(error)
                if dialog:
                    dialog.reject()

        QTimer.singleShot(0,close_output if reopen else finish_editor)
        self.page.save.click()
        self.assertFalse(errors,errors)
        self.assertEqual(len(paths),1)
        self.assertTrue(paths[0].is_file())
        return paths[0]

    def test_three_columns_prefill_all_labels_and_toggle_original(self):
        w=self.window
        p=self.page
        self.assertIs(w.workspace_tabs.currentWidget(),p)
        self.assertEqual(p.columns.count(),3)
        self.assertTrue(w.sidebar.isHidden())
        self.assertTrue(w.review_queue_widget.isHidden())
        labels=['報價單號','專案代號','報價人員','報價日期','報價有效日期','客戶','材質','報價膠別',
                '顏色','塗佈方式','規格','數量','報價','品質要求','包裝方式','注意事項','聯絡電話','客戶傳真']
        self.assertEqual([p.feature_table.item(i,0).text() for i in range(p.feature_table.rowCount())],labels)
        self.assertTrue(all(p.feature_table.item(i,1).text() for i in range(len(labels))))
        self.assertEqual(p.engine.currentData(),'compare_mineru')
        self.assertEqual(len(p.canvas.region_items),4)
        colors={item.pen().color().name() for item in p.canvas.region_items.values()}
        self.assertGreater(len(colors),1)
        p.overlays.setChecked(False)
        self.assertFalse(p.canvas.region_items)
        self.assertEqual(len(p.canvas.scene().items()),1)
        self.assertFalse(p.is_dirty())

    def test_approve_current_label_in_one_action_and_export_without_fake_costs(self):
        from workbench.nailok_ui import QuoteDraftDialog
        p=self.page
        version=p.doc['version']
        self.choose_field('adhesive.product')
        p.value.setPlainText('Nylon patch')
        self.assertTrue(p.is_dirty())
        image=self.approve_through_dialog(changes={'quotation.project':('DEMO-PROJECT',''),
                                                   'quotation.price':('12.50','TWD/M')},copy=True)
        self.assertFalse(p.is_dirty())
        self.assertEqual(p.doc['version'],version+1)
        self.assertEqual(p.approval_status.text(),'已核准')
        self.assertEqual(p.save.text(),'查看／輸出標籤圖片')
        self.assertEqual(image.suffix,'.png')
        saved=p.approval_info['final_fields']
        self.assertEqual(next(row['value'] for row in saved if row['key']=='quotation.project'),'DEMO-PROJECT')
        price=next(row for row in saved if row['key']=='quotation.price')
        self.assertEqual((price['value'],price['unit'],price['provenance']),('12.50','TWD/M','operator_input'))
        dialog=QuoteDraftDialog(p,p.doc)
        report=json.loads(dialog.export().read_text(encoding='utf-8'))
        self.assertIsNone(report['cost_estimate'])
        self.assertTrue({'Nylon patch','M6x1x35'}<={r['value'] for r in report['confirmed_features']})
        original_png=image.read_bytes()
        self.window.load_documents(p.doc['id'])
        self.assertEqual(self.approve_through_dialog(reopen=True).read_bytes(),original_png)

    def test_cancel_final_input_leaves_the_label_and_files_unapproved(self):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from workbench.nailok_label_ui import FinalLabelDialog

        before=self.store.get(self.doc['id'])
        errors=[]
        def close_editor():
            dialog=QApplication.activeModalWidget()
            if not isinstance(dialog,FinalLabelDialog):
                errors.append('找不到最終標籤輸入')
            else:
                dialog.inputs['quotation.project'][0].setText('UNCOMMITTED')
            if dialog:
                dialog.reject()
        QTimer.singleShot(0,close_editor)
        self.page.save.click()
        self.assertFalse(errors,errors)
        self.assertEqual(self.store.get(self.doc['id']),before)
        self.assertFalse((self.store.root/'exports').exists())

    def test_prices_use_decimal_and_stale_costs_cannot_export(self):
        from workbench.nailok_ui import QuoteDraftDialog
        p=self.page
        dialog=QuoteDraftDialog(p,p.doc)
        dialog.qty.setText('100')
        dialog.currency.setCurrentText('TWD')
        for field in dialog.costs.values():field.setText('1')
        dialog.setup.setText('0')
        dialog.margin.setText('20')
        self.assertEqual(dialog.preview()['unit_quote'],'6.2500')
        report=json.loads(dialog.export().read_text(encoding='utf-8'))
        self.assertEqual(report['cost_estimate']['total_quote'],'625.00')
        self.approve_through_dialog()
        with self.assertRaisesRegex(ValueError,'圖面或核對內容已更新'):
            dialog.export()

    def test_canvas_correction_preserves_raw_and_requires_recheck(self):
        from unittest.mock import patch
        p=self.page
        self.choose_field('product.specification')
        source=self.doc['regions'][1]
        p.locate_source(source['id'])
        p.use_annotation()
        with patch('workbench.nailok_ui.QInputDialog.getMultiLineText',return_value=('M6 × 1 × 35',True)):
            p.edit_annotation()
        self.assertEqual(p.value.toPlainText(),'M6 × 1 × 35')
        self.assertTrue(p.is_dirty())
        self.assertIn(source['text'],p.source_text.toPlainText())
        self.assertEqual(p.regions[source['id']]['text'],source['text'])
        self.approve_through_dialog()

    def test_crop_recognition_refills_second_page_and_keeps_other_edits_for_approval(self):
        from PyQt6.QtTest import QTest
        from unittest.mock import patch
        p=self.page
        second_source=evidence(2,[.45,.50,.8,.58],'Quality: ISO 2320','Indra OCR')
        def add_page(doc):
            doc['pages'].append(dict(doc['pages'][0],number=2))
            doc['regions'].append(second_source)
            return {'page':2}
        doc=self.store.mutate(p.doc['id'],p.doc['version'],'synthetic_test','add page to isolated test',add_page)
        self.window.doc=doc
        self.window.refresh()
        p.results.source_selected.emit(second_source['id'])
        self.assertEqual(p.page_select.currentData(),2)
        self.assertIn(second_source['id'],p.canvas.region_items)
        self.choose_field('adhesive.product')
        p.value.setPlainText('Nylon patch')
        self.choose_field('packaging.instructions')
        p.page_select.setCurrentIndex(p.page_select.findData(2))
        def capture(store,doc,page_number,box,key,**_):
            self.assertEqual((page_number,key),(2,'packaging.instructions'))
            region=evidence(page_number,box,'包裝方式：分袋包裝','RapidOCR')
            updated=store.save_region(doc['id'],doc['version'],region,None,'isolated crop')
            draft={'key':key,'value':'分袋包裝','unit':'','candidate_id':None,'evidence_ids':[region['id']],
                   'state':'present','engine':'RapidOCR'}
            return {'document':updated,'draft':draft,'alternatives':[draft],'conflict':False,'cancelled':False}
        with patch('workbench.nailok_crop.capture_field',side_effect=capture) as run:
            p.new_box.click()
            self.assertTrue(p.canvas.draw_mode)
            p.canvas.region_created.emit([.1,.73,.4,.8])
            for _ in range(100):
                if self.window.worker is None:
                    break
                QTest.qWait(10)
            self.assertIsNone(self.window.worker)
            self.assertEqual(run.call_count,1)
        self.assertEqual(p.value.toPlainText(),'分袋包裝')
        self.assertIn('adhesive.product',p.edits)
        self.approve_through_dialog()
        report=p.service.report(p.doc)
        confirmed=next(row for row in report['confirmed_features'] if row['key']=='packaging.instructions')
        self.assertEqual(confirmed['value'],'分袋包裝')
        self.assertEqual(p.regions[confirmed['evidence_ids'][0]]['page'],2)

    def test_empty_crop_can_anchor_manual_value_without_an_extra_confirmation(self):
        from PyQt6.QtTest import QTest
        from unittest.mock import patch
        p=self.page
        self.choose_field('packaging.instructions')
        methods=[(backend,{'status':'empty','tokens':[],'text':'','engine':label})
                 for backend,label in [('rapid_v6','RapidOCR'),('mineru_basic','MinerU Basic')]]
        with patch('workbench.nailok_crop.read_methods',return_value=methods):
            p.recognize_box([.12,.38,.47,.47])
            for _ in range(100):
                if self.window.worker is None:
                    break
                QTest.qWait(10)
        self.assertIsNone(self.window.worker)
        self.assertEqual(p.unread_frame,('packaging.instructions',p.source_id))
        p.value.setPlainText('每袋分裝')
        self.approve_through_dialog()
        report=p.service.report(p.doc)
        feature=next(row for row in report['confirmed_features'] if row['key']=='packaging.instructions')
        self.assertEqual(feature['value'],'每袋分裝')
        frame=p.regions[feature['evidence_ids'][0]]
        self.assertEqual((frame['source'],frame['text']),('manual_capture',''))

if __name__=='__main__':unittest.main()
