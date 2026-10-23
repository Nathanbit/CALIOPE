import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import caliope_doctor as cl


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "projects" / "demo"
        self.proj.mkdir(parents=True)
        self.patch = mock.patch.object(cl, "ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _manifest(self, lines):
        m = self.proj / "MANIFEST.sha256"
        m.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return m

    def test_exact_match_passes(self):
        (self.proj / "a.txt").write_bytes(b"hola\n")
        m = self._manifest([f"{_h(b'hola' + bytes([10]))}  a.txt"])
        self.assertEqual(cl._check_manifest(m), [])

    def test_crlf_only_difference_is_warning(self):
        (self.proj / "a.txt").write_bytes(b"x\ny\n")
        m = self._manifest([f"{_h(b'x' + bytes([13, 10]) + b'y' + bytes([13, 10]))}  a.txt"])
        out = cl._check_manifest(m)
        self.assertEqual([o["status"] for o in out], ["warn"])

    def test_content_change_fails(self):
        (self.proj / "a.txt").write_bytes(b"cambiado\n")
        m = self._manifest([f"{_h(b'original')}  a.txt"])
        self.assertEqual(cl._check_manifest(m)[0]["status"], "fail")

    def test_missing_file_and_path_escape_fail(self):
        m = self._manifest([f"{'0' * 64}  no_existe.txt", f"{'0' * 64}  ../../fuera.txt"])
        self.assertEqual([o["status"] for o in cl._check_manifest(m)], ["fail", "fail"])

    def test_uppercase_hash_accepted(self):
        (self.proj / "a.txt").write_bytes(b"z")
        m = self._manifest([f"{_h(b'z').upper()}  a.txt"])
        self.assertEqual(cl._check_manifest(m), [])

    def test_unsafe_workflow_flag_detected(self):
        self.assertTrue(cl.UNSAFE_FLAG.search("safety:\n  automatic_trading: true\n"))
        self.assertIsNone(cl.UNSAFE_FLAG.search("  automatic_trading: false\n"))


if __name__ == "__main__":
    unittest.main()
