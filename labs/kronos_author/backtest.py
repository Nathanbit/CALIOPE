#!/usr/bin/env python3
"""Replay Kronos on past candles: how often did reality fall inside its fan?

    python labs/kronos_author/backtest.py --klines <velas pasadas>.json `
      --lookback 128 --horizon 48 --windows 30 --stop-pct 0.01 --target-pct 0.02

At each past cut point (non-overlapping, latest first) Kronos sees only the
``--lookback`` candles before it and draws ``--paths`` futures one by one.
The report says how often the real close ``--horizon`` candles later fell
inside the p10-p90 of those futures (80% if Kronos is calibrated), with a 95%
interval. With ``--stop-pct`` and ``--target-pct`` (long side, as fractions
of the last close) it also scores "target before stop" with Brier against a
coin (0.25).

This is a check before trusting Kronos, not a score: it never touches the
ledger, and only sealed forecasts count in ``rival``. Its output is a
``derivation`` from model outputs and observed candles.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kronos_core as kc  # noqa: E402
import propose  # noqa: E402

sys.path.insert(0, str(kc.ROOT))
import evidence_io as eio  # noqa: E402
import resolver  # noqa: E402


def make_sampler_for(args: argparse.Namespace):
    predictor = propose.load_predictor(args)
    return lambda history, future: propose.sampler_from(predictor, args, history, future)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--klines", required=True, type=Path)
    propose.add_model_args(parser)
    parser.add_argument("--lookback", type=int, default=128)
    parser.add_argument("--horizon", type=int, default=48)
    parser.add_argument("--windows", type=int, default=30)
    parser.add_argument("--stop-pct", type=float)
    parser.add_argument("--target-pct", type=float)
    parser.add_argument("--out", help="also write the report to runs/kronos/<name>.json")
    args = parser.parse_args(argv)
    try:
        out = propose.runs_path(args.out) if args.out else None
        data, data_sha = resolver.load_klines(args.klines)
        report = kc.replay(data, make_sampler_for(args), lookback=args.lookback, horizon=args.horizon,
                           paths=args.paths, batch=args.batch, windows=args.windows,
                           stop_pct=args.stop_pct, target_pct=args.target_pct)
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"falta {exc.name}: ver labs/kronos_author/README.md"}))
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    report = {"ok": True, "model": args.model, "seed": args.seed, "paths": args.paths,
              "lookback": args.lookback, "horizon": args.horizon, "data_sha256": data_sha,
              "evidence_type": "derivation", **report}
    if out is not None:
        eio.atomic_write_json(out, report, kc.KronosAuthorError, "could not write the report")
        report["report_file"] = out.relative_to(kc.ROOT).as_posix()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
