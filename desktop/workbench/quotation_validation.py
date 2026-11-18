"""Check a quotation value's meaning and printed support, independent of OCR scores."""
import re
import unicodedata
from datetime import date


def normalized(text):
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', text))
    return ''.join(c for c in text if not unicodedata.combining(c)).casefold()


def value_issue(key, value, evidence_text='', unit='', *, check_role=True):
    """A field label is insufficient when the text describes a different role."""
    key = {'customer': 'customer.name', 'quantity': 'quotation.quantity',
           'price': 'quotation.price', 'unit_price': 'quotation.price'}.get(key, key)
    text = normalized(value).strip(' \t\n.,。:：;；()（）')
    source = normalized(evidence_text)
    source = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', source)
    if re.fullmatch(r'未(?:標示|标示|指定|提供|填寫|填写|確認|确认)|不詳|不详|unknown|not specified|n/?a|none|tbd|see notes?', text):
        return '原文未提供具體值，不能當成報價內容'
    if key == 'customer.name':
        if re.search(r'\b(?:is|are|shall(?: be)?|will(?: be)?)\s+(?:\w+\s+){0,2}responsib\w*|設計責任|设计责任|負責設計|负责设计|製造責任|制造责任', text + '\n' + source):
            return '設計／製造責任說明，不是報價客戶欄位'
        if re.search(r'customer[\s_-]*part|客[戶户](?:料[號号]|零件|圖[號号])', text + '\n' + source):
            return '客戶料號／圖號，不是客戶名稱'
        if check_role and source and not re.search(r'(?:^|\s)\s*(?:客[戶户](?:名稱|名称)?\s*[:：]?|customer\b(?:\s+(?:name|no\.?|number))?\s*[:：]?)', source):
            return '原圖未標示客戶欄位；收件人或供應商不能直接當成客戶'
        if re.search(r'\b(?:is|shall|must|including)\b|下述需求|未指定', text):
            return '此內容是敘述或未指定資訊，不是客戶名稱'
    if key == 'material':
        if re.search(r'(?:^|\W)m\s*\d+\s*[x×]|\b(?:screw|bolt|torx)\b|螺[絲丝紋纹]', text):
            return '螺紋／零件規格，不是材質'
        if re.search(r'\bnotes?\b|dimension|type,?\s*etc|未[標标]示公差', text):
            return '表頭／公差備註，不是材質'
        if re.search(r'\bpl+at\w*|封孔|鍍|镀|塗[裝装]|染黑|^w\s*/|^with\s+', text) and not re.search(r'\bsteel\b|stainless|鋼|钢|白鐵|白铁', text):
            return '表面處理，不是基材'
        if re.fullmatch(r'(?:property\s+)?(?:class|cl\.?|cla\w*)?\s*\d+\.\d+\s*[級级]?', text):
            return '強度等級不能單獨當成材質'
        if check_role and source and not re.search(r'(?:^|\s)\s*(?:\d+[.)、]\s*)?(?:material\b|材[質质]|材料)\s*[:：]?', source):
            known = r'steel|stainless|alloy|alumin(?:i)?um|brass|copper|nylon|polyamide|pom|abs|鋼|钢|白鐵|白铁|鋁|铝|銅|铜'
            bare_grade = re.fullmatch(r'(?:s\d+c|c\d{2,4}|\d{2}b\d+|sus\s*\d+|scm\s*\d+|\d{3})', text)
            if (not re.search(known, text) and not (bare_grade and normalized(evidence_text).strip(' .,:：;；') == text)) or re.search(r'\bwas\b|revision|notes?:', source):
                return '原圖沒有可支持的材質標示，不能從其他備註猜基材'
    if key == 'adhesive.product':
        if re.search(r'最高工作[溫温]度|耐[溫温]|temperature|符合.*規範|符合.*规范|請.*(?:建議|選)|请.*(?:建议|选)|^待', text):
            return '膠種條件／待選要求，不是已指定的膠別'
        if re.fullmatch(r'.*\d+\s*(?:℃|°\s*c)', text):
            return '耐溫數值，不是膠別'
    if key == 'product.specification':
        if text.startswith(('{', '[')): return '結構化辨識資料需先核對完整規格'
        if re.fullmatch(r'(?:iso|din|en|asme|astm|jis)\s*[\d\s./-]+(?:\s*(?:and|及)\s*\d+)?', text):
            return '標準代號不是完整產品規格'
    if key in ('quotation.quantity', 'quotation.price'):
        if not re.fullmatch(r'\d+(?:,\d{3})*(?:\.\d+)?\s*[^\d]*', text):
            return '缺少可辨識的數值，或混入其他數字／敘述'
    return ''


