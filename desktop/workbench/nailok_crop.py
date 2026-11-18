"""Read a user-selected quotation field locally and retain measured OCR evidence."""
from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image

from .bounded import atomic_json, supervise
from .domain import box_valid, evidence, now, requirement, uid
from .ocr_engines import BACKENDS
from .paths import resolve_path
from .quotation import FIELDS, MULTILINE, extract_quotation, grouped_lines, is_quotation, segments
from .quotation_layout import equivalent_unit, equivalent_value
from .quotation_validation import value_issue
from .region_capture import capture_dir, file_hash, prepare_capture, read_capture


RAPID_BACKEND = 'rapid_v6'
MINERU_BACKEND = 'mineru_basic'
MINERU_BUDGET = 45


def field_identity(key, value, unit):
    return equivalent_value(key, value), equivalent_unit(unit)


def split_label_rows(key, regions):
    """Recover one printed label split at an overlapping delimiter."""
    if len(regions) < 2:
        return []
    parts = sorted(regions, key=lambda part: part['box'][0])
    boxes = [part['box'] for part in parts]
    centers = [(box[1] + box[3]) / 2 for box in boxes]
    if max(centers) - min(centers) >= .4 * min(box[3] - box[1] for box in boxes):
        return []
    for left, right in zip(parts, parts[1:]):
        if right['box'][0] < left['box'][2] and not right['text'].lstrip().startswith((':', '：')):
            return []
    text = ' '.join(part['text'] for part in parts)
    if [field for field, _, _ in segments(text)] != [key]:
        return []
    records = extract_quotation([{**parts[0], 'text': text}], include_missing=False,
                                detected=True, tolerant=False)
    for record in records:
        record['evidence_ids'] = [part['id'] for part in parts]
    return records


def field_rows(key, regions):
    """Interpret only the selected field. Every returned ID belongs to measured text."""
    from .nailok_flow import CONTACT_NUMBER, annual_quantity

    records = extract_quotation(regions, include_missing=False, detected=True, tolerant=is_quotation(regions))
    matches = [row for row in records if row['key'] == key]
    if not any(row['state'] == 'present' for row in matches):
        repaired = split_label_rows(key, regions)
        if repaired:
            matches = [row for row in repaired if row['key'] == key]
    if not records and regions:
        lines = grouped_lines(regions)
        raw = '\n'.join(line['text'] for line in lines)
        if not matches and (key in MULTILINE or len(raw.splitlines()) == 1):
            # Supply the operator's selected role while parsing an unlabelled
            # value. The temporary context is never written to an OCR source.
            printed_label = FIELDS[key][1].split('|', 1)[0]
            context = {**regions[0], 'text': f'{printed_label}：{raw}'}
            matches = [row for row in extract_quotation([context], include_missing=False, detected=True,
                                                       tolerant=False) if row['key'] == key]
            for row in matches:
                row['evidence_ids'] = [region['id'] for region in regions]
    recognized = {region['id']: region for region in regions}
    rows = []
    for row in matches:
        value, unit = row['value'], row['unit']
        if row['state'] != 'present' or not value.strip():
            continue
        raw = '\n'.join(recognized[eid]['text'] for eid in row['evidence_ids'])
        if value_issue(key, value, raw, unit, check_role=False):
            continue
        if key == 'quotation.quantity' and annual_quantity(raw, value, unit):
            continue
        if key in ('contact.phone', 'contact.fax') and (
            not CONTACT_NUMBER.fullmatch(value) or len(re.findall(r'\d', value)) < 6
        ):
            continue
        rows.append({
            'key': key, 'value': value, 'unit': unit, 'state': 'present',
            'evidence_ids': list(dict.fromkeys(row['evidence_ids'])), 'candidate_id': None,
            'issues': list(row['extraction_notes']),
        })
    return rows


def draft_from_regions(doc, key, ids):
    """Use a measured source the operator explicitly associated with this label."""
    from .nailok_flow import annotation_text, is_source_region

    if key not in FIELDS:
        raise ValueError('請先選擇報價欄位')
    selected = set(ids)
    measured = defaultdict(list)
    for region in doc['regions']:
        if region['id'] in selected and is_source_region(region):
            measured[region['source']].append({**region, 'text': annotation_text(doc, region)})
    options = [row for regions in measured.values() for row in field_rows(key, regions)]
    distinct = {field_identity(key, row['value'], row['unit']) for row in options}
    return options[0] if len(distinct) == 1 else None


