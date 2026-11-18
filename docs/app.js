import { OCR_ENGINE, recognizeImage } from './ocr.js';
import { fields, cleanValue, createLabel, labelLine, labelSuggestions } from './quote.js';

const get = id => document.getElementById(id);
const fileInput = get('file-input');
const canvas = get('drawing');
const stage = get('drawing-stage');
const overlay = get('overlay');
const original = get('show-boxes');
const cropButton = get('enable-crop');
const runButton = get('run-ocr');
const prev = get('prev-page');
const next = get('next-page');
const approve = get('approve');
const finalDialog = get('final-dialog');
const exportDialog = get('export-dialog');
const savePng = get('save-png');
const copyPng = get('copy-png');
const svg = 'http://www.w3.org/2000/svg';
const pdfCdn = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
const state = { file: null, pdf: null, page: 1, total: 1, busy: false, crop: false, selected: null,
  activeField: null, drag: null, readings: new Map(), values: [], edited: new Set(), anchors: new Map(),
  png: null, pngUrl: null };

function element(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function updateStatus(text) { get('status').textContent = text; }

function controls() {
  const ready = !!state.file && stage.dataset.ready === 'true';
  fileInput.disabled = state.busy;
  get('load-example').disabled = state.busy;
  prev.disabled = !ready || state.busy || state.page <= 1;
  next.disabled = !ready || state.busy || state.page >= state.total;
  runButton.disabled = !ready || state.busy;
  approve.disabled = !ready || state.busy;
  original.disabled = !ready;
  cropButton.disabled = !ready || state.busy;
  cropButton.setAttribute('aria-pressed', String(state.crop));
  stage.dataset.cropping = String(state.crop);
  get('page-status').textContent = ready ? `${state.page} / ${state.total}` : '—';
}

function focusField(index) {
  state.activeField = index;
  for (const [i, field] of fieldInputs.entries()) field.closest('.field-row').setAttribute('aria-current', String(i === index));
  const source = state.anchors.get(index);
  if (source?.page === state.page) selectResult(source.id);
}

function addInputs(container, prefix, reserveNotes) {
  return fields.map((name, index) => {
    const row = element('div', 'field-row');
    const label = element('label');
    label.htmlFor = `${prefix}-${index}`;
    label.textContent = name;
    const input = element(reserveNotes && [13, 14, 15].includes(index) ? 'textarea' : 'input');
    if (input.tagName === 'TEXTAREA') input.rows = 2;
    input.id = label.htmlFor;
    input.placeholder = '待補';
    input.maxLength = 1000;
    input.autocomplete = 'off';
    if (prefix === 'field') {
      input.addEventListener('focus', () => focusField(index));
      input.addEventListener('input', () => {
        state.edited.add(index);
        state.values[index] = input.value;
      });
    }
    row.append(label, input);
    container.append(row);
    return input;
  });
}

const fieldInputs = addInputs(get('fields'), 'field', true);
const finalInputs = addInputs(get('final-fields'), 'final', true);

function setValue(index, value) {
  state.values[index] = value;
  fieldInputs[index].value = value;
}

function resetDocument() {
  stage.dataset.ready = 'false';
  state.page = 1;
  state.total = 1;
  state.readings.clear();
  state.edited.clear();
  state.anchors.clear();
  state.activeField = null;
  state.selected = null;
  state.crop = false;
  get('document-name').textContent = '尚未開啟圖檔';
  for (const [index, field] of fieldInputs.entries()) {
    field.closest('.field-row').setAttribute('aria-current', 'false');
    setValue(index, '');
  }
  showReadings();
}

let pdfLibrary;
async function openPdf(file) {
  pdfLibrary ??= import(`${pdfCdn}build/pdf.mjs`).catch(error => { pdfLibrary = null; throw error; });
  const pdf = await pdfLibrary;
  pdf.GlobalWorkerOptions.workerSrc = `${pdfCdn}build/pdf.worker.mjs`;
  return pdf.getDocument({ data: await file.arrayBuffer(), isEvalSupported: false,
    cMapUrl: `${pdfCdn}cmaps/`, cMapPacked: true, standardFontDataUrl: `${pdfCdn}standard_fonts/`,
    wasmUrl: `${pdfCdn}wasm/` }).promise;
}

function paint(bitmap) {
  const scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
}

async function displayPage() {
  if (state.pdf) {
    const page = await state.pdf.getPage(state.page);
    const size = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: 2200 / Math.max(size.width, size.height) });
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport }).promise;
    page.cleanup();
  }
  stage.dataset.ready = 'true';
  overlay.setAttribute('viewBox', `0 0 ${canvas.width} ${canvas.height}`);
  state.selected = null;
  showReadings();
}

