"""Read-only, source-preserving quotation/drawing skill contracts.

Rules derive from the supplied quote-drawing-extraction skill.  Typed parsing
and extraction are proposals; they never attest a human review or change a
document, its OCR text, its mappings, or its model responses.
"""
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal

from .domain import box_valid, canonical, clone, digest
from .quotation import FIELDS, LABEL_MAP, MULTILINE, grouped_lines, label_token


SCHEMA = 'drawiq-quote-skill/1'
METHOD = 'quote-drawing-extraction/0.9.0'
_LOCAL_LABEL_MAP = str.maketrans({'级': '級', '层': '層', '图': '圖', '号': '號', '纹': '紋'})


def _label_token(value):
    return label_token(value.translate(_LOCAL_LABEL_MAP))
EXTRA_ALIASES = {
    'adhesive.product': ('報價級別', '塗層級別'),
    'coating.method': ('塗覆方式',),
    'packaging.instructions': ('包裝要求',),
    'product.specification': ('產品規格', '螺紋規格', '螺紋', 'Thread'),
    'drawing_number': ('圖號', '圖面編號', 'Drawing No', 'Drawing Number'),
    'part_number': ('零件號', '品號', '外包件號', 'Part No', 'Part Number'),
}
LABELS = {key: label for key, (label, _) in FIELDS.items()}
LABELS.update(drawing_number='圖號', part_number='零件號')
ALIASES = {}
for _key, (_label, _aliases) in FIELDS.items():
    for _alias in _aliases.split('|'):
        ALIASES[_label_token(_alias)] = _key
for _key, _aliases in EXTRA_ALIASES.items():
    for _alias in _aliases:
        ALIASES[_label_token(_alias)] = _key


def _unit(unit):
    if unit is None:
        return None
    text = str(unit).strip().casefold()
    return {'mm': 'mm', '毫米': 'mm', 'cm': 'cm', '公分': 'cm',
            'in': 'inch', 'inch': 'inch', 'inches': 'inch', '英吋': 'inch',
            '"': 'inch', '″': 'inch'}.get(text)


