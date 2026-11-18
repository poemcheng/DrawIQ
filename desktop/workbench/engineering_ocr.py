"""Bounded OCR re-read of measured, dense engineering text blocks.

This pass adds measured alternatives; it does not correct a standard number,
choose an adhesive, or replace the original OCR merely because a score rises.
"""
import re
import time

from PIL import Image, ImageOps

from .domain import box_valid, evidence


METHOD = 'engineering-context-reread/0.8.0'


def _priority(text):
    if re.search(r'\b(?:standard|din|iso|is0|astm|asme|jis)\b', text, re.I):
        return 0
    if re.search(r'(?<![A-Za-z0-9])M\s*\d+(?:[.,]\d+)?\s*[x×]\s*\d', text, re.I):
        return 0
    if re.search(r'\b(?:full\s*thread|threadlock|theeadlock|threadlocking)\b|螺[紋纹]|膠[種种別别]', text, re.I):
        return 1
    if re.search(r'\b(?:ma[tl]erial|nylon|precote|tuflok|loctite)\b|材[質质]|品質要求', text, re.I):
        return 1
    return None


def _overlap(a, b):
    area = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return area/smaller if smaller else 0


def _targets(regions, page, max_crops):
    original = [region for region in regions if region.get('page', page) == page
                and region.get('kind', 'evidence') == 'evidence' and box_valid(region.get('box'))
                and region.get('text', '').strip() and region.get('ocr_pass') != 'engineering-context-reread']
    candidates = [region for region in original if _priority(region['text']) is not None]
    candidates.sort(key=lambda region: (_priority(region['text']), region['box'][1], region['box'][0]))
    output = []
    for anchor in candidates:
        b = anchor['box']; height = b[3]-b[1]
        # Group nearby engineering lines in the same column. Reading a block
        # keeps complete labels and stops a tight crop cutting neighboring
        # glyphs into plausible-looking words.
        neighbors = [region for region in original if _priority(region['text']) is not None
                     and abs((region['box'][1]+region['box'][3]-b[1]-b[3])/2) <= min(.065, height*3.7)
                     and abs(region['box'][0]-b[0]) <= max(.04, (b[2]-b[0])*.25)]
        members = [anchor, *[region for region in neighbors if region['id'] != anchor['id']]]
        box = [min(region['box'][0] for region in members), min(region['box'][1] for region in members),
               max(region['box'][2] for region in members), max(region['box'][3] for region in members)]
        if any(_overlap(box, target['box']) >= .75 for target in output):
            continue
        output.append({'box': box, 'members': members, 'priority': _priority(anchor['text'])})
        if len(output) >= max_crops:
            break
    return output


def reread_engineering_regions(image, page, initial_regions, *, cancelled=lambda: False,
                              deadline=None, max_crops=8):
    """Return measured OCR alternatives and warnings within the caller budget."""
    if cancelled():
        return [], ['工程重讀已取消；保留原始 OCR']
    if deadline is not None and time.monotonic() >= deadline-3:
        return [], ['工程重讀未取得足夠時間預算；保留原始 OCR']
    max_crops = max(0, min(12, int(max_crops)))
    if not max_crops:
        return [], []
    targets = _targets(initial_regions, page, max_crops)
    if not targets:
        return [], []
    from drawing_intelligence import ocr
    output = []; warnings = []
    for target in targets:
        if cancelled():
            warnings.append('工程重讀已取消；保留已讀文字與原始 OCR'); break
        if deadline is not None and time.monotonic() >= deadline-3:
            warnings.append('工程重讀達時間預算；保留已讀文字與原始 OCR'); break
        b = target['box']; members = target['members']
        # Padding is based on actual row height, rather than a whole-page
        # fraction that can pull half of the row above into a narrow crop.
        row_pixels = min((region['box'][3]-region['box'][1])*image.height for region in members)
        padding = max(2, round(row_pixels*.16))
        bounds = (max(0, round(b[0]*image.width)-padding), max(0, round(b[1]*image.height)-padding),
                  min(image.width, round(b[2]*image.width)+padding), min(image.height, round(b[3]*image.height)+padding))
        crop = image.crop(bounds).convert('RGB')
        if crop.width < 8 or crop.height < 8:
            continue
        original_size = crop.size
        factor = min(3 if row_pixels < 22 else 2, 1800/crop.width, 1000/crop.height)
        resized = crop.resize((max(1, round(crop.width*factor)), max(1, round(crop.height*factor))), Image.Resampling.LANCZOS)
        try:
            result = ocr._run_rapidocr(ImageOps.expand(resized, border=16, fill='white'), 1)
        except Exception as exc:
            warnings.append(f'工程局部重讀失敗，保留原始 OCR：{exc}'); continue
        scale_x = original_size[0]/resized.width; scale_y = original_size[1]/resized.height
        for item in result.regions:
            mapped = [(bounds[0]+(item.box[0]-16)*scale_x)/image.width,
                      (bounds[1]+(item.box[1]-16)*scale_y)/image.height,
                      (bounds[0]+(item.box[2]-16)*scale_x)/image.width,
                      (bounds[1]+(item.box[3]-16)*scale_y)/image.height]
            mapped = [max(0, min(1, value)) for value in mapped]
            if not box_valid(mapped):
                continue
            cy = (mapped[1]+mapped[3])/2
            parent_ids = [region['id'] for region in members
                          if region['box'][1] <= cy <= region['box'][3]
                          and (min(mapped[3], region['box'][3])-max(mapped[1], region['box'][1]))
                          >= (mapped[3]-mapped[1])*.55
                          and min(mapped[2], region['box'][2]) > max(mapped[0], region['box'][0])]
            # Do not retain OCR of a clipped neighboring row as a correction.
            if not parent_ids or not item.text.strip():
                continue
            backend = getattr(ocr, '_drawiq_engine', result.engine)
            region = evidence(page, mapped, item.text, backend+':engineering-context-reread')
            region['ocr_confidence'] = item.confidence
            region['ocr_pass'] = 'engineering-context-reread'
            region['ocr_parent_ids'] = parent_ids
            region['ocr_method'] = METHOD
            region['ocr_scale'] = factor
            region['ocr_crop'] = [bounds[0]/image.width, bounds[1]/image.height, bounds[2]/image.width, bounds[3]/image.height]
            output.append(region)
        if result.text.strip() and not result.regions:
            warnings.append('工程重讀取得文字但沒有實測文字框，未建立自動來源')
    return output, list(dict.fromkeys(warnings))
