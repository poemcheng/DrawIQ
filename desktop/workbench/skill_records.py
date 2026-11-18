"""Read-only, source-grounded skill records and deterministic review exports.

The machine decision and the existing human review are separate facts. This
module does not alter requirements, quotation references, approvals or learning.
"""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from .domain import box_valid, digest
from .quotation_layout import QUOTE_FIELDS, equivalent_value, quotation_rows

SCHEMA_VERSION='drawiq-skill-record/0.9.1'
OWNED_SCHEMA_VERSIONS=frozenset(('drawiq-skill-record/0.9.0',SCHEMA_VERSION))
REVIEW_THRESHOLD=.8
EXTRA_FIELDS={'contact.phone':'聯絡電話','contact.fax':'傳真','drawing_number':'圖號',
              'part_number':'零件／料號','engineering.specification':'工程規格候選'}
_WRITE_LOCK=threading.Lock()


def _skill_report(doc):
    from .quote_skill import analyze
    return analyze(doc)


def _confidence(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=1:
        return float(value)
    return None


def _rank_score(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
        return float(value)
    return None


def _json_value(value):
    try:
        json.dumps(value,allow_nan=False)
        return copy.deepcopy(value)
    except (TypeError,ValueError):
        return None


def _source_context(doc):
    regions={}; duplicates=set()
    for region in [*doc.get('regions',[]),*doc.get('unlocated_ocr',[])]:
        rid=region.get('id')
        if rid in regions: duplicates.add(rid)
        regions[rid]=region
    pages={p.get('number'):p for p in doc.get('pages',[])}
    return regions,pages,duplicates,{r['id'] for r in doc.get('unlocated_ocr',[])}


def _sources(refs,context):
    """Rebuild evidence from document IDs; never trust generated text or boxes."""
    regions,pages,duplicates,unlocated=context
    valid=[]; invalid=[]; seen=set()
    for ref in refs:
        rid=ref.get('region_id') if isinstance(ref,dict) else ref
        if not isinstance(rid,str):
            invalid.append('<missing_region_id>'); continue
        if rid in seen: continue
        seen.add(rid); region=regions.get(rid)
        page=pages.get(region.get('page')) if region else None
        located=bool(region and box_valid(region.get('box')))
        missing_box=bool(region and rid in unlocated and region.get('box') is None)
        if (not region or rid in duplicates or region.get('kind','evidence')!='evidence' or
            not page or not (located or missing_box)):
            invalid.append(rid); continue
        width=page.get('width'); height=page.get('height')
        pixels=all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0
                   for v in (width,height))
        box=list(region['box']) if located else None
        original_bbox=region.get('source_bbox')
        original_bbox_valid=(isinstance(original_bbox,list) and len(original_bbox)==4 and
                             all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in original_bbox) and
                             0<=original_bbox[0]<original_bbox[2] and 0<=original_bbox[1]<original_bbox[3])
        bbox=([box[0]*width,box[1]*height,box[2]*width,box[3]*height] if located and pixels else
              list(original_bbox) if original_bbox_valid else None)
        source={'region_id':rid,'page':region['page'],'box':box,
                'bbox':bbox,'located':located,
                'bbox_coordinate_space':'rendered_page_pixels','text':region.get('text',''),
                'method':region.get('source','unknown'),
                'methods':copy.deepcopy(region.get('methods',[])),
                'source_contract':region.get('source_contract'),
                'source_issues':copy.deepcopy(region.get('source_issues',[])),
                'confidence_kind':'supplied_transcription_score' if region.get('source_contract')=='provided_transcription' else 'source_engine_score',
                'confidence':_confidence(region.get('ocr_confidence',region.get('confidence')))}
        if not pixels and 'page_dimensions_missing' not in source['source_issues']:
            source['source_issues'].append('page_dimensions_missing')
        valid.append(source)
    return valid,invalid


def _empty(key,label):
    return {'key':key,'label':label,'raw':None,'original_raw':None,'normalized':None,
            'source':None,'sources':[],'confidence':None,'status':'missing',
            'machine_status':'missing','human_reviewed':False,'review_status':None,
            'original_human_reviewed':False,'proposed':False,'selected_proposal':None,
            'proposal_issues':[],
            'support_verified':False,'selected_requirement_id':None,'selected_value':None,
            'unit':None,'automatic':False,'alternatives':[],'issues':[],'invalid_source_ids':[]}


