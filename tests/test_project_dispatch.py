import tempfile
import unittest
from types import SimpleNamespace

from lodestone.hub import commands
from lodestone.registry import db


class FakeConversation:
    def __init__(self, client, peer):
        self.client = client
        self.peer = peer

    async def __aenter__(self):
        self.client.peers.append(self.peer)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send_message(self, text):
        self.client.messages.append(text)

    async def get_response(self):
        return SimpleNamespace(text="done")


class FakeClient:
    def __init__(self):
        self.peers = []
        self.messages = []

    def conversation(self, peer, timeout):
        self.timeout = timeout
        return FakeConversation(self, peer)


class FakeConfig:
    def __init__(self):
        self.dispatch = {"reply_timeout": 15}
        self.agents = [
            {
                "id": "hermes-a",
                "name": "Hermes A",
                "type": "hermes",
                "host": "host-a",
                "telegram_peer": "@hermes_a_bot",
                "projects": ["cricap", "indiweather"],
                "permissions": ["ec2:hermes-a"],
            },
            {
                "id": "hermes-b",
                "name": "Hermes B",
                "type": "hermes",
                "host": "host-b",
                "telegram_peer": "@hermes_b_bot",
                "projects": ["96football"],
                "permissions": ["ec2:hermes-b"],
            },
        ]

    def agent(self, agent_id):
        for agent in self.agents:
            if agent["id"] == agent_id:
                return agent
        return None


class FakeMemory:
    def __init__(self):
        self.captures = []

    async def capture_agent_turn(self, agent_id, user_text, assistant_text, **kwargs):
        self.captures.append(
            {
                "agent_id": agent_id,
                "user_text": user_text,
                "assistant_text": assistant_text,
                **kwargs,
            }
        )


class ProjectDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tmpdir.name}/lodestone.db"
        self.config = FakeConfig()
        db.init_db(self.db_path)
        db.sync_from_config(self.db_path, self.config)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_cmd_project_returns_owner(self):
        self.assertEqual(
            commands.cmd_project(self.db_path, "cricap"),
            "cricap -> Hermes A [hermes-a]",
        )

    def test_project_status_defaults_to_dev(self):
        # Bare-string projects sync as status 'dev' (backward compatible).
        self.assertEqual(db.get_project(self.db_path, "cricap")["status"], "dev")

    def test_project_status_mapping_form_is_live(self):
        # Re-sync with a mapping-form project marked live.
        self.config.agents[0]["projects"] = [
            "cricap", {"name": "indiweather", "status": "live"},
        ]
        db.sync_from_config(self.db_path, self.config)
        self.assertEqual(db.get_project(self.db_path, "indiweather")["status"], "live")
        self.assertEqual(db.get_project(self.db_path, "cricap")["status"], "dev")

    def test_sync_rejects_duplicate_project_owners(self):
        self.config.agents[1]["projects"] = ["96football", "cricap"]
        with self.assertRaisesRegex(ValueError, "assigned to multiple agents"):
            db.sync_from_config(self.db_path, self.config)

    async def test_cmd_dispatch_project_rejects_unknown_project(self):
        client = FakeClient()
        reply = await commands.cmd_dispatch_project(
            client, self.db_path, self.config, "unknown-project", "refresh data"
        )
        self.assertEqual(reply, "No such project: unknown-project")
        self.assertEqual(client.peers, [])

    async def test_cmd_dispatch_project_routes_to_owner_and_logs_project(self):
        client = FakeClient()
        memory = FakeMemory()
        reply = await commands.cmd_dispatch_project(
            client, self.db_path, self.config, "cricap", "refresh data", memory=memory
        )

        self.assertEqual(reply, "Hermes A replied:\n\ndone")
        self.assertEqual(client.peers, ["@hermes_a_bot"])
        self.assertEqual(client.messages, ["refresh data"])

        recent = db.recent_logs(self.db_path, limit=1)
        self.assertEqual(recent[0]["kind"], "reply")

        dispatch_log = next(r for r in db.recent_logs(self.db_path, limit=4) if r["kind"] == "dispatch")
        self.assertEqual(dispatch_log["detail"], "[project:cricap] refresh data")
        self.assertEqual(memory.captures[0]["project_name"], "cricap")
        self.assertEqual(memory.captures[0]["assistant_text"], "done")

    async def test_cmd_dispatch_enforces_inline_required_permissions(self):
        client = FakeClient()
        reply = await commands.cmd_dispatch(
            client, self.db_path, self.config,
            "hermes-a", "[requires:ec2:hermes-a] refresh data"
        )
        self.assertEqual(reply, "Hermes A replied:\n\ndone")
        self.assertEqual(client.messages, ["refresh data"])
        dispatch_log = db.recent_logs(self.db_path, limit=2)[1]
        self.assertEqual(
            dispatch_log["detail"],
            "[requires:ec2:hermes-a] refresh data",
        )

    async def test_cmd_dispatch_rejects_missing_required_permissions(self):
        client = FakeClient()
        reply = await commands.cmd_dispatch(
            client, self.db_path, self.config,
            "hermes-a", "refresh data", required_permissions=["gateway:prod"],
        )
        self.assertIn("lacks required permissions", reply)
        self.assertEqual(client.peers, [])

    async def test_cmd_dispatch_honors_wildcard_permission(self):
        self.config.agents[0]["permissions"] = ["ec2:*"]
        client = FakeClient()
        reply = await commands.cmd_dispatch(
            client, self.db_path, self.config,
            "hermes-a", "refresh data", required_permissions=["ec2:hermes-a"],
        )
        self.assertEqual(reply, "Hermes A replied:\n\ndone")
        self.assertEqual(client.peers, ["@hermes_a_bot"])

    async def test_cmd_dispatch_rejects_project_mismatch(self):
        client = FakeClient()
        reply = await commands.cmd_dispatch(
            client, self.db_path, self.config, "hermes-b", "refresh data", project_name="cricap"
        )
        self.assertEqual(reply, "hermes-b does not own project: cricap")
        self.assertEqual(client.peers, [])

    async def test_cmd_dispatch_accepts_dict_form_owned_project(self):
        # Membership must normalize dict-form projects, not compare raw entries.
        self.config.agents[0]["projects"] = [{"name": "cricap", "status": "live"}]
        client = FakeClient()
        reply = await commands.cmd_dispatch(
            client, self.db_path, self.config, "hermes-a", "refresh data", project_name="cricap"
        )
        self.assertEqual(reply, "Hermes A replied:\n\ndone")
        self.assertEqual(client.peers, ["@hermes_a_bot"])
