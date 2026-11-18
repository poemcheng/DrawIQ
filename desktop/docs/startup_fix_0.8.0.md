# DrawIQ 0.8.0 啟動修正（2026-10-08）

使用者回報雙擊啟動後只有黑色視窗，登入畫面未出現。原啟動日誌沒有階段紀錄，無法還原該次失敗的確切原因；本機 Python 3.13 與登入後主畫面在隔離工作區均能正常執行。

已修正根啟動檔未傳遞參數，兩份批次檔使用 Windows CRLF。預檢保留實際 Python 路徑與完整錯誤，優先工作區 `.venv`，失敗後試 `py -3`；不再把環境錯誤導向舊版安裝包。執行入口使用未緩衝輸出。

Windows 桌面入口清除其他工具留下的 `QT_QPA_PLATFORM`、`QT_PLUGIN_PATH` 與 `QT_QPA_PLATFORM_PLUGIN_PATH`，使用本身的 Windows Qt 外掛。登入窗置於主螢幕中央並請求前景顯示。登入初始化／建立主畫面失敗時顯示錯誤，安全退出。

`launcher.log` 現在含 `application.start`、`qt.ready`、`auth.initialize.ready`、`login.visible`、`workspace.visible` 等階段及程序 ID。只記錄執行環境與視窗狀態，不記錄密碼或圖面內容。

啟動入口為 `啟動_DrawIQ_0.8.0.bat`。帳號與工作區仍位於 `DrawIQ_Workbench/data`，本次沒有搬移或重設帳號。

驗證包含：

- 6 個真實 Windows 批次／子程序測試，涵蓋 Unicode 參數、私有 runtime、依賴失敗備援、退出碼及 CLI 不建立資料庫。
- 5 個登入生命週期測試，涵蓋真實 modal 顯示與取消、既有帳號登入、初始化失敗、桌面 Qt 環境與啟動指紋。
- Windows 啟動實測（原始驗證工作區：`qa/startup_0.8.0/report.json`）：真正根批次 → app.py → Windows 登入窗，首次設定與既有帳號各一案例。刻意傳入 offscreen／錯誤 Qt 外掛環境後，兩者均顯示可見的原生 Windows 登入窗，取消後正常退出。只使用臨時工作區。
- 完整測試結果見 `qa/harness_0.8.0/latest.json`。啟動入口與批次檔現已納入程式指紋。

本次未改 OCR、模型或報價對應規則。先前欄位準確度報告保存當次程式指紋；本次驗證啟動流程，不將舊準確度報告冒充為啟動修正後重跑的結果。
