"""Lossless, source-bound engineering candidate catalogue.

An observation is a reading of a printed region, not a released requirement.
Symbol descriptions classify notation; they do not infer feature attachment.
"""
import re
from .domain import canonical, digest, requirement

VERSION = 'engineering-candidates/0.9.1'
SYMBOLS = {'Ø':'直徑符號','⌀':'直徑符號','Φ':'直徑符號','±':'對稱公差符號',
           '°':'角度符號','⊥':'垂直度符號','∥':'平行度符號','⌖':'位置度符號',
           '⌭':'圓柱度符號','○':'圓度候選符號','⌒':'輪廓候選符號','Ⓜ':'最大實體要求符號',
           'Ⓛ':'最小實體要求符號','△':'圖面記號（意義待核對）'}

def classify_text(text, key='', label=''):
    t = text.strip().translate(str.maketrans('须范纹胶种温镀', '須範紋膠種溫鍍')); hint = (key+' '+label).casefold()
    if key.startswith('symbol.'): return '尺寸、公差與符號', label or '工程符號'
    if key.startswith('contact.') or re.search(r'電話|傳真|\b(?:tel|fax|email|e-mail)\s*[:：.]|https?://|www\.|\+\d{1,3}[-\s]\d{1,4}[-\s]\d{6,}|[\w.+-]+@[\w.-]+\.[a-z]{2,}',t,re.I):
        return '基本資料','聯絡資訊'
    if re.search(r'日期|\b(?:date|annual)\b|\d{4}[-/]\d{1,2}[-/]\d{1,2}|PCS\s*/年',t,re.I):
        return '數量、交期與商務','日期／需求量原文'
    if re.search(r'(?<![A-Z])(?:ISO|DIN|ASME|ASTM|JIS|CNS|DX)\s*[-\d]',t,re.I):
        return '檢驗與品質文件', '規範／檢驗要求'
    if re.search(r'\bM\s*\d+(?:\.\d+)?\s*[x×X]\s*\d|#\s*\d+\s*-\s*\d+|\b(?:UNC|UNF)\b',t):
        return '尺寸、公差與符號', '螺紋標註'
    if re.search(r'±|[+-]\s*\d.*[+-]\s*\d',t): return '尺寸、公差與符號', '尺寸／公差標註'
    if re.search(r'[Ø⌀Φ]|\b(?:R|SR)\s*\d|\d\s*(?:mm\b|毫米|inch\b|[°″])',t,re.I) or key.startswith('dimension.'):
        return '尺寸、公差與符號', '尺寸標註'
    if re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:min|max)?\s*',t,re.I):
        return '尺寸、公差與符號', '數值標註（單位／用途待核對）'
    if any(s in t for s in SYMBOLS): return '尺寸、公差與符號', '符號／標註原文'
    if re.search(r'材料|材質|\bmaterial\b|\b(?:S\d+C|SUS\d+|POM|PA66|nylon|polyamide)\b',t,re.I):
        return '材料與表面處理', '材料標註'
    if re.search(r'表面|鍍|镀|封孔|\b(?:plating|finish|roughness|Ra)\b',t,re.I):
        return '材料與表面處理', '表面處理／粗糙度'
    if re.search(r'塗膠|涂胶|膠種|胶种|塗佈|耐溫|溫度|[°℃]\s*C|\b(?:coating|adhesive|patch|precoat|precote|tuflok)\b',t,re.I):
        return '製程與加工要求', '塗膠／製程要求'
    if re.search(r'扭力|扭矩|檢驗|測試|首批|每批|報告|證明|証明|\b(?:torque|inspection|test)\b',t,re.I):
        return '檢驗與品質文件', '測試／品質文件要求'
    if re.search(r'數量|数量|PCS|/年|單價|报价|報價|交期|日期|\b(?:quantity|price|date|annual)\b',t,re.I):
        return '數量、交期與商務', '數量／商務原文'
    if re.search(r'客戶|客戶料號|圖號|版次|經辦|此致|\b(?:customer|drawing|revision|part|scale)\b',t,re.I):
        return '基本資料', '圖面／往來資訊'
    from .knowledge import category
    group = category(key,label)
    return group, label if key and not key.startswith(('table.','note','observation.')) else '未分類原文'

