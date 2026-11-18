"""Bounded unresolved-region plans; raw OCR and its coordinate contract stay intact."""
import math
from .domain import clone


def plan_regions(regions, skill_report, requirements, maximum=3):
    measured={e['id']:e for e in regions if e.get('box') and e.get('kind','evidence')=='evidence'
              and not e.get('source','').startswith('VLM:') and e.get('text','').strip()}
    ranked={}
    resolved=set()
    def mark(ids,priority,reason):
        for rid in ids:
            if rid in measured and (rid not in ranked or priority>ranked[rid][0]): ranked[rid]=(priority,reason)
    for item in skill_report.get('fields',[])+skill_report.get('specification_candidates',[]):
        ids=[s.get('region_id') for s in item.get('sources',[])]
        if item.get('status')=='extracted': resolved.update(ids)
        elif ids: mark(ids,90,'skill:'+item.get('status','needs_review'))
    for r in requirements:
        if r.get('state') in ('conflict','unreadable','pending_decision') or r.get('subject')=='unassigned_feature':
            mark(r.get('evidence_ids',[]),70,'unresolved_engineering_candidate')
    for rid,e in measured.items():
        score=e.get('ocr_confidence')
        if score is not None and (not isinstance(score,(int,float)) or not math.isfinite(score) or score<.8):
            mark([rid],100,'low_ocr_confidence')
        elif rid not in resolved:
            from .engineering import classify_text
            group,label=classify_text(e['text'])
            if label=='未分類原文': mark([rid],20,'unclassified_measured_text')
    plans=[]
    for rid,(priority,reason) in sorted(ranked.items(),key=lambda x:(-x[1][0],measured[x[0]]['box'][1],x[0])):
        b=measured[rid]['box']; cx=(b[0]+b[2])/2; cy=(b[1]+b[3])/2
        width=min(.92,max(.25,b[2]-b[0]+.05)); height=min(.4,max(.13,b[3]-b[1]+.08))
        x=min(max(0,cx-width/2),1-width); y=min(max(0,cy-height/2),1-height)
        box=[x,y,x+width,y+height]
        if any(p['box'][0]<=cx<=p['box'][2] and p['box'][1]<=cy<=p['box'][3] for p in plans): continue
        plans.append({'box':box,'region_ids':[rid],'reason':reason,'priority':priority})
        if len(plans)>=maximum: break
    return plans


def crop_regions(regions, box):
    x,y,right,bottom=box; width=right-x; height=bottom-y; output=[]
    for e in regions:
        b=e['box']
        if x<=b[0] and y<=b[1] and b[2]<=right and b[3]<=bottom:
            local=clone(e)
            local['box']=[(b[0]-x)/width,(b[1]-y)/height,(b[2]-x)/width,(b[3]-y)/height]
            output.append(local)
    return output
