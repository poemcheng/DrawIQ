"""Offline multilingual neural embeddings with immutable builds and stale-data checks."""
import json
import os
import re
import threading
from pathlib import Path
import numpy as np
from .domain import canonical,digest,now,uid
from .corpus import source_documents,fingerprint,records_for_document
from .ingest import sha256

MODEL_ID='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
_model=None; _lock=threading.Lock()

def load_encoder():
    global _model
    with _lock:
        if _model is None:
            os.environ.setdefault('USE_TF','0'); os.environ.setdefault('TRANSFORMERS_NO_TF','1')
            os.environ.setdefault('HF_HUB_OFFLINE','1')
            try:
                from sentence_transformers import SentenceTransformer
                from huggingface_hub import snapshot_download
            except ImportError as exc: raise ValueError('尚未安裝語意搜尋套件，請執行 install_semantic.bat') from exc
            import torch
            torch.set_num_threads(min(4,os.cpu_count() or 1))
            try: local_path=os.environ.get('DRAWIQ_EMBED_MODEL_PATH') or snapshot_download(MODEL_ID,local_files_only=True)
            except OSError as exc: raise ValueError('本機尚未準備嵌入模型，請執行 install_semantic.bat；或設定 DRAWIQ_EMBED_MODEL_PATH 指向已下載模型目錄') from exc
            _model=SentenceTransformer(local_path,local_files_only=True,device='cpu',trust_remote_code=False)
            _model.drawiq_snapshot=Path(local_path).name
        return _model

def model_info(model):
    from importlib.metadata import version
    config=model._first_module().auto_model.config
    semantic_config=config.to_dict(); semantic_config.pop('_name_or_path',None)
    return {'model_id':MODEL_ID,'revision':getattr(config,'_commit_hash',None) or getattr(model,'drawiq_snapshot',None),'config_digest':digest(semantic_config),
        'sentence_transformers_version':version('sentence-transformers'),'max_sequence_length':model.max_seq_length,'dimension':model.get_sentence_embedding_dimension(),'normalization':'L2','device':'cpu'}

def split_text(model,text):
    limit=max(8,model.max_seq_length-4)
    encoded=model.tokenizer(text,add_special_tokens=False,return_offsets_mapping=True,truncation=False,verbose=False)
    offsets=encoded['offset_mapping']
    for start in range(0,len(offsets),limit):
        group=offsets[start:start+limit]
        if group: yield text[group[0][0]:group[-1][1]],group[0][0],group[-1][1]

def index_status(store):
    pointer=store.root/'semantic_index'/'current.json'
    if not pointer.exists(): return {'status':'missing','message':'尚未建立語意索引；按「建立／更新語意索引」'}
    try:
        info=json.loads(pointer.read_text(encoding='utf-8'))
        if not re_full_build(info['build']): raise ValueError('Invalid index build')
        metadata=json.loads((pointer.parent/info['build']/'metadata.json').read_text(encoding='utf-8'))
        current=fingerprint(source_documents(store))
        metadata['status']='ready' if current==metadata['fingerprint'] else 'stale'
        metadata['message']='語意索引可用' if metadata['status']=='ready' else '圖面已有修改，請更新索引後再搜尋'
        return metadata
    except (ValueError,OSError,KeyError): return {'status':'broken','message':'索引不完整，請重新建立'}

def re_full_build(value):
    import re
    return isinstance(value,str) and bool(re.fullmatch(r'build_[a-f0-9]{16}',value))


def engineering_matches(query,text):
    """Transparent term coverage supplements neural similarity; it is not a correctness score."""
    concepts=[('溫度／耐熱',r'溫|温|耐熱|耐热|℃|°\s*c|temperature|thermal|heat resistant'),
        ('塗膠',r'塗膠|涂胶|膠種|胶种|adhesive|coating'),('螺紋',r'螺紋|螺纹|thread'),
        ('首批',r'首批|first batch'),('每批',r'每批|each batch|every batch'),
        ('檢驗文件',r'檢驗|檢測|检验|检测|報告|报告|證明|証明|certificate|inspection report')]
    requested=[(name,pattern) for name,pattern in concepts if re.search(pattern,query,re.I)]
    matches=[name for name,pattern in requested if re.search(pattern,text,re.I)]
    bonus=.24*len(matches)/len(requested) if requested else 0.
    specs=re.findall(r'(?<![A-Za-z0-9])M\d+(?:[xX×]\d+(?:\.\d+)?)?(?:-\d+[A-Za-z]+)?',query,re.I)
    exact=[spec for spec in specs if re.search(r'(?<![a-z0-9])'+re.escape(canonical(spec))+r'(?![a-z0-9.])',canonical(text))]
    if specs: bonus+=.2*len(exact)/len(specs)
    return bonus,matches+['規格 '+spec for spec in exact]

