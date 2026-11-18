"""Run the pinned MinerU Kit with local models and keep its measured page boxes."""

import json
import math
import os
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

from .bounded import supervise


VERSION = '4.2.3'
BACKENDS = {
    'mineru_flash': f'MinerU {VERSION} · Flash',
    'mineru_basic': f'MinerU {VERSION} · Basic',
    'mineru_advanced': f'MinerU {VERSION} · Advanced (VLM/OCR)',
}
ADVANCED_BACKEND = 'mineru_advanced'
VLM_REPOSITORY = 'MinerU2.5-Pro-2605-1.2B-GGUF'
VLM_WEIGHTS = {
    'MinerU2.5-Pro-2605-1.2B-Q8_0.gguf': 531_066_496,
    'mmproj-MinerU2.5-Pro-2605-1.2B-Q8_0.gguf': 709_409_600,
}
TEXT_BLOCKS = {
    'text', 'ref_text', 'doc_title', 'paragraph_title', 'header', 'footer',
    'page_number', 'aside_text', 'page_footnote', 'image_caption', 'image_footnote',
    'table_caption', 'table_footnote', 'chart_caption', 'chart_footnote',
}
CONTAINERS = {'list', 'index', 'image', 'table', 'chart', 'code'}
VISUAL_TYPES = {
    'image': 'image', 'image_body': 'image', 'table': 'table', 'table_body': 'table',
    'chart': 'chart', 'chart_body': 'chart', 'code': 'code', 'code_body': 'code',
    'algorithm_body': 'code', 'equation': 'equation',
}
CLI = (
    'from importlib.metadata import version; '
    f'version("mineru") == "{VERSION}" or __import__("sys").exit("版本不符"); '
    'from mineru.kit.main import main; main()'
)


