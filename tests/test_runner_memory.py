import unittest
from types import SimpleNamespace

from lodestone.hub.runner import _shutdown_memory


class FakeMemory:
    def __init__(self):
        self.calls = []

    async def session_end(self, scope, agent_id="", project_name=""):
        self.calls.append((scope, agent_id, project_name))


class RunnerMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_memory_flushes_orchestrator_and_agent_scopes(self):
        memory = FakeMemory()
        hub = SimpleNamespace(
            memory=memory,
            config=SimpleNamespace(
                agents=[
                    {"id": "hermes-a", "projects": ["cricap", {"name": "indiweather", "status": "live"}]},
                    {"id": "hermes-b", "projects": ["96football"]},
                ]
            ),
        )
        await _shutdown_memory(hub)
        self.assertIn(("orchestrator", "", ""), memory.calls)
        self.assertIn(("agent", "hermes-a", ""), memory.calls)
        self.assertIn(("agent", "hermes-a", "cricap"), memory.calls)
        self.assertIn(("agent", "hermes-a", "indiweather"), memory.calls)
        self.assertIn(("agent", "hermes-b", "96football"), memory.calls)