def _finalize(field):
    sources=field['sources']; field['source']=sources[0] if sources else None
    scores=[s['confidence'] for s in sources]
    field['confidence']=min(scores) if scores and all(score is not None for score in scores) else None
    kinds={s['confidence_kind'] for s in sources}
    field['confidence_kind']=next(iter(kinds)) if len(kinds)==1 else 'mixed_source_scores' if kinds else 'not_provided'
    issues=list(field['issues'])
    for source in sources: issues+=source['source_issues']
    if any(s['bbox'] is None for s in sources): issues.append('missing_bbox')
    if field['invalid_source_ids']: issues.append('invalid_source_reference')
    if field['raw'] is not None and not sources: issues.append('ungrounded_value')
    if field['raw'] is not None and field['confidence'] is None: issues.append('engine_confidence_unknown')
    if any(str(s['method']).startswith('VLM:') for s in sources) and not (field['human_reviewed'] and field['support_verified']):
        issues.append('unverified_model_source')
    if field['confidence'] is not None and field['confidence']<REVIEW_THRESHOLD: issues.append('low_engine_confidence')
    if field['raw'] is not None and field['normalized'] is None: issues.append('normalization_unresolved')
    field['issues']=list(dict.fromkeys(issues))
    if field['status']=='extracted' and field['issues']:
        field['status']='needs_review'
    if field['status']=='missing':
        field['raw']=None; field['normalized']=None
    return field


def _from_skill(item,label,context):
    key=item['key']; field=_empty(key,item.get('label') or label)
    state=item.get('status','needs_review')
    field['status']=state if state in ('extracted','needs_review','missing','conflict') else 'needs_review'
    field['machine_status']=state
    field['original_raw']=item.get('raw')
    field['raw']=item.get('raw') if isinstance(item.get('raw'),str) else None
    field['normalized']=_json_value(item.get('normalized'))
    field['sources'],field['invalid_source_ids']=_sources(item.get('sources') or ([item['source']] if item.get('source') else []),context)
    field['issues']=[x if isinstance(x,str) else x.get('code','unspecified') for x in item.get('issues',[])]
    field['alternatives']=[_from_skill({**alt,'key':key},field['label'],context)
                           for alt in item.get('alternatives',[]) if isinstance(alt,dict)]
    return _finalize(field)


def _matching_normalization(key,value,item):
    if not item or item.get('normalized') is None: return None
    normalized=item['normalized']; comparison=normalized
    if isinstance(normalized,dict):
        if key=='quotation.quantity': comparison=normalized.get('value')
        elif key=='quotation.price': comparison=normalized.get('amount')
        else: comparison=normalized.get('normalized')
    if comparison is None: return None
    if equivalent_value(key,str(value))==equivalent_value(key,str(comparison)):
        return _json_value(normalized)
    return None


def _from_requirement(r,key,label,context,*,automatic=False,item=None,explicit=False,
                      document_type='unknown',length_unit=None):
    field=_empty(key,label)
    field.update(selected_requirement_id=r['id'],selected_value=r.get('value',''),unit=r.get('unit') or None,
                 human_reviewed=r.get('status')=='verified',review_status=r.get('status'),
                 original_human_reviewed=r.get('status')=='verified',
                 support_verified=bool(r.get('support_verified')),automatic=automatic,
                 machine_status=item.get('status') if item else None)
    field['requirement_context']={name:copy.deepcopy(r.get(name)) for name in
                                  ('origin','subject','scope','conditions','operator','role_binding','extraction_notes')}
    field['sources'],field['invalid_source_ids']=_sources(r.get('evidence_ids',[]),context)
    field['original_raw']=item.get('raw') if item else r.get('raw_value')
    state=r.get('state','missing'); value=r.get('value','')
    field['raw']=value if state in ('present','pending_decision','conflict') and str(value).strip() else None
    if field['raw'] is not None:
        field['normalized']=_matching_normalization(key,value,item)
        if field['normalized'] is None:
            if key in ('product.specification','engineering.specification'):
                from .quote_skill import parse_spec
                field['normalized'],errors=parse_spec(value,document_type=document_type,length_unit=length_unit)
                field['issues'] += errors
            else:
                field['normalized']=copy.deepcopy(value)
    field['status']='conflict' if state=='conflict' else 'needs_review' if state in ('unreadable','pending_decision') else 'missing' if field['raw'] is None else 'extracted'
    if item:
        field['alternatives']=[_from_skill({**alt,'key':key},label,context)
                               for alt in item.get('alternatives',[]) if isinstance(alt,dict)]
        # The selected human value is retained while the machine's conflict is
        # still visible. A verified, explicit selection is not undone here.
        resolved=explicit and field['human_reviewed'] and field['support_verified']
        if item.get('status')=='conflict' and not resolved:
            field['status']='conflict'; field['issues'].append('machine_conflict')
        elif item.get('status')=='needs_review' and _matching_normalization(key,value,item) is not None:
            field['issues'] += [x if isinstance(x,str) else x.get('code','unspecified') for x in item.get('issues',[])]
    if r.get('skill_issues'):
        field['issues'] += list(r['skill_issues'])
    if key in ('quotation.quantity','quotation.price') and str(r.get('unit','')).upper().split('/')[-1] in ('M','K'):
        field['issues'].append('unit_unverified')
    return _finalize(field)


