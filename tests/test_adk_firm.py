"""Firma ADK (labs/adk_firm): frontera de herramientas y, si ADK está instalado, la ronda con un modelo falso."""
import asyncio
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "adk_firm"
sys.path.insert(0, str(LAB))

import herramientas as hz  # noqa: E402

def _has(module: str) -> bool:
    try:                                   # find_spec raises when the parent package is missing
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


HAS_ADK = _has("google.adk")
CONCEPT = {"asset": "BTCUSDT", "event": "touch", "interval": "15m"}


class Recorder:
    def __init__(self, returncode=0, stdout='{"ok": 1}', stderr=""):
        self.calls = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, argv, env, cwd):
        self.calls.append((argv, env, cwd))
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


def caliope(**kw):
    rec = Recorder(**kw)
    return hz.Caliope(root=ROOT, runner=rec), rec


class FrontierTest(unittest.TestCase):
    def test_no_tool_can_reach_forbidden_desk_commands(self):
        source = (LAB / "herramientas.py").read_text(encoding="utf-8") \
            + (LAB / "caliope_firm" / "agent.py").read_text(encoding="utf-8")
        for word in hz.FORBIDDEN_DESK_COMMANDS:
            self.assertNotIn(f'"{word}"', source.replace('FORBIDDEN_DESK_COMMANDS = ("approve", "submit", '
                                                         '"reject", "kill", "unkill")', ""))

    def test_seal_uses_the_bound_author_and_runs_the_cli(self):
        c, rec = caliope()
        out = c.sellar("ia:gemini", CONCEPT, 0.3, "volatilidad de 6h y cierre 81.2k a las 14:00Z", dry_run=True)
        self.assertTrue(out["ok"])
        argv = rec.calls[0][0]
        self.assertEqual(argv[argv.index("--author") + 1], "ia:gemini")
        self.assertIn("--dry-run", argv)
        self.assertEqual(json.loads(argv[argv.index("--concept") + 1]), CONCEPT)

    def test_reserved_and_human_authors_are_refused(self):
        c, rec = caliope()
        for author in ("nathan", "claude", "ia:claude", "experto:x", "IA:GEMINI"):
            self.assertFalse(c.sellar(author, CONCEPT, 0.3, "x" * 20, dry_run=True)["ok"], author)
            self.assertFalse(c.proponer(author, "q-" + "a" * 16, "buy", 0.05, 2, 79000, None, "x")["ok"])
        self.assertEqual(rec.calls, [])

    def test_bad_arguments_fail_closed_before_the_cli(self):
        c, rec = caliope()
        q = "q-" + "a" * 16
        bad = [
            lambda: c.sellar("ia:gemini", CONCEPT, 1.0, "x" * 20, dry_run=True),
            lambda: c.sellar("ia:gemini", CONCEPT, True, "x" * 20, dry_run=True),
            lambda: c.sellar("ia:gemini", {}, 0.4, "x" * 20, dry_run=True),
            lambda: c.sellar("ia:gemini", CONCEPT, 0.4, "corto", dry_run=True),
            lambda: c.proponer("ia:gemini", "q-1", "buy", 0.05, 2, 79000, None, "x"),
            lambda: c.proponer("ia:gemini", q, "long", 0.05, 2, 79000, None, "x"),
            lambda: c.proponer("ia:gemini", q, "buy", 0.2, 2, 79000, None, "x"),
            lambda: c.proponer("ia:gemini", q, "buy", 0.05, 10, 79000, None, "x"),
            lambda: c.proponer("ia:gemini", q, "buy", 0.05, 2.0, 79000, None, "x"),
            lambda: c.proponer("ia:gemini", q, "buy", 0.05, 2, 0, None, "x"),
            lambda: c.estado("ia:gemini", "q-x"),
            lambda: c.velas("ETHUSDT", "15m", 6),
            lambda: c.velas("BTCUSDT", "15m", 100),
        ]
        for call in bad:
            self.assertFalse(call()["ok"])
        self.assertEqual(rec.calls, [])

    def test_nonzero_exit_is_never_success(self):
        c, _ = caliope(returncode=1, stdout="", stderr="gate: window already open")
        out = c.agenda()
        self.assertFalse(out["ok"])
        self.assertIn("window already open", out["error"])

    def test_timeout_fails_closed(self):
        def boom(argv, env, cwd):
            raise subprocess.TimeoutExpired(argv, 1)
        self.assertFalse(hz.Caliope(root=ROOT, runner=boom).mesa_estado()["ok"])

    def test_secrets_reach_only_the_desk_and_never_the_live_switch(self):
        env = {"BLOFIN_API_KEY": "k", "BLOFIN_API_SECRET": "s", "BLOFIN_API_PASSPHRASE": "p",
               "CALIOPE_DESK_LIVE": "si", "PATH": "/bin"}
        with mock.patch.dict("os.environ", env, clear=True):
            c, rec = caliope()
            c.agenda()
            c.mesa_estado()
        ledger_env, desk_env = rec.calls[0][1], rec.calls[1][1]
        for key in hz.DESK_SECRETS:
            self.assertNotIn(key, ledger_env)
            self.assertIn(key, desk_env)
        self.assertNotIn("CALIOPE_DESK_LIVE", ledger_env)
        self.assertNotIn("CALIOPE_DESK_LIVE", desk_env)
        self.assertEqual(rec.calls[1][0][1:3], ["-m", "desk"])

    def test_propose_maps_to_desk_propose_only(self):
        c, rec = caliope()
        c.proponer("ia:gemini", "q-" + "b" * 16, "sell", 0.05, 3, 83000.0, 79000.0, "rechazo en 82k")
        argv = rec.calls[0][0]
        self.assertEqual(argv[1:4], ["-m", "desk", "propose"])
        self.assertEqual(argv[argv.index("--tp") + 1], "79000.0")