def parse_spec(value, *, document_type='unknown', length_unit=None):
    """Parse printed size syntax without inventing pitch, length, or units."""
    if not isinstance(value, str) or not value.strip():
        return None, ['unparsed_spec']
    source = unicodedata.normalize('NFKC', value).strip()
    if re.search(r'\d\s+\d', source) and not re.match(r'^\d+\s+\d+\s*/\s*\d+', source):
        return None, ['separated_numeric_tokens']
    mixed = re.match(r'^(\d+)\s+(\d+)\s*/\s*(\d+)', source)
    if mixed:
        whole, numerator, denominator = map(int, mixed.groups())
        if not denominator:
            return None, ['invalid_spec_dimension']
        source = f'{whole*denominator+numerator}/{denominator}' + source[mixed.end():]
    token = re.sub(r'\s+', '', source).replace('×', 'x').replace('＊', 'x').replace('*', 'x')
    token = token.replace('−', '-').replace('–', '-')
    printed_unit = None
    unit_match = re.search(r'(mm|cm|inch(?:es)?|in|毫米|公分|英吋|["″])$', token, re.I)
    if unit_match:
        printed_unit = _unit(unit_match[0]); token = token[:unit_match.start()]
    declared_unit = _unit(length_unit)
    if length_unit is not None and declared_unit is None:
        return None, ['invalid_length_unit']
    if printed_unit and declared_unit and printed_unit != declared_unit:
        return None, ['length_unit_conflict']
    verified_unit = printed_unit or declared_unit
    number = r'\d+(?:[.,]\d+)?'
    metric = re.fullmatch(r'M(?P<diameter>'+number+r')(?:x(?P<pitch>'+number+r'))?'
                          r'(?:x(?P<length>'+number+r'))?(?:-(?P<tolerance>\d+[a-zA-Z]+))?', token, re.I)
    def numeric(text):
        return float(Decimal(text.replace(',', '.'))) if text is not None else None
    if metric:
        parts = metric.groupdict()
        diameter = numeric(parts['diameter']); pitch = numeric(parts['pitch']); length = numeric(parts['length'])
        if any(number is not None and (not math.isfinite(number) or number <= 0) for number in (diameter, pitch, length)):
            return None, ['invalid_spec_dimension']
        issues = []
        basis = 'printed' if printed_unit else 'declared_context' if declared_unit else None
        if length is not None and verified_unit is None:
            if document_type == 'quote_label':
                verified_unit = 'mm'; basis = 'quote_label_convention'
            else:
                issues.append('length_unit_unverified')
        if pitch is not None and pitch >= diameter:
            issues.append('pitch_or_length_ambiguous')
        length_mm = length * {'mm': 1, 'cm': 10}.get(verified_unit, 0) if length is not None and verified_unit in ('mm', 'cm') else None
        return {'system': 'metric', 'diameter_mm': diameter, 'pitch_mm': pitch,
                'length_mm': length_mm, 'length_value': length, 'length_unit': verified_unit if length is not None else None,
                'length_unit_basis': basis if length is not None else None,
                'tolerance': parts['tolerance'], 'normalized': token,
                'normalization_notes': ['decimal_comma_numeric_conversion'] if ',' in token else []}, issues
    unified = re.fullmatch(r'(?P<diameter>#\d+|\d+(?:/\d+)?(?:[.,]\d+)?)-(?P<tpi>\d+)'
                           r'(?P<series>UNC|UNF|UNEF)(?:-(?P<tolerance>[123][AaBb]))?'
                           r'(?:x(?P<length>'+number+r'))?', token, re.I)
    if unified:
        parts = unified.groupdict(); diameter_raw = parts['diameter']; issues = []
        if diameter_raw.startswith('#'):
            diameter = None; issues.append('numbered_size_diameter_not_resolved')
        elif '/' in diameter_raw:
            numerator, denominator = map(int, diameter_raw.split('/'))
            if not denominator:
                return None, ['invalid_spec_dimension']
            diameter = numerator/denominator
        else:
            diameter = numeric(diameter_raw)
        tpi = int(parts['tpi']); length = numeric(parts['length'])
        if not tpi or (diameter is not None and diameter <= 0) or (length is not None and length <= 0):
            return None, ['invalid_spec_dimension']
        if length is not None and verified_unit is None:
            issues.append('length_unit_unverified')
        return {'system': 'unified', 'diameter_inch': diameter, 'size_designation': diameter_raw,
                'tpi': tpi, 'series': parts['series'].upper(), 'tolerance': parts['tolerance'],
                'length_value': length, 'length_unit': verified_unit if length is not None else None,
                'length_unit_basis': ('printed' if printed_unit else 'declared_context' if declared_unit else None) if length is not None else None,
                'normalized': token}, issues
    return None, ['unparsed_spec']


def _alias_pattern(alias):
    translated = {**LABEL_MAP, **_LOCAL_LABEL_MAP}
    characters = []
    for char in alias:
        variants = {char}
        variants.update(chr(original) for original, target in translated.items()
                        if (chr(target) if isinstance(target, int) else target) == char)
        if char.isascii() and char.isalnum():
            variants.add(chr(ord(char)+0xFEE0))
        characters.append('[' + ''.join(re.escape(c) for c in sorted(variants)) + ']')
    return r'\s*'.join(characters)


_ANCHOR_PATTERN = re.compile(r'(?<![A-Za-z0-9\u3400-\u9fff])(?P<label>' + '|'.join(
    _alias_pattern(alias) for alias in sorted(ALIASES, key=len, reverse=True)) + r')(?=[:：\s]|$)', re.I)


def _anchors(text):
    result = []
    for match in _ANCHOR_PATTERN.finditer(text):
        tail = match.end()
        cursor = tail
        while cursor < len(text) and text[cursor] in ' \t':
            cursor += 1
        colon = cursor < len(text) and text[cursor] in ':：'
        # Bare labels may start a row, but an ordinary alias word inside a
        # value (for example "Material Handling LLC") is not another label.
        # Separate bare cells are resolved from their measured boxes below.
        if not colon and text[:match.start()].strip():
            continue
        start_value = cursor+1 if colon else tail
        result.append({'key': ALIASES[_label_token(match['label'])], 'start': match.start(),
                       'value_start': start_value, 'label': match['label'], 'colon': colon})
    return result


