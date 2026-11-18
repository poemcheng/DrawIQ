"""Label-bound quotation extraction. OCR candidates are never human attestations."""
import re
import unicodedata
from datetime import date
from .domain import requirement

FIELDS = {
    'quotation.number': ('報價單號', '報價單號|報價單編號|報價編號|Quotation No|Quote No'),
    'quotation.project': ('專案代號', '專案代號|專案編號|Project No'),
    'quotation.salesperson': ('報價人員', '報價人員|報價人|Salesperson'),
    'quotation.date': ('報價日期', '報價日期|Quote Date'),
    'quotation.valid_until': ('報價有效日期', '報價有效日期|有效日期|Valid Until'),
    'customer.name': ('客戶', '客戶名稱|客戶|Customer'),
    'material': ('材質', '材質|材料|Material'),
    'adhesive.product': ('報價膠別', '報價膠別|膠別|膠種|報價級別|塗層級別|Adhesive'),
    'coating.color': ('顏色', '顏色|Color|Colour'),
    'coating.method': ('塗佈方式', '塗佈方式|塗布方式|塗覆方式|Coating Method'),
    'product.specification': ('規格', '規格|Specification'),
    'quotation.quantity': ('報價數量', '報價數量|數量|Quantity'),
    'quotation.price': ('報價金額／原單位', '報價|單價|Unit Price'),
    'quality.requirements': ('品質要求', '品質要求|Quality Requirements'),
    'packaging.instructions': ('包裝方式', '包裝方式|包裝要求|Packaging'),
    'quotation.notes': ('注意事項', '注意事項|備註|Notes'),
    'contact.phone': ('聯絡電話', '聯絡電話|電話|Phone'),
    'contact.fax': ('客戶傳真', '客戶傳真|傳真|Fax'),
}
EXTRA_LABELS = {'quotation.currency': '報價幣別', 'quotation.price_unit': '報價分母單位'}
QUOTATION_KEYS = set(FIELDS) | set(EXTRA_LABELS)
MULTILINE = {'quality.requirements', 'packaging.instructions', 'quotation.notes'}
CRITICAL = {'quotation.number', 'customer.name', 'material', 'product.specification', 'quotation.quantity', 'quotation.price'}
# Normalize label orthography only. Customer names and material values are not rewritten.
LABEL_MAP = str.maketrans('报单号专员价实质颜涂别数规话联络传备项纸装卢户', '報單號專員價實質顏塗別數規話聯絡傳備項紙裝戶戶')
LABEL_MAP.update(str.maketrans({'级':'級','层':'層'}))

def label_token(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).translate(LABEL_MAP)).casefold()

ALIASES = {label_token(alias): key for key, (_, aliases) in FIELDS.items() for alias in aliases.split('|')}
FIELD_ORDER = {key:index for index,key in enumerate(FIELDS)}
COMPACT_LABELS = {label_token(label) for label,_ in FIELDS.values()} | {'數量','報價'}
# Partial labels are allowed only after quotation detection, never in generic drawings.
PARTIAL = {'材': 'material', '材實': 'material', '格': 'product.specification', '量': 'quotation.quantity', '報': 'quotation.price'}

def one_edit(a, b):
    if abs(len(a)-len(b)) > 1: return False
    if len(a) == len(b): return sum(x != y for x, y in zip(a,b)) <= 1
    if len(a) > len(b): a,b = b,a
    return any(a == b[:i]+b[i+1:] for i in range(len(b)))

def match_label(text, tolerant=False):
    token = label_token(text).strip('.,。:：')
    if token in ALIASES: return ALIASES[token], False
    if not tolerant: return None, False
    if token in PARTIAL: return PARTIAL[token], True
    matches = {key for alias,key in ALIASES.items() if len(alias)>=3 and one_edit(token,alias)}
    # Two corrupted characters in long OCR labels may still leave an explicit role.
    if re.fullmatch(r'.{1,2}人[員眞翼真]', token): matches.add('quotation.salesperson')
    if re.fullmatch(r'.{1,2}[膠腾]別', token): matches.add('adhesive.product')
    return (next(iter(matches)), True) if len(matches)==1 else (None, False)

