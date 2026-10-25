// Lectura del ledger para el panel (solo lectura, sin dependencias).
//
// Reproduce para la vista lo que hace forecast_scoring.py: el primer
// pronóstico válido de cada autor por pregunta es el que puntúa, `void` no
// puntúa y el enjambre es la media en log-odds recortada a [0.01, 0.99].
// La autoridad sigue siendo la terminal (`verify`, `score`, `rival`): aquí
// no se comprueba la cadena de hashes.

export const SWARM = '~enjambre';
const CLIP = 0.01;

export function parseLedger(text) {
  return text.split('\n').filter((line) => line.trim()).map((line, i) => {
    try { return JSON.parse(line); } catch { throw new Error(`línea ${i + 1} no es JSON`); }
  });
}

export function indexLedger(records) {
  const questions = new Map(), forecasts = new Map(), resolutions = new Map();
  for (const r of records) {
    if (r.type === 'question') questions.set(r.question_id, r);
    else if (r.type === 'forecast') {
      if (!forecasts.has(r.question_id)) forecasts.set(r.question_id, []);
      forecasts.get(r.question_id).push(r);
    } else if (r.type === 'resolution') resolutions.set(r.question_id, r);
  }
  for (const list of forecasts.values()) list.sort((a, b) => a.seq - b.seq);
  return { questions, forecasts, resolutions };
}

// Probabilidad de un rival guardado por resolve-auto, o null si no aplica.
const stored = (res, key) => (res.observed?.[key]?.available ? res.observed[key].p : null);

// Pares (autor, p, y) que puntúan, más el enjambre.
export function scoredPairs(index, minAuthors = 2) {
  const pairs = [];
  const swarm = [];
  for (const [qid, q] of index.questions) {
    const res = index.resolutions.get(qid);
    // Only yes/no questions score here; 'value' (range) questions have their own score.
    if (!res || (res.outcome !== 'true' && res.outcome !== 'false')) continue;
    const y = res.outcome === 'true' ? 1 : 0;
    const seen = new Set();
    const group = [];
    for (const f of index.forecasts.get(qid) || []) {
      if (seen.has(f.author)) continue;
      seen.add(f.author);
      if (f.status !== 'ok') continue;
      const pair = { author: f.author, p: f.probability, y, qid, cls: q.concept_class || null,
                     vol: stored(res, 'volatility_rival'), boot: stored(res, 'bootstrap_rival') };
      pairs.push(pair);
      group.push(pair);
    }
    if (new Set(group.map((x) => x.author)).size >= minAuthors) {
      const logit = (p) => { const c = Math.min(Math.max(p, CLIP), 1 - CLIP); return Math.log(c / (1 - c)); };
      const mean = group.reduce((s, x) => s + logit(x.p), 0) / group.length;
      swarm.push({ author: SWARM, p: 1 / (1 + Math.exp(-mean)), y, qid, cls: group[0].cls,
                   vol: group[0].vol, boot: group[0].boot, members: group.map((x) => x.author).sort() });
    }
  }
  return pairs.concat(swarm);
}

const brier = (xs) => xs.length ? xs.reduce((s, x) => s + (x.p - x.y) ** 2, 0) / xs.length : null;

export function scoreboard(pairs) {
  const authors = [...new Set(pairs.map((x) => x.author))];
  return authors.map((author) => {
    const own = pairs.filter((x) => x.author === author);
    const withVol = own.filter((x) => x.vol !== null);
    const withBoot = own.filter((x) => x.boot !== null);
    return { author, n: own.length, brier: brier(own),
             coin: brier(own.map((x) => ({ ...x, p: 0.5 }))),
             volN: withVol.length, volBrier: brier(withVol.map((x) => ({ ...x, p: x.vol }))),
             bootN: withBoot.length, bootBrier: brier(withBoot.map((x) => ({ ...x, p: x.boot }))),
             ownOnBoot: brier(withBoot),
             ownOnVol: brier(withVol), hits: own.filter((x) => (x.p >= 0.5) === (x.y === 1)).length };
  }).sort((a, b) => a.brier - b.brier || b.n - a.n);
}

// Tramos de calibración: confianza declarada contra frecuencia observada.
export function calibration(pairs, author, bins = 5) {
  const own = pairs.filter((x) => x.author === author);
  const out = [];
  for (let k = 0; k < bins; k++) {
    const group = own.filter((x) => Math.min(Math.floor(x.p * bins), bins - 1) === k);
    if (!group.length) continue;
    out.push({ bin: k, label: `${(k / bins).toFixed(1)}-${((k + 1) / bins).toFixed(1)}`, n: group.length,
               stated: group.reduce((s, x) => s + x.p, 0) / group.length,
               observed: group.reduce((s, x) => s + x.y, 0) / group.length });
  }
  return out;
}

// Estado de cada pregunta según la hora: antes de la ventana, dentro, por
// resolver o resuelta.
export function questionStatus(q, res, now = new Date()) {
  if (res) return res.outcome === 'void' ? 'anulada' : `resuelta: ${res.outcome}`;
  const end = new Date(q.horizon_end);
  const start = q.rule?.start ? new Date(q.rule.start) : null;
  if (now >= end) return 'por resolver';
  if (start && now < start) return 'abre pronto';
  return 'en curso';
}

export function untilText(target, now = new Date()) {
  let ms = new Date(target) - now;
  if (ms <= 0) return 'ya pasó';
  const d = Math.floor(ms / 86_400_000); ms -= d * 86_400_000;
  const h = Math.floor(ms / 3_600_000); ms -= h * 3_600_000;
  const m = Math.floor(ms / 60_000);
  return [d ? `${d} d` : '', h ? `${h} h` : '', `${m} min`].filter(Boolean).join(' ');
}

// Comandos de PowerShell para resolver una pregunta con regla.
export function resolveCommands(q, ledger) {
  if (!q.rule) return null;
  const r = q.rule;
  const stamp = r.start.slice(0, 10).replace(/-/g, '');
  const dir = ledger.replace(/\\/g, '/').replace(/[^/]+$/, '') + 'data';
  const out = `${dir}/${r.symbol}_${r.interval}_${stamp}_${q.question_id.slice(2, 10)}.json`;
  return `python tools/fetch_klines.py --symbol ${r.symbol} --interval ${r.interval} --start ${r.start} --end ${q.horizon_end} --out ${out}\n`
    + `python prediction_ledger.py --ledger '${ledger}' resolve-auto --question ${q.question_id} --klines ${out}`;
}

// Cómo se muestra un pronóstico: probabilidad, o banda 10–90 si es de rango.
export function forecastLabel(f) {
  if (f.status !== 'ok') return 'fallo';
  if (f.quantiles) {
    const k = (v) => `${(v / 1000).toFixed(1)}k`;
    return `${k(f.quantiles['0.1'])}–${k(f.quantiles['0.9'])}`;
  }
  return `${Math.round(f.probability * 100)}%`;
}
