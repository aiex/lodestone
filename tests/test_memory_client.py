import unittest
from unittest.mock import patch

from lodestone.memory.client import MemoryGatewayClient


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.content = b"{}"
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeAsyncClient:
    responses = []
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json, headers):
        FakeAsyncClient.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(FakeAsyncClient.responses.pop(0))

    async def get(self, url, headers):
        FakeAsyncClient.calls.append({"url": url, "headers": headers})
        return FakeResponse(FakeAsyncClient.responses.pop(0))


class MemoryClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeAsyncClient.responses = []
        FakeAsyncClient.calls = []

    async def test_recall_posts_query_and_extracts_context_text(self):
        FakeAsyncClient.responses = [{"memory_context": "agent remembers postgres migrations"}]
        client = MemoryGatewayClient("http://127.0.0.1:8420", api_key="secret", namespace="lodestone")
        with patch("lodestone.memory.client.httpx.AsyncClient", FakeAsyncClient):
            text = await client.recall("how should I route this?")
        self.assertEqual(text, "agent remembers postgres migrations")
        self.assertEqual(FakeAsyncClient.calls[0]["url"], "http://127.0.0.1:8420/recall")
        self.assertEqual(FakeAsyncClient.calls[0]["json"]["query"], "how should I route this?")
        self.assertEqual(FakeAsyncClient.calls[0]["headers"]["Authorization"], "Bearer secret")

    async def test_scoped_recall_posts_project_and_agent_scope(self):
        FakeAsyncClient.responses = [{"memory_context": "demo-dev-app deployment notes"}]
        client = MemoryGatewayClient("http://127.0.0.1:8420", namespace="lodestone")
        with patch("lodestone.memory.client.httpx.AsyncClient", FakeAsyncClient):
            text = await client.recall_scoped(
                "refresh demo-dev-app", scope="agent", agent_id="hermes-a", project_name="demo-dev-app"
            )
        self.assertEqual(text, "demo-dev-app deployment notes")
        self.assertEqual(
            FakeAsyncClient.calls[0]["json"]["session_id"], "lodestone:agent:hermes-a:demo-dev-app"
        )
        self.assertEqual(FakeAsyncClient.calls[0]["json"]["project"], "demo-dev-app")

    async def test_capture_posts_messages_with_agent_metadata(self):
        FakeAsyncClient.responses = [{}]
        client = MemoryGatewayClient("http://127.0.0.1:8420", namespace="lodestone")
        with patch("lodestone.memory.client.httpx.AsyncClient", FakeAsyncClient):
            await client.capture_agent_turn(
                "hermes-a", "refresh data", "done", project_name="demo-dev-app", run_kind="dispatch"
            )
        payload = FakeAsyncClient.calls[0]["json"]
        self.assertEqual(payload["session_id"], "lodestone:agent:hermes-a:demo-dev-app")
        self.assertEqual(payload["messages"][0]["content"], "refresh data")
        self.assertEqual(payload["metadata"]["run_kind"], "dispatch")

    async def test_health_uses_get_health_endpoint(self):
        FakeAsyncClient.responses = [{"status": "ok"}]
        client = MemoryGatewayClient("http://127.0.0.1:8420", namespace="lodestone")
        with patch("lodestone.memory.client.httpx.AsyncClient", FakeAsyncClient):
            status = await client.health()
        self.assertTrue(status["ok"])
        self.assertEqual(FakeAsyncClient.calls[0]["url"], "http://127.0.0.1:8420/health")

    async def test_smoke_test_runs_recall_then_session_end(self):
        FakeAsyncClient.responses = [{"status": "ok"}, {"memory_context": "ok"}, {}]
        client = MemoryGatewayClient("http://127.0.0.1:8420", namespace="lodestone")
        with patch("lodestone.memory.client.httpx.AsyncClient", FakeAsyncClient):
            status = await client.smoke_test()
        self.assertTrue(status["smoke_ok"])
        self.assertEqual(FakeAsyncClient.calls[1]["url"], "http://127.0.0.1:8420/recall")
        self.assertEqual(FakeAsyncClient.calls[2]["url"], "http://127.0.0.1:8420/session/end")