def _alternative_requirements(candidates,key,label,context,*,document_type='unknown',length_unit=None):
    return [_from_requirement(r,key,label,context,document_type=document_type,length_unit=length_unit)
            for r in sorted(candidates,key=lambda r:r['id'])]


def _proposal(field,row,candidates,key,label,context,*,document_type='unknown',length_unit=None):
    """Keep a displayed suggestion separate from extracted or approved facts."""
    if field['raw'] is None:
        # UI placeholders describe an action; they are never field values.
        field['status']='missing'
        return field
    field['proposed']=True; field['automatic']=False
    issues=[value if isinstance(value,str) else value.get('code','unspecified')
            for value in row.get('proposal_issues',[]) if isinstance(value,(str,dict))]
    field['proposal_issues']=list(dict.fromkeys(issues))
    field['selected_proposal']={
        'chosen_requirement_id':field['selected_requirement_id'],
        'score':_rank_score(row.get('proposal_score')),
        'confidence':_confidence(row.get('proposal_confidence')),
        'score_kind':'candidate_rank_score_not_engine_confidence_or_accuracy',
        'confidence_kind':'candidate_rank_estimate_not_engine_confidence_or_accuracy',
        'reason':_json_value(row.get('proposal_reason')),
        'issues':copy.deepcopy(field['proposal_issues'])}
    field['issues']=list(dict.fromkeys(field['issues']+field['proposal_issues']))
    # Preserve parser alternatives and append every real competing requirement.
    # Generated alternative values or evidence must not replace the source IDs.
    alternatives=field['alternatives']
    ranking={alt.get('requirement_id'):alt for alt in row.get('proposal_alternatives',[])
             if isinstance(alt,dict) and isinstance(alt.get('requirement_id'),str)}
    seen={alt.get('selected_requirement_id') for alt in alternatives}
    for alternative in _alternative_requirements(candidates,key,label,context,
                                                  document_type=document_type,length_unit=length_unit):
        rid=alternative['selected_requirement_id']
        if rid!=field['selected_requirement_id'] and rid not in seen:
            if rid in ranking:
                rank=ranking[rid]
                alternative['proposal_rank']={'score':_rank_score(rank.get('score')),
                                               'score_kind':'candidate_rank_score_not_engine_confidence_or_accuracy',
                                               'reason':_json_value(rank.get('reason'))}
            alternatives.append(alternative); seen.add(rid)
    conflict=(field['status']=='conflict' or str(row.get('status','')).startswith('衝突') or
              any('conflict' in issue or issue=='competing_values' for issue in field['proposal_issues']))
    field['status']='conflict' if conflict else 'needs_review'
    field['human_reviewed']=bool(field['original_human_reviewed'] and field['support_verified'] and
                                  not field['issues'] and not conflict)
    return _finalize(field)


