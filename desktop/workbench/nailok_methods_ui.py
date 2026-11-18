"""Show and export saved method results with their original drawing sources."""
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re

from PyQt6.QtCore import QIODevice, QSaveFile, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .domain import box_valid


METHOD_NAMES = {
    'PDF文字層': 'PDF 文字層',
    'manual': '人工標註',
    'manual_capture': '人工框選',
    'nailok_human': '人工確認',
    'nailok_crop': '框選欄位結果',
    'extraction': '欄位擷取',
    'quotation_extraction': '報價欄位解析',
    'spatial_form': '空間欄位解析',
    'spatial-label-value': '空間欄位解析',
    'drawing_observation': '圖面觀察',
    'engineering_projection': '工程上下文解析',
    'engineering-roles/0.8.0': '工程上下文解析',
    'typed_annotation': '工程標註解析',
    'typed-annotations/0.10.0': '工程標註解析',
    'quote_skill': '報價標籤解析',
    'quote-drawing-extraction/0.9.0': '報價標籤解析',
    'reanalysis': '重新辨識候選',
    'page_budget': '頁面辨識',
    'refinement_summary': '重讀與補充',
}

STATUS_NAMES = {
    'complete': '完成', 'incomplete': '部分完成', 'partial_failure': '部分失敗',
    'partial_timeout': '逾時，保留已取得的資料', 'failed': '失敗', 'cancelled': '已取消',
    'running': '處理中', 'not_run': '尚未執行', 'not_needed': '此次無需執行',
    'disabled': '未啟用', 'budget_exhausted': '已達時間或次數上限', 'no_regions': '無可定位結果',
    'empty': '未讀到文字',
}

ANALYSIS_METHODS = {
    'spatial_form': '空間欄位解析', 'table': '表格解析', 'dimensions': '尺寸與公差擷取',
    'engineering_context': '工程上下文解析', 'quote_skill': '報價標籤解析',
}

LAYOUT_TYPES = {
    'algorithm_body': '程式區塊', 'code': '程式區塊', 'code_body': '程式區塊',
    'chart': '圖表', 'chart_body': '圖表',
    'equation': '公式', 'equation_interline': '公式', 'equation_block': '公式',
    'figure': '圖形', 'image': '圖形', 'image_body': '圖形', 'table': '表格', 'table_body': '表格',
}

RESULT_ROLE = Qt.ItemDataRole.UserRole
METHOD_ROLE = Qt.ItemDataRole.UserRole + 1
FORMATS = 'Markdown (*.md);;JSON (*.json)'


@dataclass(frozen=True)
class MethodResult:
    method: str
    label: str
    text: str
    page: int | None = None
    source_ids: tuple[str, ...] = ()
    kind: str = 'result'


def method_name(source):
    source = str(source or '來源未分類').strip() or '來源未分類'
    if source.startswith('VLM:'):
        return 'VLM · ' + source.removeprefix('VLM:').strip()
    if source.endswith(':engineering-context-reread'):
        return '工程局部重讀 · ' + source.rsplit(':', 1)[0]
    if source.endswith(':quotation-row'):
        return '報價整行重讀 · ' + source.rsplit(':', 1)[0]
    return METHOD_NAMES.get(source, source)


def method_status(status):
    return STATUS_NAMES.get(status, str(status or '未記錄'))


def evidence_methods(region):
    methods = region.get('methods') or ()
    if isinstance(methods, str):
        methods = (methods,)
    source = region.get('source')
    if region.get('transcription_of') or source == 'manual_transcription':
        source = '人工修正'
    return tuple(dict.fromkeys(method_name(name) for name in (source, *methods) if name)) or ('來源未分類',)


