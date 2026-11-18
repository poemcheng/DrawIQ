"""Synthetic evidence -> correction -> approval -> learning regression tests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import unittest
import zipfile
from unittest.mock import patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from workbench.canvas import DrawingCanvas
from workbench.domain import clone,evidence,requirement
from workbench.extraction import analyze,apply_analysis
from workbench.learning import StrategyService
from workbench.corpus import export_corpus
from workbench.store import ConflictError
from workbench.ui import MainWindow
from tests.test_workbench import Fixture
from tests.test_region_capture import dialog_stub


class CorrectionTests(Fixture):
    def region_edit(self,**fields):
        d=self.store.get(self.doc['id']); e=clone(d['regions'][0]); e.update(fields)
        return self.store.save_region(d['id'],d['version'],e,None,'Synthetic region correction',replace_id=d['regions'][0]['id'])

    def test_noop_saves_and_repeat_verify_preserve_release_and_audit(self):
        d,release=self.released(); history=self.store.history(d['id'])
        d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'value':'9','evidence_ids':[self.ev['id']],'upper_tolerance':''},'unchanged')
        d=self.store.save_region(d['id'],d['version'],d['regions'][0],None,'unchanged',replace_id=self.ev['id'])
        d=self.store.verify_requirement(d['id'],d['version'],self.req['id'],'already verified')
        self.assertEqual(d['current_release'],release['release_id']); self.assertEqual(d['status'],'released')
        self.assertEqual(self.store.history(d['id']),history)
        with self.assertRaises(ConflictError): self.store.edit_requirement(d['id'],1,self.req['id'],{'value':'9'},'stale no-op')

    def test_cancel_before_analysis_preserves_released_document(self):
        d,_=self.released(); output=analyze(d,self.store.root,cancelled=lambda:True)
        result=apply_analysis(self.store,d['id'],d['version'],output)
        self.assertEqual(result,d); self.assertTrue(self.store.verify_audit(d['id']))

    def test_region_change_records_all_links_and_invalidates_dependents(self):
        other=requirement('other','9',evidence_ids=[self.ev['id']])
        dep=requirement('derived','9',evidence_ids=[self.ev['id']],dependencies=[other['id']])
        d=self.store.add_requirement(self.doc['id'],1,other,'second link')
        d=self.store.add_requirement(d['id'],d['version'],dep,'dependency')
        for rid in (self.req['id'],other['id'],dep['id']): d=self.store.verify_requirement(d['id'],d['version'],rid,'verified')
        d=self.store.approve(d['id'],d['version'],'approved')
        d=self.region_edit(box=[.1,.12,.8,.22],text='TEST ONLY COATING MINIMUM 9 THREADS corrected')
        event=self.store.history(d['id'])[0]
        self.assertEqual(set(event['detail']['affected_requirement_ids']),{self.req['id'],other['id'],dep['id']})
        self.assertEqual(event['detail']['before']['box'],self.ev['box'])
        self.assertEqual(event['detail']['after']['box'],[.1,.12,.8,.22])
        self.assertTrue(all(r['status']=='needs_review' and not r['support_verified'] for r in d['requirements']))
        self.assertNotIn('approval',d); self.assertTrue(self.store.verify_audit(d['id']))

    def test_kind_change_migrates_links_atomically_and_invalid_box_rolls_back(self):
        d=self.region_edit(kind='feature'); r=d['requirements'][0]
        self.assertEqual(r['evidence_ids'],[]); self.assertEqual(r['feature_ids'],[self.ev['id']])
        with self.assertRaises(ValueError): self.region_edit(box=[-.1,.1,.8,.2])
        self.assertEqual(self.store.get(d['id']),d)

    def test_region_provenance_identity_cannot_be_overwritten(self):
        original=self.doc['regions'][0]
        d=self.region_edit(id='fake',source='fake',created_at='fake',text=original['text']+' fixed')
        for field in ('id','source','created_at'): self.assertEqual(d['regions'][0][field],original[field])

    def test_history_includes_link_removal_and_region_edits(self):
        d=self.region_edit(box=[.1,.1,.8,.2]); eid=self.ev['id']
        d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'evidence_ids':[]},'unlink')
        events=self.store.correction_history(d['id'],region_id=eid)
        self.assertEqual([e['action'] for e in events],['edit_requirement','edit_region'])
        self.assertIn('evidence_ids',events[0]['changed_fields'])
        self.assertEqual(events[1]['related_requirement_ids'],[self.req['id']])

    def test_legacy_region_event_associations_are_reconstructed(self):
        def legacy(d): d['regions'][0]['text']+=' fixed'; return {'region_id':self.ev['id'],'requirement_id':None,'before':self.ev,'after':d['regions'][0]}
        d=self.store.mutate(self.doc['id'],1,'edit_region','legacy record',legacy)
        events=self.store.correction_history(d['id'],rid=self.req['id'])
        self.assertEqual(len(events),1); self.assertEqual(events[0]['related_requirement_ids'],[self.req['id']])

    def test_region_case_requires_approval_then_retains_three_snapshots(self):
        d=self.region_edit(box=[.1,.1,.8,.25]); service=StrategyService(self.store)
        event_id=self.store.history(d['id'])[0]['id']
        with self.assertRaises(ValueError): service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','case',event_id=event_id)
        d=self.approved()
        s=service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','case',event_id=event_id)
        self.assertEqual(s['action'],'review_evidence'); self.assertEqual(s['status'],'reference')
        case=s['correction_case']; self.assertEqual(case['before']['regions'][0]['box'],self.ev['box'])
        self.assertEqual(case['after']['regions'][0]['box'],[.1,.1,.8,.25]); self.assertEqual(case['approved_version'],d['version'])
        self.assertTrue(service.source_is_current(s))
        with self.assertRaises(ValueError): service.set_status(s['id'],'active','no automatic coordinates')
        with self.assertRaises(ValueError): service.evaluate(s['id'],[{}],'no value evaluator')
        with self.assertRaises(ValueError): service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','duplicate',event_id=event_id)

    def test_region_hint_is_context_scoped_manual_and_stops_after_withdrawal(self):
        d=self.region_edit(text=self.ev['text']+' CORRECTED'); event=self.store.history(d['id'])[0]; d=self.approved()
        service=StrategyService(self.store); s=service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','case',event_id=event['id'])
        target=clone(self.doc); target['id']='target'; target=self.store.add(target)
        self.assertEqual(len(service.review_hints(target)),1); self.assertEqual(service.suggest(target),[])
        with self.assertRaises(ValueError): service.apply(target['id'],target['version'],self.req['id'],s['id'],'no auto apply')
        self.assertEqual(self.store.get(target['id']),target)
        service.set_status(s['id'],'disabled','pause'); self.assertEqual(service.review_hints(target),[])
        service.set_status(s['id'],'reference','restore'); self.assertEqual(len(service.review_hints(target)),1)
        different=clone(target); different['tenant']='another'; self.assertEqual(service.review_hints(different),[])
        different=clone(target); different['regions'][0]['text']='UNRELATED'; self.assertEqual(service.review_hints(different),[])
        self.store.reject(d['id'],d['version'],'withdraw source'); self.assertEqual(service.review_hints(target),[])

    def test_superseded_region_event_cannot_be_labeled_reviewed(self):
        d=self.region_edit(box=[.1,.1,.8,.25]); old=self.store.history(d['id'])[0]['id']
        self.region_edit(box=[.2,.1,.8,.25]); d=self.approved()
        with self.assertRaises(ValueError): StrategyService(self.store).from_correction(d['id'],self.req['id'],'COATING MINIMUM','stale',event_id=old)

    def test_unit_only_correction_becomes_testable_value_strategy(self):
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'unit':'turns'},'unit correction'); self.approved()
        service=StrategyService(self.store); s=service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','case')
        self.assertEqual(s['action'],'suggest_value'); self.assertEqual(s['input_unit'],'threads'); self.assertEqual(s['unit'],'turns')
        cases=[dict(source_sha256='a'*64,adjudicated=True,key='coating.minimum',subject='document',input_unit='threads',input_value='9',context='COATING MINIMUM',expected_value='9',expected_unit='turns',should_apply=True),
               dict(source_sha256='b'*64,adjudicated=True,key='coating.minimum',subject='document',input_unit='threads',input_value='9',context='UNRELATED',expected_value='9',expected_unit='threads',should_apply=False)]
        service.evaluate(s['id'],cases,'synthetic holdout'); service.set_status(s['id'],'active','enable suggestions')
        target=clone(self.doc); target['id']='target'; target=self.store.add(target)
        updated=service.apply(target['id'],1,self.req['id'],s['id'],'manual use')
        self.assertEqual(updated['requirements'][0]['unit'],'turns'); self.assertEqual(updated['status'],'needs_review')

    def test_corpus_exports_region_history_and_current_reviewed_cases(self):
        d=self.region_edit(box=[.1,.1,.8,.25]); event=self.store.history(d['id'])[0]; self.approved()
        service=StrategyService(self.store); service.from_correction(d['id'],self.req['id'],'COATING MINIMUM','case',event_id=event['id'])
        with zipfile.ZipFile(export_corpus(self.store)['corpus_bundle']) as z:
            records=[json.loads(x) for x in z.read('corrections.jsonl').decode().splitlines()]
            cases=[json.loads(x) for x in z.read('reviewed_correction_cases.jsonl').decode().splitlines()]
        self.assertEqual(records[0]['event']['action'],'edit_region'); self.assertTrue(records[0]['current_approved_match'])
        self.assertEqual(cases[0]['use'],'manual_review_reference'); self.assertEqual(cases[0]['split'],'unassigned')

    def test_value_strategy_rejects_later_changed_input_scope(self):
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'10'},'correct value'); event=self.store.history(d['id'])[0]
        self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'scope':'other feature'},'change scope'); d=self.approved()
        with self.assertRaises(ValueError): StrategyService(self.store).from_correction(d['id'],self.req['id'],'COATING MINIMUM','case',event_id=event['id'])


class ReviewUiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp(); self.window=MainWindow(self.store.root); self.window.load_documents(); self.window.show()
        self.window.workspace_tabs.setCurrentWidget(self.window.review_workspace); self.app.processEvents()
        self.window.reason.setText('Synthetic review')

    def tearDown(self):
        self.window.business_page.reset(); self.window.reset_form(); self.window.close(); self.window.deleteLater(); self.app.processEvents(); super().tearDown()

    def add_box(self,box=None):
        e=evidence(1,box or [.2,.4,.7,.5],'SECOND REGION')
        w=self.window; w.doc=self.store.save_region(w.doc['id'],w.doc['version'],e,None,'second region'); w.refresh()
        return e

    def gesture(self,canvas,start,end):
        a=canvas.mapFromScene(canvas.width_px*start[0],canvas.height_px*start[1]); b=canvas.mapFromScene(canvas.width_px*end[0],canvas.height_px*end[1])
        QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=a)
        QTest.mouseMove(canvas.viewport(),b,20); QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=b); self.app.processEvents()

    def test_selection_and_checkboxes_are_independent(self):
        w=self.window; e=self.add_box(); before=w.checked_region_ids(); rid=w.current_rid
        w.region_select.setCurrentIndex(w.region_select.findData(e['id']))
        self.assertEqual(w.current_region,e['id']); self.assertEqual(w.checked_region_ids(),before)
        self.assertFalse(w.form_dirty()); self.assertEqual(w.current_rid,rid)
        w.links.item(1).setCheckState(Qt.CheckState.Checked); self.assertTrue(w.form_dirty())
        w.save_links(); self.assertIn(e['id'],w.current_requirement()['evidence_ids'])

    def test_edit_text_uses_canvas_selection_and_history(self):
        w=self.window; e=self.add_box(); w.region_clicked(e['id'])
        with patch('workbench.ui.QInputDialog.getMultiLineText',return_value=('SECOND FIXED',True)): w.edit_region_text()
        self.assertEqual(next(x['text'] for x in w.doc['regions'] if x['id']==e['id']),'SECOND FIXED')
        self.assertEqual(w.doc['regions'][0]['text'],self.ev['text']); self.assertEqual(w.region_history.count(),2)
        detail=w.region_history.item(0).data(Qt.ItemDataRole.UserRole)['detail']
        self.assertEqual(detail['region_id'],e['id'])

    def test_page_switch_clears_off_page_selection(self):
        w=self.window
        def add_page(d):
            page=clone(d['pages'][0]); page['number']=2; d['pages'].append(page)
        w.doc=self.store.mutate(w.doc['id'],w.doc['version'],'synthetic_page','second page',add_page); w.refresh()
        w.region_clicked(self.ev['id']); w.page_select.setCurrentIndex(w.page_select.findData(2))
        self.assertIsNone(w.current_region); self.assertIsNone(w.canvas.selected_region); self.assertEqual(w.canvas.mode,'select')

    def test_kind_editor_migrates_checked_links_and_reason_is_shared(self):
        w=self.window; w.learning_reason.setText('Correct role'); self.assertEqual(w.reason.text(),'Correct role')
        with patch('workbench.ui.QInputDialog.getItem',return_value=('作用特徵',True)): w.edit_region_kind()
        self.assertEqual(w.current_requirement()['feature_ids'],[self.ev['id']]); self.assertEqual(w.current_requirement()['evidence_ids'],[])
        self.assertEqual(w.checked_region_ids(),[self.ev['id']]); self.assertEqual(w.current_requirement()['status'],'needs_review')

    def test_failed_move_does_not_discard_dirty_form(self):
        w=self.window; w.value.setPlainText('UNSAVED'); version=w.doc['version']
        with self.assertRaises(ValueError): w.move_region(self.ev['id'],[.1,.1,.8,.25])
        self.assertEqual(w.value.toPlainText(),'UNSAVED'); self.assertTrue(w.form_dirty()); self.assertEqual(w.doc['version'],version)
        with patch('workbench.ui.QInputDialog.getMultiLineText') as dialog:
            with self.assertRaises(ValueError): w.edit_region_text()
            dialog.assert_not_called()

    def test_move_without_optional_note_records_action_and_before_after(self):
        w=self.window; w.reason.clear(); box=[.1,.1,.8,.25]
        with patch('workbench.ui.CaptureDialog',dialog_stub()): w.move_region(self.ev['id'],box)
        event=self.store.history(w.doc['id'])[0]
        self.assertEqual(event['reason'],'人工修正原圖框位')
        self.assertEqual(event['detail']['before']['box'],self.ev['box'])
        self.assertEqual(event['detail']['after']['box'],box)
        self.assertEqual(w.canvas.normalized(w.canvas.region_items[self.ev['id']].rect()),box)

    def test_new_box_selects_result_and_cancel_leaves_no_history(self):
        w=self.window; w.canvas.set_mode('draw')
        with patch('workbench.ui.CaptureDialog',dialog_stub(text='NEW MANUAL')): w.new_region([.2,.4,.7,.5])
        eid=w.current_region; self.assertIn(eid,w.current_requirement()['evidence_ids'])
        self.assertEqual(w.canvas.mode,'select'); self.assertEqual(w.canvas.selected_region,eid)
        self.assertEqual(w.region_history.count(),1); before=clone(w.doc)
        w.canvas.set_mode('draw')
        with patch('workbench.ui.CaptureDialog',dialog_stub(accepted=False)): w.new_region([.2,.6,.7,.7])
        self.assertEqual(w.doc,before); self.assertEqual(w.canvas.mode,'select')

    def test_selected_history_builds_case_and_opens_original(self):
        w=self.window
        with patch('workbench.ui.CaptureDialog',dialog_stub()): w.move_region(self.ev['id'],[.1,.1,.8,.25])
        event=self.store.history(w.doc['id'])[0]
        w.verify_current(); w.approve_current(); w.use_history_for_learning(); w.context.setText('COATING MINIMUM'); w.create_strategy()
        self.assertIn('已核准',w.documents.currentItem().text()); self.assertIn('v'+str(w.doc['version']),w.documents.currentItem().text())
        s=w.learning.list()[0]; self.assertEqual(s['source_event_id'],event['id']); self.assertEqual(s['action'],'review_evidence')
        w.open_strategy_source(); self.assertEqual(w.doc['id'],self.doc['id']); self.assertEqual(w.current_region,self.ev['id'])

    def test_verify_next_saves_first_and_selects_pending_upstream(self):
        w=self.window; r=requirement('second','3',evidence_ids=[self.ev['id']])
        w.doc=self.store.add_requirement(w.doc['id'],w.doc['version'],r,'second'); w.refresh(); w.value.setPlainText('10')
        w.verify_next(); self.assertEqual(w.current_rid,r['id'])
        first=next(x for x in w.doc['requirements'] if x['id']==self.req['id'])
        self.assertEqual(first['value'],'10'); self.assertEqual(first['status'],'verified')

    def test_failed_verify_next_stays_on_saved_invalid_requirement(self):
        w=self.window; w.value.setPlainText('invalid numeric'); rid=w.current_rid
        with self.assertRaises(ValueError): w.verify_next()
        self.assertEqual(w.current_rid,rid); self.assertEqual(w.current_requirement()['status'],'needs_review')
        self.assertEqual(w.current_requirement()['value'],'invalid numeric')

    def test_default_drag_does_not_move_frame_or_write_event(self):
        w=self.window; original=clone(w.doc); self.gesture(w.canvas,(.4,.15),(.5,.3))
        self.assertEqual(w.doc,original); self.assertEqual(w.canvas.mode,'select')

    def test_edit_drag_commits_and_clamps_to_page_for_all_rotations(self):
        canvas=DrawingCanvas(); canvas.resize(750,750); canvas.show(); changes=[]
        canvas.region_changed.connect(lambda eid,box:changes.append(box))
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                canvas.show_page(self.doc['pages'][0],[self.ev],[self.ev['id']]); canvas.rotation=rotation; canvas.fit(); canvas.set_mode('edit'); self.app.processEvents()
                self.gesture(canvas,(.4,.15),(.8,.8)); box=changes[-1]
                self.assertTrue(0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1)
                self.assertAlmostEqual(box[2]-box[0],.82,delta=.01)
        self.assertEqual(len(changes),4); canvas.close(); canvas.deleteLater(); self.app.processEvents()

    def test_resize_after_rotation_and_escape_cancels_draw(self):
        canvas=DrawingCanvas(); canvas.resize(750,750); canvas.show(); changes=[]; created=[]
        canvas.region_changed.connect(lambda eid,box:changes.append(box)); canvas.region_created.connect(created.append)
        for rotation in (0,90,180,270):
            canvas.show_page(self.doc['pages'][0],[self.ev],[self.ev['id']]); canvas.rotation=rotation; canvas.fit(); canvas.set_mode('edit'); self.app.processEvents()
            self.gesture(canvas,(.9,.2),(.95,.35)); self.assertAlmostEqual(changes[-1][3],.35,delta=.015)
        canvas.set_mode('draw'); start=canvas.mapFromScene(canvas.width_px*.3,canvas.height_px*.4)
        QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start); QTest.keyClick(canvas,Qt.Key.Key_Escape)
        QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start); self.app.processEvents()
        self.assertEqual(created,[]); self.assertEqual(canvas.mode,'select'); self.assertIsNone(canvas.rubber)
        canvas.close(); canvas.deleteLater(); self.app.processEvents()


if __name__=='__main__': unittest.main(verbosity=2)
