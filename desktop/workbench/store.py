from __future__ import annotations

import getpass
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import checks, clone, digest, dumps, invalidate, now, uid, validate_document

class ConflictError(ValueError): pass

def _verify_rows(doc, selected):
    """Shared single/batch gate. mutate() rolls back the whole group on failure."""
    active={r['id']:r for r in doc['requirements'] if not r['deleted']}
    if not selected<=active.keys(): raise ValueError('所選需求已變更，請重新載入覆核')
    unchanged=all(active[rid]['status']=='verified' and active[rid]['support_verified'] for rid in selected)
    for rid in sorted(selected):
        r=active[rid]
        from .quotation_validation import value_issue
        from .quotation_layout import QUOTE_FIELDS
        fields={k for k,v in doc.get('quotation_mapping',{}).items() if v==rid}
        if r['key'] in QUOTE_FIELDS: fields.add(r['key'])
        for field in fields:
            issue=value_issue(field,r['value']) if r['state']=='present' else ''
            if issue: raise ValueError(f'{QUOTE_FIELDS[field]}：{issue}；請修正來源或取消帶入')
        if r['state'] in ('conflict','pending_decision','unreadable'):
            raise ValueError(f'{r["label"]}：請先處理衝突／未決／無法辨讀，再確認；不能以空白代替看不清楚')
        if r['state'] not in ('missing','not_applicable') and not r['evidence_ids']:
            raise ValueError(f'{r["label"]}：請先框選並連結原圖證據')
        r['support_verified']=True; r['status']='verified'
    failures=[f'{active[i["requirement_id"]]["label"]}：{i["message"]}' for i in checks(doc)
              if i['requirement_id'] in selected and i['severity']=='error']
    if failures: raise ValueError('；'.join(dict.fromkeys(failures)))
    from .harness import enforce_quotation_contract
    enforce_quotation_contract(doc, selected)
    if unchanged: return False
    doc.pop('approval',None); doc.pop('current_release',None)
    from .engineering import obligations
    doc['status']='verified' if all(r['status']=='verified' for r in obligations(doc)) else 'needs_review'
    return True

