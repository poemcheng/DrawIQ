# Quote & Drawing Extraction — Skill + Harness

可放在 Codex 專案根目錄的規則式抽取專案。內含：
- `.agents/skills/quote-drawing-extraction/SKILL.md`：Codex 可參考的抽取 Skill（若個人環境使用 `~/.agents/skills/`，可直接複製此資料夾）。
- `AGENTS.md`：Harness 的工程化執行規範。
- `src/qdextract/`：實作抽取與欄位驗證、簡單 OCR CLI。
- `references/`：OCR 輸入契約與輸出欄位結構。
- `examples/`：依使用者上傳影像*人工示範轉寫*的三份輸入，用來測**規則**，不是聲稱自動 OCR 成果或完整 GT。
- `tests/`：自動化回歸測試。

## Windows PowerShell 啟動
```powershell
cd quote_drawing_skill_harness
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH='src'
python -m pytest -q
python -m qdextract.cli --input examples --output outputs
```
本機 PDF/圖片 OCR 還需安裝 Tesseract 與 `chi_tra` / `eng` 語言資料：
```powershell
python -m qdextract.cli --input samples --output outputs --lang chi_tra+eng
```

## Linux/macOS
```bash
python -m pip install -r requirements.txt
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m qdextract.cli --input examples --output outputs
```

## 處理真實樣本
1. 把 PDF 與圖片放入 `samples/`，執行 CLI；或將可信 OCR/VLM 輸出轉為 `references/ocr_contract.md` 格式。
2. 先看 `outputs/summary.json` 是否有 error，再看 `review_queue.jsonl`。
3. 針對 `needs_review` 用來源頁面與 bbox 人工核實。受 OCR 錯字影響的標籤通常需額外的版面模型或 VLM 補充。
4. 工程圖的幾何尺寸與引線不能只靠 OCR 行字串判定；本版僅輸出 `specification_candidates`，**不將其當已確認工程規格**。
5. 真正的欄位精確率要另建 Ground Truth，不能用範例測試通過當成 OCR 準確率。

## Harness 狀態與限制
目前是本機可執行的 **v0.1 baseline**：固定欄位錨點、結構解析、OCR 轉接、缺值/衝突處理、審查輸出、回歸測試。尚未實作大型 VLM、自動裁切修復、PDF 跨文件物料配對、幾何語意驗證或真實 10 檔 GT 成績。若想達到生產等級，下一步加入 Layout 模型與人工驗證集。
