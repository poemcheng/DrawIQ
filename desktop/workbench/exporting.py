from __future__ import annotations
import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import fitz
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .domain import checks, digest, dumps, now
from .ingest import sha256
from .paths import resolve_path

def safe_cell(value):
    text=str(value) if value is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text

def export_bundle(store, doc_id, *, release_id=None, output_dir=None):
    if release_id:
        payload=store.get_release(release_id)
        if payload['document']['id']!=doc_id: raise ValueError('發布版本與文件不符')
        release=next(r for r in store.list_releases() if r['id']==release_id)
        if release['revoked_reason']: raise ValueError('發布版本已撤回')
        doc=payload['document']; label=release_id
    else:
        doc=store.get(doc_id); label=f'draft_{doc_id}_v{doc["version"]}'
        payload={'schema_version':'drawiq-knowledge/0.3','release_id':None,'export_status':'draft','created_at':now(),'document':doc}
    from .quotation_layout import quotation_rows
    quote_headers=['報價欄位','屬性','內容','單位','覆核','來源需求ID','證據IDs']
    quote_rows=[]
    for field in quotation_rows(doc,include_proposals=True):
        r=field['source']
        quote_rows.append([field['label'],field['key'],r['value'] if r else '',r['unit'] if r else '',
                           field['status'],r['id'] if r else '', ';'.join(r['evidence_ids']) if r else ''])
    if not release_id:
        # Validation updates can change this projection without editing original
        # facts. Keep earlier drafts, and never reuse their obsolete quotation.
        label += '_quote_' + digest(quote_rows)[:12]
    from .methods import report,candidate_report
    from .feedback import FeedbackService
    method_snapshot=report(doc); candidate_snapshot=candidate_report(doc)
    feedback_snapshot=FeedbackService(store).dataset(doc)
    feedback_snapshot['trajectories']=[e for e in feedback_snapshot['trajectories'] if e['document_version']<=doc['version']]
    if not release_id:
        label += '_evidence_' + digest([method_snapshot,candidate_snapshot,
            feedback_snapshot['trajectories'],feedback_snapshot['examples']])[:12]
    source=resolve_path(doc['source_copy'])
    if not source.exists() or sha256(source)!=doc['source_sha256']: raise ValueError('原始檔案已遺失或內容不一致，無法建立證據包')
    directory=Path(output_dir or store.root/'exports'); directory.mkdir(parents=True,exist_ok=True)
    target=directory/(label+'.zip')
    if target.exists():
        # Idempotent release/draft export, but verify the already-written bundle.
        with zipfile.ZipFile(target) as archive:
            old=json.loads(archive.read('knowledge.json'))
            if digest(old['document'])==digest(doc) and verify_bundle(target): return target
        raise ValueError('既有匯出包與文件不一致，請選擇另一個輸出資料夾')
    with tempfile.TemporaryDirectory(prefix='drawiq_export_',dir=directory) as tmp:
        tmp=Path(tmp)
        (tmp/'knowledge.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        history=[e for e in store.history(doc_id) if e['version']<=doc['version']]
        from .region_capture import copy_capture_artifacts
        copy_capture_artifacts(store.root,doc,history,tmp)
        (tmp/'audit.json').write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'methods.json').write_text(json.dumps(method_snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'harness.json').write_text(json.dumps(method_snapshot['harness'],ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'engineering_candidates.json').write_text(json.dumps(candidate_snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'human_feedback.json').write_text(json.dumps(feedback_snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'checks.json').write_text(json.dumps(checks(doc),ensure_ascii=False,indent=2),encoding='utf-8')
        from .linked_evidence import export_graph, validate_graph
        linked=export_graph(doc); validation=validate_graph(linked)
        if not validation['conforms']: raise ValueError('來源圖譜結構驗證未通過')
        (tmp/'evidence.jsonld').write_text(json.dumps(linked,ensure_ascii=False,indent=2),encoding='utf-8')
        (tmp/'graph_validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
        from .skill_records import export_records
        export_records(doc,tmp/'skill')
        headers=['需求ID','屬性','名稱','對象','值','單位','運算','範圍','條件','缺值狀態','關鍵','覆核','證據IDs','特徵IDs']
        rows=[]
        for r in doc['requirements']:
            if r['deleted']: continue
            rows.append([r['id'],r['key'],r['label'],r['subject'],r['value'],r['unit'],r['operator'],r['scope'],r['conditions'],r['state'],str(r['critical']),r['status'],';'.join(r['evidence_ids']),';'.join(r['feature_ids'])])
        with (tmp/'requirements.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f); writer.writerow(headers); writer.writerows([[safe_cell(x) for x in row] for row in rows])
        book=Workbook(); ws=book.active; ws.title='需求'; ws.append(headers)
        for row in rows:
            ws.append(row)
            for c in ws[ws.max_row]: c.data_type='s'
        evsheet=book.create_sheet('證據'); evsheet.append(['證據ID','頁面','種類','座標','原文','來源'])
        for e in doc['regions']: evsheet.append([e['id'],str(e['page']),e['kind'],json.dumps(e['box']),e['text'],e['source']])
        quotes=book.create_sheet('報價資訊'); quotes.append(quote_headers)
        for row in quote_rows: quotes.append(row)
        with (tmp/'quotation.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f); writer.writerow(quote_headers)
            writer.writerows([[safe_cell(x) for x in row] for row in quote_rows])
        audits=book.create_sheet('稽核'); audits.append(['版本','時間','使用者','動作','理由'])
        for e in reversed(history): audits.append([str(e['version']),e['at'],e['actor'],e['action'],e['reason']])
        for sheet in book:
            sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
            for col in sheet.columns:
                letter=col[0].column_letter; sheet.column_dimensions[letter].width=min(50,max(16,max(len(str(c.value or '')) for c in col)+2))
            for row in sheet:
                for c in row:
                    if isinstance(c.value,str): c.data_type='s'
            for c in sheet[1]: c.fill=PatternFill('solid',fgColor='153A47'); c.font=Font(color='FFFFFF',bold=True)
        book.save(tmp/'knowledge.xlsx')
        pdf=fitz.open()
        labels={r['id']:i+1 for i,r in enumerate([r for r in doc['requirements'] if not r['deleted']])}
        for p in doc['pages']:
            width=p['width']*72/180; height=p['height']*72/180
            sheet=pdf.new_page(width=width,height=height)
            sheet.insert_image(sheet.rect,filename=str(resolve_path(p['image_path'])))
            for r in doc['requirements']:
                if r['deleted']: continue
                for eid in r['evidence_ids']:
                    e=next(e for e in doc['regions'] if e['id']==eid)
                    if e['page']!=p['number']: continue
                    b=e['box']; rect=fitz.Rect(b[0]*width,b[1]*height,b[2]*width,b[3]*height)
                    annot=sheet.add_rect_annot(rect); annot.set_colors(stroke=(0,0.55,0.55)); annot.update()
                    annot.set_info(title=str(labels[r['id']]),content=r['id']+' | '+r['label']+' | '+r['value'])
            sheet.insert_text((8,10),('RELEASED '+release_id) if release_id else 'DRAFT - NOT RELEASED',fontsize=7,color=(.7,0,0))
        pdf.save(tmp/'evidence.pdf'); pdf.close()
        manifest={'bundle':label,'schema_version':'drawiq-bundle/0.3','source_sha256':doc['source_sha256'],'document_version':doc['version'],'release_id':release_id,'files':{}}
        files=[f for f in tmp.rglob('*') if f.is_file()]
        for f in files: manifest['files'][f.relative_to(tmp).as_posix()]=sha256(f)
        manifest['files']['original'+source.suffix]=doc['source_sha256']
        (tmp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        archive=tmp/'bundle.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for f in files+[tmp/'manifest.json']: z.write(f,f.relative_to(tmp).as_posix())
            z.write(source,'original'+source.suffix)
        os.replace(archive,target)
    return target

def verify_bundle(path):
    with zipfile.ZipFile(path) as z:
        manifest=json.loads(z.read('manifest.json'))
        if len(z.namelist())!=len(set(z.namelist())): return False
        if set(z.namelist())!=set(manifest['files'])|{'manifest.json'}: return False
        return all(hashlib.sha256(z.read(name)).hexdigest()==expected for name,expected in manifest['files'].items())
