"""Local crop recognition and atomic, evidence-preserving manual review."""
from __future__ import annotations
import hashlib
import json
import math
import re
import shutil
import sys
import time
from pathlib import Path
from PIL import Image
from .bounded import atomic_json, supervise
from .domain import box_valid, clone, evidence, invalidate, now, requirement, uid
from .paths import resolve_path
from .ocr_runtime import ENGINE


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture_dir(root, capture_id):
    if not re.fullmatch(r'capture_[a-f0-9]{16}', capture_id):
        raise ValueError('框選紀錄 ID 無效')
    root = Path(root).resolve()
    folder = (root / 'captures' / capture_id).resolve()
    if not folder.is_relative_to(root):
        raise ValueError('框選檔案必須在目前工作區內')
    return folder


def prepare_capture(doc, root, page_number, box, rotation=0):
    if not box_valid(box) or rotation not in (0, 90, 180, 270):
        raise ValueError('框選座標或旋轉角度無效')
    page = next(p for p in doc['pages'] if p['number'] == page_number)
    source = resolve_path(page['image_path'])
    with Image.open(source) as image:
        w, h = image.size
        pixels = [math.floor(box[0]*w), math.floor(box[1]*h), math.ceil(box[2]*w), math.ceil(box[3]*h)]
        if pixels[2]-pixels[0] < 8 or pixels[3]-pixels[1] < 8:
            raise ValueError('選框太小，請至少包含完整文字或符號（寬高各 8 像素以上）')
        crop = image.crop(pixels).convert('RGB')
    capture_id = uid('capture')
    folder = capture_dir(root, capture_id)
    folder.mkdir(parents=True, exist_ok=False)
    crop.save(folder / 'crop.png')
    crop.rotate(-rotation, expand=True).save(folder / 'input.png')
    request = dict(id=capture_id, document_id=doc['id'], document_version=doc['version'],
        page=page_number, box=box, pixel_box=pixels,
        actual_box=[pixels[0]/w, pixels[1]/h, pixels[2]/w, pixels[3]/h], rotation=rotation,
        created_at=now(), source_sha256=file_hash(source), engine=ENGINE, pipeline_version='crop-v2-upright',
        crop_sha256=file_hash(folder/'crop.png'), input_sha256=file_hash(folder/'input.png'))
    atomic_json(folder / 'request.json', request)
    return request


def classify(text, tokens):
    """Extract printed facts only: numbers are not an estimate of object count."""
    from .quotation import extract_quotation
    from .extraction import dimensions_from_regions
    regions = [evidence(1, t['box'], t['text'], 'crop_ocr') for t in tokens if box_valid(t.get('box'))]
    if not regions and text.strip():
        lines=[line for line in text.splitlines() if line.strip()]
        regions=[evidence(1,[0,i/len(lines),1,(i+.8)/len(lines)],line,'crop_ocr') for i,line in enumerate(lines)]
    candidates = []; seen = set()
    def add(category, label, value, unit='', key='manual.capture'):
        marker = (category, value, unit)
        if value and marker not in seen:
            seen.add(marker); candidates.append(dict(category=category, label=label, value=value, unit=unit, key=key))
    for req in extract_quotation(regions, include_missing=False, detected=True):
        if req['state'] == 'present':
            add('欄位', req['label'], req['value'], req['unit'], req['key'])
    for req in dimensions_from_regions({'regions': regions}):
        add('尺寸／規格', req['label'], req['value'], req['unit'], req['key'])
    for m in re.finditer(r'(?<![\w.])([+-]?\d+(?:[,.]\d+)*)\s*(PCS|EA|件|支|個|組|套)(?![A-Za-z])', text, re.I):
        add('數量', '印刷數量', m[1], m[2], 'quantity')
    for m in re.finditer(r'(?<![\w.])[-+]?\d+(?:[,.]\d+)*(?![\w.])', text):
        add('數值', '數值（用途待核對）', m[0])
    for symbol in dict.fromkeys(c for c in text if c in '⌀ØøΦφ±°×≤≥⊥∥⌖⌭⌯⌰⌒⌔↧↦√'):
        add('符號', '辨識到的符號', symbol, key='manual.symbol')
    add('文字', '框內全部原文', text)
    return candidates


def read_capture(root, request, *, cancelled=lambda: False, budget_seconds=30):
    if not 0 < budget_seconds <= 30: raise ValueError('局部辨識時間上限為 30 秒')
    started = time.monotonic(); folder = capture_dir(root, request['id'])
    if file_hash(folder/'input.png') != request['input_sha256']: raise ValueError('辨識裁圖內容已變更')
    # Cache belongs to this edition's workspace. Store crop-relative boxes only.
    cache_root = (folder.parent / 'cache').resolve()
    if not cache_root.is_relative_to(Path(root).resolve()): raise ValueError('快取路徑不在工作區內')
    cache_root.mkdir(exist_ok=True)
    cache_key = hashlib.sha256((request['input_sha256'] + ENGINE + '/crop-v2-upright').encode()).hexdigest()
    cache = cache_root / (cache_key + '.json')
    raw = None; cached = False
    if cancelled(): status = 'cancelled'
    else:
        if cache.exists():
            try:
                raw = json.loads(cache.read_text(encoding='utf-8'))
                if raw.get('status') != 'complete' or not isinstance(raw.get('tokens'), list): raw = None
            except (ValueError, OSError): raw = None
        if raw is not None: status = 'complete'; cached = True
        else:
            status = supervise([sys.executable, '-m', 'workbench.region_worker', str(folder)],
                cwd=Path(__file__).resolve().parents[1], deadline=started+max(.05, budget_seconds-1.5),
                cancelled=cancelled, log_path=folder/'worker.log')
            if status == 'complete':
                raw = json.loads((folder/'worker_result.json').read_text(encoding='utf-8'))
                status = raw['status']
                if status == 'complete': atomic_json(cache, raw)
    result = dict(raw or {}, status=status, cached=cached, elapsed_seconds=round(time.monotonic()-started, 3),
        deadline_seconds=budget_seconds, engine=ENGINE, capture_id=request['id'])
    result.setdefault('text', ''); result.setdefault('tokens', []); result.setdefault('candidates', [])
    from .extraction import map_box
    for token in result['tokens']:
        token['page_box'] = map_box(token['box'], request['rotation'], request['actual_box'])
    atomic_json(folder/'result.json', result)
    return result


