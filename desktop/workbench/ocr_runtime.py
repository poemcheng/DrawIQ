"""Pinned offline PP-OCRv6 small backend. No model downloads during recognition."""
import sys
from pathlib import Path

RUNTIME=Path(__file__).resolve().parents[1]/'vendor/ocr_runtime'
ENGINE='RapidOCR 3.10.0 / PP-OCRv6-small / ONNX CPU'
SELECTED='rapid_v6'
FORCE_OCR=False

def _overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    smallest=min(max(0,a[2]-a[0])*max(0,a[3]-a[1]),max(0,b[2]-b[0])*max(0,b[3]-b[1]))
    return area/smallest if smallest else 0

def _merge_regions(native, scanned):
    """Fuse measured text only; agreement can combine provenance, never values."""
    from .domain import canonical
    output=list(native)
    for item in scanned:
        agreeing=[r for r in native if canonical(r.text)==canonical(item.text) and _overlap(r.box,item.box)>=.8]
        if not agreeing:
            # Embedded PDF regions are individual words, OCR usually emits lines.
            # Fold a line only when all spatially contained words agree exactly.
            b=item.box
            words=[r for r in native if b[0]<=(r.box[0]+r.box[2])/2<=b[2]
                   and b[1]<=(r.box[1]+r.box[3])/2<=b[3] and _overlap(r.box,b)>=.8]
            words.sort(key=lambda r:(r.box[1],r.box[0]))
            if words and canonical(' '.join(r.text for r in words))==canonical(item.text): agreeing=words
        if agreeing:
            for r in agreeing:
                r.methods=list(dict.fromkeys(getattr(r,'methods',[r.source])+[item.source]))
            if len(agreeing)==1 and getattr(item,'layout_type',None):
                agreeing[0].layout_type=item.layout_type
        else:
            output.append(item)
    return output

def recognize_page(path, embedded_text='', embedded_regions=None, *, pdf_image_boxes=None, deadline=None, **kwargs):
    """Read bitmap-backed and legacy unknown PDF coverage alongside native text.

    A measured empty bitmap list permits the native-only fast path. Unknown
    coverage must not let a typed footer suppress OCR of the drawing body.
    """
    from drawing_intelligence import ocr
    from PIL import Image
    from .ocr_engines import MINERU_BACKENDS, recognize
    native=list(embedded_regions or [])
    if (native and ocr._text_is_usable(embedded_text) and pdf_image_boxes==[]
            and not FORCE_OCR and SELECTED not in MINERU_BACKENDS):
        result=ocr.OCRResult(embedded_text,native,'PDF文字層')
        result.coverage_status='complete'
        return result
    try:
        with Image.open(path) as image:
            options={'deadline':deadline} if SELECTED in MINERU_BACKENDS else {}
            result=recognize(image.convert('RGB'),SELECTED,**options)
    except Exception as exc:
        if not native: raise
        result=ocr.OCRResult(embedded_text,native,'PDF文字層',
            [f'圖面 OCR 未完成，僅保留 PDF 文字層；圖片內資訊仍須重讀：{exc}'])
        result.coverage_status='partial_failure'
        return result
    if result.engine=='RapidOCR':
        result.engine=ENGINE
        for e in result.regions: e.source=ENGINE
    if native:
        scanned=list(result.regions)
        result.regions=_merge_regions(native,scanned)
        result.text=embedded_text+'\n'+'\n'.join(e.text for e in result.regions if e not in native)
        result.engine='PDF文字層 + '+result.engine
        if not scanned:
            result.warnings.append('圖面 OCR 未取得文字；PDF 文字層之外的圖片內容仍須覆核')
    result.coverage_status='complete'
    return result

def configure(backend='rapid_v6',*,force_ocr=False):
    global SELECTED, ENGINE, FORCE_OCR
    from .ocr_engines import BACKENDS
    if backend not in BACKENDS: raise ValueError('未知 OCR 模型：'+str(backend))
    SELECTED=backend
    FORCE_OCR=force_ocr
    ENGINE=BACKENDS['rapid_v6']+' / ONNX CPU'
    from drawing_intelligence import ocr
    required=['PP-OCRv6_det_small.onnx','PP-OCRv6_rec_small.onnx','ch_ppocr_mobile_v2.0_cls_mobile.onnx']
    model_dir=RUNTIME/'rapidocr/models'
    if not all((model_dir/n).is_file() for n in required):
        raise RuntimeError('缺少本機 PP-OCRv6 模型，請修復安裝；識圖期間不下載模型')
    # Use the pinned installed 3.10 runtime, not the old vendored 3.9 package.
    from rapidocr import RapidOCR
    ocr._rapidocr_instance=RapidOCR(params={
        'Global.log_level':'error',
        'EngineConfig.onnxruntime.intra_op_num_threads':4,
        'EngineConfig.onnxruntime.inter_op_num_threads':1,
        'Det.model_path':str(model_dir/required[0]),
        'Rec.model_path':str(model_dir/required[1]),
        'Cls.model_path':str(model_dir/required[2]),
        'Rec.rec_batch_num':12,
    })
    ocr._drawiq_engine=ENGINE
    # The existing isolated page/crop workers own the deadline. No secondary
    # Tesseract process or model download is started by this backend.
    ocr.recognize_page=recognize_page