async function locked(work) {
  if (state.busy) return;
  state.busy = true;
  controls();
  try { await work(); }
  catch (error) { updateStatus(error instanceof Error ? error.message : String(error)); }
  finally { state.busy = false; controls(); }
}

async function loadDocument(file) {
  if (file.size > 50 * 1024 * 1024) throw new Error('檔案大小上限為 50 MB。');
  if (!/\.(pdf|png|jpe?g|webp|bmp)$/i.test(file.name)) throw new Error('請選擇 PDF、PNG、JPG、WebP 或 BMP。');
  if (state.pdf) await state.pdf.destroy();
  state.pdf = null;
  state.file = null;
  resetDocument();
  updateStatus('載入圖檔中…');
  if (/\.pdf$/i.test(file.name)) {
    state.pdf = await openPdf(file);
    state.total = state.pdf.numPages;
  } else {
    const bitmap = await createImageBitmap(file);
    try { paint(bitmap); } finally { bitmap.close(); }
  }
  await displayPage();
  state.file = file;
  get('document-name').textContent = file.name;
  updateStatus('圖檔已開啟。選擇「辨識本頁」或選欄框選。');
}

function pageReadings() { return state.readings.get(state.page) ?? []; }

function rect(box, className) {
  const node = document.createElementNS(svg, 'rect');
  node.setAttribute('x', box[0] * canvas.width);
  node.setAttribute('y', box[1] * canvas.height);
  node.setAttribute('width', (box[2] - box[0]) * canvas.width);
  node.setAttribute('height', (box[3] - box[1]) * canvas.height);
  node.setAttribute('class', className);
  node.setAttribute('vector-effect', 'non-scaling-stroke');
  return node;
}

function paintBoxes() {
  overlay.replaceChildren();
  if (original.checked) for (const row of pageReadings()) {
    const node = rect(row.box, row.id === state.selected ? 'source-box selected' : 'source-box');
    node.dataset.result = row.id;
    overlay.append(node);
  }
  if (state.drag) overlay.append(rect(between(state.drag.start, state.drag.end), 'crop-box'));
}

function selectResult(id, scroll = false) {
  state.selected = id;
  for (const node of get('results').querySelectorAll('.result-row')) {
    const selected = id === node.dataset.result;
    node.setAttribute('aria-current', String(selected));
    if (selected && scroll) node.scrollIntoView({ block: 'nearest' });
  }
  paintBoxes();
}

function showReadings() {
  const container = get('results');
  const empty = get('result-placeholder');
  container.replaceChildren(...(empty ? [empty] : []));
  for (const row of pageReadings()) {
    const button = element('button', 'result-row');
    button.type = 'button';
    button.dataset.result = row.id;
    const text = element('span');
    text.textContent = row.text;
    const method = element('small');
    method.textContent = row.cropped ? '框選辨識' : '本頁辨識';
    button.append(text, method);
    button.addEventListener('click', () => {
      original.checked = true;
      selectResult(row.id);
      if (matchMedia('(max-width: 1080px)').matches) stage.scrollIntoView({ block: 'center' });
    });
    container.append(button);
  }
  if (empty) empty.hidden = pageReadings().length > 0;
  selectResult(state.selected);
}

function progress(message) {
  if (message.status === 'recognizing text') updateStatus(`辨識中… ${Math.round(message.progress * 100)}%`);
  else updateStatus('準備瀏覽器 OCR 模型…');
}

