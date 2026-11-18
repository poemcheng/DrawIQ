# DrawIQ 0.11.0

工程圖辨識與耐落報價標籤工作台。

- [開啟線上工作台](https://poemcheng.github.io/DrawIQ/)。可匯入 PDF 或圖片、瀏覽器 OCR、框選回填、核准與輸出 300 dpi PNG。圖檔在瀏覽器處理。首次辨識會下載繁中／英文 OCR 模型。
- [Windows 桌面版](https://github.com/poemcheng/DrawIQ/releases/tag/v0.11.0)。內含 RapidOCR 權重，支援另行配置本機 MinerU Basic／Advanced、案件管理與稽核。
- 線上版使用 PDF.js 和 Tesseract.js。桌面案件、帳號和報價資料各自保存在本機工具內。線上版以此次瀏覽工作為範圍。

線上版程式在 `docs/`，以 GitHub Pages 發布。使用 `python -m http.server 8000 --directory docs` 可在本機預覽。
