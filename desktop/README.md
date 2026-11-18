# DrawIQ 0.11.0 台灣耐落報價工作台

當前 0.11.0 在同一畫面顯示報價欄位、工程圖與各辨識方法的結果。

- 首次使用可輸入 `user / user`，建立四位數個人帳號及密碼後進入工作台。已建立帳號可直接登入。
- 匯入 PDF 或圖片後，左側依原圖證據預填耐落報價標籤的 18 個欄位。中間可顯示彩色標註或查看原圖，右側可按方法查看結果並定位來源。
- 預設以 RapidOCR 與 MinerU Basic 本機辨識。選左側欄位後按「框選並辨識」，在原圖拖框即可回填並修正。核對後按「核准報價標籤」，在最後輸入視窗確認或補填，再按「核准並輸出圖片」。可複製或另存 300 dpi PNG，原欄未填會留空。
- 點原圖框或右側結果可雙向定位。重新辨識可選本頁或全部頁，也可強制從圖像讀字。右欄支援下載目前篩選結果的 Markdown 或 DrawIQ JSON。
- 需要整理內部報價時，從「報價草稿」匯出。工程覆核、知識庫與業務管理可從「進階工具」開啟。

## Windows 安裝

1. 下載並安裝 [Python 3.13 x64](https://www.python.org/downloads/) 與 Python Launcher。
2. 解壓 Windows 桌面版，或進入 GitHub 儲存庫的 `desktop` 資料夾，執行 `install_0.11.0.bat` 安裝固定版本的依賴。
3. 執行 `run.bat`。預設工作資料存於同層 `data`，也可在 PowerShell 執行 `.\run.bat --data "資料目錄"` 指定路徑。
4. Windows 下載版包含 RapidOCR 與 Indra 的權重。從 GitHub 原始碼安裝時，請先取得同版 Windows 下載版，將其中 `vendor/ocr_runtime/rapidocr/models/` 與 `models/indra-v1.1.0/` 的 `.onnx` 權重複製到原始碼的相同路徑。Indra 也可執行 `.venv\Scripts\python.exe tools\install_ocr_models.py` 從官方來源安裝。
5. 啟用 MinerU Basic 需另裝 Python 3.11 x64 並下載官方模型，步驟見 [MinerU 本機配置](docs/mineru_0.11.0.md#windows-配置)。完成後重新開啟工作台。

執行 `.venv\Scripts\python.exe -B tools\run_harness.py --suite full` 可重跑本公開版的隔離驗證。第三方元件依各自的授權條款提供，附註見 `third_party_licenses`、`models` 和 `vendor/ocr_runtime/licenses`。

[操作手冊](docs/manual_0.11.0.md) · [MinerU 本機辨識與評估](docs/mineru_0.11.0.md)