def read_mineru(folder, *, cancelled=lambda: False):
    """Apply the application's subprocess bound to the model and its child processes."""
    from .mineru_runtime import local_settings

    local_settings()
    started = time.monotonic()
    cutoff = started + MINERU_BUDGET
    status = supervise(
        [sys.executable, '-m', 'workbench.nailok_crop', str(folder), str(cutoff)],
        cwd=Path(__file__).resolve().parents[1], deadline=cutoff, cancelled=cancelled,
        log_path=folder / 'mineru-worker.log',
    )
    measured = {}
    if status == 'complete':
        measured = json.loads((folder / 'mineru-result.json').read_text(encoding='utf-8'))
        status = measured['status']
    return {
        **measured, 'status': status, 'engine': BACKENDS[MINERU_BACKEND],
        'elapsed_seconds': round(time.monotonic() - started, 3), 'deadline_seconds': MINERU_BUDGET,
    }


def mineru_worker(folder, cutoff):
    from .ingest import LEGACY
    from .ocr_engines import recognize

    if str(LEGACY) not in sys.path:
        sys.path.insert(0, str(LEGACY))
    try:
        with Image.open(folder / 'input.png') as image:
            result = recognize(image.convert('RGB'), MINERU_BACKEND, deadline=cutoff)
            width, height = image.size
    except TimeoutError as exc:
        atomic_json(folder / 'mineru-result.json', {'status': 'partial_timeout', 'error': str(exc)})
        return
    except (OSError, ValueError, RuntimeError) as exc:
        atomic_json(folder / 'mineru-result.json', {'status': 'failed', 'error': str(exc)})
        return
    tokens = []
    for region in result.regions:
        box = [max(0, min(1, float(value) / scale))
               for value, scale in zip(region.box, (width, height, width, height))]
        if box_valid(box) and region.text.strip():
            token={
                'text': region.text, 'box': box, 'source': region.source, 'confidence': region.confidence,
            }
            if getattr(region,'layout_type',None):
                token['layout_type']=region.layout_type
            tokens.append(token)
    atomic_json(folder / 'mineru-result.json', {
        'status': 'complete' if tokens else 'empty',
        'text': result.text, 'tokens': tokens, 'warnings': result.warnings,
    })


def read_methods(root, folder, request, progress, cancelled):
    method_specs = (
        (RAPID_BACKEND, lambda: read_capture(root, request, cancelled=cancelled)),
        (MINERU_BACKEND, lambda: read_mineru(folder, cancelled=cancelled)),
    )
    for backend, read in method_specs:
        if cancelled():
            break
        progress(f'第 {request["page"]} 頁框選辨識：{BACKENDS[backend]}')
        try:
            result = read()
        except (OSError, ValueError, RuntimeError) as exc:
            result = {'status': 'failed', 'error': str(exc)}
        yield backend, {'engine': BACKENDS[backend], 'tokens': [], 'text': '', **result}


