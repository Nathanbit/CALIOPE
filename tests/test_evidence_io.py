import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evidence_io as eio  # noqa: E402


class EvidenceIoTest(unittest.TestCase):
    def test_canonical_matches_the_historic_serialization(self):
        value = {"b": 1, "a": "ñ ", "c": [0.1, 2]}
        old = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False)
        self.assertEqual(eio.canonical_text(value, ValueError, "x"), old)
        self.assertEqual(eio.sha256_hex(old.encode(), upper=True),
                         hashlib.sha256(old.encode()).hexdigest().upper())

    def test_nan_is_refused_on_write_and_read(self):
        with self.assertRaisesRegex(KeyError, "boom"):
            eio.canonical_text({"x": float("nan")}, KeyError, "boom")
        with self.assertRaisesRegex(KeyError, "non-finite"):
            eio.loads_strict('{"x": NaN}', KeyError)

    def test_aware_time_only(self):
        self.assertEqual(eio.iso_utc(eio.parse_aware_time("2026-10-23T07:00:00-05:00", "t", ValueError)),
                         "2026-10-23T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "timezone"):
            eio.parse_aware_time("2026-10-23T07:00:00", "t", ValueError)

    def test_manifest_parser_keeps_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp) / "MANIFEST.sha256"
            m.write_text("A" * 64 + "  *source/x.pine\n\nnot a line\n", encoding="utf-8")
            entries = eio.parse_sha256_manifest(m)
        self.assertEqual(entries[0], eio.ManifestEntry(1, "a" * 64, "source/x.pine"))
        self.assertEqual((entries[1].line, entries[1].digest), (3, None))

    def test_lock_fails_at_once_or_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "x.lock"
            with eio.exclusive_lock(lock, ValueError, "busy"):
                with self.assertRaisesRegex(ValueError, "busy"):
                    with eio.exclusive_lock(lock, ValueError, "busy"):
                        pass
            self.assertFalse(lock.exists())
            held = threading.Event()

            def holder():
                with eio.exclusive_lock(lock, ValueError, "busy"):
                    held.set()
                    threading.Event().wait(0.2)

            thread = threading.Thread(target=holder)
            thread.start()
            held.wait(2)
            with eio.exclusive_lock(lock, ValueError, "busy", timeout=5):
                pass
            thread.join()


if __name__ == "__main__":
    unittest.main()
