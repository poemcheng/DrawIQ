"""Synthetic, isolated workflow tests. No real drawings are approved by tests."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image
from openpyxl import load_workbook

from workbench.domain import box_valid,canonical,checks,clone,digest,evidence,requirement,validate_document
from workbench.store import Store,ConflictError
from workbench.ingest import prepare_document
from workbench.extraction import map_box,analyze,apply_analysis,dimensions_from_regions,ask_vlm,local_request
from workbench.exporting import export_bundle,verify_bundle,safe_cell
from workbench.learning import StrategyService
from workbench.refinement import refine


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='drawiq_tests_')
        self.root=Path(self.tmp.name); self.store=Store(self.root/'data')
        source=self.root/'synthetic.pdf'
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=600)
            page.insert_text((40,90),'TEST ONLY - COATING MINIMUM 9 THREADS',fontsize=11)
            pdf.save(source)
        doc=prepare_document(source,self.store.root,tenant='synthetic-test')
        self.ev=evidence(1,[.08,.1,.9,.2],'TEST ONLY COATING MINIMUM 9 THREADS')
        self.req=requirement('coating.minimum','9',unit='threads',operator='ge',evidence_ids=[self.ev['id']],critical=True)
        doc['regions']=[self.ev]; doc['requirements']=[self.req]
        self.doc=self.store.add(doc)

    def tearDown(self): self.tmp.cleanup()

    def verify(self,doc=None,rid=None):
        doc=doc or self.store.get(self.doc['id'])
        return self.store.verify_requirement(doc['id'],doc['version'],rid or self.req['id'],'Synthetic automated test, not a real approval')

    def approved(self):
        d=self.verify(); return self.store.approve(d['id'],d['version'],'Synthetic test approval')

    def released(self):
        d=self.approved(); return self.store.release(d['id'],d['version'],'Synthetic test release')


class DomainTests(Fixture):
    def test_decimal_sign_identity(self):
        self.assertNotEqual(canonical('1.5'),canonical('15'))
        self.assertNotEqual(canonical('-0.1'),canonical('0.1'))
        self.assertEqual(canonical('M４ × 0.7'),canonical('m4x0.7'))

    def test_invalid_boxes(self):
        for b in ([.5,0,.1,1],[0,0,float('nan'),1],[-.1,0,1,1],[0,0,0,1],[False,0,1,1]): self.assertFalse(box_valid(b))

    def test_engineering_checks(self):
        for value in ('not-a-number','NaN','Infinity'):
            d=clone(self.doc); d['requirements'][0]['value']=value
            self.assertIn('numeric',[i['code'] for i in checks(d)])
        d=clone(self.doc); d['requirements'][0].update(lower_tolerance='0.2',upper_tolerance='-0.1')
        self.assertIn('tolerance_order',[i['code'] for i in checks(d)])

    def test_wrong_region_kind_and_dependency_cycle(self):
        d=clone(self.doc); d['requirements'][0]['feature_ids']=[self.ev['id']]
        with self.assertRaises(ValueError): validate_document(d)
        d=clone(self.doc); r=requirement('derived','9',evidence_ids=[self.ev['id']],dependencies=[self.req['id']])
        d['requirements'].append(r); d['requirements'][0]['dependencies']=[r['id']]
        with self.assertRaises(ValueError): validate_document(d)

    def test_equal_dimension_different_positions_preserved(self):
        d=clone(self.doc); d['regions']=[evidence(1,[.1,.1,.2,.2],'10 mm'),evidence(1,[.6,.6,.7,.7],'10 mm')]
        reqs=dimensions_from_regions(d)
        self.assertEqual(len(reqs),2)
        self.assertNotEqual(reqs[0]['evidence_ids'],reqs[1]['evidence_ids'])

    def test_crop_rotation_coordinates(self):
        crop=[.2,.3,.6,.9]
        for actual,wanted in zip(map_box([0,0,1,1],90,crop),crop): self.assertAlmostEqual(actual,wanted)
        expected={0:[.24,.42,.36,.78],90:[.28,.66,.52,.84],180:[.44,.42,.56,.78],270:[.28,.36,.52,.54]}
        for angle,box in expected.items():
            for actual,wanted in zip(map_box([.1,.2,.4,.8],angle,crop),box): self.assertAlmostEqual(actual,wanted)

    def test_pdf_crop_rotation_text_boxes(self):
        src=self.root/'rotated.pdf'
        with fitz.open() as pdf:
            p=pdf.new_page(width=400,height=600); p.insert_text((100,150),'Test 123')
            p.set_cropbox(fitz.Rect(50,50,350,550)); p.set_rotation(90); pdf.save(src)
        d=prepare_document(src,self.store.root)
        self.assertEqual(d['pages'][0]['pdf_rotation'],90)
        self.assertTrue(d['pages'][0]['embedded_words'])
        self.assertTrue(all(box_valid(w['box']) for w in d['pages'][0]['embedded_words']))


class GovernanceTests(Fixture):
    def test_profile_adds_missing_critical_fields_without_guessing(self):
        from workbench.profiles import apply_profile
        profile={'id':'synthetic','name':'Test','version':1,'required_fields':[{'key':'material','label':'Material','subject':'document','critical':True}]}
        d=self.approved(); d=apply_profile(self.store,d['id'],d['version'],profile,'test profile')
        self.assertEqual(d['profile'],'synthetic@1'); self.assertEqual(d['status'],'needs_review')
        r=d['requirements'][-1]; self.assertEqual(r['value'],''); self.assertEqual(r['state'],'missing')
        with self.assertRaises(ValueError): self.store.verify_requirement(d['id'],d['version'],r['id'],'test')

    def test_release_blocked_until_verified_approved(self):
        with self.assertRaises(ValueError): self.store.release(self.doc['id'],1,'test')
        with self.assertRaises(ValueError): self.store.approve(self.doc['id'],1,'test')
        d,p=self.released(); self.assertEqual(d['status'],'released')
        self.assertEqual(self.store.release(d['id'],d['version'],'again')[1]['release_id'],p['release_id'])
        self.assertTrue(self.store.verify_audit(d['id']))

    def test_edit_invalidates_approval_and_preserves_snapshot(self):
        d,p=self.released(); old=clone(p)
        d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'value':'10'},'test correction')
        self.assertEqual(d['status'],'needs_review'); self.assertNotIn('current_release',d)
        self.assertFalse(d['requirements'][0]['support_verified'])
        self.assertEqual(self.store.get_release(p['release_id']),old)
        self.assertEqual(self.store.search('coating'),[])

    def test_box_edit_invalidates_dependent_requirements(self):
        r=requirement('derived','9',evidence_ids=[self.ev['id']],dependencies=[self.req['id']])
        d=self.store.add_requirement(self.doc['id'],1,r,'test dependency')
        d=self.verify(d); d=self.verify(d,r['id'])
        e=clone(self.ev); e['box']=[.1,.1,.8,.2]
        d=self.store.save_region(d['id'],d['version'],e,None,'test move',replace_id=e['id'])
        self.assertTrue(all(r['status']=='needs_review' for r in d['requirements']))

    def test_optimistic_conflict_and_rollback(self):
        self.verify()
        with self.assertRaises(ConflictError): self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'5'},'stale')
        d=self.store.get(self.doc['id']); count=len(self.store.history(d['id']))
        with self.assertRaises(ValueError): self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'evidence_ids':['missing']},'bad ref')
        self.assertEqual(len(self.store.history(d['id'])),count)
        self.assertEqual(self.store.get(d['id']),d)

    def test_critical_missing_and_blank_are_not_zero(self):
        for state in ('missing','blank','unreadable','pending_decision','conflict'):
            d=self.store.get(self.doc['id'])
            d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'value':'','state':state},'unresolved')
            with self.assertRaises(ValueError): self.verify(d)

    def test_revoke_idempotent_and_audited(self):
        d,p=self.released(); self.assertTrue(self.store.search('coating'))
        self.store.revoke(p['release_id'],'test withdrawal'); self.store.revoke(p['release_id'],'repeated')
        self.assertEqual(self.store.get(d['id'])['status'],'rejected'); self.assertEqual(self.store.search('coating'),[])
        self.assertTrue(self.store.verify_audit(d['id']))

    def test_audit_and_snapshot_tampering_rejected(self):
        for table in ('events','snapshots'):
            with self.assertRaises(sqlite3.IntegrityError),self.store.connect() as c: c.execute(f'DELETE FROM {table}')
        with self.store.connect() as c: c.execute("UPDATE documents SET data=? WHERE id=?",(json.dumps({**self.doc,'name':'tampered'}),self.doc['id']))
        self.assertFalse(self.store.verify_audit(self.doc['id']))

    def test_backup_database(self):
        target=self.store.backup(self.root/'backup.sqlite3')
        con=sqlite3.connect(target)
        try: self.assertEqual(con.execute('SELECT count(*) FROM documents').fetchone()[0],1)
        finally: con.close()


class ExtractionTests(Fixture):
    def test_entire_json_compatibility_field_still_requires_schema(self):
        valid={'requirements':[],'warnings':[],'complete':False}
        body={'message':{'content':'','thinking':json.dumps(valid)},'done_reason':'stop'}
        with patch('workbench.extraction.local_request',return_value=body):
            data,stats=ask_vlm(Image.new('RGB',(100,100)),'','qwen3-vl:test','http://localhost',self.root/'trace')
        self.assertEqual(data,valid); self.assertEqual(stats['response_channel'],'structured_json_compatibility')

    def test_reanalysis_preserves_verified_value_and_flags_unit_conflict(self):
        d=self.verify(); out=clone(d); out['requirements'][0]['unit']='mm'; out['warnings']=[]; out['tool_runs']=[]
        d=apply_analysis(self.store,d['id'],d['version'],out)
        r=d['requirements'][0]
        self.assertEqual(r['unit'],'threads'); self.assertEqual(r['status'],'needs_review')
        self.assertEqual(len(d['regions']),1); self.assertTrue(r['candidates'])

    def test_remote_model_endpoint_blocked(self):
        for url in ('https://localhost:11434','http://example.com','http://user:pass@localhost:11434'):
            with self.assertRaises(ValueError): local_request(url)

    def test_vlm_bad_schema_and_inverted_box(self):
        item=dict(key='x',label='x',value='9',unit='',state='present',operator='eq',subject='document',scope='',conditions='',evidence='text',box=[.8,.1,.2,.9])
        with patch('workbench.extraction.local_request',return_value={'message':{'content':json.dumps({'requirements':[item],'warnings':[],'complete':True})}}):
            with self.assertRaises(ValueError): ask_vlm(Image.new('RGB',(100,100)),'','test','http://localhost',self.root/'trace')

    def test_unresolved_ocr_and_vlm_failure_retains_ocr(self):
        from drawing_intelligence.ocr import OCRResult
        from drawing_intelligence.models import TextRegion
        text='unresolved poor reading'
        ocr=OCRResult(text,[TextRegion(text,(20,40,160,65),.4,'synthetic OCR')],'synthetic OCR')
        with patch('drawing_intelligence.ocr.recognize_page',return_value=ocr),\
             patch('workbench.engineering_ocr.reread_engineering_regions',return_value=([],[])),\
             patch('workbench.extraction.ask_vlm',side_effect=TimeoutError('synthetic timeout')):
            out=analyze(self.doc,self.store.root,use_vlm=True)
        self.assertTrue(out['regions']); self.assertEqual(out['tool_runs'][0]['vlm_status'],'failed')
        self.assertEqual(out['regions'][0]['text'],text)
        self.assertTrue(any('synthetic timeout' in w for w in out['warnings']))

    def test_refinement_bounded_failure(self):
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'state':'conflict'},'test conflict')
        out=clone(d); out.update(requirements=[],regions=[],warnings=['synthetic failure'],tool_runs=[{'id':'failed_test','status':'failed'}])
        with patch('workbench.refinement.analyze',return_value=out) as mocked:
            result=refine(self.store,d['id'],d['version'],max_rounds=200)
        self.assertEqual(mocked.call_count,1); self.assertEqual(result['stop_reason'],'tool_failure')

    def test_cancel_before_work(self):
        out=analyze(self.doc,self.store.root,cancelled=lambda:True)
        self.assertTrue(out['cancelled']); self.assertEqual(out['tool_runs'],[])


class ExportApiTests(Fixture):
    def test_draft_bundle_and_pdf_annotations(self):
        path=export_bundle(self.store,self.doc['id'])
        self.assertTrue(verify_bundle(path)); self.assertEqual(export_bundle(self.store,self.doc['id']),path)
        with zipfile.ZipFile(path) as z:
            payload=json.loads(z.read('knowledge.json')); self.assertEqual(payload['export_status'],'draft')
            book=load_workbook(io.BytesIO(z.read('knowledge.xlsx')))
            self.assertEqual(book['需求'].max_row,2); book.close()
            with fitz.open(stream=z.read('evidence.pdf'),filetype='pdf') as pdf:
                page=pdf[0]; annots=list(page.annots()); self.assertEqual(len(annots),1)
                b=self.ev['box']; self.assertAlmostEqual(annots[0].rect.x0/page.rect.width,b[0],delta=.005)
                self.assertIn('DRAFT',pdf[0].get_text())

    def test_bundle_tamper_detected(self):
        path=export_bundle(self.store,self.doc['id'])
        with zipfile.ZipFile(path) as z: data={n:z.read(n) for n in z.namelist()}
        data['requirements.csv']=b'tampered'
        with zipfile.ZipFile(path,'w') as z:
            for name,value in data.items(): z.writestr(name,value)
        self.assertFalse(verify_bundle(path))
        with self.assertRaises(ValueError): export_bundle(self.store,self.doc['id'])

    def test_formula_injection_and_original_integrity(self):
        self.assertTrue(safe_cell('=CMD()').startswith("'"))
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'=1+1','operator':'text'},'test formula')
        path=export_bundle(self.store,d['id'])
        with zipfile.ZipFile(path) as z:
            book=load_workbook(io.BytesIO(z.read('knowledge.xlsx'))); self.assertEqual(book['需求']['E2'].data_type,'s'); book.close()
        Path(d['source_copy']).write_bytes(b'changed')
        with self.assertRaises(ValueError): export_bundle(self.store,d['id'])

    def test_api_auth_release_and_revocation(self):
        from fastapi.testclient import TestClient
        from workbench.api import create_app
        token='synthetic-test-token-123456789'
        client=TestClient(create_app(self.store.root,token)); headers={'Authorization':'Bearer '+token}
        self.assertEqual(client.get('/health').status_code,200)
        self.assertEqual(client.get('/releases').status_code,401)
        self.assertEqual(client.get('/search?q=coating',headers=headers).json(),[])
        d,p=self.released(); url='/releases/'+p['release_id']
        self.assertEqual(client.get(url,headers=headers).status_code,200)
        self.assertEqual(len(client.get('/search?q=coating',headers=headers).json()),1)
        self.store.revoke(p['release_id'],'test revoke')
        self.assertEqual(client.get(url,headers=headers).status_code,410)
        client.close()


class LearningTests(Fixture):
    def create_strategy(self):
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'10'},'Synthetic correction')
        self.approved(); service=StrategyService(self.store)
        s=service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','synthetic strategy')
        return service,s

    def cases(self):
        return [dict(source_sha256='a'*64,adjudicated=True,key='coating.minimum',subject='document',input_unit='threads',input_value='9',context='COATING MINIMUM',expected_value='10',expected_unit='threads',should_apply=True),
            dict(source_sha256='b'*64,adjudicated=True,key='coating.minimum',subject='document',input_unit='threads',input_value='9',context='ANOTHER FEATURE',expected_value='9',expected_unit='threads',should_apply=False)]

    def test_unapproved_source_and_untested_strategy_blocked(self):
        service=StrategyService(self.store)
        with self.assertRaises(ValueError): service.from_correction(self.doc['id'],self.req['id'],'COATING','test')
        service,s=self.create_strategy()
        with self.assertRaises(ValueError): service.set_status(s['id'],'active','test')

    def test_holdout_leakage_and_negative_case_gate(self):
        service,s=self.create_strategy(); cases=self.cases(); cases[0]['source_sha256']=s['source_sha256']
        with self.assertRaises(ValueError): service.evaluate(s['id'],cases,'test')
        service.evaluate(s['id'],self.cases()[:1],'positive only')
        with self.assertRaises(ValueError): service.set_status(s['id'],'active','test')

    def test_suggestions_manual_apply_rollback_and_source_withdrawal(self):
        service,s=self.create_strategy(); service.evaluate(s['id'],self.cases(),'test evaluation'); service.set_status(s['id'],'active','test activation')
        target=clone(self.doc); target['id']='synthetic-target'; target=self.store.add(target)
        self.assertEqual(len(service.suggest(target)),1)
        self.assertEqual(self.store.get(target['id'])['requirements'][0]['value'],'9')
        updated=service.apply(target['id'],target['version'],self.req['id'],s['id'],'manual test')
        self.assertEqual(updated['requirements'][0]['value'],'10'); self.assertEqual(updated['requirements'][0]['status'],'needs_review')
        service.set_status(s['id'],'disabled','test rollback'); self.assertEqual(service.suggest(target),[])
        service.set_status(s['id'],'active','test reactivate')
        source=self.store.get(self.doc['id']); self.store.reject(source['id'],source['version'],'source withdrawn')
        self.assertEqual(service.suggest(target),[])


if __name__=='__main__': unittest.main(verbosity=2)
