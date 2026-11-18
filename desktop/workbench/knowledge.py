"""Source-bound engineering summaries and assertion graphs, without invented facts."""
import html
import re
from collections import Counter
from .domain import canonical,digest,checks,STATE_LABELS,STATUS_LABELS
from .quotation import QUOTATION_KEYS

CATEGORIES=['基本資料','材料與表面處理','尺寸、公差與符號','製程與加工要求','檢驗與品質文件','數量、交期與商務','其他資訊']
KNOWLEDGE_VERSION='0.4.3'
ENGINEERING_LABELS={'thread.specification':'螺紋規格','coating.minimum_thread_count':'最少塗膠牙數','coating.maximum_uncoated_full_threads':'未塗膠完整牙數上限',
    'temperature.capability':'工作溫度能力','adhesive.selection':'膠種選擇','demand.annual':'年度需求','document.first_batch':'首批應附文件','document.every_batch':'每批應附文件'}

def category(key,label='',text=''):
    s=(key+' '+label).casefold()
    if key in ('quotation.number','quotation.project','quotation.salesperson','customer.name'): return CATEGORIES[0]
    if key=='product.specification': return CATEGORIES[2]
    if key=='document_type' or label.strip()=='委外加工詢價單': return CATEGORIES[0]
    if any(x in s for x in ('quantity','demand','price','date','delivery','setup_time','數量','需求量','單價','交期','詢價日期','回傳日期','備料','運費')): return CATEGORIES[5]
    if any(x in s for x in ('required_document','document.first','document.every','standard','檢驗','檢查','規範','證明','証明','報告','初期請檢附','quality','inspection','測試')): return CATEGORIES[4]
    if any(x in s for x in ('temperature','coating','adhesive','process','製程','加工','塗膠','膠種','溫度','heat','熱處理')): return CATEGORIES[3]
    if any(x in s for x in ('dimension','thread','螺紋','尺寸','公差','孔','直徑','半徑','符號','角度','tolerance')): return CATEGORIES[2]
    if any(x in s for x in ('material','finish','材料','表面','鍍','粗糙','surface')): return CATEGORIES[1]
    if any(x in s for x in ('part','drawing','document','company','supplier','revision','scale','unit','外包件號','料號','品名','圖號','公司','客戶','此致','版','比例','表格編號','委外','經辦')): return CATEGORIES[0]
    return CATEGORIES[6]

def fact_text(f):
    value=f['value']
    if value.startswith('{'):
        import json
        try:
            parts=json.loads(value)
            if parts.get('system')=='metric': value='M'+parts['diameter']+'×'+parts['pitch']+'-'+parts['tolerance_class']
        except (ValueError,KeyError,TypeError): pass
    if f['state'] not in ('present','conflict') and not value: value=STATE_LABELS.get(f['state'],f['state'])
    op={'ge':'≥ ','le':'≤ '}.get(f['operator'],'')
    unit=(' '+f['unit']) if f['unit'] and not canonical(value).endswith(canonical(f['unit'])) else ''
    return f"{f['label']}：{op}{value}{unit}"

