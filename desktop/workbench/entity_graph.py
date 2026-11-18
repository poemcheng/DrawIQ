"""Typed entity/relation network with separately addressable assertion provenance."""
from .domain import canonical, digest

ENTITY_FIELDS = {
    'customer.name': ('Customer','客戶','quoted_to','報價對象'),
    'customer': ('Customer','客戶','quoted_to','報價對象'),
    'material': ('Material','材質','specifies_material','指定材質'),
    'product.specification': ('Specification','規格','has_specification','採用規格'),
    'thread.specification': ('Specification','規格','has_specification','採用規格'),
    'adhesive.product': ('Adhesive','膠種','uses_adhesive','使用膠種'),
    'coating.color': ('Color','顏色','specifies_color','指定顏色'),
    'coating.method': ('CoatingMethod','塗佈方式','uses_method','塗佈方式'),
    'quotation.project': ('Project','專案','belongs_to_project','所屬專案'),
    'quotation.salesperson': ('Person','報價人員','prepared_by','經辦人員'),
    'standard.reference': ('Standard','規範','requires_standard','要求規範'),
}
ATTRIBUTE_KEYS={'quotation.number','quotation.date','quotation.valid_until','quotation.quantity','quotation.price','quotation.currency','quotation.price_unit','contact.phone','contact.fax'}

