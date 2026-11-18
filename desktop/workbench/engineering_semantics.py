"""Printed engineering roles. No guessed dimensions, brand substitutions or units."""
import re
from .domain import canonical, requirement

VERSION = 'engineering-roles/0.8.0'
PRODUCT = re.compile(r'\b(?:(?:precote|precoat)\s*\d+(?:[-/]\w+)?|loctite\s*\d+|tuflok|nylok)\b', re.I)
TORQUE_ROLES = {'鎖緊': 'tightening', '旋入': 'installation', '鎖入': 'installation',
                '破壞': 'breakaway', '鬆脫': 'removal', '鬆開': 'removal',
                '旋出': 'removal', '退出': 'removal'}

def engineering_text(text):
    return text.translate(str.maketrans('纹胶种负载紧毁没无', '紋膠種負載緊毀沒無'))


def measured_regions(regions):
    return [e for e in regions if e.get('kind', 'evidence') == 'evidence' and
            e['text'].strip() and not e.get('source', '').startswith('VLM:')]


def above_header(e, regions, pattern, distance=.16):
    b = e['box']; found = [x for x in regions if x['page'] == e['page'] and
        x['id'] != e['id'] and x['box'][3] <= b[1] + .006 and
        b[1] - x['box'][3] < distance and
        min(b[2], x['box'][2]) > max(b[0], x['box'][0]) and re.search(pattern, engineering_text(x['text']), re.I)]
    return min(found, key=lambda x: b[1] - x['box'][3]) if found else None


def semantic_requirements(regions):
    regions = measured_regions(regions); result = []
    def add(e, key, value, label, *, context=None, **extra):
        ids = ([context['id']] if context else []) + [e['id']]
        result.append(requirement(key, value, label=label, evidence_ids=ids,
            scope=f'page:{e["page"]}', origin='engineering_projection',
            semantic_rule=VERSION, candidates=[{'source': VERSION, 'value': value,
                                                'evidence_ids': ids}], **extra))
    for e in regions:
        t = engineering_text(e['text'])
        from .quotation import segments
        has_adhesive_label = any(part[0]=='adhesive.product' for part in segments(t, tolerant=False))
        # A named threadlocking product is distinct from its polymer and from
        # the fastener's base material. Retain the exact printed product token.
        context = above_header(e, regions, r'thread.?lock|th[a-z]{2,6}lock|locking\s+(?:application|specification)|塗膠規範|防鬆膠')
        for m in PRODUCT.finditer(t):
            if has_adhesive_label: continue
            if re.search(r'請.*(?:建議|選擇)|待選|suggest|recommend|alternative|\bor\b|或', t, re.I):
                continue
            if context or re.search(r'膠|塗佈|adhesive|patch|thread.?lock|precote|loctite|tuflok', t, re.I):
                add(e, 'adhesive.product', m[0], '指定防鬆膠／塗膠產品', context=context,
                    role_binding='printed_adhesive_product')
        thread=re.fullmatch(r'M\s*\d+(?:[.,]\d+)?\s*[x×X]\s*\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+[a-zA-Z])?',t.strip())
        thread_header=above_header(e,regions,r'full\s+thread|螺紋|公制螺紋',distance=.035) if thread else None
        if thread_header:
            add(e,'thread.specification',thread[0],'圖上螺紋規格',context=thread_header,
                role_binding='printed_thread_callout')
        # Standard headings are themselves explicit engineering roles, unlike
        # a standard number accidentally returned as a quantity or order ID.
        standard=re.search(r'\bStandard\s*:\s*(.+)',t,re.I)
        if standard and re.search(r'\b(?:DIN|ISO|ASME|ASTM|JIS)\s*\d[\d\s.()/-]*(?:\b|$)',standard[1],re.I):
            add(e,'standard.reference',standard[1].strip(),'標準／防鬆檢驗規範',context=context)
        # Complete printed part captions are useful specifications, whereas
        # isolated dimension tokens have no established feature role.
        if re.search(r'\b(?:screw|bolt|stud)\b|螺絲|螺栓', t, re.I) and re.search(r'\bM\s*\d+(?:\.\d+)?\s*[x×X]\s*\d', t):
            if not re.search(r'\b(?:revision|was|changed|from|to)\b|變更|改為', t, re.I):
                add(e, 'product.specification', t.strip(), '零件名稱／完整圖上規格',
                    role_binding='printed_part_caption')
        # Installation/removal and loaded/unloaded torques are different
        # engineering requirements, even when the same magnitude is printed.
        role = next((name for name in TORQUE_ROLES if re.search(name+r'\s*扭[力矩]', t)), None)
        if not role: continue
        m = re.search(r'扭[力矩]\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(N\s*[.·]?\s*m(?:m)?|kgf\s*[.·-]?\s*cm|lbf\s*[.·-]?\s*in)\s*(以上|以下|至少|至多)?', t, re.I)
        if not m: continue
        load = re.search(r'(有負載|沒有負載|無負載)', t)
        heading = None if load else above_header(e, regions, r'有負載|沒有負載|無負載')
        load_text = load[0] if load else (heading['text'] if heading else '')
        normalized_load=engineering_text(load_text)
        condition = 'unloaded' if re.search(r'沒有負載|無負載', normalized_load) else 'loaded' if '有負載' in normalized_load else 'unspecified_load'
        operator = 'ge' if m[3] in ('以上', '至少') else 'le' if m[3] in ('以下', '至多') else 'eq'
        add(e, f'torque.{condition}.{TORQUE_ROLES[role]}', m[1], role+'扭力'+('（'+load_text.strip('：: ')+'）' if load_text else '（負載條件待核對）'),
            context=heading, unit=m[2].strip(), operator=operator, conditions=load_text.strip('：: '),
            raw_value=m[0], engineering_category='檢驗與品質文件')
    return result


