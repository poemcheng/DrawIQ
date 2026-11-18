import unittest

from tests.test_nailok_flow import Fixture
from workbench.domain import evidence
from workbench.nailok_flow import FEATURES, NailokService, stage_label_draft
from workbench.store import ConflictError, Store


class LabelApprovalTests(Fixture, unittest.TestCase):
    def add_source(self, doc, reading, source='synthetic OCR'):
        region=evidence(1,[.2,.36,.7,.40],reading,source)
        doc=self.store.save_region(doc['id'],doc['version'],region,None,'isolated test reading')
        return doc,region

    def current_rows(self, doc):
        return {row['key']:row for row in self.service.prefill(doc)}

    def approval_rows(self, doc):
        return {row['key']:row for row in self.service.label_approval(doc)['fields']}

    def test_one_approval_adopts_visible_ocr_preserves_blanks_and_stores_snapshot(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-2026-04')
        doc,price=self.add_source(doc,'報價：/ TWD / M')
        old=self.current_rows(doc)
        with self.assertRaisesRegex(ValueError,'對照原圖'):
            self.service.approve_label(doc['id'],doc['version'])
        accepted=self.service.approve_label(doc['id'],doc['version'],old.values(),human_checked=True)
        self.assertEqual(accepted['version'],doc['version']+1)
        self.assertNotIn(accepted['status'],('approved','released'))
        self.assertEqual(self.store.history(doc['id'])[0]['action'],'nailok_label_approve')
        reopened=Store(self.store.root).get(doc['id'])
        approval=NailokService(self.store).label_approval(reopened)
        self.assertEqual(approval['state'],'approved')
        self.assertEqual(approval['approved_version'],reopened['version'])
        self.assertEqual([field['key'] for field in approval['fields']],[key for key,*_ in FEATURES])
        fields={row['key']:row for row in approval['fields']}
        self.assertEqual(fields['quotation.number']['value'],'SYN-2026-04')
        self.assertEqual(fields['quotation.number']['evidence_ids'],[number['id']])
        self.assertEqual((fields['quotation.price']['state'],fields['quotation.price']['value'],
                          fields['quotation.price']['unit']),('pending','','TWD/M'))
        self.assertEqual(fields['quotation.price']['evidence_ids'],[price['id']])
        self.assertIn('客戶傳真',approval['unresolved_fields'])
        self.assertEqual(self.service.report(reopened)['label_approval']['state'],'approved')
        self.assertTrue(self.store.verify_audit(reopened['id']))

    def test_captured_draft_survives_reopening_and_replaces_original_proposal(self):
        doc,_=self.add_source(self.doc,'報價單號：SYN-OLD')
        old=self.service.approve_label(doc['id'],doc['version'],human_checked=True)
        region=evidence(1,[.5,.64,.8,.70],'SYN-CORRECTED','MinerU Basic:crop')
        draft={'key':'quotation.number','value':'SYN-CORRECTED','evidence_ids':[region['id']]}

        def capture(document):
            document['regions'].append(region)
            stage_label_draft(document,draft)
            return {'region_id':region['id']}

        captured=self.store.mutate(old['id'],old['version'],'synthetic_crop','isolated test capture',capture)
        visible=self.current_rows(Store(self.store.root).get(old['id']))['quotation.number']
        self.assertEqual(visible['status'],'框選待核准')
        self.assertFalse(visible['confirmed'])
        self.assertEqual(visible['value'],'SYN-CORRECTED')
        self.assertEqual(self.service.label_approval(captured)['state'],'stale')
        approved=self.service.approve_label(captured['id'],captured['version'],human_checked=True)
        snapshot=self.approval_rows(approved)['quotation.number']
        self.assertEqual((snapshot['value'],snapshot['evidence_ids']),('SYN-CORRECTED',[region['id']]))
        self.assertFalse(approved.get('nailok_label_drafts'))
        self.assertEqual(approved['regions'],captured['regions'])

    def test_retains_previously_confirmed_id_and_explicit_clear_stays_unresolved(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-KEEP')
        preset=self.service.confirm_many(doc['id'],doc['version'],[
            {'key':'quotation.number','value':'SYN-KEEP','evidence_ids':[number['id']]}
        ],human_checked=True)
        original_id=self.current_rows(preset)['quotation.number']['source']['id']
        approved=self.service.approve_label(preset['id'],preset['version'],human_checked=True)
        self.assertEqual(self.approval_rows(approved)['quotation.number']['requirement_id'],original_id)
        cleared=self.service.approve_label(approved['id'],approved['version'],[
            {'key':'quotation.number','value':'','state':'present','evidence_ids':[number['id']]}
        ],human_checked=True)
        self.assertEqual(self.approval_rows(cleared)['quotation.number']['state'],'pending')
        reopened=self.store.get(doc['id'])
        visible=self.current_rows(reopened)['quotation.number']
        self.assertEqual((visible['value'],visible['status']),('','待補資料'))
        self.assertNotIn('quotation.number',[row['key'] for row in self.service.report(reopened)['confirmed_features']])

    def test_missing_source_and_conflicting_document_version_roll_back(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-ROLLBACK')
        invalid=[{'key':'quotation.number','value':'SYN-ROLLBACK','evidence_ids':[number['id']]},
                 {'key':'contact.fax','value':'0900000000','evidence_ids':[]}]
        with self.assertRaisesRegex(ValueError,'框選補入來源'):
            self.service.approve_label(doc['id'],doc['version'],invalid,human_checked=True)
        self.assertEqual(self.store.get(doc['id']),doc)
        with self.assertRaises(ConflictError):
            self.service.approve_label(doc['id'],doc['version']-1,human_checked=True)
        self.assertEqual(self.service.label_approval(self.store.get(doc['id']))['state'],'pending')

    def test_editing_one_approved_source_makes_saved_verdict_stale(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-OLD')
        approved=self.service.approve_label(doc['id'],doc['version'],human_checked=True)
        edited=self.service.correct_annotation(doc['id'],approved['version'],number['id'],'報價單號：SYN-NEW')
        self.assertEqual(self.service.label_approval(edited)['state'],'stale')
        self.assertEqual(self.approval_rows(edited)['quotation.number']['value'],'SYN-OLD')
        self.assertEqual(self.current_rows(edited)['quotation.number']['value'],'SYN-NEW')

    def test_reopening_can_index_original_boxes_without_invalidating_label(self):
        doc,_=self.add_source(self.doc,'報價單號：SYN-INDEXED')
        approved=self.service.approve_label(doc['id'],doc['version'],human_checked=True)
        reopened=self.store.ensure_engineering_catalogue(doc['id'],approved['version'])
        self.assertGreater(reopened['version'],approved['version'])
        self.assertEqual(self.service.label_approval(reopened)['state'],'approved')
        self.assertEqual(self.current_rows(reopened)['quotation.number']['value'],'SYN-INDEXED')

    def test_all_blank_is_no_label_to_approve(self):
        draft=self.store.mutate(self.doc['id'],self.doc['version'],'blank_fixture','isolated test partial',
                                lambda d:(d.update(requirements=[],regions=[]) or {}))
        with self.assertRaisesRegex(ValueError,'至少一個'):
            self.service.approve_label(draft['id'],draft['version'],human_checked=True)
        self.assertEqual(self.store.get(draft['id']),draft)

    def test_final_operator_values_survive_reopen_and_keep_original_review_separate(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-LABEL-HANDOFF')
        doc,price=self.add_source(doc,'報價：/ TWD / M')
        doc,material=self.add_source(doc,'材質：SUS304')
        reviewed=self.current_rows(doc)
        updates=[
            {'key':'quotation.number','value':'SYN-LABEL-HANDOFF','unit':''},
            {'key':'quotation.project','value':'ISO-DQ-001'},
            {'key':'quotation.price','value':'125.50'},
            {'key':'material','value':'SUS316'},
        ]
        final_doc=self.service.approve_label(doc['id'],doc['version'],reviewed.values(),
                                             human_checked=True,final_inputs=updates)
        self.assertEqual(final_doc['version'],doc['version']+1)
        self.assertEqual(final_doc['regions'],doc['regions'])
        source_rows=self.approval_rows(final_doc)
        reopened=Store(self.store.root).get(doc['id'])
        approval=NailokService(self.store).label_approval(reopened)
        self.assertEqual(approval['state'],'approved')
        self.assertEqual([row['key'] for row in approval['final_fields']],[key for key,*_ in FEATURES])
        final={row['key']:row for row in approval['final_fields']}
        self.assertEqual(final['quotation.number']['evidence_ids'],[number['id']])
        self.assertEqual(final['quotation.number']['provenance'],'drawing')
        for key in ('quotation.price','quotation.project','material'):
            self.assertEqual(final[key]['provenance'],'operator_input')
            self.assertEqual(final[key]['evidence_ids'],[])
            self.assertIsNone(final[key]['requirement_id'])
        self.assertEqual((final['quotation.price']['value'],final['quotation.price']['unit']),('125.50','TWD/M'))
        self.assertEqual(source_rows['quotation.price']['value'],'')
        self.assertEqual(source_rows['quotation.price']['evidence_ids'],[price['id']])
        self.assertEqual(source_rows['material']['value'],'SUS304')
        self.assertEqual(source_rows['material']['evidence_ids'],[material['id']])
        self.assertEqual(self.current_rows(reopened)['material']['value'],'SUS304')
        self.assertNotIn('報價',approval['final_unresolved_fields'])
        self.assertNotIn('專案代號',approval['final_unresolved_fields'])
        self.assertNotIn('approved',(final_doc['status'],))
        self.assertTrue(self.store.verify_audit(doc['id']))

    def test_final_operator_price_rejects_conflicting_units_and_rolls_back(self):
        doc,_=self.add_source(self.doc,'報價單號：SYN-LABEL-ATOMIC')
        for inputs, message in [
            ([{'key':'quotation.price','value':'5 / TWD / M','unit':'USD/M'}],'不同'),
            ([{'key':'quotation.price','value':'-0.15','unit':'TWD/M'}],'非負'),
            ([{'key':'quotation.date','value':'2026/02/30'}],'有效的日期'),
        ]:
            with self.subTest(inputs=inputs):
                with self.assertRaisesRegex(ValueError,message):
                    self.service.approve_label(doc['id'],doc['version'],human_checked=True,final_inputs=inputs)
                self.assertEqual(self.store.get(doc['id']),doc)

    def test_empty_or_format_only_final_price_stays_pending_and_units_are_reusable(self):
        doc,_=self.add_source(self.doc,'報價單號：SYN-LABEL-EMPTY')
        approved=self.service.approve_label(doc['id'],doc['version'],human_checked=True,final_inputs=[
            {'key':'quotation.price','value':'/ TWD / M'},
        ])
        final={field['key']:field for field in self.service.label_approval(approved)['final_fields']}
        price=final['quotation.price']
        self.assertEqual((price['value'],price['unit'],price['state']),('','TWD/M','pending'))
        self.assertEqual(price['provenance'],'operator_input')
        self.assertEqual(price['evidence_ids'],[])
        revised=self.service.approve_label(doc['id'],approved['version'],human_checked=True,final_inputs=[
            {'key':'quotation.project','value':'SECOND-APPROVAL'},
        ])
        self.assertEqual(revised['nailok_label_approval']['final_fields'][12],price)
        self.assertEqual(self.service.label_approval(revised)['state'],'approved')

    def test_new_drawing_revision_marks_old_final_operator_payload_stale(self):
        doc,number=self.add_source(self.doc,'報價單號：SYN-LABEL-V1')
        approved=self.service.approve_label(doc['id'],doc['version'],human_checked=True,final_inputs=[
            {'key':'quotation.project','value':'OPERATOR-V1'},
        ])
        old_final=self.service.label_approval(approved)['final_fields']
        corrected=self.service.correct_annotation(doc['id'],approved['version'],number['id'],'報價單號：SYN-LABEL-V2')
        stale=self.service.label_approval(corrected)
        self.assertEqual(stale['state'],'stale')
        self.assertEqual(stale['final_fields'],old_final)

    def test_pre_existing_approval_has_same_snapshot_available_for_png(self):
        approved=self.service.approve_label(self.doc['id'],self.doc['version'],human_checked=True)
        original=approved['nailok_label_approval']
        for key in ('final_fields','final_fields_sha256','final_unresolved_fields'):
            original.pop(key)
        projected=self.service.label_approval(approved)
        self.assertEqual(projected['state'],'approved')
        self.assertEqual(len(projected['final_fields']),18)
        self.assertEqual(projected['final_unresolved_fields'],projected['unresolved_fields'])


if __name__=='__main__':
    unittest.main()
