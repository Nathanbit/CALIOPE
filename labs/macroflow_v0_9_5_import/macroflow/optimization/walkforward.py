from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    k: int
    train: slice
    test: slice


def walkforward_folds(n: int, train_size: int, test_size: int, step: int | None = None,
                      embargo: int = 0, anchored: bool = False, warmup: int = 0) -> list[Fold]:
    """Ventanas walk-forward sobre n barras.

    embargo: barras descartadas entre train y test para que indicadores y trades
    abiertos al final del train no contaminen el test.
    warmup: barras extra que se ANTEPONEN al test solo para calentar indicadores
    (los trades que empiezan en ellas se descartan aguas abajo).
    """
    step = step or test_size
    folds, start, k = [], 0, 0
    while True:
        tr_start = 0 if anchored else start
        tr_end = start + train_size
        te_start = tr_end + embargo
        te_end = te_start + test_size
        if te_end > n:
            break
        folds.append(Fold(k, slice(tr_start, tr_end), slice(max(0, te_start - warmup), te_end)))
        start += step
        k += 1
    if not folds:
        raise ValueError(f"No caben folds: n={n}, train={train_size}, test={test_size}, embargo={embargo}")
    return folds
