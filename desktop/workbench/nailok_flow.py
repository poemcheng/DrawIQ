"""Taiwan Nylok feature review: full readings -> alternatives -> human facts.

Learning changes rankings only. Original OCR, corrected readings and provenance
remain separate, and every subsequent document still needs human confirmation.
"""
import re
import unicodedata
from collections import defaultdict
from datetime import date

from .domain import canonical, clone, digest, invalidate, requirement, now
from .quotation import FIELDS, extract_quotation, is_quotation, segments

LEGACY_FEATURES = [
 ('drawing_number','圖號／版次',('drawing_number','part_number','document_number','revision'),r'圖號|版次|drawing.?n|dwg|revision'),
 ('nailok.dimensions','尺寸／公差',('dimension','tolerance'),r'±|[Ø⌀Φ]|公差|尺寸|\d\s*[+-]\s*\d'),
 ('finish','表面處理／前處理',('finish','surface','heat'),r'表面處理|鍍|plating|black oxide|finish'),
 ('nailok.coverage','塗膠位置／範圍／牙數／公差',('coating.coverage','coating.patch','coating.length','thread.patch'),r'塗膠|塗佈|牙數|牙長|patch|coverage|coating length'),
 ('nailok.performance','扭力／耐溫／測試',('torque','temperature','test','inspection'),r'扭力|扭矩|耐溫|torque|temperature|測試|試驗'),
]
FIELD_ALIASES = {
    'customer.name': (('customer',), r'客戶|customer'),
    'material': ((), r'材質|材料|\bmaterial\b|steel|鋼|SUS\d'),
    'adhesive.product': (('coating.material',), r'膠別|膠種|precote|tuflok|loctite|nylon|polyamide'),
    'coating.color': ((), r'塗膠顏色|coating colou?r|藍|藍色|blue|red|yellow'),
    'coating.method': (('process',), r'塗佈|塗布|coating method|patch|precoat'),
    'product.specification': (('dimension.thread','thread.specification','specification'), r'螺紋|規格|\bM\d+\s*[x×]|UNC|UNF'),
    'quotation.quantity': (('quantity','order_quantity'), r'數量|QTY|quantity|PCS|/年'),
    'quality.requirements': (('quality','standard','inspection','certificate'), r'品質|檢驗|報告|證明|ISO|DIN|inspection|certificate'),
    'packaging.instructions': (('packaging',), r'包裝|包材|packing|packaging'),
}
VISIBLE_LABELS = {'quotation.quantity':'數量', 'quotation.price':'報價'}
FEATURES = [
    (key, VISIBLE_LABELS.get(key, label), (key, *FIELD_ALIASES.get(key, ((), aliases))[0]),
     FIELD_ALIASES.get(key, ((), aliases))[1])
    for key, (label, aliases) in FIELDS.items()
]
FIELD_DEFINITIONS = {row[0]:row for row in [*FEATURES, *LEGACY_FEATURES]}
LABELS = {key:row[1] for key,row in FIELD_DEFINITIONS.items()}
REQUIRED = {'product.specification','quotation.quantity','adhesive.product','quality.requirements'}
LABEL_BOUND = {
    'quotation.number','quotation.project','quotation.salesperson','quotation.date',
    'quotation.valid_until','quotation.price','quotation.notes','contact.phone','contact.fax',
}
PRICE_FORMAT = re.compile(r'\s*(?:[/／]\s*)?(?:TWD|USD|EUR|CNY|JPY)(?:\s*[/／]\s*[A-Za-z\u3400-\u9fff]+)?\s*',re.I)
CONTACT_NUMBER = re.compile(r'[+（(\d][\d\s()（）.+\-]+(?:\s*[（(]?(?:分機|分机|ext\.?|#)\s*\d+\s*[）)]?)?',re.I)
ANNUAL_QUANTITY=re.compile(r'年度|每年|年用量|年需求|年產量|年採購量|計畫|預估|[/／]\s*(?:年|year\b|yr\b)|\b(?:annual(?:ly)?|yearly|forecast|planned)\b|\bper\s+(?:year|yr)\b',re.I)
COATING_METHOD=r'\b(?:nylon\s+patch|precoat(?:ed)?|patch|dip\s+coating|spray\s+coating)\b|預塗|點膠|噴塗|浸塗'
EXTRA_SOURCE_LABELS={
    'drawing_number':r'圖號|版次|drawing\s*(?:no\.?|number)|dwg\.?\s*(?:no\.?)?|rev(?:ision)?\.?',
    'product.specification':r'螺紋(?:規格)?|產品規格|thread(?:\s+specification)?',
    'nailok.dimensions':r'尺寸|公差|dimensions?|tolerances?',
    'finish':r'表面處理|前處理|surface\s+(?:treatment|finish)|finish|plating',
    'coating.color':r'塗膠顏色|coating\s+colou?r',
    'nailok.coverage':r'塗膠(?:位置|長度|範圍)?|塗佈範圍|牙數|patch\s+(?:position|length|coverage)|coating\s+(?:position|length|coverage)|coverage',
    'nailok.performance':r'扭力|扭矩|耐溫|測試|試驗|torque|temperature|test',
    'quotation.quantity':r'本次數量|訂購數量|qty\.?|order\s+(?:qty\.?|quantity)',
    'quality.requirements':r'品質(?:要求|需求)?|驗收|檢驗|應附文件|quality|inspection|certificate',
    'packaging.instructions':r'包裝|包材|packing',
}
ENGINEERING_NOTE_PATTERNS={
    'finish':r'\b(?:zinc|nickel|chromium)\s+plat(?:ed|ing)\b|\bblack oxide\b|鍍鋅|鍍鎳|陽極處理',
    'nailok.coverage':r'(?=.*(?:塗[膠佈布]|thread\s+patch|coating|patch))(?=.*(?:牙距|牙數|位置|範圍|長度|周向|length|range|coverage|position|\d+\s*(?:度|°|mm)))',
    'nailok.performance':r'(?:扭力|扭矩|耐溫|\btorque\b|\btemperature\b).*(?:\d|要求)|(?:需|應|須|must|shall).*?(?:測試|試驗|\btest(?:ed|ing)?\b)',
    'quality.requirements':r'(?:依(?:據|照)?|符合|遵循|\b(?:per|according to|conform to)\b).*?\b(?:ISO|DIN|JIS|ASTM|ASME)\s*[-A-Z]*\d',
    'packaging.instructions':r'(?:包裝|包材).*?\d+\s*(?:袋|箱|支|個)|(?:[每需應須]|客戶要求).*?(?:包裝|裝箱)',
}


