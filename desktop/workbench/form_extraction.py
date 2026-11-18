"""Pair explicit form labels with nearby measured OCR boxes.

The field's label and its value can be separate OCR regions. Keep those
regions separate; their association does not create a new OCR text box.
"""
import re
import unicodedata

from .domain import requirement
from .quotation import FIELDS, label_token, match_label


LABELS = {
    '機械性質/材質': ('mechanical.properties', '機械性質／材質'),
    '機械性質／材質': ('mechanical.properties', '機械性質／材質'),
    '表面處理': ('finish', '表面處理'), '表面处理': ('finish', '表面處理'),
    '數量': ('quantity', '訂單／詢價數量'), '数量': ('quantity', '訂單／詢價數量'),
    '樣品': ('sample.quantity', '樣品數量'), '样品': ('sample.quantity', '樣品數量'),
    '樣品數量': ('sample.quantity', '樣品數量'), 'Sample Qty': ('sample.quantity', '樣品數量'),
    '材質': ('material', '材質'), '材料': ('material', '材質'),
    'material': ('material', '材質'), 'qty': ('quantity', '訂單／詢價數量'),
    '螺紋': ('thread.specification', '螺紋規格'), '螺纹': ('thread.specification', '螺紋規格'),
    'thread': ('thread.specification', '螺紋規格'),
    '包裝要求': ('packaging.instructions', '包裝方式'),
}
_LABELS = {label_token(text): definition for text, definition in LABELS.items()}


def _label(text):
    token = label_token(text).strip('.,。:：')
    if token in _LABELS:
        return _LABELS[token]
    key, _ = match_label(text, tolerant=False)
    return (key, FIELDS[key][0]) if key else None


def _height(region):
    return max(.0001, region['box'][3] - region['box'][1])


def _cy(region):
    return (region['box'][1] + region['box'][3]) / 2


def _same_row(a, b):
    overlap = min(a['box'][3], b['box'][3]) - max(a['box'][1], b['box'][1])
    return overlap >= min(_height(a), _height(b)) * .35 and abs(_cy(a) - _cy(b)) <= max(_height(a), _height(b)) * .65


def _other_field(region):
    """An inline neighboring label ends a cell even when its role is unknown."""
    text = region['text'].strip()
    return bool(re.match(r'[^\d:：]{2,40}[:：]\s*\S', text))


def _anchors(regions):
    """Recognize a whole label or adjacent fragments, never fuzzy roles."""
    ordered = sorted(regions, key=lambda region: (region['box'][1], region['box'][0]))
    proposals = []
    for region in ordered:
        definition = _label(region['text'])
        if definition:
            proposals.append(([region], definition))
        following = sorted((other for other in ordered if other is not region
                            and _same_row(region, other) and other['box'][0] >= region['box'][2] - .002),
                           key=lambda other: other['box'][0])
        parts = [region]
        for other in following[:3]:
            gap = other['box'][0] - parts[-1]['box'][2]
            if gap > max(.012, _height(region) * .8):
                break
            parts.append(other)
            definition = _label(' '.join(part['text'] for part in parts))
            if definition:
                proposals.append((list(parts), definition))
    # "報價" + "單號" belongs to the longer label, not to a price field.
    proposals.sort(key=lambda item: (-len(item[0]), item[0][0]['box'][1], item[0][0]['box'][0]))
    consumed = set(); output = []
    for parts, definition in proposals:
        ids = {part['id'] for part in parts}
        if consumed & ids:
            continue
        consumed |= ids
        box = [min(part['box'][0] for part in parts), min(part['box'][1] for part in parts),
               max(part['box'][2] for part in parts), max(part['box'][3] for part in parts)]
        output.append({'parts': parts, 'box': box, 'definition': definition,
                       'text': ' '.join(part['text'] for part in parts)})
    return sorted(output, key=lambda anchor: (anchor['box'][1], anchor['box'][0]))


def _horizontal(anchor, anchors, regions, label_ids):
    box = anchor['box']; height = _height(anchor)
    peers = [other for other in anchors if other is not anchor and _same_row(anchor, other)
             and other['box'][0] >= box[2] - .002]
    right = min((other['box'][0] for other in peers), default=1)
    candidates = sorted((region for region in regions if region['id'] not in label_ids
                         and _same_row(anchor, region) and region['box'][0] >= box[2] - .003
                         and region['box'][2] <= right + .003), key=lambda region: region['box'][0])
    if not candidates or candidates[0]['box'][0] - box[2] > max(.16, height * 8):
        return []
    selected = []
    edge = box[2]
    for region in candidates:
        if _other_field(region):
            break
        if selected and region['box'][0] - edge > max(.035, height * 2.5):
            break
        selected.append(region); edge = region['box'][2]
    return selected