def grouped_lines(regions):
    """Reassemble split label/value boxes on the same baseline, preserving every source id."""
    lines=[]
    for e in sorted((e for e in regions if e.get('kind','evidence')=='evidence'), key=lambda e:(e.get('page',1),e['box'][1],e['box'][0])):
        b=e['box']; cy=(b[1]+b[3])/2
        # Distinct methods may reread the SAME box. They are alternatives,
        # never consecutive words of a single printed line.
        def compatible(x):
            if x['page']!=e.get('page',1) or abs(x['cy']-cy)>=.4*min(x['height'],b[3]-b[1]): return False
            return not any(min(b[2],p['box'][2])-max(b[0],p['box'][0]) >
                           .2*min(b[2]-b[0],p['box'][2]-p['box'][0]) for p in x['parts'])
        line=next((x for x in reversed(lines[-6:]) if compatible(x)),None)
        if line is None:
            line={'page':e.get('page',1),'cy':cy,'height':b[3]-b[1],'parts':[]}; lines.append(line)
        line['parts'].append(e)
    for line in lines:
        parts=sorted(line['parts'],key=lambda e:e['box'][0]); line['parts']=parts
        line['text']=' '.join(e['text'] for e in parts)
        line['ids']=[e['id'] for e in parts]
        cursor=0; spans=[]
        for part in parts:
            spans.append((cursor,cursor+len(unicodedata.normalize('NFKC',part['text'])),part['id']))
            cursor=spans[-1][1]+1
        line['spans']=spans
    return lines

def segments(text, tolerant=False, *, positions=False):
    """Find explicit colon-delimited labels; end each value at the next field label."""
    normalized=unicodedata.normalize('NFKC',text)
    found=[]; last_colon=0
    for m in re.finditer(':',normalized):
        # A fuzzy label on a multiline OCR block must not consume a unit or
        # suffix from the preceding printed line.
        label_start=max(last_colon,normalized.rfind('\n',last_colon,m.start())+1)
        prefix=normalized[label_start:m.start()]
        best=None
        # A suffix search also handles two fields printed on one row.
        for i in range(len(prefix)):
            key, fuzzy=match_label(prefix[i:],tolerant)
            if not key:
                continue
            separated=i==0 or prefix[i-1].isspace() or prefix[i-1] in '|;；'
            # Layout OCR can concatenate neighboring cells into one string.
            # Permit exact template labels in order while leaving fuzzy matches
            # on ordinary text boundaries.
            compact=not fuzzy and label_token(prefix[i:]) in COMPACT_LABELS and (
                (found and FIELD_ORDER[found[-1][2]]<FIELD_ORDER[key])
                or (key=='quotation.number' and label_token(prefix[:i]).endswith(('報價標籤','報價標签')))
            )
            if not (separated or compact):
                continue
            if best is None or not fuzzy:
                best=(label_start+i,m.end(),key,fuzzy)
            if not fuzzy:
                break
        if best: found.append(best)
        last_colon=m.end()
    if not found:
        key,fuzzy=match_label(normalized,tolerant)
        # A bare heading "報價" is not an empty price field.
        if key and key!='quotation.price': return [(key,'',fuzzy,0,len(normalized))] if positions else [(key,'',fuzzy)]
    result=[]
    for i,(start,end,key,fuzzy) in enumerate(found):
        stop=found[i+1][0] if i+1<len(found) else len(normalized)
        item=(key,normalized[end:stop].strip(),fuzzy)
        result.append((*item,start,stop) if positions else item)
    return result

def is_quotation(regions):
    keys={key for line in grouped_lines(regions) for key,_,_ in segments(line['text'])}
    # RFQs routinely contain adhesive requirements and a blank unit-price
    # heading. Those two labels do not make a drawing a quotation label.
    return 'quotation.number' in keys or len(keys & {'quotation.valid_until','quotation.salesperson','quotation.date'})>=2

