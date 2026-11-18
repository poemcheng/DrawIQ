"""Isolated review regressions: crop evidence, version conflicts and Qt lifecycle."""
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from workbench.domain import clone, evidence, requirement
from workbench.store import Store, ConflictError
from workbench.ingest import prepare_document
from workbench.bounded import atomic_json
from workbench.region_capture import (prepare_capture, read_capture, save_capture, capture_dir,
    classify, copy_capture_artifacts)
from workbench.region_capture_ui import CaptureDialog


def manual_result(root, request, text='材質：SCM435', **extra):
    result=dict(status='complete', text=text, tokens=[], candidates=classify(text,[]),
                elapsed_seconds=.01, cached=False, capture_id=request['id'], **extra)
    atomic_json(capture_dir(root,request['id'])/'result.json',result)
    return result


def dialog_stub(text=None, action='frame', accepted=True):
    """Exercise real crop persistence while substituting the user's modal choice."""
    class Stub:
        def __init__(self,store,doc,page,box,**kw):
            self.store=store;self.doc=doc;self.kw=kw;self.page=page;self.box=box
            self.request=prepare_capture(doc,store.root,page,box,kw.get('rotation',0))
        def exec(self):
            if not accepted:return 0
            kw=self.kw
            request=self.request
            old=next((e for e in self.doc['regions'] if e['id']==kw.get('replace_id')),None)
            source_text=text if text is not None else old['text'] if old else 'TEST ONLY'
            result=manual_result(self.store.root,request,source_text)
            self.saved_document,self.saved_region_id,self.saved_requirement_id=save_capture(
                self.store,self.doc,request,result,text=source_text,action=action,value='SCM435',
                rid=kw.get('rid'),kind=kw.get('kind','evidence'),replace_id=kw.get('replace_id'),
                replace_links=kw.get('replace_links',False),reason=kw.get('reason') or 'Fixture capture')
            return 1
    return Stub


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.store=Store(self.base/'data');source=self.base/'source.png'
        Image.new('RGB',(400,500),'white').save(source)
        d=prepare_document(source,self.store.root,tenant='test')
        self.ev=evidence(1,[.1,.1,.8,.2],'材質：S45C');self.req=requirement('material','S45C',label='材質',evidence_ids=[self.ev['id']])
        self.other=requirement('customer.name','Test Customer',evidence_ids=[self.ev['id']])
        d['regions']=[self.ev];d['requirements']=[self.req,self.other]
        d['pages'][0]['recognition_status']='failed';self.doc=self.store.add(d)

    def capture(self,box=None,rotation=0):
        request=prepare_capture(self.doc,self.store.root,1,box or [.2,.3,.8,.6],rotation)
        return request,manual_result(self.store.root,request)

    def save(self,request,result,**kw):
        return save_capture(self.store,self.doc,request,result,text='材質：SCM435',rid=self.req['id'],**kw)

