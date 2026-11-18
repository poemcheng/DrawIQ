"""Engineering source finding, quotation review and inspectable methods."""
import json
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QPlainTextEdit,
                            QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView,
                            QAbstractItemView,QPushButton,QFileDialog,QListWidget,QListWidgetItem)
from .engineering import ranked_candidates
from .methods import report
from .domain import now

class EngineeringWorkspaceMixin:
    def record_canvas_gesture(self,event):
        if not self.doc: return
        event={**event,'page':self.page_select.currentData()}
        saved=self.feedback.record(self.doc,'canvas_'+event['action'],rid=self.current_rid,
                                   field=self._quotation_destination,detail=event)
        self._last_gesture_id=saved['id']

    def show_method_report(self):
        dialog=QDialog(self); dialog.setWindowTitle('辨識方法、模型、工程驗證與人工學習'); dialog.resize(1080,720)
        outer=QVBoxLayout(dialog); tabs=QTabWidget(); outer.addWidget(tabs,1)
        data=report(self.doc)
        methods=QPlainTextEdit(); methods.setReadOnly(True)
        methods.setPlainText('\n\n'.join(x['name']+'\n'+x['purpose'] for x in data['methods'])+
                             '\n\n'+data['coverage']+'\n'+data['neural_training']+
                             '\nShapeLab 現有索引：'+', '.join(data['shapelab']['weights']))
        tabs.addTab(methods,'方法與模型用途')
        runs=QTableWidget(0,6); runs.setHorizontalHeaderLabels(['頁','方法／模型','狀態','OCR 秒','VLM 秒','紀錄位置'])
        runs.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeMode.Stretch)
        runs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for run in data['tool_runs']:
            row=runs.rowCount(); runs.insertRow(row)
            values=(run.get('page',''),run.get('model') or run.get('ocr_engine') or run.get('type',''),
                    run.get('vlm_status',run.get('status','未知')),run.get('ocr_seconds',''),
                    run.get('vlm_seconds',run.get('elapsed_seconds','')),run.get('trace_dir',''))
            for col,value in enumerate(values):
                cell=QTableWidgetItem(str(value)); cell.setToolTip(str(value)); runs.setItem(row,col,cell)
        tabs.addTab(runs,'本文件實際執行')
        prefill=QPlainTextEdit(); prefill.setReadOnly(True)
        prefill.setPlainText('先帶入最適合候選，再對照原圖確認；排序分數不代表正確率。\n\n'+
            '\n\n'.join(f['label']+'：'+f['display_value']+'\n狀態：'+f['status']+
                ('\n首選原因：'+f['proposal_reason'] if f['proposed'] else '')+
                ('\n待核對：'+', '.join(f['proposal_issues']) if f['proposal_issues'] else '')
                for f in data.get('quotation_prefill',[])))
        tabs.addTab(prefill,'報價首選／待確認')
        skill=QPlainTextEdit(); skill.setReadOnly(True)
        skill.setPlainText('原文／結構化規格／來源與信心／缺值與衝突。分數為來源提供的分數，不代表人工確認。\n'+
            '無框位的資料保持未定位；未解決圖框才送視覺模型局部分析。\n\n'+
            json.dumps(data.get('quote_skill'),ensure_ascii=False,indent=2))
        tabs.addTab(skill,'Skill 解析／待覆核')
        harness=QPlainTextEdit(); harness.setReadOnly(True)
        inspection=data['harness']
        harness.setPlainText(('Harness Engineering '+inspection.get('policy_revision','')+'\n契約檢查：'+inspection.get('contract_status','未載入文件')+
            '\n工程核准條件已完成：'+str(inspection.get('review_ready',False))+
            '\n16 欄覆核完成：'+str(inspection.get('quotation_review_complete',False))+
            '\n16 欄皆有採用值：'+str(inspection.get('quotation_values_complete',False))+
            '\n規則通過不代表識字準確或缺項已補齊。\n\n')+
            '\n\n'.join(f['label']+'：'+f['status']+'\n'+f['explanation'] for f in inspection.get('fields',[]))+
            '\n\n規則與修正建議\n'+ '\n\n'.join(x['code']+' ['+x['severity']+'] '+x['message']+
                '\n'+x['remedy'] for x in inspection.get('findings',[])))
        tabs.addTab(harness,'工程規則／本文件驗證')
        from .harness_validation import latest_validation
        validation=latest_validation(Path(__file__).resolve().parents[1])
        version_report=QPlainTextEdit(); version_report.setReadOnly(True)
        previous=validation.get('report',{})
        summary={k:v for k,v in previous.items() if k!='source_fingerprint'}
        version_report.setPlainText(validation['message']+'\n'+
            '完整版本驗證：python tools/run_harness.py --suite full\n標註案例重播不執行 OCR／VLM；回歸測試可使用隔離合成圖，並非實圖識字準確率。\n\n'+
            json.dumps(summary,ensure_ascii=False,indent=2))
        tabs.addTab(version_report,'版本驗證紀錄')
        learning=QPlainTextEdit(); learning.setReadOnly(True)
        dataset=self.feedback.dataset(self.doc)
        learning.setPlainText(f'已確認欄位示範：{len(dataset["examples"])} 筆\n'+
            f'操作軌跡：{len(dataset["trajectories"])} 筆\n\n'+
            '已確認且仍有效的來源，用於跨圖面同租戶的候選排序。修正後失去確認的案例會立即退出。\n'+
            '框選、移框、來源選擇、套用／取消與覆核均保存；未確認操作不當作正確答案。\n'+
            '既有核准修正策略仍可在「學習／知識」建立、留出測試與啟用。\n\n'+
            json.dumps(dataset,ensure_ascii=False,indent=2))
        tabs.addTab(learning,'人工學習／軌跡')
        raw=QPlainTextEdit(); raw.setReadOnly(True); raw.setPlainText(json.dumps(data,ensure_ascii=False,indent=2))
        tabs.addTab(raw,'詳細紀錄')
        row=QHBoxLayout(); outer.addLayout(row)
        self.button('查詢／選擇本機視覺模型',self.choose_vision_models,row)
        self.button('匯出人工框選與欄位學習資料',self.export_feedback_dataset,row)
        self.button('匯出已確認欄位回歸案例',self.export_confirmed_case,row)
        self.button('匯出本文件驗證',self.export_harness_inspection,row)
        button=QPushButton('關閉'); button.clicked.connect(dialog.accept); row.addWidget(button)
        dialog.exec()

    def export_harness_inspection(self):
        self.need_doc(); self.require_saved_form()
        from .harness import inspect_document
        path,_=QFileDialog.getSaveFileName(self,'匯出本文件工程驗證',str(self.store.root/'本文件工程驗證.json'),'JSON (*.json)')
        if path:
            Path(path).write_text(json.dumps(inspect_document(self.doc),ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.setText('已匯出目前文件版本的規則判定與欄位來源原因')

    def export_confirmed_case(self):
        self.need_doc(); self.require_saved_form()
        from .harness_validation import confirmed_fixture
        fixture=confirmed_fixture(self.doc,self.quotation_key())
        path,_=QFileDialog.getSaveFileName(self,'匯出已確認欄位回歸案例',str(self.store.root/'人工確認案例.json'),'JSON (*.json)')
        if path:
            Path(path).write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.setText('已匯出欄位關聯與原文契約案例；可用 run_harness.py --cases 重播')

    def choose_vision_models(self):
        from .extraction import models
        installed=[m for m in models() if 'vision' in m.get('capabilities',[])]
        dialog=QDialog(self); dialog.setWindowTitle('本機視覺模型（最多四個）'); dialog.resize(600,420)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('各模型分別執行；完整模式每方法／每頁最多 120 秒，快速模式 30 秒。輸出仍須人工覆核。'))
        listing=QListWidget(); layout.addWidget(listing,1)
        selected={x.strip() for x in self.model.text().split(',')}
        for model in installed:
            item=QListWidgetItem(model['name']+' · '+model.get('details',{}).get('parameter_size',''))
            item.setData(Qt.ItemDataRole.UserRole,model['name']); item.setToolTip(model.get('digest',''))
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if model['name'] in selected else Qt.CheckState.Unchecked)
            listing.addItem(item)
        status=QLabel(); layout.addWidget(status)
        def accept():
            chosen=[listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                    if listing.item(i).checkState()==Qt.CheckState.Checked]
            if not 1<=len(chosen)<=4: status.setText('請選擇 1–4 個 vision 模型'); return
            self.model.setText(', '.join(chosen)); self.use_vlm.setChecked(True); dialog.accept()
        button=QPushButton('使用勾選模型'); button.clicked.connect(accept); layout.addWidget(button)
        dialog.exec()

    def export_feedback_dataset(self):
        path,_=QFileDialog.getSaveFileName(self,'匯出人工學習資料',str(self.store.root/'人工學習資料.json'),'JSON (*.json)')
        if path:
            Path(path).write_text(json.dumps(self.feedback.dataset(),ensure_ascii=False,indent=2),encoding='utf-8')
            self.status.setText('已匯出已確認示範、來源框與操作軌跡：'+path)

    def find_quotation_candidates(self):
        self.require_saved_form(); key=self.quotation_key()
        from .quotation_layout import QUOTE_FIELDS
        dialog=QDialog(self); dialog.setWindowTitle('找候選 → '+QUOTE_FIELDS[key]); dialog.resize(1120,700)
        layout=QVBoxLayout(dialog); layout.addWidget(QLabel('依欄位語意與已確認人工案例排序；可搜尋所有原文。定位原圖後再決定帶入。'))
        search=QLineEdit(); search.setPlaceholderText('搜尋候選、原文或擷取方法'); layout.addWidget(search)
        table=QTableWidget(0,5); table.setHorizontalHeaderLabels(['工程類別／欄位','內容／單位','擷取方法','對應原因','人工學習'])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table,1)
        learned=self.feedback.suggest(self.doc,key)
        candidates=ranked_candidates(self.doc,key,learned); table.setRowCount(len(candidates))
        for row,c in enumerate(candidates):
            values=(c['category']+'／'+c['label'],(c['value']+' '+c['unit']).strip(),
                    '；'.join(c['methods']),c['reason'] or '欄位與原文相符，待覆核',
                    '確認案例相似 '+str(c['learned']['similarity']) if c['learned'] else '')
            for col,value in enumerate(values):
                item=QTableWidgetItem(value); item.setToolTip(value+'\n原文：'+c['raw_text']); table.setItem(row,col,item)
        def filtering():
            for row,c in enumerate(candidates):
                table.setRowHidden(row,search.text().casefold() not in json.dumps(c,ensure_ascii=False).casefold())
        search.textChanged.connect(filtering)
        def locate():
            row=table.currentRow()
            if row<0: raise ValueError('請選擇候選')
            rid=candidates[row]['requirement_id']; self.reveal_review(rid); self.select_review_id(rid); self.focus_review_evidence()
            self.feedback.record(self.doc,'find_candidate',rid=rid,field=key,detail={'rank':row+1,'learned':candidates[row]['learned']})
        def apply():
            locate(); self.link_quotation_source(); dialog.accept()
        row=QHBoxLayout(); layout.addLayout(row)
        self.review_button('定位中央原圖',lambda:(locate(),dialog.accept()),row)
        self.review_button('帶入此欄位（待覆核）',apply,row)
        close=QPushButton('關閉'); close.clicked.connect(dialog.reject); row.addWidget(close)
        dialog.exec()

    def review_quotation_field(self):
        key=self.quotation_key(); row=next(i for i,f in enumerate(self.quotation_data) if f['key']==key)
        if self.quotation_data[row]['source'] is None: self.find_quotation_candidates()
        else: self.navigate_quotation_source(row)

    def capture_quotation_field(self):
        self.add_quotation_field()
        self._pending_quote_capture=self.quotation_key(); self.canvas.set_mode('draw')
        self.status.setText('請在中央圖面框選此報價欄位的原文，辨識後可修正並套用。')

    def verify_quotation_field(self):
        key=self.quotation_key(); field=next(f for f in self.quotation_data if f['key']==key)
        if not field['source']: raise ValueError('此欄位沒有来源，請找候選、框選補入，或新增後標示未提供／不適用')
        if self.form_dirty():
            if self.current_rid!=field['source']['id']: raise ValueError('其他候選仍有未儲存修改，請先儲存')
            self.save_current()
        self.require_saved_form()
        if field.get('proposed'):
            self.doc=self.store.set_quotation_source(self.doc['id'],self.doc['version'],key,field['source']['id'],
                self.reason_text('人工採用目前顯示的首選建議，接續核對原圖後確認'))
            self.feedback.record(self.doc,'accept_quotation_proposal',rid=field['source']['id'],field=key,
                detail={'reason':field['proposal_reason'],'issues':field['proposal_issues'],
                        'score':field['proposal_score'],'alternatives':field['proposal_alternatives']})
            self.refresh()
        self.reveal_review(field['source']['id']); self.select_review_id(field['source']['id'])
        self.verify_current()
        self.feedback.record(self.doc,'verify_quotation',rid=field['source']['id'],field=key)
        self.status.setText('已確認「'+field['label']+'」；左右來源與覆核狀態同步。')

    def verify_next_quotation(self):
        self.verify_quotation_field()
        current=next(i for i,f in enumerate(self.quotation_data) if f['key']==self._quotation_destination)
        order=self.quotation_data[current+1:]+self.quotation_data[:current]
        next_field=next((f for f in order if f['status']!='已確認'),None)
        if next_field:
            self._quotation_destination=next_field['key']; self.sync_quotation_selection()
            if next_field['source']: self.review_quotation_field()
