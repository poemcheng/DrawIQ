from __future__ import annotations
import json
from .domain import canonical, clone, digest, dumps, now, uid


def review_payload(doc,rid,extra_region_ids=()):
    req=clone(next(r for r in doc['requirements'] if r['id']==rid))
    for field in ('status','support_verified','candidates'): req.pop(field,None)
    ids=set(req['evidence_ids']+req['feature_ids'])|set(extra_region_ids)
    return {'requirement':req,'regions':[clone(e) for e in doc['regions'] if e['id'] in ids]}

class StrategyService:
    """Reviewed correction memory. Suggestions only; never silently edits facts."""
    def __init__(self,store): self.store=store

    def list(self):
        with self.store.connect() as con:
            return [json.loads(r['data']) for r in con.execute('SELECT data FROM strategies ORDER BY rowid DESC')]

    def _save(self,strategy,action,reason):
        if not reason.strip(): raise ValueError('請填寫策略決策理由')
        strategy=clone(strategy); strategy['version']=strategy.get('version',0)+1
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=con.execute('SELECT version FROM strategies WHERE id=?',(strategy['id'],)).fetchone()
            if current and current['version']!=strategy['version']-1: raise ValueError('策略已更新，請重新整理')
            con.execute('INSERT INTO strategies VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,data=excluded.data',(strategy['id'],strategy['version'],dumps(strategy)))
            con.execute('INSERT INTO strategy_events(data) VALUES(?)',(dumps({'at':now(),'actor':self.store.actor(),'action':action,'reason':reason,'snapshot':strategy}),))
        return strategy

    def from_correction(self,doc_id,rid,context,reason,*,event_id=None):
        doc=self.store.get(doc_id)
        if doc['status'] not in ('approved','released'): raise ValueError('請先核准文件，才可建立已驗證修正案例')
        req=next(r for r in doc['requirements'] if r['id']==rid)
        if req['status']!='verified' or req['deleted']: raise ValueError('需求尚未驗證')
        events=self.store.correction_history(doc_id,rid=rid)
        if event_id: events=[e for e in events if e['id']==event_id]
        else: events=[e for e in events if e['action']=='edit_requirement' and set(e['changed_fields'])&{'value','unit'}]
        if not events: raise ValueError('此需求沒有符合的修正紀錄；請選擇實際修正事件')
        event=events[0]
        if not context.strip() or len(context.strip())<3: raise ValueError('請輸入至少 3 字的原文語境以限制適用範圍')
        texts=' '.join(e['text'] for e in doc['regions'] if e['id'] in req['evidence_ids'])
        if canonical(context) not in canonical(texts): raise ValueError('策略語境必須出現在本需求的原圖證據中')
        before_doc=self.store.snapshot(doc_id,event['version']-1); after_doc=self.store.snapshot(doc_id,event['version'])
        before=(review_payload(before_doc,rid,event['related_region_ids']) if any(r['id']==rid for r in before_doc['requirements'])
            else {'requirement':{'id':rid,'value':'','unit':'','state':'missing','absent':True},'regions':[]})
        after=review_payload(after_doc,rid,event['related_region_ids'])
        # Never attach a superseded edit to today's approval as if it had been accepted.
        expected=event['detail']['after']; fields=event['changed_fields']
        current=req if event['action']=='edit_requirement' else next((e for e in doc['regions'] if e['id']==event['detail']['region_id']),{})
        if any(expected.get(k)!=current.get(k) for k in fields): raise ValueError('所選修正已被後續修改取代，請選擇目前採用的歷程')
        action='suggest_value' if event['action']=='edit_requirement' and set(fields)<= {'value','unit'} and set(fields)&{'value','unit'} and req['state']=='present' and before['requirement']['state']=='present' else 'review_evidence'
        if action=='suggest_value' and any(after['requirement'].get(k,'')!=req.get(k,'') for k in ('key','value','unit','operator','subject','scope','conditions','state')):
            raise ValueError('修正之後的值、單位或適用條件已變更，請使用一致的最新改值紀錄')
        if any(s.get('source_event_id')==event['id'] and s.get('source_requirement_id')==rid and canonical(s['context_contains'])==canonical(context) and self.source_is_current(s) for s in self.list()):
            raise ValueError('此修正事件與語境已有有效案例，請使用既有案例')
        strategy={'id':uid('strategy'),'name':req['label']+' 修正案例','tenant':doc['tenant'],'profile':doc['profile'],'status':'candidate',
            'key':req['key'],'context_contains':context.strip(),'before':before['requirement']['value'],'after':req['value'],'unit':req['unit'],
            'input_unit':before['requirement']['unit'],'subject':req['subject'],'scope':req['scope'],'conditions':req['conditions'],
            'source_document_id':doc_id,'source_sha256':doc['source_sha256'],'source_event_id':event['id'],'source_requirement_id':rid,
            'source_approved_version':doc['version'],'action':action,'reason':reason,'evaluations':[],
            'source_review_hash':digest(review_payload(doc,rid)),
            'correction_case':{'schema_version':1,'event':clone(event),'before':before,'after':after,
                'reviewed':review_payload(doc,rid),'approved_version':doc['version'],'approval':clone(doc.get('approval',{})),
                'source_sha256':doc['source_sha256'],'changed_fields':fields},
            'limitations':'值建議須經保留案例測試與人工採用；框位、原文及關聯案例只提示重新核對，不移植座標，也不自動修改原文。'}
        if action=='review_evidence': strategy['status']='reference'
        return self._save(strategy,'create',reason)

    def source_is_current(self,strategy):
        try:
            doc=self.store.get(strategy['source_document_id'])
            req=next(r for r in doc['requirements'] if r['id']==strategy['source_requirement_id'])
            if strategy.get('source_review_hash') and digest(review_payload(doc,req['id']))!=strategy['source_review_hash']: return False
            return doc['status'] in ('approved','released') and doc['profile']==strategy['profile'] and req['status']=='verified' and not req['deleted'] and req['value']==strategy['after'] and req['unit']==strategy['unit'] and not any(e['action'] not in ('verify_requirement','approve','release') and e['version']>strategy['source_approved_version'] for e in self.store.history(doc['id']))
        except (KeyError,StopIteration): return False

    def suggest(self,doc, *, statuses=('active',)):
        results=[]
        for s in self.list():
            if s.get('action','suggest_value')!='suggest_value': continue
            if s['status'] not in statuses or s['tenant']!=doc['tenant'] or s['profile']!=doc['profile']: continue
            if not self.source_is_current(s): continue
            for r in doc['requirements']:
                if r['deleted'] or r['status']=='verified' or r['key']!=s['key'] or canonical(r['value'])!=canonical(s['before']): continue
                if any(r.get(k,'')!=s.get(k,'') for k in ('subject','scope','conditions')) or r['unit']!=s.get('input_unit',''): continue
                text=' '.join(e['text'] for e in doc['regions'] if e['id'] in r['evidence_ids'])
                if canonical(s['context_contains']) in canonical(text):
                    results.append({'strategy_id':s['id'],'requirement_id':r['id'],'suggested_value':s['after'],'unit':s['unit'],'context':s['context_contains'],'source_document_id':s['source_document_id'],'status':s['status']})
        return results

    def review_hints(self,doc):
        """Context-matched correction examples, never an automatic geometric transform."""
        hints=[]
        for s in self.list():
            if s.get('action')!='review_evidence' or s['status']!='reference' or not self.source_is_current(s): continue
            if s['tenant']!=doc['tenant'] or s['profile']!=doc['profile'] or s['source_document_id']==doc['id']: continue
            for r in doc['requirements']:
                if r['deleted'] or r['status']=='verified' or r['key']!=s['key'] or any(r.get(k,'')!=s.get(k,'') for k in ('subject','scope','conditions')): continue
                text=' '.join(e['text'] for e in doc['regions'] if e['id'] in r['evidence_ids'])
                if canonical(s['context_contains']) in canonical(text):
                    hints.append({'strategy_id':s['id'],'requirement_id':r['id'],'source_document_id':s['source_document_id'],
                        'source_event_id':s['source_event_id'],'changed_fields':s['correction_case']['changed_fields'],
                        'message':'相似語境曾修正框位、原文或關聯；請比較案例前後內容，重新核對本圖。座標不會自動套用。'})
        return hints

    def evaluate(self,strategy_id,cases,reason):
        s=next(x for x in self.list() if x['id']==strategy_id)
        if s.get('action','suggest_value')!='suggest_value': raise ValueError('此為人工覆核參考案例，不能以改值測試啟用自動座標或原文修正')
        outcomes=[]
        if not cases: raise ValueError('請提供保留測試案例')
        seen=set()
        for case in cases:
            sha=case.get('source_sha256','')
            if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise ValueError('測試案例必須保存原始來源 SHA-256')
            if sha==s['source_sha256']: raise ValueError('訓練來源不能當成保留測試案例')
            if case.get('adjudicated') is not True or not isinstance(case.get('expected_value'),str) or not isinstance(case.get('expected_unit'),str) or not isinstance(case.get('should_apply'),bool): raise ValueError('案例必須經人工仲裁並指定是否適用、正確值及單位')
            case_key=(sha,case.get('key'),case.get('input_value'),case.get('context'))
            if case_key in seen: raise ValueError('測試案例重複')
            seen.add(case_key)
            applies=case['key']==s['key'] and canonical(case['input_value'])==canonical(s['before']) and canonical(s['context_contains']) in canonical(case.get('context','')) and all(case.get(k,'')==s.get(k,'') for k in ('subject','scope','conditions')) and case.get('input_unit','')==s.get('input_unit','')
            predicted=s['after'] if applies else case['input_value']
            predicted_unit=s['unit'] if applies else case.get('input_unit','')
            passed=applies==case['should_apply'] and canonical(predicted)==canonical(case['expected_value']) and predicted_unit==case['expected_unit']
            outcomes.append({'source_sha256':sha,'applied':applies,'should_apply':case['should_apply'],'passed':passed})
        evaluation={'at':now(),'cases':clone(cases),'results':outcomes,'passed':all(o['passed'] for o in outcomes),
            'has_positive':any(o['should_apply'] for o in outcomes),'has_negative':any(not o['should_apply'] for o in outcomes),'independent_sources':len({o['source_sha256'] for o in outcomes})}
        s['evaluations'].append(evaluation); s['status']='shadow' if evaluation['passed'] and evaluation['has_positive'] and evaluation['has_negative'] and evaluation['independent_sources']>=2 else 'candidate'
        return self._save(s,'evaluate',reason)

    def set_status(self,strategy_id,status,reason):
        s=next(x for x in self.list() if x['id']==strategy_id)
        if status not in ('active','disabled','shadow','reference'): raise ValueError('策略狀態無效')
        if not reason.strip(): raise ValueError('請填寫策略決策理由')
        if status=='reference' and (s.get('action')!='review_evidence' or not self.source_is_current(s)):
            raise ValueError('僅能恢復來源核准仍有效的人工覆核案例')
        if status in ('active','shadow'):
            if s.get('action','suggest_value')!='suggest_value': raise ValueError('框位／原文／關聯案例只提供人工覆核提示，不能啟用改值策略')
            if not self.source_is_current(s): raise ValueError('策略來源核准已失效，請重新建立並驗證修正案例')
            e=s['evaluations'][-1] if s['evaluations'] else {}
            if not(e.get('passed') and e.get('has_positive') and e.get('has_negative') and e.get('independent_sources',0)>=2):
                raise ValueError('需通過正例、反例及至少兩份不同來源的保留案例；此門檻只允許建議模式')
        s['status']=status
        return self._save(s,'set_status',reason)

    def apply(self,doc_id,version,rid,strategy_id,reason):
        if not reason.strip(): raise ValueError('請填寫採用策略理由')
        doc=self.store.get(doc_id)
        suggestion=next((x for x in self.suggest(doc) if x['strategy_id']==strategy_id and x['requirement_id']==rid),None)
        if not suggestion: raise ValueError('策略未啟用或目前需求不符合適用條件')
        return self.store.edit_requirement(doc_id,version,rid,{'value':suggestion['suggested_value'],'unit':suggestion['unit'],'state':'present'},f'人工採用策略 {strategy_id}：{reason}')
