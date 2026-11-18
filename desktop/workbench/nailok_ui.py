"""Single-screen quotation review with source-linked, editable drawing annotations."""
import json

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .business import COST_FIELDS, calculate_quote, default_case
from .canvas import DrawingCanvas
from .domain import box_valid, digest
from .nailok_flow import NailokService, alternatives, annotation_text
from .nailok_methods_ui import LAYOUT_TYPES, MethodResultsPanel


FIELD_COLORS = {
    'quotation.number': '#156f78',
    'quotation.project': '#156f78',
    'quotation.salesperson': '#156f78',
    'quotation.date': '#156f78',
    'quotation.valid_until': '#156f78',
    'customer.name': '#156f78',
    'contact.phone': '#156f78',
    'contact.fax': '#156f78',
    'product.specification': '#7645a6',
    'material': '#397749',
    'adhesive.product': '#a04e1f',
    'coating.color': '#a04e1f',
    'coating.method': '#a04e1f',
    'quality.requirements': '#265da7',
    'quotation.quantity': '#156f78',
    'quotation.price': '#156f78',
    'packaging.instructions': '#156f78',
    'quotation.notes': '#265da7',
}
LEGEND = (
    ('#156f78', '報價／聯絡'), ('#7645a6', '規格'), ('#397749', '材質'),
    ('#a04e1f', '塗膠'), ('#265da7', '品質／注意'), ('#64748b', '其他來源'),
)
LAYOUT_COLORS = {'圖形': '#416484', '圖表': '#416484', '表格': '#a2691b', '公式': '#7645a6'}
STATES = (('有值', 'present'), ('圖面未提供', 'missing'), ('不適用', 'not_applicable'))


def heading(title):
    label = QLabel(title)
    label.setObjectName('reviewHeading')
    return label


def reading_editor(height):
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    editor.setFixedHeight(height)
    return editor


