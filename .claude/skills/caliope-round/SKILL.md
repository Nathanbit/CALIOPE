---
name: caliope-round
description: Daily round of the CALIOPE desk firm. Checks what is due, collects the person's forecast, adds Claude's (and Kronos's) on the same question, and, only if the person wants to trade, takes a proposal through the risk officer to the person's own approval. Use when the person says "ronda", "ronda diaria", "qué toca hoy", "hagamos la ronda", or starts the day asking about BTC.
---

# Ronda diaria de la firma CALIOPE

La firma, con la idea de cbrock84/headcount (MIT): cada productor con un
auditor independiente y cada uno con su superficie de escritura.

| Puesto | Quién | Escribe | Frena |
|---|---|---|---|
| Analistas | la persona, `caliope-analyst` (Claude), Kronos | pronósticos y propuestas con su propio autor | — |
| Riesgo | `caliope-risk-officer` | nada | recomienda NO PROCEDE |
| Auditoría | `caliope-reviewer`, `caliope-fail-closed-hunter`, `caliope-security-reviewer` | nada | sus hallazgos bloquean un PR |
| Mesa | **la persona** | decisiones y envíos, en su terminal | todo |

## 1. Qué toca (sin escribir nada)

```bash
python prediction_ledger.py --ledger projects/macroflow/predictions/ledger.jsonl score
python -m desk status
```

Resume en 3 líneas: preguntas que **cerraron sin resolver** (con el comando
de `fetch_klines` + `resolve-auto` de la pestaña Preguntas del panel),
preguntas que **abren hoy** (último momento para pronosticar), y órdenes
**aprobadas sin enviar** o con resultado desconocido.

## 2. El pronóstico de la persona

Pídele una línea: nivel, plazo, probabilidad y el precio que ve ahora (o
"Leer de Binance" en el panel). Sella con la skill `caliope-predict`. Nunca
inventes el precio de referencia.

## 3. Los otros analistas, sobre la misma pregunta

- Delega en `caliope-analyst` el pronóstico de Claude (antes de que abra la
  ventana).
- Si la persona tiene Kronos instalado, dale el comando de
  `labs/kronos_author/propose.py` para esa pregunta.
- Si hay un mercado "Bitcoin above X" de Polymarket para ese día, ofrece
  `labs/polymarket/mirror.py`.

## 4. Solo si la persona quiere operar

1. Propuesta desde un pronóstico sellado, del autor que la persona elija
   (`python -m desk propose ...`, o `caliope-analyst` si es de Claude).
2. Delega en `caliope-risk-officer` la revisión de ese `order_id` y muestra
   su veredicto tal cual.
3. Dile a la persona los dos comandos que **ella** ejecuta en su terminal:
   `python -m desk approve --order o-...` y, si aprueba,
   `python -m desk submit --order o-...` antes de 2 minutos. Tú no los
   ejecutas: las guardas lo impiden y la decisión es suya.

## 5. Cierre

```bash
python caliope_loop.py doctor
```

Termina con: qué se selló, qué quedó pendiente y para cuándo, y lo que no
se pudo verificar. Ninguna recomendación de operar sin un pronóstico sellado
detrás.