def extract_quotation(regions, *, include_missing=True, detected=False,tolerant=True):
    if not detected and not is_quotation(regions): return []
    records=[]; current=None
    for line in grouped_lines(regions):
        pieces=segments(line['text'],tolerant,positions=True)
        if not pieces:
            # A damaged price label can leave just currency delimiters; no amount is inferred.
            normalized=unicodedata.normalize('NFKC',line['text'])
            if tolerant and re.fullmatch(r'\s*(?:[^:\d/]{1,3}:)?\s*/\s*(TWD|USD|EUR|CNY|JPY)\s*(?:/\s*[A-Za-z]+)?\s*',normalized,re.I):
                pieces=[('quotation.price',normalized.split(':')[-1].strip(),True,0,len(normalized))]
            elif tolerant and re.fullmatch(r'.{1,2}人[員眞翼真][\u3400-\u9fff]{2,6}',normalized):
                pieces=[('quotation.salesperson',re.split(r'人[員眞翼真]',normalized,maxsplit=1)[1],True,0,len(normalized))]
            elif current and current['key'] in MULTILINE and current['page']==line['page'] and line['cy']-current['last_y']<line['height']*2:
                current['raw_value']+='\n'+line['text']; current['raw_text']+='\n'+line['text']; current['ids']+=line['ids']; current['last_y']=line['cy']
            else: current=None
        for key,value,fuzzy,start,stop in pieces:
            ids=[eid for a,b,eid in line['spans'] if a<stop and b>start]
            current={'key':key,'raw_value':value,'raw_text':unicodedata.normalize('NFKC',line['text'])[start:stop],'ids':ids,'page':line['page'],'last_y':line['cy'],
                     'fuzzy':fuzzy,'scores':[e.get('ocr_confidence') for e in line['parts'] if e.get('ocr_confidence') is not None]}
            records.append(current)
    output=[]; seen=set()
    for rec in records:
        key=rec['key']; value=rec['raw_value']; unit=''; notes=[]; state='present' if value else 'blank'
        if not value and key in ('customer.name','contact.phone','contact.fax'):
            state='unreadable'; notes.append('OCR 只取得標籤，無法區分原欄空白、遮蔽或裁切；須原圖覆核')
        if rec['fuzzy']: notes.append('標籤有 OCR 缺字／誤字，請核對欄位')
        if rec['scores'] and min(rec['scores'])<.8: notes.append('OCR 原始分數偏低；不代表正確率')
        if value and (not re.search(r'[\w\u3400-\u9fff]',value) or re.search(r'[■█□?？]{2,}',value)):
            value=''; state='unreadable'; notes.append('原欄有遮蔽、符號或殘字，無法可靠讀取')
        if value:
            from .quotation_validation import value_issue
            issue = value_issue(key, value, rec['raw_text'], check_role=False)
            if issue:
                value=''; state='pending_decision'; notes.append(issue)
        if key in ('quotation.date','quotation.valid_until') and value:
            m=re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?',value)
            try: value=date(*map(int,m.groups())).isoformat() if m else (_ for _ in ()).throw(ValueError())
            except ValueError: value=''; state='unreadable'; notes.append('日期格式或日曆日期無效，保留原文候選')
        if key=='quotation.quantity' and value:
            m=re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*([^\d\s].*)?',value)
            if m: value=m[1].replace(',',''); unit=(m[2] or '').strip()
            else: notes.append('數量格式待核對，尚未拆分成可計算數字')
            if not unit: notes.append('原文未辨識到數量單位')
            elif unit.upper() in ('M','K'): notes.append('M／K 單位語意未定義，不換算數量')
        if key=='quotation.price':
            text=unicodedata.normalize('NFKC',rec['raw_value']); currency=re.search(r'\b(TWD|USD|EUR|CNY|JPY)\b',text,re.I)
            denominator=re.search(r'/\s*([A-Za-z\u3400-\u9fff]+)\s*$',text)
            currency_value=currency[1].upper() if currency else ''
            unit_value=denominator[1] if denominator and denominator[1].upper()!=currency_value else ''
            number=re.match(r'^\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?=/|TWD|USD|EUR|CNY|JPY|$)',text,re.I)
            value=number[1].replace(',','') if number else ''
            state='present' if number else ('blank' if not re.search(r'\d',text) else 'unreadable')
            unit='/'.join(v for v in (currency_value,unit_value) if v)
            if unit_value.upper() in ('M','K'): notes.append('報價分母單位語意未定義，不計算單件價格')
            for extra, val in [('quotation.currency',currency_value),('quotation.price_unit',unit_value)]:
                output.append(requirement(extra,val,label=EXTRA_LABELS[extra],state='present' if val else 'missing',evidence_ids=rec['ids'],
                    scope=f'page:{rec["page"]}',origin='quotation_extraction',raw_value=rec['raw_value'],extraction_notes=list(notes)))
        r=requirement(key,value,label=FIELDS[key][0],unit=unit,state=state,evidence_ids=rec['ids'],scope=f'page:{rec["page"]}',
            critical=key in CRITICAL,origin='quotation_extraction',raw_value=rec['raw_value'],extraction_notes=notes,
            label_repair=bool(rec['fuzzy']))
        r['candidates']=[{'value':rec['raw_value'],'source':'quotation-label-parser','raw_text':rec['raw_text'],'raw_scores':rec['scores']}]
        output.append(r); seen.add(key)
    if include_missing:
        # Missing fields are candidates too, and cannot become graph entities.
        for key in ('quotation.number','customer.name','material','product.specification','quotation.quantity','quotation.price'):
            if key not in seen: output.append(requirement(key,'',label=FIELDS[key][0],critical=True,origin='quotation_extraction',scope='document',state='missing'))
    return output
