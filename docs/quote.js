export const fields = [
  '報價單號', '專案代號', '報價人員', '報價日期', '報價有效日期', '客戶', '材質', '報價膠別', '顏色',
  '塗佈方式', '規格', '數量', '報價', '品質要求', '包裝方式', '注意事項', '聯絡電話', '客戶傳真',
];

const alternatives = [
  ['報價編號'], ['專案編號', '案件代號'], ['業務人員'], [], ['有效日期'], ['客戶名稱'], [], ['報價類別', '膠別'], [],
  ['塗布方式', '塗佈方法'], ['產品規格'], ['訂購數量'], ['報價金額', '單價'], [], [], ['備註'], ['電話'], ['傳真'],
];

const matches = fields.map((label, index) => [label, ...alternatives[index]]);
const normalize = value => value.normalize('NFKC').trim();

export function labelLine(raw) {
  const text = normalize(raw);
  for (let index = 0; index < matches.length; index++) {
    for (const name of matches[index]) {
      const match = text.match(new RegExp(`^${[...name].join('\\s*')}\\s*(?:[:：]|$)\\s*(.*)$`, 'u'));
      if (match) return { index, value: match[1].trim() };
    }
  }
  return null;
}

export function cleanValue(index, text) {
  const value = normalize(text);
  if (!value || !/[\p{L}\p{N}]/u.test(value)) return '';
  if (index === 12 && !/\d/.test(value)) return '';
  return value;
}

function union(blocks) {
  return [Math.min(...blocks.map(b => b.box[0])), Math.min(...blocks.map(b => b.box[1])),
    Math.max(...blocks.map(b => b.box[2])), Math.max(...blocks.map(b => b.box[3]))];
}

export function labelSuggestions(blocks) {
  // Some OCR runs split the field name and value into separate boxes on the same printed line.
  const ordered = [...blocks].sort((a, b) => (a.box[1] + a.box[3]) - (b.box[1] + b.box[3]));
  const lines = [];
  for (const block of ordered) {
    const line = lines.at(-1);
    const [, y0, , y1] = block.box;
    const shared = line && Math.min(line.y1, y1) - Math.max(line.y0, y0);
    if (line && shared > Math.min(line.y1 - line.y0, y1 - y0) * .55) line.blocks.push(block);
    else lines.push({ y0, y1, blocks: [block] });
  }
  const values = new Map();
  for (const line of lines) {
    const sorted = line.blocks.sort((a, b) => a.box[0] - b.box[0]);
    const joined = labelLine(sorted.map(block => block.text).join(' '));
    const match = sorted.map(block => ({ block, entry: labelLine(block.text) })).find(item => item.entry);
    let entry = joined;
    let anchor = sorted;
    if (match?.entry.value) {
      entry = match.entry;
      anchor = [match.block];
    }
    if (!entry || !cleanValue(entry.index, entry.value) || values.has(entry.index)) continue;
    values.set(entry.index, { value: cleanValue(entry.index, entry.value), box: union(anchor), source: anchor[0] });
  }
  return values;
}

const font = '"Noto Sans TC", "Microsoft JhengHei", "PingFang TC", sans-serif';

function linesFor(ctx, value, width) {
  const lines = [];
  for (const paragraph of value.split('\n')) {
    let line = '';
    for (const char of paragraph) {
      if (line && ctx.measureText(line + char).width > width) {
        lines.push(line);
        line = '';
      }
      line += char;
    }
    lines.push(line);
  }
  return lines;
}

function pngChunk(type, body) {
  const data = new Uint8Array(body.length + 12);
  const view = new DataView(data.buffer);
  view.setUint32(0, body.length);
  data.set(new TextEncoder().encode(type), 4);
  data.set(body, 8);
  let crc = 0xffffffff;
  for (const byte of data.subarray(4, data.length - 4)) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  view.setUint32(data.length - 4, (crc ^ 0xffffffff) >>> 0);
  return data;
}

async function setPrintResolution(blob) {
  const data = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(data.buffer);
  const density = new Uint8Array(9);
  const mm = new DataView(density.buffer);
  mm.setUint32(0, 11811);
  mm.setUint32(4, 11811);
  density[8] = 1;
  const parts = [data.subarray(0, 8)];
  for (let offset = 8; offset < data.length;) {
    const end = offset + 12 + view.getUint32(offset);
    const type = new TextDecoder().decode(data.subarray(offset + 4, offset + 8));
    if (type !== 'pHYs') parts.push(data.subarray(offset, end));
    if (type === 'IHDR') parts.push(pngChunk('pHYs', density));
    offset = end;
  }
  return new Blob(parts, { type: 'image/png' });
}

export async function createLabel(values) {
  await document.fonts.ready;
  const canvas = document.createElement('canvas');
  canvas.width = 1181;
  const ctx = canvas.getContext('2d');
  const valueFont = `38px ${font}`;
  ctx.font = valueFont;
  const entries = values.map(value => linesFor(ctx, value, 810));
  const rowHeight = 53;
  canvas.height = 160 + entries.reduce((sum, lines) => sum + lines.length * rowHeight, 0);
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#121820';
  ctx.font = `700 48px ${font}`;
  ctx.textAlign = 'center';
  ctx.fillText('報價標籤', canvas.width / 2, 69);
  ctx.textAlign = 'left';
  let y = 129;
  for (const [index, lines] of entries.entries()) {
    ctx.font = `700 36px ${font}`;
    ctx.fillText(fields[index], 44, y);
    ctx.fillText('：', 265, y);
    ctx.font = valueFont;
    for (const line of lines) {
      ctx.fillText(line, 316, y);
      y += rowHeight;
    }
  }
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('無法產生 PNG 圖片');
  return setPrintResolution(blob);
}