class NailokFlowPage(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.service = NailokService(owner.store)
        self.doc = None
        self.rows = []
        self.regions = {}
        self.layout_regions = {}
        self.overlay_filter = None
        self.examples = []
        self.edits = {}
        self.crop_choices = {}
        self.capture_target = None
        self.unread_frame = None
        self.approval_info = {'state': 'pending'}
        self.field = None
        self.source_id = None
        self.rendered_page = None

        self.setObjectName('nailokReview')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self.documents = QComboBox()
        self.documents.setMinimumContentsLength(25)
        self.documents.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.documents.activated.connect(self.open_document)
        toolbar.addWidget(self.documents, 2)
        self.version = QLabel()
        self.version.setObjectName('muted')
        toolbar.addWidget(self.version)
        toolbar.addStretch(1)
        self.engine = QComboBox()
        engine_labels = {
            'rapid_auto': 'RapidOCR · 自動方向', 'rapid_v6': 'RapidOCR',
            'rapid_detail': 'RapidOCR · 分區精讀', 'indra_auto': 'Indra · 自動方向', 'indra': 'Indra',
            'mineru_flash': 'MinerU Flash · 本機',
            'mineru_basic': 'MinerU Basic · 本機',
            'mineru_advanced': 'MinerU Advanced · 本機',
            'compare_mineru': 'RapidOCR ＋ MinerU Basic',
        }
        for index in range(owner.ocr_backend.count()):
            key = owner.ocr_backend.itemData(index)
            self.engine.addItem(engine_labels.get(key, owner.ocr_backend.itemText(index)), key)
        self.engine.setCurrentIndex(owner.ocr_backend.currentIndex())
        owner.ocr_backend.currentIndexChanged.connect(self.engine.setCurrentIndex)
        self.engine.currentIndexChanged.connect(owner.ocr_backend.setCurrentIndex)
        toolbar.addWidget(self.engine)
        self.reread_scope = QComboBox()
        self.reread_scope.setAccessibleName('重新辨識頁數')
        self.reread_scope.addItem('全部頁', 'all')
        self.reread_scope.addItem('本頁', 'current')
        toolbar.addWidget(self.reread_scope)
        self.force_ocr = QCheckBox('強制 OCR')
        self.force_ocr.setToolTip('重新辨識時讀取圖像文字，包含已有 PDF 文字層的頁面')
        toolbar.addWidget(self.force_ocr)
        owner.button('重新辨識', self.reread, toolbar)
        owner.button('報價草稿', self.open_quote, toolbar)
        layout.addLayout(toolbar)

        self.columns = QSplitter(Qt.Orientation.Horizontal)
        self.columns.setChildrenCollapsible(False)
        self.columns.addWidget(self.build_fields())
        self.columns.addWidget(self.build_drawing())
        self.results = MethodResultsPanel()
        self.results.setMinimumWidth(265)
        self.results.source_selected.connect(self.focus_source_result)
        self.results.source_filter_changed.connect(self.filter_overlays)
        self.columns.addWidget(self.results)
        self.columns.setStretchFactor(1, 1)
        self.columns.setSizes([370, 690, 325])
        layout.addWidget(self.columns, 1)

    def build_fields(self):
        panel = QWidget()
        panel.setMinimumWidth(330)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(7, 4, 7, 5)
        title = QHBoxLayout()
        title.addWidget(heading('耐落報價欄位'))
        title.addStretch(1)
        self.approval_status = QLabel('待辨識')
        self.approval_status.setObjectName('muted')
        title.addWidget(self.approval_status)
        layout.addLayout(title)
        self.feature_table = QTableWidget(0, 3)
        self.feature_table.setHorizontalHeaderLabels(['欄位', '內容', '狀態'])
        self.feature_table.verticalHeader().hide()
        self.feature_table.verticalHeader().setDefaultSectionSize(25)
        self.feature_table.setAlternatingRowColors(True)
        self.feature_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.feature_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.feature_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.feature_table.setWordWrap(False)
        header = self.feature_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.feature_table.setColumnWidth(0, 110)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.feature_table.currentCellChanged.connect(self.choose_field)
        layout.addWidget(self.feature_table, 1)

        self.field_title = QLabel('選擇欄位')
        self.field_title.setWordWrap(True)
        self.field_title.setObjectName('selectedField')
        layout.addWidget(self.field_title)
        self.candidates = QComboBox()
        self.candidates.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.candidates.activated.connect(self.choose_candidate)
        layout.addWidget(self.candidates)
        self.value = QPlainTextEdit()
        self.value.setFixedHeight(52)
        self.value.setPlaceholderText('核對原圖後可修改內容')
        self.value.textChanged.connect(self.edit_field)
        layout.addWidget(self.value)
        row = QHBoxLayout()
        self.unit = QLineEdit()
        self.unit.setPlaceholderText('單位')
        self.unit.textChanged.connect(self.edit_field)
        self.unit.setMaximumWidth(82)
        row.addWidget(self.unit)
        self.state = QComboBox()
        for label, state in STATES:
            self.state.addItem(label, state)
        self.state.currentIndexChanged.connect(self.edit_field)
        row.addWidget(self.state, 1)
        layout.addLayout(row)
        self.field_help = QLabel()
        self.field_help.setWordWrap(True)
        self.field_help.setObjectName('muted')
        layout.addWidget(self.field_help)
        footer = QHBoxLayout()
        self.save = self.owner.button('核准報價標籤', self.approve_label, footer)
        self.save.setObjectName('primary')
        self.save.setToolTip('核對目前標籤後一次核准（Ctrl+Enter）')
        self.reset = self.owner.button('還原輸入', self.reset_edits, footer)
        self.reset.setToolTip('恢復上次保存或帶入的欄位內容')
        layout.addLayout(footer)
        self.update_actions()
        return panel

    def build_drawing(self):
        panel = QWidget()
        panel.setMinimumWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 4, 5, 5)
        title = QHBoxLayout()
        title.addWidget(heading('工程圖'))
        title.addStretch(1)
        self.overlays = QCheckBox('彩色標註')
        self.overlays.setChecked(True)
        self.overlays.toggled.connect(lambda: self.owner.guard(lambda: self.render_page(keep_view=True)))
        title.addWidget(self.overlays)
        layout.addLayout(title)
        row = QHBoxLayout()
        self.page_select = QComboBox()
        self.page_select.currentIndexChanged.connect(lambda: self.owner.guard(self.render_page))
        row.addWidget(self.page_select, 1)
        self.owner.button('滿版', lambda: self.canvas.fit(), row)
        self.owner.button('-', lambda: self.canvas.scale(1/1.25, 1/1.25), row)
        self.owner.button('+', lambda: self.canvas.scale(1.25, 1.25), row)
        self.owner.button('旋轉', lambda: self.canvas.rotate_page(), row)
        layout.addLayout(row)
        self.canvas = DrawingCanvas()
        self.canvas.region_clicked.connect(self.canvas_annotation_selected)
        self.canvas.region_created.connect(lambda box: self.owner.guard(lambda: self.recognize_box(box)))
        layout.addWidget(self.canvas, 1)

        legend_text = '　'.join(f'<span style="color:{color}">■ {label}</span>' for color, label in LEGEND)
        legend = QLabel(legend_text + '　虛線：版面定位')
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.source_text = reading_editor(53)
        self.source_text.setPlaceholderText('點選圖面標註或右側結果，可查看來源')
        layout.addWidget(self.source_text)
        row = QHBoxLayout()
        self.edit_box = self.owner.button('修正標註', self.edit_annotation, row)
        self.use_box = self.owner.button('帶入目前欄位', self.use_annotation, row)
        self.new_box = self.owner.button('框選並辨識', self.start_annotation, row)
        self.new_box.setObjectName('primary')
        self.new_box.setCheckable(True)
        self.canvas.mode_changed.connect(self.sync_draw_button)
        layout.addLayout(row)
        self.update_source_controls()
        return panel

    def open_document(self, index):
        doc_id = self.documents.itemData(index)
        if self.doc and doc_id == self.doc['id']:
            return
        with QSignalBlocker(self.documents):
            self.documents.setCurrentIndex(self.documents.findData(self.doc['id']) if self.doc else -1)
        def load():
            self.owner.require_saved_form()
            self.owner.load_documents(doc_id)
        self.owner.guard(load)

    def reread(self):
        page = self.page_select.currentData() if self.reread_scope.currentData() == 'current' else None
        return self.owner.quick_analyze(current_page=page, force_ocr=self.force_ocr.isChecked())

    def refresh(self, doc):
        same = bool(self.doc and self.doc['id'] == doc['id'])
        if same and self.is_dirty() and self.doc['version'] != doc['version']:
            self.owner.status.setText('圖面版本已更新。請先還原未儲存的修改，再重新核對。')
            return
        if not same:
            self.edits.clear()
            self.crop_choices.clear()
            self.unread_frame = None
            self.source_id = None
            self.field = None
            self.rendered_page = None
            self.overlay_filter = None
            self.canvas.set_mode('select')
        self.doc = doc
        self.regions = {region['id']: region for region in doc['regions']}
        self.layout_regions = {region['id']: region for region in doc.get('layout_regions', ())
                               if region['id'] not in self.regions and box_valid(region['box'])}
        if not self.source_geometry(self.source_id):
            self.source_id = None
        self.rows = self.service.prefill(doc)
        self.approval_info = self.service.label_approval(doc)
        self.examples = self.service.examples(doc)
        self.version.setText(f'v{doc["version"]} · {len(doc["pages"])} 頁')
        current_page = self.page_select.currentData() if same else None
        with QSignalBlocker(self.page_select):
            self.page_select.clear()
            for page in doc['pages']:
                number = page['number']
                self.page_select.addItem(f'第 {number} 頁', number)
            self.page_select.setCurrentIndex(max(0, self.page_select.findData(current_page)))
        with QSignalBlocker(self.documents):
            self.documents.clear()
            for index in range(self.owner.documents.count()):
                item = self.owner.documents.item(index)
                self.documents.addItem(item.text().split('\n', 1)[0], item.data(Qt.ItemDataRole.UserRole))
            self.documents.setCurrentIndex(self.documents.findData(doc['id']))
        self.populate_fields()
        self.results.refresh(doc)
        self.render_page(keep_view=same)
        if self.source_geometry(self.source_id):
            self.select_annotation(self.source_id)
        else:
            source_ids = self.displayed(self.active_row())['evidence_ids']
            if source_ids:
                self.locate_source(source_ids[0])
            else:
                self.update_source_controls()

    def populate_fields(self):
        self.field = self.field if any(row['key'] == self.field for row in self.rows) else self.rows[0]['key']
        with QSignalBlocker(self.feature_table):
            self.feature_table.setRowCount(len(self.rows))
            for index, row in enumerate(self.rows):
                key = row['key']
                label = QTableWidgetItem(row['label'])
                label.setForeground(QColor(FIELD_COLORS[key]))
                label.setToolTip(row['label'])
                self.feature_table.setItem(index, 0, label)
                self.feature_table.setItem(index, 1, QTableWidgetItem())
                self.feature_table.setItem(index, 2, QTableWidgetItem())
                self.update_row(index)
                if key == self.field:
                    self.feature_table.setCurrentCell(index, 1)
        self.load_editor()

    def displayed(self, row):
        if row['key'] in self.edits:
            return self.edits[row['key']]
        return dict(key=row['key'], value=row['value'], unit=row['unit'],
                    candidate_id=row['candidate_id'], evidence_ids=row['evidence_ids'],
                    state=row['state'])

    def update_row(self, index):
        row = self.rows[index]
        draft = self.displayed(row)
        text = draft['value'].strip().replace('\n', ' · ')
        if draft['state'] != 'present':
            text = next(label for label, state in STATES if state == draft['state'])
        elif text and draft['unit']:
            text += ' ' + draft['unit']
        cell = self.feature_table.item(index, 1)
        placeholder = '待補資料' if row['key'] in self.edits else row['display_value']
        cell.setText(text or placeholder or '待補資料')
        cell.setForeground(QColor('#1b3343' if text else '#64748b'))
        if row['key'] in self.edits:
            status = '需核對' if draft.get('conflict') else '已修改'
        elif row['confirmed']:
            status = '已核對'
        elif draft['state'] != 'present' or not text:
            status = '待補'
        else:
            status = '需核對' if row['issues'] else '已帶入'
        self.feature_table.item(index, 2).setText(status)
        cell.setToolTip(f'{cell.text()}\n{status}')

    def active_row(self):
        return self.rows[self.feature_table.currentRow()] if self.field and self.feature_table.currentRow() >= 0 else None

    def choose_field(self, row, _column, previous, _previous_column):
        if row < 0 or row == previous or not self.rows:
            return
        self.canvas.set_mode('select')
        self.field = self.rows[row]['key']
        self.load_editor()
        sources = self.displayed(self.rows[row])['evidence_ids']
        if sources:
            self.locate_source(sources[0])
        else:
            self.source_id = None
            self.update_source_controls()
        self.render_page(keep_view=True)

    def load_editor(self):
        row = self.active_row()
        if not row:
            return
        draft = self.displayed(row)
        self.field_title.setText(row['label'])
        with QSignalBlocker(self.candidates):
            self.candidates.clear()
            self.candidates.addItem('其他辨識結果…', None)
            crop_options = self.crop_choices.get(row['key'], [])
            all_options = [*crop_options, *alternatives(self.doc, row['key'], self.examples)]
            seen = set()
            for choice in all_options:
                identity = (choice['value'], choice['unit'], tuple(choice['evidence_ids']))
                if identity in seen:
                    continue
                seen.add(identity)
                text = ' '.join(part for part in (choice['value'].replace('\n', ' '), choice['unit']) if part)
                pages = sorted({self.regions[eid]['page'] for eid in choice['evidence_ids'] if eid in self.regions})
                location = '、'.join(f'P{page}' for page in pages)
                method = choice.get('engine') or location
                self.candidates.addItem(f'{text[:65]}  ·  {method}', choice)
            self.candidates.setVisible(self.candidates.count() > 1)
        self.set_editor(draft)
        self.field_help.setText('；'.join(row['issues']) or row['status'])
        self.update_actions()

    def set_editor(self, draft):
        with QSignalBlocker(self.value), QSignalBlocker(self.unit), QSignalBlocker(self.state):
            self.value.setPlainText(draft['value'])
            self.unit.setText(draft['unit'])
            self.state.setCurrentIndex(self.state.findData(draft['state']))

    def set_draft(self, draft):
        self.edits[self.field] = draft
        index = self.feature_table.currentRow()
        with QSignalBlocker(self.feature_table):
            self.update_row(index)
        self.update_actions()

    def edit_field(self):
        row = self.active_row()
        if row:
            existing = self.displayed(row)
            value = self.value.toPlainText()
            draft = dict(existing, value=value, unit=self.unit.text(), state=self.state.currentData())
            if value != existing['value']:
                draft.pop('conflict', None)
                if value.strip() and self.unread_frame == (self.field, self.source_id):
                    draft.update(candidate_id=None, evidence_ids=[self.source_id])
            self.set_draft(draft)

    def choose_candidate(self, index):
        choice = self.candidates.itemData(index)
        if not choice:
            return
        draft = dict(key=self.field, value=choice['value'], unit=choice['unit'], state='present',
                     candidate_id=choice['candidate_id'], evidence_ids=choice['evidence_ids'])
        self.set_editor(draft)
        self.set_draft(draft)
        self.field_help.setText('；'.join(choice.get('issues', ())) or '已帶入選取的辨識結果')
        if choice['evidence_ids']:
            self.locate_source(choice['evidence_ids'][0])
        self.render_page(keep_view=True)

    def update_actions(self):
        state = self.approval_info['state']
        approved = state == 'approved' and not self.is_dirty()
        ready = any(self.displayed(row)['value'].strip() for row in self.rows)
        self.save.setEnabled(bool(self.doc and (approved or ready)))
        self.save.setText('查看／輸出標籤圖片' if approved else '核准報價標籤')
        self.approval_status.setText(
            '已核准' if approved else '修改待核准' if self.is_dirty() or state == 'stale'
            else '待核准' if self.doc else '待辨識'
        )
        self.reset.setEnabled(self.is_dirty())

    def is_dirty(self):
        return bool(self.edits)

    def reset_edits(self):
        self.edits.clear()
        self.owner.doc = self.owner.store.get(self.doc['id'])
        self.owner.refresh()

    def require_document(self):
        if not self.doc:
            raise ValueError('請先匯入或載入圖面')
        return self.doc

    def require_current_document(self):
        doc = self.require_document()
        if self.owner.store.get(doc['id'])['version'] != doc['version']:
            raise ValueError('圖面已更新，請先重新載入或還原再操作')
        return doc

    def require_saved_advanced_form(self):
        if self.owner.business_page.is_dirty() or (
            self.owner.loaded_form is not None and self.owner.form_values() != self.owner.loaded_form
        ):
            raise ValueError('請先處理進階工具中未儲存的修改')

    def approve_label(self):
        from .nailok_label_ui import LabelImageDialog

        doc = self.require_current_document()
        self.require_saved_advanced_form()
        needs_editor = self.approval_info['state'] != 'approved' or self.is_dirty()
        while True:
            if needs_editor and not self.finish_label(doc):
                return
            output = LabelImageDialog(self, self.doc, self.approval_info)
            output.exec()
            if not output.edit_requested:
                return
            doc = self.require_current_document()
            needs_editor = True

    def finish_label(self, doc):
        from .nailok_label_ui import FinalLabelDialog

        drafts = []
        for row in self.rows:
            draft = dict(self.displayed(row), label=row['label'])
            if draft['state'] != 'present':
                draft['reason'] = '人工對照原圖：' + next(label for label, state in STATES if state == draft['state'])
                draft['candidate_id'] = None
                draft['evidence_ids'] = []
            drafts.append(draft)
        editor = FinalLabelDialog(self, doc, drafts, self.approval_info.get('final_fields', ()))
        if not editor.exec() or editor.updated_doc is None:
            return False
        self.edits.clear()
        self.apply_update(editor.updated_doc)
        remaining = len(self.approval_info['final_unresolved_fields'])
        self.owner.status.setText(f'報價標籤已核准，{remaining} 欄保留待補。' if remaining else '報價標籤已核准。')
        return True

    def render_page(self, *, keep_view=False):
        if not self.doc or self.page_select.currentData() is None:
            return
        number = self.page_select.currentData()
        page = next(page for page in self.doc['pages'] if page['number'] == number)
        colors = {}
        for row in self.rows:
            for eid in self.displayed(row)['evidence_ids']:
                colors.setdefault(eid, FIELD_COLORS[row['key']])
        active = self.active_row()
        active_ids = self.displayed(active)['evidence_ids'] if active else []
        if active:
            colors.update(dict.fromkeys(active_ids, FIELD_COLORS[active['key']]))
        regions = []
        if self.overlays.isChecked():
            def visible(region):
                return region['page'] == number and (
                    self.overlay_filter is None or region['id'] in self.overlay_filter or region['id'] == self.source_id
                )
            regions.extend(dict(region, overlay_color=colors.get(region['id'], LEGEND[-1][0]),
                                text=annotation_text(self.doc, region))
                           for region in self.regions.values() if visible(region))
            for region in self.layout_regions.values():
                if visible(region):
                    name, color = self.layout_style(region)
                    regions.append(dict(region, kind='layout', layout_label=name, overlay_color=color))
        self.canvas.selected_region = self.source_id
        self.canvas.show_page(page, regions, highlight_ids=active_ids, show_all=True,
                              keep_view=keep_view and number == self.rendered_page)
        self.rendered_page = number
        source = self.source_geometry(self.source_id)
        if source and source['page'] != number:
            self.source_id = None
            self.update_source_controls()

    def filter_overlays(self, ids):
        selected = set(ids) if ids is not None else None
        if selected == self.overlay_filter:
            return
        self.overlay_filter = selected
        if selected is not None and self.source_id and self.source_id not in selected:
            self.select_annotation(None)
        if self.rendered_page is not None:
            self.render_page(keep_view=True)

    def source_geometry(self, eid):
        return self.regions.get(eid) or self.layout_regions.get(eid)

    def layout_style(self, region):
        kind = region['layout_type']
        name = LAYOUT_TYPES.get(kind, kind)
        return f'版面 · {name}', LAYOUT_COLORS.get(name, LEGEND[-1][0])

    def select_annotation(self, eid):
        self.source_id = eid if self.source_geometry(eid) else None
        self.canvas.select_region(self.source_id)
        self.update_source_controls()

    def canvas_annotation_selected(self, eid):
        self.results.select_source(eid)
        self.select_annotation(eid)

    def focus_source_result(self, eid):
        self.locate_source(eid, zoom=True)

    def locate_source(self, eid, *, zoom=False):
        region = self.source_geometry(eid)
        if not region:
            return
        index = self.page_select.findData(region['page'])
        if index < 0:
            return
        self.select_annotation(eid)
        if index != self.page_select.currentIndex():
            self.page_select.setCurrentIndex(index)
        elif self.overlays.isChecked() and eid not in self.canvas.region_items:
            self.render_page(keep_view=True)
        if zoom:
            self.canvas.focus_box(region['box'])
            return
        item = self.canvas.region_items.get(eid)
        if item:
            self.canvas.ensureVisible(item)

    def update_source_controls(self):
        region = self.regions.get(self.source_id)
        is_evidence = bool(region and region.get('kind') == 'evidence')
        self.edit_box.setEnabled(is_evidence)
        self.use_box.setEnabled(is_evidence and bool(self.field))
        if region:
            text = annotation_text(self.doc, region)
            original = '\n原始讀值：' + region['text'] if text != region['text'] else ''
            self.source_text.setPlainText(f'P{region["page"]} · {text or "框選範圍尚無讀值"}{original}')
        elif self.source_id in self.layout_regions:
            region = self.layout_regions[self.source_id]
            label, _ = self.layout_style(region)
            self.source_text.setPlainText(f'P{region["page"]} · {label} · {region["source"]}\n版面定位')
        else:
            self.source_text.clear()

    def use_annotation(self):
        from .nailok_crop import draft_from_regions
        doc = self.require_document()
        current = self.active_row()
        region = self.regions.get(self.source_id)
        if not current or not region or region.get('kind') != 'evidence':
            raise ValueError('請先選擇欄位與圖面文字標註')
        draft = draft_from_regions(doc, current['key'], [region['id']])
        if not draft:
            raise ValueError('此框沒有可帶入目前欄位的文字。可修正標註或重新框選。')
        self.set_editor(draft)
        self.set_draft(draft)
        self.field_help.setText(f'已帶入第 {region["page"]} 頁標註，可直接修正欄位內容')
        self.render_page(keep_view=True)

    def edit_annotation(self):
        from .nailok_crop import draft_from_regions
        doc = self.require_current_document()
        region = self.regions.get(self.source_id)
        if not region:
            raise ValueError('請先點選圖面標註')
        old_text = annotation_text(doc, region)
        value, accepted = QInputDialog.getMultiLineText(self, '修正圖面標註', '核對原圖後輸入此框文字', old_text)
        if not accepted or value.strip() == old_text:
            return
        if not value.strip():
            raise ValueError('請輸入修正文字')
        active = self.active_row()
        prior = self.displayed(active) if active else None
        prior_reading = draft_from_regions(doc, self.field, [region['id']]) if active else None
        updated = self.service.correct_annotation(doc['id'], doc['version'], region['id'], value)
        only_source = prior and prior['evidence_ids'] == [region['id']]
        linked = only_source and (self.field not in self.edits or (prior_reading and prior['value'] == prior_reading['value']))
        if linked or self.unread_frame == (self.field, region['id']):
            new_draft = draft_from_regions(updated, self.field, [region['id']])
            if new_draft:
                self.edits[self.field] = new_draft
        self.apply_update(updated)
        self.owner.status.setText('已保存標註修正與原始讀值。請核對目前標籤。')

    def sync_draw_button(self, mode):
        self.new_box.setChecked(mode == 'draw')

    def start_annotation(self):
        self.require_current_document()
        row = self.active_row()
        if not row:
            raise ValueError('請先選擇要辨識的報價欄位')
        if self.canvas.mode == 'draw':
            self.canvas.set_mode('select')
            return
        self.overlays.setChecked(True)
        self.canvas.set_mode('draw')
        self.owner.status.setText(f'請在原圖拖曳框選「{row["label"]}」。Esc 取消。')

    def recognize_box(self, box):
        from .nailok_crop import capture_field
        if self.owner.worker:
            raise ValueError('已有辨識工作執行中')
        self.canvas.set_mode('select')
        doc = self.require_current_document()
        active = self.active_row()
        if not active:
            raise ValueError('請先選擇要填入的欄位')
        key = active['key']
        page = self.page_select.currentData()
        rotation = self.canvas.rotation
        self.capture_target = (doc['id'], key)
        self.unread_frame = None

        def work(progress, cancelled):
            return capture_field(self.owner.store, doc, page, box, key, rotation=rotation,
                                 progress=progress, cancelled=cancelled)

        self.owner.start_job(work, on_result=self.crop_finished, allow_label_edits=True)

    def crop_finished(self, result):
        target = self.capture_target
        self.capture_target = None
        if not target or not self.doc or target[0] != self.doc['id']:
            return
        if result.get('cancelled'):
            self.owner.status.setText('已取消框選辨識。')
            return
        key = target[1]
        draft = result['draft']
        self.crop_choices[key] = result['alternatives']
        if draft:
            self.edits[key] = dict(draft, conflict=result['conflict'])
        elif result.get('frame_id'):
            self.unread_frame = (key, result['frame_id'])
        self.apply_update(result['document'])
        if draft:
            self.locate_source(draft['evidence_ids'][0])
            hint = '辨識結果有差異。已帶入一筆，可選其他結果或直接修正。' if result['conflict'] else '已帶入框選結果，可直接修正或核准。'
        else:
            if self.unread_frame:
                self.locate_source(self.unread_frame[1])
            hint = '此框未取得可用讀值。可直接輸入欄位內容或重新框選。'
        self.field_help.setText(hint)
        self.owner.status.setText(hint)

    def apply_update(self, doc):
        self.doc = doc
        self.owner.doc = doc
        self.owner.refresh()

    def open_quote(self):
        if self.is_dirty():
            raise ValueError('請先核准報價標籤或還原修改')
        QuoteDraftDialog(self, self.require_current_document()).exec()


