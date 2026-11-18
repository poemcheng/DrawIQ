from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TextRegion:
    text: str
    box: tuple[int, int, int, int]
    confidence: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["box"] = list(self.box)
        return data


@dataclass
class FieldValue:
    key: str
    label: str
    value: str = ""
    confidence: float = 0.0
    source: str = ""
    evidence: str = ""
    page_number: int = 1
    reviewed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": self.evidence,
            "page_number": self.page_number,
            "reviewed": self.reviewed,
        }


@dataclass
class Dimension:
    raw: str
    kind: str
    nominal: str = ""
    upper_tolerance: str = ""
    lower_tolerance: str = ""
    unit: str = ""
    confidence: float = 0.0
    page_number: int = 1
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 4)
        return data


@dataclass
class ExtractedTable:
    name: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    page_number: int = 1
    confidence: float = 0.0
    source: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": self.columns,
            "rows": self.rows,
            "page_number": self.page_number,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": self.evidence,
        }


@dataclass
class PageResult:
    page_number: int
    page_count: int
    image_path: Path
    raw_text: str = ""
    ocr_engine: str = ""
    text_regions: list[TextRegion] = field(default_factory=list)
    fields: dict[str, FieldValue] = field(default_factory=dict)
    dimensions: list[Dimension] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    standards: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    annotated_path: Path | None = None
    vlm_model: str = ""
    vlm_status: str = "未啟用"
    vlm_raw: str = ""

    def to_dict(self, include_regions: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "page_number": self.page_number,
            "page_count": self.page_count,
            "image_path": str(self.image_path),
            "annotated_path": str(self.annotated_path or ""),
            "ocr_engine": self.ocr_engine,
            "raw_text": self.raw_text,
            "confidence": round(float(self.confidence), 4),
            "warnings": self.warnings,
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "tables": [table.to_dict() for table in self.tables],
            "standards": self.standards,
            "notes": self.notes,
            "vlm": {"model": self.vlm_model, "status": self.vlm_status, "raw": self.vlm_raw},
        }
        if include_regions:
            data["text_regions"] = [region.to_dict() for region in self.text_regions]
        return data


@dataclass
class DocumentResult:
    document_id: str
    source_path: Path
    source_sha256: str
    pages: list[PageResult] = field(default_factory=list)
    fields: dict[str, FieldValue] = field(default_factory=dict)
    dimensions: list[Dimension] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    standards: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "需覆核"

    def to_dict(self, include_regions: bool = True) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_path": str(self.source_path),
            "source_sha256": self.source_sha256,
            "page_count": len(self.pages),
            "confidence": round(float(self.confidence), 4),
            "status": self.status,
            "warnings": self.warnings,
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "tables": [table.to_dict() for table in self.tables],
            "standards": self.standards,
            "notes": self.notes,
            "pages": [page.to_dict(include_regions=include_regions) for page in self.pages],
        }


@dataclass
class AnalysisRun:
    run_id: str
    created_at: str
    engine_version: str
    profile_id: str
    output_dir: Path
    documents: list[DocumentResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    json_path: Path | None = None
    csv_path: Path | None = None
    xlsx_path: Path | None = None

    def to_dict(self, include_regions: bool = True) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "engine_version": self.engine_version,
            "profile_id": self.profile_id,
            "output_dir": str(self.output_dir),
            "errors": self.errors,
            "documents": [doc.to_dict(include_regions=include_regions) for doc in self.documents],
        }
