"""Bounded, local second OCR pass for single-column quotation labels."""
import numpy as np
import time
from PIL import Image, ImageOps
from .domain import evidence, canonical, clone
from .quotation import is_quotation, extract_quotation

ROW_PADDING = 16
ROW_BATCH = 6
ATLAS_HEIGHT = 960


def row_tiles(image, bands):
    for y0, y1 in bands:
        bounds=(round(image.width*.01),max(0,y0-2),round(image.width*.99),min(image.height,y1+2))
        crop=image.crop(bounds)
        width,height=crop.size
        factor=min(2,1800/width)
        crop=crop.resize((max(1,round(width*factor)),max(1,round(height*factor))))
        scale=(width/crop.width,height/crop.height)
        yield bounds,ImageOps.expand(crop,border=ROW_PADDING,fill='white'),scale


def tile_groups(tiles):
    group=[]
    height=0
    for tile in tiles:
        if group and (len(group)>=ROW_BATCH or height+tile[1].height>ATLAS_HEIGHT):
            yield group
            group=[]
            height=0
        group.append(tile)
        height+=tile[1].height
    if group:
        yield group


def recognize_tiles(image, page, tiles, ocr):
    """Read adjacent strips together and project each actual box through its strip."""
    atlas=Image.new('RGB',(tiles[0][1].width,sum(tile[1].height for tile in tiles)),'white')
    slots=[]
    top=0
    for bounds,crop,scale in tiles:
        atlas.paste(crop,(0,top))
        slots.append((top,top+crop.height))
        top+=crop.height
    result=ocr._run_rapidocr(atlas,1)
    backend=getattr(ocr,'_drawiq_engine',result.engine)
    readings=[[] for _ in tiles]
    for item in result.regions:
        x0,y0,x1,y1=item.box
        slot=next((index for index,(top,bottom) in enumerate(slots) if top<=y0<y1<=bottom),None)
        if slot is None:
            continue
        bounds,_,(scale_x,scale_y)=tiles[slot]
        top=slots[slot][0]
        photo_box=[
            bounds[0]+(x0-ROW_PADDING)*scale_x,
            bounds[1]+(y0-top-ROW_PADDING)*scale_y,
            bounds[0]+(x1-ROW_PADDING)*scale_x,
            bounds[1]+(y1-top-ROW_PADDING)*scale_y,
        ]
        b=[max(low,min(high,value))/dimension for value,low,high,dimension in zip(
            photo_box,(bounds[0],bounds[1],bounds[0],bounds[1]),
            (bounds[2],bounds[3],bounds[2],bounds[3]),(*image.size,*image.size)
        )]
        try: e=evidence(page,b,item.text,backend+':quotation-row')
        except ValueError: continue
        e['ocr_confidence']=item.confidence
        e['ocr_pass']='quotation-row'
        e['ocr_crop']=[bounds[0]/image.width,bounds[1]/image.height,bounds[2]/image.width,bounds[3]/image.height]
        readings[slot].append(e)
    return readings,bool(result.text.strip() and not result.regions)


