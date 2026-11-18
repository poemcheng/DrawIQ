"""Local RFQ, costing and process-plan drafts linked to a drawing version."""
import html
import json
import os
import tempfile
import zipfile
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from pathlib import Path
from openpyxl import Workbook
from .domain import clone,digest,dumps,now,uid
from .store import ConflictError
from .knowledge import summary,summary_html,fact_text,CATEGORIES
from .ingest import sha256
from .specification import (normalize_case,specification_fingerprint,validate_spec_structure,
    spec_issues,quote_issues,spec_status,COMMERCIAL_LABELS)

COST_FIELDS={'material':'材料','machining':'加工','surface':'表面處理／塗膠','inspection':'檢驗','packing':'包裝／運費'}
OP_STATUSES=('待規劃','已排程','進行中','待檢驗','完成','暫停')

def number(value,label,*,positive=False):
    if str(value).strip()=='': raise ValueError(label+'尚未填寫；沒有此費用時請明確填 0')
    try: n=Decimal(str(value))
    except InvalidOperation: raise ValueError(label+'必須為數字')
    if not n.is_finite() or n<0 or (positive and n<=0): raise ValueError(label+'必須為'+('正數' if positive else '非負有限數值'))
    if n>Decimal('1000000000000'): raise ValueError(label+'超出本機試算範圍')
    return n

def calculate_quote(case):
    rfq=case['rfq']; q=case['quote']; qty=number(rfq['quantity'],'本次詢價數量',positive=True)
    if not rfq['unit'].strip(): raise ValueError('請填本次詢價數量的單位')
    if q['currency'] not in ('TWD','USD','EUR','CNY','JPY'): raise ValueError('請選擇報價幣別')
    costs={k:number(q['unit_costs'].get(k,''),label+'單位成本') for k,label in COST_FIELDS.items()}
    setup=number(q['setup_cost'],'一次性設定費'); margin=number(q['margin_percent'],'目標毛利率')
    if margin>=100: raise ValueError('目標毛利率須小於 100%')
    total_cost=sum(costs.values())*qty+setup
    unit_quote=(total_cost/qty/(1-margin/100)).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
    total=(unit_quote*qty).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    terms=case.get('commercial',{}); basis=number(terms.get('price_basis_qty','1'),'標價數量基數',positive=True)
    tax=None; gross=None
    if terms.get('tax_percent','').strip():
        rate=number(terms['tax_percent'],'外加稅率')
        if rate>100: raise ValueError('外加稅率須介於 0 至 100%')
        tax=(total*rate/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP); gross=total+tax
    return {'quantity':str(qty),'unit':rfq['unit'],'currency':q['currency'],'unit_cost':str(sum(costs.values())),
        'setup_cost':str(setup),'total_cost':str(total_cost),'target_margin_percent':str(margin),'unit_quote':str(unit_quote),'total_quote':str(total),
        'price_basis_qty':str(basis),'basis_quote':str((unit_quote*basis).quantize(Decimal('.0001'))),
        'tax_amount':str(tax) if tax is not None else None,'total_with_tax':str(gross) if gross is not None else None,
        'scope':'成本按每 1 個詢價單位輸入，標價可按明確數量基數顯示。外加稅率由承辦人填寫；未填不視為 0。M／K 不自動換算，年度量不代入本次數量。'}

def default_case(doc):
    facts=[f for f in summary(doc)['facts'] if f['summary_visible']]; operations=[]
    operations.append({'id':uid('op'),'name':'圖面與規格確認','owner':'','equipment':'','due_date':'','status':'待規劃','evidence_fact_ids':[],'notes':''})
    process=[f for f in facts if f['category']==CATEGORIES[3] and any(x in f['label'] for x in ('製程名稱','加工製程'))]
    if process:
        f=process[0]; operations.append({'id':uid('op'),'name':f['value'],'owner':'','equipment':'','due_date':'','status':'待規劃','evidence_fact_ids':[f['id']],'notes':'由圖面候選帶入，待承辦人確認工序'})
    operations.append({'id':uid('op'),'name':'檢驗與應附文件確認','owner':'','equipment':'','due_date':'','status':'待規劃','evidence_fact_ids':[f['id'] for f in facts if f['category']==CATEGORIES[4]][:8],'notes':''})
    return normalize_case({'document_id':doc['id'],'source_version':doc['version'],'source_sha256':doc['source_sha256'],'version':0,'status':'draft',
        'rfq':{'customer':'','project':'','quantity':'','unit':'PCS','due_date':'','notes':''},
        'quote':{'currency':'','unit_costs':{k:'' for k in COST_FIELDS},'setup_cost':'','margin_percent':''},'operations':operations},doc)

