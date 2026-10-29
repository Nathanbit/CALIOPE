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
        tests = [str(ROOT / "tools" / "panel" / name) for name in ("parse.test.mjs", "ledger.test.mjs")]
        result = subprocess.run([node, "--test", *tests],
                                capture_output=True, text=True, encoding="utf-8", timeout=120,
                                env=dict(os.environ, TZ="America/Guayaquil"))
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])


class PanelIntegrityTest(unittest.TestCase):
    """Every remote script the panel runs must be pinned to a version and a SHA-384."""

    def test_remote_scripts_are_pinned(self):
        import json
        import re
        html = (ROOT / "tools" / "panel" / "index.html").read_text(encoding="utf-8")
        imap = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))
        pinned = imap["integrity"]
        module = html[html.index('<script type="module">'):]
        self.assertLess(html.index('type="importmap"'), html.index('<script type="module">'),
                        "the import map must come before any module script")
        remote = set(re.findall(r"'(https://cdn\.[^']+\.js)'", module))
        self.assertTrue(remote, "expected the transformers.js URL")
        for url in remote:
            self.assertRegex(url, r"@\d+\.\d+\.\d+/", f"{url} must pin an exact version")
            self.assertIn(url, pinned, f"{url} runs without an integrity hash")
            self.assertRegex(pinned[url], r"^sha384-[A-Za-z0-9+/]{64}$")
        self.assertNotRegex(html, r"<script[^>]+src=[\"']https?://", "no remote <script src> without review")


if __name__ == "__main__":
    unittest.main()
