"""Declarative customer/workflow requirements; no executable profile code."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .domain import clone,digest,invalidate,requirement

SCHEMA={'type':'object','additionalProperties':False,'required':['id','name','version','required_fields'],
    'properties':{'id':{'type':'string','minLength':1,'maxLength':80},'name':{'type':'string','minLength':1},'version':{'type':'integer','minimum':1},
    'required_fields':{'type':'array','maxItems':200,'items':{'type':'object','additionalProperties':False,'required':['key','label','subject','critical'],
        'properties':{'key':{'type':'string','minLength':1},'label':{'type':'string'},'subject':{'type':'string','minLength':1},'critical':{'type':'boolean'},'scope':{'type':'string'},'conditions':{'type':'string'}}}}}}

def load_profile(path):
    p=Path(path)
    if p.stat().st_size>1024*1024: raise ValueError('需求範本超出大小限制')
    profile=json.loads(p.read_text(encoding='utf-8-sig')); Draft202012Validator(SCHEMA).validate(profile)
    return profile

def apply_profile(store,doc_id,version,profile,reason):
    Draft202012Validator(SCHEMA).validate(profile)
    def change(doc):
        before=doc.get('profile_snapshot'); added=[]
        for field in profile['required_fields']:
            matches=[r for r in doc['requirements'] if not r['deleted'] and r['key']==field['key'] and r['subject']==field['subject'] and r['scope']==field.get('scope','') and r['conditions']==field.get('conditions','')]
            if matches:
                for r in matches: r['critical']=r['critical'] or field['critical']
            else:
                r=requirement(field['key'],'',label=field['label'],subject=field['subject'],critical=field['critical'],scope=field.get('scope',''),conditions=field.get('conditions',''),origin='profile_missing_field')
                doc['requirements'].append(r); added.append(r['id'])
        doc['profile']=profile['id']+'@'+str(profile['version'])
        doc['profile_snapshot']=clone(profile); doc['profile_digest']=digest(profile)
        invalidate(doc,{r['id'] for r in doc['requirements']})
        return {'profile':doc['profile'],'before':before,'added_requirements':added,'profile_digest':doc['profile_digest']}
    return store.mutate(doc_id,version,'apply_profile',reason,change)