def region_fingerprint(region):
    return digest({key:region.get(key) for key in ('id','kind','page','box','text','source')})


def annotation_text(doc,region):
    correction=doc.get('nailok_annotation_corrections',{}).get(region['id'])
    if correction and correction['source_fingerprint']==region_fingerprint(region):
        return correction['text']
    return region['text']


def source_region_snapshot(doc,region):
    snapshot={key:region.get(key) for key in ('id','page','box','text','source')}
    corrected=annotation_text(doc,region)
    if corrected!=region['text']:
        snapshot['annotation_text']=corrected
    return snapshot

def source_fingerprint(doc, rid):
    r=next((r for r in doc['requirements'] if r['id']==rid),None)
    if r is None: return None
    linked={e['id']:e for e in doc['regions']}
    return digest([doc['source_sha256'],{k:r.get(k) for k in ('id','key','value','unit','state','deleted',
        'operator','upper_tolerance','lower_tolerance','raw_value','subject','scope','conditions')},
        [source_region_snapshot(doc,linked[e]) if e in linked else None
         for e in r['evidence_ids']]])

def selection_current(doc,r):
    if r.get('origin')!='nailok_human': return True
    if 'nailok_evidence_fingerprints' in r:
        linked={e['id']:e for e in source_regions(doc,r)}
        originals=r['nailok_evidence_fingerprints']
        if linked.keys()!=originals.keys() or any(digest(source_region_snapshot(doc,e))!=originals[eid] for eid,e in linked.items()):
            return False
    if r.get('nailok_source_ids'):
        return all(source_fingerprint(doc,rid)==r.get('nailok_source_fingerprints',{}).get(rid)
                   for rid in r['nailok_source_ids'])
    rid=r.get('nailok_source_id')
    return not rid or source_fingerprint(doc,rid)==r.get('nailok_source_fingerprint')

def source_regions(doc,r):
    ids=set(r.get('evidence_ids',[]))
    if r.get('origin')=='nailok_human' and 'nailok_evidence_fingerprints' not in r:
        ids|={source for region in doc['regions'] if region['id'] in ids
              for source in region.get('transcription_of',[])}
    return [e for e in doc['regions'] if e['id'] in ids and is_source_region(e)]


def is_source_region(region):
    return (region['kind']=='evidence' and not region.get('source','').startswith('VLM:')
            and not region.get('transcription_of'))

def candidate_value(r):
    if r['key'].startswith('dimension.') and (r.get('upper_tolerance') or r.get('lower_tolerance')):
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)',r['value'].strip()):
            return r.get('raw_value') or r['value']
        return r.get('raw_value') or (r['value']+' +'+(r.get('upper_tolerance') or '0')+' / '+(r.get('lower_tolerance') or '0'))
    return r['value']

def features(doc, *, include_legacy=True):
    mapping=doc.get('nailok_mapping',{});by_id={r['id']:r for r in doc['requirements'] if not r['deleted']}
    rows=[]
    definitions=[*FEATURES,*LEGACY_FEATURES] if include_legacy else FEATURES
    for key,label,_,_ in definitions:
        r=by_id.get(mapping.get(key))
        # Existing human selections are visible without editing old documents.
        if r is None: r=by_id.get(doc.get('quotation_mapping',{}).get(key))
        current=bool(r and selection_current(doc,r))
        if current and key=='quotation.quantity' and r['state']=='present':
            raw='\n'.join(annotation_text(doc,e) for e in source_regions(doc,r))
            current=not annual_quantity(raw,r['value'],r['unit'])
        confirmed=bool(current and r['status']=='verified' and r['support_verified'] and r['state'] in ('present','not_applicable'))
        rows.append({'key':key,'label':label,'source':r,'confirmed':confirmed,'required':key in REQUIRED,
            'status':'來源已變更，請重確認' if r and not current else '已確認' if confirmed else
                     '圖面未提供' if r and r['state']=='missing' else '待確認',
            'value':r['value'] if r else ''})
    return rows


def explicit_value(text,key):
    if key in FIELDS:
        matches=[value for line in text.splitlines() for field,value,_ in segments(line) if field==key and value]
        if matches:
            return '\n'.join(dict.fromkeys(matches))
    return engineering_label_value(text,key)


def engineering_label_value(text,key):
    if key=='coating.method':
        matches=re.findall(r'^[ \t]*coating[ \t]*[:：][ \t]*('+COATING_METHOD+r')[ \t]*$',text,re.I|re.M)
        if matches: return '\n'.join(dict.fromkeys(matches))
    alias=EXTRA_SOURCE_LABELS.get(key)
    if alias:
        matches=re.findall(r'^[ \t]*(?:'+alias+r')[ \t]*[:：][ \t]*([^\n|;；]+)',text,re.I|re.M)
        return '\n'.join(dict.fromkeys(value.strip() for value in matches if value.strip()))
    return ''


def price_unit_display(unit):
    return f'待補金額 · {unit}' if unit else '待補金額'


def annual_quantity(text,value,unit):
    labeled=[line for line in text.splitlines()
             if any(key=='quotation.quantity' for key,_,_ in segments(line))]
    context='\n'.join(labeled) if labeled else text
    return bool(ANNUAL_QUANTITY.search('\n'.join((context,value,unit))))


