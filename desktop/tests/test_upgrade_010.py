import unittest
from copy import deepcopy
from workbench.typed_annotations import parse, candidates
from workbench.domain import evidence, validate_document
from workbench.ocr_engines import unturn_box
from workbench.linked_evidence import export_graph, validate_graph

class EngineeringUpgradeTests(unittest.TestCase):
    def test_standard_is_not_a_drawing_number(self):
        from workbench.extraction_quality import field_issue
        self.assertTrue(field_issue('drawing_number','ISO-4042','ISO-4042'))
        self.assertTrue(field_issue('document_number','ISO4014','ISO4014'))
        self.assertFalse(field_issue('drawing_number','0800-2070-01','Drawing number 0800-2070-01'))
    def test_orientation_needs_engineering_anchors(self):
        from types import SimpleNamespace
        from workbench.ocr_engines import orientation_score
        self.assertEqual(orientation_score([SimpleNamespace(text='123 456 xx 15.5.28')]),0)
        self.assertGreaterEqual(orientation_score([SimpleNamespace(text='DRAWING NUMBER M6x40 QTY100M')]),6)
    def test_orientation_does_not_choose_garbage_or_weak_evidence(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from PIL import Image
        from workbench.ingest import LEGACY
        from workbench.ocr_engines import orientation
        sys.path.insert(0,str(LEGACY))
        def reading(text): return SimpleNamespace(regions=[SimpleNamespace(text=text)])
        with patch('drawing_intelligence.ocr._run_rapidocr',side_effect=[reading(t) for t in ('123','DRAWING QTY M6x40','456','78')]):
            self.assertEqual(orientation(Image.new('RGB',(100,200)))[0],90)
        with patch('drawing_intelligence.ocr._run_rapidocr',side_effect=[reading(t) for t in ('123','QTY','456','78')]):
            self.assertEqual(orientation(Image.new('RGB',(100,200)))[0],0)
    def test_symmetric_tolerance(self):
        r=parse('4±.24')[0]
        self.assertEqual((r['value'],r['upper_tolerance'],r['lower_tolerance']),('4','0.24','-0.24'))
    def test_plus_slash_minus(self):
        r=parse('35 +/-0.5')[0]
        self.assertEqual((r['value'],r['upper_tolerance'],r['lower_tolerance']),('35','0.5','-0.5'))
    def test_asymmetric_zero_upper(self):
        r=parse('10+0/-0.36')[0]
        self.assertEqual((r['value'],r['upper_tolerance'],r['lower_tolerance']),('10','0','-0.36'))
    def test_ocr_error_is_never_silently_repaired(self):
        self.assertEqual(parse('110+0/-0.36')[0]['value'],'110')
    def test_thread_three_numbers(self):
        r=parse('M8×1.25×50')[0]
        self.assertEqual((r['thread_diameter'],r['thread_pitch'],r['thread_length']),('8','1.25','50'))
    def test_thread_class(self):
        r=parse('M6x1.0-6g')[0]
        self.assertEqual((r['thread_pitch'],r['thread_class']),('1.0','6g'))
    def test_ambiguous_two_number_thread(self):
        r=parse('M3X6')[0]
        self.assertEqual(r['thread_pitch'],''); self.assertEqual(r['thread_length'],'')
        self.assertTrue(r['extraction_notes'])
    def test_m_is_not_multiplied(self):
        r=parse('QTY:200M')[0]
        self.assertEqual((r['value'],r['state']),('200M','pending_decision'))
    def test_embedded_qty(self):
        self.assertEqual(parse('M3X6QTY:100M')[-1]['value'],'100M')
    def test_quantity_commas(self):
        self.assertEqual(parse('QTY 981,920 PCS')[0]['value'],'981,920PCS')
    def test_comparison_direction(self):
        self.assertEqual([r['operator'] for r in parse('膜厚≥5μm，≤12μm')],['ge','le'])
    def test_drawing_label_required(self):
        self.assertEqual(parse('ISO4014 15.5.28 KH1234'),[])
        self.assertEqual(parse('DWG NO:0800-2070-01')[0]['value'],'0800-2070-01')
    def test_limits_not_invented(self):
        self.assertEqual(parse('5.76 / 6.24'),[])
    def test_indra_clockwise_coordinates(self):
        # Original rectangle (10,20,30,40) on a 100x200 page.
        for angle,turned in [(0,[10,20,30,40]),(90,[160,10,180,30]),
                             (180,[70,160,90,180]),(270,[20,70,40,90])]:
            self.assertEqual(unturn_box(turned,angle,100,200),[10,20,30,40])
    def test_unknown_angle_fails(self):
        with self.assertRaises(ValueError): unturn_box([0,0,10,10],45,100,200)
    def document(self):
        e=evidence(1,[.1,.1,.3,.2],'10+0/-0.36','synthetic')
        return dict(id='synthetic',version=1,source_sha256='0'*64,pages=[{'number':1}],
                    regions=[e],requirements=candidates([e]))
    def test_typed_candidate_has_evidence_and_needs_review(self):
        d=self.document();validate_document(d)
        self.assertFalse(d['requirements'][0]['support_verified'])
        self.assertEqual(d['requirements'][0]['status'],'needs_review')
    def test_pending_facts_quarantined_from_trusted_ids(self):
        d=self.document(); self.assertEqual(export_graph(d)['trustedFactIds'],[])
        d['requirements'][0].update(status='verified',support_verified=True)
        self.assertEqual(len(export_graph(d)['trustedFactIds']),1)
        d['requirements'][0]['state']='conflict'
        self.assertEqual(export_graph(d)['trustedFactIds'],[])
    def test_jsonld_shacl_roundtrip(self):
        d=self.document(); graph=export_graph(d)
        self.assertTrue(validate_graph(graph)['conforms'])
        broken=deepcopy(graph)
        next(x for x in broken['@graph'] if x['@type']=='Observation').pop('sourceSha256')
        self.assertFalse(validate_graph(broken)['conforms'])
    def test_repeated_catalogue_does_not_duplicate_or_replace_human(self):
        from workbench.engineering import catalogue_candidates
        d=self.document();d['requirements']=[]
        d['requirements'].extend(catalogue_candidates(d,semantic=True))
        row=next(r for r in d['requirements'] if r['origin']=='typed_annotation')
        row['value']='human value';row['status']='verified';row['support_verified']=True
        self.assertEqual(catalogue_candidates(d,semantic=True),[])
        self.assertEqual(row['value'],'human value')

if __name__=='__main__': unittest.main()