@unittest.skipUnless(HAS_ADK, "google-adk no está instalado (pip install -r labs/adk_firm/requirements.txt)")
class FirmTest(unittest.TestCase):
    """La ronda completa con un modelo falso: sin red, sin claves."""

    def setUp(self):
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        class Scripted(BaseLlm):
            replies: list = []
            seen: list = []

            async def generate_content_async(self, llm_request, stream=False):
                self.seen.append(llm_request)
                reply = self.replies.pop(0) if self.replies else types.Part(text="sin más")
                yield LlmResponse(content=types.Content(role="model", parts=[reply]))

        self.types = types
        self.Scripted = Scripted
        sys.path.insert(0, str(LAB))
        from caliope_firm import agent as firm  # noqa: PLC0415
        self.firm = firm

    def call(self, name, **args):
        return self.types.Part(function_call=self.types.FunctionCall(name=name, args=args))

    def text(self, value):
        return self.types.Part(text=value)

    def run_round(self, firm_agent, message="ronda"):
        from google.adk.runners import InMemoryRunner
        runner = InMemoryRunner(agent=firm_agent, app_name="caliope")

        async def go():
            session = await runner.session_service.create_session(app_name="caliope", user_id="nathan")
            events = []
            async for event in runner.run_async(user_id="nathan", session_id=session.id,
                                                new_message=self.types.Content(role="user",
                                                                               parts=[self.text(message)])):
                events.append(event)
            return session, events
        return asyncio.run(go())

    def test_analysts_do_not_see_each_other_and_seal_waits_for_the_person(self):
        c, rec = caliope(stdout='{"dry_run": true}')
        vigia = self.Scripted(model="v", replies=[self.text("AGENDA: q-1 abre pronto")])
        a = self.Scripted(model="a", replies=[self.text("SECRETO-A p=0.71")])
        b = self.Scripted(model="b", replies=[
            self.call("sellar", concept=CONCEPT, probability=0.4, rationale="volatilidad 6h, 81k 14:00Z"),
            self.text("listo")])
        risk = self.Scripted(model="r", replies=[self.text("Sin órdenes pendientes.")])
        firm = self.firm.construir_firma([("ia:a", a), ("ia:b", b)], vigia, caliope=c)
        firm.sub_agents[2].model = risk
        _, events = self.run_round(firm)

        seen_by_b = json.dumps([r.model_dump(mode="json", exclude_none=True) for r in b.seen], ensure_ascii=False)
        self.assertIn("AGENDA: q-1", seen_by_b)            # el vigía sí llega a todos
        self.assertNotIn("SECRETO-A", seen_by_b)           # el otro analista, no
        argvs = [call[0] for call in rec.calls]
        seals = [a for a in argvs if "seal" in a]
        self.assertEqual(len(seals), 1)
        self.assertIn("--dry-run", seals[0])               # solo en seco: espera a la persona
        self.assertEqual(seals[0][seals[0].index("--author") + 1], "ia:b")
        asked = [e for e in events if e.get_function_calls()
                 and any(f.name == "adk_request_confirmation" for f in e.get_function_calls())]
        self.assertEqual(len(asked), 1)

    def _seal_then_answer(self, confirmed):
        from google.adk.runners import InMemoryRunner
        c, rec = caliope(stdout='{"dry_run": true}')
        b = self.Scripted(model="b", replies=[
            self.call("sellar", concept=CONCEPT, probability=0.4, rationale="volatilidad 6h, 81k 14:00Z"),
            self.text("hecho")])
        firm = self.firm.construir_firma([("ia:b", b)], self.Scripted(model="v", replies=[self.text("ok")]),
                                         caliope=c)
        runner = InMemoryRunner(agent=firm, app_name="caliope")

        async def go():
            s = await runner.session_service.create_session(app_name="caliope", user_id="nathan")
            ask = None
            async for e in runner.run_async(user_id="nathan", session_id=s.id,
                                            new_message=self.types.Content(role="user", parts=[self.text("ronda")])):
                for f in e.get_function_calls() or []:
                    if f.name == "adk_request_confirmation":
                        ask = (f, e.invocation_id)
            answer = self.types.Part(function_response=self.types.FunctionResponse(
                id=ask[0].id, name="adk_request_confirmation", response={"confirmed": confirmed}))
            async for _ in runner.run_async(user_id="nathan", session_id=s.id, invocation_id=ask[1],
                                            new_message=self.types.Content(role="user", parts=[answer])):
                pass
        asyncio.run(go())
        return [call[0] for call in rec.calls if "seal" in call[0]]

    def test_confirmed_seal_is_written_once(self):
        seals = self._seal_then_answer(True)
        self.assertEqual(len(seals), 2)
        self.assertIn("--dry-run", seals[0])
        self.assertNotIn("--dry-run", seals[1])

    def test_rejected_seal_writes_nothing(self):
        seals = self._seal_then_answer(False)
        self.assertEqual(len(seals), 1)
        self.assertIn("--dry-run", seals[0])

    def test_guard_blocks_tools_outside_the_agent_list(self):
        guard = self.firm._guard(frozenset({"agenda"}))
        tool = mock.Mock()
        tool.name = "proponer"
        self.assertFalse(guard(tool, {}, mock.Mock())["ok"])
        tool.name = "agenda"
        self.assertIsNone(guard(tool, {}, mock.Mock()))

    def test_risk_officer_has_no_write_tools(self):
        firm = self.firm.construir_firma([("ia:a", self.Scripted(model="a"))], self.Scripted(model="r"),
                                         caliope=caliope()[0])
        names = {getattr(t, "__name__", getattr(t, "name", "")) for t in firm.sub_agents[2].tools}
        self.assertFalse(names & self.firm.WRITE_TOOLS)
        self.assertEqual(names, {"mesa_estado", "mesa_verificar", "historial", "velas"})

    def test_analyst_spec_fails_closed(self):
        self.assertEqual(self.firm.parse_analysts("ia:g=gemini-x; ia:d=deepseek/chat"),
                         [("ia:g", "gemini-x"), ("ia:d", "deepseek/chat")])
        for spec in ("", "nathan=gemini", "ia:claude=x", "ia:g", "ia:g=x;ia:g=y", "ia:g="):
            with self.assertRaises(ValueError, msg=spec):
                self.firm.parse_analysts(spec)


if __name__ == "__main__":
    unittest.main()