def context_heading(r,regions):
    linked=[regions[x] for x in r['evidence_ids'] if x in regions]
    if not linked: return None
    value=linked[-1]; b=value['box']
    headings=[e for e in regions.values() if e['page']==value['page'] and
              e['box'][3]<=b[1]+.015 and b[1]-e['box'][3]<.10 and
              min(b[2],e['box'][2])>max(b[0],e['box'][0]) and
              re.search(r'thread.?lock|th[a-z]{2,6}lock|locking specification|塗膠規範|塗膠材料',e['text'],re.I)]
    return min(headings,key=lambda e:b[1]-e['box'][3]) if headings else None

def metadata(r, regions):
    linked = [regions[x] for x in r['evidence_ids'] if x in regions]
    sources = list(dict.fromkeys(method for e in linked for method in e.get('methods',[e.get('source','未知方法')])))
    methods = list(dict.fromkeys(c.get('source') for c in r.get('candidates',[]) if c.get('source')))
    group, label = classify_text(r['value'],r['key'],r['label'])
    return {'category': r.get('engineering_category',group),
            'label': r['label'] if not r['key'].startswith(('table.','note')) else label,
            'methods': list(dict.fromkeys(sources+methods)),
            'pages': sorted({e['page'] for e in linked}),
            'boxes': [e['box'] for e in linked], 'raw_text':'\n'.join(e['text'] for e in linked)}