def reread_rows(image, page, initial_regions, *, cancelled=lambda:False, max_rows=40, deadline=None):
    if not is_quotation(initial_regions): return [], []
    # Multi-column sheets are handled by their original boxes, not joined across columns.
    from .quotation import grouped_lines, segments
    if any(len(segments(line['text'],True))>1 for line in grouped_lines(initial_regions)): return [], ['多欄報價保留原始分欄文字，未啟用整行重讀']
    from drawing_intelligence import ocr
    pixels=np.asarray(image.convert('L')); margin=max(2,round(image.width*.015))
    active=(pixels[:,margin:-margin]<170).sum(axis=1)>max(5,image.width*.01)
    bands=[]; start=None
    for y,on in enumerate(list(active)+[False]):
        if on and start is None: start=y
        if not on and start is not None:
            if bands and start-bands[-1][1]<=3: bands[-1][1]=y
            else: bands.append([start,y])
            start=None
    bands=[b for b in bands if b[1]-b[0]>=8]
    if len(bands)>max_rows: return [], ['報價整行重讀超過 40 行上限，保留原 OCR，請局部重讀']
    output=[]; warnings=[]
    for tiles in tile_groups(row_tiles(image,bands)):
        if cancelled(): break
        if deadline and time.monotonic()>deadline-4:
            return output,['報價整行重讀達時間預算，保留已讀原文；未重讀的欄位仍待覆核']
        readings,unmeasured=recognize_tiles(image,page,tiles,ocr)
        if unmeasured:
            warnings.append('報價重讀取得文字但沒有實測文字框，未建立自動來源；請局部框選覆核')
        for tile,rows in zip(tiles,readings):
            if not rows and len(tiles)>1 and not cancelled() and (deadline is None or time.monotonic()<deadline-4):
                single,unmeasured=recognize_tiles(image,page,[tile],ocr)
                rows=single[0]
                if unmeasured:
                    warnings.append('報價重讀取得文字但沒有實測文字框，未建立自動來源；請局部框選覆核')
            output.extend(rows)
    return output, list(dict.fromkeys(warnings))


def _unit_extension(old,new,key):
    """Adding an omitted price denominator is compatible; changing it is not."""
    if not old: return True
    if not new: return False
    a,b=canonical(old),canonical(new)
    if a==b: return True
    return key=='quotation.price' and '/' not in a and b.startswith(a+'/')

def combine_candidates(original, reread):
    """Use a fuller labeled row while retaining disagreement and the original OCR evidence."""
    output=clone(original)
    for incoming in reread:
        r=clone(incoming)
        matches=[i for i,x in enumerate(output) if x['key']==r['key'] and x['scope']==r['scope']]
        if not matches: output.append(r); continue
        if len(matches)!=1: continue  # multi-item quotations need separate assertions
        i=matches[0]; old=output[i]
        if r['state'] in ('missing','unreadable','pending_decision','blank') and old['state']=='present':
            old['candidates']+=r['candidates']
            old['evidence_ids']=list(dict.fromkeys(old['evidence_ids']+r['evidence_ids']))
            continue
        # Prefer explicit units and complete labels, not merely a higher OCR score.
        fuller=(r['unit'] and _unit_extension(old['unit'],r['unit'],r['key']) and canonical(r['unit'])!=canonical(old['unit'])) or (old['state'] in ('blank','missing','unreadable') and r['state']=='present') or ('標籤有' in ' '.join(old.get('extraction_notes',[])) and '標籤有' not in ' '.join(r.get('extraction_notes',[])))
        same=canonical(r['value'])==canonical(old['value']) and r['state']==old['state']
        unit_conflict=bool(old['unit'] and r['unit'] and not _unit_extension(old['unit'],r['unit'],r['key'])
                           and not _unit_extension(r['unit'],old['unit'],r['key']))
        if unit_conflict:
            old['state']='conflict'; old['candidates']+=r['candidates']
            old['evidence_ids']=list(dict.fromkeys(old['evidence_ids']+r['evidence_ids']))
            old.setdefault('extraction_notes',[]).append('整頁與整行 OCR 的單位／幣別／分母不同，請核對原圖')
            continue
        if not (fuller or same):
            old['candidates']+=r['candidates']
            if r['value'] and old['value'] and canonical(r['value'])!=canonical(old['value']):
                old['state']='conflict'; old.setdefault('extraction_notes',[]).append('整頁與整行 OCR 候選不同，請選定正確原文')
            old['evidence_ids']=list(dict.fromkeys(old['evidence_ids']+r['evidence_ids']))
            continue
        if old['unit'] and _unit_extension(r['unit'],old['unit'],r['key']): r['unit']=old['unit']
        r['candidates']=old['candidates']+r['candidates']; r['evidence_ids']=list(dict.fromkeys(old['evidence_ids']+r['evidence_ids']))
        if fuller and old['value'] and canonical(r['value'])!=canonical(old['value']):
            r['state']='conflict'; r['extraction_notes'].append('整頁與整行 OCR 候選不同，請選定正確原文')
        output[i]=r
    return output
