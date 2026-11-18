import re
from datetime import datetime

ALIASES = {
 'quote_no':['報價單號','報價單編號'], 'staff':['報價人員'], 'quote_date':['報價日期'],
 'valid_until':['報價有效日期','有效日期'], 'material':['材質','材料'],
 'coating_grade':['報價級別','塗層級別'], 'color':['顏色'],
 'coating_method':['塗佈方式','塗覆方式'], 'specification':['規格'],
 'quantity':['數量'], 'unit_price':['單價','報價'], 'quality_requirements':['品質要求'],
 'packing_notes':['包裝方式'], 'contact_phone':['聯絡電話'],
 'drawing_no':['圖號','圖面編號'], 'part_no':['零件號','品號','外包件號']
}
QUOTE_FIELDS = list(ALIASES)[:14]
DRAWING_FIELDS = ['specification','material','coating_grade','drawing_no','part_no','quantity']
DATE_RE = re.compile(r'(?<!\d)(20\d\d)[/.-](\d{1,2})[/.-](\d{1,2})(?!\d)')
METRIC = re.compile(r'^M(?P<diameter>\d+(?:\.\d+)?)(?:x(?P<pitch>\d+(?:\.\d+)?))?(?:(?:x(?P<length>\d+(?:\.\d+)?))|(?:-(?P<tolerance>\d+[a-zA-Z]+)))?$', re.I)
UNIFIED = re.compile(r'^(?P<diameter>\d+(?:/\d+)?)-(?P<tpi>\d+)(?P<series>UNC|UNF|UNEF)(?:x(?P<length>\d+(?:\.\d+)?))?$',re.I)
GRADE = re.compile(r'\b(?:precoat|precote)\s*[- ]?\s*(\d+(?:-\d+)?)\s*(?:[（(]([^()（）]+)[)）])?',re.I)


def compact(text):
    return re.sub(r'\s+', '', text).replace('：',':').replace('×','x').replace('＊','x').replace('*','x')


def parse_spec(value):
    s = compact(value).strip(':').replace('．','.')
    a = METRIC.fullmatch(s)
    if a:
        d=a.groupdict()
        return {'system':'metric','diameter_mm':float(d['diameter']), 'pitch_mm':float(d['pitch']) if d['pitch'] else None,
                'length_mm':float(d['length']) if d['length'] else None,'tolerance':d['tolerance'],'normalized':s}, []
    b=UNIFIED.fullmatch(s)
    if b:
        d=b.groupdict(); raw=d['diameter']
        diam=float(raw.split('/')[0])/float(raw.split('/')[1]) if '/' in raw else float(raw)
        return {'system':'unified','diameter_inch':diam,'tpi':int(d['tpi']),'series':d['series'].upper(),
                'length_value':float(d['length']) if d['length'] else None,'length_unit':None,'normalized':s}, ['length_unit_unverified'] if d['length'] else []
    return None, ['unparsed_spec']


def normalized(field, value):
    s=value.strip().strip('：:').strip()
    if field in ('quote_date','valid_until'):
        m=DATE_RE.search(compact(s))
        if not m:return None, ['invalid_date']
        try:return datetime(*map(int,m.groups())).strftime('%Y-%m-%d'), []
        except ValueError:return None,['invalid_date']
    if field=='quote_no':
        s=compact(s).upper(); return s, [] if re.fullmatch(r'KH\d{8}',s) else ['unusual_quote_no']
    if field=='specification':return parse_spec(s)
    if field=='quantity':
        m=re.fullmatch(r'\s*([\d,]+)\s*([\w\u4e00-\u9fff]*)\s*',s)
        if not m:return None,['unparsed_quantity']
        return {'value':int(m[1].replace(',','')),'unit_raw':m[2] or None},[]
    if field=='coating_grade':
        m=GRADE.search(s)
        return ({'family':'precoat', 'code':m[1],'color_note':m[2]} if m else s), ([] if m else ['unparsed_grade'])
    if field=='color':return {'紅':'紅色','红':'紅色','turquoise':'土耳其藍'}.get(s.lower(),s),[]
    if field=='unit_price':
        m=re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:TWD|NTD|元)',s,re.I)
        if not m:return None,['blank_price']
        return {'amount':float(m[1]),'currency':'TWD'},[]
    return s,[]