class Store:
    """Local audited data; official entry points supply an authenticated actor."""
    def __init__(self, root, *, actor=None):
        self._actor=actor
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'knowledge.sqlite3'
        with self.connect() as con:
            con.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(document_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(document_id,version));
            CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revoked(release_id TEXT PRIMARY KEY, reason TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strategies(id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strategy_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS interaction_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS interaction_no_update BEFORE UPDATE ON interaction_events BEGIN SELECT RAISE(ABORT,'interactions are append only'); END;
            CREATE TRIGGER IF NOT EXISTS interaction_no_delete BEFORE DELETE ON interaction_events BEGIN SELECT RAISE(ABORT,'interactions are append only'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'events are append only'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'events are append only'); END;
            CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT,'snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT,'snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS releases_no_update BEFORE UPDATE ON releases BEGIN SELECT RAISE(ABORT,'releases are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS releases_no_delete BEFORE DELETE ON releases BEGIN SELECT RAISE(ABORT,'releases are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS revoked_no_update BEFORE UPDATE ON revoked BEGIN SELECT RAISE(ABORT,'revocations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS revoked_no_delete BEFORE DELETE ON revoked BEGIN SELECT RAISE(ABORT,'revocations are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS strategy_events_no_update BEFORE UPDATE ON strategy_events BEGIN SELECT RAISE(ABORT,'strategy events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS strategy_events_no_delete BEFORE DELETE ON strategy_events BEGIN SELECT RAISE(ABORT,'strategy events are immutable'); END;
            ''')

    @contextmanager
    def connect(self):
        con=sqlite3.connect(self.db,timeout=30)
        con.row_factory=sqlite3.Row
        try:
            with con: yield con
        finally: con.close()

    def actor(self): return self._actor() if self._actor else getpass.getuser()

    def list_documents(self):
        with self.connect() as con:
            return [json.loads(r['data']) for r in con.execute('SELECT data FROM documents ORDER BY rowid DESC')]

    def get(self, doc_id):
        with self.connect() as con:
            row=con.execute('SELECT data FROM documents WHERE id=?',(doc_id,)).fetchone()
            if row is None: raise KeyError('找不到文件')
            return json.loads(row['data'])

    def history(self, doc_id):
        with self.connect() as con:
            return [json.loads(r['data']) for r in con.execute('SELECT data FROM events WHERE document_id=? ORDER BY seq DESC',(doc_id,))]

    def snapshot(self, doc_id, version):
        with self.connect() as con:
            row=con.execute('SELECT data FROM snapshots WHERE document_id=? AND version=?',(doc_id,version)).fetchone()
            if row is None: raise KeyError('找不到歷程版本快照')
            return json.loads(row['data'])

    def correction_history(self, doc_id, *, rid=None, region_id=None):
        """Filter immutable history; reconstruct associations for pre-0.4.2 events."""
        result=[]
        for event in self.history(doc_id):
            if event['action'] not in ('edit_requirement','edit_region','add_region'): continue
            detail=event['detail']; before=detail.get('before') or {}; after=detail.get('after') or {}
            regions={detail['region_id']} if detail.get('region_id') else set()
            reqs=set(detail.get('affected_requirement_ids',[]))
            if detail.get('requirement_id'): reqs.add(detail['requirement_id'])
            if event['action']=='edit_requirement':
                regions.update(before.get('evidence_ids',[])+before.get('feature_ids',[])+after.get('evidence_ids',[])+after.get('feature_ids',[]))
            elif not detail.get('affected_requirement_ids'):
                snap=self.snapshot(doc_id,event['version'])
                reqs.update(r['id'] for r in snap['requirements'] if not r['deleted'] and regions.intersection(r['evidence_ids']+r['feature_ids']))
            if rid and rid not in reqs: continue
            if region_id and region_id not in regions: continue
            event=clone(event)
            event['related_region_ids']=sorted(regions); event['related_requirement_ids']=sorted(reqs)
            event['changed_fields']=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k) and k not in ('status','support_verified','candidates'))
            result.append(event)
        return result

    def add(self, doc, reason='新增文件'):
        doc=clone(doc); doc['version']=1; doc['status']='needs_review'; doc['created_at']=now()
        validate_document(doc)
        with self.connect() as con:
            existing=con.execute('SELECT data FROM documents WHERE id=?',(doc['id'],)).fetchone()
            if existing: return json.loads(existing['data'])
            con.execute('INSERT INTO documents VALUES(?,?,?)',(doc['id'],1,dumps(doc)))
            self._event(con,doc,None,'import',reason,{'requirements':len(doc['requirements'])})
        return doc

    def _event(self, con, after, before, action, reason, detail):
        row=con.execute('SELECT hash FROM events WHERE document_id=? ORDER BY seq DESC LIMIT 1',(after['id'],)).fetchone()
        event={'id':uid('event'),'document_id':after['id'],'version':after['version'],'actor':self.actor(),'at':now(),'action':action,'reason':reason,
            'detail':detail,'before_hash':digest(before) if before else None,'after_hash':digest(after),'previous_event_hash':row['hash'] if row else None}
        con.execute('INSERT INTO events(document_id,version,data,hash) VALUES(?,?,?,?)',(after['id'],after['version'],dumps(event),digest(event)))
        con.execute('INSERT INTO snapshots VALUES(?,?,?)',(after['id'],after['version'],dumps(after)))

    def mutate(self, doc_id, expected_version, action, reason, change):
        if not reason.strip(): raise ValueError('請填寫修改／審核理由')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT data,version FROM documents WHERE id=?',(doc_id,)).fetchone()
            if row is None: raise KeyError('找不到文件')
            if row['version']!=expected_version: raise ConflictError('文件已由其他操作更新，請重新整理後再修改')
            before=json.loads(row['data']); after=clone(before)
            detail=change(after) or {}
            from .engineering import refresh_composites
            derived=refresh_composites(after)
            if derived:
                invalidate(after,set(derived)); detail['derived_requirement_ids']=derived
            if after==before: return before
            after['version']+=1; after['updated_at']=now()
            validate_document(after)
            con.execute('UPDATE documents SET version=?,data=? WHERE id=?',(after['version'],dumps(after),doc_id))
            self._event(con,after,before,action,reason,detail)
        return after

    def edit_requirement(self, doc_id, version, rid, patch, reason):
        allowed={'key','label','value','unit','operator','subject','scope','conditions','state','critical','evidence_ids','feature_ids','dependencies','upper_tolerance','lower_tolerance'}
        if set(patch)-allowed: raise ValueError('不允許修改此需求屬性')
        def change(doc):
            req=next(r for r in doc['requirements'] if r['id']==rid and not r['deleted'])
            before=clone(req)
            # Form defaults and list ordering do not represent engineering corrections.
            effective={k:v for k,v in clone(patch).items() if (set(v)!=set(req.get(k,[])) if k in ('evidence_ids','feature_ids','dependencies') else v!=req.get(k,''))}
            if not effective: return {}
            from .quotation_layout import retain_quotation_sources
            quotation_fields = retain_quotation_sources(doc, {rid})
            req.update(effective); invalidate(doc,{rid})
            if req.get('composite_kind') and {'value','unit'}&effective.keys(): req['manual_composite_override']=True
            return {'requirement_id':rid,'before':before,'after':clone(req),'changed_fields':sorted(effective),
                    'quotation_fields': quotation_fields}
        return self.mutate(doc_id,version,'edit_requirement',reason,change)

    def add_requirement(self, doc_id, version, req, reason):
        def change(doc):
            doc['requirements'].append(clone(req)); invalidate(doc,{req['id']})
            return {'requirement_id':req['id'],'after':req}
        return self.mutate(doc_id,version,'add_requirement',reason,change)

    def set_quotation_source(self, doc_id, version, key, rid, reason, *, create=False):
        from .quotation_layout import QUOTE_FIELDS
        from .quotation import CRITICAL
        from .domain import requirement
        if key not in QUOTE_FIELDS: raise ValueError('未知報價欄位')
        if create and rid is not None: raise ValueError('新增欄位不可同時指定來源')
        def change(doc):
            before=clone(doc.get('quotation_mapping',{}))
            source=rid
            if create:
                row=requirement(key,label=QUOTE_FIELDS[key],origin='manual',operator='text',critical=key in CRITICAL)
                doc['requirements'].append(row); source=row['id']
            elif source is not None and not any(r['id']==source and not r['deleted'] for r in doc['requirements']):
                raise ValueError('報價來源不存在或已排除')
            mapping=doc.setdefault('quotation_mapping',{})
            if key in mapping and mapping[key]==source and not create: return {}
            mapping[key]=source
            invalidate(doc,{source} if source else set())
            return {'field':key,'requirement_id':source,'before':before,'after':clone(mapping)}
        return self.mutate(doc_id,version,'set_quotation_source',reason,change)

    def ensure_engineering_catalogue(self,doc_id,version):
        from .engineering import catalogue_candidates,VERSION
        def change(doc):
            new=catalogue_candidates(doc,semantic=doc['status'] not in ('approved','released'))
            upgraded=[]
            if doc['status'] not in ('approved','released'):
                for r in doc['requirements']:
                    if not r['deleted'] and r.get('composite_kind')=='quality' and r.get('origin')=='engineering_projection' and r['status']!='verified' and not r.get('manual_composite_override') and r.get('composition_format')!='conditions/0.8.0':
                        r['composition_format']='conditions/0.8.0'; upgraded.append(r['id'])
            if not new and not upgraded: return {}
            doc['requirements'].extend(new); doc['candidate_catalogue_version']=VERSION
            semantic={r['id'] for r in new if r['origin']!='drawing_observation'}
            if semantic: invalidate(doc,semantic)
            return {'added_requirement_ids':[r['id'] for r in new],
                    'condition_format_upgraded_ids':upgraded,
                    'policy':'保存全部辨識原文／符號候選；不覆寫人工值、排除、關聯或確認',
                    'catalogue_version':VERSION}
        return self.mutate(doc_id,version,'candidate_catalogue','整理工程候選與來源索引',change)

    def delete_requirement(self, doc_id, version, rid, reason):
        def change(doc):
            req=next(r for r in doc['requirements'] if r['id']==rid)
            before=clone(req); req['deleted']=True; invalidate(doc,{rid})
            return {'requirement_id':rid,'before':before,'after':clone(req)}
        return self.mutate(doc_id,version,'delete_requirement',reason,change)

    def save_region(self, doc_id, version, region, rid, reason, replace_id=None):
        def change(doc):
            before=None
            links_before={r['id']:{k:clone(r[k]) for k in ('evidence_ids','feature_ids')} for r in doc['requirements']}
            if replace_id:
                existing=next(e for e in doc['regions'] if e['id']==replace_id)
                before=clone(existing); existing.update({k:clone(v) for k,v in region.items() if k in ('page','box','text','kind')})
                region_id=replace_id
            else:
                existing=clone(region); doc['regions'].append(existing); region_id=region['id']
            key='feature_ids' if existing['kind']=='feature' else 'evidence_ids'
            # A type correction moves existing links atomically, including archived links.
            for req in doc['requirements']:
                if region_id in req['evidence_ids']+req['feature_ids'] and region_id not in req[key]:
                    for field in ('evidence_ids','feature_ids'): req[field]=[x for x in req[field] if x!=region_id]
                    req[key].append(region_id)
            if rid:
                req=next(r for r in doc['requirements'] if r['id']==rid and not r['deleted'])
                if region_id not in req[key]: req[key].append(region_id)
            links_after={r['id']:{k:clone(r[k]) for k in ('evidence_ids','feature_ids')} for r in doc['requirements']}
            if before==existing and links_before==links_after: return {}
            affected={r['id'] for r in doc['requirements'] if not r['deleted'] and region_id in r['evidence_ids']+r['feature_ids']}
            invalidate(doc,affected)
            return {'region_id':region_id,'requirement_id':rid,'before':before,'after':clone(existing),
                'affected_requirement_ids':sorted(affected),
                'links_before':{k:v for k,v in links_before.items() if k in affected},
                'links_after':{k:v for k,v in links_after.items() if k in affected},
                'changed_fields':sorted(k for k in ('page','box','text','kind') if not before or before.get(k)!=existing.get(k))}
        return self.mutate(doc_id,version,'edit_region' if replace_id else 'add_region',reason,change)

    def verify_requirement(self, doc_id, version, rid, reason):
        def change(doc):
            before=clone(next(r for r in doc['requirements'] if r['id']==rid and not r['deleted']))
            if not _verify_rows(doc,{rid}): return {}
            from .quotation_layout import retain_quotation_sources
            quotation_fields = retain_quotation_sources(doc, {rid})
            after=clone(next(r for r in doc['requirements'] if r['id']==rid))
            return {'requirement_id':rid,'before':before,'after':after,
                'attestation':'已核對值、單位、作用對象、條件及原圖證據', 'quotation_fields': quotation_fields}
        return self.mutate(doc_id,version,'verify_requirement',reason,change)

    def review_requirements(self, doc_id, version, rids, reason, *, action='verify'):
        """One explicit attestation, one transaction; no silent partial success."""
        if action not in ('verify','exclude'): raise ValueError('未知覆核操作')
        selected=set(rids)
        if not selected: raise ValueError('請勾選已核對的列')
        def change(doc):
            active={r['id']:r for r in doc['requirements'] if not r['deleted']}
            if not selected<=active.keys(): raise ValueError('所選需求已變更，請重新開啟覆核')
            before={rid:clone(active[rid]) for rid in sorted(selected)}
            if action=='exclude':
                dependents=[r for r in active.values() if set(r['dependencies'])&selected and r['id'] not in selected]
                if dependents: raise ValueError('尚有其他需求依賴所選列，請先修正依賴關係')
                for rid in selected: active[rid]['deleted']=True
                invalidate(doc,selected)
            else:
                if not _verify_rows(doc,selected): return {}
                from .quotation_layout import retain_quotation_sources
                retain_quotation_sources(doc, selected)
            return {'requirement_ids':sorted(selected),'operation':action,'before':before,
                'after':{rid:clone(active[rid]) for rid in sorted(selected)},
                'attestation':'使用者已逐列核對值、單位、表格欄名、適用規格、作用對象、條件與原圖證據'}
        return self.mutate(doc_id,version,'review_requirements',reason,change)

    def undo_review_exclusion(self, doc_id, version, reason):
        """Restore only the latest exclusion; append an event, never erase history."""
        latest=next(iter(self.history(doc_id)),None)
        if not latest or latest['version']!=version or latest['action']!='review_requirements' or latest['detail'].get('operation')!='exclude':
            raise ValueError('只能復原最近一次排除；文件已有其他變更')
        selected=set(latest['detail']['requirement_ids'])
        def change(doc):
            rows={r['id']:r for r in doc['requirements'] if r['id'] in selected}
            if len(rows)!=len(selected) or any(not r['deleted'] for r in rows.values()): raise ValueError('排除狀態已變更，請重新載入')
            before=clone(rows)
            for r in rows.values(): r['deleted']=False
            invalidate(doc,selected)
            return {'requirement_ids':sorted(selected),'undo_event_id':latest['id'],
                    'before':before,'after':clone(rows),'operation':'restore_as_pending'}
        return self.mutate(doc_id,version,'undo_review_exclusion',reason,change)

    def approve(self, doc_id, version, reason):
        def change(doc):
            from .engineering import obligations
            active=obligations(doc)
            if not active or any(r['status']!='verified' for r in active): raise ValueError('請逐項完成需求驗證後再核准')
            if any(r['state'] in ('conflict','pending_decision','unreadable') for r in active):
                raise ValueError('仍有衝突／未決／無法辨讀項目，即使舊版曾驗證仍須重新修正')
            failures=[i['message'] for i in checks(doc) if i['severity']=='error']
            if failures: raise ValueError('；'.join(dict.fromkeys(failures)))
            from .harness import enforce_quotation_contract
            enforce_quotation_contract(doc)
            doc['status']='approved'; doc['approval']={'actor':self.actor(),'at':now(),'reason':reason,'document_version':doc['version']+1}
            doc.pop('current_release',None)
            return doc['approval']
        return self.mutate(doc_id,version,'approve',reason,change)

    def reject(self, doc_id, version, reason):
        def change(doc):
            invalidate(doc,{r['id'] for r in doc['requirements']}); doc['status']='rejected'
        return self.mutate(doc_id,version,'reject',reason,change)

    def release(self, doc_id, version, reason):
        # Immutable payload inserted in the same transaction as its release event.
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT data FROM documents WHERE id=?',(doc_id,)).fetchone()
            if row is None: raise KeyError('找不到文件')
            before=json.loads(row['data'])
            if before['version']!=version: raise ConflictError('文件版本已變更')
            if before['status']=='released': return before, self.get_release(before['current_release'])
            if before['status']!='approved': raise ValueError('只有核准版本可正式發布')
            if not reason.strip(): raise ValueError('請填寫發布用途')
            if any(i['severity']=='error' for i in checks(before)): raise ValueError('仍有工程檢查錯誤')
            from .harness import enforce_quotation_contract
            enforce_quotation_contract(before)
            rid=uid('release'); after=clone(before); after.update(status='released',current_release=rid,version=version+1)
            payload={'schema_version':'drawiq-knowledge/0.3','release_id':rid,'purpose':reason,'created_at':now(),'document':after}
            payload['sha256']=digest(payload)
            con.execute('INSERT INTO releases VALUES(?,?,?,?,?,?)',(rid,doc_id,after['version'],dumps(payload),payload['sha256'],now()))
            con.execute('UPDATE documents SET version=?,data=? WHERE id=?',(after['version'],dumps(after),doc_id))
            self._event(con,after,before,'release',reason,{'release_id':rid,'payload_hash':payload['sha256']})
        return after,payload

    def get_release(self, release_id):
        with self.connect() as con:
            row=con.execute('SELECT data FROM releases WHERE id=?',(release_id,)).fetchone()
            if row is None: raise KeyError('找不到發布版本')
            return json.loads(row['data'])

    def list_releases(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute('SELECT r.id,r.document_id,r.version,r.created_at, v.reason AS revoked_reason FROM releases r LEFT JOIN revoked v ON v.release_id=r.id ORDER BY r.created_at DESC')]

    def revoke(self, release_id, reason):
        if not reason.strip(): raise ValueError('請填寫撤回理由')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT document_id FROM releases WHERE id=?',(release_id,)).fetchone()
            if row is None: raise KeyError('找不到發布版本')
            if con.execute('SELECT 1 FROM revoked WHERE release_id=?',(release_id,)).fetchone(): return
            con.execute('INSERT INTO revoked VALUES(?,?,?,?)',(release_id,reason,self.actor(),now()))
            before=json.loads(con.execute('SELECT data FROM documents WHERE id=?',(row['document_id'],)).fetchone()['data'])
            after=clone(before)
            if after.get('current_release')==release_id:
                invalidate(after,{r['id'] for r in after['requirements']}); after['status']='rejected'
            after['version']+=1; after['updated_at']=now()
            con.execute('UPDATE documents SET version=?,data=? WHERE id=?',(after['version'],dumps(after),after['id']))
            self._event(con,after,before,'revoke',reason,{'release_id':release_id})

    def search(self, query):
        if not query.strip(): return []
        result=[]
        for release in self.list_releases():
            if release['revoked_reason']: continue
            current=self.get(release['document_id'])
            if current.get('current_release')!=release['id']: continue
            data=self.get_release(release['id'])
            for req in data['document']['requirements']:
                if not req['deleted'] and all(t.casefold() in dumps(req).casefold() for t in query.split()):
                    result.append({'release_id':release['id'],'document_id':release['document_id'],'document_name':current['name'],'requirement':req})
        return result

    def verify_audit(self, doc_id):
        with self.connect() as con:
            rows=con.execute('SELECT * FROM events WHERE document_id=? ORDER BY seq',(doc_id,)).fetchall()
            previous=None; previous_snapshot=None; last_version=0
            for row in rows:
                e=json.loads(row['data'])
                snapshot=con.execute('SELECT data FROM snapshots WHERE document_id=? AND version=?',(doc_id,row['version'])).fetchone()
                if row['version']!=last_version+1 or e['before_hash']!=previous_snapshot or digest(e)!=row['hash'] or e['previous_event_hash']!=previous or not snapshot or digest(json.loads(snapshot['data']))!=e['after_hash']:
                    return False
                previous=row['hash']; previous_snapshot=e['after_hash']; last_version=row['version']
            current=con.execute('SELECT data FROM documents WHERE id=?',(doc_id,)).fetchone()
            if current and digest(json.loads(current['data']))!=previous_snapshot: return False
            return bool(rows)

    def backup(self, target):
        target=Path(target).resolve()
        if target==self.db: raise ValueError('備份路徑不可覆蓋使用中的資料庫')
        target.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as src:
            dest=sqlite3.connect(target)
            try: src.backup(dest)
            finally: dest.close()
        return target
