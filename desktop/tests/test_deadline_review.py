import json,sys,time
from unittest.mock import patch
from workbench.domain import requirement,evidence,clone,checks
from workbench.ingest import locate_evidence
from workbench.extraction import dimensions_from_regions
from workbench.extraction_quality import field_issue,table_requirements
from workbench.review_queue import review_groups
from workbench.bounded import analyze,supervise
from workbench.store import ConflictError
from tests.test_workbench import Fixture

class QualityTests(Fixture):
    def test_empty_customer_ocr_is_not_proof_of_blank(self):
        from workbench.quotation import extract_quotation
        regions=[evidence(1,[.1,.1,.8,.14],'報價單號：Q-123'),evidence(1,[.1,.2,.8,.24],'客戶：')]
        r=next(r for r in extract_quotation(regions) if r['key']=='customer.name')
        self.assertEqual(r['state'],'unreadable'); self.assertEqual(r['value'],'')
    def test_leading_decimal_and_numbered_thread_preserved(self):
        self.doc['regions']=[evidence(1,[.1,.1,.3,.2],t) for t in ('.188','.375"','#10-32','B5yaR5a209')]
        dims=dimensions_from_regions(self.doc)
        self.assertEqual({r['value'] for r in dims},{'.188','.375"','#10-32'})
    def test_order_quantity_and_sample_quantity_stay_separate(self):
        from workbench.form_extraction import extract_form_fields
        regions=[evidence(1,b,t) for b,t in [([.1,.1,.25,.13],'數量'),([.4,.1,.55,.13],'樣品'),
            ([.12,.15,.24,.18],'166M'),([.42,.15,.54,.18],'300 pcs')]]
        reqs,ids=extract_form_fields(regions,1); values={r['key']:(r['value'],r['unit']) for r in reqs}
        self.assertEqual(values,{'quantity':('166','M'),'sample.quantity':('300','pcs')})
    def test_numeric_boundary_keeps_exact_cells(self):
        self.doc['regions']=[evidence(1,[.1,.1,.3,.2],t) for t in ('2','12','2.25','M2','20','2 mm')]
        ids=locate_evidence(self.doc,1,'2')
        # A full exact match takes precedence over unrelated numeric uses.
        self.assertEqual(ids,[self.doc['regions'][0]['id']])
        self.assertEqual(locate_evidence(self.doc,1,'2.25'),[self.doc['regions'][2]['id']])
    def test_socket_and_iso_not_order_ids(self):
        for text in ('Socket','ISO 898-1','© ISO 2001'):
            self.assertTrue(field_issue('order_number','2001',text))
        self.assertFalse(field_issue('order_number','PO-1234','PO: PO-1234'))
    def test_time_and_standard_are_not_scale(self):
        for text in ('2026.05.25 17:43:11','ISO 14580:2001'):
            self.assertTrue(field_issue('scale','17:43',text))
        self.assertFalse(field_issue('scale','1:10','SCALE 1:10'))
    def test_dates_grades_and_real_callouts(self):
        self.doc['regions']=[evidence(1,[.1,.1,.3,.2],t) for t in ('2026.05.25','10.9級','M6 ISO 14580','10 mm','10 mm')]
        dims=dimensions_from_regions(self.doc)
        self.assertEqual(sum(r['value']=='10 mm' for r in dims),2)
        self.assertTrue(any(r['value']=='M6' for r in dims))
        self.assertFalse(any('10.9' in r['value'] or '26.05' in r['value'] for r in dims))
    def test_table_preserves_all_fragments_and_source_ids(self):
        regions=[evidence(1,[.1+i*.1,.2,.17+i*.1,.23],str(i)) for i in range(6)]
        with patch('workbench.extraction_quality.grid_boxes',return_value=[[0,0,1,1]]):
            reqs,ids,blocks=table_requirements('unused',regions,1)
        self.assertEqual(ids,{e['id'] for e in regions}); self.assertEqual(len(reqs),6)
        self.assertTrue(all(r['table']['header_status']=='needs_review' for r in reqs))
    def test_queue_is_lossless_and_does_not_verify(self):
        self.doc['requirements']=[requirement('table.0.0.'+str(i),str(i),table={'name':'table A','row':0,'column':str(i)},evidence_ids=[self.ev['id']]) for i in range(12)]
        before=clone(self.doc); groups=review_groups(self.doc)
        self.assertEqual(len(groups),1); self.assertEqual(len(groups[0]['requirement_ids']),12); self.assertEqual(before,self.doc)
    def test_duplicate_values_different_features_not_collapsed(self):
        self.doc['requirements']=[requirement('dimension.尺寸','10',evidence_ids=[self.ev['id']]) for _ in range(2)]
        with patch('workbench.review_queue.grid_boxes',return_value=[]): groups=review_groups(self.doc)
        self.assertEqual(len(groups),2)

