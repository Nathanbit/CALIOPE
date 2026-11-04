"""Author weights with skfolio: the shared-question matrix (stdlib) and the allocation (skfolio)."""
import importlib.util
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import author_weights as aw  # noqa: E402
import prediction_ledger as pl  # noqa: E402

HAS_SKFOLIO = importlib.util.find_spec("skfolio") is not None


def synthetic(n=90, seed=1):
    rng, rows = random.Random(seed), []
    for _ in range(n):
        y = 1.0 if rng.random() < 0.5 else 0.0
        good = min(max((0.72 if y else 0.28) + rng.uniform(-0.15, 0.15), 0.02), 0.98)
        noise = rng.uniform(0.2, 0.8)
        bad = min(max((0.4 if y else 0.6) + rng.uniform(-0.1, 0.1), 0.02), 0.98)
        rows.append([aw.skill(p, y) for p in (good, noise, bad)])
    return [str(i) for i in range(n)], ["bueno", "ruido", "malo"], rows


class MatrixTest(unittest.TestCase):
    def test_skill_is_zero_for_a_coin_and_signed(self):
        self.assertAlmostEqual(aw.skill(0.5, 1.0), 0.0)
        self.assertGreater(aw.skill(0.8, 1.0), 0)
        self.assertLess(aw.skill(0.8, 0.0), 0)

    def test_only_shared_questions_and_a_floor_of_30(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "l.jsonl"
            qids = []
            for i in range(31):
                q = pl.ask(path, f"q{i}", f"2026-01-{1 + i % 28:02d}T00:00:00Z", "s", "c")["question_id"]
                qids.append(q)
            records = pl.load(path)
        with self.assertRaisesRegex(aw.WeightsError, "at least 2 authors"):
            aw.skill_matrix(records)
        with self.assertRaisesRegex(aw.WeightsError, "only 0 questions"):
            aw.skill_matrix(records, ["a", "b"])

    def test_min_n_cannot_be_lowered(self):
        from unittest import mock
        with mock.patch("sys.stdout"):
            self.assertEqual(aw.main(["--min-n", "5"]), 2)


@unittest.skipUnless(HAS_SKFOLIO, "skfolio no está instalado (pip install -r requirements.txt)")
class AllocationTest(unittest.TestCase):
    def test_the_skilled_author_gets_the_most_weight_out_of_sample(self):
        out = aw.allocate(*synthetic())
        cvar = out["cvar"]["weights"]
        self.assertEqual(max(cvar, key=cvar.get), "bueno")
        self.assertGreater(out["cvar"]["out_of_sample"]["mean_skill"], out["igual"]["out_of_sample"]["mean_skill"])
        for alloc in out.values():
            self.assertAlmostEqual(sum(alloc["weights"].values()), 1.0, places=3)
            self.assertTrue(all(w >= -1e-6 for w in alloc["weights"].values()))     # long only

    def test_an_author_with_one_fixed_probability_is_refused(self):
        q, a, rows = synthetic()
        rows = [[r[0], r[1], aw.skill(0.6, 1.0)] for r in rows]
        with self.assertRaisesRegex(aw.WeightsError, "same skill"):
            aw.allocate(q, a, rows)


if __name__ == "__main__":
    unittest.main()
