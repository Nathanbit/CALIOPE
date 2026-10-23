// Intérprete de mensajes del panel de CALIOPE.
//
// Convierte una frase en español ("BTC toca 82.500 hoy, 60%") en los campos de
// un concepto de concepts.py. Es solo una propuesta: la persona revisa los
// campos, `seal --dry-run` verifica y la persona confirma. Sin dependencias,
// para poder probarlo con node (tests/test_panel.py).

export const LEAD_MS = 3 * 60_000; // tiempo para pegar el comando antes del inicio
export const STEP_MS = { '1m': 60_000, '15m': 900_000 };
const HOUR = 3_600_000;

const ASSETS = [
  [/\b(btc|bitcoin|bitcoins)\b/, 'BTCUSDT'],
  [/\b(eth|ether|ethereum)\b/, 'ETHUSDT'],
  [/\b(sol|solana)\b/, 'SOLUSDT'],
];
const HUNDREDS = {
  cien: 100, ciento: 100, doscientos: 200, trescientos: 300, cuatrocientos: 400,
  quinientos: 500, seiscientos: 600, setecientos: 700, ochocientos: 800, novecientos: 900,
};
const WEEKDAYS = { domingo: 0, lunes: 1, martes: 2, miercoles: 3, jueves: 4, viernes: 5, sabado: 6 };
const UP = /\b(sube|subir|subira|suba|llega|llegar|supera|superar|rompe|romper|toca arriba|alcanza)\b/;
const DOWN = /\b(baja|bajar|bajara|cae|caer|caera|perfora|pierde|perder|retrocede)\b/;

export function normalize(text) {
  return (text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/\bpor\s*ciento\b/g, '%').replace(/\s+/g, ' ').trim();
}

function toNumber(raw) {
  // "82.500" y "82,500" son miles; "0,6" y "82.5" son decimales.
  if (/^\d{1,3}([.,]\d{3})+$/.test(raw)) return Number(raw.replace(/[.,]/g, ''));
  return Number(raw.replace(',', '.'));
}

// Números con su posición y lo que los rodea.
export function numbers(text) {
  const hundreds = Object.keys(HUNDREDS).join('|');
  const re = new RegExp(
    String.raw`(\d+(?:[.,]\d+)*)(?:\s*(k)\s*(\d{1,3})?|\s+mil(?:\s+(\d{1,3}|${hundreds}))?)?(\s*%)?`, 'g');
  const out = [];
  for (const m of text.matchAll(re)) {
    let value = toNumber(m[1]);
    if (m[2]) value = value * 1000 + (m[3] ? Number(m[3]) : 0);
    else if (/\smil\b/.test(m[0])) {
      const tail = m[4];
      value = value * 1000 + (tail ? (HUNDREDS[tail] ?? Number(tail)) : 0);
    }
    if (!Number.isFinite(value)) continue;
    const before = text.slice(0, m.index);
    const after = text.slice(m.index + m[0].length);
    let kind = 'price';
    if (m[5]) kind = 'percent';
    else if (/\b(probabilidad|prob|confianza)\s*(de\s*)?$/.test(before)) kind = 'percent';
    else if (/(a las|a la)\s*$/.test(before) || /\d:$/.test(before)) kind = 'clock';
    else if (/^\s*(h|hs|hrs|horas?)\b/.test(after)) kind = 'hours';
    else if (/^\s*(dias?)\b/.test(after)) kind = 'days';
    else if (/^\s*(min|minutos?)\b/.test(after)) kind = 'minutes';
    out.push({ value, kind, index: m.index, end: m.index + m[0].length, raw: m[0] });
  }
  return out;
}

function endOfDay(date) {
  const d = new Date(date);
  d.setHours(24, 0, 0, 0); // medianoche local siguiente
  return d;
}

