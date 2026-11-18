"""Versioned raw, structured, pretraining and reviewed-supervision corpus exports."""
import csv
import html
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from collections import Counter
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill
from .domain import canonical,digest,dumps,now,checks
from .ingest import sha256
from .paths import resolve_path
from .knowledge import summary,summary_html,fact_text,build_graph,category,KNOWLEDGE_VERSION
from .exporting import safe_cell
from .learning import StrategyService,review_payload

def source_documents(store,include_examples=False):
    return sorted([d for d in store.list_documents() if include_examples or d['tenant']!='design-example'],key=lambda d:(d['name'],d['id']))

def fingerprint(documents): return digest([KNOWLEDGE_VERSION,[(d['id'],d['version'],digest(d)) for d in sorted(documents,key=lambda d:d['id'])]])

def records_for_document(doc):
    data=summary(doc); records=[]
    common={'document_id':doc['id'],'document_name':doc['name'],'document_version':doc['version'],'source_sha256':doc['source_sha256'],
        'document_status':doc['status'],'tenant':doc['tenant'],'profile':doc['profile'],'split':'unassigned','family_id':doc['source_sha256'],'family_reviewed':False}
    regions={r['id']:r for r in doc['regions']}
    for e in doc['regions']:
        if not e['text'].strip(): continue
        kind='feature' if e['kind']=='feature' else ('symbol_dimension' if re.search(r'[⌀ØøΦ±≥≤°℃]|\bM\d+\s*[xX×]|\d+\s*(?:mm|牙)',e['text']) else 'text')
        records.append({**common,'id':f'{doc["id"]}:v{doc["version"]}:{e["id"]}','record_type':'raw','content_kind':kind,'text':e['text'],
            'category':category('',e['text']),'evidence_ids':[e['id']],'requirement_id':None,'locations':[{'page':e['page'],'box':e['box']}],
            'source_engine':e['source'],'review_status':'raw_unverified','supervised_eligible':False,'quality_flags':[]})
    for f in data['facts']:
        eligible=f['origin']=='requirement' and doc['status'] in ('approved','released') and f['status']=='verified' and bool(f['evidence_ids']) and not f['quality_flags']
        records.append({**common,'id':f'{doc["id"]}:v{doc["version"]}:{f["id"]}','record_type':'refined' if f['origin']=='evidence_rule' else 'structured',
            'content_kind':'engineering_requirement','text':fact_text(f)+(('；作用範圍：'+f['scope']) if f['scope'] else '')+(('；條件：'+f['conditions']) if f['conditions'] else ''),
            'category':f['category'],'evidence_ids':f['evidence_ids'],'requirement_id':f['requirement_id'],'fact_id':f['id'],
            'locations':[{'page':regions[e]['page'],'box':regions[e]['box']} for e in f['evidence_ids'] if e in regions],
            'source_engine':f['origin'],'review_status':'approved' if eligible else 'candidate','supervised_eligible':eligible,'quality_flags':f['quality_flags'],'assertion':f})
    return records

def write_jsonl(path,rows):
    with Path(path).open('w',encoding='utf-8',newline='\n') as f:
        for row in rows: f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')

def graphml(graph):
    from xml.etree.ElementTree import Element,SubElement,tostring
    root=Element('graphml',xmlns='http://graphml.graphdrawing.org/xmlns')
    SubElement(root,'key',id='payload',**{'for':'all','attr.name':'payload','attr.type':'string'})
    g=SubElement(root,'graph',edgedefault='directed')
    for n in graph['nodes']: SubElement(SubElement(g,'node',id=n['id']),'data',key='payload').text=json.dumps(n,ensure_ascii=False)
    for i,e in enumerate(graph['edges']): SubElement(SubElement(g,'edge',id='edge'+str(i),source=e['source'],target=e['target']),'data',key='payload').text=json.dumps(e,ensure_ascii=False)
    return tostring(root,encoding='utf-8',xml_declaration=True)