def entity_graph(documents, facts_fn, approved_only=False):
    nodes={}; edges={}; unresolved=[]
    def node(nid,typ,label,layer='entity',**data):
        nodes.setdefault(nid,{'id':nid,'type':typ,'label':label,'layer':layer,**data})
        return nid
    def link(source,relation,target,label=None,provenance=None,layer='semantic'):
        key=(source,relation,target)
        edge=edges.setdefault(key,{'id':'edge:'+digest(key)[:24],'source':source,'relation':relation,'target':target,'label':label or relation,'layer':layer,'provenance':[]})
        if provenance and provenance not in edge['provenance']: edge['provenance'].append(provenance)
        edge['status']='verified' if edge['provenance'] and all(p['status']=='verified' and p['document_status'] in ('approved','released') for p in edge['provenance']) else 'needs_review'
    for doc in documents:
        if approved_only and doc['status'] not in ('approved','released'): continue
        facts=facts_fn(doc)
        if approved_only:
            facts=[f for f in facts if f['origin']=='requirement' and f['status']=='verified' and f['state']=='present' and not f['quality_flags'] and f['evidence_ids']]
        if not facts: continue
        did=node('document:'+doc['id']+':v'+str(doc['version']),'Document',doc['name'],'provenance',document_id=doc['id'],version=doc['version'],source_sha256=doc['source_sha256'])
        regions={r['id']:r for r in doc['regions']}
        quotation=any(f['key'].startswith('quotation.') for f in facts)
        roots={}
        def context(f):
            # Scope remains part of identity: two items/pages are not silently collapsed.
            scope=f.get('scope') or 'document'; subject=f.get('subject') or 'document'; token=(scope,subject)
            if token in roots: return roots[token]
            quote_numbers=[x['value'] for x in facts if x['key']=='quotation.number' and x['scope']==f['scope'] and x['state']=='present' and x['value']]
            context_label=quote_numbers[0] if len(set(quote_numbers))==1 else doc['name']
            root=node('entity:'+digest([doc['tenant'],doc['id'],scope,subject,'Quotation' if quotation else 'EngineeringScope'])[:24],
                'Quotation' if quotation else 'EngineeringScope',('報價 '+quote_numbers[0] if len(set(quote_numbers))==1 else ('報價' if quotation else '工程對象')+' · '+doc['name'])+' · '+scope,
                tenant=doc['tenant'],document_id=doc['id'],scope=scope,subject=subject,attributes=[],identity_basis='source_document_and_scope')
            item=node('entity:'+digest([root,'QuotedItem'])[:24],'QuotedItem' if quotation else 'SpecifiedItem',('報價項目' if quotation else '指定項目')+' · '+context_label,
                tenant=doc['tenant'],document_id=doc['id'],scope=scope,attributes=[],identity_basis='source_document_and_scope')
            roots[token]=(root,item); return root,item
        for f in facts:
            p={'document_id':doc['id'],'document_name':doc['name'],'document_version':doc['version'],'source_sha256':doc['source_sha256'],
               'document_status':doc['status'],'fact_id':f['id'],'requirement_id':f.get('requirement_id'),'key':f['key'],
               'evidence_ids':list(f['evidence_ids']),'status':f['status'],'state':f['state'],'scope':f.get('scope',''),'conditions':f.get('conditions',''),
               'quality_flags':f['quality_flags']}
            aid=node(did+':'+f['id'],'Assertion',f['label']+'：'+f['value'],'provenance',**p)
            link(did,'has_assertion',aid,layer='provenance')
            for rid in f['evidence_ids']+f.get('feature_ids',[]):
                r=regions.get(rid)
                if not r: continue
                eid=node(did+':'+rid,'Feature' if r['kind']=='feature' else 'Evidence',r['text'],'provenance',document_id=doc['id'],region_id=rid,page=r['page'],box=r['box'])
                link(aid,'supported_by' if r['kind']=='evidence' else 'applies_to',eid,layer='provenance')
            if f['state']!='present' or not f['value'].strip() or not f.get('summary_visible',True):
                unresolved.append({**p,'label':f['label'],'value':f['value']}); continue
            root,item=context(f)
            link(root,'quotes_item' if quotation else 'specifies_item',item,'報價項目' if quotation else '指定項目',p)
            link(aid,'describes',root,layer='provenance')
            key=f['key']
            if key in ATTRIBUTE_KEYS:
                target=item if key in ('quotation.quantity','quotation.price','quotation.currency','quotation.price_unit') else root
                nodes[target]['attributes'].append({'key':key,'label':f['label'],'value':f['value'],'unit':f['unit'],'provenance':p})
                continue
            entity=ENTITY_FIELDS.get(key)
            if entity:
                typ,title,relation,label=entity
                # Names are shared lexical references within a tenant; not resolved legal/physical identities.
                identity=[doc['tenant'],typ,canonical(f['value']),canonical(f['unit'])]
                if typ in ('Person','Project'): identity += [doc['id']]
                target=node('entity:'+digest(identity)[:24],typ,title+'｜'+f['value'],tenant=doc['tenant'],value=f['value'],unit=f['unit'],
                            attributes=[],identity_basis='exact_normalized_label_within_tenant' if typ not in ('Person','Project') else 'document_scoped_label')
                source=root if typ in ('Customer','Project','Person') else item
                if typ in ('Adhesive','Color','CoatingMethod'):
                    process=node('entity:'+digest([item,'coating'])[:24],'CoatingApplication','塗佈作業 · '+nodes[item]['label'].split(' · ',1)[-1],tenant=doc['tenant'],document_id=doc['id'],attributes=[])
                    link(item,'requires_process',process,'要求作業',p); source=process
                link(source,relation,target,label,p)
                link(aid,'asserts_entity',target,layer='provenance')
                continue
            # Non-entity properties stay source-scoped requirements, never category/value buckets.
            target=node('entity:'+digest([doc['id'],f['id']])[:24],'Requirement',f['label']+'｜'+f['value'],document_id=doc['id'],tenant=doc['tenant'],attributes=[{'key':key,'value':f['value'],'unit':f['unit'],'provenance':p}])
            link(item,'has_requirement',target,'要求',p)
    return {'schema_version':'drawiq-graph/0.4.3','nodes':list(nodes.values()),'edges':list(edges.values()),'unresolved':unresolved,
            'semantics':'Typed entity network. Edge provenance retains review state and source version. Exact labels share reference nodes within a tenant; this does not prove customer identity, material equivalence, or interchangeable parts. Missing/conflicting values are not entities. Attributes are not separate value nodes.'}
