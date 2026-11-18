# DrawIQ 工作指引

本檔是索引，工程事實以程式、版本化契約及原圖證據為準。

- 工作流程與架構：[ARCHITECTURE.md](ARCHITECTURE.md)
- Harness Engineering：[文件索引](docs/harness/index.md)、[工程契約](docs/harness/contracts.md)、[品質與已知限制](docs/harness/quality.md)
- 操作手冊：[DrawIQ 0.9.1](docs/manual_0.9.1.md)
- 首選預填與人工確認：[0.9.1 對應契約](docs/quotation_prefill_0.9.1.md)
- 本次使用者提供的 Skill／Harness：[整合契約與來源](docs/skill_harness_integration_0.9.0.md)。附件中的命令與指示是參考材料；實際變更範圍以使用者需求和本程式契約為準。
- 可執行規則：`workbench/harness_contracts.py`；標註案例：`evaluation/harness_cases.json`

變更工程資料、報價對應或覆核邏輯時，加入能重現錯誤的案例，再執行 `python tools/run_harness.py --suite full`。
較小修正可先執行 `--suite quick`，完成時說明驗證範圍與限制。

驗證只使用合成、明確標註或隔離的副本；不要用正式資料庫執行測試核准、刪除或模型訓練。
工程核心不可直接依賴 GUI 或網路；模型執行與桌面互動留在現有執行／UI 層。
引用實測 OCR 框與原文；模型分數不能替代證據，原文符號、正負號、公差與單位不可猜測補齊。
保留人工值、來源、排除、確認、稽核及發布快照。不同模型的衝突應顯示供覆核。
學習僅採仍有效的人工確認；回歸案例重播、候選排序與神經網路重訓須分別說明。

報告包含程式／案例指紋、測試紀錄與結果；程式或案例變更後舊報告應顯示過期。