def printed_value_issue(key, value, evidence_text, unit='', *, tolerant_labels=False):
    """Require the claimed value in the linked text, including date normalization."""
    # Compare orthography without rewriting stored engineering/customer values.
    from .quotation import label_token, segments
    field_values = [text for field, text, _ in segments(evidence_text,tolerant=tolerant_labels) if field == key]
    # A bounded label repair must still identify this exact role. It only
    # repairs the label, never a company name, number, date or engineering value.
    repaired_role=tolerant_labels and bool(field_values)
    issue = value_issue(key, value, evidence_text, unit,check_role=not repaired_role)
    if issue: return issue
    linked_text = '\n'.join(field_values) if field_values else evidence_text
    wanted = label_token(value)
    support = label_token(linked_text)
    if key == 'customer.name' and field_values and not any(
            wanted.strip('.。') == label_token(text).strip('.。') for text in field_values):
        return '客戶欄位文字不一致，不能只取部分名稱'
    if unit:
        for part in unit.split('/'):
            pattern = re.escape(normalized(part).strip()).replace(r'\ ', r'\s*')
            if pattern and not re.search(r'(?<![a-z])' + pattern + r'(?![a-z])', normalized(linked_text)):
                return '來源框單位與候選不一致，不能替換數量單位／幣別／分母'
    if key in ('quotation.quantity', 'quotation.price'):
        from decimal import Decimal
        number = re.match(r'\d+(?:,\d{3})*(?:\.\d+)?', unicodedata.normalize('NFKC', value).strip())
        printed = re.findall(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])', linked_text)
        if not number or not any(Decimal(n.replace(',', '')) == Decimal(number[0].replace(',', '')) for n in printed):
            return '來源框沒有相同數值，不能用部分數字或換算值代替'
    if key not in ('material', 'quality.requirements', 'product.specification'):
        from .quotation import FIELDS, LABEL_MAP
        label, aliases = FIELDS[key]
        extra = {'quotation.quantity': ['Qty'], 'quotation.price': ['Price'],
                 'packaging.instructions': ['包裝要求']}.get(key, [])
        role_labels = [label, *aliases.split('|'), *extra]
        role_source = normalized(evidence_text).translate(LABEL_MAP)
        role_source = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', role_source)
        # Text must identify this field's role, rather than just contain its
        # value somewhere in a drawing. Split label/value boxes remain allowed.
        if not repaired_role and not any(re.search(r'(?:^|\n|[|;；]|\s{2,})\s*' + re.escape(normalized(alias).translate(LABEL_MAP)).replace(r'\ ', r'\s*') +
                             (r'(?![a-z])' if alias.isascii() else ''), role_source)
                   for alias in role_labels):
            if key != 'adhesive.product' or not re.search(r'\b(?:precote\s*\d+|loctite\s*\d+|tuflok)\b', normalized(value)):
                return '來源文字未標示此報價欄位的用途，需人工判定'
    if wanted and wanted in support: return ''
    if key in ('quotation.quantity', 'quotation.price'):
        if wanted.replace(',', '') in support.replace(',', ''): return ''
    if key in ('quotation.date', 'quotation.valid_until'):
        def dates(text):
            result = set()
            for m in re.finditer(r'(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})日?', text):
                try: result.add(date(*map(int, m.groups())).isoformat())
                except ValueError: pass
            return result
        if dates(value) & dates(linked_text): return ''
    return '來源框文字未支持這個值，需核對原圖或重新連結證據'