def _confidence(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return None


def _source(region, pages):
    page = region.get('page', 1)
    box = region.get('box')
    issues = []
    if not box_valid(box):
        box = None; issues.append('missing_bbox')
    if page not in pages:
        issues.append('missing_page')
    confidence = _confidence(region.get('ocr_confidence', region.get('confidence')))
    if confidence is None:
        issues.append('missing_confidence')
    elif confidence < .8:
        issues.append('low_confidence')
    method = region.get('source', region.get('engine', 'provided_ocr'))
    if not isinstance(method, str) or not method.strip():
        issues.append('missing_method')
    if (isinstance(method, str) and method.casefold().startswith('vlm:')) or region.get('model_generated'):
        issues.extend(['unverified_model_source', 'generated_model_evidence'])
    source = {'region_id': region['id'], 'page': page, 'box': clone(box), 'text': region.get('text', ''),
              'method': method, 'confidence': confidence}
    if 'pixel_bbox_raw' in region or 'source_bbox' in region:
        source['pixel_bbox_raw'] = clone(region.get('pixel_bbox_raw', region.get('source_bbox')))
    if 'source_contract' in region:
        source['source_contract'] = clone(region['source_contract'])
    return source, issues


def _regions(doc):
    """Keep measured, unlocated, and externally provided OCR provenance."""
    output = [clone(region) for region in doc.get('regions', []) if region.get('kind', 'evidence') == 'evidence']
    ids = {region['id'] for region in output}
    for region in doc.get('unlocated_ocr', []):
        if region.get('id') not in ids:
            output.append(clone(region)); ids.add(region.get('id'))
    for page in doc.get('pages', []):
        supplied_page = page.get('provided_ocr_page', {})
        lines = page.get('provided_ocr_lines', supplied_page.get('lines', []))
        for index, line in enumerate(lines):
            region_id = line.get('region_id', line.get('id')) or f'provided_ocr:{page.get("number",1)}:{index}'
            if region_id in ids:
                existing = next(region for region in output if region['id'] == region_id)
                if line.get('pixel_bbox_raw', line.get('pixel_bbox', line.get('bbox'))) is not None:
                    existing['pixel_bbox_raw'] = line.get('pixel_bbox_raw', line.get('pixel_bbox', line.get('bbox')))
                continue
            width = page.get('width', supplied_page.get('width')); height = page.get('height', supplied_page.get('height'))
            pixel_box = line.get('pixel_bbox_raw', line.get('pixel_bbox', line.get('bbox')))
            box = None
            if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
                if isinstance(pixel_box, list) and len(pixel_box) == 4 and all(isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in pixel_box):
                    candidate = [pixel_box[0]/width, pixel_box[1]/height, pixel_box[2]/width, pixel_box[3]/height]
                    if box_valid(candidate):
                        box = candidate
            output.append({'id': region_id, 'page': page.get('number', 1), 'box': box,
                           'text': line.get('text', ''), 'source': line.get('source', line.get('engine', 'provided_ocr')),
                           'ocr_confidence': line.get('ocr_confidence', line.get('confidence')), 'pixel_bbox_raw': pixel_box})
            ids.add(region_id)
    return output


def _lines(regions):
    measured = [region for region in regions if box_valid(region.get('box'))]
    lines = []
    for baseline in grouped_lines(measured):
        groups = []
        for part in baseline['parts']:
            if groups:
                prior = groups[-1][-1]
                gap = part['box'][0]-prior['box'][2]
                height = min(part['box'][3]-part['box'][1], prior['box'][3]-prior['box'][1])
                if gap <= max(.05, height*3):
                    groups[-1].append(part)
                    continue
            groups.append([part])
        for parts in groups:
            # A coincident baseline does not make distant columns one cell.
            # Table label/value cells beyond this bound still have the
            # measured-neighbor resolver, with label and value provenance.
            lines.append({'page': baseline['page'], 'parts': parts,
                          'text': ' '.join(part['text'] for part in parts), 'ids': [part['id'] for part in parts],
                          'cy': sum((part['box'][1]+part['box'][3])/2 for part in parts)/len(parts),
                          'height': min(part['box'][3]-part['box'][1] for part in parts)})
    for line in lines:
        # grouped_lines preserves part spans; use exact raw text lengths,
        # because NFKC can change character counts in source values.
        cursor = 0; spans = []
        for part in line['parts']:
            spans.append((cursor, cursor+len(part['text']), part['id'])); cursor = spans[-1][1]+1
        line['spans'] = spans
    for region in regions:
        if not box_valid(region.get('box')):
            lines.append({'page': region.get('page', 1), 'cy': None, 'height': None, 'parts': [region],
                          'text': region.get('text', ''), 'ids': [region['id']],
                          'spans': [(0, len(region.get('text', '')), region['id'])]})
    return lines


def _document_type(doc, lines):
    explicit = doc.get('document_type')
    if explicit in ('quote_label', 'engineering_drawing'):
        return explicit
    kinds = {page.get('document_kind') for page in doc.get('pages', [])}
    if 'quotation' in kinds or 'quote_label' in kinds:
        return 'quote_label'
    keys = {anchor['key'] for line in lines for anchor in _anchors(line['text'])}
    if 'quotation.number' in keys or len(keys & {'quotation.date', 'quotation.valid_until', 'quotation.salesperson'}) >= 2:
        return 'quote_label'
    text = '\n'.join(line['text'] for line in lines)
    if 'engineering_drawing' in kinds or re.search(r'公差|螺[紋纹絲丝]|表面處理|圖號|圖面編號|\b(?:THREAD|THREADLOCK|DIN|ISO|DATUM|TOLERANCE)\b', text, re.I):
        return 'engineering_drawing'
    return 'unknown'


def _normalize(key, raw, dtype, *, length_unit=None, label=''):
    value = raw.strip()
    if not value:
        return None, ['missing_value']
    if key in ('quotation.date', 'quotation.valid_until'):
        match = re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?', unicodedata.normalize('NFKC', value))
        try:
            return date(*map(int, match.groups())).isoformat(), [] if match else ['invalid_date']
        except (ValueError, AttributeError):
            return None, ['invalid_date']
    if key == 'quotation.number':
        return value, [] if re.fullmatch(r'KH\d{8}', value) else ['unusual_quote_no']
    if key == 'product.specification':
        return parse_spec(value, document_type=dtype, length_unit=length_unit)
    if key == 'quotation.quantity':
        match = re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*([A-Za-z\u3400-\u9fff]*)', value)
        if not match:
            return None, ['unparsed_quantity']
        number = Decimal(match[1].replace(',', '')); unit = match[2] or None
        return {'value': int(number) if number == int(number) else float(number), 'unit_raw': unit}, (
            ['quantity_unit_ambiguous'] if unit and unit.upper() in ('M', 'K') else ['quantity_unit_missing'] if unit is None else [])
    if key == 'quotation.price':
        value = unicodedata.normalize('NFKC', value)
        match = re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:/?\s*(TWD|NTD|USD|EUR|CNY|JPY|元))?\s*(?:/\s*([A-Za-z\u3400-\u9fff]+))?', value, re.I)
        if not match:
            return None, ['blank_price'] if not re.search(r'\d', value) else ['unparsed_price']
        return {'amount': float(Decimal(match[1].replace(',', ''))), 'currency': match[2].upper() if match[2] else None,
                'denominator_raw': match[3] or None}, [] if match[2] else ['price_currency_missing']
    if key == 'adhesive.product':
        match = re.fullmatch(r'(precoat|precote)\s+([\d]+(?:-[\d]+)*)\s*(?:[（(]([^()（）]+)[)）])?', value, re.I)
        if match:
            return {'family': match[1], 'code': match[2], 'color_note': match[3]}, []
        if _label_token(label) in {_label_token('報價級別'), _label_token('塗層級別')}:
            return value, ['unparsed_grade']
    return value, []


