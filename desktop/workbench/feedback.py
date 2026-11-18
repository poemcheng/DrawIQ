"""Verified context learning and human gestures; never copy another part's values."""
import json
import math
import re
from collections import Counter
from .domain import clone,digest,dumps,now,uid

def features(text):
    text=re.sub(r'\d+(?:[.,]\d+)*','#',str(text).casefold())
    words=re.findall(r'[a-z_]+|[\u3400-\u9fff]|[Ø⌀±⊥∥℃]',text)
    return Counter(words+[a+b for a,b in zip(words,words[1:])])

def cosine(a,b):
    den=math.sqrt(sum(v*v for v in a.values())*sum(v*v for v in b.values()))
    return sum(v*b.get(k,0) for k,v in a.items())/den if den else 0

class FeedbackService:
    def __init__(self,store): self.store=store

    def record(self,doc,action,*,rid=None,field=None,detail=None):
        event={'id':uid('interaction'),'document_id':doc['id'],'document_version':doc['version'],
               'source_sha256':doc['source_sha256'],'tenant':doc.get('tenant',''),
               'actor':self.store.actor(),'at':now(),'action':action,'requirement_id':rid,
               'quotation_field':field,'detail':clone(detail or {})}
        with self.store.connect() as con:
            previous=con.execute('SELECT hash FROM interaction_events ORDER BY seq DESC LIMIT 1').fetchone()
            event['previous_event_hash']=previous['hash'] if previous else None
            con.execute('INSERT INTO interaction_events(document_id,data,hash) VALUES(?,?,?)',
                        (doc['id'],dumps(event),digest(event)))
        return event

    def history(self,doc_id=None):
        with self.store.connect() as con:
            rows=con.execute('SELECT data FROM interaction_events'+(' WHERE document_id=?' if doc_id else '')+
                             ' ORDER BY seq', (doc_id,) if doc_id else ())
            return [json.loads(row['data']) for row in rows]

    def examples(self,*,tenant=None):
        from .quotation_validation import value_issue
        from .quotation_layout import source_issue
        examples=[]
        for doc in self.store.list_documents():
            if tenant is not None and doc.get('tenant')!=tenant: continue
            rows={r['id']:r for r in doc['requirements'] if not r['deleted']}
            regions={e['id']:e for e in doc['regions']}
            interactions=self.history(doc['id'])
            for field,rid in doc.get('quotation_mapping',{}).items():
                r=rows.get(rid)
                if not r or r['status']!='verified' or not r['support_verified'] or r['state']!='present': continue
                if value_issue(field,r['value']): continue
                if source_issue(r,field,regions): continue
                raw='\n'.join(regions[e]['text'] for e in r['evidence_ids'] if e in regions)
                if not raw: continue
                examples.append({'document_id':doc['id'],'document_version':doc['version'],
                    'source_sha256':doc['source_sha256'],'tenant':doc.get('tenant'),
                    'profile':doc.get('profile','general'),'source_copy':doc['source_copy'],
                    'pages':[clone(p) for p in doc['pages'] if p['number'] in {regions[e]['page'] for e in r['evidence_ids'] if e in regions}],
                    'field':field,'requirement_id':rid,'label':r['label'],'key':r['key'],
                    'raw_text':raw,'value':r['value'],'unit':r['unit'],
                    'regions':[clone(regions[e]) for e in r['evidence_ids'] if e in regions],
                    'review_hash':digest(r),'status':'human_verified',
                    'interaction_ids':[e['id'] for e in interactions if e['requirement_id']==rid or e['quotation_field']==field]})
        return examples

    def suggest(self,doc,field):
        examples=[e for e in self.examples(tenant=doc.get('tenant'))
                  if e['field']==field and e['source_sha256']!=doc['source_sha256'] and e['profile']==doc.get('profile','general')]
        regions={e['id']:e for e in doc['regions']}; suggestions=[]
        for r in doc['requirements']:
            if r['deleted']: continue
            raw='\n'.join(regions[e]['text'] for e in r['evidence_ids'] if e in regions)
            query=features(r['label']+' '+raw)
            matches=[(cosine(query,features(e['label']+' '+e['raw_text'])),e) for e in examples]
            if not matches: continue
            score,example=max(matches,key=lambda x:x[0])
            if score<.72: continue
            suggestions.append({'requirement_id':r['id'],'similarity':round(score,4),
                'example_document_id':example['document_id'],'example_requirement_id':example['requirement_id'],
                'model':'verified-context-nearest/0.6.0','field':field,
                'policy':'僅排序候選；不複製舊值、不自動確認、不移植框座標'})
        return suggestions

    def dataset(self,doc=None):
        examples=self.examples(tenant=doc.get('tenant')) if doc else self.examples()
        sources=sorted({e['source_sha256'] for e in examples})
        holdout=set(sources[::5]) if len(sources)>=3 else set()
        return {'schema':'drawiq-human-feedback/0.6.0','created_at':now(),
                'model':'verified-context-nearest/0.6.0','neural_weights_trained':False,
                'examples':[{**e,'split':'validation' if e['source_sha256'] in holdout else 'train'} for e in examples],
                'trajectories':self.history(doc['id']) if doc else self.history(),
                'validation_note':'來源 SHA 分組；人工框選與修正是監督資料，不是模型正確率。'}
