from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Dimension, FieldValue, PageResult, TextRegion
from .schema import ExtractionProfile


STANDARD_PATTERN = re.compile(
    r"\b(?:ISO|DIN|EN|ASME|ASTM|JIS|SAE|AWS|BS|IEC|GB(?:/T)?|CNS)\s*[-:]?\s*"
    r"[A-Z0-9][A-Z0-9.\-/]*(?:\s*[-:]\s*[A-Z0-9.]+)?",
    re.IGNORECASE,
)

DIMENSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "公制螺紋",
        re.compile(
            r"(?<![A-Z0-9])M\s*\d+(?:[.,]\d+)?(?:\s*[xX×]\s*(?:P\s*)?\d+(?:[.,]\d+)?){1,2}"
            r"(?:\s*[-–]\s*\d+[A-Z]\d+)?",
            re.IGNORECASE,
        ),
    ),
    (
        "英制螺紋",
        re.compile(r"\b\d+\s*/\s*\d+\s*[-–]\s*\d+\s*(?:UNC|UNF|UNEF|NPT)(?:\s*[-–]\s*[123][AB])?\b", re.I),
    ),
    (
        "極限公差",
        re.compile(r"(?:[⌀ØΦφ]\s*)?\d+(?:[.,]\d+)?\s*\+\s*\d+(?:[.,]\d+)?\s*/\s*-\s*\d+(?:[.,]\d+)?"),
    ),
    (
        "正負公差",
        re.compile(r"(?:[⌀ØΦφR]\s*)?\d+(?:[.,]\d+)?\s*(?:±|\+\s*/\s*-)\s*\d+(?:[.,]\d+)?(?:\s*(?:mm|in))?", re.I),
    ),
    (
        "配合公差",
        re.compile(r"(?:[⌀ØΦφ]\s*)?\d+(?:[.,]\d+)?\s*[HhFfGgJjKkMmNnPpRrSs]\s*\d{1,2}\b"),
    ),
    (
        "直徑",
        re.compile(r"(?:⌀|Ø|Φ|φ|DIA\.?\s*)\s*\d+(?:[.,]\d+)?(?:\s*(?:mm|in))?", re.I),
    ),
    ("半徑", re.compile(r"(?<![A-Z])R\s*\d+(?:[.,]\d+)?(?:\s*(?:mm|in))?", re.I)),
    ("角度", re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:°|DEG(?:REE)?S?)\b", re.I)),
    ("倒角", re.compile(r"\b\d+(?:[.,]\d+)?\s*[xX×]\s*45\s*°?\b")),
    (
        "尺寸",
        re.compile(r"(?<![\d/.-])\d{1,3}[.,]\d{1,4}\s*(?:mm|毫米|in\b|inch(?:es)?)?", re.I),
    ),
]