class QuoteDraftDialog(QDialog):
    """Optional cost entry scoped to the drawing version shown when opened."""
    def __init__(self, review, doc):
        super().__init__(review)
        self.review = review
        self.doc = doc
        self.setWindowTitle('內部報價草稿')
        self.resize(530, 520)
        layout = QVBoxLayout(self)
        report = review.service.report(doc)
        status = QLabel(f'已確認 {len(report["confirmed_features"])} 欄')
        status.setWordWrap(True)
        if report['missing_required']:
            status.setText(status.text() + ' · 待補 ' + '、'.join(report['missing_required']))
        layout.addWidget(status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form = QWidget()
        fields = QFormLayout(form)
        self.qty = QLineEdit()
        self.unit = QLineEdit('PCS')
        self.currency = QComboBox()
        self.currency.addItems(['請選幣別', 'TWD', 'USD', 'EUR', 'CNY', 'JPY'])
        self.costs = {key: QLineEdit() for key in COST_FIELDS}
        self.setup = QLineEdit()
        self.margin = QLineEdit()
        for label, widget in [('本次報價數量', self.qty), ('單位', self.unit), ('幣別', self.currency)]:
            fields.addRow(label, widget)
        for key, widget in self.costs.items():
            widget.setPlaceholderText('每單位成本，無費用請填 0')
            fields.addRow(COST_FIELDS[key] + '成本', widget)
        fields.addRow('一次性設定費', self.setup)
        fields.addRow('目標毛利率 %', self.margin)
        scroll.setWidget(form)
        layout.addWidget(scroll, 1)
        self.result = QLabel('可直接匯出規格草稿。填齊成本後才會試算金額。')
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        row = QHBoxLayout()
        review.owner.button('試算', self.preview, row)
        review.owner.button('匯出草稿', self.export, row).setObjectName('primary')
        self.open_output = review.owner.button('開啟資料夾', review.owner.open_exports, row)
        self.open_output.setEnabled(False)
        review.owner.button('關閉', self.reject, row)
        layout.addLayout(row)

    def current_doc(self):
        current = self.review.owner.store.get(self.doc['id'])
        if current['version'] != self.doc['version']:
            raise ValueError('圖面或核對內容已更新。請重新開啟草稿並核對成本。')
        return current

    def quote_case(self):
        case = default_case(self.doc)
        case['rfq'].update(quantity=self.qty.text().strip(), unit=self.unit.text().strip())
        case['quote'].update(
            currency=self.currency.currentText() if self.currency.currentIndex() > 0 else '',
            unit_costs={key: entry.text().strip() for key, entry in self.costs.items()},
            setup_cost=self.setup.text().strip(), margin_percent=self.margin.text().strip(),
        )
        return case

    def preview(self):
        self.current_doc()
        result = calculate_quote(self.quote_case())
        self.result.setText(f'未稅試算 {result["currency"]} {result["total_quote"]} · 每 {result["unit"]} {result["unit_quote"]}')
        return result

    def export(self):
        current = self.current_doc()
        report = self.review.service.report(current)
        case = self.quote_case()
        report['cost_inputs'] = case['quote']
        report['quote_quantity'] = case['rfq']
        try:
            report['cost_estimate'] = calculate_quote(case)
        except ValueError as exc:
            report['cost_estimate'] = None
            report['cost_missing_reason'] = str(exc)
        folder = self.review.owner.store.root / 'exports'
        folder.mkdir(exist_ok=True)
        path = folder / f'nailok_{current["id"]}_v{current["version"]}_{digest(report)[:10]}.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        self.open_output.setEnabled(True)
        self.result.setText('已匯出 ' + str(path))
        self.review.owner.status.setText('已匯出內部報價草稿：' + str(path))
        return path
