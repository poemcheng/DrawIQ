"""Shared crop preview for both desktop editions; nothing is saved implicitly."""
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QLabel,
    QPushButton, QPlainTextEdit, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QProgressBar)
from .canvas import DrawingCanvas
from .bounded import atomic_json
from .region_capture import prepare_capture, capture_dir, read_capture, save_capture, file_hash


class CaptureWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, root, request, parent):
        super().__init__(parent); self.root = root; self.request = request

    def run(self):
        try:
            result = read_capture(self.root, self.request, cancelled=self.isInterruptionRequested)
        except Exception as exc:
            result = dict(status='failed', text='', tokens=[], candidates=[], error=str(exc), cached=False,
                          capture_id=self.request['id'])
            atomic_json(capture_dir(self.root, self.request['id'])/'result.json', result)
        self.completed.emit(result)


class CaptureDialog(QDialog):
    def __init__(self, store, doc, page, box, *, rid=None, kind='evidence', replace_id=None,
                 replace_links=False, rotation=0, reason='人工框選截圖與辨識；內容待覆核', parent=None,
                 interaction_id=None,quotation_field=None):
        super().__init__(parent)
        self.store=store; self.doc=doc; self.rid=rid; self.kind=kind; self.replace_id=replace_id
        self.replace_links=replace_links; self.reason=reason; self.ocr_result=None; self.worker=None
        self.closing=False; self.saved_document=None; self.saved_region_id=None; self.saved_requirement_id=None
        self.selected_key='manual.capture'; self.request=prepare_capture(doc,store.root,page,box,rotation)
        self.folder=capture_dir(store.root,self.request['id'])
        self.request.update(interaction_id=interaction_id,quotation_field=quotation_field)
        atomic_json(self.folder/'request.json',self.request)
        self.target=next((r for r in doc['requirements'] if r['id']==rid and not r.get('deleted')),None)
        self.setWindowTitle('框選截圖與辨識｜核對後套用'); self.resize(1120,780); self.setMinimumSize(850,650)
        self.setStyleSheet('''
            QDialog,QWidget {background:#f3f7f9;color:#173b48;font-size:14px;}
            QPlainTextEdit,QLineEdit,QTableWidget {background:white;color:#173b48;border:1px solid #abc4cc;selection-background-color:#cee9ec;selection-color:#173b48;}
            QTableWidget::item {background:white;color:#173b48;padding:4px;}
            QTableWidget::item:selected {background:#cee9ec;color:#123b48;}
            QHeaderView,QHeaderView::section {background:#e0edf1;color:#173b48;padding:6px;}
            QPushButton {background:white;color:#173b48;border:1px solid #9ebcc5;border-radius:5px;padding:8px 12px;}
            QPushButton:hover {background:#e0edf1;} QPushButton:disabled {color:#687e86;background:#e6edef;}
            QPushButton#primary {background:#087f89;color:white;font-weight:bold;}
            QPushButton#primary:disabled {background:#a3bdc2;color:#f3f7f9;}
        ''')
        outer=QVBoxLayout(self)
        self.heading=QLabel('框選範圍已截圖，正在本機辨識'); self.heading.setStyleSheet('font-size:21px;font-weight:600;'); outer.addWidget(self.heading)
        self.status=QLabel('辨識上限 30 秒，可停止後自行輸入。結果須對照左側裁圖確認。');self.status.setWordWrap(True);outer.addWidget(self.status)
        split=QSplitter();outer.addWidget(split,1)
        left=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,8,0)
        ll.addWidget(QLabel(f'來源第 {page} 頁 · 原始解析度裁圖 · 旋轉 {rotation}°'))
        self.preview=DrawingCanvas(); self.preview.show_page({'number':page,'image_path':str(self.folder/'input.png')},[])
        ll.addWidget(self.preview,1)
        fit=QPushButton('符合視窗');fit.clicked.connect(self.preview.fit);ll.addWidget(fit)
        note=QLabel('Ctrl＋滾輪放大，拖曳移動畫面。\n數量僅擷取圖上印刷文字；符號可能漏辨，請目視核對。');note.setWordWrap(True);ll.addWidget(note)
        split.addWidget(left)
        right=QWidget();rl=QVBoxLayout(right);rl.setContentsMargins(8,0,0,0)
        rl.addWidget(QLabel('框內原文（可修正；原始 OCR 另存於歷程）'))
        self.raw=QPlainTextEdit();self.raw.setPlaceholderText('正在辨識…也可直接輸入框內文字');self.raw.setMaximumHeight(150);rl.addWidget(self.raw)
        old_region=next((e for e in doc['regions'] if e['id']==replace_id),None)
        if old_region:self.raw.setPlainText(old_region['text'])
        rl.addWidget(QLabel('辨識候選 · 點一下帶入下方欄位內容'))
        self.candidates=QTableWidget(0,3);self.candidates.setHorizontalHeaderLabels(['類別／欄位','內容','單位'])
        self.candidates.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidates.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidates.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidates.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
        self.candidates.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.candidates.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.candidates.cellClicked.connect(self.pick);rl.addWidget(self.candidates,1)
        target=QLabel('目前欄位：'+(self.target['label']+' ＝ '+self.target['value'] if self.target else '未選擇，可新增欄位'))
        target.setWordWrap(True);rl.addWidget(target)
        self.value=QPlainTextEdit();self.value.setPlaceholderText('套用的內容（可手動修正）');self.value.setMaximumHeight(80);rl.addWidget(self.value)
        row=QHBoxLayout();row.addWidget(QLabel('單位'));self.unit=QLineEdit();self.unit.setMaximumWidth(90);row.addWidget(self.unit)
        row.addWidget(QLabel('新增欄位名稱'));self.label=QLineEdit('框選資訊');row.addWidget(self.label,1);rl.addLayout(row)
        symbols=QHBoxLayout();symbols.addWidget(QLabel('人工補入符號'))
        for symbol in ('Ø','±','°','×','≤','≥','⊥','∥'):
            button=QPushButton(symbol);button.setToolTip('人工補入下方欄位內容：'+symbol)
            button.setStyleSheet('padding:3px;min-width:24px;');button.clicked.connect(lambda checked=False,s=symbol:self.insert_symbol(s));symbols.addWidget(button)
        rl.addLayout(symbols)
        split.addWidget(right);split.setSizes([460,630])
        self.progress=QProgressBar();self.progress.setRange(0,0);outer.addWidget(self.progress)
        bar=QHBoxLayout();outer.addLayout(bar)
        self.stop=QPushButton('停止辨識');self.stop.clicked.connect(self.stop_read);bar.addWidget(self.stop);bar.addStretch()
        self.buttons=[]
        for text,action in [('只保存來源框','frame'),('新增欄位','new'),('套用目前欄位（待覆核）','apply')]:
            b=QPushButton(text);b.clicked.connect(lambda checked=False,a=action:self.save(a));b.setEnabled(False)
            if action=='apply':b.setObjectName('primary')
            bar.addWidget(b);self.buttons.append((b,action))
        cancel=QPushButton('取消');cancel.clicked.connect(self.reject);bar.addWidget(cancel)
        QTimer.singleShot(0,self.start_read)

    def start_read(self):
        if self.closing: return
        self.preview.fit()
        self.worker=CaptureWorker(self.store.root,self.request,self)
        self.worker.completed.connect(self.on_result);self.worker.finished.connect(self.on_finished);self.worker.start()

    def on_result(self,result):
        self.ocr_result=result
        if self.closing:return
        self.heading.setText('框選辨識完成，請對照裁圖核對' if result['status']=='complete' else '請對照裁圖補正內容')
        status={'complete':'辨識完成，請核對候選','empty':'未辨識到文字，可重新框選或手動輸入',
                'partial_timeout':'辨識逾時，可重新框選或手動輸入','cancelled':'辨識已停止，可手動輸入',
                'failed':'辨識未完成，可重新框選或手動輸入'}.get(result['status'],result['status'])
        self.status.setText(status+f" · {result.get('elapsed_seconds',0):.2f} 秒"+(' · 使用相同裁圖快取' if result.get('cached') else ''))
        if result.get('error'):self.status.setToolTip(result['error'])
        # A late OCR response must never overwrite text the reviewer has started entering.
        if result['text'] and not self.raw.document().isModified(): self.raw.setPlainText(result['text'])
        items=result['candidates'];self.candidates.setRowCount(len(items))
        for row,item in enumerate(items):
            for col,text in enumerate((item['category']+'／'+item['label'],item['value'],item.get('unit',''))):
                cell=QTableWidgetItem(text);cell.setToolTip(text);self.candidates.setItem(row,col,cell)
        if not self.value.document().isModified() and not self.unit.isModified():
            match=next((n for n,c in enumerate(items) if self.target and c['key']==self.target['key']),None)
            if match is not None:self.pick(match,0)
            else:self.value.setPlainText(result['text'])

    def on_finished(self):
        worker=self.worker;self.worker=None
        if worker:worker.deleteLater()
        if self.closing:super().reject();return
        self.progress.hide();self.stop.hide()
        for b,action in self.buttons:b.setEnabled(self.ocr_result is not None and (action!='apply' or self.target is not None))

    def pick(self,row,col):
        item=self.ocr_result['candidates'][row];self.selected_key=item['key']
        self.value.setPlainText(item['value']);self.unit.setText(item.get('unit',''));self.label.setText(item['label'])
        self.candidates.selectRow(row)

    def stop_read(self):
        if self.worker:self.worker.requestInterruption();self.status.setText('正在停止辨識…');self.stop.setEnabled(False)

    def insert_symbol(self,symbol):
        self.value.insertPlainText(symbol);self.value.setFocus()

    def save(self,action):
        if self.worker or self.ocr_result is None:return
        try:
            self.saved_document,self.saved_region_id,self.saved_requirement_id=save_capture(
                self.store,self.doc,self.request,self.ocr_result,text=self.raw.toPlainText(),action=action,rid=self.rid,
                value=self.value.toPlainText().strip(),unit=self.unit.text().strip(),label=self.label.text(),key=self.selected_key,
                kind=self.kind,replace_id=self.replace_id,replace_links=self.replace_links,reason=self.reason)
        except Exception as exc:
            QMessageBox.warning(self,'尚未套用',str(exc));return
        self.accept()

    def reject(self):
        self.closing=True
        if self.worker:
            self.stop_read();self.setEnabled(False);return
        super().reject()

    def closeEvent(self,event):
        if self.worker:self.reject();event.ignore()
        else:event.accept()


