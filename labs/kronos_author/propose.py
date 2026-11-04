#!/usr/bin/env python3
"""Propose a forecast by Kronos (ia:kronos) for a question in the ledger.

    python labs/kronos_author/propose.py --ledger projects/macroflow/predictions/ledger.jsonl `
      --question q-... --klines projects/macroflow/predictions/data/<velas hasta ahora>.json `
      --kronos-dir ..\\Kronos

It never writes to the ledger: it prints the probability, the counts behind
it, and the ``forecast`` command for the person to run. Run it before the
window starts. Needs PyTorch and a clone of shiyu-coder/Kronos (MIT); the
weights come from Hugging Face (NeoQuasar/Kronos-small by default).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kronos_core as kc  # noqa: E402

sys.path.insert(0, str(kc.ROOT))
import evidence_io as eio  # noqa: E402
import prediction_ledger as pl  # noqa: E402
import resolver  # noqa: E402


def load_predictor(args: argparse.Namespace):
    """Kronos weights, loaded once (backtest.py reuses them for every window)."""
    import torch  # noqa: PLC0415  (lab only)
    sys.path.insert(0, str(Path(args.kronos_dir).resolve()))
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: PLC0415

    torch.manual_seed(args.seed)
    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    return KronosPredictor(model, tokenizer, device=args.device, max_context=512)


def sampler_from(predictor, args: argparse.Namespace, history: list[dict], future: list[int]):
    import pandas as pd  # noqa: PLC0415  (lab only)
    df = pd.DataFrame([{k: c[k] for k in ("open", "high", "low", "close")} for c in history])
    x_ts = pd.Series(pd.to_datetime([c["open_ms"] for c in history], unit="ms"))
    y_ts = pd.Series(pd.to_datetime(future, unit="ms"))

    def sample(n: int) -> list[list[tuple[float, float, float, float]]]:
        # n copies, one sample each: independent paths, never their average
        frames = predictor.predict_batch([df] * n, [x_ts] * n, [y_ts] * n, pred_len=len(future),
                                         T=args.temperature, top_p=args.top_p, sample_count=1,
                                         verbose=False)
        return [list(zip(f["open"], f["high"], f["low"], f["close"])) for f in frames]
    return sample


def kronos_sampler(args: argparse.Namespace, history: list[dict], future: list[int]):
    return sampler_from(load_predictor(args), args, history, future)


def add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kronos-dir", default=str(kc.ROOT.parent / "Kronos"))
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--paths", type=int, default=200)
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=0)


def runs_path(raw: str) -> Path:
    """Where to keep paths: always under runs/kronos/ (ignored by git), never elsewhere."""
    base = (kc.ROOT / "runs" / "kronos").resolve()
    target = (base / raw).resolve()
    if base not in target.parents or target.suffix != ".json":
        raise kc.KronosAuthorError("--save-paths takes a .json file name inside runs/kronos/")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--question", required=True)
    parser.add_argument("--klines", required=True, type=Path)
    add_model_args(parser)
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument("--save-paths", help="keep every path in runs/kronos/<name>.json")
    args = parser.parse_args(argv)
    try:
        questions, _, _ = pl.index(pl.load(args.ledger))
        question = questions.get(args.question)
        if question is None or not question.get("rule"):
            raise kc.KronosAuthorError("unknown question, or one without a machine rule")
        data, data_sha = resolver.load_klines(args.klines)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        history, future = kc.history_and_future(question["rule"], question["horizon_end"], data,
                                                now_ms, args.lookback)
        save = runs_path(args.save_paths) if args.save_paths else None
        sample = kronos_sampler(args, history, future)
        rule = resolver.validate_rule(question["rule"], question["horizon_end"])
        if rule["kind"] == "close_value":
            quantiles, drawn = kc.value_quantiles(rule, question["horizon_end"], future, sample,
                                                  args.paths, args.batch)
            result = {"quantiles": quantiles, "paths": len(drawn)}
        else:
            drawn = kc.draw(future, sample, args.paths, args.batch)
            result = kc.count(rule, future, drawn)
        if save is not None:
            save.parent.mkdir(parents=True, exist_ok=True)
            eio.atomic_write_json(save, {"question": args.question, "data_sha256": data_sha,
                                         "model": args.model, "seed": args.seed, "future_open_ms": future,
                                         "paths": [[list(c) for c in p] for p in drawn]},
                                  kc.KronosAuthorError, "could not write the paths file")
            result["paths_file"] = save.relative_to(kc.ROOT).as_posix()
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"falta {exc.name}: ver labs/kronos_author/README.md"}))
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    setup = (f"T={args.temperature}, top_p={args.top_p}, semilla {args.seed}, "
             f"{len(history)} velas de historia, datos sha256 {data_sha[:16]}")
    base = (f"python prediction_ledger.py --ledger '{args.ledger.as_posix()}' forecast "
            f"--question {args.question} --author ia:kronos")
    if "quantiles" in result:
        q = result["quantiles"]
        rationale = (f"Kronos {args.model}: cuantiles del cierre final de {result['paths']} caminos "
                     f"muestreados uno a uno (p10 {q['0.1']}, p50 {q['0.5']}, p90 {q['0.9']}); {setup}")
        command = f"{base} --quantiles '{json.dumps(q)}' --rationale '{rationale}'"
        extra = {}
    else:
        p = min(max(result["probability"], 0.01), 0.99)
        rationale = (f"Kronos {args.model}: {result['true']}/{result['true'] + result['false']} caminos "
                     f"cumplen ({result['void']} void) de {result['paths']} muestreados uno a uno; {setup}")
        command = f"{base} --probability {p} --rationale '{rationale}'"
        extra = {"probability_to_seal": p}
    print(json.dumps({"ok": True, "question": args.question, **result, **extra,
                      "history": [resolver.iso_ms(history[0]["open_ms"]), resolver.iso_ms(history[-1]["open_ms"])],
                      "predicted_candles": len(future), "data_sha256": data_sha,
                      "params_sha256": hashlib.sha256(json.dumps(vars(args), default=str, sort_keys=True).encode()).hexdigest()[:16],
                      "command": command, "evidence_type": "inference"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