def capture_field(store, doc, page_number, box, key, *, rotation=0,
                  progress=lambda _: None, cancelled=lambda: False):
    """Read, stage and save a crop in one version-checked document mutation."""
    from .extraction import map_box
    from .nailok_flow import LABELS, stage_label_draft

    if key not in FIELDS:
        raise ValueError('請先選擇報價欄位')
    response = {'document': doc, 'draft': None, 'alternatives': [], 'statuses': {},
                'conflict': False, 'frame_id': None, 'cancelled': False}
    if cancelled():
        return {**response, 'cancelled': True}
    page = next((item for item in doc['pages'] if item['number'] == page_number), None)
    if page is None:
        raise ValueError('找不到所選的圖面頁次')
    request = prepare_capture(doc, store.root, page_number, box, rotation)
    folder = capture_dir(store.root, request['id'])
    request['quotation_field'] = key
    request['pipeline_version'] = 'quote-label-local-dual-v1'
    atomic_json(folder / 'request.json', request)
    runs = []
    regions = []
    requirements = []
    options = {}
    readings = []
    methods = list(read_methods(store.root, folder, request, progress, cancelled))
    if cancelled() or any(result['status'] == 'cancelled' for _, result in methods):
        return {**response, 'cancelled': True}
    for backend, result in methods:
        local = []
        for item in result['tokens']:
            token_box, text = item.get('box'), item.get('text', '')
            if not box_valid(token_box) or not isinstance(text, str) or not text.strip():
                continue
            mapped = map_box(token_box, request['rotation'], request['actual_box'])
            source = evidence(page_number, mapped, text, item.get('source') or result['engine'])
            source['capture_id'] = request['id']
            source['ocr_confidence'] = item.get('confidence')
            if item.get('layout_type'):
                source['layout_type'] = item['layout_type']
            regions.append(source)
            local.append({**source, 'box': token_box})
            readings.append({**item, 'page_box': mapped})
        status = result['status']
        if status == 'complete' and not local:
            status = 'empty'
        response['statuses'][backend] = status
        choices = field_rows(key, local)
        runs.append({
            'id': uid('tool'), 'type': 'label_crop', 'at': now(),
            'page': page_number, 'crop': request['actual_box'], 'rotation_clockwise': rotation,
            'status': status, 'requested_ocr': backend, 'ocr_engine': result['engine'],
            'elapsed_seconds': result.get('elapsed_seconds'),
            'stop_reason': result.get('error'), 'target_field': key, 'capture_id': request['id'],
            'evidence_ids': [region['id'] for region in local],
        })
        for choice in choices:
            identity = field_identity(key, choice['value'], choice['unit'])
            if identity in options:
                continue
            record = requirement(key, choice['value'], label=LABELS[key], unit=choice['unit'],
                                 evidence_ids=choice['evidence_ids'], scope='nailok:crop:'+request['id'],
                                 origin='nailok_crop', nailok_crop_id=request['id'])
            requirements.append(record)
            options[identity] = {**choice, 'id': record['id'], 'candidate_id': record['id'],
                                 'engine': result['engine'], 'pages': [page_number]}
    choices = list(options.values())
    draft = choices[0] if choices else None
    if draft is None:
        # Even if both engines miss the characters, the exact operator-drawn
        # frame can anchor later manual transcription. It has no inferred text.
        frame = evidence(page_number, request['actual_box'], '', 'manual_capture')
        frame['capture_id'] = request['id']
        regions.append(frame)
        response['frame_id'] = frame['id']
    totals = {
        'status': 'complete' if readings else 'empty', 'capture_id': request['id'],
        'text': '\n'.join(result['text'] for _, result in methods if result['text']),
        'tokens': readings, 'candidates': [{'category': '欄位', 'label': LABELS[key], **row} for row in choices],
        'methods': response['statuses'], 'elapsed_seconds': round(sum(
            result.get('elapsed_seconds') or 0 for _, result in methods
        ), 3), 'deadline_seconds': 30 + MINERU_BUDGET,
        'engine': ' ＋ '.join(result['engine'] for _, result in methods),
    }
    atomic_json(folder / 'result.json', totals)
    if file_hash(resolve_path(page['image_path'])) != request['source_sha256']:
        raise ValueError('框選來源已變更，請重新載入原圖')
    if any(file_hash(folder / (name + '.png')) != request[name + '_sha256'] for name in ('crop', 'input')):
        raise ValueError('框選裁圖已變更，請重新框選')
    metadata = {
        **request, 'engine': totals['engine'], 'result': totals,
        'files': {name: file_hash(folder / name)
                  for name in ('crop.png', 'input.png', 'request.json', 'result.json')},
    }
    regions[0]['capture'] = metadata
    if len(choices) > 1:
        draft['issues'] = [*draft['issues'], '各辨識方法讀值不同，請核對原圖']
    if cancelled():
        return {**response, 'cancelled': True, 'frame_id': None}

    def change(after):
        after['regions'].extend(regions)
        after['requirements'].extend(requirements)
        after['tool_runs'].extend(runs)
        if draft:
            stage_label_draft(after, draft)
        return {
            'field': key, 'region_ids': [region['id'] for region in regions],
            'requirement_ids': [row['id'] for row in requirements],
            'statuses': response['statuses'], 'capture': metadata,
        }

    updated = store.mutate(doc['id'], doc['version'], 'nailok_crop', '框選辨識並回填待核准報價欄位', change)
    return {**response, 'document': updated, 'draft': draft, 'alternatives': choices, 'conflict': len(choices) > 1}


if __name__ == '__main__':
    mineru_worker(Path(sys.argv[1]), float(sys.argv[2]))