def labeled_choices(doc):
    """Use measured boxes from each recognizer independently for label binding."""
    groups=defaultdict(list)
    originals={}
    for region in doc['regions']:
        if not is_source_region(region):
            continue
        originals[region['id']]=region
        groups[region['source']].append({**region,'text':annotation_text(doc,region)})
    choices=defaultdict(list)
    for measured in groups.values():
        by_id={region['id']:region for region in measured}
        quote_form=is_quotation(measured)
        for record in extract_quotation(measured,include_missing=False,detected=True,tolerant=quote_form):
            key=record['key']
            if key not in FIELDS:
                continue
            identifiers=record['evidence_ids']
            source_text=record['candidates'][0]['raw_text']
            evidence_parts=[by_id[eid] for eid in identifiers]
            value,unit=record['value'],record['unit']
            raw='\n'.join(originals[eid]['text'] for eid in identifiers)
            display='\n'.join(region['text'] for region in evidence_parts)
            if key=='coating.color' and not quote_form:
                # A color in a generic finish callout is not a coating color.
                if not re.search(r'塗膠顏色|涂胶颜色|coating\s+colou?r',display,re.I):
                    continue
            if key=='material' and not quote_form:
                from .engineering import context_heading
                if context_heading(record,by_id) or re.search(
                    r'(?:thread.?lock(?:ing)?|torq.?patch|塗膠|防鬆膠)[^\n:：]{0,30}(?:Material|材料|材質)\s*[:：]',
                    display,re.I):
                    continue
            if key in ('contact.phone','contact.fax'):
                specific=r'[聯联][絡络]\s*[電电]\s*[話话]' if key=='contact.phone' else r'客\s*[戶户]\s*[傳传]\s*真'
                if not quote_form and not re.search(specific,source_text):
                    continue
                if not CONTACT_NUMBER.fullmatch(value) or len(re.findall(r'\d',value))<6:
                    continue
            blank_price=key=='quotation.price' and record['state']=='blank' and bool(unit)
            if record['state']!='present' and not blank_price:
                continue
            warnings=list(dict.fromkeys([*record['extraction_notes'],*candidate_warnings(key,value,unit,display)]))
            if raw!=display:
                warnings.append('已依原圖修正標註')
            if blank_price:
                warnings.append('原圖只有幣別或計價單位，未見金額')
            score=(13 if raw!=display else 10)+int(not record.get('label_repair'))
            if key in ('quotation.quantity','quotation.price') and unit:
                score+=1
            choices[key].append({
                'id':identifiers[0] if len(identifiers)==1 else f'label-{key}-{digest(identifiers)[:12]}',
                'candidate_id':None,'value':value,'unit':unit,'raw_text':raw,'display_text':display,
                'evidence_ids':identifiers,'score':score,
                'learned_from':None,'issues':warnings,'label':LABELS[key],
                'pages':sorted({region['page'] for region in evidence_parts}),
                'prefill_allowed':not blank_price,'blank_price':blank_price,
            })
    return choices


def engineering_note_value(text,key):
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if key=='product.specification' and re.fullmatch(r'M\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?){1,3}',line,re.I):
            return line
        if key=='quotation.quantity':
            match=re.search(r'(?<![A-Za-z])(?:batch\s+qty|qty\.?|本次數量|訂購數量)\s*[:：]?\s*(\d[\d,.]*\s*[A-Za-z\u3400-\u9fff]*(?:[/／][A-Za-z\u3400-\u9fff]+)?)\s*$',line,re.I)
            if match: return match[1].strip()
        if key=='coating.method':
            method=re.search(COATING_METHOD,line,re.I)
            range_only=re.match(r'^patch(?:\s+(?:length|coverage|position|range))?\s*[:：]?\s*\d',line,re.I)
            if method and not range_only:
                constrained=re.search(r'不可|不得|僅限|除非|不塗|\b(?:not|only|except|unless)\b',line,re.I)
                return line if constrained else method[0]
        pattern=ENGINEERING_NOTE_PATTERNS.get(key)
        if pattern and re.search(pattern,line,re.I): return line
    return ''


def candidate_warnings(key,value,unit,text):
    if key!='quotation.quantity':
        if key=='quotation.price' and unit.rsplit('/',1)[-1].upper() in ('M','K'):
            return ['報價分母是 M／K，請核對計價單位']
        return []
    if annual_quantity(text,value,unit):
        return ['年度／計畫需求，不能直接當單批量']
    if re.search(r'\d\s*[MK]\b',value,re.I) or unit.upper() in ('M','K'):
        return ['M／K 倍數與數量範圍須人工確認']
    return []


def matches_quote_label(key, choices, value, unit, linked):
    from .quotation_layout import equivalent_unit,equivalent_value
    linked_ids={region['id'] for region in linked}
    return any(row['prefill_allowed'] and linked_ids.intersection(row['evidence_ids'])
               and equivalent_value(key,row['value'])==equivalent_value(key,value)
               and equivalent_unit(row['unit'])==equivalent_unit(unit) for row in choices)