def catalogue_candidates(doc, region_ids=None, semantic=True):
    """Deterministic IDs preserve human exclusion/corrections on repeated reads."""
    output=[]; existing={r['id'] for r in doc['requirements']}
    regions=[e for e in doc['regions'] if e['kind']=='evidence' and e['text'].strip()
             and (region_ids is None or e['id'] in region_ids)]
    def append(r, identity):
        r['id']='req_eng_'+digest([identity])[:20]
        if r['id'] not in existing: output.append(r); existing.add(r['id'])
    for e in regions:
        group,label=classify_text(e['text'])
        r=requirement('observation.text',e['text'],label=label,evidence_ids=[e['id']],
                      scope=f'page:{e["page"]}:region:{e["id"]}',origin='drawing_observation',
                      operator='text',engineering_category=group)
        r['candidates']=[{'source':e['source'],'value':e['text'],'raw_score':e.get('ocr_confidence'),
                          'evidence_ids':[e['id']],'catalogue_version':VERSION}]
        append(r,('raw',e['id']))
        for symbol,description in SYMBOLS.items():
            if symbol not in e['text']: continue
            r=requirement('symbol.'+str(ord(symbol)),symbol,label=description,evidence_ids=[e['id']],
                          subject='unassigned_feature',scope=f'page:{e["page"]}:region:{e["id"]}',
                          origin='drawing_observation',operator='text',engineering_category='尺寸、公差與符號',
                          extraction_notes=['來源框是包含此符號的原文框；符號的作用特徵須人工核對'])
            r['candidates']=[{'source':'printed-symbol-parser/0.6.0','value':symbol,'evidence_ids':[e['id']]}]
            append(r,('symbol',e['id'],symbol))
    from .typed_annotations import candidates as typed_candidates
    for r in typed_candidates(regions) if semantic else []:
        append(r, ('typed-v1', r['key'], r['raw_value'], r['evidence_ids']))
    if semantic:
        from .quotation import extract_quotation
        from .quotation_layout import source_issue
        from .engineering_semantics import semantic_requirements, measured_regions, rfq_issuer_candidates
        from .skill_projection import candidates as skill_candidates
        from .quotation_context import context_candidates
        region_map={e['id']:e for e in doc['regions']}
        skill_rows=skill_candidates(doc,region_ids=region_ids)
        for r in skill_rows:
            append(r,('quote-skill',r['key'],r['raw_value'],r['evidence_ids']))
        for r in context_candidates(regions):
            # Prefer an existing grounded quality summary. A raw standard is
            # useful only when no complete quality source has been identified.
            if r['key']=='quality.requirements' and any(x['key']=='quality.requirements' and x['state']=='present'
                    and not x.get('suggestion_only') and not x['deleted'] for x in doc['requirements']+output): continue
            append(r,('quotation-context',r['key'],r['value'],r['evidence_ids']))
        for r in semantic_requirements(regions)+rfq_issuer_candidates(regions):
            append(r,('engineering-role',r['key'],r['value'],r['evidence_ids']))
        # Quotation labels also occur in drawing title blocks. Do not require
        # that the entire page be classified as a quotation form.
        for r in extract_quotation(measured_regions(regions),detected=True,include_missing=False,tolerant=False):
            if r['key'].startswith('contact.') or r['key'] in ('quotation.currency','quotation.price_unit'): continue
            if any(x['key']==r['key'] and set(x['evidence_ids'])==set(r['evidence_ids'])
                   for x in skill_rows): continue
            heading=context_heading(r,region_map)
            if r['key']=='material' and heading:
                r.update(key='coating.material',label='塗膠材料（非零件基材）',scope='coating',critical=False)
                r['evidence_ids']=list(dict.fromkeys([heading['id']]+r['evidence_ids']))
            elif r['state']=='present' and source_issue(r,r['key'],region_map): continue
            signature=(r['key'],canonical(r['value']),r['unit'],set(r['evidence_ids']))
            if any((x['key'],canonical(x['value']),x['unit'],set(x['evidence_ids']))==signature
                   for x in doc['requirements'] if not x['deleted']): continue
            if any(x['key']==r['key'] and x['state']=='conflict' and set(x['evidence_ids']) & set(r['evidence_ids'])
                   for x in doc['requirements'] if not x['deleted']):
                r['state']='conflict'
            r['origin']='engineering_projection'
            append(r,('label',r['key'],r['value'],r['evidence_ids']))
        for e in regions:
            t=e['text'].translate(str.maketrans('须范纹胶种温镀', '須範紋膠種溫鍍'))
            thread=re.search(r'(?:螺紋|threads?(?:\s+specification)?)\s*[:：]\s*((?:M\s*\d+(?:\.\d+)?(?:\s*[xX×]\s*\d+(?:\.\d+)?){1,2}(?:\s*[-–]\s*\d+[a-zA-Z](?:\d+[a-zA-Z])?)?)|(?:#\s*\d+\s*-\s*\d+(?:\s*(?:UNC|UNF))?))',t,re.I)
            if thread:
                value=thread[1].strip()
                r=requirement('thread.specification',value,label='螺紋規格',evidence_ids=[e['id']],
                    scope=f'page:{e["page"]}',origin='engineering_projection',
                    extraction_notes=['僅帶入圖上明示螺紋；未推算螺栓長度或其他規格'],
                    candidates=[{'source':'engineering-thread-role/0.6.0','value':value}])
                append(r,('thread-role',e['id'],value))
        # Existing engineering evidence rules become selectable facts, with
        # their printed value, scope and evidence IDs retained.
        from .knowledge import facts_for_document
        facts,_=facts_for_document(doc)
        selected={e['id'] for e in regions}
        for f in facts:
            if f['origin']!='evidence_rule' or not set(f['evidence_ids'])<=selected: continue
            r=requirement(f['key'],f['value'],label=f['label'],unit=f['unit'],operator=f['operator'],
                          scope=f['scope'],conditions=f['conditions'],state=f['state'],
                          evidence_ids=f['evidence_ids'],origin='engineering_projection',critical=False,
                          engineering_category=f['category'])
            r['candidates']=[{'source':'engineering-context-rules/0.6.0','value':f['value']}]
            append(r,('rule',f['key'],f['value'],f['evidence_ids']))
        all_rows=doc['requirements']+output
        if not any(r['key']=='quality.requirements' and not r['deleted'] and not r.get('suggestion_only') for r in all_rows):
            selected=[r for r in all_rows if not r['deleted'] and r['state']=='present' and
                      r['key'] in ('standard','standards','standard.reference','document.first_batch','document.every_batch')
                      and r['subject']=='document' and r['evidence_ids'] and set(r['evidence_ids'])<=selected]
            # Distinct objects/variants on a sheet must not become one label.
            pages={region_map[e]['page'] for r in selected for e in r['evidence_ids']}
            distinct=list({canonical(r['value']):r for r in selected}.values())
            if len(distinct)>=2 and len(pages)==1:
                r=requirement('quality.requirements','\n'.join(quality_component_value(x,region_map) for x in distinct),label='品質要求',
                    operator='text',origin='engineering_projection',scope=f'page:{next(iter(pages))}',
                    evidence_ids=list(dict.fromkeys(e for x in distinct for e in x['evidence_ids'])),
                    component_ids=[x['id'] for x in distinct],composite_kind='quality',
                    composition_format='conditions/0.8.0',
                    candidates=[{'source':'engineering-quality-composition/0.6.0','component_ids':[x['id'] for x in distinct]}])
                append(r,('quality',[x['id'] for x in distinct]))
    return output