def requirement_results(requirement, regions):
    if requirement.get('deleted') or requirement.get('key') == 'observation.text':
        return
    label = requirement.get('label') or requirement.get('key') or '欄位'
    current_value = requirement.get('value')
    candidates = requirement.get('candidates') or ()
    if current_value and not any(item.get('value') == current_value for item in candidates):
        candidates = (*candidates, {'source': requirement.get('origin'), 'value': current_value,
                                    'unit': requirement.get('unit')})
    for candidate in candidates:
        value = candidate.get('value')
        if value is None or not str(value).strip():
            continue
        method = method_name(candidate.get('source') or requirement.get('origin'))
        ids = candidate.get('evidence_ids')
        if candidate.get('unlocated_output'):
            ids = ()
        elif ids is None:
            # Historical alternatives may describe a different value; only the
            # current candidate can rely on the requirement's shared links.
            ids = requirement.get('evidence_ids', ()) if value == current_value else ()
        ids = tuple(dict.fromkeys(eid for eid in ids if eid in regions))
        preferred = tuple(eid for eid in ids if method in evidence_methods(regions[eid]))
        ids = preferred + tuple(eid for eid in ids if eid not in preferred)
        page = regions[ids[0]]['page'] if ids else None
        unit = candidate.get('unit', requirement.get('unit') if value == current_value else '')
        result = ' '.join(str(part) for part in (value, unit) if part is not None and str(part))
        yield MethodResult(method, label, result, page, ids, 'candidate')


def run_results(run):
    page = run.get('page')
    status = method_status(run.get('status'))
    detail = run.get('error') or run.get('stop_reason')
    seconds = run.get('elapsed_seconds')
    timing = f'{seconds:.2f} 秒' if isinstance(seconds, (int, float)) else None
    ocr = run.get('ocr_engine')
    if ocr:
        if run.get('ocr_coverage_status') not in (None, 'complete'):
            status = method_status(run['ocr_coverage_status'])
        for engine in ocr.split(' + '):
            yield MethodResult(method_name(engine), '執行紀錄', '｜'.join(filter(None, (status, detail, timing))), page)
    else:
        requested_ocr = run.get('requested_ocr')
        name = run.get('type') or '頁面辨識'
        label = '執行紀錄'
        if requested_ocr:
            from .ocr_engines import BACKENDS
            name = BACKENDS.get(requested_ocr, requested_ocr)
        yield MethodResult(method_name(name), label,
                           '｜'.join(filter(None, (status, detail, timing))), page)
    model = run.get('model') or run.get('actual_model')
    if model or run.get('vlm_status') not in (None, 'disabled'):
        vlm_status = method_status(run.get('vlm_status') or run.get('status'))
        actual = run.get('actual_model')
        actual_detail = f'實際模型 {actual}' if actual and actual != model else ''
        yield MethodResult(method_name(f'VLM:{model}') if model else '視覺語言模型', '執行紀錄',
                           '｜'.join(filter(None, (vlm_status, actual_detail, run.get('vlm_error')))), page)
    methods = run.get('methods') or {}
    for key, name in ANALYSIS_METHODS.items():
        phase = methods.get(key) if isinstance(methods, dict) else None
        if phase and phase not in ('not_run', 'disabled'):
            yield MethodResult(name, '執行紀錄', method_status(phase), page)
    for candidate in run.get('rejected_candidates', ()):
        label = candidate.get('key') or '欄位'
        detail = '\n'.join(str(candidate[key]) for key in ('value', 'evidence', 'reason') if candidate.get(key))
        yield MethodResult('待判讀候選', f'未採用 · {label}', detail, page)


def result_sources(doc):
    if not doc:
        return {}
    return {
        **{r['id']: r for r in doc.get('layout_regions', ()) if box_valid(r.get('box'))},
        **{r['id']: r for r in doc.get('regions', ()) if r.get('kind') == 'evidence'},
    }


def method_results(doc):
    if not doc:
        return []
    regions = {region['id']: region for region in doc.get('regions', ()) if region.get('kind') == 'evidence'}
    rows = []
    for eid, region in regions.items():
        text = region.get('text') or '此框無文字'
        for method in evidence_methods(region):
            label = '人工轉寫' if method == '人工修正' else '來源框讀值'
            rows.append(MethodResult(method, label, text, region['page'], (eid,), 'evidence'))
    for region in doc.get('layout_regions', ()):
        if box_valid(region.get('box')) and region['id'] not in regions:
            layout_type = region['layout_type']
            label = '版面 · ' + LAYOUT_TYPES.get(layout_type, layout_type)
            rows.append(MethodResult(method_name(region['source']), label, region.get('text') or label,
                                     region['page'], (region['id'],), 'layout'))
    # A preserved candidate can be recorded by more than one extraction pass.
    candidates = (row for requirement in doc.get('requirements', ()) for row in requirement_results(requirement, regions))
    rows.extend(dict.fromkeys(candidates))
    for page in doc.get('pages', ()):
        number = page['number']
        if page.get('embedded_text'):
            rows.append(MethodResult('PDF 文字層', '原生頁面全文', page['embedded_text'], number))
        if page.get('ocr_text'):
            rows.append(MethodResult('頁面全文', '已取得文字', page['ocr_text'], number))
        state = page.get('recognition_status') or page.get('processing')
        if state and state != 'complete':
            rows.append(MethodResult('頁面辨識', '目前狀態', method_status(state), number))
    for run in doc.get('tool_runs', ()):
        rows.extend(run_results(run))
    return rows


