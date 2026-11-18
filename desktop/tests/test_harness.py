"""Failure attribution, replay integrity, release gates and UI discoverability."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication,QDialog,QTabWidget
from workbench.domain import clone,digest
from workbench.harness import inspect_document,enforce_quotation_contract
from workbench import __version__
from workbench.harness_validation import (check_repository,confirmed_fixture,document_for_case,
                                         evaluate_cases,latest_validation,source_fingerprint)
from workbench.ui import MainWindow
from tests.test_workbench import Fixture
from tests.test_quotation_workspace import add_grounded


class HarnessTests(Fixture):
    def test_declared_regressions_cover_known_bad_field_assignments_without_claiming_ocr(self):
        path=Path(__file__).resolve().parents[1]/'evaluation'/'harness_cases.json'
        result=evaluate_cases(json.loads(path.read_text(encoding='utf-8')))
        self.assertTrue(result['passed'],result['cases'])
        self.assertEqual(result['case_count'],16); self.assertFalse(result['accuracy_benchmark'])
        self.assertFalse(result['ocr_or_vlm_executed']); self.assertGreater(result['assertion_count'],30)

    def test_inspection_is_read_only_and_distinguishes_readiness_from_complete_quotation(self):
        d=self.verify(); before=clone(d); report=inspect_document(d)
        self.assertEqual(d,before); self.assertEqual(self.store.get(d['id']),before)
        self.assertTrue(report['review_ready']); self.assertFalse(report['quotation_review_complete'])
        self.assertFalse(report['quotation_values_complete']); self.assertFalse(report['accuracy_verified'])

    def test_noncritical_quotation_without_evidence_confirmation_cannot_be_approved_atomically(self):
        d=self.verify(); d,r=add_grounded(self.store,d,'material','S45C',raw='材質：S45C')
        def corrupt(doc):
            row=next(x for x in doc['requirements'] if x['id']==r['id'])
            row.update(status='verified',support_verified=False)
            doc.setdefault('quotation_mapping',{})['material']=r['id']
        d=self.store.mutate(d['id'],d['version'],'synthetic_legacy_state','Simulate bad legacy confirmation',corrupt)
        before=clone(d); history=self.store.history(d['id'])
        with self.assertRaisesRegex(ValueError,'DQH-HUMAN-PROOF'):
            self.store.approve(d['id'],d['version'],'synthetic')
        self.assertEqual(self.store.get(d['id']),before); self.assertEqual(self.store.history(d['id']),history)

    def test_release_rechecks_quotation_evidence_and_rolls_back(self):
        d=self.verify(); d,r=add_grounded(self.store,d,'material','S45C',raw='材質：S45C')
        d=self.store.verify_requirement(d['id'],d['version'],r['id'],'synthetic')
        d=self.store.approve(d['id'],d['version'],'synthetic')
        def corrupt(doc):
            next(x for x in doc['requirements'] if x['id']==r['id'])['support_verified']=False
        d=self.store.mutate(d['id'],d['version'],'synthetic_corruption','Simulate corrupted approved state',corrupt)
        with self.assertRaisesRegex(ValueError,'DQH-HUMAN-PROOF'):
            self.store.release(d['id'],d['version'],'synthetic')
        self.assertEqual(self.store.get(d['id']),d); self.assertEqual(self.store.list_releases(),[])

    def test_manual_role_override_is_explicit_warning_not_silent_new_answer(self):
        case={'id':'manual','context':[{'text':'Threadlock specifications','box':[.6,.1,.95,.13]}],
              'items':[{'key':'material','value':'Nylon Tuflok','raw':'Material: Nylon Tuflok','box':[.6,.14,.95,.18]}],
              'mapping':{'material':0}}
        d=document_for_case(case); before=clone(d); report=inspect_document(d)
        self.assertTrue(any(f['code']=='DQH-MANUAL-ROLE' for f in report['findings']))
        enforce_quotation_contract(d)
        self.assertEqual(d,before); self.assertEqual(next(f for f in report['fields'] if f['key']=='material')['value'],'Nylon Tuflok')

    def test_case_export_requires_current_human_review_and_retains_source_provenance(self):
        d,r=add_grounded(self.store,self.doc,'material','S45C',raw='材質：S45C')
        d=self.store.set_quotation_source(d['id'],d['version'],'material',r['id'],'synthetic')
        with self.assertRaises(ValueError): confirmed_fixture(d,'material')
        d=self.store.verify_requirement(d['id'],d['version'],r['id'],'synthetic')
        fixture=confirmed_fixture(d,'material'); result=evaluate_cases(fixture)
        self.assertTrue(result['passed']); self.assertEqual(result['kind'],'human_confirmed_replay')
        self.assertEqual(fixture['cases'][0]['provenance']['source_sha256'],d['source_sha256'])
        d=self.store.edit_requirement(d['id'],d['version'],r['id'],{'value':'S50C'},'synthetic correction')
        with self.assertRaises(ValueError): confirmed_fixture(d,'material')

    def test_empty_case_and_wrong_expected_value_do_not_receive_a_pass(self):
        with self.assertRaises(ValueError): evaluate_cases({'schema':'drawiq-harness-cases/1','cases':[]})
        no_expected={'id':'no-expected','items':[{'key':'material','value':'S45C','raw':'材質：S45C'}]}
        result=evaluate_cases({'schema':'drawiq-harness-cases/1','cases':[no_expected]})
        self.assertFalse(result['passed'])
        no_expected['expect_fields']={'material':{'has_source':True,'value':'SCM435'}}
        self.assertFalse(evaluate_cases({'schema':'drawiq-harness-cases/1','cases':[no_expected]})['passed'])

    def test_repository_guard_detects_nested_gui_import_and_broken_doc_link(self):
        app=self.root/'repository'; (app/'workbench').mkdir(parents=True)
        (app/'workbench/domain.py').write_text('def bad():\n    from PyQt6 import QtWidgets\n',encoding='utf-8')
        (app/'AGENTS.md').write_text('[broken](absent.md)',encoding='utf-8')
        codes={f['code'] for f in check_repository(app)['findings']}
        self.assertIn('ARCH-BOUNDARY',codes); self.assertIn('DOC-LINK',codes)

    def make_validation_report(self):
        root=self.root/'repository'; (root/'workbench').mkdir(parents=True)
        (root/'workbench/domain.py').write_text('# Original source\n',encoding='utf-8')
        cases=root/'cases.json'; cases.write_text('{"schema":"fixture"}',encoding='utf-8')
        run=root/f'qa/harness_{__version__}/runs/example'; run.mkdir(parents=True)
        log=run/'tests.log'; log.write_text('OK\n',encoding='utf-8')
        data={'source_fingerprint':source_fingerprint(root),'passed':True,'cases_path':str(cases),
              'contracts':{'cases_sha256':digest(json.loads(cases.read_text(encoding='utf-8')))},
              'tests':{'log':'tests.log','log_sha256':hashlib.sha256(log.read_bytes()).hexdigest()}}
        path=run/'report.json'; path.write_text(json.dumps(data),encoding='utf-8')
        pointer={'path':'runs/example/report.json','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        (root/f'qa/harness_{__version__}/latest.json').write_text(json.dumps(pointer),encoding='utf-8')
        return root

    def test_changed_source_or_case_marks_report_stale(self):
        root=self.make_validation_report(); self.assertEqual(latest_validation(root)['state'],'current')
        (root/'cases.json').write_text('{"schema":"changed"}',encoding='utf-8')
        self.assertEqual(latest_validation(root)['state'],'stale')
        (root/'cases.json').write_text('{"schema":"fixture"}',encoding='utf-8')
        (root/'workbench/domain.py').write_text('# Changed source\n',encoding='utf-8')
        self.assertEqual(latest_validation(root)['state'],'stale')

    def test_tampered_log_or_escape_reference_is_invalid(self):
        root=self.make_validation_report()
        (root/f'qa/harness_{__version__}/runs/example/tests.log').write_text('Changed log',encoding='utf-8')
        self.assertEqual(latest_validation(root)['state'],'invalid')
        (root/f'qa/harness_{__version__}/latest.json').write_text('{"path":"../../outside.json","sha256":"x"}',encoding='utf-8')
        self.assertEqual(latest_validation(root)['state'],'invalid')

    def test_invalid_case_input_remains_an_inspectable_failed_report(self):
        root=self.make_validation_report(); cases=root/'cases.json'; cases.write_text('{invalid',encoding='utf-8')
        path=root/f'qa/harness_{__version__}/runs/example/report.json'
        data=json.loads(path.read_text(encoding='utf-8')); data['passed']=False
        data['contracts']={'kind':'invalid_fixture','passed':False,'error':'Invalid JSON'}
        data['cases_file_sha256']=hashlib.sha256(cases.read_bytes()).hexdigest()
        path.write_text(json.dumps(data),encoding='utf-8')
        (root/f'qa/harness_{__version__}/latest.json').write_text(json.dumps({
            'path':'runs/example/report.json','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}),encoding='utf-8')
        result=latest_validation(root)
        self.assertEqual(result['state'],'current'); self.assertFalse(result['passed'])
        self.assertEqual(result['report']['contracts']['error'],'Invalid JSON')

    def test_unknown_structure_returns_failure_report_instead_of_crashing_ui(self):
        report=inspect_document({'id':'bad','version':1})
        self.assertEqual(report['contract_status'],'fail'); self.assertEqual(report['findings'][0]['code'],'DQH-STRUCTURE')

    def test_invalid_units_or_truthy_expectations_cannot_bypass_boundary_validation(self):
        bad=clone(self.doc); bad['requirements'][0]['unit']=None
        self.assertEqual(inspect_document(bad)['contract_status'],'fail')
        with self.assertRaisesRegex(ValueError,'案例輸入格式無效'):
            evaluate_cases({'schema':'drawiq-harness-cases/1','cases':[{'id':'bad','items':[
                {'key':'material','value':'S45C'}],'expect_fields':{'material':{'has_source':'false'}}}]})

    def test_duplicate_case_ids_do_not_inflate_validation_coverage(self):
        case={'id':'duplicate','items':[{'key':'material','value':'S45C'}],'expect_ready':False}
        with self.assertRaisesRegex(ValueError,'案例 ID 不可重複'):
            evaluate_cases({'schema':'drawiq-harness-cases/1','cases':[case,clone(case)]})

    def test_export_attaches_exact_document_contract_snapshot(self):
        import zipfile
        from workbench.exporting import export_bundle,verify_bundle
        path=export_bundle(self.store,self.doc['id']); self.assertTrue(verify_bundle(path))
        with zipfile.ZipFile(path) as archive:
            report=json.loads(archive.read('harness.json'))
        self.assertEqual(report['policy_revision'],__version__)
        self.assertEqual(report['document_version'],self.doc['version'])
        self.assertEqual(report['source_sha256'],self.doc['source_sha256'])
        self.assertFalse(report['accuracy_verified'])


class HarnessUiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_rules_and_version_report_are_discoverable_without_running_models(self):
        w=MainWindow(self.store.root); w.load_documents(); before=clone(w.doc); titles=[]
        def inspect(dialog):
            tabs=dialog.findChild(QTabWidget); titles.extend(tabs.tabText(i) for i in range(tabs.count()))
            return 0
        with patch.object(QDialog,'exec',inspect),patch('workbench.extraction.local_request') as network:
            w.show_method_report(); network.assert_not_called()
        self.assertIn('工程規則／本文件驗證',titles); self.assertIn('版本驗證紀錄',titles)
        self.assertEqual(w.store.get(w.doc['id']),before); w.close()