def save_capture(store, doc, request, result, *, text, action='frame', rid=None, value='', unit='',
                 label='框選資訊', key='manual.capture', kind='evidence', replace_id=None,
                 replace_links=False, reason='人工框選截圖與辨識；內容待覆核'):
    if action not in ('frame', 'apply', 'new'): raise ValueError('未知的框選操作')
    folder = capture_dir(store.root, request['id'])
    if request != json.loads((folder/'request.json').read_text(encoding='utf-8')):
        raise ValueError('框選請求內容不一致')
    if request['document_id'] != doc['id'] or request['document_version'] != doc['version']:
        raise ValueError('框選版本不符，請重新框選')
    for name in ('crop', 'input'):
        if file_hash(folder/(name+'.png')) != request[name+'_sha256']: raise ValueError('裁圖已變更，請重新框選')
    page = next(p for p in doc['pages'] if p['number'] == request['page'])
    if file_hash(resolve_path(page['image_path'])) != request['source_sha256']: raise ValueError('原圖已變更，請重新匯入')
    recorded = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    if result != recorded: raise ValueError('辨識紀錄不一致')
    if action in ('apply', 'new') and not value.strip(): raise ValueError('請選擇候選或輸入欄位內容')
    if action == 'new' and not label.strip(): raise ValueError('請填寫新欄位名稱')
    metadata = dict(request, result=recorded, files={name:file_hash(folder/name)
        for name in ('crop.png', 'input.png', 'request.json', 'result.json')})
    new_id = replace_id or uid('ev'); saved_rid = rid
    def change(after):
        nonlocal saved_rid
        req = next((r for r in after['requirements'] if r['id'] == rid and not r.get('deleted')), None)
        if action == 'apply' and req is None: raise ValueError('請先選擇要套用的欄位')
        req_before = clone(req)
        old = next((e for e in after['regions'] if e['id'] == replace_id), None)
        if replace_id and old is None: raise ValueError('原選框已不存在')
        before = clone(old)
        links_before = {r['id']:{k:clone(r.get(k, [])) for k in ('evidence_ids','feature_ids')} for r in after['requirements']}
        region = old or evidence(request['page'], request['box'], source='manual_capture', kind=kind)
        region.update(id=new_id, page=request['page'], box=request['box'], text=text, kind=kind, capture=metadata)
        if old is None: after['regions'].append(region)
        # Preserve existing links and migrate their kind when editing a shared frame.
        affected = set()
        for r in after['requirements']:
            linked = new_id in r['evidence_ids'] + r.get('feature_ids', [])
            if linked:
                for link in ('evidence_ids', 'feature_ids'): r[link] = [e for e in r.get(link, []) if e != new_id]
                r['evidence_ids' if kind == 'evidence' else 'feature_ids'].append(new_id)
                affected.add(r['id'])
        if action == 'new':
            req = requirement(key or 'manual.capture', value, label=label.strip(), unit=unit, origin='manual')
            after['requirements'].append(req); saved_rid = req['id']; req_before = None
        if req is not None and not (action == 'frame' and replace_id):
            link = 'evidence_ids' if kind == 'evidence' else 'feature_ids'
            if replace_links: req[link] = [new_id]
            elif new_id not in req[link]: req[link].append(new_id)
            if action == 'apply': req.update(value=value, unit=unit, state='present')
            affected.add(req['id'])
        invalidate(after, affected)
        return dict(region_id=new_id, requirement_id=saved_rid, before=before, after=clone(region),
            affected_requirement_ids=sorted(affected), requirement_before=req_before, requirement_after=clone(req),
            links_before={r:v for r,v in links_before.items() if r in affected},
            links_after={r['id']:{k:clone(r.get(k, [])) for k in ('evidence_ids','feature_ids')} for r in after['requirements'] if r['id'] in affected},
            capture=metadata, capture_action=action, manual_text=text)
    saved = store.mutate(doc['id'], doc['version'], 'edit_region' if replace_id else 'add_region', reason, change)
    return saved, new_id, saved_rid


def copy_capture_artifacts(root, doc, history, destination):
    """Include accepted current AND historical crops with portable relative paths."""
    captures = {e['capture']['id']:e['capture'] for e in doc['regions'] if e.get('capture')}
    for event in history:
        item = event.get('detail', {}).get('capture')
        if item: captures[item['id']] = item
    for capture_id, metadata in captures.items():
        source = capture_dir(root, capture_id)
        target = Path(destination)/'captures'/capture_id; target.mkdir(parents=True, exist_ok=True)
        for name, expected in metadata['files'].items():
            if name not in ('crop.png', 'input.png', 'request.json', 'result.json'): raise ValueError('框選附件名稱無效')
            path = source/name
            if file_hash(path) != expected: raise ValueError('框選附件缺失或已變更：'+capture_id)
            shutil.copy2(path, target/name)
    return sorted(captures)
