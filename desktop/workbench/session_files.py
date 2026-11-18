"""Local result-set bookmarks. They never import assertions or overwrite the database."""
import json
from pathlib import Path
from .domain import now

def save_result_set(store, ids, path):
    if not ids: raise ValueError('尚未載入任何成果')
    docs=[store.get(x) for x in sorted(set(ids))]
    payload={'kind':'drawiq-local-result-set','schema_version':1,'created_at':now(),
        'documents':[{'id':d['id'],'name':d['name'],'source_sha256':d['source_sha256'],'version':d['version']} for d in docs],
        'scope':'固定文件集合，載入時使用本機目前保存版本；不含原檔與資料庫，換機請使用完整工作區備份'}
    Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    return payload

def load_result_set(store, path):
    path=Path(path)
    if path.stat().st_size>8_000_000: raise ValueError('成果清單檔案過大')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('kind')!='drawiq-local-result-set' or data.get('schema_version')!=1: raise ValueError('不是支援的 DrawIQ 本機成果清單')
    rows=data.get('documents')
    if not isinstance(rows,list) or not rows: raise ValueError('成果清單沒有文件')
    ids=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str): raise ValueError('成果清單格式錯誤')
        try: d=store.get(row['id'])
        except KeyError: raise ValueError(f'本機找不到成果：{row.get("name",row["id"])}；換機須先還原完整備份')
        if d['source_sha256']!=row.get('source_sha256'): raise ValueError('原檔雜湊不同，拒絕載入相異成果')
        if not isinstance(row.get('version'),int) or row['version']<1 or d['version']<row['version']: raise ValueError('本機版本早於成果清單，請先確認工作區備份版本')
        if d['id'] not in ids: ids.append(d['id'])
    return ids
