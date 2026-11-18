import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QLineEdit,QComboBox,QTableWidget,
    QTableWidgetItem,QHeaderView,QAbstractItemView,QPlainTextEdit,QDialog,QDialogButtonBox,QFormLayout,QListWidget,QListWidgetItem,QScrollArea)
from .domain import clone
from .specification import PROFILES,ITEM_STATES,template_items,spec_status,spec_issues,quote_issues,COMMERCIAL_LABELS

class SpecificationPanel(QWidget):
    def __init__(self,business):
        super().__init__(); self.business=business; self.owner=business.owner; self.spec=None
        layout=QVBoxLayout(self); self.status=QLabel('先載入來源文件'); self.status.setWordWrap(True); layout.addWidget(self.status)
        grid=QGridLayout(); self.fields={}
        for i,(key,label) in enumerate([('form_number','確認單號'),('product_name','產品／服務'),('drawing_number','图號／文件號'),('drawing_revision','文件版次')]):
            field=QLineEdit(); self.fields[key]=field; row=i//2; col=(i%2)*2; grid.addWidget(QLabel(label.replace('图','圖')),row,col); grid.addWidget(field,row,col+1)
        self.profile=QComboBox()
        for key,label in PROFILES.items(): self.profile.addItem(label,key)
        grid.addWidget(QLabel('業務範本'),2,0); grid.addWidget(self.profile,2,1)
        self.owner.button('補入範本項目',self.merge_template,grid_row:=QHBoxLayout()); grid.addLayout(grid_row,2,2,1,2)
        layout.addLayout(grid)
        self.items=QTableWidget(0,5); self.items.setHorizontalHeaderLabels(['規格／來源','判定','內容／供應範圍','驗收／判定方式','不適用原因／依據'])
        self.items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.items.verticalHeader().hide()
        for i in range(5): self.items.horizontalHeader().setSectionResizeMode(i,QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.items,1)
        row=QHBoxLayout(); self.owner.button('連結選定規格的原圖需求',self.link_sources,row)
        self.owner.button('編輯選定格的完整文字',self.edit_cell,row); layout.addLayout(row)
        self.open_issues=QLineEdit(); self.open_issues.setPlaceholderText('未結案事項（存在內容時不可完成規格確認）'); layout.addWidget(self.open_issues)
        self.feedback=QPlainTextEdit(); self.feedback.setReadOnly(True); self.feedback.setMaximumHeight(64); layout.addWidget(self.feedback)
        row=QHBoxLayout(); self.owner.button('檢查完整性',self.check,row)
        self.owner.button('儲存並完成內部確認',self.confirm,row).setObjectName('primary')
        self.owner.button('記錄客戶確認',self.customer_confirmation,row); self.owner.button('輸出規格／報價資料包',self.business.export,row); layout.addLayout(row)

    def load(self,case,doc):
        self.spec=clone(case['specification']); self.status.setText(spec_status(case)+'｜來源 '+doc['name']+f' v{case["source_version"]}｜規格變更後須重新確認')
        for key,field in self.fields.items(): field.setText(self.spec[key])
        self.profile.setCurrentIndex(self.profile.findData(self.spec['profile'])); self.open_issues.setText(self.spec['open_issues'])
        self.populate(self.spec['items'])
        issues=spec_issues(case,doc); self.feedback.setPlainText('\n'.join(issues) if issues else '檢查通過；請承辦人核對供應範圍與驗收方式後完成內部確認。')
        if self.spec.get('review'):
            review=self.spec['review']; self.feedback.appendPlainText(f'內部確認：{review["actor"]}｜{review["at"][:10]}')
        if self.spec.get('customer_confirmation'):
            ack=self.spec['customer_confirmation']; self.feedback.appendPlainText(f'客戶確認：{ack["name"]}｜{ack["confirmed_on"]}｜依據：{ack["reference"]}')

    def populate(self,rows):
        self.items.setRowCount(0)
        for row in rows:
            i=self.items.rowCount(); self.items.insertRow(i); self.items.setRowHeight(i,64)
            label=QTableWidgetItem(row['label']+f'\n來源 {len(row["requirement_ids"])} 項'); label.setData(Qt.ItemDataRole.UserRole,clone(row)); label.setFlags(label.flags()&~Qt.ItemFlag.ItemIsEditable); self.items.setItem(i,0,label)
            state=QComboBox()
            for key,text in ITEM_STATES.items(): state.addItem(text,key)
            state.setCurrentIndex(state.findData(row['state'])); self.items.setCellWidget(i,1,state)
            for col,key in [(2,'value'),(3,'acceptance'),(4,'reason')]:
                cell=QTableWidgetItem(row[key]); cell.setToolTip(row[key]); self.items.setItem(i,col,cell)

    def values(self):
        if self.spec is None: return None
        spec=clone(self.spec); spec.update({key:w.text().strip() for key,w in self.fields.items()}); spec['profile']=self.profile.currentData(); spec['open_issues']=self.open_issues.text().strip(); spec['items']=[]
        for i in range(self.items.rowCount()):
            row=clone(self.items.item(i,0).data(Qt.ItemDataRole.UserRole)); row['state']=self.items.cellWidget(i,1).currentData()
            for col,key in [(2,'value'),(3,'acceptance'),(4,'reason')]: row[key]=self.items.item(i,col).text().strip()
            spec['items'].append(row)
        return spec

    def merge_template(self):
        self.owner.need_doc(); current=self.values(); rows=current['items']; keys={x['key'] for x in rows}
        rows.extend(x for x in template_items(self.business.doc,self.profile.currentData()) if x['key'] not in keys)
        self.populate(rows); self.feedback.setPlainText('已補入所選範本的缺少項目；原有內容保留。請明確確認或填寫不適用原因。')

    def link_sources(self):
        index=self.items.currentRow()
        if index<0: raise ValueError('請先選擇規格列')
        row=self.items.item(index,0).data(Qt.ItemDataRole.UserRole)
        dialog=QDialog(self); dialog.setWindowTitle('連結原圖需求'); dialog.resize(750,570); layout=QVBoxLayout(dialog); listing=QListWidget(); layout.addWidget(listing)
        for r in self.business.doc['requirements']:
            if r['deleted']: continue
            item=QListWidgetItem(r['label']+'：'+r['value']+' '+r['unit']); item.setData(Qt.ItemDataRole.UserRole,r['id']); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if r['id'] in row['requirement_ids'] else Qt.CheckState.Unchecked); listing.addItem(item)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec():
            row=clone(row); row['requirement_ids']=[listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count()) if listing.item(i).checkState()==Qt.CheckState.Checked]
            self.items.item(index,0).setData(Qt.ItemDataRole.UserRole,row); self.items.item(index,0).setText(row['label']+f'\n來源 {len(row["requirement_ids"])} 項')

    def edit_cell(self):
        from PyQt6.QtWidgets import QInputDialog
        row=self.items.currentRow(); col=self.items.currentColumn()
        if row<0 or col not in (2,3,4): raise ValueError('請先點選內容、驗收方式或原因的格子')
        item=self.items.item(row,col); text,ok=QInputDialog.getMultiLineText(self,'編輯規格文字',self.items.horizontalHeaderItem(col).text(),item.text())
        if ok: item.setText(text); item.setToolTip(text)

    def check(self):
        case=self.business.values()
        if case is None: raise ValueError('請先載入來源文件')
        issues=spec_issues(case,self.owner.store.get(case['document_id']))
        self.feedback.setPlainText('\n'.join(issues) if issues else '規格檢查通過，可儲存並完成內部確認。')

    def confirm(self):
        self.business.save(); case=self.business.case
        try: self.business.service.confirm_specification(case['document_id'],case['version'])
        except ValueError as exc: self.feedback.setPlainText(str(exc)); return
        self.business.refresh(self.owner.doc,force=True); self.owner.status.setText('已完成內部規格確認；客戶確認仍須另行記錄回覆依據。')

    def customer_confirmation(self):
        self.business.save(); case=self.business.case
        if spec_status(case)=='規格草稿／待確認': raise ValueError('請先完成內部規格確認')
        dialog=QDialog(self); dialog.setWindowTitle('記錄已收到的客戶確認'); layout=QFormLayout(dialog)
        name=QLineEdit(); day=QLineEdit(); reference=QLineEdit(); day.setPlaceholderText('YYYY-MM-DD')
        layout.addRow('客戶確認人',name); layout.addRow('確認日期',day); layout.addRow('回覆／簽回檔案編號',reference)
        layout.addRow(QLabel('此操作記錄已收到的回覆；不寄送資料，也不代表電子簽章。'))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addRow(buttons)
        if dialog.exec():
            self.business.service.confirm_specification(case['document_id'],case['version'],customer=name.text(),reference=reference.text(),confirmed_on=day.text())
            self.business.refresh(self.owner.doc,force=True); self.owner.status.setText('客戶確認依據已記錄。')

