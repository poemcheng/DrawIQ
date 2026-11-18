"""Review integration, state gates and lossless recovery; isolated synthetic data."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtTest import QTest
from workbench.domain import requirement, clone
from workbench.store import ConflictError
from workbench.ui import MainWindow
from workbench.review_ui import ReviewQueueDialog
from tests.test_workbench import Fixture
from tests.test_quotation_workspace import add_grounded


class UnifiedStoreTests(Fixture):
    def test_legacy_verified_unreadable_cannot_be_reconfirmed_or_approved(self):
        def legacy(doc):
            doc['requirements'][0].update(state='unreadable',critical=False,status='verified',support_verified=True)
        d=self.store.mutate(self.doc['id'],self.doc['version'],'synthetic_legacy','test old status',legacy)
        for fn in (lambda:self.store.approve(d['id'],d['version'],'test'),
                   lambda:self.store.verify_requirement(d['id'],d['version'],self.req['id'],'test'),
                   lambda:self.store.review_requirements(d['id'],d['version'],[self.req['id']],'test')):
            with self.assertRaises(ValueError): fn()
        self.assertEqual(self.store.get(d['id']),d)

    def test_single_and_batch_reject_same_unresolved_noncritical_states(self):
        for state in ('unreadable','pending_decision','conflict'):
            with self.subTest(state=state):
                d=self.store.get(self.doc['id'])
                d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'state':state,'critical':False},'synthetic')
                before=clone(d); history=len(self.store.history(d['id']))
                for batch in (False,True):
                    with self.assertRaises(ValueError):
                        if batch: self.store.review_requirements(d['id'],d['version'],[self.req['id']],'synthetic')
                        else: self.store.verify_requirement(d['id'],d['version'],self.req['id'],'synthetic')
                    self.assertEqual(self.store.get(d['id']),before)
                    self.assertEqual(len(self.store.history(d['id'])),history)

    def test_repeat_batch_confirmation_preserves_release_and_version(self):
        d,payload=self.released(); history=len(self.store.history(d['id']))
        result=self.store.review_requirements(d['id'],d['version'],[self.req['id']],'repeat')
        self.assertEqual(d,result); self.assertEqual(len(self.store.history(d['id'])),history)

    def test_exclude_restore_retains_evidence_and_history_but_requires_review(self):
        d=self.approved()
        d=self.store.review_requirements(d['id'],d['version'],[self.req['id']],'synthetic exclusion',action='exclude')
        exclusion=self.store.history(d['id'])[0]
        d=self.store.undo_review_exclusion(d['id'],d['version'],'synthetic undo')
        r=d['requirements'][0]
        self.assertFalse(r['deleted']); self.assertEqual(r['status'],'needs_review'); self.assertFalse(r['support_verified'])
        self.assertNotIn('approval',d); self.assertEqual(r['evidence_ids'],[self.ev['id']])
        self.assertEqual(self.store.history(d['id'])[0]['detail']['undo_event_id'],exclusion['id'])
        self.assertTrue(self.store.verify_audit(d['id']))

    def test_restore_rejects_stale_version_and_intervening_changes(self):
        d=self.store.review_requirements(self.doc['id'],self.doc['version'],[self.req['id']],'exclude',action='exclude')
        with self.assertRaises(ValueError): self.store.undo_review_exclusion(d['id'],d['version']-1,'stale')
        d=self.store.add_requirement(d['id'],d['version'],requirement('other','9',evidence_ids=[self.ev['id']]),'new')
        with self.assertRaises(ValueError): self.store.undo_review_exclusion(d['id'],d['version'],'too late')
        self.assertTrue(d['requirements'][0]['deleted'])


class UnifiedReviewUiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp(); self.window=MainWindow(self.store.root); self.window.load_documents(); self.window.show()
        self.window.open_review_queue(); self.app.processEvents()

    def tearDown(self):
        self.window.business_page.reset(); self.window.reset_form(); self.window.close()
        self.window.deleteLater(); self.app.processEvents(); super().tearDown()

    def add_row(self,key='note.test',value='9',**kwargs):
        w=self.window
        if key=='material':
            w.doc,r=add_grounded(self.store,w.doc,key,value,label='材質',**kwargs)
        else:
            r=requirement(key,value,evidence_ids=[self.ev['id']],**kwargs)
            w.doc=self.store.add_requirement(w.doc['id'],w.doc['version'],r,'synthetic')
        w.refresh(); return r

    def test_old_entry_and_new_entry_share_editor_canvas_and_dirty_draft(self):
        w=self.window; canvas=w.canvas; table=w.requirements; rid=w.current_rid
        w.value.setPlainText('10'); ReviewQueueDialog(w).exec()
        self.assertIs(w.workspace_tabs.currentWidget(),w.review_workspace)
        self.assertIs(canvas,w.canvas); self.assertIs(table,w.requirements); self.assertEqual(rid,w.current_rid)
        self.assertEqual(w.value.toPlainText(),'10'); self.assertTrue(w.form_dirty())
        self.assertFalse(any(isinstance(x,QDialog) and x.isVisible() for x in self.app.topLevelWidgets()))

    def test_primary_click_saves_confirms_and_advances_without_note(self):
        w=self.window; r=self.add_row('material','鐵材'); w.value.setPlainText('10')
        QTest.mouseClick(w.next_btn,Qt.MouseButton.LeftButton)
        first=w.doc['requirements'][0]
        self.assertEqual(first['value'],'10'); self.assertEqual(first['status'],'verified')
        self.assertEqual(w.current_rid,r['id']); self.assertEqual(w.current_region,r['evidence_ids'][0])
        events=self.store.history(w.doc['id'])
        self.assertEqual(events[0]['action'],'verify_requirement'); self.assertEqual(events[1]['action'],'edit_requirement')
        self.assertEqual(events[1]['detail']['before']['value'],'9')
        self.assertEqual(events[1]['detail']['after']['value'],'10')

    def test_confirmation_failure_stays_inline_and_preserves_saved_edit(self):
        w=self.window; rid=w.current_rid; w.value.setPlainText('invalid number')
        with patch('workbench.ui.QMessageBox.warning') as modal:
            QTest.mouseClick(w.next_btn,Qt.MouseButton.LeftButton); modal.assert_not_called()
        self.assertEqual(w.current_rid,rid); self.assertEqual(w.current_requirement()['value'],'invalid number')
        self.assertEqual(w.current_requirement()['status'],'needs_review')
        self.assertTrue(w.review_feedback.text()); self.assertFalse(w.form_dirty())

    def test_filter_clears_checks_and_batch_applies_only_visible_rows(self):
        w=self.window; other=self.add_row('material','鐵材')
        w.check_visible_review(True)
        self.assertEqual(set(w.checked_review_ids()),{r['id'] for r in w.doc['requirements'] if not r['deleted']})
        w.req_filter.setText('鐵材'); self.assertEqual(w.checked_review_ids(),[])
        w.check_visible_review(True); self.assertEqual(w.checked_review_ids(),[other['id']])
        w.commit_review_batch('verify')
        rows={r['id']:r for r in w.doc['requirements']}
        self.assertEqual(rows[other['id']]['status'],'verified'); self.assertEqual(rows[self.req['id']]['status'],'needs_review')

    def test_selecting_a_row_does_not_check_it(self):
        w=self.window; other=self.add_row(); w.select_review_id(other['id'])
        self.assertEqual(w.checked_review_ids(),[]); self.assertFalse(w.batch_verify.isEnabled())
        self.assertEqual(w.current_rid,other['id'])

    def test_group_filter_stays_and_next_item_is_in_same_group(self):
        w=self.window; note=self.add_row('note.test','9'); field=self.add_row('material','鐵材')
        group=w.review_group_by_rid[self.req['id']]['id']
        w.review_group.setCurrentIndex(w.review_group.findData(group)); w.verify_next()
        self.assertEqual(w.review_group.currentData(),group); self.assertEqual(w.current_rid,field['id'])
        self.assertEqual(next(r for r in w.doc['requirements'] if r['id']==note['id'])['status'],'needs_review')

    def test_dirty_form_blocks_filter_and_batch_without_discarding_text(self):
        w=self.window; self.add_row(); w.check_visible_review(True); w.value.setPlainText('10')
        w.req_filter.setText('NO MATCH'); self.assertEqual(w.req_filter.text(),'')
        with self.assertRaises(ValueError): w.commit_review_batch('verify')
        self.assertEqual(w.value.toPlainText(),'10'); self.assertTrue(w.form_dirty())

    def test_source_navigation_reveals_confirmed_item_hidden_by_filters(self):
        w=self.window; other=self.add_row('note.test','9'); fact=w.summary_page.data['facts'][0]
        w.verify_current(); w.review_state.setCurrentIndex(0); w.req_filter.setText('note.test')
        w.navigate_fact(w.doc,fact)
        self.assertEqual(w.current_rid,self.req['id']); self.assertEqual(w.current_region,self.ev['id'])
        self.assertFalse(w.requirements.isRowHidden(w.requirements.currentRow()))

    def test_empty_filter_disables_confirmation_and_clears_old_source(self):
        w=self.window; w.req_filter.setText('NO MATCH')
        self.assertIsNone(w.current_rid); self.assertFalse(w.next_btn.isEnabled())
        self.assertEqual(w.value.toPlainText(),''); self.assertFalse(w.form_dirty())
        w.req_filter.clear(); self.assertEqual(w.current_rid,self.req['id']); self.assertTrue(w.next_btn.isEnabled())

    def test_exclusion_is_recoverable_in_same_workspace(self):
        w=self.window; self.add_row('material','鐵材'); w.exclude_current_review()
        self.assertTrue(w.undo_exclude.isEnabled()); w.undo_last_exclusion()
        self.assertEqual(w.current_rid,self.req['id']); self.assertFalse(w.current_requirement()['deleted'])
        self.assertEqual(w.current_requirement()['status'],'needs_review'); self.assertFalse(w.undo_exclude.isEnabled())
        self.assertTrue(self.store.verify_audit(w.doc['id']))

    def test_exclusion_dependency_guard_is_same_for_individual_and_batch(self):
        w=self.window; self.add_row('dependent','9',dependencies=[self.req['id']])
        before=clone(w.doc)
        with self.assertRaises(ValueError): w.delete_current()
        self.assertEqual(w.doc,before)

    def test_batch_failure_is_atomic_and_keeps_selection_for_fixing(self):
        w=self.window; self.add_row('bad','',state='unreadable')
        w.check_visible_review(True); before=clone(w.doc); checked=w.checked_review_ids()
        QTest.mouseClick(w.batch_verify,Qt.MouseButton.LeftButton)
        self.assertEqual(w.doc,before); self.assertEqual(w.checked_review_ids(),checked)
        self.assertIn('無法辨讀',w.review_feedback.text())

    def test_focus_evidence_does_not_modify_box_or_associations_at_any_rotation(self):
        w=self.window; before=clone(w.doc)
        for rotation in (0,90,180,270):
            w.canvas.rotation=rotation; w.canvas.fit(); w.focus_review_evidence()
            self.assertEqual(w.doc,before); self.assertFalse(w.form_dirty())
            self.assertEqual(w.checked_region_ids(),[self.ev['id']])

    def test_details_can_be_hidden_without_losing_conditions(self):
        w=self.window; w.detail_toggle.setChecked(True); w.fields['conditions'].setText('只適用 A 型')
        w.detail_toggle.setChecked(False); w.save_current()
        self.assertEqual(w.current_requirement()['conditions'],'只適用 A 型')
        self.assertIn('只適用 A 型',w.review_evidence.toPlainText())

    def test_no_note_does_not_enable_learning_strategy_decision(self):
        with self.assertRaises(ValueError): self.window.reason_text()

    def test_hidden_knowledge_page_is_refreshed_only_when_opened(self):
        w=self.window
        with patch.object(w.corpus_page,'refresh',wraps=w.corpus_page.refresh) as refresh:
            w.verify_current(); refresh.assert_not_called()
            w.workspace_tabs.setCurrentWidget(w.corpus_page)
            refresh.assert_called_once_with(w.doc)
            self.assertEqual(w.corpus_page.graph.doc['version'],w.doc['version'])

    def test_small_screen_keeps_fields_and_primary_action_visible(self):
        w=self.window; w.resize(1280,800); self.app.processEvents()
        for widget in (w.value,w.unit,w.value_state,w.next_btn,w.requirements):
            self.assertFalse(widget.visibleRegion().isEmpty())
            center=widget.mapTo(w,widget.rect().center())
            self.assertTrue(w.rect().contains(center))
