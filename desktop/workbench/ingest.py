from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path

import fitz
from PIL import Image, ImageSequence

from .domain import evidence, requirement, uid
from .paths import resolve_path

REPO=Path(__file__).resolve().parents[2]
VENDORED=Path(__file__).resolve().parents[1]/'vendor'/'legacy'
LEGACY=VENDORED if VENDORED.is_dir() else REPO/'DrawIQ_v0.2.0_通用工程圖工單_VLM'/'drawing_intelligence_system'
DEFAULT_DATA=REPO/'DrawIQ_Workbench'/'data'
SUPPORTED={'.pdf','.png','.jpg','.jpeg','.tif','.tiff','.bmp'}

def _page_raster_boxes(page):
    """Actual PDF bitmap placements in the rendered visible-page coordinates."""
    boxes=[]
    for item in page.get_image_info():
        rect=fitz.Rect(item['bbox'])*page.rotation_matrix
        visible=rect & page.rect
        if visible.is_empty: continue
        box=[max(0,visible.x0/page.rect.width),max(0,visible.y0/page.rect.height),
             min(1,visible.x1/page.rect.width),min(1,visible.y1/page.rect.height)]
        if box[0]<box[2] and box[1]<box[3]: boxes.append(box)
    return boxes

def pdf_raster_boxes(source, page_number):
    """Read missing coverage metadata for older documents without changing them."""
    try:
        with fitz.open(resolve_path(source)) as pdf:
            return _page_raster_boxes(pdf[page_number-1])
    except (OSError,ValueError,IndexError,RuntimeError,fitz.FileNotFoundError,fitz.FileDataError):
        return None

def sha256(path):
    h=hashlib.sha256()
    with resolve_path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def prepare_document(source, data_root, *, tenant='local', logical_id=None):
    source=Path(source).resolve()
    if source.suffix.lower() not in SUPPORTED: raise ValueError('不支援的文件類型')
    sha=sha256(source)
    doc_id=hashlib.sha256((tenant+'|'+str(source)+'|'+sha).encode()).hexdigest()[:24]
    assets=Path(data_root)/'assets'/doc_id; assets.mkdir(parents=True,exist_ok=True)
    original=assets/('source'+source.suffix.lower())
    if not original.exists(): shutil.copy2(source,original)
    pages=[]
    if source.suffix.lower()=='.pdf':
        with fitz.open(source) as pdf:
            if pdf.needs_pass: raise ValueError('PDF 已加密，請先提供可讀版本')
            for i,page in enumerate(pdf):
                image=assets/f'page_{i+1:04}.png'
                if not image.exists(): page.get_pixmap(dpi=180,alpha=False).save(image)
                with Image.open(image) as im: width,height=im.size
                words=[]
                for w in page.get_text('words'):
                    rect=fitz.Rect(w[:4])*page.rotation_matrix
                    box=[max(0,rect.x0/page.rect.width),max(0,rect.y0/page.rect.height),min(1,rect.x1/page.rect.width),min(1,rect.y1/page.rect.height)]
                    if box[0]<box[2] and box[1]<box[3]: words.append({'text':w[4],'box':box})
                pages.append({'number':i+1,'image_path':str(image),'width':width,'height':height,'embedded_text':page.get_text(),
                    'embedded_words':words,'pdf_image_boxes':_page_raster_boxes(page),'pdf_rotation':page.rotation,'media_box':list(page.mediabox),'crop_box':list(page.cropbox),
                    'coordinate_space':'rendered_visible_page_top_left_normalized','ocr_text':'','processing':'not_run'})
    else:
        with Image.open(source) as im:
            for i,frame in enumerate(ImageSequence.Iterator(im)):
                rgb=frame.convert('RGB'); image=assets/f'page_{i+1:04}.png'; rgb.save(image)
                pages.append({'number':i+1,'image_path':str(image),'width':rgb.width,'height':rgb.height,'embedded_text':'','embedded_words':[],
                    'pdf_rotation':0,'coordinate_space':'rendered_visible_page_top_left_normalized','ocr_text':'','processing':'not_run'})
    return {'id':doc_id,'tenant':tenant,'logical_id':logical_id or str(source),'name':source.name,'source_path':str(source),
        'source_copy':str(original),'source_sha256':sha,'pages':pages,'regions':[],'requirements':[],
        'status':'needs_review','profile':'general','warnings':[],'tool_runs':[],'engine_version':'0.11.0'}

