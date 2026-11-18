"""Generative calls must use an unresolved measured crop, never a whole page."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from workbench.domain import evidence
from workbench.skill_vlm import plan_regions, crop_regions


class SkillVlmTests(unittest.TestCase):
    def test_resolved_fields_do_not_call_model(self):
        e=evidence(1,[.1,.1,.7,.15],'材質：S45C','OCR:test'); e['ocr_confidence']=.99
        report={'fields':[{'status':'extracted','sources':[{'region_id':e['id']}]}], 'specification_candidates':[]}
        self.assertEqual(plan_regions([e],report,[]),[])

    def test_low_confidence_gets_bounded_measured_crop(self):
        e=evidence(1,[.12,.2,.5,.24],'ISO 2ć7-28','OCR:test'); e['ocr_confidence']=.5
        plans=plan_regions([e],{'fields':[],'specification_candidates':[]},[])
        self.assertEqual(plans[0]['region_ids'],[e['id']])
        b=plans[0]['box']; self.assertLess((b[2]-b[0])*(b[3]-b[1]),.5)
        self.assertNotEqual(b,[0,0,1,1])

    def test_crop_coordinates_and_raw_text_are_preserved(self):
        e=evidence(1,[.2,.3,.4,.4],'M8 × 1.25 - 6h','OCR:test')
        local=crop_regions([e],[.1,.2,.6,.7])
        self.assertEqual(local[0]['text'],e['text'])
        self.assertAlmostEqual(local[0]['box'][0],.2)
        self.assertAlmostEqual(local[0]['box'][1],.2)
        self.assertEqual(e['box'],[.2,.3,.4,.4])

    def test_outside_ocr_cannot_anchor_crop_reading(self):
        inside=evidence(1,[.2,.3,.4,.4],'inside','OCR:test')
        outside=evidence(1,[.6,.8,.9,.9],'outside','OCR:test')
        self.assertEqual([e['id'] for e in crop_regions([inside,outside],[.1,.2,.5,.6])],[inside['id']])

    def test_model_output_is_not_a_measured_target(self):
        e=evidence(1,[.1,.1,.8,.2],'bad model','VLM:test'); e['ocr_confidence']=.1
        self.assertEqual(plan_regions([e],{'fields':[],'specification_candidates':[]},[]),[])

    def test_unknown_reading_is_retained_as_unresolved_crop(self):
        e=evidence(1,[.1,.1,.8,.2],'unclassified text','OCR:test')
        self.assertTrue(plan_regions([e],{'fields':[],'specification_candidates':[]},[]))

    def test_actual_analyze_maps_crop_back_and_preserves_measured_ocr(self):
        from workbench.extraction import analyze,map_box
        from drawing_intelligence.ocr import OCRResult
        from drawing_intelligence.models import TextRegion
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); image=root/'page.png'; Image.new('RGB',(1000,1000),'white').save(image)
            doc={'id':'local-roi','version':1,'pages':[{'number':1,'width':1000,'height':1000,'image_path':str(image)}],
                 'regions':[],'requirements':[]}
            text='unknown difficult symbol'
            ocr=OCRResult(text,[TextRegion(text,(200,300,500,340),.55,'OCR:test')],'OCR:test')
            item={'key':'note','label':'讀文','value':'candidate','unit':'','state':'present','operator':'text',
                  'subject':'document','scope':'','conditions':'','evidence':'new model reading','box':[.2,.3,.6,.6]}
            with patch('drawing_intelligence.ocr.recognize_page',return_value=ocr),\
                 patch('workbench.engineering_ocr.reread_engineering_regions',return_value=([],[])),\
                 patch('workbench.extraction_quality.table_requirements',return_value=([],set(),[])),\
                 patch('workbench.extraction.ask_vlm',return_value=({'requirements':[item],'warnings':[],'complete':True},{'done_reason':'stop'})) as model:
                output=analyze(doc,root,use_vlm=True)
            run=output['tool_runs'][0]; cropped=model.call_args.args[0]
            self.assertLess(cropped.width*cropped.height,1000*1000*.5)
            self.assertEqual(model.call_args.args[1],text)
            measured=[e for e in output['regions'] if e['source']=='OCR:test']
            generated=[e for e in output['regions'] if e['source'].startswith('VLM:')]
            self.assertEqual(measured[0]['text'],text)
            self.assertEqual(measured[0]['box'],[.2,.3,.5,.34])
            self.assertEqual(generated[0]['box'],map_box([.2,.3,.6,.6],0,run['vlm_input_crop']))
            self.assertTrue((Path(run['trace_dir'])/'vlm_crop.png').is_file())
