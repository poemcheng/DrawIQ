"""DrawIQ 0.5 desktop shell: source queue, drawing canvas, quotation targets."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut, QKeySequence
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QLineEdit, QListWidget, QComboBox, QSplitter, QTabWidget, QStackedWidget,
    QScrollArea, QProgressBar)
from . import __version__
from .canvas import DrawingCanvas
from .workflow_ui import SummaryPage, CorpusKnowledgePage, BusinessPage


class WorkspaceShellMixin:
    def build_workspace(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(14, 12, 14, 10)
        self.build_workspace_header(outer)
        self.build_workspace_tools(outer)
        self.content_stack = QStackedWidget(); outer.addWidget(self.content_stack, 1)
        self.home = self.welcome_page(); self.content_stack.addWidget(self.home)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.content_stack.addWidget(self.main_splitter)
        self.build_source_sidebar()
        self.build_drawing_column()
        self.panel = QTabWidget()
        self.panel.addTab(self.requirements_tab(), '報價資訊／覆核')
        self.panel.addTab(self.inspection_tab(), '檢查／歷史')
        self.panel.addTab(self.learning_tab(), '學習／知識')
        self.review_workspace = QSplitter(Qt.Orientation.Horizontal)
        self.review_workspace.setChildrenCollapsible(False)
        self.review_workspace.addWidget(self.center); self.review_workspace.addWidget(self.panel)
        self.review_workspace.setStretchFactor(0, 3); self.review_workspace.setStretchFactor(1, 2)
        self.review_workspace.setSizes([950, 610])
        self.workspace_tabs = QTabWidget()
        from .nailok_ui import NailokFlowPage
        self.simple_page=NailokFlowPage(self)
        self.workspace_tabs.addTab(self.simple_page, "耐落報價工作台")
        self.summary_page = SummaryPage(self); self.corpus_page = CorpusKnowledgePage(self)
        self.business_page = BusinessPage(self)
        for page, title in [(self.summary_page, '1 圖面摘要'), (self.review_workspace, '2 覆核工作台'),
                            (self.corpus_page, '3 語料與知識'), (self.business_page, '4 詢價報價與製程')]:
            self.workspace_tabs.addTab(page, title)
        self.main_splitter.addWidget(self.workspace_tabs)
        self.main_splitter.setStretchFactor(0, 0); self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([310, 1290])
        self.workspace_tabs.currentChanged.connect(self.workspace_changed)
        self.workspace_tabs.setCurrentWidget(self.simple_page)
        self.advanced_mode.toggled.connect(self.toggle_advanced_mode)
        self.toggle_advanced_mode(False)
        self.workspace_changed(0)
        self.content_stack.setCurrentWidget(self.home)
        bottom = QHBoxLayout(); self.status = QLabel('就緒'); self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.busy = QProgressBar(); self.busy.setRange(0, 0); self.busy.setMaximumWidth(140)
        self.busy.hide(); bottom.addWidget(self.busy)
        self.cancel = self.button('取消工作', self.cancel_job, bottom); self.cancel.setEnabled(False)
        outer.addLayout(bottom)
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=self.confirm_workspace_selection)

    def confirm_workspace_selection(self):
        if self.workspace_tabs.currentWidget() is self.review_workspace:
            self.review_action(self.verify_next)
        elif self.simple_page.save.isEnabled():
            self.guard(self.simple_page.approve_label)

    def build_workspace_header(self, outer):
        header = QHBoxLayout(); brand = QLabel('DrawIQ'); brand.setObjectName('brand')
        header.addWidget(brand); header.addWidget(QLabel('耐落報價工作台  ·  ' + __version__))
        header.addStretch()
        if self.auth:
            header.addWidget(QLabel(self.store.actor()))
            self.button('帳號設定', self.open_account_settings, header)
            self.button('登出', self.logout, header)
        outer.addLayout(header)

    def build_workspace_tools(self, outer):
        self.easy_bar = QWidget(); easy = QHBoxLayout(self.easy_bar)
        easy.setContentsMargins(0, 0, 0, 0)
        self.button('＋ 匯入並讀圖', self.quick_import, easy).setObjectName('primary')
        self.button('載入既有成果', self.open_saved_results, easy)
        easy.addStretch()
        self.advanced_mode=QCheckBox('進階工具'); easy.addWidget(self.advanced_mode)
        self.button('操作手冊', self.open_manual, easy)
        self.more_tools = QCheckBox('更多工具'); easy.addWidget(self.more_tools); self.more_tools.hide()
        outer.addWidget(self.easy_bar)
        self.action_bar = QWidget(); bar = QHBoxLayout(self.action_bar); bar.setContentsMargins(0, 0, 0, 0)
        for title, action in [('＋ 新增圖面', self.import_files), ('新增資料夾', self.import_folder),
                              ('匯入舊版成果', self.import_history), ('開啟設計示例', self.load_example),
                              ('套用需求範本', self.apply_requirement_profile), ('開啟成果清單', self.open_result_set),
                              ('保存成果清單', self.save_result_set), ('卸載目前資料', self.unload_documents)]:
            self.button(title, action, bar)
        bar.addStretch(); self.button('資料備份', self.backup, bar); self.button('開啟輸出', self.open_exports, bar)
        self.tools_scroll = QScrollArea(); self.tools_scroll.setWidget(self.action_bar)
        self.tools_scroll.setWidgetResizable(True); self.tools_scroll.setMaximumHeight(70)
        self.tools_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tools_scroll.hide(); outer.addWidget(self.tools_scroll)
        self.more_tools.toggled.connect(self.tools_scroll.setVisible)

    def build_source_sidebar(self):
        self.sidebar = QWidget(); left = QVBoxLayout(self.sidebar); left.setContentsMargins(0, 0, 6, 0)
        self.sidebar.setMinimumWidth(230); self.sidebar.setMaximumWidth(315)
        self.doc_filter = QLineEdit(); self.doc_filter.setPlaceholderText('搜尋文件名稱')
        self.doc_filter.textChanged.connect(self.filter_documents); left.addWidget(self.doc_filter)
        self.doc_count = QLabel('尚無文件'); left.addWidget(self.doc_count)
        self.documents = QListWidget(); self.documents.currentItemChanged.connect(self.document_selected)
        left.addWidget(self.documents, 1)
        self.sidebar_hint = QLabel('原圖、分析結果與報價帶入均保留來源及覆核歷程。')
        self.sidebar_hint.setWordWrap(True); self.sidebar_hint.setObjectName('muted'); left.addWidget(self.sidebar_hint)
        self.main_splitter.addWidget(self.sidebar)

    def build_drawing_column(self):
        self.center = QWidget(); mid = QVBoxLayout(self.center); mid.setContentsMargins(0, 0, 6, 0)
        self.doc_title = QLabel('請匯入圖面或載入既有成果'); self.doc_title.setObjectName('documentTitle')
        self.doc_meta = QLabel(''); self.doc_meta.setWordWrap(True)
        mid.addWidget(self.doc_title); mid.addWidget(self.doc_meta)
        row = QHBoxLayout(); self.page_select = QComboBox()
        self.page_select.currentIndexChanged.connect(lambda: self.guard(self.page_changed)); row.addWidget(self.page_select, 1)
        for title, action in [('適合頁面', lambda: self.canvas.fit()), ('−', lambda: self.canvas.scale(1/1.2, 1/1.2)),
                              ('＋', lambda: self.canvas.scale(1.2, 1.2)), ('旋轉 90°', lambda: self.canvas.rotate_page())]:
            self.button(title, action, row)
        for index in range(row.count()): row.setStretch(index,1)
        mid.addLayout(row); row = QHBoxLayout()
        self.review_button('放大此項原文', self.focus_review_evidence, row)
        self.interaction_mode = QComboBox()
        for title, mode in [('選取／平移', 'select'), ('新增框', 'draw'), ('移動／縮放', 'edit')]:
            self.interaction_mode.addItem(title, mode)
        self.interaction_mode.currentIndexChanged.connect(lambda: self.guard(self.change_canvas_mode)); row.addWidget(self.interaction_mode, 1)
        self.region_kind = QComboBox(); self.region_kind.addItem('文字證據', 'evidence'); self.region_kind.addItem('作用特徵', 'feature')
        self.region_kind.setToolTip('設定下一個新增框的種類'); row.addWidget(self.region_kind, 1); mid.addLayout(row)
        row.setStretch(0,1)
        row = QHBoxLayout(); self.show_boxes = QCheckBox('顯示全部框')
        self.show_boxes.setChecked(True)
        self.show_boxes.toggled.connect(lambda: self.guard(lambda: self.show_page(keep=True))); row.addWidget(self.show_boxes)
        self.auto_focus = QCheckBox('選項時放大原文'); self.auto_focus.setChecked(True)
        row.addWidget(self.auto_focus); row.addStretch(); mid.addLayout(row)
        self.region_select = QComboBox(); self.region_select.setMinimumContentsLength(20)
        self.region_select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.region_select.currentIndexChanged.connect(self.region_list_selected); mid.addWidget(self.region_select)
        self.canvas = DrawingCanvas(); mid.addWidget(self.canvas, 1)
        self.canvas.mode_changed.connect(self.sync_canvas_mode)
        self.canvas.region_created.connect(lambda b: self.guard(lambda: self.new_region(b)))
        self.canvas.region_changed.connect(lambda eid, b: self.guard(lambda: self.move_region(eid, b)))
        self.canvas.region_clicked.connect(self.region_clicked)
        self.canvas.gesture_completed.connect(self.record_canvas_gesture)
        mid.addWidget(QLabel('紫＝尺寸／符號 · 綠＝材料 · 棕＝製程 · 藍＝品質／選框 · 粉＝關聯 · Esc 取消'))
        self.ocr_options = QWidget(); engine = QHBoxLayout(self.ocr_options)
        self.ocr_backend=QComboBox()
        from .ocr_engines import BACKENDS
        from .mineru_runtime import ADVANCED_BACKEND, advanced_available
        for key,label in BACKENDS.items():
            if key!=ADVANCED_BACKEND or advanced_available():
                self.ocr_backend.addItem(label,key)
        self.ocr_backend.addItem('RapidOCR 自動方向 ＋ MinerU Basic（獨立比對）', 'compare_mineru')
        self.ocr_backend.setCurrentIndex(self.ocr_backend.findData('compare_mineru'))
        self.ocr_backend.setToolTip('整頁辨識方法。MinerU 使用另裝的本機執行環境，比對模式會依序執行 RapidOCR 與 MinerU。')
        model_row=QHBoxLayout(); model_row.addWidget(QLabel('OCR 模型')); model_row.addWidget(self.ocr_backend,1); mid.addLayout(model_row)
        self.use_vlm = QCheckBox('本機 VLM'); self.use_vlm.setChecked(False); engine.addWidget(self.use_vlm)
        self.full_vlm=QCheckBox('完整模式 120 秒'); self.full_vlm.setChecked(True)
        self.full_vlm.setToolTip('完整模式讓視覺模型有時間讀完圖面；取消勾選為 30 秒快速候選模式。')
        engine.addWidget(self.full_vlm)
        self.model = QLineEdit('qwen2.5vl:7b'); self.model.setMaximumWidth(230)
        self.model.setToolTip('本機 vision 模型名稱；可用逗號指定多個模型，分別執行並保存候選。')
        engine.addWidget(self.model)
        self.read_rotation = QComboBox()
        for angle in (0, 90, 180, 270): self.read_rotation.addItem(f'辨識旋轉 {angle}°', angle)
        engine.addWidget(self.read_rotation); self.button('檢查模型', self.check_models, engine)
        mid.addWidget(self.ocr_options); self.ocr_options.hide()
        self.ocr_toggle = QCheckBox('顯示辨識設定'); mid.addWidget(self.ocr_toggle)
        self.ocr_toggle.toggled.connect(self.ocr_options.setVisible)
        self.button('辨識方法／模型／驗證／學習紀錄',self.show_method_report,mid)
        row = QHBoxLayout(); self.parse_btn = self.button('解析本頁', lambda: self.start_analysis(False), row)
        self.button('選框截圖／辨識', lambda: self.start_analysis(True), row)
        self.button('約束回饋（最多 3 區）', self.start_refine, row); mid.addLayout(row)
        for index in range(row.count()): row.setStretch(index,1)

    def ensure_workspace_selection_visible(self):
        for table in (self.requirements,self.quotation_table):
            item=table.currentItem()
            if item is not None: table.scrollToItem(item)

    def toggle_advanced_mode(self,enabled):
        for i in range(1,self.workspace_tabs.count()):self.workspace_tabs.setTabVisible(i,enabled)
        self.workspace_tabs.tabBar().setVisible(enabled)
        self.more_tools.setVisible(enabled)
        if not enabled:
            self.more_tools.setChecked(False)
            self.workspace_tabs.setCurrentWidget(self.simple_page)

