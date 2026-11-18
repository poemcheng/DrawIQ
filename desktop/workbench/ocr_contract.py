"""Validate provided OCR JSON without fabricating absent image coordinates."""
import math
from .domain import digest


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def from_ocr_json(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('pages'),list) or not payload['pages']:
        raise ValueError('OCR JSON 必須包含非空 pages 陣列')
    doc_id=payload.get('document_id','provided_ocr')
    if not isinstance(doc_id,str) or not doc_id.strip(): raise ValueError('document_id 必須是非空文字')
    dtype=payload.get('document_type','unknown')
    if dtype not in ('quote_label','engineering_drawing','unknown'): raise ValueError('document_type 無效')
    engine=payload.get('engine','provided_ocr')
    if not isinstance(engine,str) or not engine.strip(): raise ValueError('engine 必須是非空文字')
    pages=[]; regions=[]; unlocated=[]; page_numbers=set()
    for item in payload['pages']:
        if not isinstance(item,dict): raise ValueError('page 必須是物件')
        number=item.get('page',1)
        if not isinstance(number,int) or isinstance(number,bool) or number<1 or number in page_numbers:
            raise ValueError('頁碼必須是唯一正整數')
        page_numbers.add(number)
        width=item.get('width'); height=item.get('height')
        if any(value is not None and (not _number(value) or value<=0) for value in (width,height)):
            raise ValueError('頁面 width/height 必須是正有限數值')
        lines=item.get('lines',[])
        if not isinstance(lines,list): raise ValueError('lines 必須是陣列')
        pages.append({'number':number,'width':width,'height':height,'image_path':'',
                      'document_kind':'quotation' if dtype=='quote_label' else dtype})
        for index,line in enumerate(lines):
            if not isinstance(line,dict) or not isinstance(line.get('text'),str):
                raise ValueError('OCR 行 text 必須是文字')
            score=line.get('confidence')
            if score is not None and (not _number(score) or not 0<=score<=1):
                raise ValueError('OCR confidence 必須是 0..1 的有限數值或 null')
            box=line.get('bbox'); issues=[]
            if box is None: issues.append('missing_bbox')
            elif not (isinstance(box,list) and len(box)==4 and all(_number(n) for n in box)
                      and 0<=box[0]<box[2] and 0<=box[1]<box[3]):
                raise ValueError('OCR bbox 必須是頁面像素座標的正面積框')
            if width is None or height is None: issues.append('page_dimensions_missing')
            if box is not None and width is not None and height is not None:
                if box[2]>width or box[3]>height: raise ValueError('OCR bbox 超出頁面')
            source=line.get('engine',engine)
            if not isinstance(source,str) or not source.strip(): raise ValueError('行 engine 必須是非空文字')
            row={'id':'ev_ocr_'+digest([doc_id,number,index,line['text']])[:20],
                 'page':number,'text':line['text'],'source':source,'kind':'evidence',
                 'ocr_confidence':score,'source_bbox':box,'source_contract':'provided_transcription'}
            if issues:
                row.update(box=None,source_issues=issues); unlocated.append(row)
            else:
                row['box']=[box[0]/width,box[1]/height,box[2]/width,box[3]/height]
                regions.append(row)
    return {'id':doc_id,'name':doc_id,'version':1,'document_type':dtype,'pages':pages,
            'regions':regions,'unlocated_ocr':unlocated,'requirements':[],'tool_runs':[],
            'warnings':[],'status':'needs_review','tenant':'provided_ocr',
            'source_sha256':digest(payload),'source_copy':'',
            'source_contract':{'schema':'quote-drawing-ocr/1','execution':'provided_transcription',
                'confidence_mode':'supplied_transcription_score','ocr_executed':False}}