def _record(key, raw, ids, source_map, source_issues, dtype, *, label='', extra_issues=(), length_unit=None):
    sources = [clone(source_map[region_id]) for region_id in dict.fromkeys(ids) if region_id in source_map]
    normalized, errors = _normalize(key, raw, dtype, length_unit=length_unit, label=label) if raw is not None else (None, ['missing_value'])
    errors.extend(extra_issues)
    errors.extend(issue for source in sources for issue in source_issues[source['region_id']])
    if dtype == 'unknown':
        errors.append('unknown_document')
    if raw is not None and normalized is not None:
        from .quotation_validation import value_issue
        role_issue = value_issue(key, raw.strip(), '\n'.join(source['text'] for source in sources), check_role=False)
        if role_issue:
            errors.append('invalid_field_role')
    errors = list(dict.fromkeys(errors))
    scores = [source['confidence'] for source in sources]
    confidence = min(scores) if scores and all(score is not None for score in scores) else None
    missing = normalized is None and all(error in ('blank_price', 'missing_value', 'missing_confidence', 'low_confidence', 'unknown_document', 'missing_bbox', 'missing_page') for error in errors)
    return {'key': key, 'label': LABELS[key], 'raw': raw, 'normalized': normalized,
            'source': sources[0] if sources else None, 'sources': sources,
            'confidence': confidence, 'status': 'missing' if missing else 'needs_review' if errors else 'extracted',
            'issues': errors, 'alternatives': []}