class CaptureTests(CaptureFixture):
    def test_crop_keeps_original_resolution_and_rotation(self):
        req,_=self.capture(rotation=90);root=capture_dir(self.store.root,req['id'])
        with Image.open(root/'crop.png') as image:self.assertEqual(image.size,(240,150))
        with Image.open(root/'input.png') as image:self.assertEqual(image.size,(150,240))
        self.assertEqual(req['pixel_box'],[80,150,320,300])
        self.assertEqual(self.store.get(self.doc['id']),self.doc)

    def test_bad_boxes_rejected_without_creating_artifacts(self):
        for box in ([-.1,.2,.3,.4],[.1,.1,.101,.2],[.1,.5,.3,.2]):
            with self.assertRaises(ValueError):self.capture(box)
        self.assertFalse((self.store.root/'captures').exists())

    def test_candidates_keep_printed_counts_numbers_and_symbols_separate(self):
        items=classify('數量：160 PCS\nM8 x 1.25 ±0.1 mm\nØ8',[])
        self.assertTrue(any(c['key']=='quotation.quantity' and c['value']=='160' for c in items))
        self.assertTrue(any(c['category']=='符號' and c['value']=='±' for c in items))
        self.assertFalse(any(c['category']=='符號' and c['value']=='⊥' for c in items))
        self.assertFalse(any(c['category']=='數量' for c in classify('M8 x 1.25',[])))
        self.assertFalse(any(c['key']=='quotation.quantity' for c in classify('160',[])))

    def test_frame_only_keeps_value_and_page_failure(self):
        req,res=self.capture();saved,eid,rid=self.save(req,res)
        self.assertEqual(saved['requirements'][0]['value'],'S45C')
        self.assertIn(eid,saved['requirements'][0]['evidence_ids'])
        self.assertEqual(saved['pages'],self.doc['pages'])
        self.assertEqual(saved['tool_runs'],self.doc['tool_runs'])
        self.assertEqual(saved['requirements'][1],self.other)

    def test_apply_changes_only_target_and_records_original_ocr(self):
        req,res=self.capture();saved,eid,_=self.save(req,res,action='apply',value='SCM435')
        self.assertEqual(saved['requirements'][0]['value'],'SCM435')
        self.assertEqual(saved['requirements'][0]['status'],'needs_review')
        self.assertEqual(saved['requirements'][1],self.other)
        event=self.store.correction_history(saved['id'],rid=self.req['id'])[0]
        self.assertEqual(event['detail']['requirement_before']['value'],'S45C')
        self.assertEqual(event['detail']['capture']['result']['text'],'材質：SCM435')
        self.assertTrue(self.store.verify_audit(saved['id']))

    def test_replace_retains_region_identity_and_invalidates_dependents(self):
        dep=requirement('derived','S45C',dependencies=[self.req['id']],status='verified',support_verified=True)
        self.doc=self.store.add_requirement(self.doc['id'],self.doc['version'],dep,'fixture')
        req,res=self.capture();saved,eid,_=self.save(req,res,replace_id=self.ev['id'])
        self.assertEqual(eid,self.ev['id']);self.assertEqual(len(saved['regions']),1)
        self.assertEqual(saved['requirements'][2]['status'],'needs_review')
        self.assertEqual(self.store.snapshot(saved['id'],self.doc['version'])['regions'][0],self.ev)

    def test_editing_unrelated_frame_does_not_attach_current_field(self):
        e=evidence(1,[.2,.4,.6,.6],'OTHER')
        self.doc=self.store.save_region(self.doc['id'],self.doc['version'],e,None,'fixture')
        req,res=self.capture();saved,eid,_=self.save(req,res,replace_id=e['id'])
        self.assertEqual(saved['requirements'][0]['evidence_ids'],[self.ev['id']])

    def test_stale_version_rolls_back_entire_capture(self):
        req,res=self.capture()
        self.store.edit_requirement(self.doc['id'],self.doc['version'],self.req['id'],{'value':'CHANGED'},'other reviewer')
        with self.assertRaises(ConflictError):self.save(req,res,action='apply',value='SCM435')
        self.assertEqual(len(self.store.get(self.doc['id'])['regions']),1)

    def test_tampered_crop_cannot_be_saved(self):
        req,res=self.capture();(capture_dir(self.store.root,req['id'])/'crop.png').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'裁圖'):self.save(req,res)
        self.assertEqual(self.store.get(self.doc['id']),self.doc)

    def test_cancelled_and_timed_out_ocr_never_changes_document(self):
        req,_=self.capture()
        with patch('workbench.region_capture.supervise',return_value='partial_timeout'):
            result=read_capture(self.store.root,req)
        self.assertEqual(result['status'],'partial_timeout');self.assertEqual(result['text'],'')
        with patch('workbench.region_capture.supervise') as supervise:
            result=read_capture(self.store.root,req,cancelled=lambda:True);supervise.assert_not_called()
        self.assertEqual(result['status'],'cancelled');self.assertEqual(self.store.get(self.doc['id']),self.doc)

    def test_cache_remaps_identical_pixels_to_new_page_coordinates(self):
        req,_=self.capture([.1,.1,.5,.5])
        def fake_worker(*args,**kw):
            atomic_json(capture_dir(self.store.root,req['id'])/'worker_result.json',dict(status='complete',text='TEST',
                tokens=[dict(text='TEST',box=[0,0,1,1],confidence=.9)],candidates=[]))
            return 'complete'
        with patch('workbench.region_capture.supervise',side_effect=fake_worker):first=read_capture(self.store.root,req)
        second_req,_=self.capture([.5,.5,.9,.9])
        with patch('workbench.region_capture.supervise') as worker:second=read_capture(self.store.root,second_req);worker.assert_not_called()
        self.assertFalse(first['cached']);self.assertTrue(second['cached'])
        self.assertEqual(second['tokens'][0]['page_box'],[.5,.5,.9,.9])

    def test_export_includes_historical_and_current_crops_and_verifies_hashes(self):
        first,res=self.capture();self.doc,eid,_=self.save(first,res)
        second,res=self.capture();self.doc,_,_=self.save(second,res,replace_id=eid)
        target=self.base/'export';ids=copy_capture_artifacts(self.store.root,self.doc,self.store.history(self.doc['id']),target)
        self.assertEqual(set(ids),{first['id'],second['id']})
        self.assertTrue((target/'captures'/first['id']/'crop.png').exists())
        from workbench.exporting import export_bundle,verify_bundle
        bundle=export_bundle(self.store,self.doc['id']);self.assertTrue(verify_bundle(bundle))
        with zipfile.ZipFile(bundle) as z:self.assertIn('captures/'+second['id']+'/crop.png',z.namelist())

    def test_new_field_has_evidence_without_touching_selected_field(self):
        req,res=self.capture();saved,eid,rid=self.save(req,res,action='new',value='SCM435',label='材質',key='material')
        self.assertEqual(saved['requirements'][0],self.req)
        new=next(r for r in saved['requirements'] if r['id']==rid)
        self.assertEqual(new['evidence_ids'],[eid]);self.assertEqual(new['status'],'needs_review')

    def test_full_page_reanalysis_preserves_manually_applied_value(self):
        req,res=self.capture();self.doc,_,_=self.save(req,res,action='apply',value='SCM435')
        from workbench.extraction import apply_analysis
        output=dict(requirements=[self.req],regions=[self.ev],pages=self.doc['pages'],tool_runs=[],warnings=[],replace_generated_pages=[1])
        saved=apply_analysis(self.store,self.doc['id'],self.doc['version'],output)
        self.assertEqual(saved['requirements'][0]['value'],'SCM435');self.assertFalse(saved['requirements'][0]['deleted'])

    def test_new_field_can_be_used_as_review_reference_after_approval(self):
        self.doc=self.store.mutate(self.doc['id'],self.doc['version'],'fixture','fixture',
            lambda d:d['pages'][0].update(recognition_status='complete'))
        req,res=self.capture();self.doc,_,rid=self.save(req,res,action='new',value='SCM435',label='新材質',key='new.material')
        event=self.store.history(self.doc['id'])[0]
        for r in self.doc['requirements']:
            self.doc=self.store.verify_requirement(self.doc['id'],self.doc['version'],r['id'],'fixture approval')
        self.doc=self.store.approve(self.doc['id'],self.doc['version'],'fixture approval')
        from workbench.learning import StrategyService
        case=StrategyService(self.store).from_correction(self.doc['id'],rid,'SCM435','fixture reference',event_id=event['id'])
        self.assertEqual(case['action'],'review_evidence');self.assertTrue(case['correction_case']['before']['requirement']['absent'])