function appendReadings(blocks, crop) {
  const earlier = crop ? pageReadings() : pageReadings().filter(row => row.cropped);
  const rows = blocks.map((block, index) => {
    const box = crop ? block.box.map((pixel, side) => crop[side % 2] + pixel * (crop[(side % 2) + 2] - crop[side % 2])) : block.box;
    return { ...block, box, cropped: !!crop, id: `${state.page}:${crypto.randomUUID()}:${index}` };
  });
  state.readings.set(state.page, [...earlier, ...rows]);
  return rows;
}

async function recognizePage() {
  const result = await recognizeImage(canvas, { onProgress: progress });
  const blocks = appendReadings(result.blocks, null);
  const suggestions = labelSuggestions(blocks);
  for (const [index, prior] of state.anchors) {
    if (prior.page === state.page && !state.edited.has(index) && !suggestions.has(index)) {
      setValue(index, '');
      state.anchors.delete(index);
    }
  }
  let entered = 0;
  for (const [index, suggestion] of suggestions) {
    if (state.edited.has(index)) continue;
    const previous = state.anchors.get(index);
    if (state.values[index] && previous?.page !== state.page) continue;
    setValue(index, suggestion.value);
    state.anchors.set(index, { id: suggestion.source.id, page: state.page });
    entered++;
  }
  original.checked = true;
  showReadings();
  updateStatus(`本頁取得 ${blocks.length} 筆結果，帶入 ${entered} 欄。請對照原圖核對。`);
}

function localPoint(event) {
  const box = overlay.getBoundingClientRect();
  const clamp = value => Math.max(0, Math.min(1, value));
  return [clamp((event.clientX - box.left) / box.width), clamp((event.clientY - box.top) / box.height)];
}

function between(start, end) {
  return [Math.min(start[0], end[0]), Math.min(start[1], end[1]),
    Math.max(start[0], end[0]), Math.max(start[1], end[1])];
}

async function recognizeCrop(box, index) {
  const x = Math.floor(box[0] * canvas.width);
  const y = Math.floor(box[1] * canvas.height);
  const width = Math.ceil(box[2] * canvas.width) - x;
  const height = Math.ceil(box[3] * canvas.height) - y;
  if (width < 8 || height < 8) throw new Error('框選範圍太小，請重新框選。');
  const snippet = element('canvas');
  const scale = Math.min(3, Math.max(1, 90 / height));
  snippet.width = Math.ceil(width * scale);
  snippet.height = Math.ceil(height * scale);
  snippet.getContext('2d').drawImage(canvas, x, y, width, height, 0, 0, snippet.width, snippet.height);
  const result = await recognizeImage(snippet, { onProgress: progress });
  const rows = appendReadings(result.blocks, box);
  const labeled = labelSuggestions(rows).get(index);
  const plain = result.text.split(/\r?\n/u).map(s => s.trim()).filter(s => s && !labelLine(s)).join('\n');
  const value = labeled?.value || cleanValue(index, plain);
  if (value) {
    setValue(index, value);
    state.edited.add(index);
    if (rows.length) state.anchors.set(index, { id: labeled?.source.id ?? rows[0].id, page: state.page });
  }
  showReadings();
  if (rows.length) selectResult(labeled?.source.id ?? rows[0].id, true);
  updateStatus(value ? `框選結果已帶入「${fields[index]}」，可直接修改。` : '框選未取得可用值，請重新框選或手動輸入。');
}

overlay.addEventListener('pointerdown', event => {
  if (!state.crop || state.busy || event.button !== 0) return;
  overlay.setPointerCapture(event.pointerId);
  const point = localPoint(event);
  state.drag = { start: point, end: point };
  paintBoxes();
});
overlay.addEventListener('pointermove', event => {
  if (!state.drag) return;
  state.drag.end = localPoint(event);
  paintBoxes();
});
overlay.addEventListener('pointerup', event => {
  if (!state.drag) return;
  const box = between(state.drag.start, localPoint(event));
  state.drag = null;
  state.crop = false;
  paintBoxes();
  locked(() => recognizeCrop(box, state.activeField));
});
overlay.addEventListener('pointercancel', () => { state.drag = null; paintBoxes(); });
overlay.addEventListener('click', event => {
  if (!state.busy && !state.crop && event.target.dataset.result) selectResult(event.target.dataset.result, true);
});

