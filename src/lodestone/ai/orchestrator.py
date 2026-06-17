import json

from ..registry import db
from ..hub import tools

SYSTEM = (
    "You are Lodestone, the orchestrator for a fleet of Telegram agents. "
    "Each agent owns specific projects and has specific permissions. "
    "Use the tools to look up agents and to dispatch tasks to them. "
    "When the user asks to act on a project, find the owning agent first, then "
    "dispatch a clear, self-contained task to it. Be concise and reply in the "
    "user's language."
)


class Orchestrator:
    def __init__(self, provider, hub, max_rounds: int = 5):
        self.provider = provider
        self.hub = hub
        self.max_rounds = max_rounds

    def _fleet_snapshot(self) -> str:
        agents = db.list_agents(self.hub.db_path)
        if not agents:
            return "(no agents configured)"
        lines = []
        for a in agents:
            projects = ", ".join(a["projects"]) or "—"
            perms = ", ".join(a["permissions"]) or "—"
            lines.append(f"- {a['id']} ({a['type']}): projects=[{projects}] perms=[{perms}]")
        return "\n".join(lines)

    async def handle(self, user_text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM + "\n\nCurrent fleet:\n" + self._fleet_snapshot()},
            {"role": "user", "content": user_text},
        ]
        for _ in range(self.max_rounds):
            msg = await self.provider.chat(messages, tools=tools.TOOL_SCHEMAS)
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return msg.get("content") or "(no response)"
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await tools.run_tool(fn.get("name", ""), args, self.hub)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.get("id"), "content": result}
                )
        return "(reached tool-call limit without a final answer)"
