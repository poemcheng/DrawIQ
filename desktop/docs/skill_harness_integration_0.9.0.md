# DrawIQ 0.9.0：報價與工程圖 Skill／Harness 整合

DrawIQ 在現有圖面辨識、工程候選與人工覆核流程上加入可查詢的規則抽取與機器輸出契約。規則的結果仍是候選；工程尺寸、未知單位、矛盾來源與人工覆核狀態各自保存。

## 套件來源

- 原始檔：`quote_drawing_skill_harness_v0.1.zip`。
- SHA256：`8217b3027b607ad8825fb97fc2e569451991db77a72e0cf0e24b99c322949d13`，已對原始 ZIP 執行 `Get-FileHash -Algorithm SHA256` 核對。
- 原樣解開於 [`vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness`](../vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/README.md)。ZIP 與解開檔案的來源指紋由整合的 [provenance.json](../vendor/quote_drawing_skill_harness_v0_1/provenance.json) 保存。
- 規則依據：原包 [SKILL.md](../vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/.agents/skills/quote-drawing-extraction/SKILL.md)、[AGENTS.md](../vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/AGENTS.md)、[OCR 輸入契約](../vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/references/ocr_contract.md)及[欄位契約](../vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/references/field_schema.json)。

原包 README 明確說明：三份 example 是人工示範轉寫，目的在測規則，不是自動 OCR 或完整 Ground Truth。原包尚未提供真實「3 張標籤＋7 張工程圖」的完整 GT、跨文件正式配對或準確率成績。整合也不將這三份測試當作十檔真實 OCR 成績。

## 原包 baseline 與 DrawIQ 整合的差異

| 契約 | 原包 v0.1 | DrawIQ 0.9.0 |
| --- | --- | --- |
| 文字來源 | OCR JSON 或 CLI 呼叫 Tesseract | 保留既有文字層、固定 OCR、工程重讀、模型與人工框選；提供 OCR JSON 僅經契約轉接 |
| 原文 | `compact()` 在欄位抽取前刪除空白 | 規則切出原值片段，來源保留實際 OCR 行；人工當前值另行保留 |
| 報價欄位 | 14 個固定欄位，包含電話 | 對接現有右側 16 個耐落欄位；電話、傳真、圖號、零件號可查詢 |
| 多個不同值 | 標記 conflict，保留第一個值 | 保留每個原值與來源群組；conflict 不以分數平均解決 |
| 人工覆核 | 與抽取結果沒有既有帳號／覆核整合 | machine status 與 `human_reviewed`、原 review status、`support_verified` 分開保存 |
| 無 bbox／尺寸 | 接受 OCR JSON 缺 bbox | 無框保留 `box=null`；頁面尺寸不明時不猜正規化座標，仍保留可信輸入 pixel bbox |
| 信心分數 | 缺分數時 baseline 取 0.5 | 未提供分數為 null；來源引擎分數不代表正確率、模型裁決或簽核 |
| VLM 補讀 | 原包未實作 | 實測、低信心或未解文字區塊的受限裁圖，原圖座標與來源 IDs 另存；無待補項目不呼叫模型 |
| 匯出檔名 | CLI 使用輸入檔案 stem | 使用 document ID 的完整 SHA256 做安全檔名，record 內保存原 ID、版本及來源 hash |
| 批次輸出 | 直接寫 JSON／JSONL | 決定性排序、每檔 atomic replace、提交失败回復舊批次，summary 最後寫入並含 artifact hashes |
| 標籤／工程圖配對 | Skill 要求相同料號、正式對應或人類指定 | 本階段不推論配對，`relations=[]`、`inferred_pairing=false`；不以檔名、檔序或相似尺寸配對 |

## 程式接口

[`workbench/quote_skill.py`](../workbench/quote_skill.py) 的 `analyze(doc)` 是規則投影，回傳文件類型、欄位、規格候選、issue 與待覆核項目。`parse_spec()` 保留螺紋公差、系列與未知長度單位；未指明作用對象的幾何 callout 仍是候選。

[`workbench/ocr_contract.py`](../workbench/ocr_contract.py) 的 `from_ocr_json(payload)` 檢查頁碼、像素框、頁面尺寸與分數。提供的人工轉寫以 `source_contract.execution=provided_transcription`、`ocr_executed=false` 記錄；無法定位的資料放入 `unlocated_ocr`，不變成假圖框。

[`workbench/skill_records.py`](../workbench/skill_records.py) 提供：

```python
record = build_record(document_snapshot)
summary = export_records(document_snapshot, output_directory)
summary = export_records([snapshot_a, snapshot_b], output_directory)
```

`build_record()` 不建立 Store、不連線資料庫、不啟動模型、不修改傳入文件。它合併規則結果與現有 `quotation_rows()`：已帶入的來源及人工值維持原樣；明確取消來源仍維持 null；規則不繞過報價 resolver 另行填入。

CLI [`tools/run_quote_skill.py`](../tools/run_quote_skill.py) 對單一 OCR JSON 或整個 JSON 資料夾重播相同正式規則：