function example() {
  const sample = element('canvas');
  sample.width = 1200;
  sample.height = 1450;
  const ctx = sample.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, sample.width, sample.height);
  ctx.fillStyle = '#17222b';
  const font = '"Microsoft JhengHei", "PingFang TC", sans-serif';
  ctx.font = `bold 54px ${font}`;
  ctx.fillText('報價標籤', 70, 90);
  ctx.font = `42px ${font}`;
  const values = ['DEMO-011', '', '範例人員', '2026/11/19', '', '', '鐵材', 'precote 80', '紅色',
    '全周', 'M6x1x35', '160 M', '', '', '', '', '', ''];
  for (const [index, text] of fields.entries()) ctx.fillText(`${text}：${values[index]}`, 70, 169 + index * 64);
  return new Promise(resolve => sample.toBlob(blob => resolve(new File([blob], '範例報價標籤.png', { type: 'image/png' }))));
}

fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  fileInput.value = '';
  if (file) locked(() => loadDocument(file));
});
get('load-example').addEventListener('click', () => locked(async () => loadDocument(await example())));
runButton.addEventListener('click', () => locked(recognizePage));
const changePage = offset => locked(async () => {
  const previous = state.page;
  state.page += offset;
  state.crop = false;
  try { await displayPage(); }
  catch (error) { state.page = previous; await displayPage(); throw error; }
  updateStatus(`已開啟第 ${state.page} 頁。`);
});
prev.addEventListener('click', () => changePage(-1));
next.addEventListener('click', () => changePage(1));
original.addEventListener('change', paintBoxes);
cropButton.addEventListener('click', () => {
  if (state.activeField === null) {
    updateStatus('請先點選左側要回填的欄位。');
    return;
  }
  state.crop = !state.crop;
  if (state.crop) original.checked = true;
  paintBoxes();
  controls();
  updateStatus(state.crop ? `請在原圖拖曳框選「${fields[state.activeField]}」。` : '已取消框選。');
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || !state.crop) return;
  state.crop = false;
  state.drag = null;
  paintBoxes();
  controls();
  updateStatus('已取消框選。');
});

approve.addEventListener('click', () => {
  for (const [index, input] of finalInputs.entries()) input.value = state.values[index];
  finalDialog.showModal();
});
get('cancel-final').addEventListener('click', () => finalDialog.close());
get('finish-final').addEventListener('click', async event => {
  event.preventDefault();
  const button = event.currentTarget;
  const values = finalInputs.map(input => input.value.trim());
  if (button.disabled) return;
  button.disabled = true;
  try {
    const png = await createLabel(values);
    if (!finalDialog.open) return;
    for (const [index, value] of values.entries()) {
      if (state.values[index] !== value) state.edited.add(index);
      setValue(index, value);
    }
    if (state.pngUrl) URL.revokeObjectURL(state.pngUrl);
    state.png = png;
    state.pngUrl = URL.createObjectURL(png);
    get('export-preview').src = state.pngUrl;
    copyStatus().textContent = '';
    finalDialog.close();
    exportDialog.showModal();
    updateStatus('已核准目前標籤。可另存或複製 PNG。');
  } catch (error) {
    finalDialog.close();
    updateStatus(error.message);
  } finally { button.disabled = false; }
});
get('close-export').addEventListener('click', () => exportDialog.close());
savePng.addEventListener('click', () => {
  const link = element('a');
  link.href = state.pngUrl;
  const name = (state.values[0] || 'DrawIQ').replace(/[\\/:*?"<>|]/g, '_');
  link.download = `${name}_報價標籤.png`;
  link.click();
});
const copyStatus = () => get('export-status') ?? get('status');
copyPng.addEventListener('click', async () => {
  try {
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': state.png })]);
    copyStatus().textContent = '已複製圖片。';
  } catch { copyStatus().textContent = '目前瀏覽器無法寫入剪貼簿，請使用下載 PNG。'; }
});

const engineLabel = get('ocr-engine');
if (engineLabel) engineLabel.textContent = OCR_ENGINE;
resetDocument();
controls();
