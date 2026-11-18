"""Rank source-backed values for tentative display, without attesting them.

Scores describe this ranking only. They are not correctness probabilities.
The chosen requirement, its value, its units and every human decision stay
unchanged; low-confidence and conflicting readings remain visible proposals.
"""
import math
import re
import unicodedata
from datetime import date

from .domain import box_valid, canonical, digest
from .quotation import FIELDS, LABEL_MAP, label_token
from .quotation_validation import printed_value_issue, value_issue
from .quote_skill import parse_spec


KEY_ALIASES = {
    'product.specification': {'specification', 'thread.specification', 'dimension.thread'},
    'quotation.quantity': {'quantity'}, 'quotation.price': {'unit_price', 'price'},
    'quality.requirements': {'standard', 'standards', 'standard.reference'},
}
EXTRA_LABELS = {
    'product.specification': ('產品規格', '螺紋', '螺紋規格', '公制螺紋', 'Thread'),
    'quality.requirements': ('須符合規範', '適用規範', '品質需求'),
    'packaging.instructions': ('包裝要求',),
    'quotation.quantity': ('Qty',), 'quotation.price': ('Price',),
}
SPEC_TOKEN = re.compile(
    r'(?<![A-Za-z0-9])(?:M\s*\d+(?:[.,]\d+)?(?:\s*[x×X*]\s*\d+(?:[.,]\d+)?){0,2}'
    r'(?:\s*[-–]\s*\d+[a-zA-Z]+)?|(?:#\d+|\d+(?:/\d+)?)-\d+\s*(?:UNC|UNF|UNEF)'
    r'(?:-[123][AaBb])?(?:\s*[x×X]\s*\d+(?:[.,]\d+)?)?)'
    r'(?:\s*(?:mm|cm|inch(?:es)?|in|毫米|公分|英吋|["″]))?(?![A-Za-z0-9.,x×*/-])', re.I)
ANNUAL = re.compile(r'/\s*年|每年|年度|計畫|預估|annual|/\s*(?:year|yr)\b|per\s+year|forecast|planned', re.I)
STANDARD = re.compile(r'\b(?:ISO|DIN|EN|ASME|ASTM|JIS|CNS)\s*\d', re.I)


def _is_model(region):
    return str(region.get('source', '')).casefold().startswith('vlm:') or bool(region.get('model_generated'))


def _measured(regions):
    return {rid: region for rid, region in regions.items()
            if region.get('kind', 'evidence') == 'evidence' and box_valid(region.get('box'))
            and isinstance(region.get('text'), str) and region['text'].strip() and not _is_model(region)}


def _linked_sources(r, regions, measured):
    sources = []
    model = False
    for rid in r.get('evidence_ids', []):
        region = regions.get(rid)
        if not region:
            continue
        if rid in measured:
            sources.append(measured[rid])
        elif _is_model(region):
            model = True
            if not box_valid(region.get('box')):
                return [], True, '模型來源框缺失或無效'
            matches = [e for e in measured.values() if e.get('page') == region.get('page')
                       and canonical(e['text']) == canonical(region.get('text', ''))
                       and all(abs(a-b) <= .005 for a, b in zip(e['box'], region['box']))]
            if not matches:
                return [], True, '模型讀文沒有同位置的實測原文支持'
            sources.extend(matches)
    sources = list({e['id']: e for e in sources}.values())
    return sources, model, '' if sources else '沒有可定位的原文證據'


def _aliases(key):
    label, aliases = FIELDS[key]
    return (label, *aliases.split('|'), *EXTRA_LABELS.get(key, ()))


def _has_role(text, key):
    raw = unicodedata.normalize('NFKC', text).translate(LABEL_MAP)
    for alias in _aliases(key):
        token = label_token(alias)
        pattern = r'\s*'.join(re.escape(char) for char in token)
        if re.search(r'(?:^|\n|[|;；]|\s{2,})\s*(?:\d+[.)、]\s*)?' + pattern
                     + r'(?=\s*[:：]|[ \t]*(?:\n|$))', raw, re.I):
            return True
    return False


