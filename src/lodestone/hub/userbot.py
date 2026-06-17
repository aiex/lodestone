from telethon import TelegramClient, events

from . import control


def build_account_client(config) -> TelegramClient:
    """The real-account client. Its job is the back leg: messaging agent bots
    and reading their replies (a bot cannot do this)."""
    tg = config.telegram
    session = tg.get("session", "data/lodestone.session")
    return TelegramClient(session, int(tg["api_id"]), tg["api_hash"])


def attach_hub_handler(client: TelegramClient, hub: control.Hub) -> None:
    """Fallback control surface when no front-door bot is configured: the
    account listens for commands in the hub group."""
    hub_chat_id = hub.config.telegram.get("hub_chat_id") or None
    if not hub_chat_id:
        print("WARNING: no bot_token and no hub_chat_id — there is no control "
              "surface. Set telegram.bot_token or telegram.hub_chat_id.")

    @client.on(events.NewMessage(chats=hub_chat_id))
    async def _on_message(event):
        reply = await control.handle_text(hub, event.raw_text)
        if reply:
            await event.reply(reply)