def rfq_issuer_candidates(regions):
    """Nailok recipient + RFQ + unique printed header identifies issuer role.

    It is an inspectable role interpretation, never an approved customer fact.
    """
    regions=measured_regions(regions); output=[]
    for page in sorted({e['page'] for e in regions}):
        local=[e for e in regions if e['page']==page]
        titles=[e for e in local if e['box'][1]<.25 and re.search(r'委外加工詢價單|詢價單|request\s+for\s+quotation|\bRFQ\b',e['text'],re.I)]
        recipients=[e for e in local if e['box'][1]<.35 and re.search(r'(?:此致|TO)\s*[:：].*(?:台灣耐落|臺灣耐落|Nailok|Nylok)',e['text'],re.I)]
        companies=[e for e in local if e['box'][1]<.2 and re.fullmatch(r'[\u3400-\u9fff]+(?:股份)?有限公司',e['text'].strip())
                   and not re.search(r'耐落',e['text'])]
        unique={canonical(e['text']) for e in companies}
        if len(unique)!=1 or not titles or not recipients: continue
        e=companies[0]; ids=[e['id'],titles[0]['id'],recipients[0]['id']]
        output.append(requirement('customer.name',e['text'].strip(),label='詢價發出方（客戶角色待確認）',
            origin='engineering_projection',scope=f'page:{page}',evidence_ids=ids,
            role_binding='rfq_issuer_to_nailok',semantic_rule=VERSION,
            extraction_notes=['由公司圖頭、詢價單及耐落收件人共同推定客戶角色，請人工核對'],
            candidates=[{'source':VERSION,'value':e['text'].strip(),'evidence_ids':ids}]))
    return output


def rfq_issuer_supported(r,regions):
    if r.get('role_binding')!='rfq_issuer_to_nailok': return False
    # Recompute the rule from measured page context, including competing names.
    return any(canonical(x['value'])==canonical(r['value']) and
               set(x['evidence_ids'])<=set(r['evidence_ids'])
               for x in rfq_issuer_candidates(list(regions.values())))


def normalize_model_torque(item, regions):
    """Reparse a model's torque from the measured box; never convert its units."""
    if not item['key'].startswith('torque'): return item
    options = [r for r in semantic_requirements(regions) if r['key'].startswith('torque.')]
    matches = []
    for r in options:
        e = next(x for x in regions if x['id'] == r['evidence_ids'][-1])
        if canonical(e['text']) == canonical(item['evidence']) and e['box'] == item['box']:
            matches.append(r)
    if len(matches) == 1:
        r = matches[0]
        return {**item, **{k: r[k] for k in ('key','label','value','unit','operator','scope','conditions','subject')},
                'engineering_normalized': True,'normalized_evidence_ids':list(r['evidence_ids'])}
    # Keep a visible reading without treating unknown conditions as a shared
    # torque assertion or attempting arithmetic on model prose.
    return {**item, 'key': 'observation.model', 'operator': 'text',
            'label': item['label']+'（扭力條件待核對）'}
