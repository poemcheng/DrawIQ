# 工程契約與判定

可執行規則版本以 `workbench/harness_contracts.py` 為準。`harness.py` 重用現有工程／報價檢查，避免另外寫一份不一致的判定。

| 規則 | 行為 |
|---|---|
| DQH-STRUCTURE | ID、頁、原圖正規化框、證據／特徵／依賴的連結有效 |
| DQH-FIELD-TYPE | ISO、責任說明、單位文字不能作為數量或客戶值 |
| DQH-AUTO-EVIDENCE | 自動帶入必須符合欄位用途、值、單位及原文；年度量與防鬆材料分別處理 |
| DQH-HUMAN-PROOF | 已確認來源不可失去人工證據確認 |
| DQH-MANUAL-ROLE | 人工指定用途有疑點時顯示警告，保留明示人工對應，不把警告自動當成新答案 |
| DQH-REVIEW | 自動帶入仍待人工，模型不得自行確認 |
| DQH-COVERAGE | 查全部已取得原文是否有候選索引；明確排除保留歷程 |
| DQH-MODEL | OCR 與各 VLM 的完成、失敗、逾時各自呈現 |

硬契約在確認、核准、發布入口執行，失敗時整筆／整批回滾。原有關鍵要求、數值、公差、衝突與依賴仍由 domain 檢查負責。

`contract_status` 只表示規則檢查結果；`review_ready` 表示既有工程核准條件；`quotation_review_complete` 表示 16 欄均有確認；`quotation_values_complete` 表示 16 欄均有非空採用值。這些不是 OCR 準確率，也不代表缺項已經補齊。

測試契約案例的原文是已標註輸入，沒有真的執行 OCR／VLM。人工確認案例只重播欄位關聯與原文支持，來源 SHA 和覆核需求雜湊留作查核。