FIELD_FALLBACKS: dict[str, list[re.Pattern[str]]] = {
    "document_number": [re.compile(r"(?:DOC(?:UMENT)?|FORM)\s*(?:NO|NUMBER)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I)],
    "drawing_number": [
        re.compile(r"\b[A-Z]{2,6}\d{5,}(?:[-_][A-Z0-9]+)+\b", re.I),
        re.compile(r"\b[A-Z]{1,6}[A-Z0-9]*[-_]\d{3,}(?:[-_][A-Z0-9]+)*\b", re.I),
        re.compile(r"\b\d{3,5}[-_]\d{3,5}[-_]\d{1,4}\b"),
    ],
    "work_order_number": [
        re.compile(r"(?:WORK\s*ORDER|WO|JOB|MO)\s*(?:NO|NUMBER)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
        re.compile(r"(?:工單|工单|製令|制令)(?:單)?(?:號|号)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
    ],
    "order_number": [
        re.compile(r"(?:PURCHASE\s*ORDER|SALES\s*ORDER|PO|SO)\s*(?:NO|NUMBER)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
        re.compile(r"(?:訂單|订单|採購單|采购单)(?:號|号)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
    ],
    "part_number": [
        re.compile(r"(?:PART|ITEM|MATERIAL)\s*(?:NO|NUMBER)?\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
        re.compile(r"(?:料號|料号|零件號|零件号|品號|品号)\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", re.I),
    ],
    "revision": [re.compile(r"\bREV(?:ISION)?\s*[:：.-]?\s*([A-Z0-9]{1,4})\b", re.I)],
    "scale": [
        re.compile(r"\bSCALE\s*[:：]?\s*(\d+\s*[:/]\s*\d+|NTS|FULL)\b", re.I),
        re.compile(r"\b(\d+\s*:\s*\d+)\b"),
    ],
    "units": [re.compile(r"\b(?:ALL\s+)?DIMENSIONS\s+ARE\s+IN\s+(MM|MILLIMETERS?|INCH(?:ES)?)\b", re.I)],
    "date": [re.compile(r"\b(20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]20\d{2})\b")],
    "material": [
        re.compile(r"\b(C?10B21|20MNB4|SUS\s*\d+[A-Z]?|AISI\s*\d+|SCM\s*\d+|C\s*\d{2,5}|AL\s*\d{4}|ADC\s*\d+|S[SP]\d+C|SKD\s*\d+|STEEL|STAINLESS\s+STEEL|ALUMIN(?:I)?UM|BRASS|COPPER|NYLON|ABS|POM)\b", re.I),
    ],
    "finish": [
        re.compile(r"\b(BLACK\s+OXIDE|ANODIZ(?:ED|ING)|ZINC(?:\s+NICKEL)?|NICKEL\s+PLAT(?:E|ED|ING)|CHROME\s+PLAT(?:E|ED|ING)|POWDER\s+COAT(?:ED|ING)|GEOMET\s*\d*)\b", re.I),
        re.compile(r"(染黑|陽極(?:處理)?|鍍鋅|镀锌|鍍鎳|镀镍|鍍鉻|镀铬|粉體塗裝|鋅鎳合金|三[價值价][五彩]彩?[鋅锌])"),
    ],
    "heat_treatment": [
        re.compile(r"\b(HRC\s*\d+(?:\s*[-~]\s*\d+)?|HV\s*\d+(?:\s*[-~]\s*\d+)?|HB\s*\d+)\b", re.I),
        re.compile(r"(調質|滲碳|氮化|退火|淬火|回火)"),
    ],
    "weight": [re.compile(r"(?:WEIGHT|MASS|重量)\s*[:：]?\s*(\d+(?:[.,]\d+)?\s*(?:mg|g|kg|lb))\b", re.I)],
    "quantity": [re.compile(r"(?:\b(?:QTY|QUANTITY)|數量|数量|傲量)\s*[:：]?\s*([\d,.]+\s*(?:PCS|EA|K|M)?)\b", re.I)],
    "general_tolerance": [re.compile(r"\b(ISO\s*2768(?:\s*[-./ ]\s*[A-Z]{1,2})?|±\s*\d+(?:[.,]\d+)?)\b", re.I)],
    "projection": [re.compile(r"\b((?:FIRST|THIRD)\s+ANGLE(?:\s+PROJECTION)?)\b", re.I)],
    "sheet": [re.compile(r"\b(?:SHEET|PAGE)\s*(\d+\s*(?:OF|/)\s*\d+)\b", re.I)],
}


def parse_page(
    page_number: int,
    page_count: int,
    image_path,
    raw_text: str,
    regions: list[TextRegion],
    ocr_engine: str,
    profile: ExtractionProfile,
    inherited_warnings: Iterable[str] = (),
) -> PageResult:
    text = _normalize_text(raw_text)
    fields = {
        definition.key: _extract_field(definition.key, definition.label, definition.aliases, text, page_number)
        for definition in profile.fields
    }
    document_type = _classify_document(text)
    if document_type != "工程圖":
        drawing_number = fields.get("drawing_number")
        if drawing_number and drawing_number.source == "工程規則":
            fields["drawing_number"] = FieldValue("drawing_number", drawing_number.label, page_number=page_number)
    if document_type and not fields["document_type"].value:
        fields["document_type"] = FieldValue(
            "document_type", "文件類型", document_type, 0.74, "文件分類規則", document_type, page_number
        )
    if not fields["document_number"].value:
        preferred_identifiers = {
            "工單／製令": ("work_order_number", "order_number", "part_number", "drawing_number"),
            "採購／訂單": ("order_number", "work_order_number", "part_number", "drawing_number"),
            "工程圖": ("drawing_number", "part_number", "document_number", "work_order_number"),
        }.get(document_type, ("document_number", "work_order_number", "order_number", "drawing_number", "part_number"))
        for identifier_key in preferred_identifiers:
            identifier = fields.get(identifier_key)
            if identifier and identifier.value:
                fields["document_number"] = FieldValue(
                    "document_number",
                    "文件編號",
                    identifier.value,
                    max(0.5, identifier.confidence - 0.04),
                    identifier.source,
                    f"沿用 {identifier.label}：{identifier.evidence}",
                    page_number,
                )
                break
    dimensions = extract_dimensions(text, regions, page_number)
    standards = extract_standards(text)
    notes = extract_notes(text)
    warnings = list(inherited_warnings)

    if not text.strip():
        warnings.append("未取得文字，需人工確認影像品質與 OCR 安裝")
    if document_type == "工程圖" and not dimensions:
        warnings.append("工程圖未辨識到尺寸或公差")
    thread_values = list(dict.fromkeys(item.raw for item in dimensions if "螺紋" in item.kind))
    if len(thread_values) > 1:
        warnings.append("同頁出現多個螺紋規格，請確認是多部位標註或 OCR 衝突：" + "、".join(thread_values[:8]))
    missing = [profile.field_map[key].label for key in profile.critical_fields if key in fields and not fields[key].value]
    if missing:
        warnings.append("關鍵欄位缺失：" + "、".join(missing))

    filled = [field.confidence for field in fields.values() if field.value]
    ocr_quality = _ocr_quality(regions, text)
    field_score = sum(filled) / len(filled) if filled else 0.0
    completeness = len(filled) / max(1, len(fields))
    dimension_bonus = 0.08 if dimensions else 0.0
    confidence = min(0.99, 0.50 * field_score + 0.25 * ocr_quality + 0.17 * completeness + dimension_bonus)

    return PageResult(
        page_number=page_number,
        page_count=page_count,
        image_path=image_path,
        raw_text=text,
        ocr_engine=ocr_engine,
        text_regions=regions,
        fields=fields,
        dimensions=dimensions,
        standards=standards,
        notes=notes,
        warnings=list(dict.fromkeys(warnings)),
        confidence=confidence,
    )


def _classify_document(text: str) -> str:
    upper = text.upper()
    classifiers: list[tuple[str, tuple[str, ...]]] = [
        ("工單／製令", ("WORK ORDER", "JOB ORDER", "ROUTING", "TRAVELER", "工單", "工单", "製令", "制令", "工序")),
        ("檢驗／品質文件", ("INSPECTION", "CONTROL PLAN", "CHECK SHEET", "檢驗", "检验", "品質檢查", "首件")),
        ("BOM／材料表", ("BILL OF MATERIAL", "BOM", "MATERIAL LIST", "零件表", "材料表")),
        ("採購／訂單", ("PURCHASE ORDER", "SALES ORDER", "採購單", "采购单", "訂單", "订单")),
        ("工程圖", ("DRAWING NUMBER", "DWG NO", "GENERAL TOLERANCE", "PROJECTION", "圖號", "投影法", "未註公差")),
        ("製程／作業標準", ("WORK INSTRUCTION", "SOP", "PROCESS SHEET", "作業標準", "製程表", "加工流程")),
    ]
    for label, markers in classifiers:
        if any(marker in upper for marker in markers):
            return label
    return "一般工業文件" if text.strip() else ""


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("×", "x")
    # Frequent OCR confusions inside standards are safe to normalize because ISO identifiers are numeric.
    text = re.sub(r"\bIS[0O](?=\s*\d)", "ISO", text, flags=re.I)
    text = re.sub(r"\b(ISO\s+\d{2})S(\d{2})\b", r"\g<1>5\2", text, flags=re.I)
    text = text.replace("傲量", "數量").replace("材罐", "材質")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_field(
    key: str,
    label: str,
    aliases: tuple[str, ...],
    text: str,
    page_number: int,
) -> FieldValue:
    lines = [line.strip(" |\t") for line in text.splitlines() if line.strip(" |\t")]
    for alias in sorted(aliases, key=len, reverse=True):
        alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
        pattern = re.compile(rf"^\s*{alias_pattern}(?![A-Z0-9])\s*(?:[:：#=\-]\s*)?(.*)$", re.I)
        for line_index, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue
            value = _clean_field_value(match.group(1), alias)
            if _valid_field_value(key, value):
                return FieldValue(key, label, value, 0.84, "欄位標籤", line[:180], page_number)

    dynamic_fallbacks = list(FIELD_FALLBACKS.get(key, []))
    if key == "title":
        dynamic_fallbacks.append(
            re.compile(
                r"\b((?:SCREW|BOLT|NUT|WASHER|SHAFT|BRACKET|PLATE|HOUSING|ASSEMBLY|PIN|GEAR)\b[^\n]{2,100})",
                re.I,
            )
        )
    for pattern in dynamic_fallbacks:
        match = pattern.search(text)
        if not match:
            continue
        value = _clean_field_value(match.group(1 if match.groups() else 0), "")
        if _valid_field_value(key, value):
            return FieldValue(key, label, value, 0.66, "工程規則", match.group(0)[:180], page_number)
    return FieldValue(key, label, page_number=page_number)


def _clean_field_value(value: str, alias: str) -> str:
    cleaned = value.strip(" |:：#=\t")
    cleaned = re.split(r"\s{3,}|\t|[|]", cleaned, maxsplit=1)[0]
    if alias and cleaned.upper() == alias.upper():
        return ""
    return cleaned[:160].strip()


def _valid_field_value(key: str, value: str) -> bool:
    if not value or len(value) > 160:
        return False
    upper = value.upper()
    if upper in {"N/A", "NA", "NONE", "-", "SEE BELOW"}:
        return False
    if key == "revision" and len(value) > 20:
        return False
    if key == "drawing_number" and (len(value) < 4 or not re.search(r"\d", value)):
        return False
    if key == "scale" and not re.search(r"\d\s*[:/]\s*\d|NTS|FULL", value, re.I):
        return False
    return True


def extract_dimensions(text: str, regions: list[TextRegion], page_number: int) -> list[Dimension]:
    candidates: list[tuple[str, float, str]] = []
    for region in regions:
        candidates.append((region.text, region.confidence, region.source))
    candidates.extend((line, 0.68, "OCR文字") for line in text.splitlines() if line.strip())

    dimensions: list[Dimension] = []
    seen: set[str] = set()
    for candidate_text, confidence, source in candidates:
        normalized_line = candidate_text.replace("×", "x").replace(",", ".")
        for kind, pattern in DIMENSION_PATTERNS:
            for match in pattern.finditer(normalized_line):
                raw = re.sub(r"\s+", " ", match.group(0)).strip(" .,;:")
                key = re.sub(r"\s+", "", raw).upper()
                if not raw or key in seen or _looks_like_date(raw) or not _plausible_dimension(kind, raw, normalized_line):
                    continue
                seen.add(key)
                nominal, upper, lower, unit = _dimension_parts(raw)
                dimensions.append(
                    Dimension(
                        raw=raw,
                        kind=kind,
                        nominal=nominal,
                        upper_tolerance=upper,
                        lower_tolerance=lower,
                        unit=unit,
                        confidence=max(0.45, min(0.94, confidence or 0.68)),
                        page_number=page_number,
                        source=source,
                    )
                )
    return dimensions[:250]


def _plausible_dimension(kind: str, raw: str, context: str) -> bool:
    upper_context = context.upper()
    if kind == "尺寸":
        banned = ("CLASS", "ISO", "DIN", "WEIGHT", "MASS", "QTY", "QUANTITY", "DATE", "PAGE", "SHEET")
        if any(token in upper_context for token in banned):
            return False
        if re.search(r"\d\s*(?:MG|KG|LB|G)\b", upper_context):
            return False
        # A bare decimal is useful only when the OCR region itself is a short callout.
        if not re.search(r"\b(?:MM|IN|INCH|毫米)\b", upper_context) and len(context.strip()) > len(raw) + 5:
            return False
    if kind == "配合公差":
        grade = re.search(r"[HhFfGgJjKkMmNnPpRrSs]\s*(\d{1,2})\b", raw)
        nominal = re.search(r"\d+(?:[.,]\d+)?", raw)
        if not grade or grade.group(1).startswith("0") or int(grade.group(1)) > 18:
            return False
        if nominal and float(nominal.group(0).replace(",", ".")) > 1000:
            return False
    return True


def _dimension_parts(raw: str) -> tuple[str, str, str, str]:
    unit_match = re.search(r"\b(mm|in|inch(?:es)?|毫米)\b", raw, re.I)
    unit = unit_match.group(1) if unit_match else ""
    number = re.search(r"\d+(?:[.,]\d+)?", raw)
    nominal = number.group(0).replace(",", ".") if number else ""
    plus_minus = re.search(r"±\s*(\d+(?:[.,]\d+)?)", raw)
    if plus_minus:
        tolerance = plus_minus.group(1).replace(",", ".")
        return nominal, tolerance, f"-{tolerance}", unit
    limits = re.search(r"\+\s*(\d+(?:[.,]\d+)?)\s*/\s*-\s*(\d+(?:[.,]\d+)?)", raw)
    if limits:
        return nominal, limits.group(1).replace(",", "."), "-" + limits.group(2).replace(",", "."), unit
    return nominal, "", "", unit


def _looks_like_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", value.strip()))


def extract_standards(text: str) -> list[str]:
    standards: list[str] = []
    for match in STANDARD_PATTERN.finditer(text.replace("\n", " ")):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" .,:;").upper()
        if re.match(r"^ISO\s*\d+[A-Z]+\d+", value):
            continue
        canonical = re.sub(r"[\s.-]+", "", value)
        if value and all(re.sub(r"[\s.-]+", "", existing) != canonical for existing in standards):
            value = re.sub(r"^(ISO|DIN|EN|ASME|ASTM|JIS|SAE|AWS|BS|IEC|CNS)\s*", r"\1 ", value)
            standards.append(value)
    return standards[:100]


def extract_notes(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    notes: list[str] = []
    in_notes = False
    remaining = 0
    for line in lines:
        if re.search(r"\b(?:NOTES?|REMARKS?|ATTENTION)\b|備註|注意事項|說明", line, re.I):
            in_notes = True
            remaining = 12
            notes.append(line[:300])
            continue
        numbered = bool(re.match(r"^(?:\(?\d{1,2}[.)、]|[-*•])\s*", line))
        if (in_notes and remaining > 0) or numbered:
            if len(line) >= 4:
                notes.append(line[:300])
            remaining -= 1
            if remaining <= 0:
                in_notes = False
    return list(dict.fromkeys(notes))[:60]


def _ocr_quality(regions: list[TextRegion], text: str) -> float:
    valid = [region.confidence for region in regions if region.text.strip()]
    if valid:
        return max(0.0, min(1.0, sum(valid) / len(valid)))
    compact = "".join(text.split())
    return 0.75 if len(compact) >= 100 else min(0.65, len(compact) / 150.0)