class DialogTests(CaptureFixture):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def wait_for(self,check):
        deadline=time.monotonic()+3
        while not check() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertTrue(check())

    def test_auto_read_shows_candidates_and_apply_is_explicit(self):
        with patch('workbench.region_capture_ui.read_capture',side_effect=lambda root,r,**kw:manual_result(root,r)):
            dialog=CaptureDialog(self.store,self.doc,1,[.2,.3,.8,.6],rid=self.req['id']);dialog.show()
            self.wait_for(lambda:dialog.worker is None and dialog.ocr_result is not None)
            self.assertEqual(dialog.value.toPlainText(),'SCM435');self.assertGreater(dialog.candidates.rowCount(),0)
            self.assertEqual(self.store.get(self.doc['id']),self.doc)
            dialog.save('apply');self.assertEqual(dialog.saved_document['requirements'][0]['value'],'SCM435')
            dialog.deleteLater();self.app.processEvents()

    def test_late_result_does_not_overwrite_manual_text(self):
        def slow(root,r,**kw):time.sleep(.08);return manual_result(root,r)
        with patch('workbench.region_capture_ui.read_capture',side_effect=slow):
            dialog=CaptureDialog(self.store,self.doc,1,[.2,.3,.8,.6],rid=self.req['id']);dialog.show()
            dialog.raw.insertPlainText('人工原文');dialog.value.insertPlainText('人工內容')
            self.wait_for(lambda:dialog.worker is None and dialog.ocr_result is not None)
            self.assertEqual(dialog.raw.toPlainText(),'人工原文');self.assertEqual(dialog.value.toPlainText(),'人工內容')
            dialog.reject();dialog.deleteLater();self.app.processEvents()

    def test_close_waits_for_worker_cancel_without_saving(self):
        def cancellable(root,r,**kw):
            while not kw['cancelled']():time.sleep(.005)
            return manual_result(root,r)
        with patch('workbench.region_capture_ui.read_capture',side_effect=cancellable):
            dialog=CaptureDialog(self.store,self.doc,1,[.2,.3,.8,.6]);dialog.show();self.app.processEvents()
            dialog.reject();self.wait_for(lambda:dialog.worker is None)
            self.assertFalse(dialog.isVisible());self.assertEqual(self.store.get(self.doc['id']),self.doc)
            dialog.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