def build_index(store,progress=lambda _:None,cancelled=lambda:False):
    docs=source_documents(store); source_fingerprint=fingerprint(docs)
    if not docs: raise ValueError('尚無圖面可建立索引')
    progress('載入本機多語言嵌入模型（首次載入需要一些時間）')
    model=load_encoder(); rows=[]; seen=set()
    for doc in docs:
        for row in records_for_document(doc):
            # Obvious OCR/parser artefacts remain in the corpus but do not dominate retrieval.
            if row['quality_flags'] and any('可能被誤' in f or '只有單位' in f for f in row['quality_flags']): continue
            text=row['category']+'｜'+row['text']
            if row['record_type']!='raw' and row.get('assertion',{}).get('raw_text'): text+='\n原文：'+row['assertion']['raw_text']
            if len(row['text'].strip())<3: continue
            key=(doc['id'],text)
            if key in seen: continue
            seen.add(key)
            for chunk,start,end in split_text(model,text): rows.append({**row,'chunk_text':chunk,'character_start':start,'character_end':end})
    if not rows: raise ValueError('尚無足夠文字可建立索引')
    parts=[]
    for start in range(0,len(rows),64):
        if cancelled(): raise ValueError('已取消索引建立；舊索引未被替換')
        progress(f'嵌入向量 {min(start+64,len(rows))}/{len(rows)} 段')
        vectors=model.encode([r['chunk_text'] for r in rows[start:start+64]],batch_size=32,normalize_embeddings=True,show_progress_bar=False,convert_to_numpy=True)
        parts.append(np.asarray(vectors,dtype=np.float32))
    vectors=np.concatenate(parts)
    if vectors.shape[0]!=len(rows) or not np.isfinite(vectors).all(): raise ValueError('嵌入向量不完整')
    if fingerprint(source_documents(store))!=source_fingerprint: raise ValueError('建置期間圖面已更新，請重新建立索引')
    if cancelled(): raise ValueError('已取消索引建立')
    base=store.root/'semantic_index'; build=uid('build'); folder=base/build; folder.mkdir(parents=True)
    np.save(folder/'vectors.npy',vectors,allow_pickle=False)
    (folder/'records.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    metadata={'created_at':now(),'fingerprint':source_fingerprint,'model':model_info(model),'documents':len(docs),'chunks':len(rows),'build':build,
        'files':{name:sha256(folder/name) for name in ('vectors.npy','records.json')}}
    (folder/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    temp=base/(uid('pointer')+'.tmp'); temp.write_text(json.dumps({'build':build}),encoding='utf-8'); os.replace(temp,base/'current.json')
    return {'semantic_index':str(folder),'statistics':metadata}

def search(store,query,*,limit=12,approved_only=False,category_filter=None,encoder=None):
    if not query.strip(): return []
    status=index_status(store)
    if status['status']!='ready': raise ValueError(status['message'])
    folder=store.root/'semantic_index'/status['build']; rows=json.loads((folder/'records.json').read_text(encoding='utf-8'))
    if set(status.get('files',{}))!={'vectors.npy','records.json'} or any(sha256(folder/name)!=value for name,value in status['files'].items()): raise ValueError('索引檔案雜湊不一致，請重新建立')
    vectors=np.load(folder/'vectors.npy',allow_pickle=False)
    model=encoder or load_encoder()
    if encoder is None and model_info(model)!=status['model']: raise ValueError('嵌入模型版本已變更，請更新索引')
    if len(model.tokenizer(query,add_special_tokens=False)['input_ids'])>model.max_seq_length-4: raise ValueError('問題太長，請分成較短的工程問題')
    q=np.asarray(model.encode([query],normalize_embeddings=True,convert_to_numpy=True))[0]
    if vectors.ndim!=2 or vectors.shape!=(len(rows),len(q)) or not np.isfinite(vectors).all(): raise ValueError('索引向量損壞，請更新索引')
    scores=vectors@q
    matches=[engineering_matches(query,row['chunk_text']) for row in rows]
    ranks=scores+np.array([bonus for bonus,_ in matches]); order=np.argsort(-ranks); results=[]; seen=set()
    for pos in order:
        row=rows[int(pos)]
        if not row['evidence_ids']: continue
        if approved_only and not row['supervised_eligible']: continue
        if category_filter and row['category']!=category_filter: continue
        key=(row['document_id'],row['id'])
        if key in seen: continue
        seen.add(key); results.append({**row,'similarity':round(float(scores[pos]),5),'ranking_score':round(float(ranks[pos]),5),'matched_engineering_terms':matches[int(pos)][1]})
        if len(results)>=limit: break
    return results

def evidence_answer(query,hits):
    if not hits: return '目前範圍沒有可用知識。可先切換為「含待覆核語料」，或更新索引。'
    lines=['問題：'+query,'以下是語意相近的來源段落；相似度不是工程正確率，也不表示零件可互換。']
    for i,h in enumerate(hits[:6],1):
        pages=','.join(str(x) for x in sorted({p['page'] for p in h['locations']})) or '未連結頁面'
        lines.append(f'[{i}] {h["document_name"]} / 第 {pages} 頁 / {"已核准" if h["supervised_eligible"] else "待覆核"}\n{h["text"]}')
    return '\n\n'.join(lines)
