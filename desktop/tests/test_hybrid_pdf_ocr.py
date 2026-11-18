"""Mixed native/scanned coverage, measured boxes and independent OCR failures."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image
from workbench.ingest import prepare_document,pdf_raster_boxes,LEGACY
from workbench.ocr_runtime import recognize_page,ENGINE
if str(LEGACY) not in sys.path: sys.path.insert(0,str(LEGACY))
from drawing_intelligence import ocr
from drawing_intelligence.models import TextRegion


class HybridPDFTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.image=self.root/'page.png'; Image.new('RGB',(500,700),'white').save(self.image)
        self.footer='Native footer with more than sixty readable characters for metadata and publishing information.'
        self.native=[TextRegion(self.footer,(30,600,460,630),1,'PDF文字層')]

    def tearDown(self): self.temp.cleanup()

    def scan(self,text='Material: S45C'):
        return ocr.OCRResult(text,[TextRegion(text,(30,100,300,130),.95,'RapidOCR')],'RapidOCR')

    def test_native_footer_cannot_hide_raster_body(self):
        with patch.object(ocr,'_run_rapidocr',return_value=self.scan()) as call:
            result=recognize_page(self.image,self.footer,self.native,pdf_image_boxes=[[.04,.05,.95,.8]])
        self.assertEqual(call.call_count,1)
        self.assertIn('Material: S45C',result.text); self.assertIn(self.footer,result.text)
        self.assertEqual(result.regions[0].box,(30,600,460,630))
        self.assertEqual(result.regions[1].box,(30,100,300,130))
        self.assertEqual(result.regions[1].source,ENGINE)
        self.assertEqual(result.coverage_status,'complete')

    def test_unknown_old_metadata_runs_ocr(self):
        with patch.object(ocr,'_run_rapidocr',return_value=self.scan()) as call:
            result=recognize_page(self.image,self.footer,self.native)
        self.assertEqual(call.call_count,1); self.assertEqual(len(result.regions),2)

    def test_measured_pure_vector_page_uses_native_only(self):
        with patch.object(ocr,'_run_rapidocr',side_effect=AssertionError('unnecessary OCR')):
            result=recognize_page(self.image,self.footer,self.native,pdf_image_boxes=[])
        self.assertEqual(result.engine,'PDF文字層'); self.assertEqual(result.regions,self.native)

    def test_short_native_annotation_survives_ocr(self):
        words=[TextRegion('M8',(20,200,40,220),1,'PDF文字層')]
        with patch.object(ocr,'_run_rapidocr',return_value=self.scan()):
            result=recognize_page(self.image,'M8',words,pdf_image_boxes=[])
        self.assertIn('M8',result.text); self.assertIn('Material: S45C',result.text)

    def test_same_text_and_overlapping_box_preserve_native_provenance(self):
        native=[TextRegion('S45C',(30,100,100,130),1,'PDF文字層')]
        scanned=ocr.OCRResult('S45C',[TextRegion('S45C',(31,101,101,131),.9,'RapidOCR')],'RapidOCR')
        with patch.object(ocr,'_run_rapidocr',return_value=scanned):
            result=recognize_page(self.image,'S45C',native,pdf_image_boxes=[[0,0,1,1]])
        self.assertEqual(len(result.regions),1); self.assertEqual(result.regions[0].box,native[0].box)
        self.assertEqual(result.regions[0].methods,['PDF文字層',ENGINE])

    def test_distant_equal_text_is_a_separate_occurrence(self):
        native=[TextRegion('S45C',(30,500,100,530),1,'PDF文字層')]
        scanned=ocr.OCRResult('S45C',[TextRegion('S45C',(30,100,100,130),.9,'RapidOCR')],'RapidOCR')
        with patch.object(ocr,'_run_rapidocr',return_value=scanned):
            result=recognize_page(self.image,'S45C',native,pdf_image_boxes=[[0,0,1,1]])
        self.assertEqual(len(result.regions),2)

    def test_conflicting_same_box_readings_remain_separate(self):
        native=[TextRegion('S45C',(30,100,100,130),1,'PDF文字層')]
        scanned=ocr.OCRResult('S450',[TextRegion('S450',(30,100,100,130),.9,'RapidOCR')],'RapidOCR')
        with patch.object(ocr,'_run_rapidocr',return_value=scanned):
            result=recognize_page(self.image,'S45C',native,pdf_image_boxes=[[0,0,1,1]])
        self.assertEqual({r.text for r in result.regions},{'S45C','S450'})

    def test_agreeing_ocr_line_folds_into_native_words(self):
        native=[TextRegion('Material:',(30,100,120,130),1,'PDF文字層'),
                TextRegion('S45C',(130,100,190,130),1,'PDF文字層')]
        scanned=ocr.OCRResult('Material: S45C',[TextRegion('Material: S45C',(29,99,191,131),.9,'RapidOCR')],'RapidOCR')
        with patch.object(ocr,'_run_rapidocr',return_value=scanned):
            result=recognize_page(self.image,'Material: S45C',native,pdf_image_boxes=[[0,0,1,1]])
        self.assertEqual(len(result.regions),2)
        self.assertTrue(all(r.methods==['PDF文字層',ENGINE] for r in result.regions))

    def test_scan_failure_preserves_native_and_reports_partial_coverage(self):
        with patch.object(ocr,'_run_rapidocr',side_effect=RuntimeError('OCR failed')):
            result=recognize_page(self.image,self.footer,self.native,pdf_image_boxes=[[0,0,1,1]])
        self.assertEqual(result.text,self.footer); self.assertEqual(result.coverage_status,'partial_failure')
        self.assertTrue(result.warnings)

    def test_jpg_ocr_failure_stays_a_failure(self):
        with patch.object(ocr,'_run_rapidocr',side_effect=RuntimeError('OCR failed')):
            with self.assertRaises(RuntimeError): recognize_page(self.image)

    def make_pdf(self,raster=False,rotation=0):
        source=self.root/('mixed.pdf' if raster else 'vector.pdf')
        pdf=fitz.open(); page=pdf.new_page(width=200,height=100)
        page.insert_text((10,80),self.footer,fontsize=3)
        if raster:
            stream=io.BytesIO(); Image.new('RGB',(80,40),'white').save(stream,format='PNG')
            page.insert_image(fitz.Rect(20,10,100,50),stream=stream.getvalue())
        page.set_rotation(rotation); pdf.save(source); pdf.close()
        return source

    def test_import_records_actual_raster_placements(self):
        source=self.make_pdf(raster=True,rotation=90)
        doc=prepare_document(source,self.root/'assets')
        self.assertEqual(len(doc['pages'][0]['pdf_image_boxes']),1)
        for actual,expected in zip(doc['pages'][0]['pdf_image_boxes'][0],[.5,.1,.9,.5]):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(pdf_raster_boxes(source,1),doc['pages'][0]['pdf_image_boxes'])

    def test_vector_metadata_and_old_file_missing_are_distinct(self):
        source=self.make_pdf()
        self.assertEqual(prepare_document(source,self.root/'assets')['pages'][0]['pdf_image_boxes'],[])
        self.assertEqual(pdf_raster_boxes(source,1),[])
        self.assertIsNone(pdf_raster_boxes(self.root/'missing.pdf',1))


if __name__=='__main__': unittest.main()
