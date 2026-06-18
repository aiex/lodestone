import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

telethon = types.ModuleType("telethon")


class DummyTelegramClient:
    pass


class DummyEvents:
    @staticmethod
    def NewMessage(**kwargs):
        return kwargs


telethon.TelegramClient = DummyTelegramClient
telethon.events = DummyEvents
sys.modules.setdefault("telethon", telethon)

from lodestone.hub import control, router, userbot


class FakeClient:
    def __init__(self):
        self.handlers = []

    def on(self, event):
        def _decorator(fn):
            self.handlers.append((event, fn))
            return fn

        return _decorator


class FakeEvent:
    def __init__(self, sender_id, raw_text):
        self.sender_id = sender_id
        self.raw_text = raw_text
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)


def make_hub(hub_chat_id=12345, allowed_users=None):
    config = SimpleNamespace(telegram={"hub_chat_id": hub_chat_id})
    return control.Hub(config=config, db_path=":memory:", allowed_users=allowed_users or [])


class HubSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def test_is_allowed_sender_is_fail_safe(self):
        hub = make_hub(allowed_users=[])
        self.assertFalse(control.is_allowed_sender(hub, 1))

        hub = make_hub(allowed_users=[1, 2])
        self.assertTrue(control.is_allowed_sender(hub, 1))
        self.assertFalse(control.is_allowed_sender(hub, 3))

    def test_router_parse_handles_bot_suffix(self):
        self.assertEqual(router.parse("/agents@lodestone_bot"), ("agents", ""))
        self.assertEqual(router.parse(" /dispatch@lodestone_bot a1 hello "), ("dispatch", "a1 hello"))
        self.assertEqual(router.parse("hello"), (None, ""))

    async def test_handle_text_routes_project_command(self):
        hub = make_hub()
        with patch.object(userbot.control.commands, "cmd_project", return_value="cricap -> Hermes A [hermes-a]") as mocked:
            reply = await control.handle_text(hub, "/project cricap")
        mocked.assert_called_once_with(hub.db_path, "cricap")
        self.assertEqual(reply, "cricap -> Hermes A [hermes-a]")

    async def test_handle_text_routes_dispatch_project_command(self):
        hub = make_hub(allowed_users=[1])
        hub.userbot = object()
        with patch.object(userbot.control.commands, "cmd_dispatch_project", AsyncMock(return_value="ok")) as mocked:
            reply = await control.handle_text(hub, "/dispatch_project cricap refresh data")
        mocked.assert_awaited_once_with(hub.userbot, hub.db_path, hub.config, "cricap", "refresh data")
        self.assertEqual(reply, "ok")

    def test_attach_hub_handler_requires_hub_chat_id(self):
        client = FakeClient()
        hub = make_hub(hub_chat_id=0, allowed_users=[1])
        with redirect_stdout(io.StringIO()):
            userbot.attach_hub_handler(client, hub)
        self.assertEqual(client.handlers, [])

    def test_attach_hub_handler_requires_allowed_users(self):
        client = FakeClient()
        hub = make_hub(hub_chat_id=12345, allowed_users=[])
        with redirect_stdout(io.StringIO()):
            userbot.attach_hub_handler(client, hub)
        self.assertEqual(client.handlers, [])

    async def test_attach_hub_handler_ignores_unauthorized_sender(self):
        client = FakeClient()
        hub = make_hub(hub_chat_id=12345, allowed_users=[1])
        userbot.attach_hub_handler(client, hub)
        _, handler = client.handlers[0]
        event = FakeEvent(sender_id=2, raw_text="/agents")

        with patch.object(userbot.control, "handle_text", AsyncMock(return_value="should-not-run")) as mocked:
            await handler(event)

        mocked.assert_not_awaited()
        self.assertEqual(event.replies, [])

    async def test_attach_hub_handler_replies_for_authorized_sender(self):
        client = FakeClient()
        hub = make_hub(hub_chat_id=12345, allowed_users=[1])
        userbot.attach_hub_handler(client, hub)
        _, handler = client.handlers[0]
        event = FakeEvent(sender_id=1, raw_text="/agents")

        with patch.object(userbot.control, "handle_text", AsyncMock(return_value="ok")) as mocked:
            await handler(event)

        mocked.assert_awaited_once_with(hub, "/agents")
        self.assertEqual(event.replies, ["ok"])