def facts_for_document(doc):
    regions={e['id']:e for e in doc['regions']}; facts=[]; issues=[]
    from .quotation import extract_quotation
    requirements=list(doc['requirements']); virtual_ids=set()
    if not any(r['key'].startswith('quotation.') and not r.get('deleted') for r in requirements):
        for r in extract_quotation(doc['regions']):
            r['id']='virtual_quote_'+digest([doc['id'],r['key'],r['scope'],r['evidence_ids'],r['value']])[:20]
            virtual_ids.add(r['id']); requirements.append(r)
    for r in requirements:
        if r.get('deleted'): continue
        texts=' '.join(regions[x]['text'] for x in r['evidence_ids'] if x in regions)
        flags=list(r.get('extraction_notes',[])) if r['status']!='verified' else []
        if r['key']=='scale' and re.search(r'\d{1,2}:\d{2}',r['value']) and not re.search(r'SCALE|比例|縮尺',texts,re.I): flags.append('時間可能被誤當成比例')
        if r['key'].startswith('dimension.') and re.search(r'溫|温|℃|°\s*C|°\s*℃',texts,re.I): flags.append('溫度可能被誤當成幾何尺寸或角度')
        if r['value'].strip() in ('天','日','元/支(不含稅)','元／支（不含稅）','意見'): flags.append('候選只有單位或欄位標題，尚無可用數值')
        if r['key'].startswith('table.') and re.fullmatch(r'\d+[.、]?',r['value'].strip()) and re.search(r'[1-9]\.',r['label']): flags.append('表格序號可能被誤當成內容')
        if r['state'] in ('conflict','missing','blank','unreadable','pending_decision'): flags.append(STATE_LABELS[r['state']])
        if not r['evidence_ids']: flags.append('尚未連結證據')
        if not r['value'].strip() and r['state']=='present': flags.append('有值狀態卻沒有內容')
        if flags: issues.append({'requirement_id':r['id'],'label':r['label'],'value':r['value'],'messages':flags})
        f={k:r.get(k,'') for k in ('key','label','value','unit','operator','subject','scope','conditions','state')}
        if f['label']=='dimension': f['label']='尺寸候選'
        f.update(id='fact_'+digest([doc['id'],r['id']])[:20],requirement_id=r['id'],evidence_ids=list(r['evidence_ids']),feature_ids=list(r['feature_ids']),
            category=category(r['key'],r['label']),origin='requirement',status=r['status'],quality_flags=flags,
            summary_visible=not any('可能被誤' in x or '只有單位' in x for x in flags),raw_text=texts,critical=r['critical'])
        f['raw_value']=r.get('raw_value',r['value'])
        if r['id'] in virtual_ids: f['origin']='quotation_candidate'; f['requirement_id']=None
        # Present the annual scope explicitly without overwriting the source requirement.
        if r['key'] in ('quantity','demand.quantity') and re.search(r'/年|每年|年度|annual|/year',texts+' '+r['value'],re.I): f['label']='年度需求（非本次訂購量）'; f['scope']='annual'
        facts.append(f)

    def add(e,key,value,label=None,unit='',operator='text',scope='',state='present',conditions=''):
        facts.append({'id':'fact_'+digest([doc['id'],e['id'],key,value,scope])[:20],'key':key,'label':label or ENGINEERING_LABELS.get(key,key),
            'value':value,'unit':unit,'operator':operator,'subject':'document','scope':scope,'conditions':conditions,'state':state,
            'requirement_id':None,'evidence_ids':[e['id']],'feature_ids':[],'category':category(key,label or ENGINEERING_LABELS.get(key,key)),
            'origin':'evidence_rule','status':'needs_review','quality_flags':['待確認'] if state!='present' else [],'summary_visible':True,'raw_text':e['text'],
            'critical':key.startswith(('coating.','temperature.','adhesive.'))})
    for e in regions.values():
        if e['kind']!='evidence': continue
        t=e['text'].translate(str.maketrans('须范纹胶种温镀', '須範紋膠種溫鍍'))
        if re.search(r'溫|温|℃|°\s*C',t,re.I):
            m=re.search(r'(\d+(?:\.\d+)?)\s*[°℃]+\s*C?',t,re.I)
            if m: add(e,'temperature.capability',m[1],unit='°C',operator='eq',conditions='依原文核對為最高工作溫度或耐溫能力')
        m=re.search(r'(?<!未)塗膠長?\s*[:：]?\s*(\d+)\s*牙\s*(以上|至少)',t)
        if m: add(e,'coating.minimum_thread_count',m[1],unit='牙',operator='ge')
        m=re.search(r'未塗膠長?\s*[:：]?\s*(\d+)\s*全?牙\s*(以下|至多)',t)
        if m: add(e,'coating.maximum_uncoated_full_threads',m[1],unit='完整牙',operator='le')
        if re.search(r'請建議.*膠種|膠種.*請建議',t): add(e,'adhesive.selection','',state='pending_decision')
        if re.search(r'(PCS\s*/\s*年|每年|年度|annual)',t,re.I):
            m=re.search(r'(\d[\d,.]*\s*萬?\s*(?:PCS|EA)\s*/\s*年)',t,re.I)
            if m: add(e,'demand.annual',m[1],label='年度需求（非本次訂購量）',scope='annual')
        for marker,key in [('首批','document.first_batch'),('每批','document.every_batch')]:
            m=re.search(marker+r'(?:需)?(?:檢)?附\s*([^,，;；]+)',t)
            if m: add(e,key,m[1],scope=marker)
        if re.search(r'須符合規範|需符合規範|依據規範',t):
            m=re.search(r'(?:ISO|DIN|ASME|ASTM|JIS|CNS|DX)\s*[A-Z0-9][A-Z0-9.() /-]*',t,re.I)
            if m: add(e,'standard.reference',m[0].strip(),label='規範原文（關係待確認）' if '(' in m[0] else '規範原文')
    return facts,issues

