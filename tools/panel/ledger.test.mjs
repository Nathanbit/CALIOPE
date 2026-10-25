// node --test tools/panel/ledger.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseLedger, indexLedger, scoredPairs, scoreboard, calibration, questionStatus, untilText, resolveCommands, forecastLabel, SWARM } from './ledger.js';

const q = (id, extra = {}) => ({ type: 'question', question_id: id, text: id, horizon_end: '2026-10-25T05:00:00Z',
  rule: { kind: 'touch', symbol: 'BTCUSDT', interval: '15m', start: '2026-10-24T14:45:00Z', level: 79600, side: 'at_or_below' }, ...extra });
const f = (id, author, p, seq, status = 'ok') => ({ type: 'forecast', question_id: id, author, probability: p, status, seq });
const r = (id, outcome, vol, boot) => ({ type: 'resolution', question_id: id, outcome,
  observed: { ...(vol === undefined ? {} : { volatility_rival: { available: true, p: vol } }),
              ...(boot === undefined ? {} : { bootstrap_rival: { available: true, p: boot } }) } });

const RECORDS = [q('q-a'), q('q-b'), q('q-c'), f('q-a', 'nathan', 0.2, 3), f('q-a', 'claude', 0.65, 4),
  f('q-a', 'nathan', 0.9, 5), f('q-b', 'nathan', 0.7, 6), f('q-c', 'claude', null, 7, 'failure'),
  r('q-a', 'true', 0.7, 0.9), r('q-b', 'false'), r('q-c', 'void')];

test('solo el primer pronóstico de cada autor puntúa; void y fallos no', () => {
  const pairs = scoredPairs(indexLedger(RECORDS));
  const own = pairs.filter((x) => x.author !== SWARM).map((x) => [x.qid, x.author, x.p, x.y]);
  assert.deepEqual(own, [['q-a', 'nathan', 0.2, 1], ['q-a', 'claude', 0.65, 1], ['q-b', 'nathan', 0.7, 0]]);
});

test('el enjambre es la media en log-odds de los autores de la pregunta', () => {
  const [swarm] = scoredPairs(indexLedger(RECORDS)).filter((x) => x.author === SWARM);
  const logit = (p) => Math.log(p / (1 - p));
  assert.equal(swarm.qid, 'q-a');
  assert.ok(Math.abs(swarm.p - 1 / (1 + Math.exp(-(logit(0.2) + logit(0.65)) / 2))) < 1e-12);
  assert.deepEqual(swarm.members, ['claude', 'nathan']);
});

test('marcador: Brier, moneda y rival de volatilidad en las mismas preguntas', () => {
  const board = scoreboard(scoredPairs(indexLedger(RECORDS)));
  const nathan = board.find((x) => x.author === 'nathan');
  assert.ok(Math.abs(nathan.brier - ((0.2 - 1) ** 2 + 0.7 ** 2) / 2) < 1e-12);
  assert.equal(nathan.coin, 0.25);
  assert.equal(nathan.volN, 1);
  assert.ok(Math.abs(nathan.volBrier - 0.09) < 1e-12);
  assert.ok(Math.abs(nathan.ownOnVol - 0.64) < 1e-12);
  assert.equal(nathan.bootN, 1);
  assert.ok(Math.abs(nathan.bootBrier - 0.01) < 1e-12);
  assert.equal(board[0].author, 'claude', 'ordenado por Brier');
});

test('calibración por tramos', () => {
  const rows = calibration(scoredPairs(indexLedger(RECORDS)), 'nathan');
  assert.deepEqual(rows.map((x) => [x.label, x.n, x.observed]), [['0.2-0.4', 1, 1], ['0.6-0.8', 1, 0]]);
});

test('estado y cuenta regresiva', () => {
  const question = q('q-x');
  assert.equal(questionStatus(question, null, new Date('2026-10-24T14:30:00Z')), 'abre pronto');
  assert.equal(questionStatus(question, null, new Date('2026-10-24T20:00:00Z')), 'en curso');
  assert.equal(questionStatus(question, null, new Date('2026-10-25T06:00:00Z')), 'por resolver');
  assert.equal(questionStatus(question, { outcome: 'void' }), 'anulada');
  assert.equal(untilText('2026-10-25T05:00:00Z', new Date('2026-10-24T03:30:00Z')), '1 d 1 h 30 min');
  assert.equal(untilText('2026-10-24T00:00:00Z', new Date('2026-10-25T00:00:00Z')), 'ya pasó');
});

test('comandos para resolver, en la carpeta data del mismo proyecto', () => {
  const cmd = resolveCommands(q('q-acbe0f5bc6ad9833'), 'projects/macroflow/predictions/ledger.jsonl');
  assert.match(cmd, /fetch_klines\.py --symbol BTCUSDT --interval 15m --start 2026-10-24T14:45:00Z --end 2026-10-25T05:00:00Z --out projects\/macroflow\/predictions\/data\/BTCUSDT_15m_20261024_acbe0f5b\.json/);
  assert.match(cmd, /resolve-auto --question q-acbe0f5bc6ad9833 --klines projects\/macroflow/);
  assert.equal(resolveCommands({ ...q('q-y'), rule: null }, 'x'), null);
});

test('lee el ledger real del repositorio', () => {
  const records = parseLedger(readFileSync(new URL('../../projects/macroflow/predictions/ledger.jsonl', import.meta.url), 'utf8'));
  const idx = indexLedger(records);
  assert.ok(idx.questions.size >= 3);
  assert.ok(idx.forecasts.get('q-acbe0f5bc6ad9833').some((x) => x.author === 'nathan'));
});

test('las preguntas de rango no se puntúan como sí o no y se muestran como banda', () => {
  const recs = [q('q-v'), { type: 'forecast', question_id: 'q-v', author: 'nathan', probability: null, status: 'ok', seq: 2,
    quantiles: { '0.1': 78000, '0.5': 80500, '0.9': 83000 } }, { type: 'resolution', question_id: 'q-v', outcome: 'value', value: 81000, observed: {} }];
  assert.deepEqual(scoredPairs(indexLedger(recs)), []);
  assert.equal(forecastLabel(recs[1]), '78.0k–83.0k');
  assert.equal(forecastLabel({ status: 'ok', probability: 0.65 }), '65%');
  assert.equal(forecastLabel({ status: 'failure' }), 'fallo');
});
