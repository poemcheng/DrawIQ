"""Local JSON-LD provenance export. Pending readings stay outside trusted facts."""
from .domain import validate_document

NS='https://drawiq.local/schema/'
def export_graph(doc):
    validate_document(doc)
    base='urn:drawiq:'+doc['id']+':'
    nodes=[]; trusted=[]
    for e in doc['regions']:
        nodes.append({'@id':base+e['id'],'@type':'Evidence','text':e['text'],
                      'page':e['page'],'box':str(e['box']),'source':e['source']})
    for r in doc['requirements']:
        if r.get('deleted'): continue
        verified=r['status']=='verified' and r['support_verified'] and r['state']=='present' and bool(r['evidence_ids'])
        node={'@id':base+r['id'],'@type':'Observation','key':r['key'],'value':r['value'],
              'unit':r['unit'],'status':r['status'],'state':r['state'], 'humanVerified':verified,
              'sourceSha256':doc['source_sha256'],
              'evidence':[{'@id':base+x} for x in r['evidence_ids']]}
        for key in ('upper_tolerance','lower_tolerance','raw_value'):
            if key in r: node[key]=r[key]
        nodes.append(node)
        if verified: trusted.append(node['@id'])
    return {'@context':{'@vocab':NS},'@graph':nodes,'trustedFactIds':trusted,
            'documentVersion':doc.get('version',0),'sourceSha256':doc['source_sha256']}

def validate_graph(payload):
    import json
    from rdflib import Graph
    from pyshacl import validate
    data=Graph().parse(data=json.dumps(payload,ensure_ascii=False),format='json-ld')
    shapes=Graph().parse(data='''@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix d: <https://drawiq.local/schema/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
d:ObservationShape a sh:NodeShape; sh:targetClass d:Observation;
 sh:property [sh:path d:key;sh:minCount 1;sh:datatype xsd:string];
 sh:property [sh:path d:value;sh:minCount 1;sh:datatype xsd:string];
 sh:property [sh:path d:sourceSha256;sh:minCount 1;sh:datatype xsd:string];
 sh:property [sh:path d:humanVerified;sh:minCount 1;sh:datatype xsd:boolean].
d:EvidenceShape a sh:NodeShape;sh:targetClass d:Evidence;
 sh:property [sh:path d:page;sh:minCount 1;sh:minInclusive 1];
 sh:property [sh:path d:source;sh:minCount 1;sh:datatype xsd:string].
''',format='turtle')
    conforms,_,report=validate(data,shacl_graph=shapes,inference='none',advanced=False)
    return {'conforms':bool(conforms),'report':str(report),
            'engineering_accuracy_verified':False,'scope':'JSON-LD source and field structure only'}
