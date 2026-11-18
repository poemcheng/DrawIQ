# DrawIQ 0.11.0 本機 MinerU 辨識評估

2026-11-18 在 Windows 本機用同一批原始圖檔比較 RapidOCR 3.10 / PP-OCRv6、MinerU 4.2.3 Flash 和 MinerU 4.2.3 Basic。電腦有 20 個邏輯處理器及 NVIDIA GeForce RTX 5070 Ti Laptop GPU。MinerU ONNX 的獨立試跑使用 DirectML，工作台使用自動裝置選擇。

使用三份報價標籤評估 30 個可清楚核對的非空欄位和 11 個明確空欄。使用一份工程 PDF 評估五個可從原圖核對的文字或規格。每種方法各跑四次，一筆輸入一次，共完成 12 次。端到端時間包含辨識子程序、後續擷取、保存及報價預填。主 OCR 和報價逐列重讀是各方法執行紀錄內的分項。相同日曆日期、全半形、大小寫、空白及規格中的乘號表示做格式統一，數量單位和工程數值仍須一致。

## 報價標籤

| 本機方法 | 端到端中位數 | 主 OCR 中位數 | 逐列重讀中位數 | 值吻合 | 空欄保留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| RapidOCR 四向比對 | 6.555 秒 | 0.544 秒 | 1.682 秒 | 27 / 30 | 10 / 11 |
| MinerU Basic | 7.815 秒 | 6.754 秒 | 0.003 秒 | 27 / 30 | 11 / 11 |
| MinerU Flash | 9.651 秒 | 8.548 秒 | 0.004 秒 | 27 / 30 | 11 / 11 |

這三頁的空白價格在三種方法下均保留空白。RapidOCR 有一格空白品質要求被帶入建議。

開發驗收中，相同三份標籤的 RapidOCR 逐列重讀中位數由 9.741 秒降到 1.682 秒，端到端中位數由 14.736 秒降到 6.555 秒。現行版本將原圖裁切區合批辨識，保留每一列的來源定位。此批標籤的 RapidOCR 單方法最快。工作台可選單跑 RapidOCR，或比對 RapidOCR 與 MinerU Basic。

## 工程圖

| 本機方法 | 端到端 | 主 OCR | 工程文字或規格吻合 |
| --- | ---: | ---: | ---: |
| RapidOCR 四向比對 | 13.359 秒 | 2.616 秒 | 3 / 5 |
| MinerU Basic | 11.448 秒 | 8.220 秒 | 4 / 5 |
| MinerU Flash | 11.777 秒 | 10.619 秒 | 0 / 5 |

這份工程圖以 MinerU Basic 取得四個標註，端到端快 1.911 秒。可在工作台選用 Basic，並從各方法結果對照原圖。三張標籤和一份工程圖的結果只描述本次樣本。參考截圖中的線上 advanced 強制 OCR 顯示 4.62 秒。線上配置與執行條件另有差異，本表採本機同批輸入的時間。

## 重跑

使用 `tools/benchmark_mineru.py`，以本機私人 JSON 指定原始文件、人工值和工程文字。人工值可含單位。確定未填的欄位用 `null`，模糊或未標註的欄位留出評估範圍。

```powershell
.\.venv\Scripts\python.exe tools\benchmark_mineru.py --fixture private.json --output result.json --backend rapid_auto --backend mineru_basic --backend mineru_flash
```

輸出包含通過狀態、時間及數量統計。原圖、實際讀值、人工標註和暫存與正式工作資料分開。MinerU 模型及指令參照 [MinerU 4.2.3](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.2.3-released) 與 [官方模型配置](https://opendatalab.github.io/MinerU/usage/model_source/)。