def _aggregate(records, key):
    if not records:
        return {'key': key, 'label': LABELS[key], 'raw': None, 'normalized': None, 'source': None,
                'sources': [], 'confidence': None, 'status': 'missing', 'issues': ['missing_value'], 'alternatives': []}
    sources = list({source['region_id']: source for record in records for source in record['sources']}.values())
    def identity_of(record):
        value = record['normalized']
        if value is None:
            return canonical(record['raw'] or '')
        if key == 'product.specification' and isinstance(value, dict):
            # Printed notation and conversion notes are provenance, not a
            # dimensional disagreement. Tolerance case remains semantic.
            value = {name: item for name, item in value.items()
                     if name not in ('normalized', 'normalization_notes', 'length_unit_basis')}
            if not str(value.get('size_designation', '')).startswith('#'):
                value.pop('size_designation', None)
        return digest(value)
    identity = {identity_of(record) for record in records}
    errors = list(dict.fromkeys(issue for record in records for issue in record['issues']))
    if len(identity) > 1:
        scores = [source['confidence'] for source in sources]
        return {'key': key, 'label': LABELS[key], 'raw': None, 'normalized': None, 'source': None, 'sources': sources,
                'confidence': min(scores) if scores and all(score is not None for score in scores) else None,
                'status': 'conflict', 'issues': list(dict.fromkeys([*errors, 'conflict'])), 'alternatives': records}
    result = clone(records[0]); result['sources'] = sources; result['source'] = sources[0] if sources else None
    result['issues'] = errors
    result['alternatives'] = records if len(records) > 1 else []
    scores = [source['confidence'] for source in sources]
    result['confidence'] = min(scores) if scores and all(score is not None for score in scores) else None
    if result['status'] != 'missing' and errors:
        result['status'] = 'needs_review'
    return result


