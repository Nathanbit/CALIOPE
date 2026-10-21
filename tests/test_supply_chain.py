import tempfile
import unittest
from pathlib import Path
from unittest import mock

import caliope_loop as cl


class SecretScanTests(unittest.TestCase):
    def test_detects_known_token_shapes_without_values(self):
        fake = "ghp_" + "a" * 36
        found = cl.scan_text("x.py", f"TOKEN = '{fake}'\n".encode())
        self.assertEqual(found, [{"file": "x.py", "line": 1, "kind": "github_token"}])
        self.assertNotIn(fake, str(found))

    def test_detects_ai_and_cloud_keys(self):
        lines = "\n".join([
            "a=" + "sk-ant-" + "b" * 30,
            "b=" + "AKIA" + "C" * 16,
            "c=" + "sk-or-v1-" + "d" * 40,
            "-----BEGIN " + "PRIVATE KEY-----",
        ]).encode()
        kinds = {f["kind"] for f in cl.scan_text("cfg.txt", lines)}
        self.assertTrue({"anthropic_key", "aws_access_key", "openrouter_key", "private_key"} <= kinds)

    def test_ignores_hashes_and_binary(self):
        self.assertEqual(cl.scan_text("m.sha256", ("a" * 64 + "  f.pine\n").encode()), [])
        self.assertEqual(cl.scan_text("img.png", b"\x89PNG\x00" + b"ghp_" + b"a" * 36), [])

    def test_detects_ghostaction_markers(self):
        marker = "my-git" + "hub.com"
        found = cl.scan_text(".github/workflows/x.yml", f"curl http://{marker}\n".encode())
        self.assertEqual(found[0]["kind"], "ghostaction_ioc")


class WorkflowGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(cl, "ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_no_workflows_passes(self):
        self.assertEqual(cl._check_workflows()[0]["status"], "pass")

    def test_unapproved_workflow_fails(self):
        wf = self.root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "security-audit.yml").write_text("on: push\n")
        self.assertEqual(cl._check_workflows()[0]["status"], "fail")

    def test_approved_by_hash_passes_and_tamper_fails(self):
        wf = self.root / ".github" / "workflows"
        wf.mkdir(parents=True)
        f = wf / "ok.yml"
        f.write_text("on: workflow_dispatch\n")
        (self.root / "security").mkdir()
        allow = self.root / "security" / "allowed_workflows.sha256"
        allow.write_text(f"{cl.sha256_file(f).lower()}  .github/workflows/ok.yml\n")
        self.assertEqual(cl._check_workflows()[0]["status"], "pass")
        f.write_text("on: push\nrun: curl evil\n")
        self.assertEqual(cl._check_workflows()[0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
