from __future__ import annotations
import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer
from PyQt6.QtGui import QDesktopServices, QFont, QColor, QShortcut, QKeySequence
from PyQt6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QSplitter,QLabel,QPushButton,
    QLineEdit,QTextEdit,QPlainTextEdit,QListWidget,QListWidgetItem,QTableWidget,QTableWidgetItem,QTabWidget,QComboBox,QCheckBox,
    QFileDialog,QMessageBox,QInputDialog,QScrollArea,QHeaderView,QAbstractItemView,QProgressBar,QFrame,QStackedWidget,QSizePolicy)

from .canvas import DrawingCanvas
from .region_capture_ui import CaptureDialog,show_capture_record
from .domain import STATUS_LABELS,STATES,STATE_LABELS,checks,clone,evidence,requirement,uid
from .store import Store
from .ingest import REPO,LEGACY,SUPPORTED,prepare_document,import_legacy,import_example
from .extraction import apply_analysis,models
from .bounded import analyze
from .refinement import refine
from .exporting import export_bundle
from .learning import StrategyService
from .geometry import measure_step,compare_steps
from .backup import backup_workspace
from .profiles import load_profile,apply_profile
from .workflow_ui import SummaryPage,CorpusKnowledgePage,BusinessPage,help_dialog
from .knowledge import summary
from .review_workspace import ReviewWorkspaceMixin
from .quotation_workspace import QuotationWorkspaceMixin
from .engineering_workspace import EngineeringWorkspaceMixin
from .feedback import FeedbackService
from .session_ui import SessionMixin
from .workspace_shell import WorkspaceShellMixin
from . import __version__

CORRECTION_FIELDS={'box':'框位','page':'頁碼','text':'原文','kind':'區域種類','value':'值','unit':'單位','evidence_ids':'證據關聯','feature_ids':'特徵關聯','label':'名稱','key':'屬性','subject':'作用對象','scope':'範圍','conditions':'條件','dependencies':'上游依賴','critical':'關鍵需求','state':'值狀態','operator':'比較','upper_tolerance':'上公差','lower_tolerance':'下公差'}
CORRECTION_ACTIONS={'add_region':'新增框','edit_region':'修正框／原文','edit_requirement':'修正需求／關聯'}

class Worker(QThread):
    progress=pyqtSignal(str); completed=pyqtSignal(object); failed=pyqtSignal(str)
    def __init__(self,fn): super().__init__(); self.fn=fn
    def run(self):
        try: self.completed.emit(self.fn(self.progress.emit,self.isInterruptionRequested))
        except Exception as exc: self.failed.emit(str(exc))