def alternatives(doc,key,examples=(),*,all_readings=False,quoted=None):
    definition=FIELD_DEFINITIONS.get(key)
    if not definition: raise ValueError('未知耐落特徵')
    from .feedback import features as vector,cosine
    from .quotation_layout import source_issue
    _,_,prefixes,pattern=definition;result=[]
    quoted=(labeled_choices(doc) if quoted is None else quoted).get(key,())
    all_regions={e['id']:e for e in doc['regions']}
    region_map={eid:e for eid,e in all_regions.items() if is_source_region(e)}
    wrong_role_regions=set()
    example_vectors=[(vector(e['raw_text']),e) for e in examples if e['field']==key]
    for r in doc['requirements']:
        if r['deleted'] or r.get('origin')=='nailok_human': continue
        linked=[region_map[eid] for eid in r['evidence_ids'] if eid in region_map]
        if not linked: continue
        raw='\n'.join(e['text'] for e in linked)
        display='\n'.join(annotation_text(doc,e) for e in linked)
        typed=any(r['key']==p or r['key'].startswith(p+'.') for p in prefixes)
        if key=='nailok.dimensions' and r['key'].startswith('dimension.thread'): typed=False
        score=6 if typed else 0
        if re.search(pattern,display+' '+r['label'],re.I): score+=2
        if not score and not all_readings: continue
        context=vector(display) if example_vectors else None
        matches=[(cosine(context,trained),e) for trained,e in example_vectors]
        similarity,learned=max(matches,key=lambda x:x[0]) if matches else (0,None)
        value=candidate_value(r); unit=r['unit']
        changed=display!=raw
        corrected_value=''
        if changed and typed and len(linked)==1:
            corrected_value=explicit_value(display,key)
            if not corrected_value and canonical(raw) in (canonical(value),canonical(value+unit)):
                corrected_value=display.strip()
            if corrected_value:
                value=corrected_value; unit=''
        if key=='quotation.quantity' and (corrected_value or not unit):
            value,unit=quantity_parts(value,unit)
        warnings=candidate_warnings(key,value,unit,display)
        if r['state']!='present': warnings.append('原候選狀態：'+r['state'])
        if changed: warnings.append('原文已修正，請以圖上標註核對')
        wrong_role=False
        if typed and key in ('material','coating.color'):
            issue=source_issue(r,key,all_regions)
            if issue:warnings.append(issue)
            wrong_role='不能當' in issue or '不能從' in issue
            if wrong_role: wrong_role_regions.update(e['id'] for e in linked)
        if typed and key in LABEL_BOUND and not matches_quote_label(key,quoted,value,unit,linked):
            warnings.append('原圖標籤未支持此報價欄位值，請先核對用途')
            wrong_role=True
        if typed and key=='coating.color' and not (
            matches_quote_label(key,quoted,value,unit,linked) or engineering_label_value(display,key)
        ):
            warnings.append('請核對是否為塗膠顏色')
            wrong_role=True
        result.append({'id':r['id'],'candidate_id':r['id'],'value':value,'unit':unit,'raw_text':raw,'display_text':display,
            'evidence_ids':[e['id'] for e in linked],'score':score+(2*similarity if similarity>=.72 else 0),
            'learned_from':learned['document_id'] if similarity>=.72 else None,'issues':warnings,
            'label':r['label'],'pages':sorted({e['page'] for e in linked}),
            'prefill_allowed':typed and r['state'] in ('present','conflict') and (not changed or bool(corrected_value)) and not wrong_role})
    result.extend(dict(row,prefill_allowed=row['prefill_allowed'] and not wrong_role_regions.intersection(row['evidence_ids']))
                  for row in quoted)
    for region in region_map.values():
        raw=region['text']; display=annotation_text(doc,region)
        value=engineering_label_value(display,key)
        note=engineering_note_value(display,key) if not value else ''
        value=value or note
        if not value and not all_readings:
            continue
        unit=''; text=value or display.strip()
        if key=='quotation.quantity': text,unit=quantity_parts(text,unit)
        warning=candidate_warnings(key,text,unit,display)
        if raw!=display: warning.append('已依原圖修正標註')
        if note: warning.append('依原文推定欄位，請核對用途')
        score=(9 if raw!=display else 4 if note else 5) if value else 0
        result.append({'id':region['id'],'candidate_id':None,'value':text,'unit':unit,'raw_text':raw,'display_text':display,
            'evidence_ids':[region['id']],'score':score,'learned_from':None,'issues':warning,
            'label':LABELS[key],'pages':[region['page']],'prefill_allowed':bool(value) and region['id'] not in wrong_role_regions})
    # Deduplicate only identical readings of the same source area. Other values
    # and repeated occurrences remain alternatives.
    unique={}
    for row in sorted(result,key=lambda x:(-x['score'],not x['prefill_allowed'])):
        identity=(canonical(row['value']),row['unit'],tuple(row['evidence_ids']))
        previous=unique.get(identity)
        if previous is None or (row['prefill_allowed'] and not previous['prefill_allowed']):
            unique[identity]=row
    return sorted(unique.values(),key=lambda row:-row['score'])


def quantity_parts(value,unit):
    match=re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*([A-Za-z\u3400-\u9fff]+(?:[/／][A-Za-z\u3400-\u9fff]+)?)?',value.strip())
    return (match[1],(match[2] or unit).strip()) if match else (value,unit)

def coverage(doc):
    readings=[e for e in doc['regions'] if e.get('kind')=='evidence']
    incomplete=[p['number'] for p in doc['pages'] if p.get('recognition_status') not in ('complete',) and p.get('processing')!='complete']
    return {'pages':len(doc['pages']),'readings':len(readings),'characters':sum(len(e['text']) for e in readings),
            'incomplete_pages':incomplete,'all_drawing_information_verified':False,
            'note':'列出全部已取得原文；模型仍可能漏字、漏符號，完整性須對照原圖。'}


