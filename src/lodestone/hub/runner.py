import asyncio

from ..config import load_config
from ..registry import db
from . import control, frontbot, userbot


def _build_orchestrator(config, hub):
    ai = config.ai
    if not ai.get("api_key"):
        return None  # AI disabled -> slash commands only
    from ..ai.orchestrator import Orchestrator
    from ..ai.provider import OpenAICompatProvider
    provider = OpenAICompatProvider(
        ai.get("base_url", "https://api.openai.com/v1"),
        ai["api_key"],
        ai.get("model", "gpt-4o-mini"),
    )
    return Orchestrator(provider, hub)


def run(config_path: str = None) -> None:
    config = load_config(config_path)
    db_path = config.db_path
    db.sync_from_config(db_path, config)

    tg = config.telegram
    account = userbot.build_account_client(config)

    # Hub first (orchestrator needs it), then wire the orchestrator back in.
    hub = control.Hub(
        config, db_path,
        userbot=account,
        orchestrator=None,
        allowed_users=tg.get("allowed_users") or [],
    )
    hub.orchestrator = _build_orchestrator(config, hub)

    bot_token = tg.get("bot_token")
    bot = frontbot.build_bot_client(config) if bot_token else None
    if bot:
        frontbot.attach(bot, hub)
    else:
        userbot.attach_hub_handler(account, hub)

    asyncio.run(_amain(account, bot, bot_token, hub))


async def _amain(account, bot, bot_token, hub) -> None:
    await account.connect()
    if not await account.is_user_authorized():
        print("Account not logged in. Run `lodestone login` once, then `lodestone run`.")
        return

    brain = "ON" if hub.orchestrator else "OFF (slash commands only)"
    print(f"Lodestone running. account=connected  AI brain={brain}")

    coros = [account.run_until_disconnected()]
    if bot:
        await bot.start(bot_token=bot_token)
        print("Front-door bot=connected")
        coros.append(bot.run_until_disconnected())
    await asyncio.gather(*coros)