def locate_evidence(doc, page, text, value=''):
    """Conservative text linking. A match is a candidate, never human verified."""
    from .domain import canonical
    wanted=canonical(text); val=canonical(value)
    import re
    def contains(haystack,needle):
        if not needle: return False
        if re.fullmatch(r'[+\-]?\d+(?:[.,]\d+)?',needle):
            return bool(re.search(r'(?<![\w.,])'+re.escape(needle)+r'(?=$|[^\w.,]|(?:mm|inch|in|μm|um|pcs)\b)',haystack,re.I))
        if re.fullmatch(r'[a-z0-9._/-]+',needle,re.I):
            return bool(re.search(r'(?<![a-z0-9])'+re.escape(needle)+r'(?![a-z0-9])',haystack,re.I))
        return len(needle)>=2 and needle in haystack
    ranked=[]
    for e in doc['regions']:
        if e['page']!=page or e['kind']!='evidence': continue
        token=canonical(e['text'])
        if not token: continue
        priority = 0 if wanted and token==wanted else 1 if contains(token,wanted) else 2 if len(token)>=3 and contains(wanted,token) else 3 if contains(token,val) else None
        if priority is not None: ranked.append((priority,e['id']))
    if not ranked: return []
    best=min(x[0] for x in ranked); found=[eid for priority,eid in ranked if priority==best]
    # Repeated "26"/"ISO ..." occurrences cannot identify which feature or
    # test the parser meant. Keep the raw candidates; require spatial binding.
    return found if len(found)==1 else []

def import_legacy(store, path, progress=lambda _:None, cancelled=lambda:False):
    path=Path(path); raw=json.loads(path.read_text(encoding='utf-8'))
    sources={}
    for p in (REPO/'engineering_drawings').rglob('*.pdf'):
        sources[sha256(p)]=p
    results=[]; errors=[]
    for item in raw.get('documents',[]):
        if cancelled(): break
        try:
            # Prefer the supplied workspace copy even when the old desktop path exists.
            source=sources.get(item['source_sha256']) or Path(item['source_path'])
            if not source.exists() or sha256(source)!=item['source_sha256']: source=None
            if source is None: raise ValueError('找不到雜湊相符的原始圖面')
            progress(f'匯入歷史解析：{Path(source).name}')
            doc=prepare_document(source,store.root)
            doc['warnings']=['歷史解析匯入；全部需重新驗證，原始分數不是已校準機率']+item.get('warnings',[])
            for oldpage in item.get('pages',[]):
                p=next(p for p in doc['pages'] if p['number']==oldpage['page_number'])
                old_image=path.parent/'pages'/item['document_id']/Path(oldpage['image_path']).name
                ow,oh=p['width'],p['height']
                if old_image.exists():
                    with Image.open(old_image) as im: ow,oh=im.size
                p['ocr_text']=oldpage.get('raw_text',''); p['processing']='historical'; p['vlm_status']=oldpage.get('vlm',{}).get('status','未啟用')
                for r in oldpage.get('text_regions',[]):
                    x0,y0,x1,y1=r['box']; box=[max(0,x0/ow),max(0,y0/oh),min(1,x1/ow),min(1,y1/oh)]
                    if box[0]<box[2] and box[1]<box[3]: doc['regions'].append(evidence(p['number'],box,r['text'],r.get('source','historical')))
            for key,f in item.get('fields',{}).items():
                if not f.get('value'): continue
                req=requirement(key,f['value'],label=f.get('label',key),evidence_ids=locate_evidence(doc,f.get('page_number',1),f.get('evidence',''),f['value']))
                req['candidates']=[{'value':f['value'],'source':f.get('source'),'evidence_text':f.get('evidence'),'raw_score':f.get('confidence'),'imported_run':raw.get('run_id')}]
                doc['requirements'].append(req)
            # Recover dimension occurrences from OCR regions, preserving separate positions.
            from .extraction import dimensions_from_regions
            recovered=dimensions_from_regions(doc)
            doc['requirements'].extend(recovered)
            for d in item.get('dimensions',[]):
                links=locate_evidence(doc,d.get('page_number',1),d['raw'])
                if any(r['value']==d['raw'] and set(r['evidence_ids'])&set(links) for r in recovered): continue
                doc['requirements'].append(requirement('dimension.'+d['kind'],d['raw'],label=d['kind'],unit=d.get('unit',''),subject='unassigned_feature',evidence_ids=links,upper_tolerance=d.get('upper_tolerance',''),lower_tolerance=d.get('lower_tolerance','')))
            append_tables(doc,item.get('tables',[]))
            results.append(store.add(doc,'匯入 DrawIQ v0.2 歷史解析，原核准狀態不沿用')['id'])
        except Exception as exc: errors.append(f'{item.get("document_id")}: {exc}')
    return {'document_ids':results,'errors':errors}

