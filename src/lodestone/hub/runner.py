import asyncio

from ..config import load_config
from ..memory import build_memory_client
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
        memory=build_memory_client(config),
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
    if hub.memory is not None:
        status = await hub.memory.health()
        state = "healthy" if status.get("ok") else "unhealthy"
        print(f"Memory backend={state}  base_url={status.get('base_url')}  detail={status.get('detail')}")

    coros = [account.run_until_disconnected()]
    if bot:
        await bot.start(bot_token=bot_token)
        print("Front-door bot=connected")
        coros.append(bot.run_until_disconnected())
    try:
        await asyncio.gather(*coros)
    finally:
        await _shutdown_memory(hub)


async def _shutdown_memory(hub) -> None:
    memory = getattr(hub, "memory", None)
    if memory is None:
        return
    try:
        await memory.session_end("orchestrator")
    except Exception:
        pass
    seen = set()
    for agent in getattr(hub.config, "agents", []) or []:
        agent_id = agent.get("id") or ""
        if agent_id and ("agent", agent_id, "") not in seen:
            seen.add(("agent", agent_id, ""))
            try:
                await memory.session_end("agent", agent_id=agent_id)
            except Exception:
                pass
        for proj in agent.get("projects", []) or []:
            if isinstance(proj, dict):
                project_name = (proj.get("name") or "").strip()
            else:
                project_name = str(proj).strip()
            key = ("agent", agent_id, project_name)
            if not agent_id or not project_name or key in seen:
                continue
            seen.add(key)
            try:
                await memory.session_end("agent", agent_id=agent_id, project_name=project_name)
            except Exception:
                pass
