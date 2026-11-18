"""Project uploaded skill readings into grounded, reversible review candidates."""
import re
from .domain import requirement
from .quote_skill import analyze, METHOD as METHOD_VERSION


def candidates(doc, report=None, region_ids=None):
    report = report or analyze(doc)
    measured = {e['id']: e for e in doc.get('regions', [])
                if e.get('box') and not e.get('source', '').startswith('VLM:')}
    output = []
    def add(item, *, unassigned=False):
        raw = item.get('raw')
        if raw is None or not str(raw).strip(): return
        ids = list(dict.fromkeys(s.get('region_id') for s in item.get('sources', [])
                               if s.get('region_id') in measured))
        if not ids or (region_ids is not None and not set(ids) <= set(region_ids)): return
        key = item.get('key', 'product.specification')
        if 'invalid_field_role' in item.get('issues',[]):
            key='observation.skill_review'
        value, unit = str(raw), ''
        normalized = item.get('normalized')
        if key in ('quotation.date', 'quotation.valid_until') and isinstance(normalized, str):
            value = normalized
        if key == 'quotation.quantity' and isinstance(normalized, dict):
            number = re.match(r'\s*(\d+(?:,\d{3})*(?:\.\d+)?)', value)
            if number: value = number[1].replace(',', '')
            unit = normalized.get('unit_raw') or ''
        if key == 'quotation.price' and isinstance(normalized, dict):
            amount = normalized.get('amount')
            if amount is not None:
                number = re.search(r'\d+(?:,\d{3})*(?:\.\d+)?', value)
                if number: value = number[0].replace(',', '')
                unit = '/'.join(v for v in (normalized.get('currency'),normalized.get('denominator_raw')) if v)
        state = 'conflict' if item.get('status') == 'conflict' else 'present'
        if normalized is None: state = 'unreadable'
        pages = {measured[e]['page'] for e in ids}
        r=requirement(key, value, unit=unit, label=item.get('label', '螺紋規格（作用對象待確認）'),
            state=state, subject='unassigned_feature' if unassigned else 'document',
            scope=f'page:{next(iter(pages))}' if len(pages) == 1 else 'document',
            evidence_ids=ids, origin='drawing_observation' if unassigned or key.startswith('observation.') else 'quote_skill', raw_value=raw,
            skill_normalized=normalized, skill_status=item.get('status'),
            skill_issues=item.get('issues', []), skill_method=METHOD_VERSION,
            candidates=[{'source': METHOD_VERSION, 'value': raw, 'normalized': normalized,
                         'evidence_ids': ids, 'issues': item.get('issues', [])}])
        if key=='material':
            from .engineering import context_heading
            heading=context_heading(r,measured)
            if heading:
                r.update(key='coating.material',label='塗膠材料（非零件基材）',scope='coating',critical=False)
                r['evidence_ids']=list(dict.fromkeys([heading['id']]+r['evidence_ids']))
        output.append(r)
    for field in report['fields']:
        if field['status'] == 'conflict':
            for alternative in field['alternatives']:
                add({**alternative, 'key': field['key'], 'label': field['label'], 'status': 'conflict'})
        else: add(field)
    for item in report.get('specification_candidates', []):
        add({**item, 'key': 'product.specification'}, unassigned=True)
    return output