def summary(doc):
    facts,issues=facts_for_document(doc); sections={name:[] for name in CATEGORIES}; seen=set()
    # Refined semantic statements precede legacy parser candidates.
    for f in sorted(facts,key=lambda f:f['origin']!='evidence_rule'):
        if not f['summary_visible']: continue
        key=(f['category'],canonical(fact_text(f)),f['scope'])
        if key in seen: continue
        seen.add(key); sections[f['category']].append(f)
    phrases=[]
    kind=next((f['value'] for f in facts if f['key']=='document_type' and f['summary_visible']),'')
    part=next((f['value'] for f in facts if re.search(r'外包件號|料號|零件號',f['label']) and f['summary_visible']),'')
    if kind or part: phrases.append(('目前辨識為「'+kind+'」' if kind else '這張圖')+('，零件／外包件號候選為「'+part+'」' if part else '')+'。')
    for name,prefix in [(CATEGORIES[1],'材料與表面資訊包含'),(CATEGORIES[2],'可讀到的尺寸與符號包含'),(CATEGORIES[3],'加工需求包含'),(CATEGORIES[4],'品質與文件要求包含'),(CATEGORIES[5],'商務條件包含')]:
        chosen=[]; values=set()
        ordered=sections[name]
        if name==CATEGORIES[3]: ordered=sorted(ordered,key=lambda f:0 if '製程名稱' in f['label'] else 1)
        if name==CATEGORIES[2]:
            # The lead paragraph favors explicit specifications; isolated numbers remain in the detailed list.
            ordered=[f for f in ordered if f['unit'] or re.search(r'螺紋|公差|thread|tolerance',f['label']+' '+f['key'],re.I)]
            ordered=sorted(ordered,key=lambda f:-len(f['value']))
        for f in ordered:
            if canonical(f['value']) in values: continue
            if name==CATEGORIES[2] and any(canonical(f['value']) in v for v in values): continue
            values.add(canonical(f['value'])); chosen.append(f)
            if len(chosen)==3: break
        if chosen: phrases.append(prefix+'「'+'；'.join(fact_text(f) for f in chosen)+'」。')
    missing=[]
    for label,terms in [('本次詢價數量',('order_quantity','rfq.quantity','quotation.quantity')),('單價',('unit_price','price','quotation.price')),('材料',('material',))]:
        aliases={'本次詢價數量':r'^(本次)?(訂購|詢價)數量$','單價':r'^(單價|unit price)$','材料':r'^(材料|材質|material)$'}
        if not any((f['key'] in terms or re.search(aliases[label],f['label'],re.I)) and f['scope']!='annual' and f['value'] and not f['quality_flags'] for f in facts): missing.append(label+'尚未得到可用值，請由承辦人確認')
    if not facts: missing.insert(0,'尚未讀圖，請按「匯入並讀圖」或「重新讀這張圖」')
    return {'document_id':doc['id'],'document_version':doc['version'],'source_sha256':doc['source_sha256'],'name':doc['name'],
        'status':doc['status'],'summary_kind':'evidence_bound_candidate_summary','narrative':'\n'.join(phrases),
        'quotation_fields':[f for f in facts if f['key'] in QUOTATION_KEYS] if any(f['key'].startswith('quotation.') for f in facts) else [],'facts':facts,'sections':sections,'issues':issues,'missing':missing,'counts':dict(Counter(f['category'] for f in facts)),
        'notice':'初步摘要與精煉結果仍須核對原圖。不同候選、未定義單位及作用特徵不可自行視為已確認。'}

