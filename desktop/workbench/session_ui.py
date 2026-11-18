from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QLabel,QHBoxLayout,QDialog,
    QListWidget,QListWidgetItem,QLineEdit,QCheckBox,QDialogButtonBox,QFileDialog)
from .domain import STATUS_LABELS
from .session_files import save_result_set, load_result_set

class SessionMixin:
    def welcome_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setContentsMargins(70,50,70,50); layout.addStretch()
        title=QLabel('尚未載入工程圖'); title.setObjectName('documentTitle'); layout.addWidget(title)
        label=QLabel('匯入 PDF 或圖片開始辨識，也可以載入已保存的成果。')
        label.setWordWrap(True); layout.addWidget(label)
        layout.addStretch(); return page

    def open_manual(self):
        path=Path(__file__).resolve().parents[1]/'docs'/'manual_0.11.0.html'
        if not path.is_file(): raise ValueError('找不到操作手冊：'+str(path))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_saved_results(self):
        self.require_saved_form()
        docs=sorted(self.store.list_documents(),key=lambda d:(d['tenant']=='design-example',d['name']))
        dialog=QDialog(self); dialog.setWindowTitle('載入已保存的成果'); dialog.resize(850,650)
        layout=QVBoxLayout(dialog); layout.addWidget(QLabel('勾選要開啟的文件；載入會保留原辨識內容、版本與覆核歷程。'))
        query=QLineEdit(); query.setPlaceholderText('搜尋檔名'); layout.addWidget(query)
        examples=QCheckBox('包含設計示例'); layout.addWidget(examples)
        listing=QListWidget(); layout.addWidget(listing,1)
        for doc in docs:
            pending=sum(not r['deleted'] and r['status']!='verified' for r in doc['requirements'])
            item=QListWidgetItem(f'{doc["name"]}　v{doc["version"]}　{STATUS_LABELS.get(doc["status"],doc["status"])}　待覆核 {pending} 項')
            item.setData(Qt.ItemDataRole.UserRole,doc); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked); listing.addItem(item)
        def filtering():
            for i in range(listing.count()):
                item=listing.item(i); doc=item.data(Qt.ItemDataRole.UserRole)
                hidden=query.text().casefold() not in doc['name'].casefold() or (doc['tenant']=='design-example' and not examples.isChecked())
                item.setHidden(hidden)
                if hidden: item.setCheckState(Qt.CheckState.Unchecked)
        def select(checked):
            for i in range(listing.count()):
                item=listing.item(i); item.setCheckState(Qt.CheckState.Checked if checked and not item.isHidden() else Qt.CheckState.Unchecked)
        query.textChanged.connect(filtering); examples.toggled.connect(filtering); filtering()
        row=QHBoxLayout(); self.button('勾選目前清單全部',lambda:select(True),row); self.button('取消勾選',lambda:select(False),row); layout.addLayout(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Open|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec():
            ids=[listing.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(listing.count()) if not listing.item(i).isHidden() and listing.item(i).checkState()==Qt.CheckState.Checked]
            if not ids: raise ValueError('請至少勾選一份成果')
            self.load_documents(document_ids=self.loaded_document_ids|set(ids))
            self.status.setText(f'已載入 {len(ids)} 份既有成果；開啟文件時補列已保存原文候選，不重做 OCR、不覆寫人工值。')

    def open_result_set(self):
        self.require_saved_form()
        path,_=QFileDialog.getOpenFileName(self,'開啟本機成果清單',str(self.store.root),'DrawIQ 成果清單 (*.drawiq.json *.json)')
        if path:
            ids=load_result_set(self.store,path)
            self.load_documents(document_ids=self.loaded_document_ids|set(ids))
            self.status.setText(f'已載入清單中 {len(ids)} 份文件的目前保存版本，沒有重做 OCR。')

    def open_original_results(self):
        self.require_saved_form()
        path=Path(__file__).resolve().parents[1]/'docs'/'既有成果_76份.drawiq.json'
        if not path.exists(): raise ValueError('此安裝沒有原有成果集；請用「載入既有成果」選取本機文件')
        ids=load_result_set(self.store,path); self.load_documents(document_ids=set(ids))
        self.status.setText(f'已載入原有 {len(ids)} 份成果；開啟文件時整理已保存的候選與來源，實際重新辨識請按解析本頁。')

    def save_result_set(self):
        self.require_saved_form()
        if not self.loaded_document_ids: raise ValueError('請先載入成果')
        path,_=QFileDialog.getSaveFileName(self,'儲存目前載入成果的清單',str(self.store.root/'成果清單.drawiq.json'),'DrawIQ 成果清單 (*.drawiq.json)')
        if path:
            save_result_set(self.store,self.loaded_document_ids,path); self.status.setText('成果清單已保存：'+path)

    def unload_documents(self):
        self.require_saved_form()
        if self.worker: raise ValueError('請先等候目前工作結束')
        self.loaded_form=None; self.doc=None; self.current_rid=None; self.current_region=None; self.loaded_document_ids=set()
        self.documents.blockSignals(True); self.documents.clear(); self.documents.blockSignals(False)
        self.doc_count.setText('尚未載入資料'); self.business_page.clear_document()
        self.content_stack.setCurrentWidget(self.home); self.status.setText('已回到初始空白畫面；既有資料與歷程仍保留。')
