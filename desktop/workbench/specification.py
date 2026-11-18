"""Version-bound specification confirmation and quote-readiness checks."""
from datetime import date
from .domain import clone, digest

PROFILES={'manufacturing':'零件製造','coating':'塗膠／表面加工','outsourcing':'委外加工','general':'其他商品／服務'}
ITEM_STATES={'pending':'待確認','confirmed':'已確認','not_applicable':'不適用（須原因）'}
COMMON=[('scope','供應／服務範圍與排除事項',()),('acceptance','驗收方法、允收標準及應附文件',('inspection','quality','certificate')),
        ('delivery','交付、包裝與標示要求',('packing','packaging','delivery'))]
TEMPLATES={
 'manufacturing':[('material','材質與牌號',('material',)),('dimensions','尺寸、公差、螺紋與基準',('dimension','thread','tolerance','product.specification')),('finish','熱處理、表面處理與粗糙度',('finish','roughness','heat'))],
 'coating':[('material','基材與前處理',('material',)),('adhesive','膠別、顏色與產品規範',('coating','adhesive','color')),('coverage','塗佈位置、範圍、牙數與公差',('coating','thread')),('performance','扭力、固化、耐溫與測試要求',('torque','temperature','curing'))],
 'outsourcing':[('supplied','客供料、數量與責任界面',('material','quantity')),('process','委外工序與禁止替代條件',('process','coating','finish')),('quality','檢驗、追溯與不合格處理',('inspection','quality'))],
 'general':[('deliverables','商品／服務交付項目',()),('performance','功能、性能與限制',()),('responsibility','雙方責任與變更條件',())]}
COMMERCIAL_LABELS={'quote_number':'報價單號','quote_date':'報價日期 YYYY-MM-DD','valid_until':'有效日期 YYYY-MM-DD',
    'payment_terms':'付款條件','delivery_terms':'交貨條件／地點','lead_time':'交期／起算基準','tax_percent':'外加稅率 %（明確填 0 或數值）',
    'price_basis_qty':'每筆標示價格的數量基數','moq':'最低訂購量（同詢價單位）','exclusions':'不含項目／其他商務條件'}

def template_items(doc,profile):
    if profile not in PROFILES: raise ValueError('未知規格範本')
    result=[]
    for key,label,prefixes in COMMON+TEMPLATES[profile]:
        rows=[r for r in doc['requirements'] if not r['deleted'] and any(r['key'].startswith(p) for p in prefixes)]
        result.append({'key':key,'label':label,'state':'pending','value':'\n'.join(dict.fromkeys((r['label']+'：'+r['value']+' '+r['unit']).strip() for r in rows)),
            'acceptance':'','reason':'','requirement_ids':[r['id'] for r in rows]})
    return result

def default_spec(doc,profile='manufacturing'):
    return {'profile':profile,'form_number':'','product_name':'','drawing_number':'','drawing_revision':'','open_issues':'',
            'items':template_items(doc,profile),'review':None,'customer_confirmation':None}

def normalize_case(case,doc):
    case=clone(case)
    if 'specification' not in case: case['specification']=default_spec(doc)
    case.setdefault('commercial',{k:('1' if k=='price_basis_qty' else '') for k in COMMERCIAL_LABELS})
    for key in COMMERCIAL_LABELS: case['commercial'].setdefault(key,'1' if key=='price_basis_qty' else '')
    return case

def specification_fingerprint(case):
    spec={k:v for k,v in case['specification'].items() if k not in ('review','customer_confirmation')}
    return digest({'document_id':case['document_id'],'source_version':case['source_version'],
        'source_sha256':case['source_sha256'],'rfq':case['rfq'],'specification':spec})

def spec_status(case):
    spec=case['specification']; fp=specification_fingerprint(case)
    if spec.get('review',{} ) and spec['review'].get('fingerprint')==fp:
        if spec.get('customer_confirmation') and spec['customer_confirmation'].get('fingerprint')==fp: return '已記錄客戶確認'
        return '內部規格已確認'
    return '規格草稿／待確認'

def validate_spec_structure(case,doc):
    spec=case['specification']
    if spec['profile'] not in PROFILES: raise ValueError('未知規格範本')
    active={r['id'] for r in doc['requirements'] if not r['deleted']}
    keys=[x['key'] for x in spec['items']]
    if len(keys)!=len(set(keys)): raise ValueError('規格檢核項目重複')
    expected={k for k,_,_ in COMMON+TEMPLATES[spec['profile']]}
    if not expected<=set(keys): raise ValueError('規格範本必要項目不可遺漏，請補入範本項目')
    for row in spec['items']:
        if not row['label'].strip() or row['state'] not in ITEM_STATES: raise ValueError('規格項目名稱或狀態無效')
        if not set(row['requirement_ids'])<=active: raise ValueError('規格連結已失效，請先同步來源版本並重審')

def spec_issues(case,doc):
    issues=[]; spec=case['specification']
    if case['source_version']!=doc['version'] or case['source_sha256']!=doc['source_sha256']: issues.append('來源版本已變更，請同步圖面版本')
    if doc['status'] not in ('approved','released'): issues.append('原圖／來源文件尚未核准')
    for key,label in [('customer','客戶／供應商'),('project','專案／案件'),('quantity','本次數量'),('unit','數量單位'),('due_date','交期')]:
        if not case['rfq'].get(key,'').strip(): issues.append(label+'未填')
    for key,label in [('form_number','規格確認單號'),('product_name','產品／服務名稱'),('drawing_number','圖號／來源文件號'),('drawing_revision','圖面／文件修訂版次')]:
        if not spec.get(key,'').strip(): issues.append(label+'未填')
    if spec.get('open_issues','').strip(): issues.append('尚有未結案的待確認事項')
    try: validate_spec_structure(case,doc)
    except ValueError as exc: issues.append(str(exc))
    for row in spec['items']:
        if row['state']=='pending': issues.append(row['label']+'尚待確認')
        elif row['state']=='not_applicable' and not row['reason'].strip(): issues.append(row['label']+'標示不適用但未填原因')
        elif row['state']=='confirmed' and (not row['value'].strip() or not row['acceptance'].strip()): issues.append(row['label']+'須填內容與驗收／判定方式')
    return list(dict.fromkeys(issues))

def quote_issues(case,doc):
    from .business import calculate_quote,number
    issues=spec_issues(case,doc)
    if spec_status(case)=='規格草稿／待確認': issues.append('尚未完成內部規格確認')
    terms=case['commercial']
    dates={}
    for key in ('quote_date','valid_until'):
        if terms.get(key):
            try: dates[key]=date.fromisoformat(terms[key])
            except ValueError: issues.append(COMMERCIAL_LABELS[key]+'格式錯誤')
    if len(dates)==2 and dates['valid_until']<dates['quote_date']: issues.append('有效日期不可早於報價日期')
    for key in ('quote_number','quote_date','valid_until','payment_terms','delivery_terms','lead_time','tax_percent','price_basis_qty','moq'):
        if not terms.get(key,'').strip(): issues.append(COMMERCIAL_LABELS[key]+'未填')
    if terms.get('valid_until'):
        try:
            if date.fromisoformat(terms['valid_until'])<date.today(): issues.append('報價有效日期已過期')
        except ValueError: issues.append('報價有效日期格式錯誤')
    try:
        calculate_quote(case)
        if terms.get('moq') and number(case['rfq']['quantity'],'本次數量',positive=True)<number(terms['moq'],'最低訂購量',positive=True): issues.append('本次數量低於最低訂購量')
    except ValueError as exc: issues.append(str(exc))
    return list(dict.fromkeys(issues))
