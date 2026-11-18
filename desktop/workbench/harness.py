"""Read-only engineering contracts, explanations and release checks.

This evaluates evidence and state, not visual recognition accuracy. Providers and
UI remain outside this module. An incomplete model pass is not a failed OCR pass.
"""
from .domain import checks, clone, digest, validate_document
from .harness_contracts import POLICY
from .quotation_layout import source_issue
from .quotation_validation import value_issue
from .engineering import obligations


def policy():
    return clone(POLICY)


def inspect_document(doc):
    from .quotation_layout import quotation_rows
    result = {'schema': 'drawiq-harness-report/1', 'policy_revision': POLICY['revision'],
              'policy_sha256': digest(POLICY), 'document_id': doc.get('id'),
              'document_version': doc.get('version'), 'source_sha256': doc.get('source_sha256'),
              'findings': [], 'fields': [], 'methods': [], 'accuracy_verified': False,
              'review_ready': False, 'contract_status': 'pass'}

    def finding(code, message, *, rid=None, field=None, severity=None):
        default, rule, remedy = POLICY['rules'][code]
        result['findings'].append({'code': code, 'severity': severity or default,
                                  'requirement_id': rid, 'field': field,
                                  'message': message, 'rule': rule, 'remedy': remedy})

    try:
        for r in doc['requirements']:
            for key in ('id','key','label','value','unit','subject','scope','conditions','state','status'):
                if not isinstance(r.get(key),str): raise ValueError(f'需求 {key} 必須為文字')
            for key in ('deleted','support_verified','critical'):
                if not isinstance(r.get(key),bool): raise ValueError(f'需求 {key} 必須為布林值')
        for e in doc['regions']:
            if not isinstance(e.get('text'),str): raise ValueError('原文必須為文字')
        validate_document(doc)
    except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as exc:
        finding('DQH-STRUCTURE', str(exc))
        result['contract_status'] = 'fail'
        return result

    regions = {e['id']: e for e in doc['regions']}
    for row in quotation_rows(doc):
        r = row['source']; reason = ''
        if r:
            if r['state'] == 'present':
                issue = value_issue(row['key'], r['value'])
                if issue:
                    finding('DQH-FIELD-TYPE', issue, rid=r['id'], field=row['key'])
                reason = source_issue(r, row['key'], regions)
                if reason:
                    finding('DQH-AUTO-EVIDENCE' if row['automatic'] else 'DQH-MANUAL-ROLE',
                            reason, rid=r['id'], field=row['key'])
            if r['status'] == 'verified' and not r['support_verified']:
                finding('DQH-HUMAN-PROOF', '已確認來源失去原圖證據確認', rid=r['id'], field=row['key'])
            if r['status'] != 'verified':
                finding('DQH-REVIEW', row['label']+'仍待確認', rid=r['id'], field=row['key'])
            explanation = reason or ('原文與用途符合；仍須人工確認' if row['automatic'] else '人工指定來源；保留覆核責任')
            if r.get('role_binding')=='rfq_issuer_to_nailok' and not reason:
                explanation='公司圖頭＋詢價單＋耐落收件人推定客戶角色，請人工核對交易角色'
        else:
            explanation = '；'.join(dict.fromkeys(x['reason'] for x in row['rejected_candidates'])) or row['status']
        result['fields'].append({'key': row['key'], 'label': row['label'], 'status': row['status'],
                                 'automatic': row['automatic'], 'source_id': r['id'] if r else None,
                                 'value': r['value'] if r else '', 'unit': r['unit'] if r else '',
                                 'evidence_ids': r['evidence_ids'] if r else [],
                                 'explanation': explanation, 'rejected_candidates': clone(row['rejected_candidates'])})

    # Existing domain checks remain the single authority for geometry,
    # contradictions, dependencies and critical requirements.
    for issue in checks(doc):
        result['findings'].append({**issue, 'code': 'DOMAIN:'+issue['code'],
                                   'field': None, 'rule': '既有工程檢查',
                                   'remedy': '定位該候選並依原圖修正或明確排除'})
    observed = {eid for r in doc['requirements'] if r['key'] == 'observation.text'
                for eid in r['evidence_ids']}  # Excluded readings still exist in history.
    missing = [e['id'] for e in doc['regions'] if e['kind'] == 'evidence' and e['text'].strip() and e['id'] not in observed]
    if missing:
        finding('DQH-COVERAGE', f'{len(missing)} 個原文框尚未有原文候選索引')
    result['coverage'] = {'printed_regions': sum(e['kind'] == 'evidence' and bool(e['text'].strip()) for e in doc['regions']),
                          'indexed_regions': len(observed), 'unindexed_region_ids': missing}
    for run in doc.get('tool_runs', []):
        state = run.get('vlm_status')
        result['methods'].append({k: run.get(k) for k in ('id', 'page', 'model', 'status', 'vlm_status',
                                  'ocr_engine', 'elapsed_seconds', 'trace_dir', 'vlm_error', 'ocr_aligned_vlm_count')})
        if state and state not in ('complete','not_needed') or run.get('status') in ('failed', 'cancelled', 'partial_timeout'):
            finding('DQH-MODEL', f"第 {run.get('page','?')} 頁 {run.get('model') or '方法'}：{state or run.get('status')}")
    active = obligations(doc)
    result['quotation_review_complete'] = all(f['status'] == '已確認' for f in result['fields'])
    result['quotation_values_complete'] = all(f['source_id'] and f['value'] for f in result['fields'])
    errors = [x for x in result['findings'] if x['severity'] == 'error']
    result['contract_status'] = 'fail' if errors else 'warning' if result['findings'] else 'pass'
    result['review_ready'] = bool(active) and not errors and all(
        r['status'] == 'verified' and r['support_verified'] and r['state'] not in ('conflict','pending_decision','unreadable')
        for r in active)
    return result


def enforce_quotation_contract(doc, selected=None):
    """Only enforce hard quotation invariants; retain explicit manual overrides."""
    hard = {'DQH-FIELD-TYPE', 'DQH-AUTO-EVIDENCE', 'DQH-HUMAN-PROOF', 'DQH-STRUCTURE'}
    failures = [x for x in inspect_document(doc)['findings'] if x['code'] in hard and
                (selected is None or x['requirement_id'] in selected or x['code'] == 'DQH-STRUCTURE')]
    if failures:
        raise ValueError('；'.join(dict.fromkeys(x['code']+'：'+x['message'] for x in failures)))