def _below(anchor, anchors, regions, label_ids):
    box = anchor['box']; height = _height(anchor)
    peers = sorted((other for other in anchors if _same_row(anchor, other)), key=lambda other: other['box'][0])
    pos = peers.index(anchor)
    if len(peers) == 1 and anchor['definition'][0] == 'quotation.price':
        # An isolated "報價" / "Unit Price" heading is not an empty price
        # cell whose value can be borrowed from a later row.
        return []
    left = (peers[pos-1]['box'][2] + box[0]) / 2 if pos else max(0, box[0] - .04)
    right = (box[2] + peers[pos+1]['box'][0]) / 2 if pos+1 < len(peers) else min(1, box[2] + .04)
    if len(peers) == 1:
        # One label does not define an entire page column.
        left = max(0, box[0] - .01); right = min(1, box[2] + .08)
    next_label = min((other['box'][1] for other in anchors if other is not anchor
                      and other['box'][1] > box[3] and left <= (other['box'][0]+other['box'][2])/2 <= right), default=1)
    bottom = min(next_label, box[3] + max(.035, height * 2.5))
    candidates = sorted((region for region in regions if region['id'] not in label_ids
                         and left <= (region['box'][0] + region['box'][2]) / 2 <= right
                         and region['box'][1] >= box[3] - .002 and _cy(region) <= bottom),
                        key=lambda region: (region['box'][1], region['box'][0]))
    if not candidates:
        return []
    first = candidates[0]
    if _other_field(first):
        return []
    if len(peers) == 1 and abs(first['box'][0] - box[0]) > max(.035, height * 1.2):
        return []
    return sorted((region for region in candidates if _same_row(first, region)), key=lambda region: region['box'][0])


def extract_form_fields(regions, page):
    """Return grounded requirements and used IDs; absence remains absence.

    Only whole explicit labels are recognized. Enquiry dates and recipients
    do not acquire quotation roles because their values look similar.
    """
    regions = [region for region in regions if region.get('page', page) == page
               and region.get('kind', 'evidence') == 'evidence' and region.get('text', '').strip()]
    anchors = _anchors(regions)
    label_ids = {part['id'] for anchor in anchors for part in anchor['parts']}
    output = []; used = set(); value_owners = {}
    for anchor in anchors:
        key, label = anchor['definition']
        selected = _horizontal(anchor, anchors, regions, label_ids)
        direction = 'same_row'
        if not selected:
            selected = _below(anchor, anchors, regions, label_ids); direction = 'same_column'
        if not selected or any(part['id'] in value_owners for part in selected):
            continue
        raw_value = ' '.join(part['text'] for part in selected).strip()
        value = raw_value.lstrip(' :：'); unit = ''
        if not value or _label(value):
            continue
        if key in ('quantity', 'quotation.quantity', 'sample.quantity'):
            match = re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(M|K|PCS|EA|件|支|個|个|顆|颗)?',
                                 unicodedata.normalize('NFKC', value), re.I)
            if not match:
                continue
            value = match[1].replace(',', ''); unit = match[2] or ''
        if key == 'mechanical.properties' and re.fullmatch(r'\d{1,2}\.\d\s*[級级]', value):
            key = 'strength.class'; label = '強度等級'
        ids = [part['id'] for part in anchor['parts']] + [part['id'] for part in selected]
        raw_text = '\n'.join(part['text'] for part in anchor['parts'] + selected)
        from .quotation_validation import value_issue
        if value_issue(key, value, raw_text, unit):
            continue
        if key in ('quotation.date', 'quotation.valid_until'):
            from datetime import date
            match = re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?', value)
            try:
                value = date(*map(int, match.groups())).isoformat() if match else ''
            except ValueError:
                value = ''
            if not value:
                continue
        if key == 'quotation.price':
            # Reuse currency/denominator parsing; the temporary joined string
            # is parser input and is never saved as an OCR region.
            from .quotation import extract_quotation
            parsed = extract_quotation([{'id': ids[0], 'page': page, 'box': anchor['box'],
                                         'text': anchor['text'].rstrip(' :：') + '：' + value}],
                                        detected=True, include_missing=False, tolerant=False)
            price = next((item for item in parsed if item['key'] == key), None)
            if not price or price['state'] != 'present':
                continue
            value = price['value']; unit = price['unit']
        used.update(ids)
        value_owners.update({part['id']: key for part in selected})
        record = requirement(key, value, label=label, unit=unit, scope=f'page:{page}', evidence_ids=ids,
                             origin='spatial_form', raw_value=raw_value,
                             extraction_notes=['依原始 OCR 標籤與相鄰值的框位配對；保留兩者原文與來源'])
        record['spatial_pairing'] = {'direction': direction, 'label_ids': [part['id'] for part in anchor['parts']],
                                     'value_ids': [part['id'] for part in selected],
                                     'method': 'explicit-label-neighbor/0.8.0'}
        record['candidates'] = [{'value': raw_value, 'source': 'spatial-label-value', 'raw_text': raw_text}]
        output.append(record)
    return output, used
