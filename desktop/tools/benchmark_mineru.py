"""Measure local DrawIQ recognition against private, manually transcribed inputs.

The fixture JSON holds a "documents" array. Each entry supplies "file" and either
"fields" (feature keys to text, null, or {"value": "...", "unit": "..."}) or "tokens"
(literal on-drawing text to check). Use null for a visibly blank labeled field and
omit obscured or uncertain fields. Paths may be relative to the fixture file.

The output contains timings and counts only. Original documents, model artifacts,
extracted text and the private fixture remain outside the release report.
"""
import argparse
import json
import os
import platform
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workbench.bounded import analyze
from workbench.extraction import apply_analysis
from workbench.ingest import prepare_document
from workbench.nailok_flow import NailokService
from workbench.ocr_engines import BACKENDS
from workbench.quotation_layout import equivalent_value
from workbench.store import Store

PAGE_STATES = {'complete', 'partial_failure', 'partial_timeout', 'failed', 'cancelled', 'not_run'}


def normalized(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('×', 'x')
    return re.sub(r'\s+', '', text)


def cell_value(cell):
    if isinstance(cell, dict):
        return f'{cell.get("value", "")} {cell.get("unit", "")}'.strip()
    return cell or ''


def comparable_cell(key, cell):
    value = cell_value(cell)
    if key in ('quotation.date', 'quotation.valid_until'):
        value = equivalent_value(key, unicodedata.normalize('NFKC', value))
    return normalized(value)


def field_scores(expected, predictions, *, completed=True):
    counts = dict(expected_values=0, exact_values=0, incorrect_values=0, missed_values=0,
                  labeled_blanks=0, retained_blanks=0, false_fills=0, unverified_blanks=0)
    for key, expected_cell in expected.items():
        actual = predictions.get(key)
        truth = comparable_cell(key, expected_cell)
        if truth:
            prediction = comparable_cell(key, actual)
            counts['expected_values'] += 1
            if prediction == truth:
                counts['exact_values'] += 1
            elif prediction:
                counts['incorrect_values'] += 1
            else:
                counts['missed_values'] += 1
        else:
            counts['labeled_blanks'] += 1
            value = actual.get('value', '') if isinstance(actual, dict) else actual
            if normalized(value or ''):
                outcome = 'false_fills'
            else:
                outcome = 'retained_blanks' if completed else 'unverified_blanks'
            counts[outcome] += 1
    return counts


def token_scores(tokens, doc):
    readings = [
        normalized(box['text']) for box in doc['regions'] if box['kind'] == 'evidence'
    ]
    return {
        'expected_tokens': len(tokens),
        'found_tokens': sum(any(normalized(token) in value for value in readings) for token in tokens),
    }


def load_fixture(path):
    payload = json.loads(path.read_text(encoding='utf-8'))
    documents = payload.get('documents')
    if not isinstance(documents, list) or not documents:
        raise ValueError('The fixture must include a non-empty documents array.')
    for entry in documents:
        file = path.parent / entry['file']
        fields = entry.get('fields', {})
        tokens = entry.get('tokens', [])
        if not file.is_file() or not (fields or tokens):
            raise ValueError('Each entry must have an existing source and fields or tokens.')
        if any(
            cell is not None and not isinstance(cell, str) and
            (not isinstance(cell, dict) or any(not isinstance(cell.get(field, ''), str)
                                             for field in ('value', 'unit')))
            for cell in fields.values()
        ) or any(not isinstance(token, str) or not normalized(token) for token in tokens):
            raise ValueError('Fixture cells and tokens must be text.')
        yield file, fields, tokens


def evaluate(source, expected_fields, expected_tokens, backend, root):
    store = Store(root, actor=lambda: 'local comparison')
    doc = store.add(prepare_document(source, store.root, tenant='ocr_benchmark'))
    started = time.perf_counter()
    output = analyze(doc, store.root, ocr_backend=backend, use_vlm=False)
    doc = apply_analysis(store, doc['id'], doc['version'], output, 'Local OCR benchmark')
    predictions = {row['key']: row for row in NailokService(store).prefill(doc)}
    duration = time.perf_counter() - started
    states = [page.get('recognition_status') for page in doc['pages']]
    completed = bool(states) and all(state == 'complete' for state in states)
    tool_runs = output['tool_runs']
    return {
        'seconds': round(duration, 3),
        'primary_ocr_seconds': round(sum(run.get('ocr_seconds', 0.0) for run in tool_runs), 3),
        'quotation_reread_seconds': round(sum(run.get('quotation_reread_seconds', 0.0) for run in tool_runs), 3),
        'page_statuses': [state if state in PAGE_STATES else 'unknown' for state in states],
        'completed': completed,
        'recognition_boxes': sum(region['kind'] == 'evidence' for region in doc['regions']),
        'fields': field_scores(expected_fields, predictions, completed=completed),
        'drawing_tokens': token_scores(expected_tokens, doc),
    }


def totals(samples, metric, first_attempt):
    summary = {}
    for sample in samples:
        if sample['attempt'] != first_attempt or metric not in sample:
            continue
        for key, count in sample[metric].items():
            summary[key] = summary.get(key, 0) + count
    return summary


def summarize(samples):
    completed = [sample for sample in samples if sample['completed']]
    by_kind = {}
    for kind in sorted({sample['kind'] for sample in samples}):
        group = [sample for sample in samples if sample['kind'] == kind]
        finished = [sample for sample in group if sample['completed']]
        by_kind[kind] = {
            'runs': len(group),
            'completed_runs': len(finished),
            **{f'median_{key}': round(statistics.median(sample[key] for sample in finished), 3)
               if finished else None for key in ('seconds', 'primary_ocr_seconds', 'quotation_reread_seconds')},
        }
    return {
        'runs': len(samples),
        'completed_runs': len(completed),
        'method_timings': by_kind,
        'fields_on_first_attempt': totals(samples, 'fields', 1),
        'tokens_on_first_attempt': totals(samples, 'drawing_tokens', 1),
        'samples': samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True, help='Private ground-truth JSON.')
    parser.add_argument('--output', type=Path, required=True, help='Write aggregate JSON here.')
    parser.add_argument('--backend', action='append', choices=sorted(BACKENDS), required=True)
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error('--repeat must be between 1 and 10.')
    try:
        fixture = args.fixture.resolve()
        documents = list(load_fixture(fixture))
    except (OSError, ValueError, KeyError, TypeError):
        parser.error('Could not read private fixture documents or ground truth.')
    output = args.output.resolve()
    if output.suffix.lower() != '.json' or output in {fixture, *(source.resolve() for source, _, _ in documents)}:
        parser.error('--output must name a JSON report separate from all fixture inputs.')
    backends = list(dict.fromkeys(args.backend))
    results = {backend: [] for backend in backends}
    with TemporaryDirectory(prefix='drawiq-mineru-benchmark-') as work:
        for attempt in range(1, args.repeat + 1):
            for number, (source, fields, tokens) in enumerate(documents, 1):
                for backend in backends:
                    sample = {
                        'sample': number, 'attempt': attempt,
                        'kind': 'quotation_label' if fields else 'engineering_drawing',
                    }
                    try:
                        sample.update(evaluate(source, fields, tokens, backend,
                                               Path(work) / f'{attempt}_{number}_{backend}'))
                    except (OSError, RuntimeError, ValueError, KeyError) as exc:
                        sample.update(completed=False, error_type=type(exc).__name__,
                                      fields=field_scores(fields, {}, completed=False),
                                      drawing_tokens={'expected_tokens': len(tokens), 'found_tokens': 0})
                    results[backend].append(sample)

    report = {
        'schema': 'drawiq-local-recognition-comparison/1',
        'system': platform.system(),
        'logical_cpus': os.cpu_count(),
        'documents': len(documents),
        'repetitions': args.repeat,
        'timing_scope': 'bounded OCR workers, drawing extraction, persistence and field prefill',
        'exact_match_normalization': 'Unicode NFKC, case-insensitive, no whitespace, equivalent x/× and calendar dates',
        'results': {backend: summarize(samples) for backend, samples in results.items()},
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({backend: {k: v for k, v in summarize(samples).items() if k != 'samples'}
                      for backend, samples in results.items()}, ensure_ascii=False))
    return 0 if all(sample['completed'] for samples in results.values() for sample in samples) else 1


if __name__ == '__main__':
    raise SystemExit(main())
