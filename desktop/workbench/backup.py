"""Consistent database backup plus immutable input assets and execution traces."""
import json
import os
import tempfile
import zipfile
import sqlite3
from pathlib import Path
from .ingest import sha256
from .domain import now

def backup_workspace(store,target):
    target=Path(target).resolve()
    if target.suffix.lower()!='.zip': raise ValueError('工作區備份須為 .zip')
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): raise ValueError('備份檔已存在，請使用新的檔名')
    with tempfile.TemporaryDirectory(prefix='drawiq_backup_') as temp:
        temp=Path(temp); database=store.backup(temp/'knowledge.sqlite3')
        manifest={'kind':'drawiq-workspace-backup','at':now(),'original_root':str(store.root),
            'restore':'Use DrawIQ_Setup.exe --backup on a new empty installation. Asset paths are resolved through path_map.json; audited documents are not rewritten.',
            'files':{}}
        # Imports/traces are append-only in normal operation. Extra files created during
        # the backup are harmless; the SQLite backup determines the consistent state.
        files=[(database,'knowledge.sqlite3')]
        auth_db=store.root/'auth.sqlite3'
        if auth_db.exists():
            # Copy the live WAL-backed account DB through SQLite, not a raw file copy.
            source=sqlite3.connect(auth_db.as_uri()+'?mode=ro',uri=True)
            target_auth=sqlite3.connect(temp/'auth.sqlite3')
            try: source.backup(target_auth)
            finally: target_auth.close(); source.close()
            files.append((temp/'auth.sqlite3','auth.sqlite3'))
            manifest['account_backup']='Includes salted password hashes and authentication history. Protect this backup; login sessions are not stored.'
        if (store.root/'path_map.json').exists(): files.append((store.root/'path_map.json','path_map.json'))
        for name in ('assets','traces','geometry_reports','semantic_index','captures'):
            folder=store.root/name
            if folder.exists(): files.extend((p,p.relative_to(store.root).as_posix()) for p in folder.rglob('*') if p.is_file())
        staging=target.with_suffix('.zip.partial')
        if staging.exists(): raise ValueError('存在未完成的備份，請選擇新的檔名')
        try:
            with zipfile.ZipFile(staging,'w',zipfile.ZIP_DEFLATED) as z:
                for path,name in files: z.write(path,name); manifest['files'][name]=sha256(path)
                z.writestr('backup_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            os.replace(staging,target)
        except Exception:
            staging.unlink(missing_ok=True); raise
    return target
