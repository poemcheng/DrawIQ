from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

STATES = ('present', 'blank', 'missing', 'unreadable', 'not_applicable', 'pending_decision', 'conflict')
STATUS_LABELS = {'needs_review':'待覆核','verified':'已驗證','approved':'已核准','released':'已發布','rejected':'已退回'}
STATE_LABELS = {'present':'有值','blank':'原文空白','missing':'未提供','unreadable':'無法判讀','not_applicable':'不適用','pending_decision':'待決定','conflict':'有衝突'}

def uid(prefix):
    return f'{prefix}_{uuid4().hex[:16]}'

def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)

def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()

def canonical(value):
    # Do not strip decimal points, minus signs, identifiers or tolerance symbols.
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',str(value)).replace('×','x').replace('−','-')).casefold()

def box_valid(box):
    return isinstance(box,list) and len(box)==4 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in box) and 0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1

def requirement(key, value='', *, label=None, unit='', subject='document', evidence_ids=None, critical=False, **extra):
    result={'id':uid('req'),'key':key,'label':label or key,'value':str(value) if value is not None else '',
        'unit':unit,'operator':'eq','subject':subject,'scope':'','conditions':'','state':'present' if value not in ('',None) else 'missing',
        'evidence_ids':evidence_ids or [],'feature_ids':[],'critical':critical,'status':'needs_review',
        'support_verified':False,'candidates':[],'dependencies':[],'deleted':False,'origin':'extraction'}
    result.update(extra)
    return result

def evidence(page, box, text='', source='manual', kind='evidence'):
    if not box_valid(box):
        raise ValueError('框必須在頁面內且具有正面積')
    return {'id':uid('ev'),'page':int(page),'box':box,'text':text,'source':source,'kind':kind,'created_at':now()}

def validate_document(doc):
    pages={p['number'] for p in doc['pages']}
    if not pages or len(pages)!=len(doc['pages']):
        raise ValueError('頁面編號缺失或重複')
    ids=[x['id'] for x in doc['requirements']+doc['regions']]
    if len(ids)!=len(set(ids)):
        raise ValueError('資料 ID 重複')
    regions={e['id']:e for e in doc['regions']}
    requirements={r['id']:r for r in doc['requirements']}
    for e in regions.values():
        if e['page'] not in pages or not box_valid(e['box']):
            raise ValueError('證據頁面或座標無效')
        if e['kind'] not in ('evidence','feature'): raise ValueError('區域種類無效')
    for r in requirements.values():
        if r['state'] not in STATES or not isinstance(r['value'],str):
            raise ValueError('需求值或狀態無效')
        if r['operator'] not in ('eq','ge','le','range','text'):
            raise ValueError('比較運算無效')
        if not r['key'].strip() or not r['subject'].strip():
            raise ValueError('需求屬性及作用對象不可空白')
        if any(x not in regions for x in r['evidence_ids']+r.get('feature_ids',[])):
            raise ValueError('需求連結到不存在的區域')
        if any(regions[x]['kind']!='evidence' for x in r['evidence_ids']) or any(regions[x]['kind']!='feature' for x in r.get('feature_ids',[])):
            raise ValueError('文字證據與作用特徵的關聯種類不符')
        if any(x not in requirements or x==r['id'] for x in r.get('dependencies',[])):
            raise ValueError('衍生需求依賴無效')
        if any(x not in requirements or x==r['id'] for x in r.get('component_ids',[])):
            raise ValueError('品質彙整來源無效')
    visiting=set(); visited=set()
    def visit(rid):
        if rid in visiting: raise ValueError('衍生需求依賴不可形成循環')
        if rid in visited: return
        visiting.add(rid)
        for parent in requirements[rid].get('dependencies',[]): visit(parent)
        visiting.remove(rid); visited.add(rid)
    for rid in requirements: visit(rid)
    dumps(doc)

