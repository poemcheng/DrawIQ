"""OCR JSON is a transcription contract, not a license to invent drawing boxes."""
import math
import unittest
from workbench.ocr_contract import from_ocr_json


class OcrContractTests(unittest.TestCase):
    def sample(self):
        return {'document_id':'example','document_type':'quote_label','pages':[
            {'page':1,'width':1000,'height':1400,'lines':[
                {'text':'規格： M8 × 1.25 - 6h','bbox':[20,200,800,235],'confidence':.91}]}]}

    def test_pixel_box_maps_to_original_page_and_text_is_unchanged(self):
        actual=from_ocr_json(self.sample())
        row=actual['regions'][0]
        self.assertEqual(row['box'],[.02,200/1400,.8,235/1400])
        self.assertEqual(row['text'],'規格： M8 × 1.25 - 6h')
        self.assertEqual(row['ocr_confidence'],.91)
        self.assertEqual(actual['source_contract']['execution'],'provided_transcription')

    def test_missing_box_is_retained_unlocated_without_fabricating_page_geometry(self):
        original=self.sample(); original['pages'][0]['lines'][0].pop('bbox')
        actual=from_ocr_json(original)
        self.assertEqual(actual['regions'],[])
        self.assertEqual(actual['unlocated_ocr'][0]['text'],original['pages'][0]['lines'][0]['text'])
        self.assertIsNone(actual['unlocated_ocr'][0]['box'])
        self.assertIn('missing_bbox',actual['unlocated_ocr'][0]['source_issues'])

    def test_unsupported_dimensions_do_not_turn_pixels_into_normalized_coordinates(self):
        original=self.sample(); original['pages'][0].pop('width')
        actual=from_ocr_json(original)
        self.assertEqual(actual['regions'],[])
        self.assertIn('page_dimensions_missing',actual['unlocated_ocr'][0]['source_issues'])

    def test_invalid_confidence_and_inverted_or_outside_boxes_are_rejected(self):
        for invalid in (-.1,1.1,float('nan'),float('inf'),True):
            original=self.sample(); original['pages'][0]['lines'][0]['confidence']=invalid
            with self.subTest(confidence=invalid),self.assertRaises(ValueError): from_ocr_json(original)
        for box in ([0,0,0,1],[1,1,0,2],[-1,0,4,4],[1,1,1001,1400]):
            original=self.sample(); original['pages'][0]['lines'][0]['bbox']=box
            with self.subTest(box=box),self.assertRaises(ValueError): from_ocr_json(original)

    def test_duplicate_page_and_nontext_ocr_are_rejected(self):
        original=self.sample(); original['pages'].append(original['pages'][0])
        with self.assertRaises(ValueError): from_ocr_json(original)
        original=self.sample(); original['pages'][0]['lines'][0]['text']=123
        with self.assertRaises(ValueError): from_ocr_json(original)


if __name__=='__main__': unittest.main()
