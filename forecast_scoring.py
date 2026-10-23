"""Read-only scoring of a CALIOPE prediction ledger.

Brier and log scores per author, coin and base-rate baselines, Murphy
decomposition, ``learn`` (first recursion level: calibration proposals) and
``score_lessons`` (second level: does following a lesson help?). Nothing
here writes to a ledger; outputs are observation-only proposals.

Standard library only.
"""
from __future__ import annotations

import math

import prediction_ledger as pl


def scored_pairs(records: list[dict]) -> tuple[list[dict], dict]:
    """One forecast per author and question: the first one sealed. Later
    forecasts by the same author are counted but never scored, so repeating
    a forecast cannot improve a score."""
    questions, forecasts, resolutions = pl.index(records)
    pairs = []
    counts = {"questions": len(questions), "open": 0, "void": 0, "resolved": 0,
              "failures": 0, "repeat_forecasts_ignored": 0}
    failures_by_author: dict[str, int] = {}
    for qid in questions:
        resolution = resolutions.get(qid)
        if resolution is None:
            counts["open"] += 1
            continue
        if resolution["outcome"] == "void":
            counts["void"] += 1
            continue
        counts["resolved"] += 1
        y = 1.0 if resolution["outcome"] == "true" else 0.0
        seen: set[str] = set()
        for item in sorted(forecasts.get(qid, []), key=lambda r: r["seq"]):
            if item["author"] in seen:
                counts["repeat_forecasts_ignored"] += 1
                continue
            seen.add(item["author"])
            if item["status"] == pl.FAILURE:
                counts["failures"] += 1
                failures_by_author[item["author"]] = failures_by_author.get(item["author"], 0) + 1
                continue
            pairs.append({"author": item["author"], "p": float(item["probability"]), "y": y,
                          "base_rate": questions[qid].get("base_rate"), "question_id": qid,
                          "seq": item["seq"], "lessons": item.get("lessons_applied") or [],
                          "concept_class": questions[qid].get("concept_class")})
    counts["failures_by_author"] = failures_by_author
    return pairs, counts


def brier(pairs: list[dict]) -> float | None:
    return sum((x["p"] - x["y"]) ** 2 for x in pairs) / len(pairs) if pairs else None


def log_score(pairs: list[dict], eps: float = 1e-6) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for x in pairs:
        p = min(max(x["p"], eps), 1 - eps)
        total += math.log(p if x["y"] == 1.0 else 1 - p)
    return total / len(pairs)


def _bin(p: float, bins: int) -> int:
    return min(int(p * bins), bins - 1)


def _bin_label(key: int, bins: int) -> str:
    return f"{key / bins:.1f}-{(key + 1) / bins:.1f}"


def _grouped(pairs: list[dict], bins: int) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for x in pairs:
        groups.setdefault(_bin(x["p"], bins), []).append(x)
    return groups


def murphy(pairs: list[dict], bins: int = 10) -> dict | None:
    """Brier = REL - RES + UNC (+ within-bin terms when forecasts share a bin
    but differ). Reports the residual so the identity is checkable."""
    if not pairs:
        return None
    n = len(pairs)
    base = sum(x["y"] for x in pairs) / n
    rel = res = ece = 0.0
    table = []
    for key, group in sorted(_grouped(pairs, bins).items()):
        mean_p = sum(x["p"] for x in group) / len(group)
        freq = sum(x["y"] for x in group) / len(group)
        rel += len(group) * (mean_p - freq) ** 2
        res += len(group) * (freq - base) ** 2
        ece += len(group) * abs(mean_p - freq)
        table.append({"bin": _bin_label(key, bins), "n": len(group),
                      "mean_forecast": round(mean_p, 4), "observed_freq": round(freq, 4)})
    rel, res, unc = rel / n, res / n, base * (1 - base)
    score_value = brier(pairs)
    return {"brier": score_value, "reliability": rel, "resolution": res, "uncertainty": unc,
            "within_bin_residual": score_value - (rel - res + unc), "bins": table, "ece": ece / n}


def score(records: list[dict]) -> dict:
    pairs, counts = scored_pairs(records)
    board = []
    for author in sorted({x["author"] for x in pairs} | set(counts["failures_by_author"])):
        own = [x for x in pairs if x["author"] == author]
        failures = counts["failures_by_author"].get(author, 0)
        board.append({"author": author, "n": len(own), "brier": brier(own),
                      "log_score": log_score(own), "failures": failures,
                      "failure_rate": failures / (len(own) + failures)})
    with_base = [x for x in pairs if x["base_rate"] is not None]
    report = {"counts": counts, "scoreboard": board,
              "baselines": {
                  "coin": {"n": len(pairs), "brier": brier([dict(x, p=0.5) for x in pairs])},
                  "base_rate": {"n": len(with_base),
                                "brier": brier([dict(x, p=x["base_rate"]) for x in with_base]),
                                "authors_on_same_questions": brier(with_base)}},
              "calibration": murphy(pairs), "by_concept": by_concept(pairs)}
    if len(pairs) < 30:
        report["warning"] = (f"only {len(pairs)} scored forecasts: calibration and skill are "
                             "not statistically meaningful yet")
    return report


