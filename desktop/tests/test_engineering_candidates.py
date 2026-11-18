"""Engineering catalogue, multi-method evidence and reviewed feedback invariants."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import zipfile
from unittest.mock import patch
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication
from workbench.domain import evidence,requirement,clone,validate_document
from workbench.engineering import catalogue_candidates,classify_text,ranked_candidates,obligations
from workbench.feedback import FeedbackService
from workbench.quotation_layout import quotation_rows
from workbench.extraction import apply_analysis
from workbench.extraction import ask_vlm,analyze as extract_page
from workbench.bounded import analyze
from workbench.exporting import export_bundle
from workbench.canvas import DrawingCanvas
from workbench.ui import MainWindow
from tests.test_workbench import Fixture
from tests.test_quotation_workspace import add_grounded

class EngineeringTests(Fixture):
    def test_model_contract_limits_requirements_and_repeated_warnings(self):
        body={'message':{'content':json.dumps({'requirements':[],'warnings':[],'complete':False})}}
        with patch('workbench.extraction.local_request',return_value=body) as request:
            ask_vlm(__import__('PIL.Image',fromlist=['Image']).new('RGB',(40,60)),
                    'Printed text','vision','http://localhost',self.root/'contract',
                    ocr_regions=[{'text':'Printed text','box':[.1,.2,.8,.3]}])
        payload=request.call_args.args[1]; schema=payload['format']
        self.assertEqual(schema['properties']['requirements']['maxItems'],8)
        self.assertEqual(schema['properties']['warnings']['maxItems'],3)
        prompt=payload['messages'][0]['content']
        self.assertIn(json.dumps(schema,ensure_ascii=False),prompt)
        self.assertNotIn('"maxItems": 150',prompt)
        self.assertIn('"box":[0.1,0.2,0.8,0.3]',prompt)

    def test_reasoning_only_model_response_is_saved_and_cannot_be_applied(self):
        body={'message':{'content':'','thinking':'<think>Consider material S45C...'},'done_reason':'length'}
        with patch('workbench.extraction.local_request',return_value=body):
            with self.assertRaisesRegex(ValueError,'模型沒有回傳結構化候選'):
                ask_vlm(__import__('PIL.Image',fromlist=['Image']).new('RGB',(40,60)),
                    'Actual text','vision','http://localhost',self.root/'reasoning')
        self.assertEqual(json.loads((self.root/'reasoning/response.json').read_text(encoding='utf-8')),body)

    def test_new_draft_export_includes_feedback_at_unchanged_document_version(self):
        first=export_bundle(self.store,self.doc['id'])
        FeedbackService(self.store).record(self.doc,'canvas_draw',detail={'box':[.1,.2,.3,.4]})
        second=export_bundle(self.store,self.doc['id'])
        self.assertNotEqual(first,second); self.assertEqual(self.store.get(self.doc['id'])['version'],1)
        with zipfile.ZipFile(first) as archive: self.assertEqual(json.loads(archive.read('human_feedback.json'))['trajectories'],[])
        with zipfile.ZipFile(second) as archive: self.assertEqual(len(json.loads(archive.read('human_feedback.json'))['trajectories']),1)
        self.assertEqual(second,export_bundle(self.store,self.doc['id']))

    def test_rfq_adhesive_and_blank_price_are_not_a_quotation_page(self):
        from workbench.quotation import is_quotation,extract_quotation
        regions=[evidence(1,[.1,.1,.8,.14],'膠種：符合規範之膠種'),
                 evidence(1,[.1,.2,.8,.24],'單價：元／支（不含稅）'),
                 evidence(1,[.1,.3,.8,.34],'詢價日期：2026年5月22日')]
        self.assertFalse(is_quotation(regions))
        generic=extract_quotation(regions,detected=True,include_missing=False,tolerant=False)
        self.assertFalse(any(r['key']=='quotation.date' for r in generic))

    def test_model_box_failure_can_use_actual_unique_ocr_anchor_without_scaling_guesses(self):
        from drawing_intelligence.models import TextRegion
        from types import SimpleNamespace
        item=dict(key='material',label='材質',value='S45C',unit='',state='present',operator='eq',
                  subject='document',scope='',conditions='',evidence='Material: S45C',box=[88,110,180,150])
        body={'model':'custom-vision','message':{'content':json.dumps({'requirements':[item],'warnings':[],'complete':True})}}
        with patch('workbench.extraction.local_request',return_value=body):
            response,stats=ask_vlm(__import__('PIL.Image',fromlist=['Image']).new('RGB',(400,600)),
                'Material: S45C','custom-vision','http://localhost',self.root/'trace',allow_ocr_anchor=True)
        self.assertEqual(response['requirements'],[]); self.assertEqual(stats['unlocated_requirements'][0]['box'],[88,110,180,150])
        fake_ocr=SimpleNamespace(text='Material: S45C',engine='actual-ocr',
            regions=[TextRegion('Material: S45C',(10,20,200,40),.95,'actual-ocr')],warnings=[])
        with patch('drawing_intelligence.ocr.recognize_page',return_value=fake_ocr),\
             patch('workbench.extraction.ask_vlm',return_value=(response,stats)):
            output=extract_page(self.doc,self.store.root,use_vlm=True,model='custom-vision')
        run=output['tool_runs'][0]; self.assertEqual(run['ocr_anchored_vlm_count'],1)
        model_frame=next(e for e in output['regions'] if e['source']=='VLM:custom-vision')
        self.assertEqual(model_frame['box'],output['regions'][0]['box'])

    def test_every_nonempty_printed_region_and_symbol_is_selectable(self):
        d=clone(self.doc)
        d['regions']=[evidence(1,[.1,.1,.5,.15],t,'actual-ocr') for t in ('Ø8 ±0.1','ISO 4042','52.8','Hello','')]
        d['requirements']=[]; candidates=catalogue_candidates(d)
        raw=[r for r in candidates if r['key']=='observation.text']
        self.assertEqual({r['value'] for r in raw},{'Ø8 ±0.1','ISO 4042','52.8','Hello'})
        self.assertEqual({r['value'] for r in candidates if r['key'].startswith('symbol.')},{'Ø','±'})
        self.assertTrue(all(r['status']=='needs_review' and r['evidence_ids'] for r in candidates))
        d['requirements']=candidates; validate_document(d)
        self.assertEqual(catalogue_candidates(d),[])

    def test_valid_but_wrong_model_box_uses_unique_measured_ocr_text_box(self):
        from drawing_intelligence.models import TextRegion
        from types import SimpleNamespace
        item=dict(key='material',label='材質',value='S45C',unit='',state='present',operator='eq',
                  subject='document',scope='',conditions='',evidence='Material: S45C',box=[.8,.8,.9,.9])
        fake=SimpleNamespace(text='Material: S45C',engine='actual-ocr',
            regions=[TextRegion('Material: S45C',(10,20,200,40),.95,'actual-ocr')],warnings=[])
        with patch('drawing_intelligence.ocr.recognize_page',return_value=fake),\
             patch('workbench.extraction.ask_vlm',return_value=({'requirements':[item],'warnings':[],'complete':True},{})):
            output=extract_page(self.doc,self.store.root,use_vlm=True,model='vision')
        model_frame=next(e for e in output['regions'] if e['source']=='VLM:vision')
        self.assertEqual(model_frame['box'],output['regions'][0]['box'])
        r=next(r for r in output['requirements'] if r['key']=='material' and
               any(c.get('source')=='VLM:vision' for c in r['candidates']))
        self.assertEqual(r['candidates'][0]['model_box'],[.8,.8,.9,.9])
        self.assertEqual(output['tool_runs'][0]['ocr_aligned_vlm_count'],1)

    def test_classification_separates_standards_thread_surface_and_business(self):
        examples={'ISO 267-27 MK':'檢驗與品質文件','M8x1.25-6h':'尺寸、公差與符號',
                  'Ø8 ±0.1':'尺寸、公差與符號','表面處理：三價黑鋅':'材料與表面處理',
                  '130萬PCS/年':'數量、交期與商務','最高工作溫度135℃':'製程與加工要求'}
        for text,category in examples.items(): self.assertEqual(classify_text(text)[0],category)
        self.assertEqual(classify_text('傅真：+886-7-6991511')[0],'基本資料')
        self.assertEqual(classify_text('網址：http://www.example.com')[0],'基本資料')

    def test_drawing_title_labels_auto_project_without_quote_page_classification(self):
        def add(d):
            d['regions'].extend([evidence(1,[.1,.3,.8,.35],'材質：S45C','ocr'),
                                 evidence(1,[.1,.4,.8,.45],'客戶：測試有限公司','ocr')])
        d=self.store.mutate(self.doc['id'],1,'test','synthetic drawing',add)
        d=self.store.ensure_engineering_catalogue(d['id'],d['version'])
        rows={f['key']:f for f in quotation_rows(d)}
        self.assertEqual(rows['material']['source']['value'],'S45C')
        self.assertEqual(rows['customer.name']['source']['value'],'測試有限公司')
        self.assertTrue(rows['material']['automatic']); self.assertEqual(rows['material']['status'],'待覆核')

    def test_scope_keeps_threadlocking_polymer_out_of_base_material(self):
        d=clone(self.doc); d['requirements']=[]
        d['regions']=[evidence(1,[.6,.1,.95,.13],'Threadlock specifications','ocr'),
                      evidence(1,[.6,.14,.95,.17],'Material: Nylon Torq-Patch Tuflok','ocr')]
        d['requirements']=catalogue_candidates(d)
        self.assertTrue(any(r['key']=='coating.material' for r in d['requirements']))
        self.assertIsNone(next(f for f in quotation_rows(d) if f['key']=='material')['source'])

    def test_observations_do_not_force_review_of_every_ocr_word_before_release(self):
        d=self.store.ensure_engineering_catalogue(self.doc['id'],1)
        self.assertEqual([r['id'] for r in obligations(d)],[self.req['id']])
        d=self.store.verify_requirement(d['id'],d['version'],self.req['id'],'synthetic')
        d=self.store.approve(d['id'],d['version'],'synthetic')
        self.assertEqual(d['status'],'approved')

    def test_catalogue_preserves_corrected_values_and_excluded_observations(self):
        d=self.store.ensure_engineering_catalogue(self.doc['id'],1)
        raw=next(r for r in d['requirements'] if r['key']=='observation.text')
        d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'value':'10'},'human correction')
        d=self.store.review_requirements(d['id'],d['version'],[raw['id']],'human exclusion',action='exclude')
        before=clone(d); d=self.store.ensure_engineering_catalogue(d['id'],d['version'])
        self.assertEqual(d,before); self.assertTrue(self.store.verify_audit(d['id']))

    def test_multiple_models_keep_actual_status_and_source_candidates(self):
        outputs=[]
        for index,name in enumerate(('vision-a','vision-b')):
            d=clone(self.doc); e=evidence(1,[.2,.3,.8,.4],'Material: S45C','VLM:'+name)
            d.update(regions=[e],requirements=[requirement('material','S45C',label='材質',evidence_ids=[e['id']],
                     candidates=[{'value':'S45C','source':'VLM:'+name}])],tool_runs=[{'id':name,'page':1,'model':name,'status':'complete'}])
            d['replace_generated_pages']=[]; outputs.append(d)
        with patch('workbench.bounded._analyze_single',side_effect=outputs) as run:
            output=analyze(self.doc,self.store.root,use_vlm=True,model='vision-a, vision-b')
        self.assertEqual(run.call_count,2); self.assertEqual(len(output['tool_runs']),2)
        d=apply_analysis(self.store,self.doc['id'],1,output)
        materials=[r for r in d['requirements'] if r['key']=='material']
        self.assertEqual(len(materials),1)
        methods={c['source'] for c in materials[0]['candidates']}
        self.assertTrue({'VLM:vision-a','VLM:vision-b'}<=methods)
        validate_document(d)

    def test_mineru_compare_keeps_rapid_result_when_mineru_times_out(self):
        rapid=clone(self.doc)
        rapid['pages'][0]['recognition_status']='complete'
        reading=evidence(1,[.2,.3,.8,.4],'Material: S45C','RapidOCR')
        rapid.update(regions=[reading],requirements=[],replace_generated_pages=[1],
                     tool_runs=[{'id':'rapid','page':1,'status':'complete'}])
        mineru=clone(self.doc)
        mineru['pages'][0]['recognition_status']='partial_timeout'
        mineru.update(regions=[],requirements=[],replace_generated_pages=[],
                      tool_runs=[{'id':'mineru','page':1,'status':'partial_timeout'}])
        with patch('workbench.bounded._analyze_single',side_effect=[rapid,mineru]) as run:
            result=analyze(self.doc,self.store.root,ocr_backend='compare_mineru',use_vlm=True)
        first,second=(call.kwargs for call in run.call_args_list)
        self.assertEqual((first['ocr_backend'],second['ocr_backend']),('rapid_auto','mineru_basic'))
        self.assertEqual((first['page_budget_seconds'],second['page_budget_seconds']),(30,90))
        self.assertTrue(first['force_ocr'] and second['force_ocr'])
        self.assertTrue(first['use_vlm'])
        self.assertFalse(second['use_vlm'])
        self.assertEqual(result['pages'][0]['recognition_status'],'complete')
        self.assertEqual(result['regions'],[reading])
        self.assertEqual([item['status'] for item in result['tool_runs']],['complete','partial_timeout'])

    def test_manual_iso_as_quantity_is_visible_but_cannot_be_confirmed(self):
        d,r=add_grounded(self.store,self.doc,'standard','ISO 4042',label='規範',raw='ISO 4042')
        d=self.store.set_quotation_source(d['id'],d['version'],'quotation.quantity',r['id'],'manual wrong binding')
        field=next(f for f in quotation_rows(d) if f['key']=='quotation.quantity')
        self.assertEqual(field['status'],'需修正'); self.assertEqual(field['source']['id'],r['id'])
        with self.assertRaises(ValueError): self.store.verify_requirement(d['id'],d['version'],r['id'],'synthetic')

    def test_feedback_only_learns_current_verified_sources_without_transferring_values(self):
        service=FeedbackService(self.store)
        d,r=add_grounded(self.store,self.doc,'material','S45C',raw='材質：S45C')
        d=self.store.set_quotation_source(d['id'],d['version'],'material',r['id'],'human')
        service.record(d,'bind_quotation',rid=r['id'],field='material')
        self.assertEqual(service.examples(),[])
        d=self.store.verify_requirement(d['id'],d['version'],r['id'],'human checked')
        self.assertEqual(len(service.examples()),1)
        query=clone(d); query['id']='other'; query['source_sha256']='other-source'
        before=clone(query); suggestions=service.suggest(query,'material')
        self.assertTrue(any(x['requirement_id']==r['id'] for x in suggestions)); self.assertEqual(query,before)
        query['tenant']='other-tenant'; self.assertEqual(service.suggest(query,'material'),[])
        d=self.store.edit_requirement(d['id'],d['version'],r['id'],{'value':'S50C'},'correction')
        self.assertEqual(service.examples(),[])

    def test_interactions_are_append_only_and_do_not_change_document_version(self):
        service=FeedbackService(self.store); before=self.store.get(self.doc['id'])
        first=service.record(before,'canvas_draw',detail={'points':[[.1,.2,0],[.2,.3,50]],'box':[.1,.2,.2,.3]})
        second=service.record(before,'capture_cancel')
        self.assertEqual(self.store.get(before['id']),before)
        self.assertIsNotNone(second['previous_event_hash']); self.assertEqual(service.history(before['id'])[0],first)
        with self.assertRaises(Exception):
            with self.store.connect() as con: con.execute('DELETE FROM interaction_events')

    def test_quality_composition_updates_when_a_component_is_corrected(self):
        def source(d):
            d['regions'].extend([evidence(1,[.1,.3,.8,.34],'須符合規範 DIN 267-27 MK','ocr'),
                evidence(1,[.1,.4,.8,.44],'首批需附膠種成分證明，每批需附檢測報告','ocr')])
        d=self.store.mutate(self.doc['id'],1,'test','synthetic requirements',source)
        d=self.store.ensure_engineering_catalogue(d['id'],d['version'])
        quality=next(f for f in quotation_rows(d) if f['key']=='quality.requirements')
        self.assertIsNotNone(quality['source']); r=quality['source']
        self.assertIn('DIN 267-27 MK',r['value']); self.assertIn('檢測報告',r['value'])
        d=self.store.verify_requirement(d['id'],d['version'],r['id'],'human review')
        component=next(x for x in d['requirements'] if x['id'] in r['component_ids'] and x['key']=='document.every_batch')
        d=self.store.edit_requirement(d['id'],d['version'],component['id'],{'value':'材料證明'},'human correction')
        updated=next(x for x in d['requirements'] if x['id']==r['id'])
        self.assertIn('材料證明',updated['value']); self.assertEqual(updated['status'],'needs_review')

    def test_conflicting_models_leave_both_values_selectable(self):
        d=self.store.get(self.doc['id']); outputs=[]
        for index,value in enumerate(('S45C','SCM435')):
            e=evidence(1,[.2,.3,.8,.4],'Material: '+value,'VLM:'+str(index))
            r=requirement('material',value,label='材質',evidence_ids=[e['id']])
            outputs.append((e,r))
        output=clone(d); output.update(regions=[e for e,r in outputs],requirements=[r for e,r in outputs],tool_runs=[])
        d=apply_analysis(self.store,d['id'],d['version'],output)
        materials=[r for r in d['requirements'] if r['key']=='material' and not r['deleted']]
        self.assertEqual({r['value'] for r in materials},{'S45C','SCM435'})
        self.assertTrue(all(r['state']=='conflict' for r in materials))
        self.assertIsNone(next(f for f in quotation_rows(d) if f['key']=='material')['source'])

    def test_export_contains_methods_candidates_and_trajectories(self):
        FeedbackService(self.store).record(self.doc,'canvas_select',rid=self.req['id'])
        path=export_bundle(self.store,self.doc['id'])
        with zipfile.ZipFile(path) as archive:
            for name in ('methods.json','engineering_candidates.json','human_feedback.json'): self.assertIn(name,archive.namelist())
            data=json.loads(archive.read('human_feedback.json'))
            self.assertEqual(len(data['trajectories']),1); self.assertFalse(data['neural_weights_trained'])

class EngineeringUiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_trajectory_uses_original_image_coordinates_after_display_rotation(self):
        canvas=DrawingCanvas(); canvas.width_px=100; canvas.height_px=200; canvas.rotation=90
        events=[]; canvas.gesture_completed.connect(events.append)
        canvas.begin_trajectory(QPointF(10,20)); canvas.append_trajectory(QPointF(30,60))
        canvas.finish_trajectory('draw',QPointF(40,80),[.1,.1,.4,.4])
        self.assertEqual(events[0]['points'][0][:2],[.1,.1]); self.assertEqual(events[0]['points'][-1][:2],[.4,.4])
        self.assertEqual(events[0]['coordinate_space'],'original_page_normalized'); canvas.close()

    def test_right_confirmation_uses_target_source_even_when_left_cursor_differs(self):
        w=MainWindow(self.store.root); w.load_documents()
        w.doc,r=add_grounded(w.store,w.doc,'material','S45C',raw='材質：S45C'); w.refresh()
        w._quotation_destination='material'; w.sync_quotation_selection(); w.select_review_id(self.req['id'])
        w.verify_quotation_field()
        rows={r['id']:r for r in w.doc['requirements']}
        self.assertEqual(rows[r['id']]['status'],'verified'); self.assertEqual(rows[self.req['id']]['status'],'needs_review')
        self.assertEqual(w.doc['quotation_mapping']['material'],r['id']); w.close()

    def test_frame_fill_target_creates_missing_field_then_enters_draw_mode(self):
        w=MainWindow(self.store.root); w.load_documents(); w._quotation_destination='packaging.instructions'
        w.sync_quotation_selection(); w.capture_quotation_field()
        self.assertEqual(w.current_requirement()['key'],'packaging.instructions')
        self.assertEqual(w.canvas.mode,'draw'); self.assertTrue(w.show_boxes.isChecked()); w.close()

    def test_canceled_drawing_clears_quotation_target_and_records_original_trajectory(self):
        w=MainWindow(self.store.root); w.load_documents(); w._quotation_destination='packaging.instructions'
        w.sync_quotation_selection(); w.capture_quotation_field()
        self.assertIsNotNone(w._pending_quote_capture)
        w.canvas.begin_trajectory(QPointF(10,20)); w.canvas.set_mode('select')
        self.assertIsNone(w._pending_quote_capture)
        history=w.feedback.history(w.doc['id'])
        self.assertTrue(any(e['action']=='canvas_cancel' for e in history)); w.close()
