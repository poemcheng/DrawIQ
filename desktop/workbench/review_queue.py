"""A lossless review projection. Grouping is not deletion or verification."""
from collections import OrderedDict
from .extraction_quality import field_issue, grid_boxes, inside, dimension_context_issue

def review_groups(doc, *, include_verified=False):
    regions={e['id']:e for e in doc['regions']}
    grids={}
    for page in doc['pages']:
        try: grids[page['number']]=grid_boxes(page['image_path'])
        except (OSError,ValueError): grids[page['number']]=[]
    groups=OrderedDict()
    for r in doc['requirements']:
        if r['deleted'] or (r['status']=='verified' and not include_verified): continue
        evidence=[regions[x] for x in r['evidence_ids'] if x in regions]
        p=evidence[0]['page'] if evidence else 1
        text='\n'.join(e['text'] for e in evidence)
        # The original parser evidence is more precise than older substring links.
        source='\n'.join(c.get('evidence_text','') or '' for c in r.get('candidates',[])) or text
        reason=field_issue(r['key'],r['value'],source)
        if r['key'].startswith('dimension.') and evidence and all(dimension_context_issue(e['text']) for e in evidence):
            reason='尺寸候選落在日期、數量或強度等文字，請比對原圖後保留／排除'
        table=r.get('table')
        if r.get('origin')=='drawing_observation':
            from .engineering import metadata
            cat=metadata(r,regions)['category']
            key='engineering:'+cat; title=cat+'：原文／符號候選'; kind='observations'
        elif reason:
            key='suspect'; title='優先處理：疑似誤抓候選'; kind='suspect'
        elif table:
            identity=table.get('block_id') or (r['key'].split('.')[1] if r['key'].startswith('table.') else table['name'])
            key=f'table:{p}:{identity}'; title=table['name']; kind='table'
        elif r['key'].startswith('dimension.'):
            matched=next((i for i,b in enumerate(grids.get(p,[])) if evidence and all(inside(e['box'],b) for e in evidence)),None)
            if matched is not None:
                key=f'grid:{p}:{matched}'; title=f'第 {p} 頁規格表區域 {matched+1}：尺寸候選'; kind='table'
            else:
                # Different physical callouts never become the same assertion.
                key=r['id']; title=r['label']+' '+r['value']; kind='dimension'
        elif r['key'].startswith('note'):
            key=f'notes:{p}'; title=f'第 {p} 頁工程備註'; kind='notes'
        else:
            key=f'fields:{p}'; title=f'第 {p} 頁報價／文件欄位'; kind='fields'
        group=groups.setdefault(key,{'id':key,'title':title,'kind':kind,'page':p,'requirement_ids':[], 'reasons':{}})
        group['requirement_ids'].append(r['id'])
        if reason: group['reasons'][r['id']]=reason
    return sorted(groups.values(),key=lambda g:g['kind']!='suspect')

def queue_stats(doc):
    groups=review_groups(doc)
    return {'groups':len(groups),'pending':sum(len(g['requirement_ids']) for g in groups)}
