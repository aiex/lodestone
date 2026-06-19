import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lodestone.ai.orchestrator import Orchestrator
from lodestone.registry import db


class FakeProvider:
    def __init__(self):
        self.model = "gpt-4o-mini"
        self.calls = []

    async def chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "我會交給 Hermes A 處理"}, {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }


class FakeMemory:
    def __init__(self):
        self.recalled = []
        self.captured = []

    async def recall(self, query, scope="orchestrator"):
        self.recalled.append((query, scope))
        return "Hermes A 最近處理過 cricket refresh 與 postgres migration。"

    async def recall_scoped(self, query, scope="agent", agent_id="", project_name=""):
        self.recalled.append((query, scope, agent_id, project_name))
        return "demo-dev-app 最近一次 refresh 需要先檢查 postgres migration。"

    async def capture_orchestrator_turn(self, user_text, assistant_text):
        self.captured.append((user_text, assistant_text))


class OrchestratorMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "lodestone.db")
        self.config = SimpleNamespace(
            ai={"pricing": {}},
            agents=[
                {
                    "id": "hermes-a",
                    "name": "Hermes A",
                    "type": "hermes",
                    "host": "host-a",
                    "telegram_peer": "@hermes_a_bot",
                    "projects": ["demo-dev-app"],
                    "permissions": ["ec2:hermes-a"],
                }
            ],
        )
        self.config.agent = lambda aid: next((a for a in self.config.agents if a["id"] == aid), None)
        db.init_db(self.db_path)
        db.sync_from_config(self.db_path, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_orchestrator_injects_memory_context_and_captures_final_reply(self):
        provider = FakeProvider()
        memory = FakeMemory()
        hub = SimpleNamespace(db_path=self.db_path, config=self.config, memory=memory)
        orch = Orchestrator(provider, hub)
        reply = await orch.handle("幫我找最適合處理 demo-dev-app refresh 的 agent")
        self.assertEqual(reply, "我會交給 Hermes A 處理")
        self.assertEqual(memory.recalled[0], ("幫我找最適合處理 demo-dev-app refresh 的 agent", "orchestrator"))
        self.assertEqual(memory.captured[0][1], "我會交給 Hermes A 處理")
        messages = provider.calls[0]["messages"]
        self.assertEqual(messages[1]["role"], "system")
        self.assertIn("<memory-context>", messages[1]["content"])
        self.assertGreaterEqual(len(memory.recalled), 1)
