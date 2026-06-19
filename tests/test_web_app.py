import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from lodestone.registry import db
from lodestone.web.app import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "lodestone.db")
        self.config = SimpleNamespace(
            db_path=self.db_path,
            web={"token": "secret-token"},
            agents=[
                {
                    "id": "hermes-a",
                    "name": "Hermes A",
                    "type": "hermes",
                    "host": "host-a",
                    "telegram_peer": "@hermes_a_bot",
                    "projects": ["demo-dev-app", "sample-weather-app"],
                    "permissions": ["ec2:hermes-a"],
                },
                {
                    "id": "hermes-b",
                    "name": "Hermes B",
                    "type": "hermes",
                    "host": "host-b",
                    "telegram_peer": "@hermes_b_bot",
                    "projects": ["sample-sports-app"],
                    "permissions": ["ec2:hermes-b"],
                },
            ],
        )
        db.init_db(self.db_path)
        db.sync_from_config(self.db_path, self.config)
        db.log_event(self.db_path, "hermes-a", "dispatch", "[project:demo-dev-app] refresh data")
        db.log_event(self.db_path, "hermes-a", "reply", "done")
        db.log_event(self.db_path, "hermes-b", "error", "network")
        db.log_event(self.db_path, "hermes-a", "memory_recall", "[project:demo-dev-app] refresh demo-dev-app")
        db.log_event(self.db_path, "hermes-a", "memory_search", "[project:demo-dev-app] structured query=deploy habits")
        db.log_event(self.db_path, "hermes-a", "memory_capture", "[project:demo-dev-app] captured dispatch exchange")
        db.log_usage(self.db_path, "hermes-a", "gpt-4o-mini", 100, 50, 150, 0.000045)
        db.log_usage(self.db_path, None, "gpt-4o-mini", 70, 30, 100, 0.000029)
        self.client = TestClient(create_app(self.config))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dashboard_requires_token(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 401)

    def test_index_query_token_sets_cookie(self):
        resp = self.client.get("/?token=secret-token", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/")
        self.assertIn("lodestone_token=secret-token", resp.headers.get("set-cookie", ""))

    def test_stats_accepts_bearer_token(self):
        resp = self.client.get("/api/stats", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["agents"], 2)
        self.assertEqual(payload["projects"], 3)
        self.assertEqual(payload["dispatches"], 1)
        self.assertEqual(payload["errors"], 1)
        self.assertEqual(payload["memory_events"], 3)
        self.assertEqual(payload["memory_recalls"], 1)
        self.assertEqual(payload["llm_calls"], 2)

    def test_activity_endpoint_exposes_by_agent(self):
        resp = self.client.get("/api/activity", headers={"X-Lodestone-Token": "secret-token"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("by_agent", payload)
        self.assertEqual(payload["by_agent"][0]["agent_id"], "hermes-a")

    def test_usage_endpoint_exposes_recent_and_by_agent(self):
        resp = self.client.get("/api/usage?days=30", headers={"X-Lodestone-Token": "secret-token"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("by_agent", payload)
        self.assertIn("recent", payload)
        self.assertEqual(payload["by_agent"][0]["agent_name"], "Hermes A")
        self.assertEqual(payload["recent"][0]["total_tokens"], 100)

    def test_memory_endpoint_exposes_project_and_agent_breakdowns(self):
        resp = self.client.get("/api/memory?days=30&limit=20", headers={"X-Lodestone-Token": "secret-token"})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("status", payload)
        self.assertIn("by_kind", payload)
        self.assertIn("by_agent", payload)
        self.assertIn("by_project", payload)
        self.assertEqual(payload["by_project"][0]["project"], "demo-dev-app")

    def test_memory_endpoint_reports_backend_status(self):
        class FakeMemory:
            async def health(self):
                return {
                    "configured": True,
                    "ok": True,
                    "base_url": "http://127.0.0.1:8420",
                    "namespace": "lodestone",
                    "detail": "ok",
                }

        with patch("lodestone.web.app.build_memory_client", return_value=FakeMemory()):
            client = TestClient(create_app(self.config))
            resp = client.get("/api/memory?days=30&limit=20", headers={"X-Lodestone-Token": "secret-token"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["status"]["ok"])