def analyze(doc):
    """Produce typed fields, all alternatives, and an inspectable review queue."""
    regions = _regions(doc); region_map = {region['id']: region for region in regions}
    pages = {page.get('number', 1) for page in doc.get('pages', [])}
    source_map = {}; source_issues = {}
    for region in regions:
        source_map[region['id']], source_issues[region['id']] = _source(region, pages)
    lines = _lines(regions); dtype = _document_type(doc, lines)
    raw_records = []; current = None
    length_unit = doc.get('length_unit')
    def bounds(parts):
        boxes = [part['box'] for part in parts if box_valid(part.get('box'))]
        return [min(box[0] for box in boxes), max(box[2] for box in boxes)] if boxes else None

    def same_column(current, line):
        prior = current.get('column'); following = bounds(line['parts'])
        if prior is None or following is None:
            return False
        prior_width = prior[1]-prior[0]; next_width = following[1]-following[0]
        overlap = max(0, min(prior[1], following[1])-max(prior[0], following[0]))
        return abs(prior[0]-following[0]) <= max(.06, prior_width*.2) and overlap >= min(prior_width, next_width)*.3

    for line in lines:
        anchors = _anchors(line['text'])
        if not anchors:
            if current and current['key'] in MULTILINE and current['page'] == line['page'] and line['cy'] is not None and current['last_y'] is not None:
                if current['raw'] is not None and same_column(current, line) and 0 < line['cy']-current['last_y'] < max(line['height'], current['height'])*2:
                    current['raw'] += '\n'+line['text']; current['ids'].extend(line['ids']); current['last_y'] = line['cy']
                    continue
            current = None; continue
        for index, anchor in enumerate(anchors):
            stop = anchors[index+1]['start'] if index+1 < len(anchors) else len(line['text'])
            raw = line['text'][anchor['value_start']:stop]
            if anchor['key'] == 'quotation.price' and not anchor['colon'] and not raw.strip():
                continue
            ids = [region_id for start, end, region_id in line['spans'] if start < stop and end > anchor['start']]
            current = {'key': anchor['key'], 'raw': raw or None, 'ids': ids, 'page': line['page'],
                       'last_y': line['cy'], 'height': line['height'], 'label': anchor['label'],
                       'column': bounds([part for part in line['parts'] if part['id'] in ids])}
            raw_records.append(current)
    # Explicit bare labels paired spatially have no colon, so use the existing
    # measured-neighbor resolver, retaining original value fragments.
    from .form_extraction import extract_form_fields
    for page in sorted(pages):
        spatial, _ = extract_form_fields([region for region in regions if box_valid(region.get('box'))], page)
        for item in spatial:
            key = 'quotation.quantity' if item['key'] == 'quantity' else item['key']
            if key not in LABELS:
                continue
            if any(record['key'] == key and set(record['ids']) & set(item['evidence_ids']) and record['raw'] and record['raw'].strip() for record in raw_records):
                continue
            raw_records = [record for record in raw_records if not (record['key'] == key and set(record['ids']) & set(item['evidence_ids']) and not (record['raw'] or '').strip())]
            raw_records.append({'key': key, 'raw': item.get('raw_value', item['value']), 'ids': item['evidence_ids'], 'label': item['label']})
    records = [_record(record['key'], record['raw'], record['ids'], source_map, source_issues, dtype,
                       label=record['label'], length_unit=length_unit) for record in raw_records]
    fields = [_aggregate([record for record in records if record['key'] == key], key) for key in LABELS]
    by_key = {field['key']: field for field in fields}
    quoted = by_key['quotation.date']; valid = by_key['quotation.valid_until']
    if isinstance(quoted['normalized'], str) and isinstance(valid['normalized'], str) and valid['normalized'] < quoted['normalized']:
        valid['issues'].append('invalid_date_order'); valid['status'] = 'needs_review'
    candidates = []
    callout = re.compile(r'(?<![\w/])(?:M\s*\d+(?:[.,]\d+)?(?:\s*[x×*]\s*\d+(?:[.,]\d+)?){0,2}(?:\s*-\s*\d+[a-zA-Z]+)?|(?:#\d+|\d+(?:/\d+)?)-\d+\s*(?:UNC|UNF|UNEF)(?:-[123][AaBb])?(?:\s*[x×]\s*\d+(?:[.,]\d+)?)?)(?![\w.,x×*/-])', re.I)
    anchored_spec_ids = {region_id for record in raw_records if record['key'] == 'product.specification' for region_id in record['ids']}
    for region in regions:
        if region['id'] in anchored_spec_ids:
            continue
        for match in callout.finditer(region.get('text', '')):
            parsed, errors = parse_spec(match[0], document_type=dtype, length_unit=length_unit)
            if parsed is None:
                continue
            candidate = _record('product.specification', match[0], [region['id']], source_map, source_issues, dtype,
                                extra_issues=['unanchored_spec'], length_unit=length_unit)
            candidate['status'] = 'needs_review'; candidates.append(candidate)
    issues = [{'field': field['key'], 'code': issue, 'message': issue, 'severity': 'warning'}
              for field in fields for issue in field['issues']]
    if dtype == 'unknown':
        issues.append({'field': 'document_type', 'code': 'unknown_document', 'message': 'Needs document classification', 'severity': 'warning'})
    return {'schema': SCHEMA, 'method': METHOD, 'document_id': doc.get('id', doc.get('document_id', 'unknown')),
            'document_type': dtype, 'fields': fields, 'issues': issues, 'specification_candidates': candidates,
            'review_queue': [field for field in [*fields, *candidates] if field['status'] != 'extracted']}
