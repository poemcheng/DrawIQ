"""Bounded inspect-check-reread loop adapted from OmniMech tool feedback."""
from .domain import checks, clone, digest, now
from .extraction import apply_analysis
from .bounded import analyze
import time

def next_regions(doc,limit=3):
    selected=[]; seen=set()
    retry_codes={'unit_unknown','ungrounded','conflict','cross_source_conflict','time_as_scale','annual_quantity'}
    for issue in sorted(checks(doc),key=lambda x:x['severity']!='error'):
        if issue['code'] not in retry_codes or not issue['requirement_id']: continue
        r=next(x for x in doc['requirements'] if x['id']==issue['requirement_id'])
        if r['status']=='verified': continue
        for eid in r['evidence_ids']:
            e=next(x for x in doc['regions'] if x['id']==eid)
            b=e['box']; crop=[max(0,b[0]-.03),max(0,b[1]-.03),min(1,b[2]+.03),min(1,b[3]+.03)]
            key=(e['page'],tuple(round(x,3) for x in crop))
            if key in seen: continue
            seen.add(key); selected.append({'page':e['page'],'crop':crop,'reason':issue['message']})
            if len(selected)>=limit: return selected
    return selected

def refine(store,doc_id,version,*,use_vlm=False,model='qwen2.5vl:7b',host='http://localhost:11434',max_rounds=3,progress=lambda _:None,cancelled=lambda:False):
    max_rounds=max(1,min(3,max_rounds)); doc=store.get(doc_id)
    if doc['version']!=version: raise ValueError('文件版本已更新')
    plans=next_regions(doc,max_rounds)
    if not plans: return {'document_id':doc_id,'stop_reason':'no_grounded_retry_target','rounds':0}
    previous=None; rounds=0; stop='budget_exhausted'; page_started={}
    for plan in plans:
        if cancelled(): stop='cancelled'; break
        progress(f'約束回饋 {rounds+1}/{len(plans)}：{plan["reason"]}')
        started=page_started.setdefault(plan['page'],time.monotonic()); remaining=30-(time.monotonic()-started)
        if remaining<3: stop='page_deadline'; break
        output=analyze(doc,store.root,page_numbers=[plan['page']],crop=plan['crop'],use_vlm=use_vlm,model=model,host=host,max_vlm_calls=1,progress=progress,cancelled=cancelled,page_budget_seconds=remaining)
        signature=digest([(r['key'],r['value'],r['unit']) for r in output['requirements']])
        if signature==previous: stop='no_new_candidate'; break
        previous=signature
        doc=apply_analysis(store,doc_id,doc['version'],output,'約束檢查觸發局部重讀：'+plan['reason']); rounds+=1
        if all(r['status']=='failed' for r in output['tool_runs']): stop='tool_failure'; break
    def finish(d):
        d['tool_runs'].append({'id':'refinement_'+now(),'type':'refinement_summary','rounds':rounds,'max_rounds':max_rounds,'stop_reason':stop,'at':now()})
        return {'rounds':rounds,'stop_reason':stop}
    doc=store.mutate(doc_id,doc['version'],'refinement_finished','保存停止條件與工具預算',finish)
    return {'document_id':doc_id,'stop_reason':stop,'rounds':rounds}
