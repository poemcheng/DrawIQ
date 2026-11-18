import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from pathlib import Path
from PyQt6.QtCore import QPoint,Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from workbench.ui import MainWindow
from workbench.canvas import DrawingCanvas
from workbench.domain import requirement
from workbench.geometry import measure_step,compare_steps
from workbench.backup import backup_workspace
from tests.test_workbench import Fixture
import zipfile,json,hashlib


class GuiTests(Fixture):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp(); self.window=MainWindow(self.store.root); self.window.load_documents()
        self.window.show(); self.app.processEvents()

    def tearDown(self):
        self.window.business_page.reset(); self.window.reset_form(); self.window.close(); self.window.deleteLater(); self.app.processEvents(); super().tearDown()

    def test_simple_flow_is_front_door_and_advanced_summary_opens_matching_evidence(self):
        w=self.window; self.assertIs(w.workspace_tabs.currentWidget(),w.simple_page)
        w.advanced_mode.setChecked(True)
        w.workspace_tabs.setCurrentWidget(w.summary_page)
        fact=w.summary_page.data['facts'][0]; w.summary_page.show_fact(fact)
        self.assertEqual(w.summary_page.selected_fact['evidence_ids'],[self.ev['id']])
        w.summary_page.open_review(); self.assertIs(w.workspace_tabs.currentWidget(),w.review_workspace)
        self.assertEqual(w.current_rid,self.req['id'])

    def test_business_dirty_guard_and_quote_form_save_reload(self):
        w=self.window; page=w.business_page; page.rfq_fields['quantity'].setText('100')
        self.assertTrue(page.is_dirty())
        with self.assertRaises(ValueError): w.require_saved_form()
        page.refresh(w.doc); self.assertEqual(page.rfq_fields['quantity'].text(),'100')
        page.currency.setCurrentText('TWD')
        for field in page.costs.values(): field.setText('1')
        page.setup.setText('100'); page.margin.setText('20'); page.calculate()
        self.assertIn('750.00',page.calculation.toPlainText())
        page.save(); self.assertFalse(page.is_dirty()); w.require_saved_form()
        self.assertEqual(page.service.get(w.doc['id'])['rfq']['quantity'],'100')

    def test_refined_summary_adoption_stays_unreviewed(self):
        from workbench.domain import evidence
        w=self.window; e=evidence(1,[.1,.3,.8,.4],'最高工作溫度可達135°C')
        w.doc=self.store.save_region(w.doc['id'],w.doc['version'],e,None,'synthetic evidence'); w.refresh()
        fact=next(f for f in w.summary_page.data['facts'] if f['key']=='temperature.capability')
        w.summary_page.show_fact(fact); w.summary_page.adopt_fact()
        r=w.current_requirement(); self.assertEqual(r['value'],'135'); self.assertEqual(r['status'],'needs_review')
        self.assertEqual(r['evidence_ids'],[e['id']])

    def test_edit_save_verify_flow(self):
        w=self.window; w.value.setPlainText('10'); w.reason.setText('Synthetic UI test')
        with self.assertRaises(ValueError): w.verify_current()
        w.save_current(); self.assertEqual(w.current_requirement()['value'],'10')
        w.verify_current(); self.assertEqual(w.current_requirement()['status'],'verified')

    def test_dirty_link_cannot_be_approved(self):
        w=self.window; w.links.item(0).setCheckState(Qt.CheckState.Unchecked); w.reason.setText('Synthetic UI test')
        self.assertTrue(w.form_dirty())
        with self.assertRaises(ValueError): w.verify_current()
        with self.assertRaises(ValueError): w.approve_current()
        w.reset_form(); self.assertFalse(w.form_dirty())

    def test_requirement_switch_keeps_unsaved_text(self):
        w=self.window; r=requirement('other','3',evidence_ids=[self.ev['id']])
        w.doc=self.store.add_requirement(w.doc['id'],w.doc['version'],r,'synthetic'); w.refresh()
        rid=w.current_rid; w.value.setPlainText('11'); w.requirements.selectRow(1)
        self.assertEqual(w.current_rid,rid); self.assertEqual(w.value.toPlainText(),'11')

    def test_canvas_draw_after_rotation(self):
        canvas=DrawingCanvas(); canvas.resize(500,650); canvas.show()
        canvas.show_page(self.doc['pages'][0],[]); canvas.rotate_page(); self.app.processEvents()
        result=[]; canvas.region_created.connect(result.append); canvas.set_draw(True)
        start=canvas.mapFromScene(canvas.width_px*.2,canvas.height_px*.3)
        end=canvas.mapFromScene(canvas.width_px*.7,canvas.height_px*.6)
        QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(canvas.viewport(),end,20)
        QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end)
        self.app.processEvents(); self.assertEqual(len(result),1)
        for actual,wanted in zip(result[0],[.2,.3,.7,.6]): self.assertAlmostEqual(actual,wanted,delta=.015)
        canvas.close(); canvas.deleteLater()

    def test_clicking_box_does_not_create_false_edit(self):
        canvas=self.window.canvas; changes=[]; canvas.region_changed.connect(lambda *x:changes.append(x))
        canvas.fit(); self.app.processEvents()
        pos=canvas.mapFromScene(canvas.width_px*.4,canvas.height_px*.15)
        QTest.mouseClick(canvas.viewport(),Qt.MouseButton.LeftButton,pos=pos); self.app.processEvents()
        self.assertEqual(changes,[])


class GeometryBackupTests(Fixture):
    def test_complete_backup_contains_original_and_database(self):
        p=backup_workspace(self.store,self.root/'backup.zip')
        with zipfile.ZipFile(p) as z:
            m=json.loads(z.read('backup_manifest.json'))
            self.assertIn('knowledge.sqlite3',m['files'])
            self.assertTrue(any(n.endswith('source.pdf') for n in m['files']))
            self.assertTrue(all(hashlib.sha256(z.read(n)).hexdigest()==h for n,h in m['files'].items()))

    def test_fixed_step_worker_and_comparison(self):
        try:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
            from OCP.IFSelect import IFSelect_RetDone
        except ImportError: self.skipTest('Optional cadquery-ocp is not installed')
        paths=[]
        for width in (10,12):
            p=self.root/f'synthetic_box_{width}.step'; writer=STEPControl_Writer()
            writer.Transfer(BRepPrimAPI_MakeBox(width,20,30).Shape(),STEPControl_AsIs)
            self.assertEqual(writer.Write(str(p)),IFSelect_RetDone); paths.append(p)
        result=measure_step(paths[0]); self.assertTrue(result['valid']); self.assertEqual(result['solids'],1)
        self.assertAlmostEqual(result['volume'],6000,places=3)
        comparison=compare_steps(paths[1],paths[0]); self.assertAlmostEqual(comparison['volume_relative_error'],.2,places=4)


if __name__=='__main__': unittest.main(verbosity=2)