def append_tables(doc, tables):
    for ti,table in enumerate(tables):
        for ri,row in enumerate(table.get('rows',[])):
            for ci,value in enumerate(row):
                if not str(value).strip(): continue
                columns=table.get('columns',[]); label=columns[ci] if ci<len(columns) else f'欄{ci+1}'
                req=requirement(f'table.{ti}.{ri}.{ci}',value,label=f'{table.get("name","表格")}｜{ri+1}列｜{label}',subject=f'table_{ti}_row_{ri}',
                    evidence_ids=locate_evidence(doc,table.get('page_number',1),str(value)))
                req['table']={'name':table.get('name'),'row':ri,'column':ci,'columns':columns}
                doc['requirements'].append(req)

def import_example(store, path):
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    doc=prepare_document(raw['document']['source_path'],store.root,tenant='design-example')
    doc['name']='[設計示例] '+doc['name']; doc['warnings']=['分析人員轉錄／約略框；尚未完成工程覆核，不是金標']
    mapping={}
    for old in raw['evidence_regions']+raw.get('features',[]):
        poly=old['polygon']; box=[min(x[0] for x in poly),min(x[1] for x in poly),max(x[0] for x in poly),max(x[1] for x in poly)]
        e=evidence(old.get('page_number',1),box,old.get('raw_text',old.get('type','')),'analyst_example','feature' if old in raw.get('features',[]) else 'evidence')
        mapping[old['id']]=e['id']; doc['regions'].append(e)
    labels={'customer.external_part_number':'客戶料號','thread.specification':'螺紋規格','coating.minimum_thread_count':'最少塗膠牙數',
        'coating.maximum_uncoated_full_threads':'未塗膠完整牙數上限','required_operating_temperature_capability':'工作溫度能力',
        'adhesive.selection':'膠種選擇','standard.reference_raw':'規範原文（待釐清）','required_document':'應附文件',
        'demand.quantity':'年度需求量','setup_time':'設定時間','unit_price':'單價'}
    for old in raw['requirements']:
        val=old['value']; val=json.dumps(val,ensure_ascii=False) if isinstance(val,dict) else val
        req=requirement(old['property'],val,label=labels.get(old['property'],old['property']),subject=old['subject_id'],unit=old.get('unit') or '',operator=old['operator'],state=old['value_state'],
            scope=json.dumps(old['scope'],ensure_ascii=False),conditions=json.dumps(old['conditions'],ensure_ascii=False),
            evidence_ids=[mapping[x] for x in old['evidence_ids']],origin='analyst_example')
        if old['subject_id'] in mapping: req['feature_ids']=[mapping[old['subject_id']]]
        doc['requirements'].append(req)
    return store.add(doc,'匯入明確標示的未核准設計示例')