def build_record(doc):
    """Return a machine projection of one snapshot, without changing the input."""
    if not isinstance(doc,dict) or not isinstance(doc.get('id'),str) or not doc['id']:
        raise ValueError('A non-empty document ID is required')
    report=_skill_report(doc); context=_source_context(doc)
    dtype=report.get('document_type','unknown')
    if dtype not in ('quote_label','engineering_drawing','unknown'): dtype='unknown'
    entries=report.get('fields',[])
    if isinstance(entries,dict): entries=[{'key':key,**value} for key,value in entries.items()]
    by_key={item['key']:item for item in entries if isinstance(item,dict) and isinstance(item.get('key'),str)}
    fields={key:_from_skill(by_key[key],label,context) if key in by_key else _empty(key,label)
            for key,label in {**QUOTE_FIELDS,**EXTRA_FIELDS}.items()}
    active={r['id']:r for r in doc.get('requirements',[]) if not r.get('deleted')}
    mapping=doc.get('quotation_mapping',{})
    for row in quotation_rows(doc,include_proposals=True):
        key=row['key']; selected=row['source']; item=by_key.get(key)
        candidate_ids=list(row.get('candidate_ids',[]))
        if row.get('proposed'):
            candidate_ids += [alt['requirement_id'] for alt in row.get('proposal_alternatives',[])
                              if isinstance(alt,dict) and isinstance(alt.get('requirement_id'),str)]
        candidates=[active[rid] for rid in dict.fromkeys(candidate_ids) if rid in active]
        if selected:
            fields[key]=_from_requirement(selected,key,row['label'],context,automatic=row['automatic'],
                                          item=item,explicit=key in mapping,document_type=dtype,length_unit=doc.get('length_unit'))
            if row.get('manual_issue'):
                fields[key]['issues'].append('manual_value_requires_correction')
                fields[key]['status']='needs_review'
            if row.get('proposed'):
                fields[key]=_proposal(fields[key],row,candidates,key,row['label'],context,
                                      document_type=dtype,length_unit=doc.get('length_unit'))
        elif key in mapping:
            # Explicitly cleared/excluded right-hand fields remain cleared.
            fields[key]=_empty(key,row['label'])
            fields[key]['issues']=['manual_source_cleared' if not mapping[key] else 'selected_requirement_unavailable']
            fields[key]['alternatives']=_alternative_requirements(candidates,key,row['label'],context,document_type=dtype,length_unit=doc.get('length_unit'))
        elif candidates:
            # A rule report must never bypass the existing quotation resolver.
            fields[key]=_empty(key,row['label'])
            fields[key]['alternatives']=_alternative_requirements(candidates,key,row['label'],context,document_type=dtype,length_unit=doc.get('length_unit'))
            fields[key]['status']='conflict' if any(r.get('state')=='conflict' for r in candidates) or len({equivalent_value(key,r.get('value','')) for r in candidates if r.get('state')=='present'})>1 else 'needs_review'
            fields[key]['issues']=['quotation_source_not_selected']
        elif item and fields[key]['raw'] is not None:
            # A rule-only candidate is queryable, but is not current auto-fill.
            fields[key]['issues'].append('rule_candidate_not_current_quotation')
            fields[key]['status']='needs_review'
        fields[key]['quotation_status']=row['status']
    supplemental={'drawing_number':{'drawing_number','drawing.number','drawing.no'},
                  'part_number':{'part_number','part.number','part.no'},
                  'contact.phone':{'contact.phone'},'contact.fax':{'contact.fax'}}
    for key,aliases in supplemental.items():
        candidates=[r for r in active.values() if r.get('key') in aliases and r.get('state')=='present']
        if not candidates: continue
        alternatives=_alternative_requirements(candidates,key,EXTRA_FIELDS[key],context,document_type=dtype,length_unit=doc.get('length_unit'))
        if len({r.get('value') for r in candidates})==1:
            fields[key]=_from_requirement(sorted(candidates,key=lambda r:r['id'])[0],key,EXTRA_FIELDS[key],context,item=by_key.get(key))
        else:
            fields[key]=_empty(key,EXTRA_FIELDS[key]); fields[key].update(status='conflict',machine_status='conflict',alternatives=alternatives,issues=['multiple_distinct_values'])
    specs=[_from_skill({**item,'key':'engineering.specification'},EXTRA_FIELDS['engineering.specification'],context)
           for item in report.get('specification_candidates',[]) if isinstance(item,dict)]
    if specs:
        field=copy.deepcopy(specs[0]) if len(specs)==1 else _empty('engineering.specification',EXTRA_FIELDS['engineering.specification'])
        field.update(status='needs_review',machine_status='candidate_needs_review',alternatives=specs)
        field['issues']=list(dict.fromkeys(field['issues']+['engineering_object_and_units_unverified']))
        fields['engineering.specification']=field
    # Keep additional declared parser fields (for example currency and denominator).
    for key,item in by_key.items():
        if key not in fields: fields[key]=_from_skill(item,item.get('label',key),context)
    source_hash=doc.get('source_sha256')
    valid_hash=isinstance(source_hash,str) and re.fullmatch(r'[0-9a-fA-F]{64}',source_hash)
    issues=[copy.deepcopy(x) for x in report.get('issues',[])]
    if not valid_hash: issues.append({'field':'source_sha256','code':'source_hash_unavailable','message':'Original-source SHA256 is not present in this snapshot'})
    for key,field in fields.items():
        for code in field['issues']:
            issues.append({'field':key,'code':code,'message':'Requires evidence or manual review'})
    return {'schema_version':SCHEMA_VERSION,'document_id':doc['id'],'document_version':doc.get('version'),
            'source_sha256':source_hash.lower() if valid_hash else None,'document_sha256':digest(doc),
            'source_contract':copy.deepcopy(doc.get('source_contract')),
            'source_hash_kind':'provided_ocr_json_snapshot' if doc.get('source_contract') else 'original_document_bytes',
            'document_type':dtype,'fields':fields,'issues':issues,
            'relations':[],'relation_policy':{'mode':'explicit_only','inferred_pairing':False,
                                            'filename_pairing':False},
            'confidence_kind':'source_scores_not_accuracy_or_approval'}