def detect_type(lines,explicit=None):
    if explicit in ('quote_label','engineering_drawing'):return explicit
    t='\n'.join(line.get('text','') for line in lines)
    if '報價單號' in compact(t) and '報價級別' in compact(t):return 'quote_label'
    if any(w in t for w in ('圖號','公差','THREAD','螺紋','螺絲','DIN 267','表面處理')):return 'engineering_drawing'
    return 'unknown'


def extract(doc, engine='provided_ocr'):
    lines=[]
    for p in doc.get('pages',[]):
        for l in p.get('lines',[]):lines.append({**l,'page':p.get('page',1)})
    dtype=detect_type(lines,doc.get('document_type'))
    wanted=QUOTE_FIELDS if dtype=='quote_label' else DRAWING_FIELDS if dtype=='engineering_drawing' else []
    found={}; issues=[]
    for line in lines:
        raw=line.get('text',''); canon=compact(raw)
        for key in wanted:
            for alias in sorted(ALIASES[key],key=len,reverse=True):
                if not canon.startswith(alias):continue
                suffix=canon[len(alias):].lstrip(':')
                # Avoid matching shorter alias as a longer key; ignore label without a value.
                if not suffix:break
                if key=='unit_price' and suffix.startswith('單號'):break
                # prevent generic 材料 from swallowing long paragraphs without layout evidence
                val=suffix
                n,errors=normalized(key,val)
                conf=float(line.get('confidence',0.5))
                if key=='unit_price' and n is None:break
                status='needs_review' if errors or conf<0.80 else 'extracted'
                obj={'raw':val,'normalized':n,'source':{'page':line['page'],'bbox':line.get('bbox'), 'text':raw,'engine':engine},'confidence':conf,'status':status}
                if key in found and found[key]['normalized'] != n:
                    issues.append({'field':key,'code':'conflict','message':'Multiple distinct anchored values'});found[key]['status']='conflict'
                else:found.setdefault(key,obj)
                for e in errors:issues.append({'field':key,'code':e,'message':'Requires manual verification'})
                break
    if dtype=='engineering_drawing':
        # Unanchored drawing callouts: candidates only, not asserted field extraction.
        pattern=r'(?<![\w/])(?:M\d+(?:\.\d+)?(?:[x×]\d+(?:\.\d+)?){0,2}(?:-\d+[gGhH])?|\d+/\d+-\d+(?:UNC|UNF|UNEF)(?:[x×]\d+(?:\.\d+)?)?)'
        cand=[]
        for line in lines:
            for m in re.finditer(pattern,line.get('text',''),re.I):
                n,errs=parse_spec(m[0]);
                if n:cand.append({'raw':m[0],'normalized':n,'source':{'page':line['page'],'bbox':line.get('bbox'),'text':line['text'],'engine':engine},'status':'candidate_needs_review'})
        if cand:found['specification_candidates']=cand
    if dtype=='quote_label' and 'quote_date' in found and 'valid_until' in found:
        a=found['quote_date']['normalized'];b=found['valid_until']['normalized']
        if a and b and b<a:
            issues.append({'field':'valid_until','code':'invalid_date_order','message':'Validity before quotation date'});found['valid_until']['status']='needs_review'
    for f in wanted:
        if f not in found:
            found[f]={'raw':None,'normalized':None,'source':None,'confidence':0.0,'status':'missing'}
    if dtype=='unknown':issues.append({'field':'document_type','code':'unknown_document','message':'Needs document classification'})
    return {'document_id':doc.get('document_id','unknown'),'document_type':dtype,'fields':found,'issues':issues}
