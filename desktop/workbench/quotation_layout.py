"""Resolve quotation references without copying facts or changing review decisions."""
import re
from decimal import Decimal,InvalidOperation
from .domain import canonical
from .quotation import FIELDS, label_token
from .quotation_validation import printed_value_issue,value_issue

QUOTE_FIELDS = {key: label for key, (label, _) in FIELDS.items()
                if key not in ('contact.phone', 'contact.fax')}
QUOTE_FIELDS.update({'quotation.quantity': '數量', 'quotation.price': '報價'})

# Older drawing extraction uses generic or custom keys. Match whole labels only;
# a table heading, RFQ date or supplier address must not become a quotation fact.
KEY_ALIASES = {
    'product.specification': {'specification', 'thread.specification', 'dimension.thread'},
    'quotation.quantity': {'quantity'},
    'quotation.price': {'unit_price', 'price'},
    'quality.requirements': {'standard', 'standards','standard.reference'},
}
LABEL_ALIASES = {
    'product.specification': {'產品規格', '螺紋', '螺紋規格', '公制螺紋'},
    'quality.requirements': {'須符合規範', '適用規範', '品質需求'},
    'packaging.instructions': {'包裝要求'},
}
LABELS = {key: {label_token(x).strip('.。:：')
                for x in [label, *aliases.split('|'), *LABEL_ALIASES.get(key, ())]}
          for key, (label, aliases) in FIELDS.items() if key in QUOTE_FIELDS}


def match_priority(r, key):
    if r['key'].startswith('table.'): return None
    if r['key'] == key: return 0
    if r['key'] in QUOTE_FIELDS: return None
    if r['key'] in KEY_ALIASES.get(key, ()): return 1
    if label_token(r['label']).strip('.。:：') in LABELS[key]: return 2
    return None


