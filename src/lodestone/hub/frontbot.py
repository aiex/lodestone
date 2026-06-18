from telethon import TelegramClient, events

from . import control


def build_bot_client(config) -> TelegramClient:
    tg = config.telegram
    session = tg.get("bot_session", "data/lodestone-bot.session")
    return TelegramClient(session, int(tg["api_id"]), tg["api_hash"])


def attach(bot: TelegramClient, hub: control.Hub) -> None:
    @bot.on(events.NewMessage(incoming=True))
    async def _on_message(event):
        # Only whitelisted users may command the fleet. If allowed_users is
        # empty, refuse everyone (fail safe — you must opt people in).
        if not control.is_allowed_sender(hub, event.sender_id):
            return
        reply = await control.handle_text(hub, event.raw_text)
        if reply:
            await event.reply(reply)
