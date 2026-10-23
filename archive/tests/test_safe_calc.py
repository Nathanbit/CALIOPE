"""Pruebas del evaluador acotado de themis (sin importar el paquete completo)."""
import importlib.util
import time
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "themis" / "themis" / "safe_calc.py"  # archive/themis
_spec = importlib.util.spec_from_file_location("safe_calc", _PATH)
safe_calc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(safe_calc)


class SafeCalcTests(unittest.TestCase):
    def test_financial_arithmetic(self):
        cases = {
            "1,000 * 1.05^10": 1000 * 1.05 ** 10,
            "(120 - 100) / 100": 0.2,
            "-3 + +2": -1,
            "7 % 3": 1,
            "17 // 5": 3,
            "2.5e3 / 5": 500.0,
            "((((1))))": 1,
            "+".join(["1"] * 60): 60,
        }
        for expr, expected in cases.items():
            with self.subTest(expr=expr):
                self.assertAlmostEqual(safe_calc.evaluate(expr), expected)

    def test_rejects_resource_exhaustion_quickly(self):
        for expr in ("9^9^9", "9^9^9^9", "10^100000", "2^5000", "(10^300)^(10^3)"):
            with self.subTest(expr=expr):
                start = time.perf_counter()
                with self.assertRaises(safe_calc.CalcError):
                    safe_calc.evaluate(expr)
                self.assertLess(time.perf_counter() - start, 0.5)

    def test_rejects_code_and_bad_input(self):
        for expr in ("__import__('os')", "().__class__", "1 if 1 else 2", "", "1/0",
                     "1" * 300, "1e308 * 10", "-" * 150 + "1"):
            with self.subTest(expr=expr[:30]):
                with self.assertRaises(safe_calc.CalcError):
                    safe_calc.evaluate(expr)


if __name__ == "__main__":
    unittest.main()