class MainWindow(WorkspaceShellMixin,SessionMixin,ReviewWorkspaceMixin,QuotationWorkspaceMixin,EngineeringWorkspaceMixin,QMainWindow):
    logout_requested=pyqtSignal()
    def __init__(self,data_root,*,auth=None,auth_session=None):
        super().__init__(); self.auth=auth; self.auth_session=auth_session; self._logging_out=False
        if self.auth: self.auth.require(self.auth_session)
        self.store=Store(data_root,actor=(lambda:self.auth.actor(self.auth_session)) if self.auth else None); self.learning=StrategyService(self.store)
        self.feedback=FeedbackService(self.store); self._last_gesture_id=None; self._pending_quote_capture=None
        self.doc=None; self.current_rid=None; self.current_region=None; self.worker=None; self.loading=False
        self.loaded_form=None; self.loaded_document_ids=set()
        self.setWindowTitle('DrawIQ '+__version__+'｜圖面與報價覆核工作台'); self.resize(1600,1000); self.setMinimumSize(1180,740)
        self.build(); self.theme()

    def button(self,text,fn,layout):
        b=QPushButton(text); b.clicked.connect(lambda:self.guard(fn)); layout.addWidget(b); return b

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'quotation_table'):
            QTimer.singleShot(0,self.ensure_workspace_selection_visible)

    def guard(self,fn):
        try:
            # A revoked session must still be able to return to the login screen.
            if self.auth and fn!=self.logout: self.auth.require(self.auth_session)
            return fn()
        except Exception as exc:
            self.status.setText(str(exc)); QMessageBox.warning(self,'無法完成操作',str(exc))

    def build(self):
        self.build_workspace()

    def requirements_tab(self):
        w=QWidget(); outer=QVBoxLayout(w); outer.setContentsMargins(8,8,8,8)
        body=QWidget(); layout=QVBoxLayout(body); layout.setContentsMargins(0,0,0,0)
        self.review_queue_widget=QWidget(); queue_layout=QVBoxLayout(self.review_queue_widget); queue_layout.setContentsMargins(0,0,0,0)
        self.build_review_queue(queue_layout); self.sidebar.layout().insertWidget(3,self.review_queue_widget,2)
        self.review_form_splitter=QSplitter(Qt.Orientation.Vertical)
        self.review_form_splitter.setChildrenCollapsible(False)
        self.build_quotation_overview(self.review_form_splitter)
        edit_tabs=QTabWidget(); layout.addWidget(edit_tabs,1)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); formw=QWidget(); form=QFormLayout(formw); scroll.setWidget(formw)
        self.fields={}
        for key,label in [('label','欄位名稱')]:
            e=QLineEdit(); self.fields[key]=e
        self.value=QPlainTextEdit(); self.value.setFixedHeight(44); form.addRow('辨識內容',self.value)
        self.unit=QLineEdit()
        self.operator=QComboBox()
        for label,key in [('等於','eq'),('大於等於','ge'),('小於等於','le'),('區間','range'),('文字','text')]: self.operator.addItem(label,key)
        self.value_state=QComboBox()
        for state in STATES: self.value_state.addItem(STATE_LABELS[state],state)
        unit_state=QWidget(); unit_state_layout=QHBoxLayout(unit_state); unit_state_layout.setContentsMargins(0,0,0,0)
        unit_state_layout.addWidget(QLabel('單位')); unit_state_layout.addWidget(self.unit,1)
        unit_state_layout.addWidget(QLabel('值狀態')); unit_state_layout.addWidget(self.value_state,1)
        form.addRow(unit_state)
        self.review_evidence=QPlainTextEdit(); self.review_evidence.setReadOnly(True); self.review_evidence.setMaximumHeight(90)
        form.addRow('原文／提醒',self.review_evidence)
        self.detail_toggle=QPushButton('詳細條件（對象／公差／工程屬性）'); self.detail_toggle.setCheckable(True); form.addRow(self.detail_toggle)
        self.detail_fields=QWidget(); detail=QFormLayout(self.detail_fields); detail.setContentsMargins(0,0,0,0)
        detail.addRow('欄位名稱',self.fields['label'])
        for key,label in [('key','工程屬性'),('subject','作用對象'),('scope','作用範圍'),('conditions','適用條件'),('upper_tolerance','上公差'),('lower_tolerance','下公差')]:
            e=QLineEdit(); self.fields[key]=e; detail.addRow(label,e)
        detail.addRow('比較',self.operator)
        self.critical=QCheckBox('此需求為放行必要條件'); detail.addRow('關鍵需求',self.critical)
        form.addRow(self.detail_fields); self.detail_fields.hide(); self.detail_toggle.toggled.connect(self.detail_fields.setVisible)
        edit_tabs.addTab(scroll,'欄位內容')
        links=QWidget(); ll=QVBoxLayout(links); self.links=QListWidget(); self.links.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); ll.addWidget(self.links)
        self.links.currentItemChanged.connect(self.link_cursor_changed)
        ll.addWidget(QLabel('勾選＝需求關聯；點選列＝定位框。按「儲存關聯」才寫入。'))
        self.button('儲存關聯',self.save_links,ll); self.button('編輯選取區域原文',self.edit_region_text,ll)
        self.depends=QLineEdit(); self.depends.setPlaceholderText('上游需求 IDs，以逗號分隔'); ll.addWidget(self.depends)
        self.button('儲存衍生依賴',self.save_dependencies,ll); edit_tabs.addTab(links,'框與關聯')
        self.candidates=QPlainTextEdit(); self.candidates.setReadOnly(True); edit_tabs.addTab(self.candidates,'原始候選')
        history=QWidget(); hl=QVBoxLayout(history)
        self.region_info=QLabel('請選框查看歷程'); self.region_info.setWordWrap(True); hl.addWidget(self.region_info)
        self.region_history=QListWidget(); self.region_history.setMaximumHeight(125); hl.addWidget(self.region_history)
        self.region_history.currentItemChanged.connect(self.region_history_selected)
        self.region_history_detail=QPlainTextEdit(); self.region_history_detail.setReadOnly(True); hl.addWidget(self.region_history_detail)
        row=QHBoxLayout(); self.button('編輯選框原文',self.edit_region_text,row); self.button('變更選框種類',self.edit_region_kind,row); hl.addLayout(row)
        self.button('查看本筆裁圖與辨識',self.view_capture_history,hl)
        self.button('將此筆歷程帶入學習',self.use_history_for_learning,hl)
        edit_tabs.addTab(history,'選框歷程'); self.edit_tabs=edit_tabs
        edit_tabs.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Ignored)
        edit_tabs.setMinimumHeight(155)
        body.setMinimumHeight(155)
        self.review_form_splitter.addWidget(body)
        self.review_form_splitter.setSizes([505,195])
        outer.addWidget(self.review_form_splitter,1); layout=outer
        self.review_feedback=QLabel('左側選來源，右側帶入報價；請對照中央原圖後確認。'); self.review_feedback.setWordWrap(True); self.review_feedback.setMaximumHeight(60); layout.addWidget(self.review_feedback)
        self.reason=QLineEdit(); self.reason.setPlaceholderText('覆核備註（選填；操作、前後內容與人員會自動記錄）'); layout.addWidget(self.reason)
        row=QHBoxLayout(); self.save_btn=self.review_button('只儲存',self.save_current,row)
        self.review_button('還原表單',self.reset_form,row)
        self.verify_btn=self.review_button('確認此項',self.verify_current,row)
        self.next_btn=self.review_button('確認並下一項  Ctrl+Enter',self.verify_next,row); self.next_btn.setObjectName('primary'); layout.addLayout(row)
        row=QHBoxLayout(); self.review_button('補新增項目',self.add_requirement,row)
        self.exclude_current_btn=self.review_button('排除此項',self.exclude_current_review,row)
        self.undo_exclude=self.review_button('復原排除',self.undo_last_exclusion,row); self.undo_exclude.setEnabled(False)
        layout.addLayout(row)
        decisions=QHBoxLayout(); self.review_button('核准文件',self.approve_current,decisions)
        self.release_btn=self.review_button('發布知識包',lambda:self.export_current(True),decisions)
        self.review_button('匯出草稿',lambda:self.export_current(False),decisions); layout.addLayout(decisions)
        self.value.textChanged.connect(self.preview_review_values)
        self.unit.textChanged.connect(self.preview_review_values)
        self.value_state.currentIndexChanged.connect(self.preview_review_values)
        for field in self.fields.values(): field.textChanged.connect(self.preview_review_values)
        self.operator.currentIndexChanged.connect(self.preview_review_values)
        self.critical.toggled.connect(self.preview_review_values)
        self.depends.textChanged.connect(self.preview_review_values)
        self.links.itemChanged.connect(self.preview_review_values)
        return w

    def inspection_tab(self):
        w=QWidget(); layout=QVBoxLayout(w)
        self.inspection=QPlainTextEdit(); self.inspection.setReadOnly(True); layout.addWidget(self.inspection,2)
        self.audit=QPlainTextEdit(); self.audit.setReadOnly(True); layout.addWidget(self.audit,1)
        row=QHBoxLayout(); self.button('核對稽核鏈',self.check_audit,row); self.button('量測 STEP',self.inspect_step,row); self.button('比對參考 STEP',self.compare_step,row); layout.addLayout(row)
        self.button('撤回目前發布版本',self.revoke_release,layout)
        self.button('退回文件（重新覆核）',self.reject_current,layout)
        return w

    def learning_tab(self):
        w=QWidget(); layout=QVBoxLayout(w)
        text=QLabel('選擇實際修正歷程；核准後建立案例。值／單位修正須經保留正反例測試才能啟用；框位、原文與關聯案例提供人工覆核提示。'); text.setWordWrap(True); layout.addWidget(text)
        self.correction_select=QComboBox(); self.correction_select.setMinimumContentsLength(18); self.correction_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); layout.addWidget(self.correction_select)
        self.context=QLineEdit(); self.context.setPlaceholderText('適用原文語境，例如 PCS/年'); layout.addWidget(self.context)
        self.learning_reason=QLineEdit(); self.learning_reason.setPlaceholderText('案例／策略決策理由（必填，與覆核理由同步）'); layout.addWidget(self.learning_reason)
        self.learning_reason.textChanged.connect(self.reason.setText); self.reason.textChanged.connect(self.learning_reason.setText)
        self.button('從選定歷程建立覆核案例／策略',self.create_strategy,layout)
        self.strategies=QListWidget(); self.strategies.setMaximumHeight(145); layout.addWidget(self.strategies)
        row=QHBoxLayout(); self.button('匯入保留案例測試',self.evaluate_strategy,row); self.button('啟用／恢復',lambda:self.strategy_status('active'),row); self.button('停用／回退',lambda:self.strategy_status('disabled'),row); layout.addLayout(row)
        self.strategy_info=QPlainTextEdit(); self.strategy_info.setReadOnly(True); self.strategy_info.setMaximumHeight(150); layout.addWidget(self.strategy_info)
        self.strategies.currentItemChanged.connect(self.strategy_selected)
        self.button('查看本圖策略建議',self.show_suggestions,layout)
        self.button('查看選定案例的來源原圖',self.open_strategy_source,layout)
        self.button('人工採用選定策略',self.apply_strategy,layout)
        layout.addWidget(QLabel('已發布知識搜尋'))
        row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('搜尋需求、材料、單位或原文'); row.addWidget(self.search); self.button('搜尋',self.search_knowledge,row); layout.addLayout(row)
        self.search_results=QListWidget(); self.search_results.itemDoubleClicked.connect(lambda item:self.guard(lambda:self.open_search_result(item))); layout.addWidget(self.search_results,1)
        return w

    def theme(self):
        self.setStyleSheet('''
        QMainWindow, QWidget {background:#f5f7f8;color:#203641;font-family:"Microsoft JhengHei UI";font-size:13px;}
        QLabel#brand {font-size:28px;font-weight:800;color:#136e75;}
        QLabel#reviewHeading {font-size:16px;font-weight:700;color:#183746;}
        QLabel#selectedField {font-weight:700;color:#183746;}
        QLabel#documentTitle {font-size:18px;font-weight:700;} QLabel#muted {color:#73858e;}
        QPushButton {background:white;border:1px solid #c8d6db;border-radius:5px;padding:7px 10px;}
        QPushButton:hover {background:#e2f0f1;} QPushButton:checked,QPushButton#primary {background:#137d82;color:white;border-color:#137d82;}
        QPushButton:disabled {color:#93a1a6;background:#eef1f3;}
        QLineEdit,QPlainTextEdit,QTextEdit,QComboBox {background:white;border:1px solid #ccd7dc;border-radius:4px;padding:5px;selection-background-color:#b8dfe2;selection-color:#15333b;}
        QListWidget,QTableWidget {background:white;border:1px solid #d6e0e3;border-radius:5px;alternate-background-color:#f2f7f8;}
        QListWidget::item {padding:9px 5px;border-bottom:1px solid #edf1f3;} QListWidget::item:selected {background:#d7ecee;color:#153f48;}
        QTableWidget::item:selected {background:#d7ecee;color:#153f48;} QHeaderView::section {background:#e9eff1;border:0;padding:6px;color:#46606b;}
        QTabWidget::pane {border:1px solid #d5dfe3;background:white;} QTabBar::tab {padding:9px 12px;background:#e9eff1;} QTabBar::tab:selected {background:white;color:#137d82;}
        QSplitter::handle {background:#dce6e8;width:3px;} QScrollArea {border:0;} QProgressBar {border:0;height:6px;background:#d7e6e8;} QProgressBar::chunk {background:#13848a;}
        ''')

    def need_doc(self):
        if not self.doc: raise ValueError('請先選擇文件')
        return self.doc

    def open_review_queue(self):
        self.need_doc()
        self.workspace_tabs.setCurrentWidget(self.review_workspace); self.panel.setCurrentIndex(0)
        self.status.setText('集中覆核與逐項修正已整合；點列修改，勾選後批次確認。')

    def current_requirement(self):
        self.need_doc()
        r=next((r for r in self.doc['requirements'] if r['id']==self.current_rid and not r['deleted']),None)
        if not r: raise ValueError('請先選擇需求')
        return r

    def reason_text(self, default=None):
        text=self.reason.text().strip()
        if not text and default: return default
        if not text: raise ValueError('請填寫修改／驗證理由')
        return text

    def load_documents(self, select_id=None, document_ids=None):
        all_docs=self.store.list_documents()
        if document_ids is not None: self.loaded_document_ids=set(document_ids)
        elif select_id: self.loaded_document_ids.add(select_id)
        else: self.loaded_document_ids={d['id'] for d in all_docs}
        docs=sorted([d for d in all_docs if d['id'] in self.loaded_document_ids],key=lambda d:(d['tenant']=='design-example',d['name']))
        self.content_stack.setCurrentWidget(self.main_splitter if docs else self.home)
        self.documents.blockSignals(True); self.documents.clear()
        for doc in docs:
            count=sum(not r['deleted'] for r in doc['requirements'])
            item=QListWidgetItem(f'{doc["name"]}\n{STATUS_LABELS.get(doc["status"],doc["status"])} · {count} 筆原始需求 · v{doc["version"]}')
            item.setData(Qt.ItemDataRole.UserRole,doc['id']); self.documents.addItem(item)
            if doc['id']==select_id: self.documents.setCurrentItem(item)
        self.documents.blockSignals(False); self.doc_count.setText(f'{len(docs)} 份文件')
        self.filter_documents(); self.load_strategies()
        if select_id:
            new_document=self.doc is None or self.doc['id']!=select_id
            self.doc=self.store.get(select_id); self.refresh(new_document=new_document)
            if self.documents.currentItem(): self.documents.scrollToItem(self.documents.currentItem())
        elif self.documents.count():
            preferred=next((i for i,d in enumerate(docs) if d['name']=='Drawing_006.pdf' and d['tenant']=='local'),0)
            self.documents.setCurrentRow(preferred)

    def filter_documents(self):
        text=self.doc_filter.text().casefold()
        for i in range(self.documents.count()):
            item=self.documents.item(i); item.setHidden(text not in item.text().casefold())

    def document_selected(self,item,previous=None):
        if not item: return
        if self.form_dirty() or self.business_page.is_dirty():
            self.documents.blockSignals(True); self.documents.setCurrentItem(previous); self.documents.blockSignals(False)
            self.status.setText('有未儲存的核對內容或業務草稿；請先儲存，或還原表單'); return
        self.loaded_form=None
        self.doc=self.store.get(item.data(Qt.ItemDataRole.UserRole)); self.current_rid=None; self.current_region=None; self.refresh(new_document=True)

    def refresh(self,new_document=False):
        if not self.doc: return
        if new_document:
            self.canvas.set_mode('select'); self._pending_quote_capture=None; self._last_gesture_id=None
            self.doc=self.store.ensure_engineering_catalogue(self.doc['id'],self.doc['version'])
        self.loaded_form=None
        self.loading=True; page=self.page_select.currentData()
        self.doc_title.setText(self.doc['name'])
        from .review_queue import queue_stats
        queue=queue_stats(self.doc)
        self.doc_meta.setText(f'{STATUS_LABELS.get(self.doc["status"])} · {queue["groups"]} 組／{queue["pending"]} 項待覆核 · v{self.doc["version"]} · {len(self.doc["pages"])} 頁')
        self.page_select.blockSignals(True); self.page_select.clear()
        for p in self.doc['pages']: self.page_select.addItem(f'第 {p["number"]} 頁',p['number'])
        idx=self.page_select.findData(page); self.page_select.setCurrentIndex(idx if idx>=0 and not new_document else 0); self.page_select.blockSignals(False)
        reqs=self.rebuild_review_queue(new_document)
        self.refresh_quotation_overview()
        selected=next((i for i,r in enumerate(reqs) if r['id']==self.current_rid),None)
        if selected is None: selected=next((i for i in range(len(reqs)) if not self.requirements.isRowHidden(i)),None)
        self.loading=False
        self.show_page(keep=not new_document)
        if selected is not None: self.requirements.selectRow(selected); self.requirement_selected()
        else: self.clear_review_editor()
        issues=checks(self.doc)
        page_text='\n\n'.join(f'── 第 {p["number"]} 頁 / {p["processing"]} ──\n{p.get("ocr_text","")}' for p in self.doc['pages'])
        issue_text='\n'.join(f'[{i["severity"]}] {i["message"]} ({i["requirement_id"] or "document"})' for i in issues)
        self.inspection.setPlainText('工程檢查\n'+(issue_text or '沒有自動檢查錯誤；仍須人工核對完整性')+'\n\n處理警告\n'+'\n'.join(self.doc['warnings'])+'\n\n'+page_text+'\n\n工具紀錄\n'+json.dumps(self.doc['tool_runs'],ensure_ascii=False,indent=2))
        self.audit.setPlainText('\n\n'.join(f'v{e["version"]} | {e["at"]} | {e["actor"]}\n{e["action"]}：{e["reason"]}\n{json.dumps(e["detail"],ensure_ascii=False)}' for e in self.store.history(self.doc['id'])))
        self.release_btn.setEnabled(self.doc['status'] in ('approved','released'))
        for i in range(self.documents.count()):
            item=self.documents.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==self.doc['id']:
                item.setText(f'{self.doc["name"]}\n{STATUS_LABELS.get(self.doc["status"],self.doc["status"])} · {queue["groups"]} 組／{queue["pending"]} 筆待覆核 · v{self.doc["version"]}')
        self.summary_page.refresh(self.doc); self.business_page.refresh(self.doc)
        self.simple_page.refresh(self.doc)
        # Do not rebuild the graph and inspect the corpus index on every confirmation.
        if self.workspace_tabs.currentWidget() is self.corpus_page: self.corpus_page.refresh(self.doc)

    def requirement_selected(self):
        if self.loading or not self.doc: return
        row=self.requirements.currentRow()
        if row<0 or not self.requirements.item(row,0): return
        rid=self.requirements.item(row,0).data(Qt.ItemDataRole.UserRole)
        if self.form_dirty():
            self.requirements.blockSignals(True)
            for i in range(self.requirements.rowCount()):
                if self.requirements.item(i,0).data(Qt.ItemDataRole.UserRole)==self.current_rid: self.requirements.selectRow(i); break
            self.requirements.blockSignals(False); self.status.setText('請先按「確認並下一項」、「只儲存」或「還原表單」'); self.review_feedback.setText(self.status.text()); return
        changed=rid!=self.current_rid; self.current_rid=rid
        r=self.current_requirement()
        self.loaded_form=None
        for key,w in self.fields.items(): w.setText(str(r.get(key,'')))
        self.value.setPlainText(r['value']); self.unit.setText(r['unit']); self.operator.setCurrentIndex(self.operator.findData(r['operator']))
        self.value_state.setCurrentIndex(self.value_state.findData(r['state'])); self.critical.setChecked(r['critical'])
        self.depends.setText(','.join(r.get('dependencies',[])))
        self.candidates.setPlainText(json.dumps({'id':r['id'],'origin':r['origin'],'support_verified':r['support_verified'],'candidates':r['candidates']},ensure_ascii=False,indent=2))
        self.links.blockSignals(True); self.links.clear()
        for e in self.doc['regions']:
            item=QListWidgetItem(f'P{e["page"]} · {"特徵" if e["kind"]=="feature" else "證據"} · {e["text"][:100]}')
            item.setData(Qt.ItemDataRole.UserRole,e['id']); item.setToolTip(e['id']); self.links.addItem(item)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if e['id'] in r['evidence_ids']+r['feature_ids'] else Qt.CheckState.Unchecked)
        self.links.blockSignals(False)
        self.loaded_form=self.form_values()
        linked=[e for e in self.doc['regions'] if e['id'] in r['evidence_ids']+r['feature_ids']]
        if changed: self.current_region=linked[0]['id'] if linked else None
        if not getattr(self,'from_canvas',False):
            if linked:
                chosen=next((e for e in self.doc['regions'] if e['id']==self.current_region),linked[0])
                pnum=chosen['page']; idx=self.page_select.findData(pnum)
                self.page_select.blockSignals(True); old=self.page_select.currentIndex(); self.page_select.setCurrentIndex(idx); self.page_select.blockSignals(False)
                self.show_page(keep=old==idx)
            else: self.show_page(keep=True)
        self.refresh_correction_choices(); self.update_review_evidence()
        self.sync_quotation_selection()
        if changed and self.auto_focus.isChecked(): self.focus_review_evidence()
        if changed and not self.loading:
            self.feedback.record(self.doc,'candidate_focus',rid=rid,field=self._quotation_destination,
                                 detail={'origin':'ui_focus_navigation'})

    def show_page(self,keep=False):
        if not self.doc or self.page_select.currentData() is None: return
        p=next(p for p in self.doc['pages'] if p['number']==self.page_select.currentData())
        r=next((r for r in self.doc['requirements'] if r['id']==self.current_rid),None)
        highlight=(r['evidence_ids']+r['feature_ids']) if r else []
        if self.current_region and self.current_region not in highlight: highlight=highlight+[self.current_region]
        self.canvas.selected_region=self.current_region
        from .engineering import classify_text
        annotated=[]
        for e in self.doc['regions']:
            category,label=classify_text(e['text'])
            annotated.append({**e,'engineering_category':category,'engineering_label':label})
        self.canvas.show_page(p,annotated,highlight,self.show_boxes.isChecked(),keep)
        self.region_select.blockSignals(True); self.region_select.clear(); self.region_select.addItem('本頁選框（重疊框可從這裡選擇）',None)
        for e in self.doc['regions']:
            if e['page']==p['number']: self.region_select.addItem(f'{e["id"][-6:]} · {"特徵" if e["kind"]=="feature" else "證據"} · {e["text"][:65]}',e['id'])
        self.region_select.setCurrentIndex(max(0,self.region_select.findData(self.current_region))); self.region_select.blockSignals(False)
        self.refresh_region_history()

    def page_changed(self):
        if self.doc:
            e=next((e for e in self.doc['regions'] if e['id']==self.current_region),None)
            if e and e['page']!=self.page_select.currentData(): self.current_region=None
        self.canvas.set_mode('select'); self.show_page()

    def region_clicked(self,eid):
        if not self.doc or not any(e['id']==eid for e in self.doc['regions']): return
        self.current_region=eid
        matches=[r['id'] for r in self.doc['requirements'] if not r['deleted'] and eid in r['evidence_ids']+r['feature_ids']]
        self.canvas.select_region(eid)
        self.region_select.blockSignals(True); self.region_select.setCurrentIndex(self.region_select.findData(eid)); self.region_select.blockSignals(False)
        self.links.blockSignals(True)
        for i in range(self.links.count()):
            if self.links.item(i).data(Qt.ItemDataRole.UserRole)==eid: self.links.setCurrentRow(i); break
        self.links.blockSignals(False); self.refresh_region_history()
        self.status.setText(f'已選框 {eid} · 關聯 {len(matches)} 項需求；選框不改變需求關聯')

    def checked_region_ids(self):
        return [self.links.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.links.count()) if self.links.item(i).checkState()==Qt.CheckState.Checked]

    def change_canvas_mode(self):
        mode=self.interaction_mode.currentData()
        try:
            if mode!='select': self.require_saved_form(); self.need_doc()
            self.canvas.set_mode(mode)
        except Exception:
            self.canvas.set_mode('select'); raise

    def sync_canvas_mode(self,mode):
        if mode=='select': self._pending_quote_capture=None
        self.interaction_mode.blockSignals(True); self.interaction_mode.setCurrentIndex(self.interaction_mode.findData(mode)); self.interaction_mode.blockSignals(False)

    def region_list_selected(self):
        eid=self.region_select.currentData()
        if eid: self.region_clicked(eid); self.show_page(keep=True)

    def link_cursor_changed(self,item,previous=None):
        if not item or not self.doc: return
        eid=item.data(Qt.ItemDataRole.UserRole)
        e=next((e for e in self.doc['regions'] if e['id']==eid),None)
        if not e: return
        old=self.page_select.currentData(); self.current_region=eid
        self.page_select.blockSignals(True); self.page_select.setCurrentIndex(self.page_select.findData(e['page'])); self.page_select.blockSignals(False)
        self.region_clicked(eid); self.show_page(keep=old==e['page'])

    def refresh_region_history(self):
        if not hasattr(self,'region_history'): return
        self.region_history.clear(); self.region_history_detail.clear()
        e=next((e for e in self.doc['regions'] if e['id']==self.current_region),None) if self.doc else None
        if not e: self.region_info.setText('請點框或使用本頁選框清單'); return
        linked=[r['label'] for r in self.doc['requirements'] if not r['deleted'] and e['id'] in r['evidence_ids']+r['feature_ids']]
        self.region_info.setText(f'P{e["page"]} · {e["id"]}\n座標：{e["box"]}\n關聯需求：'+('、'.join(linked) or '尚未關聯'))
        for event in self.store.correction_history(self.doc['id'],region_id=e['id']):
            item=QListWidgetItem(f'v{event["version"]} · {CORRECTION_ACTIONS.get(event["action"],event["action"])} · {event["reason"]}')
            item.setData(Qt.ItemDataRole.UserRole,event); self.region_history.addItem(item)
        if self.region_history.count(): self.region_history.setCurrentRow(0)
        else: self.region_history_detail.setPlainText('此框來自匯入／分析，尚無人工修正紀錄。')

    def region_history_selected(self,item,previous=None):
        if not item: return
        event=item.data(Qt.ItemDataRole.UserRole); before=event['detail'].get('before') or {}; after=event['detail'].get('after') or {}
        lines=[f'版本：v{event["version"]}　操作人：{event["actor"]}',f'時間：{event["at"]}',f'理由：{event["reason"]}','']
        for k in event['changed_fields']:
            if k in ('id','created_at','source','capture'): continue
            lines.extend([CORRECTION_FIELDS.get(k,k)+'：','  修正前：'+json.dumps(before.get(k),ensure_ascii=False),'  修正後：'+json.dumps(after.get(k),ensure_ascii=False),''])
        capture=event['detail'].get('capture')
        if capture:
            result=capture['result']
            lines.extend(['框選辨識：'+result['status']+f" · {result.get('elapsed_seconds',0):.2f} 秒",
                '原始 OCR：'+result['text'],'人工保存原文：'+event['detail'].get('manual_text',''),
                '裁圖：'+str(self.store.root/'captures'/capture['id']/'crop.png')])
            req_before=event['detail'].get('requirement_before') or {};req_after=event['detail'].get('requirement_after') or {}
            lines.extend(['欄位修正前：'+req_before.get('value','（無）'),'欄位修正後：'+req_after.get('value','（無）')])
        lines.append('關聯需求：'+('、'.join(event['related_requirement_ids']) or '無'))
        self.region_history_detail.setPlainText('\n'.join(lines))

    def view_capture_history(self):
        item=self.region_history.currentItem()
        if not item:raise ValueError('請先選擇一筆框選歷程')
        show_capture_record(self,self.store.root,item.data(Qt.ItemDataRole.UserRole))

    def refresh_correction_choices(self):
        previous=self.correction_select.currentData(); self.correction_select.clear()
        if self.doc and self.current_rid:
            for e in self.store.correction_history(self.doc['id'],rid=self.current_rid):
                fields='、'.join(CORRECTION_FIELDS.get(k,k) for k in e['changed_fields'] if k in CORRECTION_FIELDS)
                self.correction_select.addItem(f'v{e["version"]} · {fields} · {e["reason"]}',e['id'])
        idx=self.correction_select.findData(previous)
        if idx>=0: self.correction_select.setCurrentIndex(idx)

    def use_history_for_learning(self):
        self.require_saved_form(); item=self.region_history.currentItem()
        if not item: raise ValueError('請先選擇一筆框選修正歷程')
        event=item.data(Qt.ItemDataRole.UserRole)
        idx=self.correction_select.findData(event['id'])
        if idx<0: raise ValueError('此歷程未關聯目前需求；請先選擇該框對應的需求')
        self.correction_select.setCurrentIndex(idx); self.panel.setCurrentIndex(2)
        self.status.setText('已帶入指定歷程。填寫原文語境與理由；文件核准後可建立案例。')

    def new_region(self,box):
        self.require_saved_form()
        self.need_doc()
        if self._pending_quote_capture:
            field=next(f for f in self.quotation_data if f['key']==self._pending_quote_capture)
            if field['source']:
                self.reveal_review(field['source']['id']); self.select_review_id(field['source']['id'])
            self._pending_quote_capture=None
        self.capture_region(self.page_select.currentData(),box,kind=self.region_kind.currentData(),
            reason=self.reason_text('人工新增原圖證據／特徵框'),rotation=self.canvas.rotation)

    def capture_region(self,page,box,*,kind='evidence',replace_id=None,reason='',rotation=0):
        self.require_saved_form()
        if self.worker: raise ValueError('請等候目前辨識工作完成')
        try:
            dialog=CaptureDialog(self.store,self.doc,page,box,rid=self.current_rid,kind=kind,
                replace_id=replace_id,rotation=rotation,reason=reason,parent=self,
                interaction_id=self._last_gesture_id,quotation_field=self._quotation_destination)
            if dialog.exec():
                self.doc=dialog.saved_document; self.current_region=dialog.saved_region_id
                self.current_rid=dialog.saved_requirement_id or self.current_rid
                self.feedback.record(self.doc,'capture_accept',rid=self.current_rid,field=self._quotation_destination,
                    detail={'capture_id':dialog.request['id'],'gesture_id':self._last_gesture_id,
                            'page':page,'box':box,'region_id':self.current_region})
                self.refresh(); self.region_clicked(self.current_region); self.edit_tabs.setCurrentIndex(3)
                self.status.setText('裁圖、辨識結果與修正歷程已保存。請核對後確認此欄位。')
            else:
                self.feedback.record(self.doc,'capture_cancel',rid=self.current_rid,field=self._quotation_destination,
                    detail={'capture_id':dialog.request['id'],'gesture_id':self._last_gesture_id,'page':page,'box':box})
        finally:
            self.canvas.set_mode('select'); self.show_page(keep=True)

    def move_region(self,eid,box):
        try:
            self.require_saved_form()
            e=next(e for e in self.doc['regions'] if e['id']==eid)
            self.capture_region(e['page'],box,kind=e['kind'],replace_id=eid,
                reason=self.reason_text('人工修正原圖框位'),rotation=self.canvas.rotation)
        except Exception:
            self.show_page(keep=True); raise

    def save_current(self):
        if self.business_page.is_dirty(): raise ValueError('請先儲存或還原業務草稿')
        r=self.current_requirement(); patch={key:w.text().strip() for key,w in self.fields.items()}
        patch.update(value=self.value.toPlainText().strip(),unit=self.unit.text().strip(),operator=self.operator.currentData(),state=self.value_state.currentData(),critical=self.critical.isChecked())
        ids=self.checked_region_ids()
        patch.update({key:[e['id'] for e in self.doc['regions'] if e['id'] in ids and e['kind']==kind] for key,kind in [('evidence_ids','evidence'),('feature_ids','feature')]})
        patch['dependencies']=[x.strip() for x in self.depends.text().split(',') if x.strip()]
        version=self.doc['version']; self.doc=self.store.edit_requirement(self.doc['id'],version,r['id'],patch,self.reason_text('人工對照原圖修正欄位或關聯'))
        if self.doc['version']!=version:
            self.feedback.record(self.doc,'edit_candidate',rid=r['id'],field=self._quotation_destination,detail={'before_version':version,'patch':patch})
        self.refresh(); self.status.setText('沒有內容變更，保留原覆核狀態' if self.doc['version']==version else '修改已保存；此項與受影響的衍生需求需重新確認')

    def save_links(self):
        self.save_current()

    def save_dependencies(self):
        self.save_current()

    def edit_region_text(self):
        self.require_saved_form()
        if not self.current_region: raise ValueError('請選擇一個區域')
        e=clone(next(e for e in self.doc['regions'] if e['id']==self.current_region))
        text,ok=QInputDialog.getMultiLineText(self,'修改區域原文','原文／特徵名稱',e['text'])
        if ok:
            e['text']=text; self.doc=self.store.save_region(self.doc['id'],self.doc['version'],e,None,self.reason_text('人工修正選框原文'),replace_id=e['id']); self.refresh()

    def edit_region_kind(self):
        self.require_saved_form(); self.need_doc(); reason=self.reason_text('人工變更選框種類與關聯')
        if not self.current_region: raise ValueError('請先選框')
        e=clone(next(e for e in self.doc['regions'] if e['id']==self.current_region))
        labels=['文字證據','作用特徵']; label,ok=QInputDialog.getItem(self,'變更選框種類','所有關聯需求會一併更新並需要重新覆核',labels,0 if e['kind']=='evidence' else 1,False)
        if ok:
            e['kind']='evidence' if label==labels[0] else 'feature'
            self.doc=self.store.save_region(self.doc['id'],self.doc['version'],e,None,reason,replace_id=e['id']); self.refresh()

    def add_requirement(self):
        self.require_saved_form()
        self.need_doc(); key,ok=QInputDialog.getText(self,'新增需求','屬性名稱（例如 coating.minimum_threads）')
        if ok and key.strip():
            r=requirement(key.strip(),origin='manual'); self.doc=self.store.add_requirement(self.doc['id'],self.doc['version'],r,'人工新增需求'); self.current_rid=r['id']; self.refresh()

    def delete_current(self):
        self.exclude_current_review()

    def form_values(self):
        return {**{k:w.text().strip() for k,w in self.fields.items()},'value':self.value.toPlainText().strip(),'unit':self.unit.text().strip(),
            'operator':self.operator.currentData(),'state':self.value_state.currentData(),'critical':self.critical.isChecked(),
            'links':sorted(self.checked_region_ids()),'dependencies':self.depends.text().strip()}

    def form_dirty(self):
        return (hasattr(self,"simple_page") and self.simple_page.is_dirty()) or (self.loaded_form is not None and self.form_values()!=self.loaded_form)

    def reset_form(self):
        self.loaded_form=None; self.refresh(); self.status.setText('表單已還原至最後儲存版本')

    def require_saved_form(self):
        if self.simple_page.is_dirty(): raise ValueError('請先核准報價標籤或還原修改')
        if self.form_dirty(): raise ValueError('表單／關聯有尚未儲存的修改，請先儲存或重設表單')
        if self.business_page.is_dirty(): raise ValueError('業務草稿有尚未儲存的修改，請先儲存業務草稿或還原內容')

    def verify_current(self):
        r=self.current_requirement()
        # Never verify unsaved edits that differ from persisted content.
        self.require_saved_form()
        self.doc=self.store.verify_requirement(self.doc['id'],self.doc['version'],r['id'],self.reason_text('人工已核對值、單位、對象、條件與原圖證據')); self.refresh(); self.status.setText('此項已確認；Ctrl＋Enter 可繼續下一待覆核項')

    def approve_current(self):
        self.require_saved_form()
        d=self.need_doc(); self.doc=self.store.approve(d['id'],d['version'],self.reason_text('人工確認文件完整性並核准')); self.refresh(); self.status.setText('文件已核准，可發布知識包')

    def verify_next(self):
        group=self.review_group_by_rid.get(self.current_rid,{}).get('id')
        if self.form_dirty(): self.save_current()
        self.verify_current()
        self.select_next_review(group)

    def reject_current(self):
        self.require_saved_form()
        d=self.need_doc(); self.doc=self.store.reject(d['id'],d['version'],self.reason_text()); self.refresh()

    def export_current(self,official):
        self.require_saved_form()
        d=self.need_doc(); release_id=None
        if official:
            self.doc,payload=self.store.release(d['id'],d['version'],self.reason_text('人工發布已核准知識包')); release_id=payload['release_id']; self.refresh()
        path=export_bundle(self.store,d['id'],release_id=release_id)
        self.status.setText(f'已建立 {path}'); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def revoke_release(self):
        self.require_saved_form()
        d=self.need_doc()
        if not d.get('current_release'): raise ValueError('目前沒有已發布版本')
        self.store.revoke(d['current_release'],self.reason_text()); self.doc=self.store.get(d['id']); self.refresh()

    def start_job(self,fn,*,on_result=None,allow_label_edits=False):
        if allow_label_edits:
            self.simple_page.require_saved_advanced_form()
        else:
            self.require_saved_form()
        if self.worker: raise ValueError('已有工作執行中')
        self.worker=Worker(fn); self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(on_result or self.job_done); self.worker.failed.connect(self.job_failed); self.worker.finished.connect(self.job_finished)
        self.busy.show(); self.cancel.setEnabled(True); self.action_bar.setEnabled(False); self.panel.setEnabled(False); self.sidebar.setEnabled(False); self.center.setEnabled(False)
        self.workspace_tabs.setEnabled(False); self.easy_bar.setEnabled(False)
        self.worker.start()

    def job_done(self,result):
        selected=self.doc['id'] if self.doc else None
        if isinstance(result,dict):
            selected=result.get('document_id') or (result.get('document_ids') or [selected])[0]
            if result.get('errors'): self.status.setText('部分失敗：'+'；'.join(result['errors']))
            elif result.get('cancelled'): self.status.setText('工作已取消；已完成部分保留，未開始的工作未寫入。')
            else: self.status.setText('工作完成，內容已整理。可查看圖面摘要、語料與知識或輸出資料夾。')
        self.load_documents(selected)
        if isinstance(result,dict):
            if 'semantic_hits' in result:
                self.corpus_page.set_results(result); self.workspace_tabs.setCurrentWidget(self.corpus_page); self.status.setText(f'找到 {len(result["semantic_hits"])} 筆相關來源；雙擊可回原圖')
            elif 'corpus_bundle' in result:
                self.workspace_tabs.setCurrentWidget(self.corpus_page); self.status.setText('語料庫已輸出：'+result['corpus_bundle'])
            elif 'semantic_index' in result:
                self.workspace_tabs.setCurrentWidget(self.corpus_page); self.status.setText('本機語意索引已建立，可以用自然語言搜尋')
            elif result.get('show_review'):
                if self.advanced_mode.isChecked():self.open_review_queue()
                else:
                    self.workspace_tabs.setCurrentWidget(self.simple_page)
                    if not result.get('errors') and not result.get('cancelled'):
                        hint = '已載入既有結果。更換方法後可按「重新辨識」。' if result.get('reused') else '已帶入報價欄位。要修改時請選欄位，再按「框選並辨識」。'
                        self.status.setText(hint)
            elif result.get('show_summary'): self.workspace_tabs.setCurrentIndex(0)
        if isinstance(result,dict) and 'geometry' in result:
            folder=self.store.root/'geometry_reports'; folder.mkdir(parents=True,exist_ok=True)
            path=folder/(uid('geometry')+'.json'); content=json.dumps(result['geometry'],ensure_ascii=False,indent=2)
            path.write_text(content,encoding='utf-8'); self.inspection.setPlainText('STEP 量測報告\n'+str(path)+'\n\n'+content)
            self.panel.setCurrentIndex(1); self.status.setText('STEP 量測完成：'+str(path))

    def job_failed(self,error):
        self.status.setText('工作失敗：'+error); QMessageBox.warning(self,'工作失敗',error)

    def job_finished(self):
        self.worker.deleteLater(); self.worker=None; self.busy.hide(); self.cancel.setEnabled(False)
        self.action_bar.setEnabled(True); self.panel.setEnabled(True); self.sidebar.setEnabled(True); self.center.setEnabled(True)
        self.workspace_tabs.setEnabled(True); self.easy_bar.setEnabled(True)

    def cancel_job(self):
        if self.worker: self.worker.requestInterruption(); self.status.setText('已要求取消；正在進行的 OCR／模型呼叫完成或逾時後停止')

    def import_paths(self,paths):
        def work(progress,cancelled):
            ids=[]; errors=[]
            for path in paths:
                if cancelled(): break
                try:
                    progress('讀取：'+str(path)); ids.append(self.store.add(prepare_document(path,self.store.root))['id'])
                except Exception as exc: errors.append(f'{Path(path).name}：{exc}')
            return {'document_ids':ids,'errors':errors}
        self.start_job(work)

    def import_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'新增工程文件',str(REPO/'engineering_drawings'),'工程文件 (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp)')
        if paths: self.import_paths(paths)

    def import_folder(self):
        path=QFileDialog.getExistingDirectory(self,'新增資料夾',str(REPO/'engineering_drawings'))
        if path: self.import_paths([p for p in Path(path).rglob('*') if p.suffix.lower() in SUPPORTED])

    def import_history(self):
        path,_=QFileDialog.getOpenFileName(self,'匯入舊版 analysis.json',str(LEGACY/'outputs/runs/20260907_220007_502/analysis.json'),'解析結果 (analysis.json *.json)')
        if path: self.start_job(lambda progress,cancelled:import_legacy(self.store,path,progress,cancelled))

    def load_example(self):
        if not (REPO/'analysis/drawing_006_knowledge_example.json').is_file():
            examples=[d for d in self.store.list_documents() if d.get('tenant')=='design-example' or '設計示例' in d.get('name','')]
            if examples: self.load_documents(examples[0]['id']); return
            raise ValueError('此工作區沒有設計示例，請使用「匯入並讀圖」加入自己的 PDF／圖片')
        self.start_job(lambda progress,cancelled:{'document_id':import_example(self.store,REPO/'analysis/drawing_006_knowledge_example.json')['id']})

    def workspace_changed(self,index):
        self.tools_scroll.setVisible(self.more_tools.isChecked())
        review=self.workspace_tabs.currentWidget() is self.review_workspace
        self.sidebar.setVisible(self.workspace_tabs.currentWidget() is not self.simple_page)
        self.review_queue_widget.setVisible(review); self.sidebar_hint.setVisible(not review)
        self.documents.setMaximumHeight(120 if review else 16777215)
        self.sidebar.setMaximumWidth(315 if review else 270)
        self.main_splitter.setSizes([300 if review else 210,max(700,self.width()-340)])
        QTimer.singleShot(0,lambda:self.documents.scrollToItem(self.documents.currentItem()) if self.documents.currentItem() else None)
        if self.workspace_tabs.currentWidget() is self.corpus_page: self.corpus_page.refresh(self.doc)

    def quick_import(self):
        paths,_=QFileDialog.getOpenFileNames(self,'選擇圖面，讀圖後顯示全部結果與報價覆核工作台',str(REPO/'engineering_drawings'),'工程圖 (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp)')
        if not paths: return
        use=self.use_vlm.isChecked(); model=self.model.text().strip()
        ocr_backend=self.ocr_backend.currentData()
        vlm_budget=120 if self.full_vlm.isChecked() else None
        def work(progress,cancelled):
            ids=[]; errors=[]; reused=False
            for path in paths:
                if cancelled(): break
                try:
                    progress('匯入並讀圖：'+Path(path).name); d=self.store.add(prepare_document(path,self.store.root))
                    if not d['requirements']:
                        output=analyze(d,self.store.root,use_vlm=use,model=model,progress=progress,cancelled=cancelled,vlm_budget_seconds=vlm_budget,ocr_backend=ocr_backend,max_vlm_calls=len(d['pages']))
                        d=apply_analysis(self.store,d['id'],d['version'],output,'匯入後自動初步識圖')
                    else:
                        reused=True
                    ids.append(d['id'])
                except Exception as exc: errors.append(Path(path).name+'：'+str(exc))
            return {'document_ids':ids,'errors':errors,'show_review':True,'reused':reused,'cancelled':cancelled()}
        self.start_job(work)

    def quick_analyze(self, *, current_page=None, force_ocr=False):
        d=clone(self.need_doc()); use=self.use_vlm.isChecked(); model=self.model.text().strip()
        ocr_backend=self.ocr_backend.currentData()
        vlm_budget=120 if self.full_vlm.isChecked() else None
        page_numbers=None
        if current_page is not None:
            if current_page not in {page['number'] for page in d['pages']}:
                raise ValueError('請先選擇要辨識的頁次')
            page_numbers=[current_page]
        def work(progress,cancelled):
            output=analyze(d,self.store.root,page_numbers=page_numbers,use_vlm=use,model=model,
                           progress=progress,cancelled=cancelled,vlm_budget_seconds=vlm_budget,
                           ocr_backend=ocr_backend,max_vlm_calls=len(page_numbers or d['pages']),force_ocr=force_ocr)
            updated=apply_analysis(self.store,d['id'],d['version'],output,'使用者重新讀圖並更新摘要')
            return {'document_id':updated['id'],'show_review':True,'cancelled':output.get('cancelled',False)}
        self.start_job(work)

    def navigate_fact(self,doc,fact=None):
        self.require_saved_form()
        self.current_rid=fact.get('requirement_id') if fact else None
        self.current_region=(fact['evidence_ids'][0] if fact and fact['evidence_ids'] else None)
        self.reveal_review(self.current_rid); self.load_documents(doc['id']); self.workspace_tabs.setCurrentWidget(self.review_workspace); self.panel.setCurrentIndex(0)
        if fact and fact['evidence_ids']:
            e=next((e for e in self.doc['regions'] if e['id']==fact['evidence_ids'][0]),None)
            if e:
                self.page_select.setCurrentIndex(self.page_select.findData(e['page'])); self.current_region=e['id']; self.show_page()
                if self.auto_focus.isChecked(): self.focus_review_evidence()
        self.status.setText('已定位到原圖。若是新精煉資訊，可先在摘要頁按「加入待覆核需求」再修正。')

    def apply_requirement_profile(self):
        self.require_saved_form(); d=self.need_doc()
        path,_=QFileDialog.getOpenFileName(self,'選擇企業／客戶需求範本',str(Path(__file__).resolve().parents[1]/'profiles'),'JSON (*.json)')
        if path:
            self.doc=apply_profile(self.store,d['id'],d['version'],load_profile(path),self.reason_text()); self.refresh()
            self.status.setText('已套用需求範本，缺項已加入待補清單；請依範本重新覆核')

    def start_analysis(self,selected_region):
        doc=clone(self.need_doc()); crop=None
        pnum=self.page_select.currentData()
        if selected_region:
            if not self.current_region: raise ValueError('請先在圖上點選一個區域')
            e=next(e for e in doc['regions'] if e['id']==self.current_region)
            return self.capture_region(e['page'],e['box'],kind=e['kind'],replace_id=e['id'],
                reason=self.reason_text('人工重新辨識選框'),rotation=self.read_rotation.currentData())
        use=self.use_vlm.isChecked(); model=self.model.text().strip(); rotation=self.read_rotation.currentData()
        ocr_backend=self.ocr_backend.currentData()
        vlm_budget=120 if self.full_vlm.isChecked() else None
        def work(progress,cancelled):
            output=analyze(doc,self.store.root,page_numbers=[pnum],crop=crop,rotation=rotation,use_vlm=use,model=model,progress=progress,cancelled=cancelled,vlm_budget_seconds=vlm_budget,ocr_backend=ocr_backend)
            updated=apply_analysis(self.store,doc['id'],doc['version'],output)
            return {'document_id':updated['id'],'candidates':len(output['requirements']),'cancelled':output.get('cancelled')}
        self.start_job(work)

    def start_refine(self):
        d=self.need_doc(); doc_id=d['id']; version=d['version']; use=self.use_vlm.isChecked(); model=self.model.text().strip()
        self.start_job(lambda progress,cancelled:refine(self.store,doc_id,version,use_vlm=use,model=model,progress=progress,cancelled=cancelled))

    def check_models(self):
        found=models(); names=[m['name'] for m in found]
        self.status.setText('本機模型：'+('、'.join(names) or '尚未安裝模型'))

    def backup(self):
        if self.auth: self.auth.require(self.auth_session,admin=True)
        path,_=QFileDialog.getSaveFileName(self,'備份完整工作區',str(self.store.root/'backups'/('workspace_'+uid('backup')+'.zip')),'ZIP (*.zip)')
        if path:
            self.start_job(lambda progress,cancelled:{'backup':str(backup_workspace(self.store,path))})

    def open_exports(self):
        path=self.store.root/'exports'; path.mkdir(exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def check_audit(self):
        ok=self.store.verify_audit(self.need_doc()['id']); self.status.setText('稽核鏈與版本快照一致' if ok else '稽核鏈驗證失敗')

    def load_strategies(self,selected_id=None):
        previous=self.strategies.currentItem(); selected_id=selected_id or (previous.data(Qt.ItemDataRole.UserRole) if previous else None)
        self.strategies.clear()
        labels={'reference':'人工覆核參考','candidate':'候選待測','shadow':'測試通過待啟用','active':'已啟用建議','disabled':'已停用'}
        for s in self.learning.list():
            item=QListWidgetItem(f'{s["name"]} · {labels.get(s["status"],s["status"])} · v{s["version"]}'); item.setData(Qt.ItemDataRole.UserRole,s['id']); self.strategies.addItem(item)
            if s['id']==selected_id: self.strategies.setCurrentItem(item)

    def selected_strategy(self):
        item=self.strategies.currentItem()
        if not item: raise ValueError('請選擇策略')
        return item.data(Qt.ItemDataRole.UserRole)

    def strategy_selected(self,item,previous=None):
        if item:
            s=next(x for x in self.learning.list() if x['id']==item.data(Qt.ItemDataRole.UserRole)); self.strategy_info.setPlainText(json.dumps(s,ensure_ascii=False,indent=2))

    def create_strategy(self):
        self.require_saved_form(); r=self.current_requirement()
        event_id=self.correction_select.currentData()
        if not event_id: raise ValueError('目前需求尚無可使用的人工修正歷程')
        s=self.learning.from_correction(self.doc['id'],r['id'],self.context.text(),self.reason_text(),event_id=event_id); self.load_strategies(s['id'])
        self.strategy_info.setPlainText(json.dumps(s,ensure_ascii=False,indent=2)); self.status.setText('已建立 '+('人工覆核案例 ' if s['action']=='review_evidence' else '待測試的候選策略 ')+s['id'])

    def evaluate_strategy(self):
        sid=self.selected_strategy(); path,_=QFileDialog.getOpenFileName(self,'選擇仲裁後的保留案例 JSON',str(REPO/'DrawIQ_Workbench'),'JSON (*.json)')
        if path:
            cases=json.loads(Path(path).read_text(encoding='utf-8')); result=self.learning.evaluate(sid,cases,self.reason_text()); self.load_strategies(); self.strategy_info.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))

    def strategy_status(self,status):
        s=next(x for x in self.learning.list() if x['id']==self.selected_strategy())
        if status=='active' and s.get('action')=='review_evidence': status='reference'
        result=self.learning.set_status(self.selected_strategy(),status,self.reason_text()); self.load_strategies(); self.strategy_info.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))

    def show_suggestions(self):
        doc=self.need_doc(); values=self.learning.suggest(doc,statuses=('active','shadow')); hints=self.learning.review_hints(doc)
        self.strategy_info.setPlainText(json.dumps({'值建議':values,'人工覆核提示':hints},ensure_ascii=False,indent=2) if values or hints else '目前沒有符合範圍的策略建議')

    def apply_strategy(self):
        self.require_saved_form()
        r=self.current_requirement(); self.doc=self.learning.apply(self.doc['id'],self.doc['version'],r['id'],self.selected_strategy(),self.reason_text()); self.refresh()

    def open_strategy_source(self):
        self.require_saved_form()
        s=next(x for x in self.learning.list() if x['id']==self.selected_strategy())
        doc=self.store.get(s['source_document_id']); r=next(r for r in doc['requirements'] if r['id']==s['source_requirement_id'])
        self.current_rid=r['id']; self.current_region=s.get('correction_case',{}).get('event',{}).get('detail',{}).get('region_id') or next(iter(r['evidence_ids']),None)
        self.reveal_review(self.current_rid); self.load_documents(doc['id']); self.workspace_tabs.setCurrentWidget(self.review_workspace); self.panel.setCurrentIndex(0); self.edit_tabs.setCurrentIndex(3)
        self.status.setText(f'已開啟來源目前版本 v{doc["version"]}；訓練案例採用的核准版本為 v{s["source_approved_version"]}，前後快照保存在案例內。')

    def search_knowledge(self):
        self.search_results.clear()
        for hit in self.store.search(self.search.text()):
            r=hit['requirement']; item=QListWidgetItem(f'{hit["document_name"]}\n{r["label"]}：{r["value"]} {r["unit"]}'); item.setData(Qt.ItemDataRole.UserRole,hit); self.search_results.addItem(item)

    def open_search_result(self,item):
        self.require_saved_form()
        hit=item.data(Qt.ItemDataRole.UserRole); self.current_rid=hit['requirement']['id']; self.reveal_review(self.current_rid); self.load_documents(hit['document_id']); self.open_review_queue()

    def inspect_step(self):
        path,_=QFileDialog.getOpenFileName(self,'選擇已知 STEP',str(REPO),'STEP (*.step *.stp)')
        if path:
            self.start_job(lambda progress,cancelled:{'geometry':measure_step(path)})

    def compare_step(self):
        paths,_=QFileDialog.getOpenFileNames(self,'依序選擇候選 STEP 與參考 STEP',str(REPO),'STEP (*.step *.stp)')
        if paths:
            if len(paths)!=2: raise ValueError('請選擇兩份 STEP，第一份為候選，第二份為參考')
            self.start_job(lambda progress,cancelled:{'geometry':compare_steps(paths[0],paths[1])})

    def open_account_settings(self):
        if not self.auth: raise ValueError('請由登入入口啟動工作台')
        from .auth_ui import AccountDialog
        AccountDialog(self).exec()

    def logout(self):
        self.require_saved_form()
        if self.worker: raise ValueError('請先等候目前工作結束後再登出')
        self._logging_out=True
        self.close()

    def closeEvent(self,event):
        if self.worker:
            self.cancel_job(); event.ignore(); self.status.setText('請等待目前工作安全停止後再關閉')
        elif self.simple_page.is_dirty(): event.ignore(); self.status.setText('請先核准報價標籤或還原修改，再關閉。')
        elif self.form_dirty() or self.business_page.is_dirty(): event.ignore(); self.status.setText('請先儲存核對內容或業務草稿，或還原表單後再關閉')
        else:
            if self.auth:self.auth.logout(self.auth_session)
            event.accept()
            if self._logging_out:self.logout_requested.emit()
            elif self.auth:QTimer.singleShot(0,QApplication.instance().quit)

def main(data_root):
    from .startup import stage
    app=QApplication.instance() or QApplication([]); app.setFont(QFont('Microsoft JhengHei UI',10))
    stage('qt.ready',platform=app.platformName(),screens=len(app.screens()))
    from .auth_ui import DesktopSession
    app.setQuitOnLastWindowClosed(False)
    stage('auth.initialize.start')
    controller=DesktopSession(app,data_root); QTimer.singleShot(0,controller.start)
    stage('auth.initialize.ready')
    return app.exec()