def _match_role(r, key):
    rkey = r.get('key', '')
    if rkey.startswith('table.'):
        return None
    if rkey == key:
        return 0
    if rkey in FIELDS:
        return None
    if rkey in KEY_ALIASES.get(key, ()):
        return 1
    if label_token(r.get('label', '')).strip('.。:：') in {label_token(v) for v in _aliases(key)}:
        return 2
    return None


def _bound_value(text, pattern, value, *, stop_at_separator=False):
    tail = r'([^\n|;；]+)' if stop_at_separator else r'([^\n]+)'
    return any(canonical(match[1].strip()) == canonical(value)
               for match in re.finditer('(?:' + pattern + ')' + r'\s*[:：]\s*' + tail, text, re.I))


def _spec_signature(parsed):
    identity = {key: value for key, value in parsed.items()
                if key not in ('normalized', 'normalization_notes', 'length_unit_basis')}
    if not str(identity.get('size_designation', '')).startswith('#'):
        identity.pop('size_designation', None)
    return identity


def _specification(value, source):
    parsed, issues = parse_spec(value)
    if parsed is not None:
        printed = [parse_spec(match[0])[0] for match in SPEC_TOKEN.finditer(source)]
        if not any(item and _spec_signature(item) == _spec_signature(parsed) for item in printed):
            return None, [], '原文不支持此完整螺紋規格／公差'
        return parsed, issues, ''
    if re.search(r'\b(?:screw|bolt|stud)\b|螺絲|螺栓', value, re.I) and canonical(value) in canonical(source):
        tokens = [parse_spec(match[0])[0] for match in SPEC_TOKEN.finditer(value)]
        if any(tokens):
            return next(item for item in tokens if item), ['part_caption_proposal'], ''
    return None, [], '沒有可判讀的螺紋／產品規格'


def _semantic_issue(r, key, sources, measured, human):
    value = r['value'].strip(); unit = r.get('unit', '')
    source = '\n'.join(e['text'] for e in sources)
    if 'invalid_field_role' in r.get('skill_issues', []):
        return '欄位用途不符', None, []
    binding = r.get('role_binding')
    local = {**r, 'evidence_ids': [e['id'] for e in sources]}
    rfq_supported = False
    if key == 'customer.name' and binding == 'rfq_issuer_to_nailok':
        from .engineering_semantics import rfq_issuer_supported
        rfq_supported = rfq_issuer_supported(local, measured)
    issue = value_issue(key, value, source, unit,
        check_role=not human and not rfq_supported)
    if issue:
        return issue, None, []
    if key == 'quotation.quantity':
        context = source + '\n' + value + ' ' + unit + ' ' + r.get('conditions', '')
        if ANNUAL.search(context):
            return '年度／計畫量不能當本次報價數量', None, []
    if key in ('quotation.date', 'quotation.valid_until'):
        match = re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?', value)
        try:
            if not match: raise ValueError('invalid date')
            date(*map(int, match.groups()))
        except ValueError:
            return '日期無效', None, []
    if key == 'material':
        from .engineering import context_heading
        if context_heading(local, measured) or re.search(r'thread.?lock\s*(?:material|材料)|塗膠材料|涂胶材料', source, re.I):
            return '塗膠材料不能當成零件基材', None, []
        if not human and not _has_role(source, key):
            # A customer called Material Handling LLC does not introduce a
            # material cell. An isolated recognizable grade remains usable.
            known = re.search(r'steel|stainless|alloy|alumin(?:i)?um|brass|copper|nylon|polyamide|pom|abs|鋼|钢|白鐵|白铁|鋁|铝|銅|铜', value, re.I)
            grade = re.fullmatch(r'(?:S\d+C|C\d{2,4}|\d{2}B\d+|SUS\s*\d+|SCM\s*\d+|\d{3})', value, re.I)
            if not known and not (grade and any(canonical(e['text']) == canonical(value) for e in sources)):
                return '原文沒有材質欄位或可辨識的基材標示', None, []
    if key == 'coating.color' and sources:
        explicit_coating = _bound_value(source, r'Coating\s+Colou?r|塗膠顏色|涂胶颜色', value, stop_at_separator=True)
        if explicit_coating and (not unit or canonical(unit) in canonical(source)):
            return '', None, []
        if re.search(r'surface\s+treatment|plating|finish\s*[:：]|表面處理|鍍層', source, re.I):
            return '鍍層／表面處理顏色不能當塗膠顏色', None, []
        from .engineering_semantics import above_header
        if above_header(sources[-1], list(measured.values()), r'surface\s+treatment|plating|finish|表面處理|鍍層', distance=.06):
            return '鍍層／表面處理顏色不能當塗膠顏色', None, []
    if key == 'coating.method' and binding == 'printed_coating_process':
        supported = _bound_value(source, r'加工[製制]程名[稱称]', value)
        coating = re.search(r'塗膠|涂胶|防鬆膠|thread.?lock|adhesive|patch|coating', value, re.I)
        return ('', None, ['coating_extent_unconfirmed']) if supported and coating else ('加工製程原文不支持此塗膠方式', None, [])
    if key == 'quality.requirements' and binding == 'unresolved_printed_standard':
        supported = _bound_value(source, r'Standard', value) and STANDARD.search(value)
        return ('', None, ['standard_number_requires_review']) if supported else ('標準欄位原文不支持此未確認代號', None, [])
    if key == 'quality.requirements' and not human and not _has_role(source, key) and not STANDARD.search(source):
        if not re.search(r'品質|檢驗|检验|測試|测试|材[質质]證明|材證|首批|每批|檢測|检測|檢驗報告|品質報告|certificate|inspection|quality|test|report', source, re.I):
            return '原文沒有品質／檢驗／標準用途', None, []
    if key == 'customer.name' and binding == 'rfq_issuer_to_nailok':
        return ('', None, ['interpreted_customer_role']) if rfq_supported else ('詢價發出方角色證據不足', None, [])
    if key == 'adhesive.product' and binding == 'printed_adhesive_product':
        from .engineering_semantics import semantic_requirements
        supported = any(item['key'] == key and canonical(item['value']) == canonical(value)
                        and set(item['evidence_ids']) <= set(local['evidence_ids'])
                        for item in semantic_requirements(list(measured.values())))
        if supported:
            return '', None, []
    parsed = None; extra_issues = []
    if key == 'product.specification':
        parsed, extra_issues, issue = _specification(value, source)
        if issue and not human:
            return issue, None, []
    if not human:
        issue = printed_value_issue(key, value, source, unit,
            tolerant_labels=bool(r.get('label_repair')) and r.get('origin') in ('quotation_extraction', 'spatial_form'))
        if issue and not (key == 'product.specification' and parsed is not None):
            return issue, None, []
    return '', parsed, extra_issues