```powershell
python tools/run_quote_skill.py --input "vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/examples/ocr_quote_01.json" --output "exports/skill_batch"
python tools/run_quote_skill.py --input "vendor/quote_drawing_skill_harness_v0_1/quote_drawing_skill_harness/examples" --output "exports/skill_batch"
```

命令在 `DrawIQ_Workbench` 執行。來源標 `provided_transcription`，不執行 OCR、VLM 或神經網路訓練；所有 JSON 驗證及候選建立完成後才提交批次。任一檔案契約無效、重複 ID 或資料夾無 JSON，回傳 exit code 2，原先成功批次保持不變。資料夾只處理直接子層的 `.json`。

## Record 契約

主要文件屬性為 `schema_version`、`document_id`、`document_version`、`source_sha256`、`document_sha256`、`document_type`、`fields`、`issues` 與 `relation_policy`。提供 OCR JSON 的 `source_sha256` 是 JSON snapshot 的指紋，透過 `source_hash_kind=provided_ocr_json_snapshot` 明示；不能當原始照片的 SHA256。

每欄保存：

- `raw`／`normalized`：當前值及唯讀正規化；缺值為 null。已選取的人類值另以 `selected_value` 原樣保存；`original_raw` 保留規則片段或先前抽取原值，便於辨識人工修改。
- `source`／`sources`：只從文件內真正的 region ID 或 unlocated OCR ID 重建。保存實際頁碼、原文、方法、原方法集合、normalized `box`、pixel `bbox`、來源分數與定位狀態。未知來源 ID 不得到假證據。
- `confidence`：所有有效來源分數皆已知時取最小值；任一來源分數缺失則為 null。每個 source 仍保存自己的原始分數。
- `status`：`extracted`、`needs_review`、`missing` 或 `conflict`，屬機器投影；`machine_status` 保留規則原始判定。
- `human_reviewed`／`review_status`／`support_verified`：分別保存既有 verified 狀態與證據確認，不提高 OCR 分數、不建立新核准。
- `alternatives`／`issues`／`invalid_source_ids`：不同原值、來源、缺值、低分、未知單位及來源不完整等可查詢原因。

既有工程來源已辨識出的規格，也以 `parse_spec()` 作唯讀結構解析。例如 Full Thread 原文中的 `M6x1,0-6g` 可保留原逗號並輸出直徑 6、螺距 1、公差 6g；幾何長度沒有單位證據時仍保留未知。來源需求的 origin、作用對象、scope、conditions 與 role_binding 一起保存；模型生成的來源未取得人類證據確認時仍列待覆核。

右側的 16 個 key 與現有報價 schema 一致。補充 key 為 `contact.phone`、`contact.fax`、`drawing_number`、`part_number`、`engineering.specification`；工程規格候選不當作人工確認的螺絲完整規格。

## 批次輸出與失敗處理

`export_records()` 產生 `records/<SHA256(document_id)>.json`、`results.jsonl`、`review_queue.jsonl`、`summary.json`。同一批相同 ID 的相同 snapshot 可去重；同 ID 不同 snapshot／版本會在寫入前拒絕。不同 ID 不因 Windows 大小寫、保留字、斜線或相同來源檔名碰撞。

每批只包含這次傳入的文件；前一批由 summary 列出的舊 record 可清理，未列出的其他檔案不刪除。symlink 與離開指定輸出目录的目標會拒絕。輸出先寫隔離 staging，再以 atomic file replacement 提交；任何替換失敗會回復原批次。summary 最後提交並保存所有其它 artifact 的 SHA256，讀取端可以偵測尚未提交完整的批次。此處不宣稱跨多個檔案的單一檔案系統 transaction，外部平行 writer 也不屬目前接口契約。

覆核清單包含缺值、低來源分數、衝突、未定單位、沒有定位框及 unknown 文件類型，並帶 `region_ids`、可定位的 `located_region_ids` 與原始 sources。已存在的人類確認狀態一起保存，便於區分機器限制與人工決定。

## 驗證與仍待完成的證據

專屬測試 [`tests/test_skill_records.py`](../tests/test_skill_records.py) 覆蓋來源重建、人工值與明確清除、低分／衝突、提供 OCR 缺框或尺寸、空白價格、規格候選、決定性排序、路徑／檔名安全、舊批次清理及注入替換失敗後回復。規則及整體驗收案例另由版本化 evaluation 與 Harness 驗證。

已有方法查詢、人工框選軌跡、回歸重播及學習資料流程繼續保存。此次增加規則契約與來源輸出，沒有聲稱已重新訓練神經網路。未取得真實十檔逐欄、逐框人工 GT 前，匯出 summary 的 field precision／recall／exact match 為 null，並標 `ground_truth_status=not_provided`。真實 OCR 欄位準確率需以獨立標註資料計算，不能以單元測試通過率替代。

可重跑的規則／來源輸出結果見 `qa/quote_skill_0.9.0/verification_report.json`（原始驗證工作區：`qa/quote_skill_0.9.0/verification_report.json`）。該流程的原包輸入是提供轉寫，並未替代原始圖片 OCR 驗證。桌面方法查詢列出 Skill 的實際規則投影；裁圖 VLM 每頁最多規劃三個區域並實際補讀一區，限制來自既有 worker 時間預算與 `skill_vlm` 契約，原圖文字保持不覆寫。
