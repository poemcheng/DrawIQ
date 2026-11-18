from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_PATH = APP_ROOT / "config" / "enterprise_general.json"


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionProfile:
    profile_id: str
    name: str
    version: str
    review_threshold: float
    critical_fields: tuple[str, ...]
    fields: tuple[FieldDefinition, ...]

    @property
    def field_map(self) -> dict[str, FieldDefinition]:
        return {field.key: field for field in self.fields}


def load_profile(path: Path | str = DEFAULT_PROFILE_PATH) -> ExtractionProfile:
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    fields = tuple(
        FieldDefinition(
            key=str(item["key"]),
            label=str(item["label"]),
            aliases=tuple(str(alias) for alias in item.get("aliases", [])),
        )
        for item in data["fields"]
    )
    return ExtractionProfile(
        profile_id=str(data["profile_id"]),
        name=str(data["name"]),
        version=str(data.get("version", "1.0")),
        review_threshold=float(data.get("review_threshold", 0.72)),
        critical_fields=tuple(str(key) for key in data.get("critical_fields", [])),
        fields=fields,
    )