def summary_html(doc,links=True):
    s=summary(doc); esc=html.escape
    out=['<html><meta charset="utf-8"><style>body{font-family:Microsoft JhengHei,sans-serif;color:#193c49;font-size:15px;line-height:1.7}h1{font-size:24px}h2{font-size:18px;color:#0c777d}a{color:#0c777d}li{margin:8px 0}.note{background:#fff2d6;padding:12px}</style><body>',
        '<h1>'+esc(doc['name'])+'｜工程識圖摘要</h1>','<p class="note">'+esc(STATUS_LABELS.get(doc['status'],doc['status']))+' · '+esc(s['notice'])+'</p>',
        ('<p>'+esc(s['narrative']).replace('\n','<br>')+'</p>') if not s['quotation_fields'] else '']
    if s['quotation_fields']:
        out.append('<h2>報價標籤｜欄位擷取與覆核</h2><table border="1" cellspacing="0" cellpadding="7" width="100%"><tr><th>欄位</th><th>擷取值／單位</th><th>狀態</th></tr>')
        priority=['quotation.number','customer.name','material','product.specification','adhesive.product','quotation.quantity','quotation.price','quotation.currency','quotation.price_unit','quotation.date','quotation.valid_until']
        for f in sorted(s['quotation_fields'],key=lambda f:priority.index(f['key']) if f['key'] in priority else 99):
            value=(f['value'] or STATE_LABELS.get(f['state'],f['state']))+(' '+f['unit'] if f['unit'] else '')
            label=esc(f['label']); label=f'<a href="fact:{f["id"]}">{label}</a>' if links else label
            status=('已覆核' if f['status']=='verified' else '待覆核')+' · '+STATE_LABELS.get(f['state'],f['state'])
            out.append('<tr><td>'+label+'</td><td>'+esc(value)+'</td><td>'+esc(status)+'</td></tr>')
        out.append('</table><p>點欄位回查原圖。原始 OCR 候選、空白、遮蔽與人工修正分開保存；M／K 不自動換算。</p>')
    for name,facts in s['sections'].items():
        if not facts: continue
        out.append('<h2>'+name+f'（{len(facts)}）</h2><ul>')
        for f in facts:
            text=esc(fact_text(f)); text=f'<a href="fact:{f["id"]}">{text}</a>' if links else text
            out.append('<li>'+text+(' <small>〔語意精煉，待核對〕</small>' if f['origin']=='evidence_rule' else '')+'</li>')
        out.append('</ul>')
    out.append('<h2>需要先確認</h2><ul>')
    for issue in s['issues']:
        out.append('<li>'+esc(issue['label']+'：'+issue['value']+' → '+'；'.join(issue['messages']))+'</li>')
    out.extend('<li>'+esc(m)+'</li>' for m in s['missing']); out.append('</ul></body></html>')
    return ''.join(out)

def build_graph(documents,approved_only=False):
    from .entity_graph import entity_graph
    return entity_graph(documents,lambda doc:facts_for_document(doc)[0],approved_only)