def result_snapshot(doc, rows, regions, method, page):
    results = []
    for row in rows:
        sources = []
        for source_id in row.source_ids:
            region = regions[source_id]
            box = region.get('box')
            if box_valid(box):
                sources.append({'id': source_id, 'page': region['page'], 'bbox': list(box),
                                'source': region.get('source', ''), 'text': region.get('text', ''),
                                'kind': region.get('kind', 'layout'),
                                **({'layout_type': region['layout_type']} if region.get('layout_type') else {})})
        results.append({'method': row.method, 'label': row.label, 'text': row.text, 'page': row.page,
                        'kind': row.kind, 'sources': sources})
    return {'schema': 'drawiq-recognition-results/1', 'document_id': doc.get('id'),
            'document_name': doc.get('name'), 'document_version': doc.get('version'),
            'source_sha256': doc.get('source_sha256'),
            'coordinate_space': 'rendered_visible_page_top_left_normalized',
            'filter': {'method': method, 'page': page}, 'results': results}


def markdown_results(snapshot):
    def escape(value):
        return re.sub(r'([\\`*_{}\[\]<>#!|~])', r'\\\1', str(value).replace('&', '&amp;'))

    scope = snapshot['filter']
    page_scope = f'第 {scope["page"]} 頁' if scope['page'] is not None else '全部頁面'
    lines = ['# DrawIQ 辨識結果', '',
             f'文件 · {escape(snapshot["document_name"] or "未命名文件")}',
             f'方法 · {escape(scope["method"] or "全部方法")}',
             f'頁次 · {page_scope}', '',
             '來源框為原圖左上角起算的正規化座標。', '']
    method = None
    for row in snapshot['results']:
        if method != row['method']:
            method = row['method']
            lines.extend([f'## {escape(method)}', ''])
        location = f'（第 {row["page"]} 頁）' if row['page'] else ''
        lines.append(f'**{escape(row["label"])}**{location}')
        if row['text'] != row['label']:
            lines.extend(['', *[escape(line) + '  ' for line in row['text'].splitlines()]])
        if row['sources']:
            lines.append('')
        for source in row['sources']:
            coordinates = ', '.join(map(str, source['bbox']))
            lines.append(f'- 第 {source["page"]} 頁，來源框 `{coordinates}`')
        lines.append('')
    return '\n'.join(lines)