class TableText(HTMLParser):
    """Plain table rows for the whole upstream table box, with no synthetic cell boxes."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.cell_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.cell_depth += 1
        elif tag == 'br' and self.cell_depth:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell_depth:
            self.cell_depth -= 1
            self.parts.append('\t')
        elif tag == 'tr':
            self.parts.append('\n')

    def handle_data(self, value):
        if self.cell_depth:
            self.parts.append(value)

    def text(self):
        return '\n'.join(
            line.strip() for line in ''.join(self.parts).splitlines() if line.strip()
        )


def block_text(block):
    content = block.get('content')
    if block.get('type') == 'table_body' and isinstance(content, str):
        table = TableText()
        table.feed(content)
        table.close()
        return table.text()
    if block.get('type') in TEXT_BLOCKS and isinstance(content, list):
        # Formulas are derived expressions, so leave a boundary where one occurs.
        return ''.join(
            '\n' if span.get('type') == 'equation_inline' else span['content']
            for span in content if isinstance(span, dict) and isinstance(span.get('content'), str)
        ).strip()
    return ''


def pixel_box(bbox, size):
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(
        type(value) in (int, float) and 0 <= value <= 1 for value in bbox
    ):
        return None
    width, height = size
    x0, y0, x1, y1 = bbox
    if x0 < x1 and y0 < y1:
        return (
            math.floor(x0 * width), math.floor(y0 * height),
            math.ceil(x1 * width), math.ceil(y1 * height),
        )
    return None


def parse_middle(payload, size, backend):
    """DocVortex 2.0 bounding boxes are normalized to the top-left visible page."""
    from drawing_intelligence.models import TextRegion
    from drawing_intelligence.ocr import OCRResult

    tier = backend.removeprefix('mineru_')
    if payload.get('schema') != 'docvortex.middle' or payload.get('schema_version') != '2.0':
        raise ValueError('MinerU 回傳未支援的 Middle JSON 格式')
    producer = payload.get('metadata', {}).get('producer', {})
    extensions = payload.get('extensions', {})
    layout = extensions.get('docvortex_layout', {})
    metadata = extensions.get('mineru', {})
    if producer.get('name') != 'mineru' or producer.get('version') != VERSION or metadata.get('tier') != tier:
        raise ValueError('MinerU 回傳的版本或辨識層級與本次要求不符')
    pages = payload.get('pages', [])
    geometry = layout.get('pages', [])
    if (
        len(pages) != 1 or len(geometry) != 1 or layout.get('version') != 1
        or pages[0].get('page_idx') != 0 or geometry[0].get('page_idx') != 0
    ):
        raise ValueError('MinerU 回傳缺少單頁幾何資料')
    width = geometry[0].get('width_pt')
    height = geometry[0].get('height_pt')
    if not all(type(value) in (int, float) and 0 < value < 1e9 for value in (width, height)):
        raise ValueError('MinerU 回傳頁面尺寸無效')
    if not math.isclose(width / height, size[0] / size[1], rel_tol=0.02):
        raise ValueError('MinerU 回傳頁面的方向或裁切範圍與原圖不符')

    source = BACKENDS[backend]
    regions = []
    layout_regions = {}
    unlocated = 0

    def collect(blocks, visual_figure=False):
        nonlocal unlocated
        for block in blocks:
            kind = block.get('type')
            box = pixel_box(block.get('bbox'), size)
            if box and kind in VISUAL_TYPES:
                # Parent and body sometimes share exactly one measured rectangle.
                layout_regions.setdefault((box, VISUAL_TYPES[kind]), {'box': box, 'layout_type': kind})
            if kind in CONTAINERS:
                # Advanced emits descriptions for figures. Preserve their outline
                # without letting anything produced in the figure become OCR.
                child_figure = visual_figure or (backend == ADVANCED_BACKEND and kind in {'image', 'chart'})
                collect(block.get('content', []), visual_figure=child_figure)
                continue
            text = '' if visual_figure else block_text(block)
            if not text:
                continue
            if box:
                # Protocol 2.0 has no OCR confidence at this level.
                region = TextRegion(text, box, 0.0, source)
                region.layout_type = kind
                regions.append(region)
            else:
                unlocated += 1

    collect(pages[0].get('blocks', []))
    notes = ['MinerU 提供區塊座標，未提供字元信心分數；表格採整框定位。']
    if unlocated:
        notes.append(f'MinerU 有 {unlocated} 個文字區塊缺少有效座標，未納入原圖證據。')
    if not regions:
        notes.append('MinerU 未取得可對照原圖的文字區塊。')
    result = OCRResult('\n'.join(region.text for region in regions), regions, source, notes)
    result.layout_regions = list(layout_regions.values())
    return result


def local_settings():
    local_app_data = os.environ.get('LOCALAPPDATA')
    root = Path(local_app_data) / 'DrawIQ' if local_app_data else Path.home() / '.drawiq'
    root = root / 'MinerU' / VERSION
    default_python = root / 'venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    executable = Path(os.environ.get('DRAWIQ_MINERU_PYTHON') or default_python).expanduser()
    cache = Path(os.environ.get('DRAWIQ_MINERU_HOME') or root / 'runtime-cache').expanduser()
    device = os.environ.get('DRAWIQ_MINERU_ONNX_DEVICE', 'auto')
    if device not in ('auto', 'cpu', 'gpu'):
        raise ValueError('DRAWIQ_MINERU_ONNX_DEVICE 須為 auto、cpu 或 gpu')
    if not executable.is_absolute() or not executable.is_file():
        raise RuntimeError(f'找不到 MinerU {VERSION} 隔離環境，請參照手冊安裝或指定 DRAWIQ_MINERU_PYTHON。')
    if not cache.is_absolute() or not (cache / 'models').is_dir():
        raise RuntimeError('找不到 MinerU 本機模型，請依手冊下載並驗證模型。')
    return executable, cache, device


def advanced_models_ready(cache):
    """Match the upstream completion manifest and on-disk sizes without loading weights."""
    repository = cache / 'models' / VLM_REPOSITORY
    try:
        manifest = json.loads((repository / '.mineru_complete').read_text(encoding='utf-8'))
        files = manifest['files']
        return manifest['version'] == 1 and isinstance(files, dict) and all(
            files.get(name) == size and (repository / name).stat().st_size == size
            for name, size in VLM_WEIGHTS.items()
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def advanced_available():
    """Return whether the optional local Advanced setup is ready for selection."""
    try:
        _, cache, _ = local_settings()
    except (OSError, RuntimeError, ValueError):
        return False
    return advanced_models_ready(cache)


def recognize(image, backend, *, deadline=None):
    executable, cache, device = local_settings()
    advanced = backend == ADVANCED_BACKEND
    if advanced and not advanced_models_ready(cache):
        raise RuntimeError('MinerU Advanced 缺少本機 VLM 權重，請先下載並驗證 Standard 模型。')
    started = time.monotonic()
    max_time = started + (180 if advanced else 90)
    cutoff = min(max_time, deadline - 1.5) if deadline else max_time
    if cutoff <= started:
        raise TimeoutError('本頁已無剩餘的 MinerU 辨識時間')
    with tempfile.TemporaryDirectory(prefix='drawiq-mineru-') as directory:
        folder = Path(directory)
        input_path = folder / 'page.png'
        output_path = folder / 'page.json'
        config_path = folder / 'config.yaml'
        image.save(input_path, 'PNG')
        config_path.write_text(json.dumps({
            'model': {
                'source': 'local', 'base_dir': str(cache / 'models'),
                'small_backend': 'onnx', 'onnx_device': device,
                **({'vlm': {'engine': 'llama-cpp'}} if advanced else {}),
            },
        }), encoding='utf-8')
        child_env = {key: value for key, value in os.environ.items() if not key.upper().startswith('MINERU_')}
        child_env.update(
            MINERU_HOME=str(cache), MINERU_CONFIG=str(config_path), MINERU_MODEL_SOURCE='local',
            MINERU_TELEMETRY_DISABLED='1', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        )
        if time.monotonic() >= cutoff:
            raise TimeoutError('本頁已無剩餘的 MinerU 辨識時間')
        command = [
            str(executable), '-I', '-B', '-c', CLI, 'parse', str(input_path),
            '-o', str(output_path), '--format', 'middle_json', '--tier',
            backend.removeprefix('mineru_'), '--ocr-mode', 'ocr',
        ]
        if not advanced:
            command.append('--disable-image-analysis')
        result = supervise(
            command,
            cwd=folder, env=child_env, deadline=cutoff, log_path=folder / 'worker.log',
        )
        if result == 'partial_timeout':
            raise TimeoutError(f'MinerU 本機辨識超時（{cutoff - started:.0f} 秒）')
        if result != 'complete' or not output_path.is_file():
            raise RuntimeError(
                f'MinerU 本機解析失敗，請檢查 {VERSION} 安裝、模型與 ONNX 裝置。'
            )
        return parse_middle(json.loads(output_path.read_text(encoding='utf-8')), image.size, backend)
