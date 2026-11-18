"""Edit, approve and export the final quotation-label image."""
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from .domain import digest
from .nailok_label_image import render_label, write_label_png
from .quotation import MULTILINE


UNIT_FIELDS = {'quotation.quantity', 'quotation.price'}


def action_button(row, text, action, *, primary=False):
    button = QPushButton(text)
    if primary:
        button.setObjectName('primary')
    button.clicked.connect(action)
    row.addWidget(button)
    return button


def preview_panel():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setMinimumWidth(380)
    label = QLabel()
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    label.setAccessibleName('報價標籤圖片預覽')
    scroll.setWidget(label)
    return scroll, label


def show_preview(label, image):
    preview = QPixmap.fromImage(image).scaledToWidth(360, Qt.TransformationMode.SmoothTransformation)
    label.setMinimumHeight(preview.height())
    label.setPixmap(preview)


class FinalLabelDialog(QDialog):
    def __init__(self, review, doc, drafts, previous_fields=()):
        super().__init__(review)
        self.review = review
        self.doc = doc
        self.drafts = drafts
        self.updated_doc = None
        self.inputs = {}
        self.setWindowTitle('確認最終報價標籤')
        self.resize(1020, 810)

        layout = QVBoxLayout(self)
        intro = QLabel('請核對最終內容，可補填價格、聯絡資料或其他欄位。未填的欄位在圖片中留空。')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(470)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        split.addWidget(scroll)
        preview_scroll, self.preview = preview_panel()
        split.addWidget(preview_scroll)
        split.setSizes([580, 410])

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(140)
        self.timer.timeout.connect(self.update_preview)

        previous = {field['key']: field for field in previous_fields
                    if field.get('provenance') == 'operator_input'}
        first_blank = None
        for draft in drafts:
            key, label = draft['key'], draft['label']
            seed = previous.get(key, draft)
            value = seed['value'] if seed.get('state', 'present') == 'present' else ''
            unit = seed['unit']
            if key in MULTILINE:
                entry = QPlainTextEdit()
                entry.setFixedHeight(58)
                entry.setPlainText(value)
            else:
                entry = QLineEdit(value)
            entry.setPlaceholderText('待補')
            entry.setAccessibleName(label)
            entry.textChanged.connect(lambda *_: self.timer.start())
            if not value and first_blank is None:
                first_blank = entry

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(entry, 1)
            unit_entry = None
            if key in UNIT_FIELDS or unit:
                unit_entry = QLineEdit(unit)
                unit_entry.setMaximumWidth(112)
                unit_entry.setPlaceholderText('TWD/M' if key == 'quotation.price' else '單位')
                unit_entry.setAccessibleName(label + '單位')
                unit_entry.textChanged.connect(lambda *_: self.timer.start())
                row_layout.addWidget(unit_entry)
            self.inputs[key] = (entry, unit_entry)
            form.addRow(label, row)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        action_button(buttons, '回到圖面', self.reject)
        self.finish_button = action_button(buttons, '核准並輸出圖片', self.finish, primary=True)
        layout.addLayout(buttons)
        self.update_preview()
        if first_blank is not None:
            first_blank.setFocus()

    def field_values(self):
        fields = []
        for draft in self.drafts:
            entry, unit = self.inputs[draft['key']]
            value = entry.toPlainText() if isinstance(entry, QPlainTextEdit) else entry.text()
            fields.append({
                'key': draft['key'], 'label': draft['label'], 'value': value.strip(),
                'unit': unit.text().strip() if unit is not None else '',
            })
        return fields

    def update_preview(self):
        fields = self.field_values()
        try:
            show_preview(self.preview, render_label(fields))
        except (ValueError, RuntimeError) as error:
            self.preview.setText('請先修正欄位內容')
            self.status.setText(str(error))
            self.finish_button.setEnabled(False)
            return
        remaining = sum(not field['value'] for field in fields)
        self.status.setText(f'{remaining} 欄留空' if remaining else '18 欄已填寫')
        self.finish_button.setEnabled(any(field['value'] for field in fields))

    def finish(self):
        fields = self.field_values()
        try:
            render_label(fields)
            self.updated_doc = self.review.service.approve_label(
                self.doc['id'], self.doc['version'], self.drafts, human_checked=True,
                final_inputs=fields,
            )
        except (ValueError, RuntimeError, OSError) as error:
            self.status.setText(str(error))
            return
        self.accept()


class LabelImageDialog(QDialog):
    def __init__(self, review, doc, approved):
        super().__init__(review)
        self.setWindowTitle('報價標籤圖片')
        self.resize(570, 810)
        self.image = render_label(approved['final_fields'])
        self.edit_requested = False
        self.saved_path = None
        folder = review.owner.store.root / 'exports'
        identity = digest(doc['id'])[:12]
        self.default_path = folder / f'報價標籤_{identity}_v{approved["approved_version"]}.png'

        layout = QVBoxLayout(self)
        scroll, preview = preview_panel()
        show_preview(preview, self.image)
        layout.addWidget(scroll, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        action_button(buttons, '修改最終資料', self.edit_again)
        buttons.addStretch(1)
        action_button(buttons, '另存 PNG', self.save_as)
        action_button(buttons, '複製圖片', self.copy_image, primary=True)
        action_button(buttons, '關閉', self.accept)
        layout.addLayout(buttons)
        self.save_to(self.default_path)

    def save_to(self, path):
        try:
            if path == self.default_path:
                path.parent.mkdir(exist_ok=True)
            self.saved_path = write_label_png(self.image, path)
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(f'標籤已核准，圖片未能儲存。{error}')
            return
        self.status.setText(f'已儲存 PNG　{self.saved_path}')

    def save_as(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, '另存報價標籤', str(self.saved_path or self.default_path), 'PNG 圖片 (*.png)',
        )
        if filename:
            path = Path(filename)
            if path.suffix.lower() != '.png':
                path = path.with_name(path.name + '.png')
            self.save_to(path)

    def copy_image(self):
        QApplication.clipboard().setImage(self.image)
        path = f'　{self.saved_path}' if self.saved_path else ''
        self.status.setText('已複製圖片，可貼入報價單。' + path)

    def edit_again(self):
        self.edit_requested = True
        self.accept()
