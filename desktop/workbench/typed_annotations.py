"""Source-bound engineering syntax. No unit, M multiplier or feature is guessed."""
import re
import unicodedata
from decimal import Decimal
from .domain import requirement

VERSION = 'typed-annotations/0.10.0'
N = r'(?:\d+(?:\.\d+)?|\.\d+)'

def parse(text):
    t = unicodedata.normalize('NFKC', text).replace('−', '-').replace('×', 'x')
    out = []
    def add(key, value, label, match, **extra):
        out.append(dict(key=key, value=value, label=label, raw_value=match.group(0),
                        source_span=list(match.span()), **extra))
    # A metric thread with three numbers explicitly identifies pitch and length.
    for m in re.finditer(r'(?<![\w])M\s*('+N+r')\s*[xX]\s*('+N+r')(?:\s*[xX]\s*('+N+r'))?(?:\s*[-–]\s*(\d+[gGhH]))?', t):
        add('dimension.thread', m.group(0), '公制螺紋原文', m,
            thread_diameter=m[1], thread_pitch=m[2] if m[3] or m[4] else '',
            thread_length=m[3] or '', thread_class=m[4] or '',
            extraction_notes=[] if m[3] or m[4] else ['兩個數字可能為直徑×長度或直徑×螺距，須人工確認'])
    for m in re.finditer(r'(?<![\d.])('+N+r')\s*(?:±|\+\s*/\s*-)\s*('+N+r')', t):
        add('dimension.linear', m[1], '尺寸／對稱公差', m,
            upper_tolerance=str(Decimal(m[2])), lower_tolerance=str(-Decimal(m[2])))
    for m in re.finditer(r'(?<![\d.])('+N+r')\s*\+\s*('+N+r')\s*(?:/\s*)?-\s*('+N+r')', t):
        add('dimension.linear', m[1], '尺寸／非對稱公差', m,
            upper_tolerance=str(Decimal(m[2])), lower_tolerance=str(-Decimal(m[3])))
    for m in re.finditer(r'(≥|≤|>=|<=)\s*('+N+r')\s*(°|mm|μm|um)?', t):
        add('dimension.angle' if m[3]=='°' else 'dimension.limit', m[2], '限制條件', m,
            operator='ge' if m[1] in ('≥','>=') else 'le', unit=m[3] or '')
    for m in re.finditer(r'(?:QTY\s*[:：]?|QUANTITY\s*[:：]?|數量\s*[:：]?)\s*('+N+r'(?:,\d{3})*)\s*(M|PCS|PC|件)?', t, re.I):
        suffix=(m[2] or '').upper()
        add('quantity', m[1]+suffix, '數量（原文）', m,
            quantity_magnitude=m[1], quantity_suffix=suffix,
            state='pending_decision' if suffix=='M' else 'present',
            extraction_notes=['M 的倍數及單批／年度範圍須人工確認，不自動換算'] if suffix=='M' else [])
    for m in re.finditer(r'(?:圖號|DRAWING\s*(?:NO\.?|NUMBER)|DWG\s*NO\.?)\s*[:：#]?\s*([A-Z0-9][A-Z0-9._/-]{3,})', t, re.I):
        add('drawing_number',m[1],'圖號（有明確標籤）',m)
    return out

def candidates(regions):
    out=[]
    for e in regions:
        for data in parse(e['text']):
            fields=dict(data); key=fields.pop('key'); value=fields.pop('value'); label=fields.pop('label')
            fields.setdefault('unit','')
            r=requirement(key,value,label=label,evidence_ids=[e['id']],
                subject='unassigned_feature' if key.startswith('dimension.') else 'document',
                scope=f'page:{e["page"]}:region:{e["id"]}',origin='typed_annotation',**fields)
            r['candidates']=[{'source':VERSION,'value':value,'raw_value':data['raw_value'],
                              'evidence_ids':[e['id']]}]
            out.append(r)
    return out
