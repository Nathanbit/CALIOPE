"""The panel's message parser (tools/panel/parse.js), tested with Node when present."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PanelParserTest(unittest.TestCase):
    def test_parser(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node no está instalado: parse.js sin verificar")
        result = subprocess.run([node, "--test", str(ROOT / "tools" / "panel" / "parse.test.mjs")],
                                capture_output=True, text=True, encoding="utf-8", timeout=120,
                                env=dict(os.environ, TZ="America/Guayaquil"))
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
