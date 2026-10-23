"""Concepts: a prediction expressed as structure, not as free text.

Idea adapted from Meta FAIR's Large Concept Model (LCM, arXiv 2412.08821),
which reasons over whole-sentence "concepts" instead of tokens. CALIOPE
cannot host such a model (standard library only, no weights); it keeps the
architectural idea instead:

1. A forecast is captured as a *concept*: asset, event, levels, window and
   the observed reference price. Wording is generated from the concept
   deterministically, so two people saying the same thing produce the same
   question, and nobody can smuggle ambiguity into the text.
2. Every concept belongs to a *concept class* that drops the exact numbers
   (asset, event, side, distance bucket, horizon bucket). Learning and
   lessons group by class, so calibration generalizes across questions that
   mean the same kind of thing.

A language model may turn a person's message into a concept (it proposes);
``validate_concept`` and the ledger gates verify; the person confirms.

Standard library only.
"""
from __future__ import annotations

import math
import re

import evidence_io as eio
import resolver

CONCEPT_VERSION = "1.0"
EVENTS = ("touch_first", "touch", "close_beyond")
KEYS = {
    "touch_first": {"asset", "event", "interval", "start", "horizon_end", "reference_price",
                    "reference_source", "lower", "upper", "true_if"},
    "touch": {"asset", "event", "interval", "start", "horizon_end", "reference_price",
              "reference_source", "level", "side"},
    "close_beyond": {"asset", "event", "interval", "start", "horizon_end", "reference_price",
                     "reference_source", "level", "side"},
}
DISTANCE_BUCKETS = ((0.5, "d<0.5%"), (1.0, "d0.5-1%"), (2.0, "d1-2%"), (5.0, "d2-5%"),
                    (math.inf, "d>5%"))
HORIZON_BUCKETS = ((6, "h<=6h"), (24, "h<=24h"), (72, "h<=3d"), (168, "h<=7d"),
                   (math.inf, "h>7d"))
SIDE_WORDS = {"at_or_below": "a o por debajo de", "at_or_above": "a o por encima de",
              "below": "por debajo de", "above": "por encima de"}


class ConceptError(ValueError):
    """The concept is incomplete, inconsistent or not falsifiable."""


