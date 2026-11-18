"""Printed engineering context available as review proposals, without repairing text."""
import re
from .domain import requirement


def context_candidates(regions):
    output=[]
    for e in regions:
        if e.get('kind','evidence')!='evidence' or e.get('source','').startswith('VLM:'): continue
        text=e.get('text','')
        process=re.search(r'加工製程名稱\s*[:：]\s*(.+)',text)
        if process and re.search(r'塗膠|涂胶|塗佈|塗布|塗覆|coating|adhesive',process[1],re.I):
            output.append(requirement('coating.method',process[1].strip(),label='塗膠製程（施作方式待確認）',
                evidence_ids=[e['id']],scope=f'page:{e["page"]}:region:{e["id"]}',
                origin='drawing_observation',suggestion_only=True,role_binding='printed_coating_process',
                proposal_issues=['coating_extent_unconfirmed'],engineering_category='製程與加工要求',
                candidates=[{'source':'engineering-context-proposals/0.9.1','value':process[1].strip(),'evidence_ids':[e['id']]}]))
        standard=re.search(r'\bStandard\s*[:：]\s*(.+)',text,re.I)
        if standard and re.search(r'\b(?:DIN|ISO|EN|ASME|ASTM|JIS)\s*\S+',standard[1],re.I):
            output.append(requirement('quality.requirements',standard[1].strip(),label='標準原文（編號待覆核）',
                evidence_ids=[e['id']],scope=f'page:{e["page"]}:region:{e["id"]}',
                origin='drawing_observation',suggestion_only=True,role_binding='unresolved_printed_standard',
                proposal_issues=['standard_number_requires_review'],engineering_category='檢驗與品質文件',
                candidates=[{'source':'engineering-context-proposals/0.9.1','value':standard[1].strip(),'evidence_ids':[e['id']]}]))
    return output