def show_capture_record(parent,root,event):
    """Read a selected audit event's original crop without launching recognition."""
    detail=event.get('detail',{});capture=detail.get('capture')
    if not capture:raise ValueError('此筆歷程沒有框選截圖；舊紀錄可用「選框截圖／辨識」補建')
    folder=capture_dir(root,capture['id']);path=folder/'input.png'
    if file_hash(path)!=capture['input_sha256']:raise ValueError('框選截圖缺失或已變更')
    dialog=QDialog(parent);dialog.setWindowTitle('歷程裁圖與辨識結果');dialog.resize(940,660)
    layout=QVBoxLayout(dialog);split=QSplitter();layout.addWidget(split,1)
    preview=DrawingCanvas();preview.show_page({'number':capture['page'],'image_path':str(path)},[]);split.addWidget(preview)
    result=capture['result'];text=QPlainTextEdit();text.setReadOnly(True)
    text.setStyleSheet('background:white;color:#173b48;')
    text.setPlainText(f"版本：{event['version']}\n第 {capture['page']} 頁 · 狀態：{result['status']}\n辨識耗時：{result.get('elapsed_seconds',0):.2f} 秒\n\n原始辨識文字：\n{result['text']}\n\n人工保存原文：\n{detail.get('manual_text','')}\n\n辨識候選：\n"+
        '\n'.join(c['label']+'：'+c['value']+' '+c.get('unit','') for c in result['candidates']))
    split.addWidget(text);split.setSizes([470,470]);close=QPushButton('關閉');close.clicked.connect(dialog.accept);layout.addWidget(close)
    QTimer.singleShot(0,preview.fit);dialog.exec()