class CommercialPanel(QWidget):
    def __init__(self,business):
        super().__init__(); self.business=business; outer=QVBoxLayout(self)
        note=QLabel('報價前確認商務條件。稅率由承辦人填寫；數量基數只改標價顯示，不轉換 M／K 等單位。'); note.setWordWrap(True); outer.addWidget(note)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); widget=QWidget(); form=QFormLayout(widget); self.fields={}
        for key,label in COMMERCIAL_LABELS.items():
            field=QLineEdit(); self.fields[key]=field; form.addRow(label,field)
        scroll.setWidget(widget); outer.addWidget(scroll,1)
        business.owner.button('檢查報價準備狀態',self.check,outer)
        self.result=QPlainTextEdit(); self.result.setReadOnly(True); self.result.setMaximumHeight(135); outer.addWidget(self.result)
    def load(self,case):
        for key,w in self.fields.items(): w.setText(case['commercial'][key])
        self.result.setPlainText('填妥並保存條件後，檢查規格、報價數值及有效期限。')
    def values(self): return {key:w.text().strip() for key,w in self.fields.items()}
    def check(self):
        case=self.business.values()
        if case is None: raise ValueError('請先選擇來源文件')
        issues=quote_issues(case,self.business.owner.store.get(case['document_id']))
        self.result.setPlainText('\n'.join(issues) if issues else '資料已備齊，可送內部商務審核；輸出仍為草稿，不會自動對外發送或接單。')
