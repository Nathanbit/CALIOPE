// node --test tools/panel/parse.test.mjs  (con TZ=America/Guayaquil)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseMessage, buildConcept, numbers, normalize, asciiJson, psQuote, sealCommands } from './parse.js';

// Viernes 23 oct 2026, 09:00 en Guayaquil (14:00Z).
const NOW = new Date('2026-10-23T14:00:00Z');
const SRC = 'Binance API ticker/price BTCUSDT 2026-10-23T14:00:00Z';

test('el mensaje de hoy: toca 82.500 hoy con 60%', () => {
  const f = parseMessage('Creo que BTC sube hasta 82.500 hoy, 60%', NOW);
  assert.deepEqual([f.asset, f.event, f.levels, f.probability, f.missing], ['BTCUSDT', 'touch', [82500], 0.6, []]);
  assert.equal(f.date.toISOString(), '2026-10-24T05:00:00.000Z'); // medianoche local
  const { concept, errors } = buildConcept(f, 81600, SRC, NOW);
  assert.deepEqual(errors, []);
  assert.deepEqual(concept, {
    asset: 'BTCUSDT', event: 'touch', interval: '15m', start: '2026-10-23T14:15:00Z',
    horizon_end: '2026-10-24T05:00:00Z', reference_price: 81600, reference_source: SRC,
    level: 82500, side: 'at_or_above' });
});

test('formas de decir el número', () => {
  for (const [text, value] of [['82.500', 82500], ['82,500', 82500], ['82500', 82500], ['82k500', 82500],
                               ['82.5k', 82500], ['82 mil 500', 82500], ['82 mil quinientos', 82500], ['80 mil', 80000]]) {
    assert.equal(numbers(normalize(text))[0].value, value, text);
  }
  assert.equal(parseMessage('btc a 80 mil, probabilidad de 30', NOW).probability, 0.3);
  assert.equal(parseMessage('btc toca 80000 con 40 por ciento', NOW).probability, 0.4);
});

test('plazos', () => {
  const at = (t) => parseMessage(`btc toca 83000 ${t} 50%`, NOW).date.toISOString();
  assert.equal(at('mañana'), '2026-10-25T05:00:00.000Z');
  assert.equal(at('el domingo'), '2026-10-26T05:00:00.000Z');
  assert.equal(at('en 4 horas'), '2026-10-23T18:00:00.000Z');
  assert.equal(at('hoy a las 7 de la noche'), '2026-10-24T00:00:00.000Z');
  assert.equal(at('mañana a las 9 de la mañana'), '2026-10-24T14:00:00.000Z');
  assert.equal(at('a las 19:30'), '2026-10-24T00:30:00.000Z');
  assert.deepEqual(parseMessage('btc toca 83000 50%', NOW).missing, ['fecha límite']);
});

test('plazo corto usa velas de 1 minuto y el inicio deja margen', () => {
  const f = parseMessage('btc toca 82000 en 2 horas 55%', NOW);
  const { concept } = buildConcept(f, 81600, SRC, NOW);
  assert.equal(concept.interval, '1m');
  assert.equal(concept.start, '2026-10-23T14:03:00Z');
});

test('cierre, toca primero y bajadas', () => {
  const c = buildConcept(parseMessage('btc cierra por debajo de 80.000 mañana 35%', NOW), 81600, SRC, NOW).concept;
  assert.deepEqual([c.event, c.side, c.level], ['close_beyond', 'below', 80000]);
  const f = parseMessage('btc toca 80.000 antes que 83.000 el domingo 40%', NOW);
  const t = buildConcept(f, 81600, SRC, NOW).concept;
  assert.deepEqual([t.event, t.lower, t.upper, t.true_if], ['touch_first', 80000, 83000, 'lower_first']);
  assert.equal(buildConcept(parseMessage('btc cae a 79k hoy 50%', NOW), 81600, SRC, NOW).concept.side, 'at_or_below');
});

test('nunca acepta lo que ya pasó ni inventa el precio', () => {
  const f = parseMessage('btc toca 81.000 hoy 50%', NOW);
  assert.match(buildConcept({ ...f, side: 'at_or_above' }, 81600, SRC, NOW).errors[0], /ya está ahí/);
  assert.match(buildConcept(f, null, SRC, NOW).errors[0], /precio de referencia/);
  assert.match(buildConcept(f, 81600, ' ', NOW).errors[0], /de dónde/);
  const between = buildConcept(parseMessage('btc toca 80000 antes que 81000 hoy 50%', NOW), 81600, SRC, NOW);
  assert.match(between.errors[0], /entre los dos niveles/);
});

test('comandos seguros para PowerShell', () => {
  assert.equal(asciiJson({ s: 'según' }), '{"s":"seg\\u00fan"}');
  assert.equal(psQuote("it's $x\nok"), "'it''s $x ok'");
  const { concept } = buildConcept(parseMessage('btc toca 82500 hoy 60%', NOW), 81600, SRC, NOW);
  const cmd = sealCommands({ concept, author: 'nathan', probability: 0.6, rationale: 'rebote', ledger: 'projects/macroflow/predictions/ledger.jsonl' });
  assert.match(cmd.dryRun, /^New-Item -ItemType Directory -Force state/);
  assert.match(cmd.dryRun, /Out-File -Encoding ascii state\\concepto\.json\npython prediction_ledger\.py .* --dry-run$/);
  assert.ok(!cmd.seal.includes('--dry-run'));
});

test('un setup con objetivo y stop es "toca el objetivo antes que el stop"', () => {
  const long = parseMessage('setup largo entrada 80.500, objetivo 83.000, stop 79.500, hoy 65%', NOW);
  assert.deepEqual([long.event, long.levels, long.true_if, long.probability, long.setup],
                   ['touch_first', [83000, 79500], 'upper_first', 0.65, true]);
  const c = buildConcept(long, 80600, SRC, NOW).concept;
  assert.deepEqual([c.lower, c.upper, c.true_if], [79500, 83000, 'upper_first']);
  const short = parseMessage('corto btc tp 78k sl 81.2k mañana 55%', NOW);
  assert.deepEqual([short.levels, short.true_if], [[78000, 81200], 'lower_first']);
});
