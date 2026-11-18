const MODULE_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/tesseract.esm.min.js';
const WORKER_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/worker.min.js';
const CORE_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js-core@6.0.0';
const LANGUAGE_URL = 'https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@4.1.0';

export const OCR_ENGINE = 'Tesseract.js 6.0.1（繁中／英文）';

let worker;
let progressCallback;
let queue = Promise.resolve();

function withDeadline(pending, milliseconds, description) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(description)), milliseconds);
  });
  return Promise.race([pending, deadline]).finally(() => clearTimeout(timer));
}

async function loadWorker() {
  const { default: tesseract } = await withDeadline(
    import(MODULE_URL), 45_000, '無法下載瀏覽器 OCR 程式，請檢查網路後重試。'
  );
  let reportError;
  const failed = new Promise((_, reject) => { reportError = reject; });
  const starting = tesseract.createWorker(['chi_tra', 'eng'], tesseract.OEM.LSTM_ONLY, {
    workerPath: WORKER_URL,
    corePath: CORE_URL,
    langPath: LANGUAGE_URL,
    cachePath: 'drawiq-tessdata-fast-4.1.0',
    gzip: false,
    logger: (event) => progressCallback?.(event),
    errorHandler: (failure) => reportError(new Error(String(failure))),
  });
  try {
    const ready = await withDeadline(
      Promise.race([starting, failed]), 60_000, 'OCR 模型載入逾時，請檢查網路後重試。'
    );
    await withDeadline(
      ready.setParameters({ tessedit_pageseg_mode: tesseract.PSM.SPARSE_TEXT }),
      10_000, 'OCR 設定逾時，請重試。'
    );
    return ready;
  } catch (error) {
    starting.then((ready) => ready.terminate()).catch(() => {});
    throw error;
  }
}

function normalizedBlock(line, width, height) {
  const text = line.text?.trim();
  const { x0, y0, x1, y1 } = line.bbox ?? {};
  if (!text || ![x0, y0, x1, y1].every(Number.isFinite)) return null;
  const clip = (ratio) => Math.max(0, Math.min(1, ratio));
  const box = [clip(x0 / width), clip(y0 / height), clip(x1 / width), clip(y1 / height)];
  if (box[0] >= box[2] || box[1] >= box[3]) return null;
  const confidence = Number.isFinite(line.confidence) ? clip(line.confidence / 100) : null;
  return { text, box, confidence };
}

/** Recognize a page or cropped canvas entirely in a browser worker. */
export function recognizeImage(canvas, { onProgress } = {}) {
  if (!canvas?.width || !canvas?.height) {
    return Promise.reject(new Error('找不到可辨識的圖像。'));
  }
  const run = queue.then(async () => {
    progressCallback = onProgress;
    try {
      worker ??= await loadWorker();
      const { width, height } = canvas;
      const { data } = await withDeadline(
        worker.recognize(canvas, {}, { text: true, blocks: true }),
        90_000, '圖像辨識逾時，請重試或框選較小的範圍。'
      );
      const blocks = (data.blocks ?? [])
        .flatMap((block) => block.paragraphs ?? [])
        .flatMap((paragraph) => paragraph.lines ?? [])
        .map((line) => normalizedBlock(line, width, height))
        .filter(Boolean);
      return { text: (data.text ?? '').trim(), blocks };
    } catch (error) {
      if (worker) {
        await worker.terminate();
        worker = undefined;
      }
      throw new Error(error?.message || String(error), { cause: error });
    } finally {
      progressCallback = undefined;
    }
  });
  queue = run.catch(() => {});
  return run;
}