class MethodResultsPanel(QWidget):
    """Right-hand method browser. Selecting a linked result locates its source."""

    source_selected = pyqtSignal(str)
    source_filter_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.groups = {}
        self.regions = {}
        self.visible_rows = []
        self.source_items = {}
        self.rows_by_source = {}
        self.preview_snapshot = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel('各方法結果')
        title.setObjectName('reviewHeading')
        layout.addWidget(title)
        filters = QHBoxLayout()
        self.method_filter = QComboBox()
        self.method_filter.setAccessibleName('篩選辨識方法')
        self.method_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.method_filter.setMinimumContentsLength(10)
        self.method_filter.currentIndexChanged.connect(self.show_results)
        filters.addWidget(self.method_filter, 1)
        self.page_filter = QComboBox()
        self.page_filter.setAccessibleName('篩選結果頁次')
        self.page_filter.currentIndexChanged.connect(self.show_results)
        filters.addWidget(self.page_filter)
        layout.addLayout(filters)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        browse = QWidget()
        browse_layout = QVBoxLayout(browse)
        browse_layout.setContentsMargins(0, 4, 0, 0)
        self.tabs.addTab(browse, '來源')
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        browse_layout.addWidget(self.empty)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['結果', '頁'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAccessibleName('各方法的辨識結果')
        self.tree.currentItemChanged.connect(self.show_details)
        self.tree.itemActivated.connect(self.focus_current)
        browse_layout.addWidget(self.tree, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('完整辨識內容')
        self.details.setMaximumHeight(126)
        browse_layout.addWidget(self.details)
        self.sources = QComboBox()
        self.sources.setAccessibleName('切換原圖來源')
        self.sources.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sources.setMinimumContentsLength(13)
        self.sources.currentIndexChanged.connect(self.emit_source)
        browse_layout.addWidget(self.sources)
        self.markdown_view = QTextBrowser()
        self.markdown_view.setOpenLinks(False)
        self.tabs.addTab(self.markdown_view, '全文')
        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.tabs.addTab(self.json_view, 'JSON')
        self.tabs.currentChanged.connect(self.update_preview)
        self.export_button = QPushButton('匯出目前結果')
        self.export_button.clicked.connect(self.export_results)
        layout.addWidget(self.export_button)
        self.refresh(None)

    def refresh(self, doc):
        previous_method = self.method_filter.currentData() if doc else None
        same = bool(doc and self.doc and doc.get('id') == self.doc.get('id'))
        previous_page = self.page_filter.currentData() if same else None
        self.doc = doc
        self.regions = result_sources(doc)
        self.groups = {}
        self.rows_by_source = {}
        for row in method_results(doc):
            self.groups.setdefault(row.method, []).append(row)
            for source_id in row.source_ids:
                self.rows_by_source.setdefault(source_id, []).append(row)
        with QSignalBlocker(self.method_filter), QSignalBlocker(self.page_filter):
            self.method_filter.clear()
            self.method_filter.addItem('全部方法', None)
            for method in self.groups:
                self.method_filter.addItem(method, method)
            self.method_filter.setCurrentIndex(max(0, self.method_filter.findData(previous_method)))
            self.page_filter.clear()
            self.page_filter.addItem('全部頁面', None)
            for page in doc.get('pages', ()) if doc else ():
                number = page['number']
                self.page_filter.addItem(f'第 {number} 頁', number)
            self.page_filter.setCurrentIndex(max(0, self.page_filter.findData(previous_page)))
        self.show_results()

    def set_page(self, page):
        index = self.page_filter.findData(page)
        if index >= 0:
            self.page_filter.setCurrentIndex(index)

    def rows_on_page(self, rows, page):
        for row in rows:
            if page is None:
                yield row
            elif row.source_ids:
                source_ids = tuple(eid for eid in row.source_ids if self.regions[eid]['page'] == page)
                if source_ids:
                    yield replace(row, page=page, source_ids=source_ids)
            elif row.page == page:
                yield row

    def show_results(self, *_):
        previous = self.tree.currentItem()
        previous_row = previous.data(0, RESULT_ROLE) if previous else None
        expanded = {
            self.tree.topLevelItem(index).data(0, METHOD_ROLE)
            for index in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(index).isExpanded()
        }
        first_load = self.tree.topLevelItemCount() == 0
        selected_method = self.method_filter.currentData()
        selected_page = self.page_filter.currentData()
        self.visible_rows = []
        self.source_items = {}
        self.preview_snapshot = None
        with QSignalBlocker(self.tree):
            self.tree.clear()
            for method, rows in self.groups.items():
                if selected_method and method != selected_method:
                    continue
                visible = list(self.rows_on_page(rows, selected_page))
                if not visible:
                    continue
                self.visible_rows.extend(visible)
                group = QTreeWidgetItem([f'{method}（{len(visible)}）'])
                group.setData(0, METHOD_ROLE, method)
                group.setToolTip(0, method)
                self.tree.addTopLevelItem(group)
                for row in visible:
                    excerpt = ' '.join(row.text.split())[:220]
                    item = QTreeWidgetItem([f'{row.label} | {excerpt}', str(row.page or '')])
                    item.setData(0, RESULT_ROLE, row)
                    item.setToolTip(0, f'{row.label}\n{row.text[:1000]}')
                    group.addChild(item)
                    for source_id in row.source_ids:
                        self.source_items.setdefault(source_id, []).append(item)
                    if row == previous_row:
                        self.tree.setCurrentItem(item)
                group.setExpanded(bool(selected_method or method in expanded))
            if self.tree.topLevelItemCount() == 1 or (first_load and self.tree.topLevelItemCount()):
                self.tree.topLevelItem(0).setExpanded(True)
        self.empty.setText('沒有符合篩選的結果。' if self.groups else '匯入並辨識圖面後即可查看結果。')
        self.empty.setVisible(not self.visible_rows)
        self.tree.setVisible(bool(self.visible_rows))
        self.export_button.setEnabled(bool(self.visible_rows))
        self.show_details(self.tree.currentItem(), emit=False)
        self.update_preview()
        self.source_filter_changed.emit(set(self.source_items) if selected_method or selected_page else None)

    def select_source(self, evidence_id):
        """Show the direct result for a canvas source without requesting another focus."""
        region = self.regions.get(evidence_id)
        rows = self.rows_by_source.get(evidence_id)
        if region is None or not rows:
            return False

        def priority(row):
            return (row.kind not in ('evidence', 'layout'),
                    row.method != method_name(region.get('source')), len(row.source_ids))

        if evidence_id not in self.source_items:
            selected_method = self.method_filter.currentData()
            compatible = [row for row in rows if row.method == selected_method]
            best = min(compatible or rows, key=priority)
            with QSignalBlocker(self.method_filter), QSignalBlocker(self.page_filter):
                if selected_method and not compatible:
                    self.method_filter.setCurrentIndex(self.method_filter.findData(best.method))
                if self.page_filter.currentData() is not None:
                    self.set_page(region['page'])
            self.show_results()

        matching = self.source_items.get(evidence_id)
        if not matching:
            return False
        item = min(matching, key=lambda candidate: priority(candidate.data(0, RESULT_ROLE)))
        self.tabs.setCurrentIndex(0)
        with QSignalBlocker(self.tree):
            item.parent().setExpanded(True)
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self.show_details(item, emit=False, source_id=evidence_id)
        return True

    def show_details(self, item, *_previous, emit=True, source_id=None):
        row = item.data(0, RESULT_ROLE) if item else None
        self.details.setVisible(row is not None)
        with QSignalBlocker(self.sources):
            self.sources.clear()
            if row is not None:
                location = f'第 {row.page} 頁' if row.page else '無原圖定位'
                self.details.setPlainText(f'{row.method} · {location}\n{row.label}\n{row.text}')
                for eid in row.source_ids:
                    region = self.regions[eid]
                    text = region.get('text') or region.get('layout_type') or ''
                    excerpt = ' '.join(text.split())[:60]
                    self.sources.addItem(f'第 {region["page"]} 頁 · {excerpt}', eid)
                self.sources.setCurrentIndex(max(0, self.sources.findData(source_id)))
            else:
                self.details.clear()
            self.sources.setVisible(self.sources.count() > 1)
        if emit:
            self.emit_source()

    def focus_current(self, item, _column):
        if item == self.tree.currentItem():
            self.emit_source()

    def emit_source(self, *_):
        source_id = self.sources.currentData()
        if source_id:
            self.source_selected.emit(source_id)

    def snapshot(self):
        if self.preview_snapshot is None:
            self.preview_snapshot = result_snapshot(
                self.doc or {}, self.visible_rows, self.regions,
                self.method_filter.currentData(), self.page_filter.currentData(),
            )
        return self.preview_snapshot

    def update_preview(self, *_):
        view = self.tabs.currentWidget()
        if view is self.markdown_view:
            view.document().setMarkdown(markdown_results(self.snapshot()) if self.visible_rows else '')
        elif view is self.json_view:
            view.setPlainText(json.dumps(self.snapshot(), ensure_ascii=False, indent=2) if self.visible_rows else '')

    def export_results(self):
        if not self.visible_rows:
            return
        export_json = self.tabs.currentWidget() is self.json_view
        extension = '.json' if export_json else '.md'
        name = Path(self.doc.get('name') or 'drawiq').stem + '-辨識結果'
        filename, selected_format = QFileDialog.getSaveFileName(
            self, '匯出目前結果', name + extension, FORMATS,
            'JSON (*.json)' if export_json else 'Markdown (*.md)',
        )
        if not filename:
            return
        path = Path(filename)
        suffix = path.suffix.lower()
        is_json = suffix == '.json' if suffix in ('.md', '.json') else selected_format == 'JSON (*.json)'
        snapshot = self.snapshot()
        content = (json.dumps(snapshot, ensure_ascii=False, indent=2) if is_json else markdown_results(snapshot))
        encoded = (content.rstrip('\n') + '\n').encode('utf-8')
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            QMessageBox.warning(self, '無法匯出結果', output.errorString())
        elif output.write(encoded) != len(encoded):
            output.cancelWriting()
            QMessageBox.warning(self, '無法匯出結果', output.errorString())
        elif not output.commit():
            QMessageBox.warning(self, '無法匯出結果', output.errorString())
