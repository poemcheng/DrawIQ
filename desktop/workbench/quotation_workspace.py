"""Quotation overview and source selection in the shared review workspace."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                            QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from .domain import STATE_LABELS
from .quotation_layout import quotation_rows


class QuotationWorkspaceMixin:
    def build_quotation_overview(self, layout):
        box = QWidget(); inner = QVBoxLayout(box); inner.setContentsMargins(0, 0, 0, 0)
        heading = QLabel('耐落客製標籤｜先帶入各欄最適合的候選，再對照原圖確認；缺值會明確標示待補填')
        heading.setWordWrap(True); inner.addWidget(heading)
        self.quotation_table = QTableWidget(0, 3)
        self.quotation_table.setHorizontalHeaderLabels(['報價欄位', '內容／單位', '覆核'])
        self.quotation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.quotation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.quotation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.quotation_table.verticalHeader().hide()
        self.quotation_table.verticalHeader().setDefaultSectionSize(22)
        self.quotation_table.setAlternatingRowColors(True)
        self.quotation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.quotation_table.setColumnWidth(0, 105); self.quotation_table.setColumnWidth(2, 86)
        self.quotation_table.setMinimumHeight(70)
        self.quotation_table.itemSelectionChanged.connect(self.quotation_selected)
        self.quotation_table.cellClicked.connect(lambda *_: self.quotation_selected())
        self.quotation_table.cellDoubleClicked.connect(lambda row, column:
            self.review_action(lambda: self.navigate_quotation_source(row)))
        inner.addWidget(self.quotation_table)
        self.quotation_target = QLabel(); self.quotation_target.setWordWrap(True); inner.addWidget(self.quotation_target)
        row = QHBoxLayout()
        self.quote_link_btn = self.review_button('帶入左側選項', self.link_quotation_source, row)
        self.quote_add_btn = self.review_button('新增／補填欄位', self.add_quotation_field, row)
        self.quote_unlink_btn = self.review_button('取消帶入', self.unlink_quotation_source, row)
        for button in (self.quote_link_btn, self.quote_add_btn, self.quote_unlink_btn): button.setEnabled(False)
        inner.addLayout(row); box.setMinimumHeight(160); layout.addWidget(box)
        row=QHBoxLayout()
        self.quote_find_btn=self.review_button('找候選',self.find_quotation_candidates,row)
        self.quote_capture_btn=self.review_button('框選補入',self.capture_quotation_field,row)
        self.quote_review_btn=self.review_button('定位／修正',self.review_quotation_field,row)
        self.quote_confirm_btn=self.review_button('確認報價 → 下一欄',self.verify_next_quotation,row)
        self.quote_confirm_btn.setObjectName('primary'); inner.addLayout(row)
        box.setMinimumHeight(265)
        self._selected_quote_key = None; self._quotation_destination = None; self._quotation_document = None

    def refresh_quotation_overview(self):
        if self._quotation_document != self.doc['id']:
            self._selected_quote_key = None; self._quotation_destination = None; self._quotation_document = self.doc['id']
        self.quotation_data = quotation_rows(self.doc,include_proposals=True)
        table = self.quotation_table; table.blockSignals(True); table.setRowCount(len(self.quotation_data))
        regions = {e['id']: e for e in self.doc['regions']}
        for i, field in enumerate(self.quotation_data):
            r = field['source']
            text = field['display_value']
            source_text = '\n'.join(f'P{regions[e]["page"]}：{regions[e]["text"]}'
                                    for e in r['evidence_ids'] if e in regions) if r else ''
            tooltip = (f'{field["label"]}：{text}\n來源：{r["label"]}\n' +
                       (source_text or '尚未連結圖面證據，請框選或勾選既有框')) if r else (
                       f'{field["label"]}：{field["status"]}。請選擇左側來源後帶入，或新增補填。'
                       + (f'\n左側有 {len(field["candidate_ids"])} 筆相符候選；來源不明確或內容未完整時，請人工選擇。' if field['candidate_ids'] else ''))
            if r:
                if field['proposed']:
                    tooltip+='\n首選建議，尚未人工確認：'+field['proposal_reason']
                    if field['proposal_issues']: tooltip+='\n待核對：'+', '.join(field['proposal_issues'])
                    if field['proposal_alternatives']:
                        tooltip+='\n其他候選：'+ '；'.join((a['value']+' '+a.get('unit','')).strip() for a in field['proposal_alternatives'] if a['requirement_id']!=r['id'])
                else:
                    tooltip += ('\n自動帶入：欄位與來源相符；覆核狀態沿用左側來源。' if field['automatic'] else '\n人工指定來源。')
                if r.get('role_binding')=='rfq_issuer_to_nailok':
                    tooltip+='\n客戶角色推定：圖頭公司發出詢價給耐落；請確認交易角色，三處原圖證據均可回查。'
                if r.get('semantic_rule'): tooltip+='\n工程語意方法：'+r['semantic_rule']
                if r.get('skill_method'):
                    import json
                    tooltip+='\nSkill 方法：'+r['skill_method']
                    tooltip+='\n結構化解析：'+json.dumps(r.get('skill_normalized'),ensure_ascii=False)
                    if r.get('skill_issues'): tooltip+='\n待核對：'+', '.join(r['skill_issues'])
                if field.get('manual_issue'): tooltip+='\n須修正：'+field['manual_issue']
                tooltip += '\n單擊選擇帶入目標；雙擊定位既有來源。'
            elif field['rejected_candidates']:
                tooltip += '\n未自動帶入的原因：\n' + '\n'.join(
                    f'{x["label"]}：{x["reason"]}' for x in field['rejected_candidates'])
                if len(field['rejected_candidates']) == 1: tooltip += '\n雙擊可定位待判定的候選與原圖；不會直接帶入。'
            for c, value in enumerate((field['label'], text.replace('\n', ' '), field['status'])):
                item = QTableWidgetItem(value); item.setToolTip(tooltip)
                item.setData(Qt.ItemDataRole.UserRole, field['key']); table.setItem(i, c, item)
            table.item(i, 2).setForeground(QColor('#13756b' if field['status'] == '已確認' else '#9c632b'))
        table.clearSelection(); table.setCurrentCell(-1, -1); table.blockSignals(False)
        self.sync_quotation_selection()

    def sync_quotation_selection(self):
        # Source selection and destination selection are independent cursors.
        key = self._quotation_destination
        self.quotation_table.blockSignals(True)
        if key:
            index = next(i for i, f in enumerate(self.quotation_data) if f['key'] == key)
            self.quotation_table.selectRow(index)
            self.quotation_table.scrollToItem(self.quotation_table.item(index, 0))
        else:
            self.quotation_table.clearSelection(); self.quotation_table.setCurrentCell(-1, -1)
        self.quotation_table.blockSignals(False)
        self.update_quotation_target()

    def update_quotation_target(self):
        field = next((f for f in self.quotation_data if f['key'] == self._quotation_destination), None)
        self.quotation_target.setText(('帶入目標：' + field['label'] + ('（首選建議，待人工確認）' if field['proposed'] else '（已自動帶入，可雙擊覆核）' if field['automatic'] else ''))
                                      if field else '各欄已先帶入適合候選；雙擊內容可定位原圖，待補填欄可找候選或框選。')
        editable = self.doc is not None and not self.form_dirty()
        self.quote_link_btn.setEnabled(editable and field is not None and self.current_rid is not None)
        self.quote_add_btn.setEnabled(editable and field is not None)
        mapped = field is not None and (field['source'] is not None or bool(self.doc.get('quotation_mapping',{}).get(field['key']))) if self.doc else False
        self.quote_unlink_btn.setEnabled(editable and mapped)
        for button in (self.quote_find_btn,self.quote_capture_btn,self.quote_review_btn,self.quote_confirm_btn):
            button.setEnabled(field is not None and self.doc is not None)

    def quotation_selected(self):
        if self.loading or not self.doc: return
        row = self.quotation_table.currentRow()
        if row < 0: return
        field = self.quotation_data[row]; self._selected_quote_key = field['key']; self._quotation_destination = field['key']
        if self._pending_quote_capture and self._pending_quote_capture!=field['key']:
            self.canvas.set_mode('select')
            self.status.setText('已切換報價目標，請再次按「框選補入」選取這一欄的原文。')
        self.feedback.record(self.doc,'select_quotation_target',field=field['key'],
                             detail={'source_id':field['source']['id'] if field['source'] else None})
        self.update_quotation_target()

    def navigate_quotation_source(self, row):
        self.require_saved_form()
        field = self.quotation_data[row]; source = field['source']; warning = ''
        if source is None and len(field['rejected_candidates']) == 1:
            candidate = field['rejected_candidates'][0]
            source = next((r for r in self.doc['requirements'] if r['id'] == candidate['requirement_id'] and not r['deleted']), None)
            warning = candidate['reason']
        if source is None: raise ValueError('此報價欄位尚未帶入來源，請從左側選取後帶入。')
        self.reveal_review(source['id']); self.select_review_id(source['id'])
        self.edit_tabs.setCurrentIndex(0); self.focus_review_evidence()
        self.status.setText('此候選未自動帶入：' + warning if warning else '已定位報價來源，請對照中央圖面確認。')

    def quotation_key(self):
        if not self._quotation_destination: raise ValueError('請先在右側選取要帶入的報價欄位')
        return self._quotation_destination

    def link_quotation_source(self):
        self.require_saved_form(); key = self.quotation_key(); r = self.current_requirement()
        self.doc = self.store.set_quotation_source(self.doc['id'], self.doc['version'], key, r['id'],
            self.reason_text('人工選擇報價欄位來源；仍須對照原圖覆核'))
        self.feedback.record(self.doc,'bind_quotation',rid=r['id'],field=key)
        self.refresh(); self.status.setText('已帶入同一筆來源資料；左右內容、證據與覆核狀態同步。')

    def unlink_quotation_source(self):
        self.require_saved_form(); key = self.quotation_key()
        self.doc = self.store.set_quotation_source(self.doc['id'], self.doc['version'], key, None,
            self.reason_text('人工取消報價帶入；保留原始分析結果'))
        self.feedback.record(self.doc,'unbind_quotation',field=key)
        self.refresh(); self.status.setText('已取消帶入；左側來源與覆核歷程保留。')

    def add_quotation_field(self):
        self.require_saved_form(); key = self.quotation_key()
        existing = next(f['source'] for f in self.quotation_data if f['key'] == key)
        if existing:
            self.reveal_review(existing['id']); self.select_review_id(existing['id']); self.edit_tabs.setCurrentIndex(0)
            self.status.setText('此報價欄位已有來源，可直接修正或重新選框辨識。'); return
        self.doc = self.store.set_quotation_source(self.doc['id'], self.doc['version'], key, None,
            self.reason_text('人工補新增報價欄位，待連結原圖證據並確認'), create=True)
        self.current_rid = self.doc['quotation_mapping'][key]
        self.reveal_review(self.current_rid); self.refresh(); self.edit_tabs.setCurrentIndex(0)
        self.status.setText('已新增待補欄位；請輸入內容、選框連結原圖並確認。')

    def preview_review_values(self, *_):
        if self.loading or self.loaded_form is None or not self.doc: return
        dirty = self.form_dirty(); r = self.current_requirement()
        text = (self.value.toPlainText().strip() + ' ' + self.unit.text().strip()).strip()
        text = text or STATE_LABELS[self.value_state.currentData()]
        status = '未儲存' if dirty else '已確認' if r['status'] == 'verified' else '待覆核'
        self.requirements.blockSignals(True)
        for i in range(self.requirements.rowCount()):
            if self.requirements.item(i, 0).data(Qt.ItemDataRole.UserRole) == self.current_rid:
                self.requirements.item(i, 1).setText(text.replace('\n', ' '))
                self.requirements.item(i, 2).setText(status)
        self.requirements.blockSignals(False)
        for i, field in enumerate(self.quotation_data):
            if field['source'] and field['source']['id'] == self.current_rid:
                self.quotation_table.item(i, 1).setText(text.replace('\n', ' '))
                self.quotation_table.item(i, 2).setText(status)
        self.update_quotation_target()