def checks(doc):
    issues=[]
    active=[r for r in doc['requirements'] if not r.get('deleted')]
    region_map={e['id']:e for e in doc['regions']}
    def add(r, code, text, severity='warning'):
        issues.append({'requirement_id':r['id'] if r else None,'code':code,'message':text,'severity':severity})
    if not active: add(None,'empty','文件尚無需求','error')
    for page in doc['pages']:
        if page.get('recognition_status') in ('partial_timeout','failed','cancelled'):
            add(None,'incomplete_page',f'第 {page["number"]} 頁辨識未完成，需成功重讀後才可核准','error')
    for r in active:
        severity='error' if r['critical'] else 'warning'
        if r.get('origin')=='nailok_human':
            from .nailok_flow import selection_current
            if not selection_current(doc,r):add(r,'nailok_source_changed','此耐落特徵的原文已變更，須重新確認','error')
        if r['state']=='present' and not r['value'].strip(): add(r,'empty_value','有值狀態的內容不可空白','error')
        if r['state'] in ('blank','missing','unreadable','not_applicable') and r['value'].strip(): add(r,'state_value','此缺值狀態應清空數值','error')
        if r['critical'] and r['state'] in ('missing','unreadable','pending_decision','conflict','blank'): add(r,'critical_unresolved','適用關鍵需求尚未解決','error')
        if not r['evidence_ids'] and r['state'] not in ('missing','not_applicable'): add(r,'ungrounded','尚未連結原圖證據',severity)
        if r['state']=='conflict': add(r,'conflict','候選衝突需要人工處理',severity)
        if r['key'] in ('scale','比例') and re.search(r'\d{1,2}:\d{2}',r['value']):
            texts=' '.join(region_map[x]['text'] for x in r['evidence_ids'])
            if not re.search(r'SCALE|比例|縮尺',texts,re.I): add(r,'time_as_scale','此比例可能為頁首時間，請比對標籤')
        if r['key'].startswith('dimension.') and r['state']=='present' and not r['unit'] and '螺紋' not in r['label']:
            add(r,'unit_unknown','尺寸單位尚未確認',severity)
        if r['operator'] in ('ge','le') and r['state']=='present':
            try:
                if not Decimal(r['value']).is_finite(): raise InvalidOperation
            except InvalidOperation: add(r,'numeric','≥／≤ 條件須為十進位數值','error')
        if r.get('upper_tolerance') or r.get('lower_tolerance'):
            try:
                if not all(Decimal(r.get(k) or '0').is_finite() for k in ('lower_tolerance','upper_tolerance')): raise InvalidOperation
                if Decimal(r.get('lower_tolerance') or '0')>Decimal(r.get('upper_tolerance') or '0'): add(r,'tolerance_order','下公差不可大於上公差','error')
            except InvalidOperation: add(r,'tolerance_numeric','公差格式需要確認','error')
        if r['key'] in ('quantity','order_quantity'):
            txt=' '.join(region_map[x]['text'] for x in r['evidence_ids'])+' '+r['value']
            if re.search(r'/年|每年|annual|/year',txt,re.I): add(r,'annual_quantity','偵測到年度需求，請與單批量分開')
        if r['critical'] and r['state']!='not_applicable' and not r['support_verified']:
            add(r,'support_unverified','關鍵需求的證據尚未人工確認','error')
        for dep in r.get('dependencies',[]):
            parent=next(x for x in doc['requirements'] if x['id']==dep)
            if parent.get('deleted') or parent['status']!='verified': add(r,'dependency','上游需求尚未驗證','error')
    # Page-level contradictions are retained, never resolved using average confidence.
    groups={}
    for r in active:
        if r['state']=='present' and not r['key'].startswith(('dimension.','table.')):
            groups.setdefault((r['key'],r['subject'],r['scope'],r['conditions']),[]).append(r)
    for group in groups.values():
        from .quotation_layout import equivalent_value
        if len({(equivalent_value(r['key'],r['value']),r['unit']) for r in group})>1:
            for r in group: add(r,'cross_source_conflict','同對象／條件有不同值；請區分作用範圍或解決衝突','error')
    from .quotation_validation import value_issue
    from .quotation_layout import QUOTE_FIELDS
    active_by_id={r['id']:r for r in active}
    for field,rid in doc.get('quotation_mapping',{}).items():
        r=active_by_id.get(rid)
        if not r or field not in QUOTE_FIELDS or r['state']!='present': continue
        issue=value_issue(field,r['value'])
        if issue: add(r,'quotation_type',QUOTE_FIELDS[field]+'：'+issue,'error')
    return issues

def invalidate(doc, changed_ids):
    affected=set(changed_ids)
    while True:
        more={r['id'] for r in doc['requirements'] if
              (set(r.get('dependencies',[])+r.get('component_ids',[]))&affected) or
              (r.get('origin')=='nailok_human' and
               (set(r.get('nailok_source_ids',[]))&affected or
                (isinstance(r.get('nailok_source_id'),str) and r.get('nailok_source_id') in affected)))}
        if more<=affected: break
        affected|=more
    for r in doc['requirements']:
        if r['id'] in affected:
            r['status']='needs_review'; r['support_verified']=False
    doc['status']='needs_review'
    doc.pop('approval',None)
    doc.pop('current_release',None)

def clone(value):
    return copy.deepcopy(value)