def _confidence(sources):
    values = [e.get('ocr_confidence') for e in sources]
    valid = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1
    if not values or any(not valid(value) for value in values):
        return None, ['missing_confidence'] if any(value is None for value in values) else ['invalid_confidence']
    value = min(values)
    return float(value), ['low_confidence'] if value < .8 else []


def _identity(key, r, parsed):
    if key == 'product.specification' and parsed:
        return digest(_spec_signature(parsed))
    return digest([r['value'].strip(), re.sub(r'\s+', '', r.get('unit', '')), r.get('subject', 'document')])


def choose_proposal(candidates, key, regions):
    """Return the best supported literal proposal and all eligible alternatives.

    Missing/low OCR scores and conflicting values may be displayed for human
    review. Wrong roles, substituted units and self-supported model text are
    rejected. This function does not create mappings or change review states.
    """
    if key not in FIELDS:
        return {'source': None, 'reason': '未知客製欄位', 'issues': [], 'confidence': None, 'alternatives': [], 'score': None, 'rejected': []}
    measured = _measured(regions); eligible = []; rejected = []
    for r in candidates:
        priority = _match_role(r, key)
        if priority is None or r.get('deleted'):
            continue
        value = r.get('value')
        if not isinstance(value, str) or not value.strip() or r.get('state') in ('blank', 'missing', 'not_applicable'):
            rejected.append({'requirement_id': r.get('id'), 'reason': '原文沒有具體值'})
            continue
        sources, model, issue = _linked_sources(r, regions, measured)
        if issue:
            rejected.append({'requirement_id': r.get('id'), 'reason': issue}); continue
        human = r.get('status') == 'verified' and bool(r.get('support_verified'))
        issue, parsed, semantic_issues = _semantic_issue(r, key, sources, measured, human)
        if issue:
            rejected.append({'requirement_id': r.get('id'), 'reason': issue}); continue
        confidence, score_issues = _confidence(sources)
        issues = list(dict.fromkeys([*r.get('skill_issues', []), *r.get('proposal_issues', []), *semantic_issues, *score_issues]))
        if model: issues.append('model_interpretation')
        if r.get('state') == 'conflict': issues.append('conflict_candidate')
        if r.get('state') in ('pending_decision', 'unreadable'):
            issues.extend(['unresolved_state', r['state']+'_candidate'])
        if r.get('subject') == 'unassigned_feature': issues.append('unassigned_object')
        if key == 'quotation.quantity' and re.search(r'^(?:M|K)$', r.get('unit', '').strip(), re.I):
            issues.append('quantity_unit_ambiguous')
        source_text = '\n'.join(e['text'] for e in sources)
        explicit = _has_role(source_text, key)
        binding = r.get('role_binding', '')
        role_score = 32 if explicit else 27 if binding in ('printed_thread_callout', 'printed_part_caption', 'printed_adhesive_product', 'rfq_issuer_to_nailok') else 22 if key == 'quality.requirements' and STANDARD.search(source_text) else 18
        role_score -= priority*2
        if binding == 'printed_coating_process': role_score = 16
        object_score = 10 if r.get('subject', 'document') == 'document' else 0 if r.get('subject') == 'unassigned_feature' else 6
        completeness = 0
        if parsed:
            pitch = parsed.get('pitch_mm', parsed.get('tpi'))
            completeness = 8 if pitch is not None and parsed.get('length_value') is not None else 4 if pitch is not None else 2
        reasons = ['明確欄位標籤' if explicit else '工程欄位／原文用途', '實測原文支持']
        if completeness >= 8: reasons.append('完整規格')
        if confidence is not None: reasons.append(f'OCR 信心 {confidence:.2f}')
        else: reasons.append('來源信心待確認')
        if human: reasons.append('人工已確認')
        if r.get('state') == 'conflict': reasons.append('保留衝突待覆核')
        if r.get('state') in ('pending_decision', 'unreadable'): reasons.append('候選內容尚待人工覆核')
        methods = {str(method) for e in sources for method in e.get('methods', [e.get('source')])
                   if method and not str(method).casefold().startswith('vlm:')}
        base_score = role_score + object_score + 10 + completeness + (confidence or 0)*18 + (50 if human else 0)
        if r.get('state') == 'conflict': base_score -= 3
        if r.get('suggestion_only'): base_score -= 2
        position = min((e.get('page', 1), e['box'][1], e['box'][0], e['box'][3], e['box'][2]) for e in sources)
        eligible.append({'r': r, 'confidence': confidence, 'issues': list(dict.fromkeys(issues)), 'reasons': reasons,
                         'base_score': base_score, 'methods': methods, 'identity': _identity(key, r, parsed), 'position': position})
    competing = len({item['identity'] for item in eligible}) > 1
    for item in eligible:
        methods = set().union(*(peer['methods'] for peer in eligible if peer['identity'] == item['identity']))
        agreement = min(10, max(0, len(methods)-1)*4)
        item['score'] = round(min(100, item['base_score']+agreement), 3)
        if len(methods) > 1: item['reasons'].append(f'{len(methods)} 種方法讀值一致')
        if competing:
            item['issues'].extend(['competing_values', 'conflicting_alternatives'])
            item['reasons'].append('另有不同值候選待覆核')
        item['reason'] = '；'.join(item['reasons'])
    eligible.sort(key=lambda item: (-item['score'], item['position'], item['r']['id']))
    alternatives = [{'requirement_id': item['r']['id'], 'value': item['r']['value'], 'unit': item['r'].get('unit', ''),
                     'score': item['score'], 'reason': item['reason'], 'issues': item['issues'], 'confidence': item['confidence']}
                    for item in eligible]
    if not eligible:
        return {'source': None, 'reason': '沒有具體且原文支持的適合候選', 'issues': [], 'confidence': None,
                'alternatives': [], 'score': None, 'rejected': rejected}
    best = eligible[0]
    return {'source': best['r'], 'reason': best['reason'], 'issues': best['issues'], 'confidence': best['confidence'],
            'alternatives': alternatives, 'score': best['score'], 'rejected': rejected}