def _review_queue(record):
    items=[]
    for key,field in record['fields'].items():
        reasons=[]
        if field['status'] in ('missing','needs_review','conflict'): reasons.append(field['status'])
        if field['confidence'] is not None and field['confidence']<REVIEW_THRESHOLD: reasons.append('low_engine_confidence')
        reasons += field['issues']
        if not reasons: continue
        linked={s['region_id'] for s in field['sources']}
        for alt in field['alternatives']: linked.update(s['region_id'] for s in alt['sources'])
        items.append({'document_id':record['document_id'],'document_version':record['document_version'],
                      'field':key,'label':field['label'],'status':field['status'],
                      'reasons':list(dict.fromkeys(reasons)),'region_ids':sorted(linked),
                      'located_region_ids':sorted(s['region_id'] for s in field['sources'] if s['located']),
                      'sources':field['sources'],'alternatives':field['alternatives'],
                      'human_reviewed':field['human_reviewed'],'review_status':field['review_status'],
                      'original_human_reviewed':field['original_human_reviewed'],
                      'selected_requirement_id':field['selected_requirement_id'],
                      'proposed':field['proposed'],'proposal_issues':field['proposal_issues'],
                      'selected_proposal':field['selected_proposal']})
    if record['document_type']=='unknown':
        items.append({'document_id':record['document_id'],'document_version':record['document_version'],
                      'field':'document_type','status':'needs_review','reasons':['unknown_document'],
                      'region_ids':sorted({s['region_id'] for f in record['fields'].values() for s in f['sources']}),
                      'sources':[],'alternatives':[],'human_reviewed':False,'review_status':None,
                      'selected_requirement_id':None})
    return items


def _bytes(value,*,pretty=False):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
                       indent=2 if pretty else None,separators=None if pretty else (',',':'))+'\n').encode('utf-8')


def _commit(outputdir,payloads):
    """Atomic file replacement, rollback on failure, summary committed last.

    A summary's artifact hashes let readers detect an in-progress batch. The
    output directory may contain unrelated files; only our listed outputs change.
    """
    with _WRITE_LOCK:
        _commit_unlocked(outputdir,payloads)


