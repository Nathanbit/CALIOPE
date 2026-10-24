// Responde evals/predict/cases.json con parse.js (el intérprete sin IA), para
// calificarlo con tools/eval_predict.py. Uso:
//   TZ=America/Guayaquil node tools/panel/answer_cases.mjs > state/answers.json
import { readFileSync } from 'node:fs';
import { parseMessage, buildConcept } from './parse.js';

const cases = JSON.parse(readFileSync(new URL('../../evals/predict/cases.json', import.meta.url), 'utf8'));
const now = new Date(cases.now);
const answers = {};
for (const c of cases.cases) {
  const fields = parseMessage(c.message, now);
  const { concept } = buildConcept(fields, c.reference_price, 'eval', now);
  answers[c.id] = { concept, probability: fields.probability };
}
console.log(JSON.stringify(answers, null, 2));