class BusinessService:
    def __init__(self,store):
        self.store=store
        with store.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS business_cases(document_id TEXT PRIMARY KEY,version INTEGER NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS business_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,document_id TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS business_events_no_update BEFORE UPDATE ON business_events BEGIN SELECT RAISE(ABORT,'append only'); END;
            CREATE TRIGGER IF NOT EXISTS business_events_no_delete BEFORE DELETE ON business_events BEGIN SELECT RAISE(ABORT,'append only'); END;''')

    def get(self,doc_id):
        doc=self.store.get(doc_id)
        with self.store.connect() as c: row=c.execute('SELECT data FROM business_cases WHERE document_id=?',(doc_id,)).fetchone()
        return normalize_case(json.loads(row['data']),doc) if row else default_case(doc)

    def save(self,case,reason='承辦人儲存業務草稿'):
        if not reason.strip(): raise ValueError('請填寫業務修改理由')
        case=clone(case); doc=self.store.get(case['document_id']); case=normalize_case(case,doc)
        if case['source_version']!=doc['version'] or case['source_sha256']!=doc['source_sha256']: raise ConflictError('圖面已更新，請先同步圖面版本後重審業務草稿')
        ids=[op['id'] for op in case['operations']]
        if len(ids)!=len(set(ids)): raise ValueError('工序 ID 重複')
        facts={f['id'] for f in summary(doc)['facts']}
        validate_spec_structure(case,doc)
        from datetime import date
        for value,label in [(case['rfq']['due_date'],'要求交期'),(case['commercial']['quote_date'],'報價日期'),(case['commercial']['valid_until'],'有效日期')]:
            if value:
                try: date.fromisoformat(value)
                except ValueError: raise ValueError(label+'請使用 YYYY-MM-DD')
        terms=case['commercial']
        if terms['quote_date'] and terms['valid_until'] and terms['valid_until']<terms['quote_date']: raise ValueError('有效日期不可早於報價日期')
        for key in ('price_basis_qty','moq','tax_percent'):
            if terms[key].strip():
                value=number(terms[key],COMMERCIAL_LABELS[key],positive=key!='tax_percent')
                if key=='tax_percent' and value>100: raise ValueError('外加稅率須介於 0 至 100%')
        if case['rfq']['quantity'].strip(): number(case['rfq']['quantity'],'本次詢價數量',positive=True)
        for key,label in COST_FIELDS.items():
            value=case['quote']['unit_costs'].get(key,'')
            if value.strip(): number(value,label+'單位成本')
        if case['quote']['setup_cost'].strip(): number(case['quote']['setup_cost'],'一次性設定費')
        if case['quote']['margin_percent'].strip() and number(case['quote']['margin_percent'],'毛利率')>=100: raise ValueError('毛利率須小於 100%')
        for op in case['operations']:
            if any(x not in facts for x in op['evidence_fact_ids']): raise ValueError('工序連結到不存在的工程知識，請重新選取')
            if not op['name'].strip() or op['status'] not in OP_STATUSES: raise ValueError('工序名稱或狀態無效')
            if op['status'] in ('進行中','待檢驗','完成') and doc['status'] not in ('approved','released'): raise ValueError('圖面尚未核准；目前可整理與排程，執行狀態需先核准圖面')
            if op['due_date']:
                from datetime import date
                try: date.fromisoformat(op['due_date'])
                except ValueError: raise ValueError('工序日期請使用 YYYY-MM-DD')
        case['status']='draft'
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            # Check source version in the same transaction, after acquiring the write lock.
            current_doc=json.loads(c.execute('SELECT data FROM documents WHERE id=?',(doc['id'],)).fetchone()['data'])
            if current_doc['version']!=case['source_version']: raise ConflictError('圖面剛被更新，請重新整理')
            row=c.execute('SELECT version,data FROM business_cases WHERE document_id=?',(doc['id'],)).fetchone()
            if (row['version'] if row else 0)!=case['version']: raise ConflictError('業務草稿已被更新，請重新載入')
            before=json.loads(row['data']) if row else None
            previous=normalize_case(before,doc) if before else None
            same_spec=previous is not None and specification_fingerprint(previous)==specification_fingerprint(case)
            for stamp in ('review','customer_confirmation'):
                case['specification'][stamp]=clone(previous['specification'].get(stamp)) if same_spec else None
            content=lambda x:{k:v for k,v in x.items() if k not in ('version','updated_at')}
            if previous and content(previous)==content(case): return previous
            case['version']+=1; case['updated_at']=now()
            c.execute('INSERT INTO business_cases VALUES(?,?,?) ON CONFLICT(document_id) DO UPDATE SET version=excluded.version,data=excluded.data',(doc['id'],case['version'],dumps(case)))
            c.execute('INSERT INTO business_events(document_id,data) VALUES(?,?)',(doc['id'],dumps({'at':now(),'actor':self.store.actor(),'reason':reason,'before':before,'after':case})))
        return case

    def confirm_specification(self,doc_id,version,*,customer=None,reference=None,confirmed_on=None):
        """Internal review or a recorded external confirmation, never an e-signature."""
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT version,data FROM business_cases WHERE document_id=?',(doc_id,)).fetchone()
            if not row or row['version']!=version: raise ConflictError('業務草稿已變更，請先儲存並重新確認')
            doc=json.loads(c.execute('SELECT data FROM documents WHERE id=?',(doc_id,)).fetchone()['data'])
            before=json.loads(row['data']); case=normalize_case(before,doc); failures=spec_issues(case,doc)
            if failures: raise ValueError('；'.join(failures))
            fp=specification_fingerprint(case); spec=case['specification']
            if customer is not None:
                from datetime import date
                if not customer.strip() or not (reference or '').strip(): raise ValueError('請填客戶確認人與回覆／簽回檔案編號')
                try: confirmed=date.fromisoformat(confirmed_on or '')
                except ValueError: raise ValueError('客戶確認日期請使用 YYYY-MM-DD')
                if confirmed>date.today(): raise ValueError('客戶確認日期不可晚於今天')
                if spec_status(case)=='規格草稿／待確認': raise ValueError('請先完成內部規格確認')
                spec['customer_confirmation']={'name':customer.strip(),'reference':reference.strip(),'confirmed_on':confirmed_on,
                    'fingerprint':fp,'recorded_at':now(),'recorded_by':self.store.actor(),'meaning':'人工記錄已收到的客戶回覆；不是電子簽章或自動寄送'}
                reason='人工記錄客戶規格確認依據'
            else:
                if spec.get('review') and spec['review'].get('fingerprint')==fp: return case
                spec['review']={'actor':self.store.actor(),'at':now(),'fingerprint':fp,'source_version':doc['version']}; spec['customer_confirmation']=None
                reason='人工完成內部規格確認'
            case['version']+=1; case['updated_at']=now()
            c.execute('UPDATE business_cases SET version=?,data=? WHERE document_id=?',(case['version'],dumps(case),doc_id))
            c.execute('INSERT INTO business_events(document_id,data) VALUES(?,?)',(doc_id,dumps({'at':now(),'actor':self.store.actor(),'reason':reason,'before':before,'after':case})))
            return case

    def rebase(self,doc_id):
        case=self.get(doc_id); doc=self.store.get(doc_id)
        if case['source_version']==doc['version'] and case['source_sha256']==doc['source_sha256']: return case
        case['source_version']=doc['version']; case['source_sha256']=doc['source_sha256']
        active={r['id'] for r in doc['requirements'] if not r['deleted']}
        for item in case['specification']['items']:
            item['state']='pending'; item['requirement_ids']=[rid for rid in item['requirement_ids'] if rid in active]
        case['specification']['review']=None; case['specification']['customer_confirmation']=None
        valid={f['id'] for f in summary(doc)['facts']}; removed=0
        for op in case['operations']:
            op['status']='待規劃'
            old=op['evidence_fact_ids']; op['evidence_fact_ids']=[fid for fid in old if fid in valid]
            removed+=len(old)-len(op['evidence_fact_ids'])
        return self.save(case,f'同步新圖面版本；重審工序與業務草稿。移除 {removed} 個已失效知識連結，原連結保留於業務歷史')

    def export(self,doc_id):
        doc=self.store.get(doc_id); case=self.get(doc_id)
        if case['source_version']!=doc['version']: raise ValueError('請先同步新圖面版本')
        try: calculation=calculate_quote(case); quote_error=None
        except ValueError as exc: calculation=None; quote_error=str(exc)
        out=self.store.root/'exports'; out.mkdir(exist_ok=True); path=out/(uid('business_draft')+'.zip')
        with tempfile.TemporaryDirectory(prefix='drawiq_business_') as temp:
            temp=Path(temp); payload={'schema_version':'drawiq-business/0.4.6','export_status':'draft','source_document_status':doc['status'],'case':case,'quote_calculation':calculation,'quote_missing':quote_error,'quote_readiness_issues':quote_issues(case,doc),'specification_status':spec_status(case),'summary':summary(doc)}
            (temp/'business.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            with self.store.connect() as connection: history=[json.loads(row['data']) for row in connection.execute('SELECT data FROM business_events WHERE document_id=? ORDER BY seq',(doc_id,))]
            (temp/'business_history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
            (temp/'engineering_summary.html').write_text(summary_html(doc,links=False),encoding='utf-8')
            facts={f['id']:fact_text(f) for f in payload['summary']['facts']}
            linked_text=lambda op:'；'.join(facts.get(fid,'連結失效，請重審') for fid in op['evidence_fact_ids'])
            esc=html.escape; h=['<html><meta charset="utf-8"><body style="font-family:Microsoft JhengHei;line-height:1.8"><h1>詢價／報價／製程草稿</h1><p>內部草稿 · 圖面 '+esc(doc['name'])+' v'+str(doc['version'])+' · 尚未對外發送</p><h2>詢價條件</h2>']
            for k,label in [('customer','客戶'),('project','專案'),('quantity','本次數量'),('unit','單位'),('due_date','交期'),('notes','承辦備註')]: h.append('<p>'+label+'：'+esc(case['rfq'][k] or '待填寫')+'</p>')
            h.append('<h2>未稅成本／報價試算</h2>')
            if calculation:
                for k,label in [('currency','幣別'),('total_cost','總成本'),('unit_quote','報價單價'),('total_quote','報價總額'),('scope','試算口徑')]: h.append('<p>'+label+'：'+esc(calculation[k])+'</p>')
            else: h.append('<p>'+esc(quote_error)+'</p>')
            h.append('<h2>製程管理草稿</h2><ol>')
            for op in case['operations']: h.append('<li>'+esc(op['name']+'｜'+op['status']+'｜'+op['owner']+'｜'+op['due_date']+'｜'+op['notes'])+'<p>關聯要求：'+esc(linked_text(op) or '尚未連結')+'</p></li>')
            (temp/'business_draft.html').write_text(''.join(h)+'</ol></body></html>',encoding='utf-8')
            book=Workbook(); w=book.active; w.title='詢價'; w.append(['欄位','值'])
            for k,label in [('customer','客戶／供應商'),('project','專案／案件'),('quantity','本次詢價數量'),('unit','數量單位'),('due_date','要求交期'),('notes','承辦備註')]: w.append([label,case['rfq'][k]])
            w=book.create_sheet('報價試算'); w.append(['項目','數值'])
            w.append(['幣別',case['quote']['currency'] or '待填寫']); w.append(['每一報價單位',case['rfq']['unit']])
            for k,label in COST_FIELDS.items(): w.append([label+'／報價單位',case['quote']['unit_costs'].get(k,'') or '待填寫'])
            w.append(['一次性設定費',case['quote']['setup_cost'] or '待填寫']); w.append(['目標毛利率 %',case['quote']['margin_percent'] or '待填寫'])
            if calculation:
                for k,label in [('total_cost','總成本'),('unit_quote','報價單價'),('total_quote','報價總額'),('scope','試算口徑')]: w.append([label,calculation[k]])
            else: w.append(['尚未完成試算',quote_error])
            w=book.create_sheet('製程'); w.append(['工序','負責人','設備','預定日期','狀態','應遵守工程要求（候選須覆核）','備註','知識連結ID'])
            for op in case['operations']: w.append([op['name'],op['owner'],op['equipment'],op['due_date'],op['status'],linked_text(op),op['notes'],','.join(op['evidence_fact_ids'])])
            for w in book:
                w.freeze_panes='A2'
                for row in w:
                    for cell in row:
                        if isinstance(cell.value,str): cell.data_type='s'
                for col in w.columns: w.column_dimensions[col[0].column_letter].width=32
            book.save(temp/'business_workbook.xlsx')
            from .specification_export import export_spec_files
            export_spec_files(temp,case,doc,payload,book)
            import shutil
            if sha256(doc['source_copy'])!=doc['source_sha256']: raise ValueError('原檔雜湊不一致')
            from .paths import resolve_path
            shutil.copy2(resolve_path(doc['source_copy']),temp/('original'+Path(doc['source_copy']).suffix))
            manifest={'files':{p.name:sha256(p) for p in temp.iterdir()},'source_sha256':doc['source_sha256'],'document_version':doc['version']}
            (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
                for p in temp.iterdir(): z.write(p,p.name)
        return path
