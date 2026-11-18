"""Offline model adapters owned by isolated workers, never the engineering core."""
from pathlib import Path
from .mineru_runtime import BACKENDS as MINERU_BACKENDS

ROOT=Path(__file__).resolve().parents[1]
BACKENDS={'rapid_auto':'RapidOCR 3.10 / PP-OCRv6 四向比對（建議）',
          'rapid_v6':'RapidOCR 3.10.0 / PP-OCRv6-small',
          'rapid_detail':'RapidOCR 3.10.0 / PP-OCRv6-small / 分區精讀',
          'indra_auto':'Indra 1.1.2 / v1.1.0 / 四向比對',
          'indra':'Indra OCR 1.1.2 / v1.1.0 multilingual',
          **MINERU_BACKENDS}

def unturn_box(box, rotation, width, height):
    """Indra 1.1.2 applies np.rot90(gray,-k): positive rotations are clockwise."""
    x0,y0,x1,y1=map(float,box)
    if rotation==90: b=[y0,height-x1,y1,height-x0]
    elif rotation==180: b=[width-x1,height-y1,width-x0,height-y0]
    elif rotation==270: b=[width-y1,x0,width-y0,x1]
    elif rotation==0: b=[x0,y0,x1,y1]
    else: raise ValueError('Indra 回傳不支援的旋轉角度')
    return [max(0,min(limit,value)) for value,limit in zip(b,(width,height,width,height))]

def orientation_score(regions):
    import re
    text=' '.join(r.text for r in regions)
    anchors=re.findall(r'QTY|QUANTITY|DRAWING|MATERIAL|PART\s*NO|DIMENSIONS|TOLERANCE|PRECISION|PCS|THREAD|ISO\s*\d|DIN\s*\d|M\d+(?:\.\d+)?[xX×]\d|圖號|數量|材質|公差|日期|塗膠',text,re.I)
    words=re.findall(r'\b(?:drawing|number|steel|screw|finish|scale|date|customer|length|weight|size|quality|note|bolt|plating)\b',text,re.I)
    return len(anchors)*3+len(words)

def orientation(image):
    from drawing_intelligence import ocr
    thumb=image.copy();thumb.thumbnail((1000,1000))
    probes=[]
    for angle in (0,90,180,270):
        probe=ocr._run_rapidocr(thumb.rotate(-angle,expand=True),1)
        probes.append((orientation_score(probe.regions),angle))
    best=max(probes,key=lambda x:x[0]); baseline=probes[0][0]
    return (best[1] if best[0]>=6 and best[0]>=baseline+3 else 0),probes

def recognize(image, backend, *, deadline=None):
    if backend in MINERU_BACKENDS:
        from .mineru_runtime import recognize as recognize_mineru
        return recognize_mineru(image, backend, deadline=deadline)
    from drawing_intelligence import ocr
    from drawing_intelligence.models import TextRegion
    if backend in ('rapid_auto','indra_auto'):
        angle,probes=orientation(image)
        result=recognize(image.rotate(-angle,expand=True),'indra' if backend=='indra_auto' else 'rapid_v6')
        for r in result.regions: r.box=tuple(unturn_box(r.box,angle,image.width,image.height))
        result.warnings.append(f'四向 OCR 比對 {probes}，本次採用 {angle}°；方向與內容仍須對照原圖確認')
        result.engine=BACKENDS[backend]
        for r in result.regions: r.source=BACKENDS[backend]
        return result
    if backend=='rapid_v6':
        working,scale=ocr._working_image(image)
        return ocr._run_rapidocr(working,scale)
    if backend=='rapid_detail':
        result=recognize(image,'rapid_v6')
        if max(image.size)<=1600: return result
        # Overlap preserves labels crossing tile boundaries. Conflicts survive.
        from .ocr_runtime import _overlap
        from .domain import canonical
        size=1600; stride=1440
        def starts(length): return sorted(set([*range(0,max(1,length-size+1),stride),max(0,length-size)]))
        for y in starts(image.height):
            for x in starts(image.width):
                tile=image.crop((x,y,min(x+size,image.width),min(y+size,image.height)))
                part=ocr._run_rapidocr(tile,1)
                for r in part.regions:
                    r.box=(r.box[0]+x,r.box[1]+y,r.box[2]+x,r.box[3]+y)
                    if not any(canonical(a.text)==canonical(r.text) and _overlap(a.box,r.box)>.7 for a in result.regions):
                        result.regions.append(r)
        result.text='\n'.join(r.text for r in result.regions)
        return result
    if backend!='indra': raise ValueError('未知 OCR 模型：'+str(backend))
    import numpy as np
    import indra_ocr
    model_dir=ROOT/'models'/'indra-v1.1.0'
    # An explicit existing folder makes Indra fail locally rather than download.
    if not all((model_dir/n).is_file() for n in ('det.onnx','rec.onnx','meta.json')):
        raise RuntimeError('Indra 本機模型未完整安裝，請執行安裝模型工具')
    converter=indra_ocr.load(model_dir,threads=4,furniture=True)
    page=converter.ocr(np.asarray(image.convert('L')))
    regions=[]
    for line in page.lines:
        b=unturn_box(line.box,page.rotation,image.width,image.height)
        if line.text.strip() and b[0]<b[2] and b[1]<b[3]:
            regions.append(TextRegion(line.text,tuple(b),float(line.conf),BACKENDS[backend]))
    result=ocr.OCRResult('\n'.join(r.text for r in regions),regions,BACKENDS[backend])
    result.warnings.append(f'Indra 旋轉 {page.rotation}°、傾斜 {page.skew}°，證據框已映回原圖；內容待覆核')
    return result