def source_issue(r, key, regions):
    if r['state'] != 'present': return '內容尚未確定'
    # Loose dimension tokens have no identified object and may be only a prefix
    # of the complete specification extracted from the very same text box.
    if r['subject'] == 'unassigned_feature': return '尺寸的作用對象尚未指定'
    evidence = [regions[e]['text'] for e in r['evidence_ids']
                if e in regions and regions[e].get('kind', 'evidence') == 'evidence']
    if not evidence: return '尚未連結原圖證據'
    human_attested = r['status']=='verified' and r.get('support_verified')
    if r.get('suggestion_only') and not human_attested:
        return '工程上下文建議，請先人工確認欄位用途'
    if not human_attested:
        if 'invalid_date_order' in r.get('skill_issues',[]):
            return '報價有效日期早於報價日期，需人工判定'
        import math
        for eid in r['evidence_ids']:
            e=regions.get(eid,{})
            if e.get('source','').startswith('VLM:'): continue
            if 'ocr_confidence' not in e: continue  # historical/manual evidence has no supplied score
            score=e['ocr_confidence']
            if score is None: return '來源辨識信心未提供，請人工覆核'
            if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
                return '來源辨識信心格式無效，請人工覆核'
            if score<.8: return '來源辨識信心低於 0.8，請框選重讀或人工覆核'
        if key=='quotation.valid_until':
            from datetime import date
            from .quote_skill import analyze as skill_analyze
            dates=[]
            measured=[e for e in regions.values() if e.get('box') and not e.get('source','').startswith('VLM:')]
            report=skill_analyze({'regions':measured,'pages':[{'number':n} for n in sorted({e['page'] for e in measured})]})
            for quoted in report['fields']:
                if quoted['key']!='quotation.date': continue
                m=re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?',str(quoted.get('normalized') or '').strip())
                if m:
                    try: dates.append(date(*map(int,m.groups())))
                    except ValueError: pass
            m=re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?',r['value'].strip())
            if m and len(set(dates))==1:
                try:
                    if date(*map(int,m.groups()))<dates[0]: return '報價有效日期早於報價日期，需人工判定'
                except ValueError: pass
    if key=='customer.name' and r.get('role_binding')=='rfq_issuer_to_nailok':
        from .engineering_semantics import rfq_issuer_supported
        if rfq_issuer_supported(r,regions): return ''
        return '詢價發出方角色的公司圖頭／詢價單／耐落收件人證據不完整或有不同公司'
    # A model's generated "evidence" is not an independent transcription.
    # Auto values need a measured OCR/native anchor at the same location, or
    # an explicit human attestation. Do not let invented text certify itself.
    if not (r['status']=='verified' and r.get('support_verified')):
        for eid in r['evidence_ids']:
            e=regions.get(eid)
            if not e or not e.get('source','').startswith('VLM:'): continue
            measured=[x for x in regions.values() if x['page']==e['page'] and
                      not x.get('source','').startswith('VLM:') and x.get('kind','evidence')=='evidence' and
                      canonical(x['text'])==canonical(e['text']) and
                      all(abs(a-b)<=.005 for a,b in zip(x['box'],e['box']))]
            if not measured: return '模型讀文尚未有同位置的實測文字支持；請框選重讀或人工覆核'
            scores=[x.get('ocr_confidence') for x in measured]
            # Generated text cannot hide a low/missing measured anchor score
            # by omitting that anchor from the requirement's own source list.
            if not any(isinstance(s,(int,float)) and not isinstance(s,bool) and math.isfinite(s)
                       and .8<=s<=1 for s in scores):
                return '模型讀文的實測支持信心不足或未提供；請人工覆核'
    if r.get('composite_kind')=='quality' and not r.get('manual_composite_override'):
        parts=r['value'].split('\n')
        return '' if all(any(canonical(part) in canonical(text) for text in evidence) for part in parts) else '品質彙整包含原文尚未支持的內容'
    if key=='material':
        from .engineering import context_heading
        coating_material=re.search(r'(?:thread.?lock(?:ing)?|torq.?patch|塗膠|防鬆膠)[^\n:：]{0,30}(?:Material|材料|材質)\s*[:：]',
                                   '\n'.join(evidence),re.I)
        if coating_material or context_heading(r,regions): return '此材料位於螺紋防鬆／塗膠規範中，不能當零件基材'
    if key=='coating.color':
        from .engineering_semantics import above_header
        color_source='\n'.join(evidence)
        explicit_coating=any(canonical(m[1].strip())==canonical(r['value']) for m in re.finditer(
            r'(?:Coating\s+Colou?r|塗膠顏色|涂胶颜色)\s*[:：]\s*([^\n|;；]+)',color_source,re.I))
        if explicit_coating and (not r['unit'] or canonical(r['unit']) in canonical(color_source)):
            return ''
        if re.search(r'surface\s+treatment|plating|finish\s*[:：]|表面處理|鍍層',color_source,re.I):
            return '此顏色位於表面處理／鍍層中，不能當塗膠顏色'
        linked=[regions[x] for x in r['evidence_ids'] if x in regions]
        if linked and above_header(linked[-1],list(regions.values()),r'surface\s+treatment|plating|finish|表面處理|鍍層',distance=.06):
            return '此顏色位於表面處理／鍍層中，不能當塗膠顏色'
    value = r['value'].strip()
    token = label_token(value).strip('.。:：;；-—_ /\\')
    if not token or token in LABELS[key] or token == label_token(r['label']).strip('.。:：'):
        return '標題或空白，沒有具體內容'
    if token in {'天', '日', '元', 'pcs', '支', '個', '意見', '待填', '待補', '待確認', 'tbd'}:
        return '僅單位、標題或待補文字'
    if key in ('quotation.price', 'quotation.quantity') and not re.search(r'\d', value):
        return '沒有實際數值'
    if key == 'quotation.quantity':
        context = ' '.join([*(str(r.get(k, '')) for k in ('value', 'unit', 'scope', 'conditions')), *evidence])
        # This is the legacy parser's generic label, even for actual order
        # quantities; its wording is not evidence of an annual/planned volume.
        if r['label'] != '需求／計畫數量': context += ' ' + r['label']
        if re.search(r'/\s*年|每年|年度|計畫|預估|annual|/\s*(?:year|yr)\b|per\s+year|forecast|planned', context, re.I):
            return '年度／計畫需求，不能當成本次報價數量'
    return printed_value_issue(key, value, '\n'.join(evidence), r['unit'],
                               tolerant_labels=bool(r.get('label_repair')) and r.get('origin') in ('quotation_extraction','spatial_form'))


def usable_source(r, key, regions):
    return not source_issue(r, key, regions)


def automatic_source(candidates, key, regions):
    usable = [r for r in candidates if usable_source(r, key, regions)]
    # A contradictory source is not resolved by picking the first row. Equal
    # duplicates can share one reference, but facts for different objects cannot.
    identities = {(equivalent_value(key,r['value']), equivalent_unit(r['unit']), canonical(r['subject']),
                   canonical(r.get('conditions', ''))) for r in usable}
    unresolved = any(r['state'] in ('conflict', 'unreadable', 'pending_decision') for r in candidates)
    if len(identities) != 1 or unresolved: return None
    return min(usable, key=lambda r: (match_priority(r, key), r['id']))


