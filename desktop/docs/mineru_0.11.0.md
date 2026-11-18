# MinerU 本機辨識與評估

DrawIQ 0.11.0 使用獨立的 MinerU 4.2.3 執行環境，提供 Flash、Basic 和 RapidOCR ＋ MinerU Basic。已安裝進階權重時亦可選本機 Advanced。比對模式依序處理同一頁，保留方法名稱、各自讀值、來源框、完成狀態和方法耗時，適合判讀同一欄的不同結果。

MinerU 使用本機 ONNX 模型與強制 OCR，透過版本化 Middle JSON 讀取原文和量測框。無文字、座標不合格式或沒有量測框的項目無法成為報價證據。表格如僅有整區框，來源定位維持表格層級。PDF 的文字層與其他補充 OCR 仍會各自標示來源。Flash 與 Basic 關閉影像分析。Advanced 使用本機 llama.cpp 權重與 OCR。圖形等非文字版面區塊保留上游實測座標供定位，生成的圖片描述不帶入報價欄位。

上方選定方法後，可匯入新圖或按「重新辨識」。預設比對 RapidOCR 與 MinerU Basic，也可選 RapidOCR 單獨辨識。框選固定比對兩套本機工具。遇到辨識分歧時，可從右側點結果回到原圖確認後，再選左側欄位的備選。

## 圖面與結果對照

DrawIQ 依方法及頁次篩選原圖框和結果。點原圖框可選到右側結果；點右側結果可置中放大來源。可查看文字及 DrawIQ JSON，也能下載同一份篩選範圍的 Markdown 或 JSON，包含頁次、方法和座標。重新辨識支援「本頁」和「強制 OCR」。操作參考 [ModelScope MinerU Studio](https://www.modelscope.cn/studios/OpenDataLab/MinerU) 與 [MinerU WebUI 官方手冊](https://opendatalab.github.io/MinerU/usage/webui/)。

## Windows 配置

DrawIQ 會尋找 `%LOCALAPPDATA%\DrawIQ\MinerU\4.2.3` 下的獨立執行環境及模型。可在裝有 64 位元 Python 3.11 的 Windows 上執行：

```powershell
$drawiqMinerURuntime = Join-Path $env:LOCALAPPDATA 'DrawIQ\MinerU\4.2.3'
py -3.11 -m venv (Join-Path $drawiqMinerURuntime 'venv')
$drawiqMinerUPython = Join-Path $drawiqMinerURuntime 'venv\Scripts\python.exe'
$drawiqMinerUKit = Join-Path $drawiqMinerURuntime 'venv\Scripts\mineru-kit.exe'
& $drawiqMinerUPython -m pip install 'mineru==4.2.3'
$env:MINERU_HOME = Join-Path $drawiqMinerURuntime 'runtime-cache'
& $drawiqMinerUKit models download --tier basic --small-backend onnx --source huggingface
& $drawiqMinerUKit models verify --tier basic --small-backend onnx
```

若已部署在其他路徑，可在啟動工作台的環境設定 `DRAWIQ_MINERU_PYTHON` 指向獨立環境的 Python，`DRAWIQ_MINERU_HOME` 指向含 `models` 的模型快取。可用 `DRAWIQ_MINERU_ONNX_DEVICE=auto|cpu|gpu` 指定裝置。模型下載為設定階段，工作台讀圖固定使用本機檔案、模型及程序。DrawIQ 更新包提供整合程式與說明，MinerU 與權重由上游個別安裝。

MinerU 4.2.3 採用以 Apache 2.0 為基礎並附加條款的 MinerU Open Source License。版本、模型配置與授權請參照官方文件。 [MinerU 專案](https://github.com/opendatalab/MinerU) · [欄位與框位的輸出格式](https://opendatalab.github.io/MinerU/reference/output_files/) · [模型下載及離線配置](https://opendatalab.github.io/MinerU/usage/model_source/) · [4.2.3 授權](https://raw.githubusercontent.com/opendatalab/MinerU/mineru-4.2.3-released/LICENSE.md)

## 選用本機 Advanced

在相同的 MinerU 獨立環境下載並驗證 Standard 權重。Standard 與 Advanced 共用這組 VLM 權重，完成後重新開啟工作台即可出現 Advanced。

```powershell
& $drawiqMinerUKit models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source huggingface
& $drawiqMinerUKit models verify --tier standard --small-backend onnx --vlm-engine llama-cpp
```

在相同原件 一份工程圖 PDF 的本機驗證中，Advanced 兩次約 36.40 和 70.48 秒，5 個抽查項目吻合 2 個。Basic 更新後同檔實測約 10.41 秒，吻合 4 個。Advanced 同時提供可定位的圖形與表格版面框。這是單份工程圖的結果，日常預設仍使用 RapidOCR 與 Basic。

## 評估方法

三份報價標籤、一份工程圖及三種方法的實測結果見 [本機辨識評估](mineru_evaluation_0.11.0.md)。

以同機、同一份 PDF 或影像比較 DrawIQ 的可選後端。耗時含啟動隔離程序、辨識及擷取結果。報價標籤以人工核對的可讀欄位計算正確、錯值與漏填；空白、遮蔽或無法確認的欄位記錄得分範圍，另檢查空白是否被誤填。工程圖選取可人工核對的文字作為附加案例。此測試衡量指定樣本上可見的結果，未涵蓋整張圖面的工程尺寸與公差。

使用 `tools/benchmark_mineru.py` 傳入本機私有標註 JSON，可重跑並輸出聚合結果。使用者提供的線上截圖使用 Advanced 且顯示 4.62 秒，測試環境及計時範圍與本機各後端不同。

現有工具選擇亦參考 [Awesome OCR 2026](https://github.com/WalidHadri-Iron/awesome-ocr-2026) 的 OCR 分類。現場已裝的 PP-OCRv6／RapidOCR 和 MinerU Basic 用於預設流程，Indra 與既有方法可從辨識設定選用。