def quality_component_value(r,regions):
    """Keep first/every-batch applicability in the label, including after edits."""
    if r['key'] not in ('document.first_batch','document.every_batch'): return r['value']
    marker='首批' if r['key']=='document.first_batch' else '每批'
    for eid in r['evidence_ids']:
        m=re.search(marker+r'[^,，;；\n]+',regions[eid]['text'])
        if m and canonical(r['value']) in canonical(m[0]): return m[0].strip()
    return marker+'需附'+r['value']

def refresh_composites(doc):
    active={r['id']:r for r in doc['requirements'] if not r['deleted']}; changed=[]
    regions={e['id']:e for e in doc['regions']}
    for r in active.values():
        if r.get('composite_kind')!='quality' or r.get('manual_composite_override'): continue
        components=[active.get(x) for x in r.get('component_ids',[])]
        if any(x is None or x['state']!='present' for x in components):
            if r['state']!='conflict': r['state']='conflict'; changed.append(r['id'])
            continue
        value='\n'.join(quality_component_value(x,regions) if r.get('composition_format')=='conditions/0.8.0' else x['value'] for x in components)
        evidence=list(dict.fromkeys(e for x in components for e in x['evidence_ids']))
        if r['value']!=value or r['evidence_ids']!=evidence:
            r.update(value=value,evidence_ids=evidence,state='present'); changed.append(r['id'])
    return changed

def obligations(doc):
    """Raw observations are available for review but do not require release.
    Selecting one as a quotation source makes it a review obligation.
    """
    selected=set(doc.get('quotation_mapping',{}).values())
    depended={x for r in doc['requirements'] if not r['deleted'] for x in r.get('dependencies',[])}
    return [r for r in doc['requirements'] if not r['deleted'] and
            (r.get('origin')!='drawing_observation' or r['id'] in selected|depended or r['critical'])]

def ranked_candidates(doc,key,learned=()):
    from .quotation_layout import match_priority,source_issue,QUOTE_FIELDS,LABELS
    regions={e['id']:e for e in doc['regions']}; ranked=[]
    categories={'material':'材料與表面處理','adhesive.product':'製程與加工要求','coating.color':'製程與加工要求',
                'coating.method':'製程與加工要求','product.specification':'尺寸、公差與符號',
                'quality.requirements':'檢驗與品質文件','quotation.quantity':'數量、交期與商務',
                'quotation.price':'數量、交期與商務'}
    scores={x['requirement_id']:x for x in learned}
    for r in doc['requirements']:
        if r['deleted']: continue
        info=metadata(r,regions); priority=match_priority(r,key)
        same=info['category']==categories.get(key,'基本資料')
        score=100-priority*10 if priority is not None else 30 if same else 0
        feedback=scores.get(r['id'])
        if feedback: score+=45
        # Show low relevance too; the user can search all raw candidates.
        ranked.append({'requirement_id':r['id'],'score':score,'label':info['label'],
                       'value':r['value'],'unit':r['unit'],'category':info['category'],
                       'reason':source_issue(r,key,regions) if priority is not None else '尚未建立此欄位的明確語意關係',
                       'methods':info['methods'],'raw_text':info['raw_text'],
                       'learned':feedback})
    return sorted(ranked,key=lambda x:(-x['score'],x['label'],x['requirement_id']))