def by_concept(pairs: list[dict]) -> list[dict]:
    """Outcomes per concept class: how often this *kind* of event happens
    (the empirical base rate) and how each author did on it."""
    rows = []
    for label in sorted({x["concept_class"] for x in pairs if x["concept_class"]}):
        group = [x for x in pairs if x["concept_class"] == label]
        rows.append({"concept_class": label, "n": len(group),
                     "observed_rate": sum(x["y"] for x in group) / len(group),
                     "brier": brier(group),
                     "authors": {a: brier([x for x in group if x["author"] == a])
                                 for a in sorted({x["author"] for x in group})}})
    return rows


def learn(records: list[dict], min_n: int = 30, prior_strength: float = 20.0,
          bins: int = 5, group_by: str = "author") -> dict:
    """Per author (or per author and concept class) and probability bin,
    compare stated confidence with observed frequency. Shrinks the observed
    frequency toward the stated probability with ``prior_strength``
    pseudo-observations, and only proposes an adjustment once a bin has
    ``min_n`` resolved forecasts. ``group_by="concept"`` learns per kind of
    prediction, so calibration transfers between questions of the same class
    (the LCM idea). Output is an observation-only proposal."""
    if group_by not in ("author", "concept"):
        raise ValueError("group_by must be 'author' or 'concept'")
    pairs, counts = scored_pairs(records)
    if group_by == "concept":
        pairs = [x for x in pairs if x["concept_class"]]
    keys = sorted({(x["author"], x["concept_class"] if group_by == "concept" else None)
                   for x in pairs}, key=lambda k: (k[0], k[1] or ""))
    lessons = []
    for author, label in keys:
        own = [x for x in pairs if x["author"] == author
               and (group_by == "author" or x["concept_class"] == label)]
        for key, group in sorted(_grouped(own, bins).items()):
            n = len(group)
            stated = sum(x["p"] for x in group) / n
            hits = sum(x["y"] for x in group)
            shrunk = (hits + prior_strength * stated) / (n + prior_strength)
            ready = n >= min_n
            subject = f"{author} on {label}" if label else author
            lessons.append({
                "evidence_questions": sorted({x["question_id"] for x in group}),
                "author": author, "concept_class": label, "bin": _bin_label(key, bins), "n": n,
                "stated_mean": round(stated, 4), "observed_freq": round(hits / n, 4),
                "shrunk_freq": round(shrunk, 4), "ready": ready,
                "proposal": (f"When {subject} states ~{stated:.0%}, treat it as ~{shrunk:.0%}"
                             if ready else f"gathering evidence ({n}/{min_n})"),
            })
    return {"evidence_type": "inference", "observation_only": True, "automatic_edit": False,
            "requires_human_decision": True, "min_n": min_n, "prior_strength": prior_strength,
            "group_by": group_by, "counts": counts, "lessons": lessons}


def score_lessons(records: list[dict], min_n: int = 30, margin: float = 0.01) -> dict:
    """Second recursion level: does following a lesson improve later forecasts?

    ``before``: scored forecasts in the lesson's scope sealed before adoption,
    excluding the questions cited as the lesson's own evidence (they were
    chosen because they went badly, which would flatter the lesson).
    ``after``: scored forecasts that cite the lesson. Verdicts need ``min_n``
    on both sides; the output only proposes."""
    pairs, _ = scored_pairs(records)
    report = []
    for lid, entry in pl.lessons_index(records).items():
        adoption = next((d for d in entry["decisions"] if d["decision"] == "adopt"), None)
        lesson = entry["lesson"]
        row = {"lesson_id": lid, "text": lesson["text"], "scope": lesson["scope"],
               "status": pl.lesson_status(entry)}
        if adoption is None:
            report.append(dict(row, verdict="not_adopted"))
            continue
        evidence = set(lesson["evidence_questions"])
        in_scope = [x for x in pairs if lesson["scope"] in ("*", x["author"])]
        before = [x for x in in_scope
                  if x["seq"] < adoption["seq"] and x["question_id"] not in evidence]
        after = [x for x in in_scope if lid in x["lessons"]]
        b_before, b_after = brier(before), brier(after)
        delta = None if b_before is None or b_after is None else b_after - b_before
        if len(before) < min_n or len(after) < min_n:
            verdict = "insufficient"
        elif delta < -margin:
            verdict = "helps"
        elif delta > margin:
            verdict = "hurts"
        else:
            verdict = "neutral"
        report.append(dict(row, n_before=len(before), n_after=len(after), brier_before=b_before,
                           brier_after=b_after, delta=delta, verdict=verdict,
                           proposal=("propose retiring it: forecasts got worse"
                                     if verdict == "hurts" else None)))
    return {"evidence_type": "inference", "observation_only": True,
            "requires_human_decision": True, "min_n": min_n, "margin": margin,
            "lessons": report}
