"""Daily series for the causal lab: load, align and split. Standard library only.

Rules that keep the experiment honest:

- A day enters only if every series has a value that day. A missing value is
  never filled (no forward fill): it removes the day.
- Prices become log returns between consecutive kept days. Columns ending in
  ``_funding`` are already rates and stay as levels.
- The holdout (the last ``holdout_days`` aligned days) is fixed before any
  result is seen, and discovery never reads it.
"""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

TARGET = "btc"


class SeriesError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_csv(path: Path) -> tuple[list[str], dict[str, list[float | None]]]:
    """``date`` column plus one column per series; empty cells are missing."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "date" not in rows[0]:
        raise SeriesError("the CSV needs a 'date' column and at least one row")
    names = [c for c in rows[0] if c != "date"]
    if TARGET not in names:
        raise SeriesError(f"the CSV needs a '{TARGET}' column")
    dates, cols = [], {n: [] for n in names}
    previous = None
    for row in rows:
        date = row["date"]
        if previous is not None and date <= previous:
            raise SeriesError(f"dates must be strictly increasing: {date} after {previous}")
        previous = date
        dates.append(date)
        for n in names:
            cell = (row.get(n) or "").strip()
            if not cell:
                cols[n].append(None)
                continue
            value = float(cell)
            if not math.isfinite(value):
                raise SeriesError(f"{n} on {date} is not finite")
            cols[n].append(value)
    return dates, cols


def transform(dates: list[str], cols: dict[str, list[float | None]]) -> dict:
    """Aligned rows: date, and for each series its return (or level) for the
    interval that ends on that date."""
    names = list(cols)
    kept = [i for i in range(len(dates)) if all(cols[n][i] is not None for n in names)]
    for n in names:
        if not n.endswith("_funding") and any(cols[n][i] <= 0 for i in kept):
            raise SeriesError(f"{n} has non-positive prices: cannot take log returns")
    out = {"dates": [], "names": names, "values": {n: [] for n in names},
           "dropped_days": len(dates) - len(kept)}
    for a, b in zip(kept, kept[1:]):
        out["dates"].append(dates[b])
        for n in names:
            value = cols[n][b] if n.endswith("_funding") else math.log(cols[n][b] / cols[n][a])
            out["values"][n].append(value)
    return out


def split(data: dict, holdout_days: int) -> tuple[dict, dict]:
    n = len(data["dates"])
    if not 30 <= holdout_days <= n - 100:
        raise SeriesError(f"holdout_days must leave at least 100 discovery days (have {n})")
    cut = n - holdout_days

    def part(lo: int, hi: int) -> dict:
        return {"dates": data["dates"][lo:hi], "names": data["names"],
                "values": {k: v[lo:hi] for k, v in data["values"].items()}}
    return part(0, cut), part(cut, n)
