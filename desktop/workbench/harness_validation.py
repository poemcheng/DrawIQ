"""Repository checks and declarative correction replay, without OCR or live DB IO."""
import ast
import hashlib
import json
import re
from pathlib import Path
from jsonschema import Draft202012Validator
from .domain import clone, digest, evidence, requirement
from .harness import inspect_document, policy
from .quotation_layout import quotation_rows, source_issue


def source_fingerprint(root):
    root = Path(root)
    files = sorted({p for directory in ('workbench','tests','tools')
                    for p in (root/directory).glob('*.py')} |
                   {p for p in (root/'evaluation').glob('harness_cases.json')} |
                   {p for name in ('app.py','run.bat') if (p := root/name).is_file()} |
                   {root/p for p in policy().get('reference_files',[]) if (root/p).is_file()} |
                   {root/p for p in policy()['required_documents'] if (root/p).is_file()})
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {'sha256': digest(hashes), 'files': hashes}


def check_repository(root):
    root = Path(root); configuration = policy(); findings = []
    for filename in configuration['required_documents']:
        path = root/filename
        if not path.is_file():
            findings.append({'code':'DOC-MISSING','file':filename,'message':'建立規則所列的架構／品質文件'})
        else:
            for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'): continue
                if not (path.parent/target.split('#')[0]).is_file():
                    findings.append({'code':'DOC-LINK','file':filename,'message':'修正失效文件連結：'+target})
    architecture = configuration['architecture']
    for filename in configuration['required_tests']:
        if not (root/filename).is_file():
            findings.append({'code':'TEST-MISSING','file':filename,'message':'還原必要的來源／覆核回歸測試'})
    for name in architecture['protected_modules']:
        path = root/'workbench'/f'{name}.py'
        if not path.is_file():
            findings.append({'code':'MODULE-MISSING','file':str(path),'message':'核心模組不可移除而未更新契約'})
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'),filename=str(path))):
            if isinstance(node,ast.Import): imports = [a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                imports = [node.module or '']
                if not node.module: imports += [a.name for a in node.names]
            else: continue
            for imported in imports:
                banned = any(imported == x or imported.startswith(x+'.') for x in architecture['forbidden_packages'])
                banned |= imported.split('.')[-1] in architecture['forbidden_ui_modules']
                if banned:
                    findings.append({'code':'ARCH-BOUNDARY','file':path.relative_to(root).as_posix(),
                                     'line':node.lineno,'message':f'{imported} 不可由工程核心直接匯入；透過執行層／UI 注入結果'})
    return {'passed':not findings,'findings':findings}


def document_for_case(case):
    """The declared text is a fixture, not a result of running a model."""
    doc = {'id':'case_'+case['id'],'version':1,'name':case['id'],'tenant':'harness-synthetic',
           'source_sha256':digest(case),'source_copy':'synthetic-fixture-no-image',
           'status':'needs_review','regions':[],'requirements':[],'tool_runs':clone(case.get('tool_runs',[]))}
    for i,context in enumerate(case.get('context',[])):
        e = evidence(context.get('page',1),context['box'],context['text'],'fixture')
        e['id']=f'context_{i}'; doc['regions'].append(e)
    for i,item in enumerate(case.get('items',[])):
        ids=[]
        for j,raw in enumerate(item.get('evidence') or [{'page':1,'box':item.get('box',[.1,.2+i*.06,.9,.24+i*.06]),
                                                       'text':item.get('raw',item['value'])}]):
            e=evidence(raw.get('page',1),raw['box'],raw['text'],'fixture')
            e['id']=f'item_{i}_{j}'; ids.append(e['id']); doc['regions'].append(e)
        r=requirement(item['key'],item['value'],label=item.get('label',item['key']),unit=item.get('unit',''),
                      subject=item.get('subject','document'),scope=item.get('scope',''),
                      conditions=item.get('conditions',''),operator=item.get('operator','text'),
                      state=item.get('state','present'),evidence_ids=ids,
                      status=item.get('status','needs_review'),support_verified=item.get('support_verified',False))
        r['id']=f'req_{i}'; doc['requirements'].append(r)
    doc['pages']=[{'number':n,'image_path':'synthetic-fixture-no-image','processing':'complete'}
                  for n in sorted({e['page'] for e in doc['regions']} or {1})]
    doc['quotation_mapping']={field:f'req_{index}' if index is not None else None
                              for field,index in case.get('mapping',{}).items()}
    from .engineering import catalogue_candidates
    doc['requirements'].extend(catalogue_candidates(doc,semantic=False))
    if case.get('invalid_box'): doc['regions'][0]['box']=case['invalid_box']
    return doc


def evaluate_cases(payload):
    schema={'type':'object','required':['schema','cases'],'properties':{
        'schema':{'const':'drawiq-harness-cases/1'},'cases':{'type':'array','minItems':1,'items':{
            'type':'object','required':['id','items'],'properties':{
                'id':{'type':'string','minLength':1},'items':{'type':'array','minItems':1,'items':{
                    'type':'object','required':['key','value'],'properties':{
                        k:{'type':'string'} for k in ('key','value','unit','raw','label','subject','scope','conditions')}}},
                'expect_fields':{'type':'object','additionalProperties':{'type':'object','required':['has_source'],
                    'properties':{'has_source':{'type':'boolean'},'value':{'type':'string'}}}},
                'expect_codes':{'type':'array','items':{'type':'string'}},
                'forbid_codes':{'type':'array','items':{'type':'string'}},'expect_ready':{'type':'boolean'}}}}}}
    errors=list(Draft202012Validator(schema).iter_errors(payload))
    if errors: raise ValueError('案例輸入格式無效：'+errors[0].message)
    ids=[case['id'] for case in payload['cases']]
    if len(set(ids))!=len(ids): raise ValueError('案例 ID 不可重複')
    if payload.get('schema') != 'drawiq-harness-cases/1' or not payload.get('cases'):
        raise ValueError('需提供非空 drawiq-harness-cases/1 案例集')
    results=[]
    for case in payload['cases']:
        try:
            doc=document_for_case(case); report=inspect_document(doc)
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            results.append({'id':case['id'],'passed':False,'assertions':0,
                            'failures':['案例輸入無法建立：'+str(exc)],'contract_status':'invalid_fixture'})
            continue
        fields={f['key']:f for f in report['fields']}; errors=[]; assertions=0
        for key,expected in case.get('expect_fields',{}).items():
            f=fields.get(key); assertions+=1
            if f is None or bool(f['source_id']) != expected['has_source']:
                errors.append(key+'：來源是否帶入不符預期'); continue
            if 'value' in expected:
                assertions+=1
                if f['value'] != expected['value']: errors.append(key+'：值不符已標註預期')
        for code in case.get('expect_codes',[]):
            assertions+=1
            if not any(f['code']==code for f in report['findings']): errors.append('缺少預期規則：'+code)
        for code in case.get('forbid_codes',[]):
            assertions+=1
            if any(f['code']==code for f in report['findings']): errors.append('出現不應觸發規則：'+code)
        if 'expect_ready' in case:
            assertions+=1
            if report['review_ready'] != case['expect_ready']: errors.append('工程覆核完成條件不符預期')
        if not assertions: errors.append('案例缺少預期條件，不能當作通過')
        results.append({'id':case['id'],'passed':not errors,'assertions':assertions,'failures':errors,
                        'contract_status':report['contract_status']})
    return {'passed':all(r['passed'] for r in results),'kind':payload.get('kind','synthetic_contract'),
            'case_count':len(results),'assertion_count':sum(r['assertions'] for r in results),
            'cases_sha256':digest(payload),'cases':results,'ocr_or_vlm_executed':False,'accuracy_benchmark':False}


def confirmed_fixture(doc, field):
    row=next(f for f in quotation_rows(doc) if f['key']==field); r=row['source']
    regions={e['id']:e for e in doc['regions']}
    if not r or r['status']!='verified' or not r['support_verified'] or r['state']!='present':
        raise ValueError('只有仍有效、已人工確認的有值欄位可升級為回歸案例')
    issue=source_issue(r,field,regions)
    if issue: raise ValueError('此案例原文尚未支持客製欄位：'+issue)
    item={k:r[k] for k in ('key','label','value','unit','subject','scope','conditions','operator','state')}
    item['evidence']=[{k:regions[e][k] for k in ('page','box','text')} for e in r['evidence_ids']]
    return {'schema':'drawiq-harness-cases/1','kind':'human_confirmed_replay','cases':[{
        'id':'human_'+digest([doc['source_sha256'],field,r['id'],r['value']])[:16],
        'items':[item],'mapping':{field:0},'expect_fields':{field:{'has_source':True,'value':r['value']}},
        'forbid_codes':['DQH-FIELD-TYPE','DQH-AUTO-EVIDENCE','DQH-STRUCTURE'],
        'provenance':{'document_id':doc['id'],'document_version':doc['version'],
                      'source_sha256':doc['source_sha256'],'requirement_id':r['id'],
                      'verified_requirement_sha256':digest(r)},
        'scope':'重播欄位對應／原文契約；不重新執行 OCR，不代表識字準確率'}]}


def latest_validation(root):
    from . import __version__
    root=Path(root); pointer=root/'qa'/f'harness_{__version__}'/'latest.json'
    if not pointer.is_file(): return {'state':'not_run','message':'本安裝尚未執行版本驗證'}
    try:
        reference=json.loads(pointer.read_text(encoding='utf-8')); path=(pointer.parent/reference['path']).resolve()
        if not path.is_relative_to(pointer.parent.resolve()): raise ValueError('驗證路徑超出報告目錄')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=reference['sha256']: raise ValueError('報告雜湊不一致')
        data=json.loads(body)
        log=(path.parent/data['tests']['log']).resolve()
        if not log.is_relative_to(path.parent) or hashlib.sha256(log.read_bytes()).hexdigest()!=data['tests']['log_sha256']:
            raise ValueError('測試日誌遺失或雜湊不一致')
        relative_cases=data.get('cases_relative_path')
        cases=(root/relative_cases).resolve() if relative_cases else Path(data['cases_path'])
        if relative_cases and not cases.is_relative_to(root.resolve()):
            raise ValueError('案例路徑超出安裝目錄')
        if data.get('cases_file_sha256'):
            case_current=cases.is_file() and hashlib.sha256(cases.read_bytes()).hexdigest()==data['cases_file_sha256']
        elif data['contracts'].get('kind')=='invalid_fixture':
            case_current=not cases.is_file()
        else:
            case_current=cases.is_file() and digest(json.loads(cases.read_text(encoding='utf-8')))==data['contracts']['cases_sha256']
        current=case_current and source_fingerprint(root)['sha256']==data['source_fingerprint']['sha256']
        passed=current and data.get('passed') is True
        return {'state':('current' if current else 'stale'),'passed':passed,
                'message':(('目前版本驗證通過（'+data.get('suite','unknown')+'）' if passed else '目前版本驗證未通過') if current else '程式／案例已有變更，請重跑驗證'),
                'report_path':str(path),'report':data}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return {'state':'invalid','message':'無法驗證版本報告：'+str(exc)}
