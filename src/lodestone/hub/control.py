from . import commands, router


class Hub:
    """Shared context passed to every control surface (bot, userbot, web)."""

    def __init__(self, config, db_path, userbot=None, orchestrator=None, allowed_users=None):
        self.config = config
        self.db_path = db_path
        self.userbot = userbot            # account client (dispatch transport)
        self.orchestrator = orchestrator  # LLM brain, or None if AI disabled
        self.allowed_users = set(allowed_users or [])


async def handle_text(hub: Hub, text: str):
    """Route one message. Slash command -> deterministic. Plain text -> LLM.

    Returns the reply string, or None if nothing should be sent.
    """
    cmd, rest = router.parse(text)

    if cmd is not None:
        if cmd in ("help", "start"):
            extra = "" if hub.orchestrator else "\n(natural language is off: set ai.api_key to enable)"
            return commands.HELP_TEXT + extra
        if cmd == "agents":
            return commands.cmd_agents(hub.db_path)
        if cmd == "agent":
            aid = rest.split()[0] if rest else ""
            return commands.cmd_agent(hub.db_path, aid) if aid else "Usage: /agent <id>"
        if cmd == "projects":
            return commands.cmd_projects(hub.db_path)
        if cmd == "dispatch":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /dispatch <agent_id> <task>"
            if hub.userbot is None:
                return "dispatch unavailable: userbot (account) is not connected"
            return await commands.cmd_dispatch(hub.userbot, hub.db_path, hub.config, parts[0], parts[1])
        return None  # unknown slash command -> stay quiet

    # natural language
    if hub.orchestrator is None:
        return "AI is not configured. Use /help to see available commands."
    return await hub.orchestrator.handle(text)
