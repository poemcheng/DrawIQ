from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .models import TextRegion
from .schema import APP_ROOT


# Larger pages can trigger native ONNX memory failures before Python can catch an
# exception. 2000 px remains legible for OCR and keeps the desktop process stable.
OCR_MAX_SIDE = 2000
_rapidocr_instance = None
_rapidocr_lock = threading.Lock()


@dataclass
class OCRResult:
    text: str
    regions: list[TextRegion]
    engine: str
    warnings: list[str] = field(default_factory=list)


def _text_is_usable(text: str) -> bool:
    compact = "".join(text.split())
    if len(compact) < 60:
        return False
    meaningful = sum(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in compact)
    return meaningful / max(1, len(compact)) >= 0.55


def _working_image(image: Image.Image) -> tuple[Image.Image, float]:
    longest = max(image.size)
    if longest <= OCR_MAX_SIDE:
        return image, 1.0
    ratio = OCR_MAX_SIDE / longest
    resized = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
    return resized, 1.0 / ratio


def recognize_page(
    image_path: Path,
    embedded_text: str = "",
    embedded_regions: list[TextRegion] | None = None,
    engine: str = "auto",
) -> OCRResult:
    selected = engine.lower().strip()
    if selected in {"auto", "pdf"} and _text_is_usable(embedded_text):
        return OCRResult(embedded_text, list(embedded_regions or []), "PDF文字層")
    if selected == "pdf":
        return OCRResult(embedded_text, list(embedded_regions or []), "PDF文字層", ["PDF 文字層內容不足"])

    image = Image.open(image_path).convert("RGB")
    working, scale = _working_image(image)
    errors: list[str] = []
    rapid_result = None

    if selected in {"auto", "rapidocr"}:
        try:
            result = _run_rapidocr(working, scale)
            rapid_result = result
            if _text_is_usable(result.text) or selected == "rapidocr":
                return result
            errors.append("RapidOCR 文字量不足，已改用 Tesseract")
        except Exception as exc:
            errors.append(f"RapidOCR 不可用：{exc}")

    try:
        result = _run_tesseract(working, scale)
        result.warnings[:0] = errors
        return result
    except Exception as exc:
        errors.append(f"Tesseract 不可用：{exc}")

    if rapid_result is not None and rapid_result.text.strip():
        rapid_result.warnings[:0] = errors
        return rapid_result
    if embedded_text.strip():
        return OCRResult(embedded_text, list(embedded_regions or []), "PDF文字層", errors)
    return OCRResult("", [], "無可用 OCR", errors or ["未取得可解析文字"])


def _rapidocr():
    global _rapidocr_instance
    with _rapidocr_lock:
        if _rapidocr_instance is None:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                # Compatibility with the original workspace environment.
                from rapidocr_onnxruntime import RapidOCR

            _rapidocr_instance = RapidOCR()
        return _rapidocr_instance


def _run_rapidocr(image: Image.Image, scale: float) -> OCRResult:
    output = _rapidocr()(np.asarray(image))
    if hasattr(output, "txts"):
        boxes = output.boxes if output.boxes is not None else []
        texts = output.txts if output.txts is not None else []
        scores = output.scores if output.scores is not None else []
        raw = list(zip(boxes, texts, scores))
    else:
        raw, _ = output
    lines: list[str] = []
    regions: list[TextRegion] = []
    for item in raw or []:
        if len(item) < 2 or not str(item[1]).strip():
            continue
        text = str(item[1]).strip()
        confidence = float(item[2]) if len(item) > 2 else 0.0
        points = np.asarray(item[0], dtype=float).reshape(-1, 2)
        x1, y1 = points.min(axis=0)
        x2, y2 = points.max(axis=0)
        lines.append(text)
        regions.append(
            TextRegion(
                text=text,
                box=(int(x1 * scale), int(y1 * scale), int(x2 * scale), int(y2 * scale)),
                confidence=max(0.0, min(1.0, confidence)),
                source="RapidOCR",
            )
        )
    return OCRResult("\n".join(lines), regions, "RapidOCR")


def _find_tesseract() -> Path | None:
    candidates = [
        APP_ROOT.parent / "tools" / "tesseract-ocr-portable" / "tesseract.exe",
        APP_ROOT / "tools" / "tesseract-ocr-portable" / "tesseract.exe",
    ]
    command = shutil.which("tesseract")
    if command:
        candidates.append(Path(command))
    return next((path for path in candidates if path.exists()), None)


def _run_tesseract(image: Image.Image, scale: float) -> OCRResult:
    import pytesseract

    exe = _find_tesseract()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        tessdata = exe.parent / "tessdata"
        if tessdata.exists():
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
    languages = "eng"
    try:
        if "chi_tra" in pytesseract.get_languages(config=""):
            languages = "eng+chi_tra"
    except Exception:
        pass

    data = pytesseract.image_to_data(
        image,
        lang=languages,
        config="--oem 3 --psm 11",
        output_type=pytesseract.Output.DICT,
    )
    lines: list[str] = []
    regions: list[TextRegion] = []
    grouped: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = {}
    for index, word in enumerate(data.get("text", [])):
        text = str(word).strip()
        try:
            raw_conf = float(data["conf"][index])
        except Exception:
            raw_conf = -1.0
        if not text or raw_conf < 25:
            continue
        left = int(float(data["left"][index]) * scale)
        top = int(float(data["top"][index]) * scale)
        width = int(float(data["width"][index]) * scale)
        height = int(float(data["height"][index]) * scale)
        line_key = (
            int(data.get("block_num", [0] * len(data["text"]))[index]),
            int(data.get("par_num", [0] * len(data["text"]))[index]),
            int(data.get("line_num", [index] * len(data["text"]))[index]),
        )
        grouped.setdefault(line_key, []).append((text, raw_conf, left, top, left + width, top + height))
    for words in grouped.values():
        line_text = " ".join(word[0] for word in words)
        average = sum(word[1] for word in words) / len(words)
        lines.append(line_text)
        regions.append(
            TextRegion(
                text=line_text,
                box=(min(word[2] for word in words), min(word[3] for word in words), max(word[4] for word in words), max(word[5] for word in words)),
                confidence=max(0.0, min(1.0, average / 100.0)),
                source="Tesseract",
            )
        )
    return OCRResult("\n".join(lines), regions, "Tesseract")