def export_corpus(store,*,document_ids=None,include_examples=False,progress=lambda _:None,cancelled=lambda:False):
    docs=source_documents(store,include_examples)
    if document_ids is not None: docs=[d for d in docs if d['id'] in document_ids]
    if not docs: raise ValueError('目前沒有可匯出的圖面')
    records=[]; summaries=[]; corrections=[]
    for i,doc in enumerate(docs):
        if cancelled(): raise ValueError('已取消語料整理，尚未建立新匯出包')
        progress(f'整理語料 {i+1}/{len(docs)}：{doc["name"]}')
        if sha256(doc['source_copy'])!=doc['source_sha256']: raise ValueError('原檔雜湊不一致：'+doc['name'])
        records.extend(records_for_document(doc)); summaries.append(summary(doc))
        for event in store.correction_history(doc['id']):
            snapshot=store.snapshot(doc['id'],event['version']); ids=event['related_requirement_ids']
            current={r['id']:r for r in doc['requirements'] if not r['deleted'] and r['status']=='verified'}
            approved=bool(ids and doc['status'] in ('approved','released') and all(rid in current and review_payload(doc,rid)==review_payload(snapshot,rid) for rid in ids))
            corrections.append({'document_id':doc['id'],'source_sha256':doc['source_sha256'],'event':event,'current_approved_match':approved,'split':'unassigned'})
    service=StrategyService(store); doc_ids={d['id'] for d in docs}
    reviewed_cases=[{**s,'split':'unassigned','use':'manual_review_reference' if s['action']=='review_evidence' else 'value_suggestion_candidate'} for s in service.list() if s.get('correction_case') and s['source_document_id'] in doc_ids and s['status']!='disabled' and service.source_is_current(s)]
    pretraining={}; supervised=[]
    for row in records:
        if row['record_type']=='raw' and (len(row['text'].strip())>=3 or row['content_kind']=='symbol_dimension'):
            key=(row['tenant'],canonical(row['text']))
            if key not in pretraining: pretraining[key]={'text':row['text'],'source_record_ids':[],'source_document_ids':[],'review_status':'unverified_pretraining_candidate','split':'unassigned'}
            p=pretraining[key]; p['source_record_ids'].append(row['id']); p['source_document_ids']=sorted(set(p['source_document_ids']+[row['document_id']]))
        if row['supervised_eligible']:
            a=row['assertion']; answer={k:a[k] for k in ('key','value','unit','operator','state','scope','conditions')}
            supervised.append({'source_record_id':row['id'],'source_sha256':row['source_sha256'],'split':'unassigned','messages':[
                {'role':'system','content':'依原圖證據抽取指定工程需求，不猜測缺失內容。'},
                {'role':'user','content':'欄位：'+a['label']+'\n證據：'+a['raw_text']},
                {'role':'assistant','content':json.dumps(answer,ensure_ascii=False)}]})
    graph=build_graph(docs); stamp=now().replace(':','').replace('-','').replace('.','')
    folder=store.root/'exports'; folder.mkdir(parents=True,exist_ok=True); target=folder/f'corpus_{stamp}.zip'
    stats={'documents':len(docs),'raw_records':sum(r['record_type']=='raw' for r in records),'structured_records':sum(r['record_type']!='raw' for r in records),
        'pretraining_texts':len(pretraining),'reviewed_sft_samples':len(supervised),'correction_events':len(corrections),'graph_nodes':len(graph['nodes']),'graph_edges':len(graph['edges']),
        'categories':dict(Counter(r['category'] for r in records)),'quality_flags':dict(Counter(flag for r in records for flag in r['quality_flags']))}
    with tempfile.TemporaryDirectory(prefix='drawiq_corpus_') as tmp:
        tmp=Path(tmp)
        write_jsonl(tmp/'corpus_raw.jsonl',[r for r in records if r['record_type']=='raw'])
        write_jsonl(tmp/'corpus_structured.jsonl',[r for r in records if r['record_type']!='raw'])
        write_jsonl(tmp/'corpus_pretraining.jsonl',pretraining.values()); write_jsonl(tmp/'sft_reviewed.jsonl',supervised); write_jsonl(tmp/'corrections.jsonl',corrections)
        write_jsonl(tmp/'reviewed_correction_cases.jsonl',reviewed_cases)
        (tmp/'correction_cases_README.txt').write_text('corrections.jsonl：人工新增框、移框、原文及需求／關聯修正，包含事件與來源。current_approved_match=false 不能當成已核准答案。\nreviewed_correction_cases.jsonl：從指定事件建立、來源核准仍有效且未停用的案例；包含修正前、修正後與最終核准快照。manual_review_reference 僅供人工覆核；value_suggestion_candidate 仍須看策略狀態與測試紀錄。\n全部 split=unassigned；須依圖面家族隔離訓練／測試，未執行模型訓練或自動套用座標。\n',encoding='utf-8')
        write_jsonl(tmp/'summaries.jsonl',summaries)
        (tmp/'knowledge_graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'knowledge_graph.graphml').write_bytes(graphml(graph))
        (tmp/'statistics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'README.txt').write_text('DrawIQ 0.4 工程語料庫\n\n先開 corpus_catalog.xlsx 看分類與來源，再開 summaries/ 看各圖摘要。\ncorpus_raw.jsonl：原始文字、符號、尺寸與座標。\ncorpus_structured.jsonl：結構化候選及帶證據的語意精煉。\ncorpus_pretraining.jsonl：去重文字候選，保留所有來源，不是已驗證答案。\nsft_reviewed.jsonl：只收目前核准且逐項驗證的樣本，可能為空。\nknowledge_graph.json / .graphml：文件、主張、證據、特徵與共用值。共用值不表示可互換零件。\n\n全部 split=unassigned；家族關聯尚須人工整理。同一原圖的裁切或改解析度不可分散到訓練與測試組。未執行模型微調。\n所有範例與候選均須核對原圖；sources/ 內保存原 PDF。\n',encoding='utf-8')
        wb=Workbook(); ws=wb.active; ws.title='語料總覽'; ws.append(['語料ID','圖面','版本','類型','分類','內容','覆核','可做監督樣本','頁面','框座標','品質提醒'])
        for r in records: ws.append([r['id'],r['document_name'],str(r['document_version']),{'raw':'原始擷取','structured':'結構化候選','refined':'語意精煉候選'}[r['record_type']],r['category'],r['text'],{'raw_unverified':'原文待核對','candidate':'候選待覆核','approved':'已核准'}[r['review_status']],'是' if r['supervised_eligible'] else '否',','.join(str(e['page']) for e in r['locations']),json.dumps(r['locations']),'；'.join(r['quality_flags'])])
        ds=wb.create_sheet('文件摘要'); ds.append(['圖面','版本','狀態','摘要','待確認'])
        from .domain import STATUS_LABELS
        for s in summaries:
            ds.append([s['name'],str(s['document_version']),STATUS_LABELS.get(s['status'],s['status']),s['narrative'],'；'.join(s['missing'])])
            ds.cell(ds.max_row,1).hyperlink='summaries/'+s['document_id']+'.html'; ds.cell(ds.max_row,1).style='Hyperlink'
        qs=wb.create_sheet('整理統計'); qs.append(['項目','數值'])
        labels={'documents':'實際圖面數','raw_records':'原始擷取筆數','structured_records':'結構化及精煉筆數','pretraining_texts':'去重文字語料數','reviewed_sft_samples':'已核准監督樣本數','correction_events':'人工修改事件數','graph_nodes':'圖譜節點數','graph_edges':'圖譜關聯數','categories':'分類統計','quality_flags':'品質提醒統計'}
        for k,v in stats.items(): qs.append([labels[k],json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else str(v)])
        for sheet in wb:
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value,str): cell.data_type='s'
            for cell in sheet[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='166B72')
            for col in sheet.columns: sheet.column_dimensions[col[0].column_letter].width=25
        ws.column_dimensions['F'].width=75; ds.column_dimensions['D'].width=90; wb.save(tmp/'corpus_catalog.xlsx')
        with (tmp/'document_split_template.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f); writer.writerow(['document_id','name','source_sha256','family_id','family_reviewed','split'])
            for d in docs: writer.writerow([d['id'],safe_cell(d['name']),d['source_sha256'],d['source_sha256'],'false','unassigned'])
        (tmp/'summaries').mkdir(); (tmp/'sources').mkdir()
        copied=set()
        import shutil
        for d in docs:
            from .region_capture import copy_capture_artifacts
            copy_capture_artifacts(store.root,d,store.history(d['id']),tmp)
            (tmp/'summaries'/(d['id']+'.html')).write_text(summary_html(d,links=False),encoding='utf-8')
            if d['source_sha256'] not in copied:
                shutil.copy2(resolve_path(d['source_copy']),tmp/'sources'/(d['source_sha256']+Path(d['source_copy']).suffix)); copied.add(d['source_sha256'])
        manifest={'schema_version':'drawiq-corpus/0.4','created_at':now(),'fingerprint':fingerprint(docs),'include_examples':include_examples,'statistics':stats,'files':{}}
        for file in tmp.rglob('*'):
            if file.is_file(): manifest['files'][file.relative_to(tmp).as_posix()]=sha256(file)
        (tmp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        staging=target.with_suffix('.partial')
        try:
            with zipfile.ZipFile(staging,'w',zipfile.ZIP_DEFLATED) as z:
                for file in tmp.rglob('*'):
                    if file.is_file(): z.write(file,file.relative_to(tmp).as_posix())
            os.replace(staging,target)
        finally: staging.unlink(missing_ok=True)
    pointer=store.root/'corpus_latest.json'; pointer.write_text(json.dumps({'path':str(target),'statistics':stats,'fingerprint':fingerprint(docs),'created_at':now()},ensure_ascii=False,indent=2),encoding='utf-8')
    return {'corpus_bundle':str(target),'statistics':stats}