def confirm_feature(doc,row):
    from .quotation_layout import QUOTE_FIELDS

    key=row['key']; label=LABELS.get(key)
    if label is None: raise ValueError('未知耐落特徵')
    state=row.get('state','present')
    value=row.get('value','').strip(); unit=row.get('unit','').strip(); reason=row.get('reason','').strip()
    if state not in ('present','missing','not_applicable'):
        raise ValueError(f'{label}：請先處理衝突或無法辨識，再確認')
    if state=='present' and not value:
        raise ValueError(f'{label}：請輸入確認內容')
    if state!='present' and not reason:
        raise ValueError(f'{label}：未提供／不適用請填寫原因')

    candidate_id=row.get('candidate_id')
    ids=list(dict.fromkeys(candidate_id if isinstance(candidate_id,(list,tuple)) else [candidate_id] if candidate_id else []))
    regions={region['id']:region for region in doc['regions'] if is_source_region(region)}
    # Older callers can use a raw option's stable ID from alternatives().
    raw_ids={identifier for identifier in ids if identifier in regions}
    ids=[identifier for identifier in ids if identifier not in raw_ids]
    available={r['id']:r for r in doc['requirements'] if not r['deleted'] and r.get('origin')!='nailok_human'}
    if any(identifier not in available for identifier in ids):
        raise ValueError(f'{label}：原文備選已變更，請重新載入')
    sources=[available[identifier] for identifier in ids]
    explicit_ids=set(row.get('evidence_ids') or [])|raw_ids
    if not explicit_ids<=regions.keys():
        raise ValueError(f'{label}：原圖標註已變更，請重新選擇')
    linked_ids=explicit_ids or {eid for source in sources for eid in source['evidence_ids'] if eid in regions}
    linked=[region for eid,region in regions.items() if eid in linked_ids]
    raw='\n'.join(e['text'] for e in linked)
    review_text='\n'.join(annotation_text(doc,e) for e in linked)
    if state=='present' and not linked:
        raise ValueError(f'{label}：請選擇原圖標註或框選補入來源')
    if state=='present' and key=='quotation.quantity' and annual_quantity(review_text,value,unit):
        raise ValueError('年度／計畫量不能直接確認為本次數量，請選擇單批來源或標示圖面未提供')
    if (state=='present' and key in ('nailok.dimensions','nailok.coverage') and re.search(r'\d',value)
            and not unit and not re.search(r'mm|cm|inch|[°″]|牙|齒|圈|\d\s*度|%|μm|µm|毫米|公分|英吋',value,re.I)):
        raise ValueError(f'{label}：請明確填寫單位，或在內容中保留牙數等單位')

    old_id=doc.get('nailok_mapping',{}).get(key)
    old=next((r for r in doc['requirements'] if r['id']==old_id),None)
    if old and old.get('origin')=='nailok_human': old['deleted']=True
    fingerprints={source['id']:source_fingerprint(doc,source['id']) for source in sources}
    corrected=bool(state=='present' and sources and (
        value!='\n'.join(dict.fromkeys(candidate_value(source) for source in sources)) or
        any(unit!=source['unit'] for source in sources)))
    r=requirement(key,value if state=='present' else '',label=label,unit=unit if state=='present' else '',
        evidence_ids=[e['id'] for e in linked],operator='text',origin='nailok_human',scope='nailok:'+key,state=state,
        nailok_source_id=ids[0] if ids else None,nailok_source_ids=ids,nailok_source_fingerprints=fingerprints,
        nailok_source_fingerprint=fingerprints[ids[0]] if ids else None,
        nailok_evidence_fingerprints={e['id']:digest(source_region_snapshot(doc,e)) for e in linked},
        nailok_raw_text=raw,nailok_review_text=review_text,nailok_review_reason=reason,nailok_corrected=corrected)
    if row.get('nailok_label_pending'):
        r['nailok_label_pending']=True
    doc['requirements'].append(r)
    doc.setdefault('nailok_mapping',{})[key]=r['id']
    if key in QUOTE_FIELDS: doc.setdefault('quotation_mapping',{})[key]=r['id']
    return {'field':key,'requirement_id':r['id'],'source_requirement_id':r['nailok_source_id'],
        'source_requirement_ids':ids,'evidence_ids':r['evidence_ids'],'previous_selection':old_id,
        'corrected':corrected,'state':state,'raw_text':raw,'review_text':review_text,'value':r['value'],'unit':r['unit']}


def _label_draft(row):
    """Values and source links that a user can see and accept in the label."""
    return {
        'key':row['key'], 'value':row.get('value','').strip(), 'unit':row.get('unit','').strip(),
        'state':row.get('state','present'), 'candidate_id':row.get('candidate_id'),
        'evidence_ids':list(dict.fromkeys(row.get('evidence_ids') or [])),
        'reason':row.get('reason','').strip(),
    }


def _label_region_hashes(doc, ids):
    return {region['id']:digest(source_region_snapshot(doc,region)) for region in doc['regions']
            if region['id'] in ids and is_source_region(region)}


def stage_label_draft(doc, row):
    """Persist a selected crop for the current label, within an existing mutation."""
    draft=_label_draft(row)
    if draft['key'] not in FIELDS or draft['state']!='present' or not draft['value']:
        raise ValueError('框選結果缺少有效的報價欄位或文字')
    ids=set(draft['evidence_ids'])
    linked=_label_region_hashes(doc,ids)
    if not ids or linked.keys()!=ids:
        raise ValueError('框選原圖來源已變更，請重新選擇')
    doc.setdefault('nailok_label_drafts',{})[draft['key']]={
        **draft,'evidence_fingerprints':linked,'source_sha256':doc['source_sha256'],
    }


def _current_label_draft(doc, key):
    draft=doc.get('nailok_label_drafts',{}).get(key)
    if draft and draft.get('source_sha256')==doc['source_sha256'] and (
        draft['evidence_fingerprints']==_label_region_hashes(doc,set(draft['evidence_ids']))
    ):
        candidate=draft['candidate_id']
        if candidate and not any(r['id']==candidate and not r['deleted'] for r in doc['requirements']):
            # Explicit measured boxes still support the reviewed value even if a
            # machine candidate was consolidated by a later recognition pass.
            return {**draft,'candidate_id':None}
        return draft
    return None


def _label_snapshot_row(doc, key, state, value='', unit='', evidence_ids=None, requirement_id=None):
    ids=list(evidence_ids or [])
    return {'key':key,'label':LABELS[key],'state':state,'value':value,'unit':unit,
            'evidence_ids':ids,'requirement_id':requirement_id,
            'evidence_fingerprints':_label_region_hashes(doc,set(ids))}


def _final_drawing_field(field):
    result={key:clone(field[key]) for key in ('key','label','state','value','unit','evidence_ids','requirement_id')}
    result['provenance']='drawing' if field['evidence_ids'] else 'unresolved'
    return result


