"""Pruebas de .claude/hooks/guard.py y tools/verify.py."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"
sys.path.insert(0, str(ROOT / "tools"))
import verify  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _git(self.root, "init", "-q")
        proj = self.root / "projects" / "demo"
        (proj / "source").mkdir(parents=True)
        (proj / "source" / "base.pine").write_text("//@version=5\n", encoding="utf-8")
        (proj / "MANIFEST.sha256").write_text("0" * 64 + "  source/base.pine\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, mode: str, payload, raw: str | None = None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        stdin = raw if raw is not None else json.dumps(payload)
        return subprocess.run((sys.executable, str(GUARD), mode), input=stdin, text=True,
                              capture_output=True, env=env, check=False)

    def bash(self, command: str):
        return self.run_hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": command}})

    def edit(self, path: str):
        return self.run_hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(self.root / path)}})

    # pre-bash
    def test_blocks_force_push_and_no_verify(self):
        for cmd in ("git push --force origin main", "git push -f", "git push origin +main",
                    "git commit -m x --no-verify", "git commit -nm x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd).returncode, 2)

    def test_blocks_audit_bypasses(self):
        """Variants confirmed by the 2026-10-23 audit as slipping through."""
        for cmd in ("git push -uf origin main", 'git commit -m "a;b" --no-verify',
                    "git -c core.hooksPath=/dev/null commit -m x",
                    "echo {} >> projects/macroflow/predictions/ledger.jsonl",
                    "sed -i 's/0.5/0.9/' projects/macroflow/predictions/ledger.jsonl",
                    "python -c \"open('.env').read()\""):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd).returncode, 2)

    def test_allows_ledger_cli_and_reads(self):
        for cmd in ("python prediction_ledger.py --ledger projects/macroflow/predictions/ledger.jsonl score",
                    "cat projects/macroflow/predictions/ledger.jsonl",
                    "sha256sum projects/macroflow/predictions/ledger.jsonl",
                    "git config core.hooksPath .githooks"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd).returncode, 0, self.bash(cmd).stderr)

    def test_blocks_reading_credentials(self):
        for cmd in ("cat .env", "type secrets/broker.txt", "head -n1 deploy.pem", "cp id_rsa /tmp/x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd).returncode, 2)

    def test_allows_normal_commands(self):
        for cmd in ("git status", "python tools/verify.py", "cat .env.example", "git push origin feat/x",
                    "grep -n env README.md"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd).returncode, 0, self.bash(cmd).stderr)

    def test_commit_requires_githooks(self):
        self.assertEqual(self.bash("git add -A && git commit -m x").returncode, 2)
        _git(self.root, "config", "core.hooksPath", ".githooks")
        self.assertEqual(self.bash("git add -A && git commit -m x").returncode, 0)

    def test_unreadable_input_fails_closed(self):
        self.assertEqual(self.run_hook("pre-bash", None, raw="{no json").returncode, 2)
        self.assertEqual(self.run_hook("pre-edit", None, raw="{no json").returncode, 2)

    # pre-edit
    def test_blocks_protected_paths(self):
        for path in (".github/workflows/audit.yml", "security/allowed_workflows.sha256",
                     "projects/demo/MANIFEST.sha256", "projects/demo/source/base.pine",
                     ".env", "secrets/x.txt", "keys/broker.pem",
                     "projects/demo/predictions/ledger.jsonl"):
            with self.subTest(path=path):
                self.assertEqual(self.edit(path).returncode, 2)

    def test_allows_new_and_ordinary_files(self):
        for path in ("projects/demo/source/base_v2.pine", "tools/x.py", ".env.example", "docs/a.md"):
            with self.subTest(path=path):
                self.assertEqual(self.edit(path).returncode, 0)

    # post-edit
    def test_post_edit_rejects_syntax_error(self):
        bad = self.root / "bad.py"
        bad.write_text("def f(:\n", encoding="utf-8")
        payload = {"tool_input": {"file_path": str(bad)}}
        self.assertEqual(self.run_hook("post-edit", payload).returncode, 2)
        bad.write_text("def f():\n    return 1\n", encoding="utf-8")
        self.assertEqual(self.run_hook("post-edit", payload).returncode, 0)

    # stop
    def test_stop_is_quiet_without_changes_and_never_loops(self):
        _git(self.root, "config", "core.hooksPath", ".githooks")
        clean = tempfile.TemporaryDirectory()
        try:
            _git(Path(clean.name), "init", "-q")
            env = {**os.environ, "CLAUDE_PROJECT_DIR": clean.name}
            r = subprocess.run((sys.executable, str(GUARD), "stop"), input="{}", text=True,
                               capture_output=True, env=env, check=False)
            self.assertEqual(r.returncode, 0)
        finally:
            clean.cleanup()
        self.assertEqual(self.run_hook("stop", {"stop_hook_active": True}).returncode, 0)


class VerifySelectionTests(unittest.TestCase):
    def names(self, files):
        checks, notes = verify.select_checks(files)
        return [c.name for c in checks], notes

    def test_docs_only_runs_doctor(self):
        names, _ = self.names(["docs/DETERMINISM.md"])
        self.assertEqual(names, [verify.DOCTOR.name])

    def test_runner_change_runs_root_tests(self):
        names, _ = self.names(["caliope_loop.py"])
        self.assertIn(verify.ROOT_TESTS.name, names)
        self.assertNotIn(verify.ARCHIVE_TESTS.name, names)

    def test_archive_change_runs_archive_tests_and_flags_gap(self):
        names, notes = self.names(["archive/labs/macroflow_v0_9_5_import/macroflow/strategy/reference.py"])
        self.assertIn(verify.ARCHIVE_TESTS.name, names)
        self.assertTrue(any("archive/" in n for n in notes))

    def test_ledger_change_is_flagged_append_only(self):
        names, notes = self.names(["projects/macroflow/predictions/ledger.jsonl"])
        self.assertEqual(names, [verify.DOCTOR.name])
        self.assertTrue(any("prediction_ledger.py" in n for n in notes))

    def test_pine_change_is_declared_unverified(self):
        _, notes = self.names(["projects/macroflow/source/new.pine"])
        self.assertTrue(any("TradingView" in n for n in notes))

    def test_missing_module_is_skipped_not_passed(self):
        check = verify.Check("x", ("python",), requires_module="modulo_que_no_existe_123")
        self.assertEqual(verify.run_check(check).status, "skipped")


if __name__ == "__main__":
    unittest.main()