def _commit_unlocked(outputdir,payloads):
    outputdir=Path(outputdir).absolute()
    if outputdir.is_symlink(): raise ValueError('The output directory must not be a symlink')
    outputdir.mkdir(parents=True,exist_ok=True); outputdir=outputdir.resolve()
    recorddir=outputdir/'records'
    if recorddir.is_symlink() or not recorddir.resolve().is_relative_to(outputdir):
        raise ValueError('The records directory must remain inside the output directory')
    recorddir.mkdir(exist_ok=True)
    targets={name:outputdir/name for name in payloads}
    for target in targets.values():
        if target.is_symlink() or not target.resolve().is_relative_to(outputdir): raise ValueError('Unsafe export target')
        if target.exists() and not target.is_file(): raise ValueError('An export target is not a regular file')
    for name,target in targets.items():
        if name.startswith('records/') and target.exists():
            try:
                old=json.loads(target.read_text(encoding='utf-8')); new=json.loads(payloads[name])
            except (OSError,ValueError):
                raise ValueError('An existing record filename belongs to an unrecognized file')
            if old.get('schema_version') not in OWNED_SCHEMA_VERSIONS or old.get('document_id')!=new['document_id']:
                raise ValueError('An existing record filename belongs to a different document')
    previous=[]
    summary=outputdir/'summary.json'
    if summary.exists():
        try:
            old=json.loads(summary.read_text(encoding='utf-8'))
            if old.get('schema_version') in OWNED_SCHEMA_VERSIONS:
                previous=[name for name in old.get('files',{}) if re.fullmatch(r'records/[0-9a-f]{64}\.json',name) and name not in payloads]
        except (OSError,ValueError): pass
    for name in previous:
        target=outputdir/name
        if target.is_symlink() or not target.resolve().is_relative_to(outputdir): raise ValueError('Unsafe stale record target')
    with tempfile.TemporaryDirectory(prefix='.drawiq_skill_stage_',dir=outputdir) as tmp:
        staging=Path(tmp).resolve(); staged=staging/'new'; backups=staging/'backup'
        changed=[]
        try:
            for name,content in payloads.items():
                file=staged/name; file.parent.mkdir(parents=True,exist_ok=True)
                with file.open('wb') as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
            for name in [*sorted(n for n in payloads if n!='summary.json'),*previous,'summary.json']:
                target=outputdir/name; backup=backups/name
                existed=target.exists()
                if existed:
                    backup.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,backup)
                if name in payloads: os.replace(staged/name,target)
                elif existed: target.unlink()
                changed.append((target,backup,existed))
        except BaseException:
            for target,backup,existed in reversed(changed):
                if existed: os.replace(backup,target)
                elif target.exists(): target.unlink()
            raise


def export_records(documents,outputdir):
    """Export exactly this batch; accept one document or an iterable of snapshots."""
    docs=[documents] if isinstance(documents,dict) else list(documents)
    records_by_id={}
    for doc in docs:
        record=build_record(doc); key=record['document_id']
        if key in records_by_id and records_by_id[key]['document_sha256']!=record['document_sha256']:
            raise ValueError('A batch cannot contain conflicting versions of the same document ID')
        records_by_id[key]=record
    records=[records_by_id[key] for key in sorted(records_by_id)]
    queue=[item for record in records for item in _review_queue(record)]
    payloads={}
    for record in records:
        name=f'records/{hashlib.sha256(record["document_id"].encode("utf-8")).hexdigest()}.json'
        if name in payloads: raise ValueError('Document IDs produced a record filename collision')
        payloads[name]=_bytes(record,pretty=True)
    payloads['results.jsonl']=b''.join(_bytes(record) for record in records)
    payloads['review_queue.jsonl']=b''.join(_bytes(item) for item in queue)
    summary={'schema_version':SCHEMA_VERSION,'document_count':len(records),'review_item_count':len(queue),
             'document_type_counts':dict(sorted(Counter(r['document_type'] for r in records).items())),
             'field_status_counts':dict(sorted(Counter(f['status'] for r in records for f in r['fields'].values()).items())),
             'ground_truth_status':'not_provided','field_precision':None,'field_recall':None,'field_exact_match':None,
             'confidence_kind':'source_scores_not_accuracy_or_approval',
             'relation_policy':'explicit_only_no_filename_or_dimension_pairing',
             'files':{name:hashlib.sha256(content).hexdigest() for name,content in sorted(payloads.items())}}
    payloads['summary.json']=_bytes(summary,pretty=True)
    _commit(outputdir,payloads)
    return summary
