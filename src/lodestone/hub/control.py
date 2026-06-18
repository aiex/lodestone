import asyncio

from . import commands, router


class Hub:
    """Shared context passed to every control surface (bot, userbot, web)."""

    def __init__(self, config, db_path, userbot=None, orchestrator=None, allowed_users=None):
        self.config = config
        self.db_path = db_path
        self.userbot = userbot            # account client (dispatch transport)
        self.orchestrator = orchestrator  # LLM brain, or None if AI disabled
        self.allowed_users = set(allowed_users or [])
        self._loop_supervisor = None

    def loop_supervisor(self):
        """The Agent Loop supervisor, built lazily over the account transport.

        Cached on first use. Returns None if there is no userbot to message
        agents through.
        """
        if self.userbot is None:
            return None
        if self._loop_supervisor is None:
            from .loop import LoopSupervisor

            # A loop step may take far longer than a one-shot dispatch (the agent
            # is doing real work between checkpoints), so it waits up to
            # loop.heartbeat_timeout, falling back to the dispatch timeout.
            loop_cfg = self.config.loop if hasattr(self.config, "loop") else {}
            timeout = (loop_cfg or {}).get(
                "heartbeat_timeout", self.config.dispatch.get("reply_timeout", 60))

            async def send_and_wait(peer, text):
                try:
                    async with self.userbot.conversation(peer, timeout=timeout) as conv:
                        await conv.send_message(text)
                        resp = await conv.get_response()
                        return resp.text or ""
                except asyncio.TimeoutError:
                    raise TimeoutError(f"no checkpoint within {timeout}s")

            self._loop_supervisor = LoopSupervisor(self.db_path, self.config, send_and_wait)
        return self._loop_supervisor


def is_allowed_sender(hub: Hub, sender_id) -> bool:
    """Fail safe: an empty allow-list means nobody may control the fleet."""
    return bool(hub.allowed_users) and sender_id in hub.allowed_users


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
        if cmd == "project":
            project_name = rest.split()[0] if rest else ""
            return commands.cmd_project(hub.db_path, project_name) if project_name else "Usage: /project <name>"
        if cmd == "projects":
            return commands.cmd_projects(hub.db_path)
        if cmd == "dispatch":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /dispatch <agent_id> <task>"
            if hub.userbot is None:
                return "dispatch unavailable: userbot (account) is not connected"
            return await commands.cmd_dispatch(hub.userbot, hub.db_path, hub.config, parts[0], parts[1])
        if cmd == "dispatch_project":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /dispatch_project <project> <task>"
            if hub.userbot is None:
                return "dispatch unavailable: userbot (account) is not connected"
            return await commands.cmd_dispatch_project(
                hub.userbot, hub.db_path, hub.config, parts[0], parts[1]
            )
        if cmd == "loop":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /loop <project> <task>"
            return await commands.cmd_loop(hub, parts[0], parts[1])
        if cmd == "loop_confirm":
            tid = rest.split()[0] if rest else ""
            return await commands.cmd_loop_confirm(hub, tid) if tid else "Usage: /loop_confirm <task_id>"
        if cmd == "loop_status":
            tid = rest.split()[0] if rest else None
            return await commands.cmd_loop_status(hub, tid)
        if cmd == "loop_input":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return "Usage: /loop_input <task_id> <text>"
            return await commands.cmd_loop_input(hub, parts[0], parts[1])
        if cmd == "approve":
            tid = rest.split()[0] if rest else ""
            return await commands.cmd_loop_approve(hub, tid) if tid else "Usage: /approve <task_id>"
        if cmd == "reject":
            parts = rest.split(maxsplit=1)
            tid = parts[0] if parts else ""
            reason = parts[1] if len(parts) > 1 else ""
            return await commands.cmd_loop_reject(hub, tid, reason) if tid else "Usage: /reject <task_id> [reason]"
        if cmd == "loop_stop":
            tid = rest.split()[0] if rest else ""
            return await commands.cmd_loop_stop(hub, tid) if tid else "Usage: /loop_stop <task_id>"
        return None  # unknown slash command -> stay quiet

    # natural language
    if hub.orchestrator is None:
        return "AI is not configured. Use /help to see available commands."
    return await hub.orchestrator.handle(text)