function addDays(date, days) {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

// Fecha límite en hora local de quien escribe.
export function deadline(text, now) {
  const nums = numbers(text);
  const hours = nums.find((n) => n.kind === 'hours');
  const minutes = nums.find((n) => n.kind === 'minutes');
  if (/\ben\b/.test(text) && (hours || minutes)) {
    const ms = (hours ? hours.value * HOUR : 0) + (minutes ? minutes.value * 60_000 : 0);
    return { date: new Date(now.getTime() + ms), label: `en ${hours ? hours.value + ' h' : ''}${minutes ? ' ' + minutes.value + ' min' : ''}`.trim() };
  }
  let day = null;
  let label = '';
  const daysN = nums.find((n) => n.kind === 'days');
  if (daysN && /\ben\b/.test(text)) { day = addDays(now, daysN.value); label = `en ${daysN.value} días`; }
  else if (/\bpasado manana\b/.test(text)) { day = addDays(now, 2); label = 'pasado mañana'; }
  else if (/\bmanana\b/.test(text.replace(/de la manana/g, ''))) { day = addDays(now, 1); label = 'mañana'; }
  else if (/\bhoy\b|\besta noche\b/.test(text)) { day = new Date(now); label = 'hoy'; }
  else if (/\bfin de semana\b|\besta semana\b/.test(text)) {
    day = addDays(now, (7 - now.getDay()) % 7); label = 'el domingo';
  } else {
    for (const [name, wd] of Object.entries(WEEKDAYS)) {
      if (new RegExp(`\\b${name}\\b`).test(text)) {
        day = addDays(now, (wd - now.getDay() + 7) % 7);
        label = `el ${name}`;
        break;
      }
    }
  }
  const clock = text.match(/a las? (\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|de la manana|de la tarde|de la noche)?/);
  if (clock) {
    let h = Number(clock[1]);
    const m = clock[2] ? Number(clock[2]) : 0;
    const part = clock[3] || '';
    if (/pm|p\.m\.|tarde|noche/.test(part) && h < 12) h += 12;
    if (/am|a\.m\.|manana/.test(part) && h === 12) h = 0;
    const d = new Date(day || now);
    d.setHours(h, m, 0, 0);
    return { date: d, label: `${label || 'hoy'} a las ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}` };
  }
  if (day) return { date: endOfDay(day), label: `${label} (fin del día)` };
  return { date: null, label: '' };
}

// Lo que se puede leer del mensaje. Lo que falte queda en `missing`.
export function parseMessage(rawText, now = new Date()) {
  const text = normalize(rawText);
  const asset = (ASSETS.find(([re]) => re.test(text)) || [null, 'BTCUSDT'])[1];
  const nums = numbers(text);
  const prices = nums.filter((n) => n.kind === 'price' && n.value >= 1);
  const pct = nums.find((n) => n.kind === 'percent');
  let probability = null;
  if (pct) probability = pct.value > 1 ? pct.value / 100 : pct.value;
  if (probability !== null && !(probability >= 0 && probability <= 1)) probability = null;
  const out = { asset, event: 'touch', levels: prices.map((n) => n.value), probability,
                hint: null, true_if: null, ...deadline(text, now), missing: [] };
  if (/\bcierr|\bcerrar|\bcierre\b/.test(text)) out.event = 'close_beyond';
  if (prices.length >= 2) {
    const between = text.slice(prices[0].end, prices[1].index);
    if (/antes (que|de que)/.test(between)) {
      out.event = 'touch_first';
      out.true_if = prices[0].value < prices[1].value ? 'lower_first' : 'upper_first';
      out.levels = [prices[0].value, prices[1].value];
    }
  }
  if (out.event !== 'touch_first') out.levels = out.levels.slice(0, 1);
  if (/\b(debajo|abajo|bajo|menos)\b/.test(text)) out.hint = 'down';
  else if (/\b(encima|arriba|sobre|mas)\b/.test(text)) out.hint = 'up';
  else if (DOWN.test(text)) out.hint = 'down';
  else if (UP.test(text)) out.hint = 'up';
  if (!out.levels.length) out.missing.push('nivel');
  if (out.probability === null) out.missing.push('probabilidad');
  if (!out.date) out.missing.push('fecha límite');
  return out;
}

function iso(ms) {
  return new Date(ms).toISOString().replace('.000Z', 'Z');
}

// Concepto exacto que valida concepts.validate_concept. `fields` sale de
// parseMessage y lo que la persona corrija en el formulario.
export function buildConcept(fields, reference, source, now = new Date()) {
  const errors = [];
  const warnings = [];
  if (!(reference > 0)) errors.push('Falta el precio de referencia observado.');
  if (!source || !source.trim()) errors.push('Falta de dónde salió el precio de referencia.');
  if (!fields.date) errors.push('Falta la fecha límite.');
  if (!fields.levels || !fields.levels.length) errors.push('Falta el nivel.');
  if (errors.length) return { concept: null, errors, warnings };
  const span = fields.date.getTime() - now.getTime();
  const interval = span <= 6 * HOUR ? '1m' : '15m';
  const step = STEP_MS[interval];
  const start = Math.ceil((now.getTime() + LEAD_MS) / step) * step;
  const end = Math.floor(fields.date.getTime() / step) * step;
  if (end <= start) {
    return { concept: null, errors: ['La fecha límite queda antes del inicio de la ventana: dale más plazo.'], warnings };
  }
  const concept = { asset: fields.asset, event: fields.event, interval, start: iso(start),
                    horizon_end: iso(end), reference_price: reference,
                    reference_source: source.trim() };
  if (fields.event === 'touch_first') {
    if (fields.levels.length < 2) return { concept: null, errors: ['"Toca X antes que Y" necesita dos niveles.'], warnings };
    const [a, b] = fields.levels;
    concept.lower = Math.min(a, b);
    concept.upper = Math.max(a, b);
    concept.true_if = fields.true_if || (a < b ? 'lower_first' : 'upper_first');
    if (!(concept.lower < reference && reference < concept.upper)) {
      errors.push('El precio de referencia tiene que estar entre los dos niveles.');
    }
  } else {
    const level = fields.levels[0];
    concept.level = level;
    const up = fields.event === 'touch' ? 'at_or_above' : 'above';
    const down = fields.event === 'touch' ? 'at_or_below' : 'below';
    concept.side = fields.side || (level > reference ? up : level < reference ? down
      : (fields.hint === 'down' ? down : up));
    if (fields.event === 'touch' && ((concept.side === 'at_or_above' && reference >= level)
        || (concept.side === 'at_or_below' && reference <= level))) {
      errors.push('El precio ya está ahí: no es una predicción. Elige otro nivel.');
    }
    if (fields.hint === 'up' && concept.side.endsWith('below')) warnings.push('El mensaje suena a subida, pero el nivel está por debajo del precio.');
    if (fields.hint === 'down' && concept.side.endsWith('above')) warnings.push('El mensaje suena a bajada, pero el nivel está por encima del precio.');
  }
  return { concept: errors.length ? null : concept, errors, warnings, startMs: start };
}

// JSON solo ASCII: así PowerShell 5 no le añade BOM ni rompe tildes.
export function asciiJson(value) {
  return JSON.stringify(value).replace(/[\u0080-￿]/g,
    (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}

export function psQuote(text) {
  return "'" + String(text).replace(/[\r\n]+/g, ' ').replace(/'/g, "''") + "'";
}

// Comandos de PowerShell: escribir el concepto en state\ y sellar.
export function sealCommands({ concept, author, probability, rationale, ledger }) {
  const write = `New-Item -ItemType Directory -Force state | Out-Null\n@'\n${asciiJson(concept)}\n'@ | Out-File -Encoding ascii state\\concepto.json`;
  const base = `python prediction_ledger.py --ledger ${psQuote(ledger)} seal --concept "@state/concepto.json"`
    + ` --author ${psQuote(author)} --probability ${probability} --rationale ${psQuote(rationale || 'sin motivo')}`;
  return { dryRun: `${write}\n${base} --dry-run`, seal: base };
}
