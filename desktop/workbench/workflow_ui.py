"""Task-oriented front door: summary, corpus/knowledge, and business drafts."""
import html
import json
from pathlib import Path
from PyQt6.QtCore import Qt,QTimer,QUrl
from PyQt6.QtGui import QColor,QBrush,QPen,QDesktopServices
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTextBrowser,QLineEdit,QComboBox,QSplitter,
    QTabWidget,QFormLayout,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QDialog,QDialogButtonBox,QListWidget,
    QListWidgetItem,QGraphicsView,QGraphicsScene,QGraphicsRectItem,QPlainTextEdit,QCheckBox,QScrollArea)
from .knowledge import summary,summary_html,fact_text,CATEGORIES
from .canvas import DrawingCanvas
from .graph_view import KnowledgeGraphView
from .domain import clone,requirement,uid,STATUS_LABELS
from .corpus import export_corpus,source_documents
from .retrieval import index_status,build_index,search,evidence_answer
from .business import BusinessService,COST_FIELDS,OP_STATUSES,calculate_quote
from .specification_ui import SpecificationPanel,CommercialPanel

def help_dialog(owner):
    """Keep both help entry points on the complete versioned manual."""
    owner.open_manual()


class SummaryPage(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.doc=None; self.selected_fact=None
        layout=QVBoxLayout(self); title=QLabel('辨識報價與圖面資訊，逐欄核對原文'); title.setObjectName('documentTitle'); layout.addWidget(title)
        self.overview=QLabel(); self.overview.setWordWrap(True); layout.addWidget(self.overview)
        row=QHBoxLayout(); owner.button('重新讀這張圖',owner.quick_analyze,row); owner.button('輸出這張圖摘要',self.export_summary,row)
        owner.button('操作教學',lambda:help_dialog(owner),row); row.addStretch(); layout.addLayout(row)
        split=QSplitter(); layout.addWidget(split,1)
        self.browser=QTextBrowser(); self.browser.setOpenLinks(False); self.browser.anchorClicked.connect(self.anchor); split.addWidget(self.browser)
        right=QWidget(); rl=QVBoxLayout(right); self.preview=DrawingCanvas(); rl.addWidget(self.preview,1)
        self.evidence_text=QTextBrowser(); self.evidence_text.setMaximumHeight(145); rl.addWidget(self.evidence_text)
        buttons=QHBoxLayout(); owner.button('到原圖核對',self.open_review,buttons); self.adopt=owner.button('加入待覆核需求',self.adopt_fact,buttons); rl.addLayout(buttons)
        split.addWidget(right); split.setSizes([710,500])

    def refresh(self,doc):
        self.doc=doc; self.selected_fact=None
        if not doc: self.browser.setHtml('<h2>歡迎使用 DrawIQ</h2><p>按上方「匯入並讀圖」即可開始。</p>'); return
        s=summary(doc); self.data=s
        self.overview.setText(f'{doc["name"]}｜{len(doc["pages"])} 頁｜{len(s["facts"])} 筆候選／精煉資訊｜{len(s["issues"])} 項需確認。點摘要中的文字，右側顯示原圖證據。')
        self.browser.setHtml(summary_html(doc)); self.preview.show_page(doc['pages'][0],[])
        self.evidence_text.setPlainText('點選左側摘要中的藍綠色文字，可定位到原圖。\n初步擷取可先輸出語料，工程核准在之後完成。'); self.adopt.setEnabled(False)
        QTimer.singleShot(0,self.preview.fit)

    def anchor(self,url):
        fact_id=url.toString().split(':',1)[-1]
        f=next((f for f in self.data['facts'] if f['id']==fact_id),None)
        if f: self.show_fact(f)

    def show_fact(self,f):
        self.selected_fact=f; ids=f['evidence_ids']+f.get('feature_ids',[])
        region=next((e for e in self.doc['regions'] if e['id'] in ids),None)
        page=next(p for p in self.doc['pages'] if p['number']==(region['page'] if region else 1))
        self.preview.show_page(page,self.doc['regions'],ids)
        for item in self.preview.region_items.values(): item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.evidence_text.setPlainText(fact_text(f)+'\n\n原文：'+(f.get('raw_text') or '目前沒有可定位原文，需人工補證據')+'\n'+('已連結原需求' if f.get('requirement_id') else '語意精煉候選，尚未加入正式需求'))
        self.adopt.setEnabled(not bool(f.get('requirement_id')))

    def open_review(self): self.owner.navigate_fact(self.doc,self.selected_fact)

    def adopt_fact(self):
        self.owner.require_saved_form(); f=self.selected_fact
        if not f or f.get('requirement_id'): return
        r=requirement(f['key'],f['value'],label=f['label'],unit=f['unit'],operator=f['operator'],subject=f['subject'],scope=f['scope'],conditions=f['conditions'],
            state=f['state'],critical=f['critical'],evidence_ids=list(f['evidence_ids']),origin='semantic_refinement',raw_value=f.get('raw_value',f['value']),extraction_notes=list(f.get('quality_flags',[])))
        self.owner.doc=self.owner.store.add_requirement(self.doc['id'],self.doc['version'],r,'使用者從摘要採用帶證據的語意精煉候選')
        self.owner.current_rid=r['id']; self.owner.reveal_review(r['id']); self.owner.refresh(); self.owner.open_review_queue()

    def export_summary(self):
        doc=self.owner.need_doc(); folder=self.owner.store.root/'exports'/f'summary_{doc["id"]}_v{doc["version"]}'; folder.mkdir(parents=True,exist_ok=True)
        (folder/'工程圖摘要.html').write_text(summary_html(doc,links=False),encoding='utf-8')
        (folder/'工程圖摘要.json').write_text(json.dumps(summary(doc),ensure_ascii=False,indent=2),encoding='utf-8')
        self.owner.status.setText('摘要已輸出：'+str(folder)); QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class CorpusKnowledgePage(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.hits=[]
        layout=QVBoxLayout(self); title=QLabel('先輸出語料，再建立可搜尋、可追溯的知識'); title.setObjectName('documentTitle'); layout.addWidget(title)
        self.stats=QLabel(); self.stats.setWordWrap(True); layout.addWidget(self.stats)
        row=QHBoxLayout(); export=owner.button('一鍵輸出全部語料（Excel／JSONL／圖譜）',self.export_all,row); export.setObjectName('primary')
        owner.button('建立／更新語意索引',self.build,row); owner.button('開啟最新語料包',self.open_latest,row); layout.addLayout(row)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs,1)
        page=QWidget(); p=QVBoxLayout(page); self.index_label=QLabel(); p.addWidget(self.index_label)
        queryrow=QHBoxLayout(); self.query=QLineEdit(); self.query.setPlaceholderText('試試：需要耐高溫的塗膠要求，或每批應附哪些報告？'); queryrow.addWidget(self.query,1)
        self.scope=QComboBox(); self.scope.addItems(['含待覆核語料','只看已核准知識']); queryrow.addWidget(self.scope)
        self.category_filter=QComboBox(); self.category_filter.addItems(['全部分類']+CATEGORIES); queryrow.addWidget(self.category_filter)
        owner.button('搜尋知識',self.run_search,queryrow); p.addLayout(queryrow); self.query.returnPressed.connect(lambda:owner.guard(self.run_search))
        split=QSplitter(); p.addWidget(split,1); self.results=QListWidget(); split.addWidget(self.results)
        self.answer=QTextBrowser(); self.answer.setPlainText('搜尋結果會附上圖名、頁面及候選／核准狀態。雙擊結果可回到原圖核對。'); split.addWidget(self.answer); split.setSizes([650,500])
        self.results.itemDoubleClicked.connect(self.open_hit); self.results.currentRowChanged.connect(self.show_hit)
        self.tabs.addTab(page,'自然語言找知識')
        graphpage=QWidget(); gl=QVBoxLayout(graphpage); hint=QLabel('客戶、報價、材質、規格與塗佈作業形成實體關係網路。可跨文件查看共用實體，點關係回查證據。'); hint.setWordWrap(True); gl.addWidget(hint)
        self.graph=KnowledgeGraphView(owner); gl.addWidget(self.graph); self.tabs.addTab(graphpage,'知識圖譜')
        guide=QTextBrowser(); guide.setHtml('<h2>輸出後先開哪個檔案？</h2><p><b>corpus_catalog.xlsx</b>：分類、圖名、內容、品質提醒及覆核狀態。</p><p><b>corpus_raw.jsonl</b>：原始文字／符號／尺寸與位置。</p><p><b>corpus_structured.jsonl</b>：結構化候選及精煉主張。</p><p><b>corpus_pretraining.jsonl</b>：去重原文語料，可先清理與規劃預訓練。</p><p><b>sft_reviewed.jsonl</b>：已核准監督樣本；尚無核准資料時為空。</p><p><b>knowledge_graph.json / .graphml</b>：完整可鏈結知識圖譜。</p><p>訓練／測試分組目前為未指定，需先核對同圖與變體家族。系統不會自動微調模型，也不會把未確認 OCR 當成標準答案。</p>'); self.tabs.addTab(guide,'語料用途說明')

    def refresh(self,doc):
        docs=source_documents(self.owner.store); self.stats.setText(f'範圍：{len(docs)} 份匯入文件（不含內建設計示例）。尚未核准也能先輸出語料；已核准樣本另外保存。')
        status=index_status(self.owner.store); self.index_label.setText(status['message']+(f' · {status["chunks"]:,} 段本機向量' if status.get('chunks') else ''))
        if status['status']!='ready' and self.hits:
            self.hits=[]; self.results.clear(); self.answer.setPlainText(status['message'])
        self.graph.refresh(doc)

    def export_all(self): self.owner.start_job(lambda progress,cancelled:export_corpus(self.owner.store,progress=progress,cancelled=cancelled))
    def build(self): self.owner.start_job(lambda progress,cancelled:build_index(self.owner.store,progress,cancelled))
    def open_latest(self):
        pointer=self.owner.store.root/'corpus_latest.json'
        if not pointer.exists(): raise ValueError('尚未輸出語料，請先按「一鍵輸出全部語料」')
        path=Path(json.loads(pointer.read_text(encoding='utf-8'))['path']); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self.owner.status.setText('最新語料：'+str(path))
    def run_search(self):
        query=self.query.text().strip()
        if not query: raise ValueError('請輸入想查詢的工程問題')
        approved=self.scope.currentIndex()==1; cat=self.category_filter.currentText() if self.category_filter.currentIndex()>0 else None
        self.owner.start_job(lambda progress,cancelled:{'semantic_hits':search(self.owner.store,query,approved_only=approved,category_filter=cat),'semantic_query':query})
    def set_results(self,result):
        self.hits=result['semantic_hits']; self.results.clear()
        for i,h in enumerate(self.hits):
            matched='／'.join(h.get('matched_engineering_terms',[]))
            item=QListWidgetItem(f'{h["document_name"]} · {"已核准" if h["supervised_eligible"] else "待覆核"} · 相似度 {h["similarity"]:.2f}'+(' · '+matched if matched else '')+f'\n{h["text"][:130]}'); item.setData(Qt.ItemDataRole.UserRole,i); self.results.addItem(item)
        self.answer.setPlainText(evidence_answer(result['semantic_query'],self.hits))
    def show_hit(self,index):
        if index<0 or index>=len(self.hits): return
        h=self.hits[index]; self.answer.setPlainText(h['document_name']+'\n'+h['category']+'\n\n'+h['text']+'\n\n來源段落：\n'+h['chunk_text']+'\n\n雙擊結果可回原圖核對。')
    def open_hit(self,item):
        h=self.hits[item.data(Qt.ItemDataRole.UserRole)]; doc=self.owner.store.get(h['document_id'])
        if h['document_version']!=doc['version']: raise ValueError('圖面已更新，請更新索引後重新搜尋')
        facts=summary(doc)['facts']; f=next((f for f in facts if f['id']==h.get('fact_id')),None)
        if not f: f={'id':h['id'],'label':'原始語料','value':h['text'],'unit':'','operator':'text','state':'present','evidence_ids':h['evidence_ids'],'feature_ids':[],'requirement_id':h['requirement_id'],'raw_text':h['text']}
        self.owner.navigate_fact(doc,f)


class BusinessPage(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.service=BusinessService(owner.store); self.case=None; self.loaded=None; self.loading=False; self.doc=None
        layout=QVBoxLayout(self); self.banner=QLabel('先選圖面，再填本次詢價與成本。原圖資訊可作參考，草稿不會自動對外發送。'); self.banner.setWordWrap(True); layout.addWidget(self.banner)
        row=QHBoxLayout(); owner.button('儲存業務草稿',self.save,row).setObjectName('primary'); owner.button('還原未儲存內容',self.reset,row)
        owner.button('同步新圖面版本（重審）',self.rebase,row); owner.button('輸出詢價／報價／工序包',self.export,row); layout.addLayout(row)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs,1)
        rfq=QWidget(); form=QFormLayout(rfq); self.rfq_fields={}
        for k,label in [('customer','客戶／供應商'),('project','專案／案件名稱'),('quantity','本次詢價數量'),('unit','數量單位'),('due_date','要求交期'),('notes','承辦備註')]:
            field=QLineEdit(); field.setPlaceholderText('請填本次數量，不能用年度需求量代替' if k=='quantity' else '由承辦人確認後填寫'); self.rfq_fields[k]=field; form.addRow(label,field)
        self.source_hint=QTextBrowser(); form.addRow('本圖可引用的候選資訊',self.source_hint); self.tabs.addTab(rfq,'1 詢價條件')
        quote=QWidget(); ql=QVBoxLayout(quote); note=QLabel('成本一律按每 1 個詢價單位輸入（如 1 PCS 或 1 kg）。商務條件中的數量基數只改標價顯示。沒有的成本填 0；設定費為本批一次性費用。毛利率採（售價－成本）／售價。'); note.setWordWrap(True); ql.addWidget(note)
        form=QFormLayout(); ql.addLayout(form); self.currency=QComboBox(); self.currency.addItems(['請選幣別','TWD','USD','EUR','CNY','JPY']); form.addRow('報價幣別',self.currency); self.costs={}
        for k,label in COST_FIELDS.items(): field=QLineEdit(); field.setPlaceholderText('每 1 個詢價單位的成本；沒有此費用填 0'); self.costs[k]=field; form.addRow(label+'／每 1 詢價單位',field)
        self.setup=QLineEdit(); form.addRow('本批一次性設定費',self.setup); self.margin=QLineEdit(); self.margin.setPlaceholderText('例如 20 代表 20%'); form.addRow('目標毛利率 %',self.margin)
        owner.button('試算報價',self.calculate,ql); self.calculation=QTextBrowser(); ql.addWidget(self.calculation,1); self.tabs.addTab(quote,'2 成本與報價')
        process=QWidget(); pl=QVBoxLayout(process); pl.addWidget(QLabel('直接點格子填工序、人員、設備及日期。執行狀態需要先核准圖面；日期格式 YYYY-MM-DD。'))
        self.ops=QTableWidget(0,7); self.ops.setHorizontalHeaderLabels(['工序','負責人','設備','預定日期','狀態','工程知識','備註']); self.ops.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.ops.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); pl.addWidget(self.ops)
        row=QHBoxLayout(); owner.button('新增工序',self.add_operation,row); owner.button('刪除所選工序',self.delete_operation,row); owner.button('連結工程要求',self.link_operation,row); pl.addLayout(row); self.tabs.addTab(process,'3 製程追蹤')
        self.spec_panel=SpecificationPanel(self); self.commercial_panel=CommercialPanel(self)
        self.tabs.insertTab(1,self.spec_panel,'2 規格確認單')
        self.tabs.setTabText(2,'3 成本試算'); self.tabs.insertTab(3,self.commercial_panel,'4 商務條件'); self.tabs.setTabText(4,'5 製程追蹤')

    def clear_document(self):
        self.loading=True; self.doc=None; self.case=None; self.loaded=None; self.spec_panel.spec=None
        self.spec_panel.items.setRowCount(0); self.ops.setRowCount(0)
        for field in [*self.rfq_fields.values(),*self.costs.values(),*self.spec_panel.fields.values(),*self.commercial_panel.fields.values(),self.setup,self.margin]: field.clear()
        self.source_hint.clear(); self.calculation.clear(); self.loading=False

    def refresh(self,doc,force=False):
        if not doc: return
        if self.case and self.case['document_id']==doc['id'] and self.is_dirty() and not force: return
        self.loading=True; self.doc=doc; self.case=self.service.get(doc['id'])
        stale=self.case['source_version']!=doc['version']; self.banner.setText(doc['name']+f'｜圖面 v{doc["version"]}｜業務草稿 v{self.case["version"]}'+('｜圖面已更新，請先同步版本重審' if stale else '｜內部草稿，尚未對外發送'))
        for k,f in self.rfq_fields.items(): f.setText(self.case['rfq'][k])
        q=self.case['quote']; self.currency.setCurrentText(q['currency'] or '請選幣別')
        for k,f in self.costs.items(): f.setText(q['unit_costs'].get(k,''))
        self.setup.setText(q['setup_cost']); self.margin.setText(q['margin_percent']); self.ops.setRowCount(0)
        for op in self.case['operations']: self.insert_operation(op)
        self.spec_panel.load(self.case,doc); self.commercial_panel.load(self.case)
        s=summary(doc); lines=[fact_text(f) for f in s['facts'] if f['summary_visible'] and f['category'] in (CATEGORIES[0],CATEGORIES[1],CATEGORIES[3],CATEGORIES[4],CATEGORIES[5])]
        self.source_hint.setPlainText('圖面候選（未自動代入本次數量與價格）：\n\n'+'\n'.join(dict.fromkeys(lines)))
        self.loading=False; self.loaded=self.values(); self.calculation.setPlainText('輸入本次數量與成本後，按「試算報價」。')

    def insert_operation(self,op):
        i=self.ops.rowCount(); self.ops.insertRow(i)
        for j,key in [(0,'name'),(1,'owner'),(2,'equipment'),(3,'due_date'),(6,'notes')]: self.ops.setItem(i,j,QTableWidgetItem(op[key]))
        self.ops.item(i,0).setData(Qt.ItemDataRole.UserRole,clone(op))
        combo=QComboBox(); combo.addItems(OP_STATUSES); combo.setCurrentText(op['status']); self.ops.setCellWidget(i,4,combo)
        item=QTableWidgetItem(f'{len(op["evidence_fact_ids"])} 項關聯'); item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable); self.ops.setItem(i,5,item)

    def values(self):
        if not self.case: return None
        c=clone(self.case); c['rfq']={k:w.text().strip() for k,w in self.rfq_fields.items()}
        c['quote']={'currency':self.currency.currentText() if self.currency.currentIndex()>0 else '', 'unit_costs':{k:w.text().strip() for k,w in self.costs.items()},'setup_cost':self.setup.text().strip(),'margin_percent':self.margin.text().strip()}; c['operations']=[]
        c['specification']=self.spec_panel.values(); c['commercial']=self.commercial_panel.values()
        for i in range(self.ops.rowCount()):
            op=clone(self.ops.item(i,0).data(Qt.ItemDataRole.UserRole))
            for j,key in [(0,'name'),(1,'owner'),(2,'equipment'),(3,'due_date'),(6,'notes')]: op[key]=self.ops.item(i,j).text().strip()
            op['status']=self.ops.cellWidget(i,4).currentText(); c['operations'].append(op)
        return c
    def is_dirty(self): return not self.loading and self.loaded is not None and self.values()!=self.loaded
    def save(self):
        if not self.case: raise ValueError('請先選擇圖面')
        if self.owner.form_dirty(): raise ValueError('原圖覆核有未儲存的修改，請先儲存或還原')
        self.case=self.service.save(self.values()); self.refresh(self.owner.store.get(self.case['document_id']),force=True); self.owner.status.setText('業務草稿已儲存，可繼續規劃或輸出')
    def reset(self):
        if self.doc: self.refresh(self.owner.store.get(self.doc['id']),force=True)
    def rebase(self):
        if self.is_dirty(): raise ValueError('請先儲存或還原表單，再同步圖面版本')
        self.service.rebase(self.doc['id']); self.refresh(self.owner.store.get(self.doc['id']),force=True)
    def calculate(self):
        if self.values() is None: raise ValueError('請先載入來源文件')
        result=calculate_quote(self.values()); self.calculation.setHtml('<h2>'+result['currency']+' '+result['basis_quote']+'／'+result['price_basis_qty']+' '+html.escape(result['unit'])+'</h2><p>未稅總額：'+result['total_quote']+'<br>總成本：'+result['total_cost']+'<br>目標毛利率：'+result['target_margin_percent']+'%<br>外加稅額：'+(result['tax_amount'] or '待確認')+'<br>含稅總額：'+(result['total_with_tax'] or '待確認')+'</p><p>'+result['scope']+'</p><p>此為試算；規格與商務條件另行檢查，請保存輸入。</p>')
    def export(self):
        self.save(); path=self.service.export(self.case['document_id']); self.owner.status.setText('業務草稿已輸出：'+str(path)); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
    def add_operation(self):
        self.insert_operation({'id':uid('op'),'name':'新工序','owner':'','equipment':'','due_date':'','status':'待規劃','evidence_fact_ids':[],'notes':''})
    def delete_operation(self):
        row=self.ops.currentRow()
        if row>=0: self.ops.removeRow(row)
    def link_operation(self):
        row=self.ops.currentRow()
        if row<0: raise ValueError('先選擇一個工序')
        op=self.ops.item(row,0).data(Qt.ItemDataRole.UserRole); dialog=QDialog(self); dialog.setWindowTitle('選取此工序應遵守的工程要求'); dialog.resize(760,580)
        layout=QVBoxLayout(dialog); items=QListWidget(); layout.addWidget(items)
        for f in summary(self.doc)['facts']:
            if not f['summary_visible']: continue
            item=QListWidgetItem(fact_text(f)); item.setData(Qt.ItemDataRole.UserRole,f['id']); item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if f['id'] in op['evidence_fact_ids'] else Qt.CheckState.Unchecked); items.addItem(item)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec():
            op=clone(op); op['evidence_fact_ids']=[items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(items.count()) if items.item(i).checkState()==Qt.CheckState.Checked]
            self.ops.item(row,0).setData(Qt.ItemDataRole.UserRole,op); self.ops.item(row,5).setText(f'{len(op["evidence_fact_ids"])} 項關聯')