def _final_input_values(field, entry):
    """Check what goes on the label without creating drawing evidence."""
    from .quotation_validation import value_issue

    key=field['key']
    label=field['label']
    value=entry['value'].strip()
    unit=entry.get('unit',field['unit']).strip()
    if (value,unit)==(field['value'],field['unit']):
        return value,unit
    normalized=unicodedata.normalize('NFKC',value)
    if key=='quotation.price' and PRICE_FORMAT.fullmatch(normalized):
        printed_unit='/'.join(part.strip() for part in normalized.split('/') if part.strip())
        if unit and canonical(unit)!=canonical(field['unit']) and canonical(unit)!=canonical(printed_unit):
            raise ValueError(f'{label}：內容與計價單位不同，請只在單位欄填寫幣別／分母')
        return '',printed_unit
    if value and key in ('quotation.price','quotation.quantity'):
        amount=re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*((?:/\s*)?[^\d\s.,+\-].*))?',normalized)
        if not amount or amount[2] and not re.fullmatch(r'(?:/\s*)?[A-Za-z\u3400-\u9fff]+(?:\s*/\s*[A-Za-z\u3400-\u9fff]+)?',amount[2]):
            raise ValueError(f'{label}：請輸入非負數字，單位可填在單位欄')
        if amount[2]:
            printed_unit='/'.join(part.strip() for part in amount[2].split('/') if part.strip())
            if unit and canonical(unit)!=canonical(printed_unit):
                raise ValueError(f'{label}：內容與單位欄的單位不同')
            unit=printed_unit
        value=amount[1]
    if value and key in ('quotation.date','quotation.valid_until'):
        parts=re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?',normalized)
        try:
            if not parts:
                raise ValueError
            date(*map(int,parts.groups()))
        except ValueError:
            raise ValueError(f'{label}：請輸入有效的日期（例如 2026/11/19）') from None
    issue=value_issue(key,value) if value else ''
    if issue:
        raise ValueError(f'{label}：{issue}')
    return value,unit


def _final_label_fields(fields, final_inputs):
    final=[]
    for field in fields:
        key=field['key']
        row=_final_drawing_field(field)
        if key in final_inputs:
            value,unit=_final_input_values(field,final_inputs[key])
            if (value,unit)!=(field['value'],field['unit']):
                row.update(value=value,unit=unit,state='present' if value else 'pending',
                           evidence_ids=[],requirement_id=None,provenance='operator_input')
        final.append(row)
    return final


def _retained_final_inputs(fields, approval):
    if approval['state']!='approved':
        return {}
    baselines={field['key']:field for field in approval['fields']}
    current={field['key']:field for field in fields}
    return {field['key']:field for field in approval['final_fields']
            if field['provenance']=='operator_input' and baselines[field['key']]==current[field['key']]}


def _label_unresolved(fields):
    return [field['label'] for field in fields if field['state'] in ('pending','missing')]


def _label_input_fingerprint(doc):
    """Distinguish new recognition/edits from catalogue-only bookkeeping."""
    mappings=doc.get('nailok_mapping',{})
    quotation=doc.get('quotation_mapping',{})
    return digest({
        'source':doc['source_sha256'],
        'regions':_label_region_hashes(doc,{region['id'] for region in doc['regions']}),
        'recognition':[(run.get('id'),run.get('status')) for run in doc.get('tool_runs',[])],
        'pages':[(page['number'],page.get('recognition_status'),page.get('processing')) for page in doc['pages']],
        'selections':{key:mappings.get(key,quotation.get(key)) for key in FIELDS},
        'drafts':doc.get('nailok_label_drafts',{}),
    })


def _verify_label_changes(doc, results):
    from .store import _verify_rows
    if not results:
        return
    changed={entry['requirement_id'] for entry in results}
    replaced={entry['previous_selection'] for entry in results if entry['previous_selection']}
    invalidate(doc,changed|replaced)
    selected={entry['requirement_id'] for entry in results if entry['state']!='missing'}
    if selected:
        _verify_rows(doc,selected)


