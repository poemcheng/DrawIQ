---
name: quote-drawing-extraction
description: Extract structured quotation-label and engineering-drawing attributes from Chinese/English scanned documents with source evidence, conservative normalization, validation and review queue. Use for quotation labels, screw/thread drawings, coatings, specification parsing, and discrepancy checks.
---
# 報價標籤與工程圖結構化抽取 Skill

## 目的與輸出契約
- 輸入：PNG/JPG/TIFF/PDF 或 OCR JSON；先區分 `quote_label`, `engineering_drawing`, `unknown`。
- 輸出：每份文件 `records/*.json`，彙整 `results.jsonl`、`review_queue.jsonl` 與 `summary.json`。
- **不得臆測**空白單價、配對關係、未標示單位、未辨識尺寸、公差或圖面數據。
- 對每項有值的欄位保留 `raw`, `normalized`, `source`(page, bbox, text, engine), `confidence`, `status`。
- 缺失 = `null`；衝突 = `conflict`；低信心 = `needs_review`。不得默默修正 OCR 錯字。

## 工作流
1. **掃描品質預檢**：解析是否為原生文字 PDF；掃描/旋轉圖面先判斷方向，再轉 200–300 dpi；保持原始檔。
2. **OCR**：優先使用輸入 OCR JSON (有文字、bbox、page、confidence)。無 OCR JSON 時本 harness 可呼叫本機 Tesseract，中文需安裝 `chi_tra`；請勿將空的 PDF 文字層誤判為有效識別。
3. **文件分類**：區分報價標籤與工程圖；不確定者 `unknown`，排入人工覆核。
4. **欄位錨點**：優先同一行冒號右側；欄位名稱容許中文字間空格；文字跨行時僅在相鄰區塊搜尋，不能跨越下一欄位錨點。
5. **結構剖析**：保留 `precoat` 型號後綴；分辨 Metric、UNC/UNF 與 `-6g/-6h` 公差。數量 `M` 不自動視作米/千件，單位原樣保存。
6. **規則校驗**：日期順序、報價編號格式、尺寸語法、欄位間矛盾、單價是否真的存在；沒有證據則不輸出。
7. **輸出與覆核**：以 `needs_review`, `conflict`, `missing` 彙整人工審查工作清單。
8. **跨文件比對**：必須有相同零件/料號、正式對應表或使用者明確指定關係，才能配對標籤與工程圖。不得靠文件序號或相似尺寸強行配對。

## 關鍵規則
- `DQ26004001`：報價單號。`KH`+8 digits 為目前樣本格式，只標記異常，不可拒絕其他供應商的合法編號。
- `precoat 80-8(紅)` ≠ `precoat 80(紅色)`，顏色正規化不可以合併產品/塗層級別。
- `M8x1.25x50` = 公稱徑 8mm、螺距 1.25mm、長度 50（標籤約定為 mm，仍保留來源）。
- `M8x1.25-6h` = 公稱徑 8mm、螺距 1.25mm、公差 `6h`，**不是長度**。
- `M10-6g` = 公稱徑 10mm、公差 `6g`，不可假設螺距。
- `7/16-20UNCx25.4` = 英制 7/16 英吋、20 TPI、UNC；最後長度 25.4 的單位除非文件/專案慣例另有明證，暫為 `unit_unverified`。
- 單價「/ TWD / M」但無數值：`unit_price=null`。
- 品質與包裝段落採分段擷取，完整保留原文，避免過度解析扭力門檻。

## Harness 使用
從專案根目錄執行：
```bash
python -m pip install -r requirements.txt
PYTHONPATH=src python -m qdextract.cli --input samples --output outputs
PYTHONPATH=src python -m pytest -q
```
使用已存在的 OCR 辨識輸出（推薦）:
```bash
PYTHONPATH=src python -m qdextract.cli --input examples/ocr_quote_01.json --output outputs
```
格式參考 `references/ocr_contract.md` 與 `references/field_schema.json`；詳見根目錄 `README.md`。

## 驗收標準
- 測試案例需覆蓋 3 張報價標籤關鍵欄位、7 張工程圖的多格式螺紋語法、空白價格及衝突。
- 保留抽取值與來源；建立人工 GT 後計算 field precision/recall/exact-match、spec parse accuracy，並按文件類別分層統計。
- 所有推論和規則產出不得冒充人工標註或經驗證成果。
