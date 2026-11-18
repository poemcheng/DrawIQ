# DrawIQ 0.9.1 架構

0.9.1 的右側先呈現首選候選，衝突／低信心待人工確認。`quotation_proposals.py` 負責排序、`quotation_context.py` 保留圖上製程與受損標準原文的建議；`quotation_rows(include_proposals=True)` 用於桌面與草稿匯出。嚴格來源驗證仍獨立，不將建議當確認；缺值在畫面標示待補填，資料記錄仍為 null。人工採用會保留該來源，並沿用既有對照原圖、修正、確認與稽核流程。

既有模組與準確度缺陷盤點：[0.8.0 盤點報告](docs/accuracy_architecture_audit_0.8.0.md)。前版固定來源比較：[0.8.0 驗證範圍與結果](docs/accuracy_results_0.8.0.md)。本版：[Skill／Harness 整合契約](docs/skill_harness_integration_0.9.0.md)。

0.9.0 加入使用者提供的 quote-drawing-extraction 規則。`ocr_contract.py` 驗證輸入；`quote_skill.py` 保留原文、型別與缺值／衝突；`skill_projection.py` 產生有實測來源的左側候選；`quotation_layout.py` 統一檢查後帶入右側。資料庫與人工簽核仍由既有 Store 管理，純規則不寫入原文、映射或覆核狀態。

`skill_vlm.py` 只規劃未解／低信心的實測區塊；`extraction.py` 保存局部輸入與座標轉換，再合併模型候選。OCR 原文和模型讀文分別保存。單次最多取一個規劃區塊送模型，其他區塊待後續框選／有預算時重讀；模型呼叫仍受每頁與各模型的時間限制。

`skill_records.py` 匯出每文件 JSON、`results.jsonl`、`review_queue.jsonl` 與摘要；同樣結果納入證據包。機器 extracted 與人工 verified 分開，不從檔名推定報價標籤和工程圖的配對。

0.8.0 新增 `engineering_semantics.py` 的工程角色與扭力條件；`engineering_ocr.py` 為受程序時間預算控制的原圖區塊重讀。混合 PDF 字層與 OCR 融合在 `ocr_runtime.py`，位圖位置由 `ingest.py` 實測。語意與報價層只消費已保存的文字框／模型讀文，不直接操作 GUI 或網路。

產品目標：工程 PDF／圖片 → 全部已取得的候選 → 原圖框與證據 → 耐落 16 欄標籤 → 人工覆核。

| 邊界 | 主要模組 | 職責 |
|---|---|---|
| 工程資料 | `domain.py`, `quotation_validation.py` | 結構、ID、單位、比較式、矛盾與型別 |
| 證據與對應 | `engineering.py`, `quotation_layout.py` | 原文索引、工程分類、16 欄對應、拒絕原因 |
| 持久化與學習 | `store.py`, `feedback.py`, `learning.py` | 原子版本、追加稽核、人工示範與核准策略 |
| 執行與來源 | `ingest.py`, `extraction.py`, `bounded.py`, `region_capture.py` | PDF／OCR／VLM、限時、取消、原始回應 |
| 桌面 UI | `ui.py`, `canvas.py`, `*_workspace.py`, `region_capture_ui.py` | 三欄、來源定位、框選與人工確認 |
| 驗證 | `harness.py`, `harness_validation.py`, `tools/run_harness.py` | 唯讀契約檢查、架構檢查、案例與測試報告 |

工程核心禁止直接匯入 Qt、UI 或 HTTP 用戶端；實際受保護清單以 `harness_contracts.py` 為準，AST 檢查包含函式內匯入。現有部分服務有延遲匯入與互相呼叫；本版沒有宣稱整個既有程式已重構成嚴格無循環分層。

`Store.mutate()` 維持單一原子變更與稽核。單筆／批次確認、核准、發布透過同一客製欄位硬契約；人工指定來源的用途警告仍供覆核。未完成 VLM 不刪除成功 OCR，也不單憑模型未完成禁止人工完成工程核准。

規則查詢不重跑模型、不修改文件版本。匯出包包含原文、框、方法、人工軌跡、`harness.json` 與 `skill/` 機器記錄；程式驗證報告另存於 `qa/harness_0.9.0/runs/`，不寫入正式文件或替人簽核。