class GroupReviewTests(Fixture):
    def test_successful_full_page_replaces_only_untouched_machine_candidates(self):
        from workbench.extraction import apply_analysis
        d=clone(self.doc); e=evidence(1,[.2,.2,.4,.3],'M6','OCR'); r=requirement('dimension.公制螺紋','M6',evidence_ids=[e['id']])
        def add(x): x['regions'].append(e); x['requirements'].append(r)
        d=self.store.mutate(d['id'],1,'analysis','test machine output',add)
        output=clone(d); output['requirements']=[clone(r)]; output['requirements'][0]['id']='new_req'
        output['regions']=[e]; output['tool_runs']=[]; output['replace_generated_pages']=[1]
        new=apply_analysis(self.store,d['id'],d['version'],output)
        self.assertTrue(next(x for x in new['requirements'] if x['id']==r['id'])['deleted'])
        self.assertFalse(next(x for x in new['requirements'] if x['id']==self.req['id'])['deleted'])
        assertions=[x for x in new['requirements'] if not x['deleted'] and x.get('origin')!='drawing_observation']
        self.assertEqual(len(assertions),2)
        self.assertTrue(any(x['key']=='observation.text' and x['value']=='M6' for x in new['requirements']))
        old=self.store.snapshot(d['id'],d['version']); self.assertFalse(old['requirements'][-1]['deleted'])
    def test_human_correction_survives_full_page_replacement(self):
        from workbench.extraction import apply_analysis
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'12'},'human correction')
        output=clone(d); output.update(requirements=[],regions=[],tool_runs=[],replace_generated_pages=[1])
        new=apply_analysis(self.store,d['id'],d['version'],output)
        self.assertEqual(new['requirements'][0]['value'],'12'); self.assertFalse(new['requirements'][0]['deleted'])
    def test_verify_records_all_before_after(self):
        d=self.store.review_requirements(self.doc['id'],1,[self.req['id']],'synthetic attestation')
        event=self.store.history(d['id'])[0]
        self.assertEqual(d['requirements'][0]['status'],'verified')
        self.assertEqual(event['detail']['before'][self.req['id']]['status'],'needs_review')
        self.assertEqual(event['detail']['after'][self.req['id']]['status'],'verified')
        self.assertTrue(event['actor'])
    def test_one_conflict_rolls_back_entire_group(self):
        r=requirement('material','a',state='conflict',evidence_ids=[self.ev['id']])
        d=self.store.add_requirement(self.doc['id'],1,r,'test'); before=clone(d)
        with self.assertRaises(ValueError): self.store.review_requirements(d['id'],d['version'],[self.req['id'],r['id']],'test')
        self.assertEqual(self.store.get(d['id']),before)
    def test_verify_dependency_in_same_group_is_atomic(self):
        r=requirement('derived','result',evidence_ids=[self.ev['id']],dependencies=[self.req['id']])
        d=self.store.add_requirement(self.doc['id'],1,r,'test')
        d=self.store.review_requirements(d['id'],d['version'],[r['id'],self.req['id']],'test')
        self.assertTrue(all(r['status']=='verified' for r in d['requirements']))
    def test_stale_group_cannot_mutate(self):
        self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'12'},'test')
        with self.assertRaises(ConflictError): self.store.review_requirements(self.doc['id'],1,[self.req['id']],'test')
    def test_exclude_keeps_snapshot_and_invalidates_release(self):
        d=self.approved(); d=self.store.review_requirements(d['id'],d['version'],[self.req['id']],'test exclusion',action='exclude')
        self.assertTrue(d['requirements'][0]['deleted']); self.assertNotIn('approval',d)
        self.assertFalse(self.store.snapshot(d['id'],d['version']-1)['requirements'][0]['deleted'])
    def test_exclude_dependency_is_blocked(self):
        r=requirement('child','yes',dependencies=[self.req['id']])
        d=self.store.add_requirement(self.doc['id'],1,r,'test')
        with self.assertRaises(ValueError): self.store.review_requirements(d['id'],d['version'],[self.req['id']],'test',action='exclude')
    def test_empty_selection_is_not_attestation(self):
        with self.assertRaises(ValueError): self.store.review_requirements(self.doc['id'],1,[],'test')

class DeadlineTests(Fixture):
    def test_native_hang_process_is_killed(self):
        start=time.monotonic()
        status=supervise([sys.executable,'-c','import time; time.sleep(60)'],cwd=self.root,
            deadline=start+.25,log_path=self.root/'hang.log')
        self.assertEqual(status,'partial_timeout'); self.assertLess(time.monotonic()-start,2)
    def test_partial_output_never_marks_page_success(self):
        start=time.monotonic(); before=clone(self.doc)
        result=analyze(self.doc,self.root,page_budget_seconds=.2)
        self.assertLess(time.monotonic()-start,2)
        self.assertEqual(result['pages'][0]['recognition_status'],'partial_timeout')
        self.assertTrue(any(i['code']=='incomplete_page' for i in checks(result)))
        self.assertEqual(before,self.doc)
    def test_failed_page_blocks_approval(self):
        d=self.doc; d['pages'][0]['recognition_status']='partial_timeout'
        def change(x): x['pages']=d['pages']
        d=self.store.mutate(d['id'],1,'test','test',change)
        d=self.verify(d)
        with self.assertRaises(ValueError): self.store.approve(d['id'],d['version'],'test')
    def test_cancel_before_worker_is_empty(self):
        result=analyze(self.doc,self.root,cancelled=lambda:True)
        self.assertTrue(result['cancelled']); self.assertEqual(result['tool_runs'],[])
    def test_more_than_30_seconds_rejected(self):
        with self.assertRaises(ValueError): analyze(self.doc,self.root,page_budget_seconds=31)
