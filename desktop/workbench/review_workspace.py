"""One review selection, source canvas and editor for individual and batch review."""
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView)
from .domain import STATE_LABELS, checks
from .review_queue import review_groups


class ReviewWorkspaceMixin:
    def review_button(self, text, fn, layout):
        button=QPushButton(text)
        button.clicked.connect(lambda:self.review_action(fn))
        layout.addWidget(button)
        return button

    def review_action(self, fn):
        # Normal validation stays beside the form instead of opening a modal.
        try:
            result=fn()
            self.review_feedback.setText(self.status.text())
            self.review_feedback.setStyleSheet('color:#13756b;')
            return result
        except Exception as exc:
            self.status.setText(str(exc))
            self.review_feedback.setText(str(exc))
            self.review_feedback.setStyleSheet('color:#9c4324;')

    def build_review_queue(self, layout):
        self.review_counter=QLabel(); self.review_counter.setWordWrap(True)
        layout.addWidget(self.review_counter)
        self.review_progress=QProgressBar(); self.review_progress.setFixedHeight(6)
        self.review_progress.setTextVisible(False); layout.addWidget(self.review_progress)
        row=QHBoxLayout()
        self.review_group=QComboBox(); self.review_group.setMinimumContentsLength(14)
        self.review_group.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.review_group.addItem('全部群組',None); row.addWidget(self.review_group,1)
        self.review_state=QComboBox()
        for label, key in [('待覆核','pending'),('全部項目','all'),('已確認','verified')]: self.review_state.addItem(label,key)
        self.review_state.setCurrentIndex(1)
        row.addWidget(self.review_state); layout.addLayout(row)
        self.req_filter=QLineEdit(); self.req_filter.setPlaceholderText('搜尋欄位、內容、單位或原文')
        self.req_filter.setClearButtonEnabled(True); layout.addWidget(self.req_filter)
        self._review_filters=(None,'all','')
        self.review_group.currentIndexChanged.connect(self.filter_requirements)
        self.review_state.currentIndexChanged.connect(self.filter_requirements)
        self.req_filter.textChanged.connect(self.filter_requirements)
        self.requirements=QTableWidget(0,3)
        self.requirements.setHorizontalHeaderLabels(['勾選 / 欄位','內容','狀態'])
        self.requirements.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.requirements.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.requirements.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.requirements.verticalHeader().hide(); self.requirements.setAlternatingRowColors(True)
        for i in (0,1): self.requirements.horizontalHeader().setSectionResizeMode(i,QHeaderView.ResizeMode.Stretch)
        self.requirements.setColumnWidth(2,70)
        self.requirements.itemSelectionChanged.connect(self.requirement_selected)
        self.requirements.cellClicked.connect(lambda *_: self.feedback.record(self.doc,'select_candidate',
            rid=self.current_rid,field=self._quotation_destination) if self.doc else None)
        self.requirements.itemChanged.connect(self.update_batch_controls)
        self.requirements.setMinimumHeight(100)
        layout.addWidget(self.requirements,1)
        row=QHBoxLayout(); row.setSpacing(4)
        self.review_button('勾選本清單',lambda:self.check_visible_review(True),row)
        self.review_button('清除勾選',lambda:self.check_visible_review(False),row)
        layout.addLayout(row); row=QHBoxLayout(); row.setSpacing(4)
        self.batch_verify=self.review_button('確認勾選 (0)',lambda:self.commit_review_batch('verify'),row)
        self.batch_exclude=self.review_button('排除勾選 (0)',lambda:self.commit_review_batch('exclude'),row)
        layout.addLayout(row)
        self.review_empty=QLabel(); self.review_empty.setWordWrap(True); layout.addWidget(self.review_empty)
        self.review_empty.hide()
        self.update_batch_controls()

    def rebuild_review_queue(self, new_document=False):
        self.review_groups=review_groups(self.doc,include_verified=True)
        self.review_group_by_rid={rid:g for g in self.review_groups for rid in g['requirement_ids']}
        reqs=[r for r in self.doc['requirements'] if not r['deleted']]
        self.review_rows={r['id']:r for r in reqs}
        previous=self.review_group.currentData() if not new_document else None
        self.review_group.blockSignals(True); self.review_group.clear()
        self.review_group.addItem('全部群組',None)
        for group in self.review_groups:
            pending=sum(self.review_rows[rid]['status']!='verified' for rid in group['requirement_ids'])
            self.review_group.addItem(f'{group["title"]} · 待覆核 {pending} / {len(group["requirement_ids"])}',group['id'])
        self.review_group.setCurrentIndex(max(0,self.review_group.findData(previous))); self.review_group.blockSignals(False)
        if new_document:
            self.review_state.blockSignals(True); self.review_state.setCurrentIndex(1); self.review_state.blockSignals(False)
            self.req_filter.blockSignals(True); self.req_filter.clear(); self.req_filter.blockSignals(False)
            self.review_feedback.clear(); self.reason.clear()
        self.requirements.blockSignals(True); self.requirements.setRowCount(len(reqs))
        self.review_search={}
        regions={e['id']:e for e in self.doc['regions']}
        for i,r in enumerate(reqs):
            from .engineering import metadata
            info=metadata(r,regions)
            label=('★ ' if r['critical'] else '')+info['label']
            item=QTableWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole,r['id'])
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked)
            self.requirements.setItem(i,0,item)
            text=(r['value']+' '+r['unit']).strip() or STATE_LABELS[r['state']]
            self.requirements.setItem(i,1,QTableWidgetItem(text.replace('\n',' ')[:150]))
            state='已確認' if r['status']=='verified' else STATE_LABELS.get(r['state'],'待覆核') if r['state']!='present' else '待覆核'
            if r.get('origin')=='drawing_observation' and r['status']!='verified' and r['state']=='present': state='候選'
            item=QTableWidgetItem(state); item.setForeground(QColor('#13756b' if r['status']=='verified' else '#9c632b'))
            self.requirements.setItem(i,2,item)
            source='\n'.join(regions[x]['text'] for x in r['evidence_ids'] if x in regions)
            reason=self.review_group_by_rid[r['id']]['reasons'].get(r['id'],'')
            tooltip=f'{label}：{text}\n原文：{source or "尚未連結證據"}'+(f'\n注意：{reason}' if reason else '')
            tooltip+='\n工程分類：'+info['category']+'\n擷取方法：'+'；'.join(info['methods'])
            tooltip+='\n'+('原文觀察候選；選為報價來源後須覆核' if r.get('origin')=='drawing_observation' else '工程欄位候選')
            for c in range(3): self.requirements.item(i,c).setToolTip(tooltip)
            self.review_search[r['id']]=f'{label} {r["key"]} {text} {source}'.casefold()
        self.requirements.blockSignals(False)
        self.apply_review_filter(select=False)
        verified=sum(r['status']=='verified' for r in reqs); pending=len(reqs)-verified
        pending_groups=sum(any(self.review_rows[rid]['status']!='verified' for rid in g['requirement_ids']) for g in self.review_groups)
        from .engineering import obligations
        needed=obligations(self.doc); remaining=sum(r['status']!='verified' for r in needed)
        self.review_counter.setText(f'全部候選 {len(reqs)} 項 · 工程／採用來源待覆核 {remaining} 項 · 已確認 {verified} 項')
        self.review_progress.setRange(0,max(1,len(reqs))); self.review_progress.setValue(verified)
        history=self.store.history(self.doc['id']); latest=history[0] if history else {}
        self.undo_exclude.setEnabled(latest.get('version')==self.doc['version'] and latest.get('action')=='review_requirements' and latest.get('detail',{}).get('operation')=='exclude')
        return reqs

    def filter_requirements(self, *_):
        if self.loading or not self.doc: return
        if self.form_dirty():
            group,state,text=self._review_filters
            for widget, value in ((self.review_group,group),(self.review_state,state)):
                widget.blockSignals(True); widget.setCurrentIndex(max(0,widget.findData(value))); widget.blockSignals(False)
            self.req_filter.blockSignals(True); self.req_filter.setText(text); self.req_filter.blockSignals(False)
            self.review_feedback.setText('目前有未儲存的修改。按「確認並下一項」，或先儲存／還原後再篩選。')
            return
        self.apply_review_filter(select=True)

    def apply_review_filter(self, *, select):
        group=self.review_group.currentData(); state=self.review_state.currentData(); text=self.req_filter.text().casefold()
        self._review_filters=(group,state,self.req_filter.text())
        self.review_group.setToolTip(self.review_group.currentText())
        first=None; current_visible=False
        self.requirements.blockSignals(True)
        for i in range(self.requirements.rowCount()):
            item=self.requirements.item(i,0); rid=item.data(Qt.ItemDataRole.UserRole); r=self.review_rows[rid]
            visible=(group is None or self.review_group_by_rid[rid]['id']==group)
            visible=visible and (state=='all' or (r['status']=='verified')==(state=='verified')) and text in self.review_search[rid]
            self.requirements.setRowHidden(i,not visible)
            # A filter change must never leave hidden rows approved by a batch click.
            if not visible or select: item.setCheckState(Qt.CheckState.Unchecked)
            if visible and first is None: first=i
            if visible and rid==self.current_rid: current_visible=True
        self.requirements.blockSignals(False)
        self.review_empty.setVisible(first is None)
        self.review_empty.setText('這個篩選沒有項目。可切換「全部項目」或其他群組。')
        if select and not current_visible:
            if first is not None: self.requirements.selectRow(first); self.requirement_selected()
            else: self.clear_review_editor()
        self.update_batch_controls()

    def clear_review_editor(self):
        self.loaded_form=None; self.current_rid=None; self.current_region=None
        self.requirements.blockSignals(True); self.requirements.clearSelection(); self.requirements.setCurrentCell(-1,-1); self.requirements.blockSignals(False)
        self.value.clear(); self.unit.clear(); self.links.clear()
        self.depends.clear(); self.candidates.clear(); self.review_evidence.clear()
        for widget in self.fields.values(): widget.clear()
        self.set_review_editor_enabled(False); self.show_page(keep=True)
        self.sync_quotation_selection()

    def set_review_editor_enabled(self, enabled):
        self.edit_tabs.setEnabled(enabled); self.save_btn.setEnabled(enabled)
        self.verify_btn.setEnabled(enabled); self.next_btn.setEnabled(enabled); self.exclude_current_btn.setEnabled(enabled)

    def checked_review_ids(self):
        return [self.requirements.item(i,0).data(Qt.ItemDataRole.UserRole) for i in range(self.requirements.rowCount())
                if not self.requirements.isRowHidden(i) and self.requirements.item(i,0).checkState()==Qt.CheckState.Checked]

    def update_batch_controls(self, *_):
        count=len(self.checked_review_ids())
        for button,label in ((self.batch_verify,'確認勾選'),(self.batch_exclude,'排除勾選')):
            button.setText(f'{label} ({count})'); button.setEnabled(count>0)

    def check_visible_review(self, checked):
        self.require_saved_form()
        self.requirements.blockSignals(True)
        for i in range(self.requirements.rowCount()):
            self.requirements.item(i,0).setCheckState(Qt.CheckState.Checked if checked and not self.requirements.isRowHidden(i) else Qt.CheckState.Unchecked)
        self.requirements.blockSignals(False); self.update_batch_controls()

    def commit_review_batch(self, action):
        self.require_saved_form(); ids=self.checked_review_ids()
        note='人工逐列核對原圖、欄名、規格、值、單位與條件' if action=='verify' else '人工判定所勾選候選不適用，排除並保留歷程'
        self.doc=self.store.review_requirements(self.doc['id'],self.doc['version'],ids,self.reason_text(note),action=action)
        self.refresh(); self.select_next_review()
        self.status.setText(f'已{"確認" if action=="verify" else "排除"} {len(ids)} 項。'+('可按「復原排除」還原為待覆核。' if action=='exclude' else '未勾選的項目保留原狀。'))

    def exclude_current_review(self):
        self.require_saved_form(); rid=self.current_requirement()['id']
        self.doc=self.store.review_requirements(self.doc['id'],self.doc['version'],[rid],self.reason_text('人工判定此候選不適用，排除並保留歷程'),action='exclude')
        self.refresh(); self.select_next_review(); self.status.setText('已排除此項；可立即按「復原排除」還原為待覆核。')

    def undo_last_exclusion(self):
        self.require_saved_form()
        latest=self.store.history(self.doc['id'])[0]
        self.doc=self.store.undo_review_exclusion(self.doc['id'],self.doc['version'],self.reason_text('人工復原最近一次排除，重新覆核'))
        self.current_rid=latest['detail']['requirement_ids'][0]
        self.reveal_review(self.current_rid); self.refresh(); self.status.setText('已復原為待覆核；排除與復原歷程均保留。')

    def select_next_review(self, preferred_group=None):
        pending=[r for r in self.doc['requirements'] if not r['deleted'] and r['status']!='verified']
        verified={r['id'] for r in self.doc['requirements'] if not r['deleted'] and r['status']=='verified'}
        visible={self.requirements.item(i,0).data(Qt.ItemDataRole.UserRole) for i in range(self.requirements.rowCount()) if not self.requirements.isRowHidden(i)}
        pending.sort(key=lambda r:(r['id'] not in visible,
            self.review_group_by_rid.get(r['id'],{}).get('id')!=preferred_group,not r['critical']))
        ready=next((r for r in pending if set(r.get('dependencies',[]))<=verified),None)
        if ready:
            if ready['id'] not in visible: self.reveal_review(ready['id'],pending=True)
            self.select_review_id(ready['id'])
            self.edit_tabs.setCurrentIndex(0)
            self.status.setText(f'已前往下一項；尚有 {len(pending)} 項待覆核。')
        elif not pending:
            self.status.setText('本文件所有項目已確認。請檢查有無漏項，再按「核准文件」。')
        else: self.status.setText('上游依賴尚未完成；請在「詳細條件」與「框與關聯」檢查依賴關係。')

    def select_review_id(self, rid):
        for i in range(self.requirements.rowCount()):
            if self.requirements.item(i,0).data(Qt.ItemDataRole.UserRole)==rid:
                self.requirements.selectRow(i); self.requirement_selected()
                self.requirements.scrollToItem(self.requirements.item(i,0)); return

    def reveal_review(self, rid, *, pending=False):
        # Source links must reveal their exact item even when a different group is filtered.
        for widget,index in ((self.review_group,0),(self.review_state,0 if pending else 1)):
            widget.blockSignals(True); widget.setCurrentIndex(index); widget.blockSignals(False)
        self.req_filter.blockSignals(True); self.req_filter.clear(); self.req_filter.blockSignals(False)
        if self.doc and hasattr(self,'review_rows'): self.apply_review_filter(select=False)

    def update_review_evidence(self):
        r=self.current_requirement(); self.set_review_editor_enabled(True)
        linked=[e for e in self.doc['regions'] if e['id'] in r['evidence_ids']]
        raw='\n'.join(f'P{e["page"]}：{e["text"]}' for e in linked)
        details='；'.join(f'{label}：{r[key] if r[key]!="document" else "整份文件"}' for key,label in [('subject','對象'),('scope','範圍'),('conditions','條件'),('upper_tolerance','上公差'),('lower_tolerance','下公差')] if r.get(key))
        attention=self.review_group_by_rid.get(r['id'],{}).get('reasons',{}).get(r['id'],'')
        issues=[i['message'] for i in checks(self.doc) if i['requirement_id']==r['id']]
        text=('★ 放行必要項目\n' if r['critical'] else '')+(raw or '沒有原圖證據：請在左側新增框，或到「框與關聯」勾選既有框。')
        if details: text+='\n'+details
        if attention: text+='\n注意：'+attention
        if issues: text+='\n待處理：'+'；'.join(dict.fromkeys(issues))
        self.review_evidence.setPlainText(text); self.review_evidence.setToolTip(text)

    def focus_review_evidence(self):
        r=self.current_requirement()
        regions=[e for e in self.doc['regions'] if e['id'] in r['evidence_ids']+r['feature_ids'] and e['page']==self.page_select.currentData()]
        if not regions: self.canvas.fit(); return
        boxes=[e['box'] for e in regions]
        x0=min(b[0] for b in boxes); y0=min(b[1] for b in boxes); x1=max(b[2] for b in boxes); y1=max(b[3] for b in boxes)
        # Context around small text is retained, rather than zooming into a single glyph.
        cx=(x0+x1)/2; cy=(y0+y1)/2; width=max(.22,x1-x0+.08); height=max(.18,y1-y0+.08)
        rect=QRectF(max(0,cx-width/2)*self.canvas.width_px,max(0,cy-height/2)*self.canvas.height_px,
                    min(1,width)*self.canvas.width_px,min(1,height)*self.canvas.height_px)
        self.canvas.fitInView(rect,Qt.AspectRatioMode.KeepAspectRatio)
