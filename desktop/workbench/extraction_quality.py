"""Grounded metadata gates and ruled-table regions; scores are not probabilities."""
import re
from functools import lru_cache
from pathlib import Path
from .domain import requirement

def field_issue(key,value,evidence_text):
    text=evidence_text or ''
    if key in ('drawing_number','document_number') and re.search(r'\b(?:ISO|DIN|ASME|ASTM|JIS|CNS)[\s-]*\d',value,re.I):
        return '標準代號不能自動當成圖號／文件號；保留原文供人工判定用途'
    if key in ('customer', 'customer.name', 'material', 'adhesive.product'):
        from .quotation_validation import value_issue
        issue = value_issue(key, value, text)
        if issue: return issue
    if key in ('order_number','work_order_number'):
        label=r'(?:訂單|订单|採購|采购|工單|工单|製令|制令|\b(?:PURCHASE\s+ORDER|SALES\s+ORDER|WORK\s+ORDER|PO|SO|WO|JOB|MO)\b)'
        if not re.search(label,text,re.I): return '編號缺少獨立的訂單／工單標籤；避免 Socket、ISO 誤命中'
    if key=='document_number' and (re.search(r'\bISO\b|copyright|©',text,re.I) or not re.search(r'\d',value)):
        return '標準／版權文字或無數字的詞不能作為文件編號'
    if key in ('part_number','customer_part_number') and not re.search(r'\d',value) and not re.search(r'part\s*(?:no\.?|number|#)\s*[:：]|料號\s*[:：]|零件號\s*[:：]',text,re.I):
        return '料號候選沒有識別數字；一般 drawing／part 字詞須保留為原文，不能當成料號'
    if key=='revision' and not re.search(r'(?:^|\s|[|;；])(?:revision|rev\.?|版次|版本)\s*[:：.]?',text,re.I):
        return '版次沒有明確欄位用途；變更紀錄中的一般單字不是版次'
    if key=='supplier' and re.search(r'request\s+for|responsib|feasibility',value,re.I):
        return '往來／責任敘述不是供應商名稱'
    if key=='scale' and not re.search(r'\bSCALE\b|比例',text,re.I):
        return '比例需要標籤證據；傳真時間與標準版次不能當成比例'
    if key=='material' and ('\n' in value or re.fullmatch(r'C\s+\d+',value)):
        return '材質跨行拼接或單一 C 字母接數字，需原圖判定'
    return ''

def dimension_context_issue(text):
    if re.search(r'\d{2,4}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}:\d{2}:\d{2}',text): return '日期／時間'
    if re.search(r'(?:\b(?:ISO|DIN|ASTM|FAX|TEL|QTY|HRC|HV|CLASS|CL)\b|數量|数量|電話|电话|傳真|传真|級|级)',text,re.I): return '編號、數量或機械性質'
    return ''

def inside(box,area):
    x=(box[0]+box[2])/2; y=(box[1]+box[3])/2
    return area[0]<=x<=area[2] and area[1]<=y<=area[3]

@lru_cache(maxsize=100)
def _grid_boxes(path,mtime):
    import cv2
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        im=im.convert('L'); im.thumbnail((1600,1600)); pixels=np.asarray(im)
    h,w=pixels.shape
    bw=cv2.threshold(pixels,175,255,cv2.THRESH_BINARY_INV)[1]
    horizontal=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(max(25,w//30),1)))
    vertical=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(20,h//70))))
    joined=cv2.dilate(horizontal|vertical,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(joined,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    def bands(on): return sum(bool(v) and (i==0 or not on[i-1]) for i,v in enumerate(on))
    for contour in contours:
        x,y,cw,ch=cv2.boundingRect(contour)
        if cw<w*.15 or ch<h*.035: continue
        hs=(horizontal[y:y+ch,x:x+cw]>0).sum(axis=1)>cw*.55
        vs=(vertical[y:y+ch,x:x+cw]>0).sum(axis=0)>ch*.45
        if bands(hs)<3 or bands(vs)<3: continue
        boxes.append([x/w,y/h,(x+cw)/w,(y+ch)/h])
    return sorted(boxes,key=lambda b:(b[1],b[0]))

def grid_boxes(path):
    from .paths import resolve_path
    p=resolve_path(path)
    return _grid_boxes(str(p),p.stat().st_mtime_ns)

def table_requirements(image_path,regions,page):
    output=[]; used=set(); blocks=[]
    for index,box in enumerate(grid_boxes(image_path)):
        members=[e for e in regions if inside(e['box'],box)]
        if len(members)<6 or sum(bool(re.search(r'\d',e['text'])) for e in members)<4: continue
        rows=[]
        for e in sorted(members,key=lambda e:((e['box'][1]+e['box'][3])/2,e['box'][0])):
            cy=(e['box'][1]+e['box'][3])/2; height=e['box'][3]-e['box'][1]
            if not rows or abs(cy-rows[-1][0])>max(height*.65,.004): rows.append([cy,[]])
            rows[-1][1].append(e)
        name=f'第 {page} 頁規格表 {index+1}'
        for row,(_,entries) in enumerate(rows):
            for col,e in enumerate(sorted(entries,key=lambda e:e['box'][0])):
                r=requirement(f'table.{page}.{index}.{row}.{col}',e['text'],label=f'{name}｜第 {row+1} 列｜片段 {col+1}',
                    subject=f'table_{page}_{index}_row_{row}',scope=f'page:{page}',evidence_ids=[e['id']])
                r['table']={'name':name,'row':row,'column':f'片段 {col+1}','columns':[],
                    'block_id':f'page:{page}:grid:{index}','box':box,'header_status':'needs_review',
                    'structure':'OCR 位置分列；合併儲存格、欄名與適用規格須覆核'}
                output.append(r); used.add(e['id'])
        blocks.append({'box':box,'name':name,'region_ids':[e['id'] for e in members]})
    return output,used,blocks
