"""Right-side prefill is a complete review draft, never an automatic attestation."""
import unittest
from workbench.domain import clone,evidence,requirement
from workbench.engineering import catalogue_candidates
from workbench.quotation_layout import quotation_rows
from tests.test_workbench import Fixture


def document(lines):
    regions=[]; requirements=[]
    for i,(key,value,raw,confidence) in enumerate(lines):
        ev=evidence(1,[.1,.1+i*.07,.9,.14+i*.07],raw,'OCR:prefill')
        ev['ocr_confidence']=confidence; regions.append(ev)
        requirements.append(requirement(key,value,evidence_ids=[ev['id']]))
    return {'id':'prefill','name':'prefill','version':1,'status':'needs_review','pages':[{'number':1,'width':1000,'height':1000}],
            'regions':regions,'requirements':requirements,'tool_runs':[],'warnings':[]}


def rows(doc): return {row['key']:row for row in quotation_rows(doc,include_proposals=True)}


class PrefillTests(unittest.TestCase):
    def test_low_confidence_field_is_shown_before_human_review(self):
        doc=document([('material','S45C','材質：S45C',.45)])
        before=clone(doc); row=rows(doc)['material']
        self.assertEqual(row['display_value'],'S45C')
        self.assertTrue(row['proposed']); self.assertFalse(row['automatic'])
        self.assertIn('待確認',row['status']); self.assertTrue(row['proposal_issues'])
        self.assertIsNone(next(r for r in quotation_rows(doc) if r['key']=='material')['source'])
        self.assertEqual(doc,before)

    def test_conflict_chooses_strongest_role_supported_value(self):
        doc=document([('material','S45C','材質：S45C',.85),('material','SUS304','材質：SUS304',.99)])
        row=rows(doc)['material']
        self.assertEqual(row['source']['value'],'SUS304')
        self.assertTrue(row['proposed']); self.assertFalse(row['automatic'])
        self.assertGreaterEqual(len(row['proposal_alternatives']),2)

    def test_every_missing_cell_is_visible_without_invented_facts(self):
        doc=document([]); before=clone(doc); result=rows(doc)
        self.assertEqual(len(result),16)
        self.assertTrue(all(r['display_value'].strip() for r in result.values()))
        self.assertTrue(all(r['source'] is None for r in result.values()))
        self.assertEqual(doc,before)

    def test_single_unresolved_value_is_pending_without_claiming_conflict(self):
        for state in ('pending_decision','unreadable'):
            with self.subTest(state=state):
                doc=document([('material','S45C','材質：S45C',.95)])
                doc['requirements'][0]['state']=state
                row=rows(doc)['material']
                self.assertTrue(row['proposed'])
                self.assertEqual(row['status'],'建議／待確認')
                self.assertIn('unresolved_state',row['proposal_issues'])

    def test_manual_clear_is_not_filled_again(self):
        doc=document([('material','S45C','材質：S45C',.99)])
        doc['quotation_mapping']={'material':None}
        row=rows(doc)['material']; self.assertIsNone(row['source'])
        self.assertFalse(row['proposed']); self.assertTrue(row['display_value'])

    def test_wrong_field_roles_remain_missing(self):
        for key,value,raw in [('customer.name','456289 is design responsible.','Customer 456289 is design responsible.'),
                              ('quotation.quantity','72000','年度需求：72000 PCS/年'),
                              ('material','Nylon','Threadlock Material: Nylon Torq-Patch')]:
            with self.subTest(key=key):
                doc=document([(key,value,raw,.99)])
                self.assertIsNone(rows(doc)[key]['source'])

    def test_printed_coating_process_is_a_pending_proposal(self):
        doc=document([('observation.text','加工製程名稱：塗膠（CT91）','加工製程名稱：塗膠（CT91）',.97)])
        doc['requirements'].extend(catalogue_candidates(doc))
        row=rows(doc)['coating.method']
        self.assertEqual(row['source']['value'],'塗膠（CT91）')
        self.assertTrue(row['proposed']); self.assertFalse(row['automatic'])
        self.assertIn(row['source']['id'],{r['id'] for r in doc['requirements']})

    def test_damaged_standard_is_preserved_as_printed(self):
        raw='Standard: KL acc.to DIN 2ć7-28'
        doc=document([('observation.text',raw,raw,.93)])
        doc['requirements'].extend(catalogue_candidates(doc))
        row=rows(doc)['quality.requirements']
        self.assertIn('2ć7-28',row['display_value']); self.assertNotIn('267-28',row['display_value'])
        self.assertTrue(row['proposed']); self.assertFalse(row['automatic'])


class PrefillHumanReviewTests(Fixture):
    def test_explicit_review_preserves_low_confidence_proposal_and_audit(self):
        current=clone(self.doc); ev=current['regions'][0]
        ev.update(text='材質：S45C',source='OCR:prefill',ocr_confidence=.45)
        r=current['requirements'][0]; r.update(key='material',value='S45C',unit='',operator='eq',conditions='')
        d=self.store.mutate(current['id'],1,'test_fixture','low confidence fixture',lambda x:x.update(current))
        row=rows(d)['material']; self.assertTrue(row['proposed'])
        d=self.store.set_quotation_source(d['id'],d['version'],'material',r['id'],'human chooses displayed proposal')
        d=self.store.verify_requirement(d['id'],d['version'],r['id'],'human compared the original drawing')
        row=rows(d)['material']; self.assertEqual(row['status'],'已確認')
        self.assertFalse(row['proposed']); self.assertEqual(d['quotation_mapping']['material'],r['id'])
        self.assertTrue(self.store.verify_audit(d['id']))