def equivalent_unit(value):
    """Only separator spacing equivalence, without guessing or converting units."""
    return re.sub(r'\s*/\s*','/',canonical(value))


def equivalent_value(key,value):
    """Equivalence is comparison only; the original human/printed value stays."""
    if key in ('quotation.date','quotation.valid_until'):
        from datetime import date
        m=re.fullmatch(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?',value.strip())
        if m:
            try: return date(*map(int,m.groups())).isoformat()
            except ValueError: pass
    if key in ('product.specification','thread.specification','dimension.thread'):
        value=re.sub(r'(?<=\d),(?=\d)', '.',value)
    if key in ('quotation.quantity','quotation.price'):
        # Only equal complete numeric values; no unit conversion or multiplying
        # M/K/annual counts. Inline units remain part of the identity.
        m=re.fullmatch(r'(\d+(?:,\d{3})*(?:\.\d+)?)(.*)',value.strip())
        if m:
            try: return str(Decimal(m[1].replace(',','')).normalize())+canonical(m[2])
            except InvalidOperation: pass
    return canonical(value)


def quotation_rows(doc, *, include_proposals=False):
    active = {r['id']: r for r in doc['requirements'] if not r['deleted']}
    mapping = doc.get('quotation_mapping', {})
    regions = {e['id']: e for e in doc['regions']}
    rows = []
    for key, label in QUOTE_FIELDS.items():
        candidates = [r for r in active.values() if match_priority(r, key) is not None]
        if key=='quality.requirements':
            components={x for r in candidates if r.get('composite_kind')=='quality' for x in r.get('component_ids',[])}
            candidates=[r for r in candidates if r['id'] not in components]
        explicit = key in mapping
        source = active.get(mapping[key]) if explicit else automatic_source(candidates, key, regions)
        rejected = [{'requirement_id': r['id'], 'label': r['label'], 'reason': source_issue(r, key, regions)}
                    for r in candidates if r['state'] == 'present' and source_issue(r, key, regions)]
        ambiguous = (sum(usable_source(r, key, regions) for r in candidates) > 1 or
                     any(r['state'] in ('conflict', 'unreadable', 'pending_decision') for r in candidates))
        state = ('已確認' if source['status'] == 'verified' else '待覆核') if source else (
            '來源已排除' if explicit and mapping[key] else '未帶入' if explicit else
            '請選擇來源' if ambiguous else '需判定' if rejected else '待補資料')
        manual_issue=value_issue(key,source['value']) if explicit and source and source['state']=='present' else ''
        if manual_issue: state='需修正'
        proposal={}; proposed=False
        if include_proposals and not explicit and source is None and doc.get('status') not in ('approved','released'):
            from .quotation_proposals import choose_proposal
            proposal=choose_proposal(candidates,key,regions)
            if proposal.get('source'):
                source=proposal['source']; proposed=True
                state='衝突／待確認' if source['state']=='conflict' or 'competing_values' in proposal.get('issues',[]) else '建議／待確認'
        display_value=(source['value']+' '+source['unit']).strip() if source else ''
        if not display_value:
            display_value=('人工取消帶入，請補填' if explicit and mapping[key] is None else
                           '待補填：'+rejected[0]['reason'] if rejected else
                           '尚無適合資料，請補填' if candidates else '圖面未提供，請補填')
        rows.append({'key': key, 'label': label, 'source': source, 'status': state,'manual_issue':manual_issue,
                     'automatic': not explicit and source is not None and not proposed,
                     'proposed':proposed,'display_value':display_value,
                     'proposal_reason':proposal.get('reason',''),'proposal_issues':proposal.get('issues',[]),
                     'proposal_score':proposal.get('score'),'proposal_confidence':proposal.get('confidence'),
                     'proposal_alternatives':proposal.get('alternatives',[]),
                     'rejected_candidates': rejected,
                     'candidate_ids': [r['id'] for r in candidates]})
    return rows


def retain_quotation_sources(doc, rids):
    """An intentional edit/review retains the selected reference atomically."""
    selected = [f for f in quotation_rows(doc,include_proposals=True)
                if (f['automatic'] or f['proposed']) and f['source']['id'] in rids]
    for field in selected:
        doc.setdefault('quotation_mapping', {})[field['key']] = field['source']['id']
    return [f['key'] for f in selected]
