import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lodestone.registry import db
from lodestone.hub import control, tools
from lodestone.hub.protocol import MARKER


def env(status, seq, **kw):
    d = {"status": status, "seq": seq, "summary": status.lower(), "tokens_used": 100}
    d.update(kw)
    return f"{MARKER} {json.dumps(d)}"


class FakeConv:
    def __init__(self, reply):
        self.reply = reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, text):
        pass

    async def get_response(self):
        return SimpleNamespace(text=self.reply)


class FakeAccount:
    def __init__(self):
        self.script = []
        self.i = 0

    def conversation(self, peer, timeout):
        reply = self.script[self.i]
        self.i += 1
        return FakeConv(reply)


class FakeMemory:
    def __init__(self):
        self.captures = []
        self.searches = []
        self.health_calls = 0

    async def capture_agent_turn(self, agent_id, user_text, assistant_text, **kwargs):
        self.captures.append((agent_id, user_text, assistant_text, kwargs))

    async def health(self):
        self.health_calls += 1
        return {
            "configured": True,
            "ok": True,
            "base_url": "http://127.0.0.1:8420",
            "namespace": "lodestone",
            "detail": "ok",
        }

    async def search_agent_memory(self, agent_id, query, limit=5, memory_type="", project_name=""):
        self.searches.append(("memory", agent_id, query, limit, memory_type, project_name))
        return "remembered summary"

    async def search_agent_conversation(self, agent_id, query, limit=5, project_name=""):
        self.searches.append(("conversation", agent_id, query, limit, project_name))
        return "raw conversation"


def make_hub(db_path, userbot=None, allowed_users=None):
    agents = [{
        "id": "a1", "name": "A1", "type": "t", "host": "h", "telegram_peer": "@a",
        "projects": ["devproj", {"name": "liveproj", "status": "live"}],
        "permissions": ["x"],
    }]
    cfg = SimpleNamespace(agents=agents, ai={"model": "gpt-4o-mini"},
                          dispatch={"reply_timeout": 30},
                          loop={"enabled": True, "token_budget": 100000, "max_steps": 10})
    cfg.agent = lambda aid: next((a for a in agents if a["id"] == aid), None)
    cfg.loop_enabled = bool(cfg.loop.get("enabled"))
    db.init_db(db_path)
    db.sync_from_config(db_path, cfg)
    return control.Hub(cfg, db_path, userbot=userbot, allowed_users=allowed_users or [1])


def _task_id_from(msg):
    for line in msg.splitlines():
        if line.startswith("Task id:"):
            return line.split(":", 1)[1].strip()
    return None


class LoopCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "l.db")
        self.acct = FakeAccount()
        self.hub = make_hub(self.db_path, userbot=self.acct, allowed_users=[1])

    def tearDown(self):
        self.tmp.cleanup()

    async def test_loop_command_estimates_then_confirm_runs(self):
        msg = await control.handle_text(self.hub, "/loop devproj build it")
        self.assertIn("estimated", msg.lower())
        tid = _task_id_from(msg)
        self.assertIsNotNone(tid)
        # Nothing has run yet — status is 'estimated'.
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "estimated")

        self.acct.script = [env("DONE", 1)]
        out = await control.handle_text(self.hub, f"/loop_confirm {tid}")
        self.assertIn("DONE", out)

    async def test_loop_usage_messages(self):
        self.assertIn("Usage: /loop", await control.handle_text(self.hub, "/loop"))
        self.assertIn("Usage: /loop_confirm", await control.handle_text(self.hub, "/loop_confirm"))
        self.assertIn("Usage: /approve", await control.handle_text(self.hub, "/approve"))

    async def test_live_pr_gate_via_commands(self):
        msg = await control.handle_text(self.hub, "/loop liveproj ship it")
        tid = _task_id_from(msg)
        self.acct.script = [env("GATE_PR", 1, pr_url="https://github.com/a/b/pull/9")]
        out = await control.handle_text(self.hub, f"/loop_confirm {tid}")
        self.assertIn("LIVE", out)
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "awaiting_pr_approval")

        self.acct.script = [env("DONE", 2)]
        self.acct.i = 0
        out2 = await control.handle_text(self.hub, f"/approve {tid}")
        self.assertIn("DONE", out2)

    async def test_ai_start_loop_tool_does_not_bypass_confirm_or_gate(self):
        # The AI tool only ESTIMATES; it returns a task id and a confirm prompt,
        # and never auto-runs. The live gate therefore cannot be skipped via the
        # model path.
        result = await tools.run_tool("start_loop",
                                      {"project_name": "liveproj", "task": "ship it"},
                                      self.hub)
        self.assertIn("Task id:", result)
        tid = _task_id_from(result)
        self.assertEqual(db.get_loop_run(self.db_path, tid)["status"], "estimated")

    async def test_dispatch_tool_passes_required_permissions(self):
        result = await tools.run_tool(
            "dispatch",
            {"agent_id": "a1", "task": "build it", "required_permissions": ["y"]},
            self.hub,
        )
        self.assertIn("lacks required permissions", result)

    async def test_memory_search_tools_use_hub_memory(self):
        self.hub.memory = FakeMemory()
        result = await tools.run_tool(
            "search_agent_memory",
            {"agent_id": "a1", "query": "deploy habits", "limit": 3, "memory_type": "episodic"},
            self.hub,
        )
        self.assertEqual(result, "remembered summary")
        result2 = await tools.run_tool(
            "search_agent_conversation",
            {"agent_id": "a1", "query": "postgres", "limit": 2},
            self.hub,
        )
        self.assertEqual(result2, "raw conversation")

    async def test_memory_status_command_reports_backend_health(self):
        self.hub.memory = FakeMemory()
        out = await control.handle_text(self.hub, "/memory_status")
        self.assertIn("healthy: yes", out)
        self.assertIn("http://127.0.0.1:8420", out)

    async def test_memory_search_command_queries_agent_memory(self):
        self.hub.memory = FakeMemory()
        out = await control.handle_text(self.hub, "/memory_search a1 deploy habits")
        self.assertIn("remembered summary", out)

    async def test_memory_search_project_command_resolves_owner(self):
        self.hub.memory = FakeMemory()
        out = await control.handle_text(self.hub, "/memory_search_project devproj postgres")
        self.assertIn("remembered summary", out)
        self.assertEqual(self.hub.memory.searches[0][-1], "devproj")

    async def test_loop_captures_initial_agent_exchange_to_memory(self):
        self.hub.memory = FakeMemory()
        msg = await control.handle_text(self.hub, "/loop devproj build it")
        tid = _task_id_from(msg)
        self.acct.script = [env("DONE", 1)]
        await control.handle_text(self.hub, f"/loop_confirm {tid}")
        self.assertEqual(self.hub.memory.captures[0][0], "a1")
        self.assertIn("project 'devproj'", self.hub.memory.captures[0][1])
        self.assertIn("DONE", self.hub.memory.captures[0][2])

    async def test_loop_unavailable_without_userbot(self):
        hub = make_hub(self.db_path, userbot=None, allowed_users=[1])
        out = await control.handle_text(hub, "/loop devproj build it")
        self.assertIn("unavailable", out.lower())

    async def test_loop_rejects_missing_required_permissions(self):
        out = await control.handle_text(self.hub, "/loop devproj [requires:y] build it")
        self.assertIn("lacks required permissions", out)
        self.assertEqual(db.list_loop_runs(self.db_path), [])

    async def test_loop_disabled_in_config(self):
        hub = make_hub(self.db_path, userbot=self.acct, allowed_users=[1])
        hub.config.loop["enabled"] = False
        hub.config.loop_enabled = False
        out = await control.handle_text(hub, "/loop devproj build it")
        self.assertIn("disabled", out.lower())
        self.assertEqual(db.list_loop_runs(self.db_path), [])

    async def test_loop_status_reports_active(self):
        msg = await control.handle_text(self.hub, "/loop devproj build it")
        tid = _task_id_from(msg)
        out = await control.handle_text(self.hub, "/loop_status")
        self.assertIn(tid, out)


if __name__ == "__main__":
    unittest.main()
