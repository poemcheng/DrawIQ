# DrawIQ 0.8.0 來源欄位準確度驗證

以下為 2026-10-07 的固定指紋評估。2026-10-08 的 [啟動修正](startup_fix_0.8.0.md) 未改擷取／對應規則，也未重跑這組圖面；報告中的當次 `valid_current_comparison` 應與保存的程式指紋一併解讀。

修正前後使用相同正式工作程序 `bounded.analyze → page_worker → ocr_runtime.configure`，相同 PP-OCRv6 ONNX 權重、相同來源 SHA。測試結果寫入隔離副本，正式文件資料庫只讀且前後摘要一致。測試不重訓模型、不替使用者簽核。

|來源組|可填標註欄位|0.7.0 正確／誤填／漏填|0.8.0 正確／誤填／漏填|
|---|---:|---:|---:|
|兩張實際工程圖 PDF／JPG|5|1／1／3|4／0／1|
|使用者提供的合成示範標籤|16|16／0／0|16／0／0|

實際工程欄位召回率由 20% 提升至 80%；已自動帶入欄位的正確比例由 50% 至 100%。樣本僅兩張實際圖、五個明示欄位，不能推廣為所有圖面的辨識率。示範標籤另列，避免掩蓋工程圖的難度。

改善包括 Full Thread 螺紋、Threadlock Tuflok 與品質彙整的首批／每批條件。剩餘一項為 第一張實際工程圖 的受損 DIN 標準號，OCR 無法可靠判讀該標準號，系統保留待核。可用右側找候選、框選補入及人工修改處理。第二張實際工程圖 的詢價發出方客戶是三處證據的角色推定，另列未計分提案。

所有 20 個正確帶入欄位的證據與人工標註區域相交。這是來源位置覆蓋检查，不是精確字框 IoU 或逐字／逐符號識字率。

另一份合成混合 PDF 路徑測試：原本可讀頁尾導致主體 OCR 被略過，五個主體工程 token 取得 0／5；融合後取得 5／5。這不與實際工程欄位指標混算。

原始資料與可重跑入口：

- 標註集（原始驗證工作區：`evaluation/quotation_accuracy_sources.json`）
- 修正前正式 OCR（原始驗證工作區：`qa/quotation_accuracy_0.8.0/baseline/report.json`）
- 修正後正式 OCR（原始驗證工作區：`qa/quotation_accuracy_0.8.0/after/report.json`）
- 獨立比較報告（原始驗證工作區：`qa/quotation_accuracy_0.8.0/comparison.json`）
- 混合 PDF 路徑（原始驗證工作區：`qa/hybrid_pdf_0.8.0/report.json`）
- 執行 `python qa/quotation_accuracy_benchmark.py --stage after` 重跑三來源正式 OCR。來源、模型與程式雜湊都保存在報告。

開發診斷曾使用未設定正式 ONNX 的 legacy OCR；該結果另存並明示不可比較，不計入上述數字。OCR 與模型方法須查實際執行紀錄，不能以模型名稱推測是否啟用。
