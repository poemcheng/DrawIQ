"""Scoped reanalysis and MinerU geometry retain their separate provenance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from workbench import bounded, extraction
from workbench.domain import clone
from workbench.ingest import prepare_document
from workbench.store import Store


class ParseReviewTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        root = Path(work.name)
        source = root / 'synthetic.tiff'
        Image.new('RGB', (400, 500), 'white').save(
            source, save_all=True, append_images=[Image.new('RGB', (400, 500), 'gray')],
        )
        self.store = Store(root / 'data')
        doc = prepare_document(source, self.store.root)
        doc['pages'][1].update(embedded_text='Reusable text layer',
                               embedded_words=[{'text':'Reusable','box':[.2,.25,.4,.3]}],
                               pdf_image_boxes=[])
        self.doc=self.store.add(doc)

    def test_force_ocr_reaches_both_methods_for_only_the_selected_page(self):
        result = clone(self.doc)
        result.update(regions=[], requirements=[], warnings=[], tool_runs=[], layout_layers=[])
        with patch('workbench.bounded._analyze_single', side_effect=[clone(result), clone(result)]) as read:
            bounded.analyze(self.doc, self.store.root, page_numbers=[2],
                            ocr_backend='compare_mineru', force_ocr=True)
        self.assertEqual([call.kwargs['ocr_backend'] for call in read.call_args_list], ['rapid_auto','mineru_basic'])
        for call in read.call_args_list:
            self.assertEqual(call.kwargs['page_numbers'], [2])
            self.assertTrue(call.kwargs['force_ocr'])
            self.assertTrue(call.kwargs['ignore_pdf_text'])

    def test_forced_parser_excludes_native_layer_and_saves_only_measured_shapes(self):
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult

        engine='MinerU 4.2.3 · Basic'
        word=TextRegion('OCR legible text',(40,50,140,100),0.0,engine)
        word.layout_type='text'
        ocr=OCRResult('OCR legible text',[word],engine,[])
        ocr.layout_regions=[{'box':(100,150,240,320),'layout_type':'image_body'},
                            {'box':(220,240,200,480),'layout_type':'table_body'}]

        def recognize(path, embedded_text='', embedded_regions=None, *, pdf_image_boxes=None, deadline=None):
            self.assertEqual(embedded_text,'')
            self.assertEqual(embedded_regions,[])
            return ocr

        with patch('drawing_intelligence.ocr.recognize_page',side_effect=recognize):
            result=extraction.analyze(self.doc,self.store.root,page_numbers=[2],ignore_pdf_text=True)
        self.assertTrue(result['tool_runs'][0]['ignore_pdf_text'])
        self.assertEqual(result['regions'][0]['layout_type'],'text')
        updated=extraction.apply_analysis(self.store,self.doc['id'],self.doc['version'],result)
        [shape]=updated['layout_regions']
        self.assertEqual((shape['page'], shape['source'], shape['layout_type']),(2,engine,'image_body'))
        self.assertEqual(shape['box'],[.25,.3,.6,.64])
        self.assertNotIn(shape['id'],[region['id'] for region in updated['regions']])
        self.assertTrue(all(shape['id'] not in requirement['evidence_ids'] for requirement in updated['requirements']))
        self.assertEqual(updated['pages'][0],self.doc['pages'][0])
        original_region_ids={region['id'] for region in updated['regions']}
        layer={'page':2,'source':engine,'regions':[]}
        empty={**result,'regions':[],'requirements':[],'tool_runs':[],'layout_layers':[layer]}
        reprocessed=extraction.apply_analysis(self.store,updated['id'],updated['version'],empty)
        self.assertEqual(reprocessed['layout_regions'],[])
        self.assertEqual({region['id'] for region in reprocessed['regions']},original_region_ids)


if __name__ == '__main__':
    unittest.main()