def _price(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise ConceptError(f"{field} must be a positive finite number")
    return float(value)


def _fmt(price: float) -> str:
    return f"{price:,.2f}".rstrip("0").rstrip(".")


def validate_concept(concept: dict) -> dict:
    """Strict keys, normalized values, rule that the resolver accepts."""
    if not isinstance(concept, dict):
        raise ConceptError("concept must be an object")
    event = concept.get("event")
    if event not in EVENTS:
        raise ConceptError(f"event must be one of {EVENTS}")
    keys = set(concept)
    if keys != KEYS[event]:
        raise ConceptError(f"concept keys for {event}: missing {sorted(KEYS[event] - keys)}, "
                           f"unexpected {sorted(keys - KEYS[event])}")
    source = concept["reference_source"]
    if not isinstance(source, str) or not source.strip():
        raise ConceptError("reference_source is required: where the reference price was observed")
    horizon = eio.iso_utc(eio.parse_aware_time(concept["horizon_end"], "horizon_end", ConceptError))
    rule = {"kind": event, "symbol": concept["asset"], "interval": concept["interval"],
            "start": concept["start"]}
    if event == "touch_first":
        rule.update(lower=concept["lower"], upper=concept["upper"], true_if=concept["true_if"])
    else:
        rule.update(level=concept["level"], side=concept["side"])
    try:
        rule = resolver.validate_rule(rule, horizon)
    except resolver.ResolutionError as exc:
        raise ConceptError(str(exc)) from None
    out = {"asset": rule["symbol"], "event": event, "interval": rule["interval"],
           "start": rule["start"], "horizon_end": horizon,
           "reference_price": _price(concept["reference_price"], "reference_price"),
           "reference_source": source.strip()}
    if event == "touch_first":
        out.update(lower=rule["lower"], upper=rule["upper"], true_if=rule["true_if"])
        if not out["lower"] < out["reference_price"] < out["upper"]:
            raise ConceptError("the reference price must lie between lower and upper, or the "
                               "question is already decided")
    else:
        out.update(level=rule["level"], side=rule["side"])
        already = {"at_or_below": out["reference_price"] <= out["level"],
                   "at_or_above": out["reference_price"] >= out["level"]}.get(out["side"])
        if already:
            raise ConceptError("the reference price already satisfies the event: not a prediction")
    return out


def rule_of(concept: dict) -> dict:
    rule = {"kind": concept["event"], "symbol": concept["asset"], "interval": concept["interval"],
            "start": concept["start"]}
    if concept["event"] == "touch_first":
        rule.update(lower=concept["lower"], upper=concept["upper"], true_if=concept["true_if"])
    else:
        rule.update(level=concept["level"], side=concept["side"])
    return resolver.validate_rule(rule, concept["horizon_end"])


def question_fields(concept: dict) -> dict:
    """Deterministic Spanish wording, source and criteria for a validated concept."""
    asset, interval = concept["asset"], concept["interval"]
    window = f"entre {concept['start']} y {concept['horizon_end']}"
    if concept["event"] == "touch_first":
        first, other = (("lower", "upper") if concept["true_if"] == "lower_first"
                        else ("upper", "lower"))
        verb = {"lower": "toca {} o menos", "upper": "toca {} o más"}
        text = (f"{asset} {verb[first].format(_fmt(concept[first]))} antes de que "
                f"{verb[other].format(_fmt(concept[other]))}, {window} (velas de {interval})")
        criteria = (f"Primer nivel tocado por mínimos/máximos de velas de {interval} en la ventana: "
                    f"{_fmt(concept[first])} primero = true, {_fmt(concept[other])} primero = false; "
                    "ninguno o ambos en la misma vela = void")
    elif concept["event"] == "touch":
        text = (f"{asset} cotiza {SIDE_WORDS[concept['side']]} {_fmt(concept['level'])} "
                f"{window} (velas de {interval})")
        extreme = "mínimo" if concept["side"] == "at_or_below" else "máximo"
        criteria = (f"true si el {extreme} de alguna vela de {interval} en la ventana alcanza "
                    f"{_fmt(concept['level'])}; si no, false")
    else:
        text = (f"{asset} cierra una vela de {interval} {SIDE_WORDS[concept['side']]} "
                f"{_fmt(concept['level'])} {window}")
        criteria = (f"true si alguna vela de {interval} cerrada en la ventana cierra "
                    f"{SIDE_WORDS[concept['side']]} {_fmt(concept['level'])}; una mecha no cuenta; "
                    "si no, false")
    return {"text": text, "source": f"BINANCE:{asset} {interval} klines", "criteria": criteria,
            "horizon_end": concept["horizon_end"], "rule": rule_of(concept)}


def _bucket(value: float, buckets: tuple) -> str:
    return next(label for limit, label in buckets if value <= limit)


def concept_class(concept: dict) -> str:
    """What kind of prediction this is, without the exact numbers."""
    if concept["event"] == "touch_first":
        target = concept["lower"] if concept["true_if"] == "lower_first" else concept["upper"]
        direction = concept["true_if"]
    else:
        target, direction = concept["level"], concept["side"]
    distance = abs(target - concept["reference_price"]) / concept["reference_price"] * 100
    start = eio.parse_aware_time(concept["start"], "start", ConceptError)
    end = eio.parse_aware_time(concept["horizon_end"], "horizon_end", ConceptError)
    hours = (end - start).total_seconds() / 3600
    return "|".join((concept["asset"], concept["event"], direction,
                     _bucket(distance, DISTANCE_BUCKETS), _bucket(hours, HORIZON_BUCKETS)))


def parse_class(label: str) -> dict:
    parts = label.split("|")
    if len(parts) != 5 or not re.fullmatch(r"[A-Z0-9]{2,30}", parts[0]):
        raise ConceptError(f"not a concept class: {label}")
    return dict(zip(("asset", "event", "direction", "distance", "horizon"), parts))
