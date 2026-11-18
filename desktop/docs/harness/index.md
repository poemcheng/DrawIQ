# Harness Engineering 文件索引

DrawIQ 把工程意圖寫成版本化契約，再用可重跑的架構檢查、來源契約、回歸案例和操作證據形成回饋迴圈。
此設計參考 [OpenAI 的 Harness Engineering 方法](https://openai.com/index/harness-engineering/) 中的文件索引、機械式架構約束、可觀測性與驗證回饋；是本專案的實作，不是額外的 OCR 模型或外部產品認證。

- [架構](../../ARCHITECTURE.md)：既有模組邊界與實際約束範圍
- [契約](contracts.md)：欄位、來源、人工覆核與模型完成狀態
- [品質與限制](quality.md)：驗證範圍、指紋、人工案例與待處理項目
- [完整程式與架構盤點](../accuracy_architecture_audit_0.8.0.md)
- [實際來源欄位評估](../accuracy_results_0.8.0.md)：正式 OCR 修正前後比較，示範與工程圖分開計算
- [操作手冊](../manual_0.9.1.md)：如何查詢、修改、覆核與匯出
- [首選預填與人工確認](../quotation_prefill_0.9.1.md)：先填最適合候選、提示待確認及保留缺值
- [使用者 Skill／Harness 整合](../skill_harness_integration_0.9.0.md)：解析規則、來源契約、機器可讀覆核佇列與限制

開發／修正流程：重現問題 → 將明確預期加入案例或測試 → 修正 → 一鍵重跑 → 查證報告 → 人工覆核產品。

快速驗證：`python tools/run_harness.py --suite quick`

完整驗證：`python tools/run_harness.py --suite full`

重播人工匯出案例：`python tools/run_harness.py --suite quick --cases 人工確認案例.json`

各次執行保存獨立目錄、案例結果、原始測試日誌、政策與程式指紋；最新報告只是一個帶雜湊的索引。