class NailokService:
    def __init__(self,store):self.store=store
    def examples(self,doc):
        result=[]
        for other in self.store.list_documents():
            if other['source_sha256']==doc['source_sha256'] or other.get('tenant')!=doc.get('tenant') or other.get('profile')!=doc.get('profile'):continue
            for row in features(other):
                r=row['source']
                if not row['confirmed'] or r['state']!='present':continue
                raw=(r.get('nailok_review_text') or r.get('nailok_raw_text') or
                     '\n'.join(annotation_text(other,e) for e in source_regions(other,r)))
                result.append({'field':row['key'],'raw_text':raw,'document_id':other['id'],'requirement_id':r['id']})
        return result

    def prefill(self,doc,*,include_legacy=False):
        from .quotation_validation import value_issue
        examples=self.examples(doc)
        quoted=labeled_choices(doc)
        approval=self.label_approval(doc)
        approved_blanks={field['key']:field for field in approval['fields']
                         if approval['state']=='approved' and field['state']=='pending'}
        answer=[]
        for row in features(doc,include_legacy=include_legacy):
            key=row['key']; source=row['source']
            row.update(candidate_id=None,evidence_ids=[],unit='',issues=[],state='present')
            blank=approved_blanks.get(key)
            if blank:
                if blank['unit'] and key=='quotation.price':
                    row.update(value='',unit=blank['unit'],evidence_ids=blank['evidence_ids'],
                               status='待補金額',display_value=price_unit_display(blank['unit']))
                else:
                    row.update(value='',status='待補資料',display_value='待補資料')
                answer.append(row)
                continue
            draft=_current_label_draft(doc,key)
            if draft:
                row.update({field:clone(draft[field]) for field in ('candidate_id','evidence_ids','value','unit','state')})
                row.update(confirmed=False,status='框選待核准',
                           display_value=(draft['value']+' '+draft['unit']).strip())
                answer.append(row)
                continue
            if source and selection_current(doc,source):
                linked=source_regions(doc,source)
                state=source['state']
                if source.get('nailok_label_pending') and state=='missing':
                    unit_only=next((option for option in quoted[key] if option['blank_price']),None)
                    row.update(value='',status='待補資料',display_value='待補資料')
                    if unit_only:
                        row.update(unit=unit_only['unit'],evidence_ids=unit_only['evidence_ids'],
                                   status='待補金額',display_value=price_unit_display(unit_only['unit']))
                    answer.append(row)
                    continue
                raw='\n'.join(annotation_text(doc,e) for e in linked)
                annual=key=='quotation.quantity' and state=='present' and annual_quantity(raw,source['value'],source['unit'])
                role_supported=key not in LABEL_BOUND or matches_quote_label(key,quoted[key],source['value'],source['unit'],linked)
                usable=state=='present' and linked and source['value'] and not PRICE_FORMAT.fullmatch(source['value'])
                if row['confirmed'] or state=='missing' or (usable and not annual and role_supported):
                    ids=source.get('nailok_source_ids') or source.get('nailok_source_id')
                    if source.get('origin')!='nailok_human': ids=source['id']
                    if isinstance(ids,list) and len(ids)==1: ids=ids[0]
                    row.update(candidate_id=ids,evidence_ids=[e['id'] for e in linked],unit=source['unit'],state=state,
                               display_value=(source['value']+' '+source['unit']).strip() if source['value'] else row['status'])
                    answer.append(row)
                    continue
            options=alternatives(doc,key,examples,quoted=quoted)
            grounded=[option for option in options if option['prefill_allowed'] and option['value'].strip()
                         and not any('不能直接當單批量' in issue for issue in option['issues'])
                         and not PRICE_FORMAT.fullmatch(option['value'])
                         and not value_issue(key,option['value'])]
            if grounded:
                chosen=grounded[0]
                for field in ('candidate_id','value','unit','evidence_ids','issues'):
                    row[field]=chosen[field]
                if len({(canonical(option['value']),canonical(option['unit'])) for option in grounded})>1:
                    row['issues']=[*row['issues'],'有多個備選值，請對照確認']
                row['display_value']=(row['value']+' '+row['unit']).strip()
                row['status']='待確認' if not row['issues'] else '請核對建議'
            else:
                row.update(value='',display_value='待補資料',status='待補資料')
                unit_only=next((option for option in quoted[key] if option['blank_price']),None)
                if unit_only:
                    row.update(display_value=price_unit_display(unit_only['unit']),status='待補金額',unit=unit_only['unit'],
                               evidence_ids=unit_only['evidence_ids'],issues=unit_only['issues'])
            answer.append(row)
        return answer

    def correct_annotation(self,doc_id,version,evidence_id,text):
        text=text.strip()
        if not text: raise ValueError('請輸入對照原圖後的完整標註')
        def change(doc):
            region=next((e for e in doc['regions'] if e['id']==evidence_id and is_source_region(e)),None)
            if region is None: raise ValueError('請選擇可對照原圖的標註框')
            before=annotation_text(doc,region)
            if text==before: return {}
            corrections=doc.setdefault('nailok_annotation_corrections',{})
            if text==region['text']:
                corrections.pop(evidence_id,None)
            else:
                corrections[evidence_id]={'text':text,'source_fingerprint':region_fingerprint(region)}
            affected={r['id'] for r in doc['requirements'] if not r['deleted'] and evidence_id in r['evidence_ids']}
            invalidate(doc,affected)
            return {'region_id':evidence_id,'page':region['page'],'box':region['box'],'original_text':region['text'],
                    'before':before,'after':text,'affected_requirement_ids':sorted(affected)}
        return self.store.mutate(doc_id,version,'nailok_annotation','對照原圖修正個別標註',change)

    def confirm(self,doc_id,version,key,candidate_id,value,unit='',*,state='present',reason='',human_checked=False):
        row={'key':key,'candidate_id':candidate_id,'value':value,'unit':unit,'state':state,'reason':reason}
        return self._confirm_rows(doc_id,version,[row],human_checked=human_checked,action='nailok_confirm')

    def confirm_many(self,doc_id,version,rows,*,human_checked=False):
        return self._confirm_rows(doc_id,version,list(rows),human_checked=human_checked,action='nailok_confirm_batch')

    def _confirm_rows(self,doc_id,version,rows,*,human_checked,action):
        if not human_checked: raise ValueError('請先對照原圖確認')
        if not rows: raise ValueError('請選擇已對照原圖的欄位')
        keys=[row['key'] for row in rows]
        if len(set(keys))!=len(keys): raise ValueError('同一欄位不可重複確認')

        def change(doc):
            results=[confirm_feature(doc,row) for row in rows]
            _verify_label_changes(doc,results)
            if action=='nailok_confirm': return results[0]
            return {'fields':results,'requirement_ids':sorted(entry['requirement_id'] for entry in results),
                    'attestation':'已對照原圖核對所選耐落報價欄位'}

        reason=rows[0].get('reason','').strip() if action=='nailok_confirm' else ''
        return self.store.mutate(doc_id,version,action,reason or '對照原圖確認耐落報價特徵',change)

    def label_approval(self,doc):
        saved=doc.get('nailok_label_approval')
        if not saved:
            return {'state':'pending','fields':[],'unresolved_fields':[],
                    'final_fields':[],'final_unresolved_fields':[]}
        current=(saved['source_sha256']==doc['source_sha256']
                 and saved.get('input_fingerprint')==_label_input_fingerprint(doc))
        final_fields=saved.get('final_fields')
        if final_fields is None:
            final_fields=_final_label_fields(saved['fields'],{})
        elif saved.get('final_fields_sha256')!=digest(final_fields):
            current=False
        if current:
            rows={row['key']:row for row in features(doc,include_legacy=False)}
            for field in saved['fields']:
                links_valid=field['evidence_fingerprints']==_label_region_hashes(doc,set(field['evidence_ids']))
                if not links_valid:
                    current=False
                    break
                if field['state'] in ('present','not_applicable'):
                    row=rows[field['key']]
                    source=row['source']
                    if (not row['confirmed'] or source['id']!=field['requirement_id']
                            or any(source[name]!=field[name] for name in ('state','value','unit'))):
                        current=False
                        break
        return {**clone(saved),'state':'approved' if current else 'stale','final_fields':clone(final_fields),
                'final_unresolved_fields':_label_unresolved(final_fields)}

    def approve_label(self,doc_id,version,edits=(),*,human_checked=False,final_inputs=()):
        """Approve the drawing fields and the clerk's printable label in one version."""
        if not human_checked:
            raise ValueError('核准前請先對照原圖及目前標籤內容')
        submitted={}
        for row in edits:
            key=row['key']
            if key not in FIELDS or key in submitted:
                raise ValueError('報價標籤欄位無效或重複')
            submitted[key]=_label_draft(row)
        clerk_inputs={}
        for entry in final_inputs:
            key=entry.get('key')
            if key not in FIELDS or key in clerk_inputs or not isinstance(entry.get('value'),str) or (
                'unit' in entry and not isinstance(entry['unit'],str)
            ):
                raise ValueError('正式報價標籤欄位無效或重複')
            clerk_inputs[key]=entry

        def change(doc):
            previous=self.label_approval(doc)
            if not submitted and not clerk_inputs and previous['state']=='approved':
                return {}
            fields=[]
            results=[]
            for row in self.prefill(doc):
                key=row['key']
                draft=submitted.get(key) or _label_draft(row)
                source=row['source']
                state=draft['state']
                if state=='present' and not draft['value']:
                    # Untouched blanks remain unresolved. If an operator clears
                    # a visible value, save the omission as a versioned choice.
                    if row['value'].strip():
                        result=confirm_feature(doc,{'key':key,'state':'missing','reason':'保留空白待補',
                                                    'nailok_label_pending':True})
                        results.append(result)
                    if key=='quotation.price' and not row['value'] and row['unit'] and row['evidence_ids']:
                        fields.append(_label_snapshot_row(doc,key,'pending',unit=row['unit'],
                                                          evidence_ids=row['evidence_ids']))
                    else:
                        fields.append(_label_snapshot_row(doc,key,'pending'))
                    continue
                if state!='present' and not draft['reason']:
                    draft={**draft,'reason':'人工標記不適用' if state=='not_applicable' else '人工標記圖面未提供'}
                if (source and (row['confirmed'] or (source['state']=='missing' and state=='missing'))
                        and draft['state']==source['state'] and draft['value']==source['value']
                        and draft['unit']==source['unit'] and set(draft['evidence_ids'])==set(row['evidence_ids'])
                        and draft['candidate_id']==row['candidate_id']):
                    fields.append(_label_snapshot_row(doc,key,state,source['value'],source['unit'],
                                                      row['evidence_ids'],source['id']))
                    continue
                result=confirm_feature(doc,draft)
                results.append(result)
                fields.append(_label_snapshot_row(doc,key,result['state'],result['value'],result['unit'],
                                                  result['evidence_ids'],result['requirement_id']))
            if not any(field['state']=='present' for field in fields):
                raise ValueError('請先辨識或框選至少一個有原圖依據的報價欄位')
            final_fields=_final_label_fields(fields,{**_retained_final_inputs(fields,previous),**clerk_inputs})
            _verify_label_changes(doc,results)
            doc.pop('nailok_label_drafts',None)
            approval={'approved_version':doc['version']+1,'source_sha256':doc['source_sha256'],
                      'input_fingerprint':_label_input_fingerprint(doc),
                      'approved_at':now(),'actor':self.store.actor(),'fields':fields,
                      'unresolved_fields':_label_unresolved(fields),
                      'final_fields':final_fields,'final_fields_sha256':digest(final_fields),
                      'final_unresolved_fields':_label_unresolved(final_fields)}
            doc['nailok_label_approval']=approval
            return {'approval':approval,'requirement_ids':sorted(result['requirement_id'] for result in results),
                    'attestation':'已核對原圖欄位與承辦輸入，核准報價標籤'}

        return self.store.mutate(doc_id,version,'nailok_label_approve','對照原圖核准報價標籤',change)

    def report(self,doc):
        rows=features(doc)
        visible=[r for r in rows if r['key'] in FIELDS]
        engineering=[r for r in rows if r['key'] not in FIELDS]
        confirmed=[r for r in rows if r['confirmed'] and r['source']['state']=='present']
        def confirmed_payload(r):
            return {'key':r['key'],'label':r['label'],'value':r['value'],'unit':r['source']['unit'],
                    'requirement_id':r['source']['id'],'evidence_ids':r['source']['evidence_ids']}
        def field_payload(r):
            return {key:r[key] for key in ('key','label','status','value')}
        price_format=next((entry for entry in labeled_choices(doc)['quotation.price'] if entry['blank_price']),None)
        return {'schema':'drawiq-nailok-review/0.11.0','created_at':now(),'document_id':doc['id'],
            'document_version':doc['version'],'source_sha256':doc['source_sha256'],'coverage':coverage(doc),
            'label_approval':self.label_approval(doc),
            'status':'internal_draft','missing_required':[r['label'] for r in visible if r['required'] and
                (not r['confirmed'] or r['source']['state']!='present')],
            'confirmed_features':[confirmed_payload(r) for r in confirmed if r['key'] in FIELDS],
            'features':[field_payload(r) for r in visible],
            'confirmed_engineering_features':[confirmed_payload(r) for r in confirmed if r['key'] not in FIELDS],
            'engineering_features':[field_payload(r) for r in engineering],
            'quotation_price_format':{'unit':price_format['unit'],'evidence_ids':price_format['evidence_ids']} if price_format else None,
            'learning':{'valid_examples':len(self.examples(doc)),'method':'verified context candidate ranking',
                        'neural_weights_retrained':False},
            'corrections':[{'feature':r['key'],'raw_text':r['source'].get('nailok_raw_text',''),
                'corrected_value':r['value'],'unit':r['source']['unit'],
                'source_requirement_id':r['source'].get('nailok_source_id'),
                'source_requirement_ids':r['source'].get('nailok_source_ids',[]),
                'human_requirement_id':r['source']['id'],'evidence_ids':r['source']['evidence_ids']}
                for r in confirmed if r['source'].get('nailok_corrected')],
            'annotation_corrections':[{'evidence_id':e['id'],'page':e['page'],'box':clone(e['box']),
                'original_text':e['text'],'corrected_text':annotation_text(doc,e)}
                for e in doc['regions'] if annotation_text(doc,e)!=e['text']],
            'readings':clone(doc['regions']),'source_file':doc['source_copy']}
