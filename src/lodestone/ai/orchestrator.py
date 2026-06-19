import json

from ..registry import db
from ..hub import tools
from .cost import cost_usd

SYSTEM = (
    "You are Lodestone, the orchestrator for a fleet of Telegram agents. "
    "Each agent owns specific projects and has specific permissions. "
    "Use the tools to look up agents, inspect their memory when useful, and dispatch tasks to them. "
    "When the user asks to act on a project, use the project tools first and "
    "prefer dispatch_project so routing is validated against the registry. "
    "Before assigning work that depends on prior context, search the target "
    "agent's memory or conversation history. "
    "When a task needs infrastructure or data access, include concrete "
    "required_permissions in the tool call so execution is checked against the "
    "agent's declared scopes. "
    "Only use dispatch when the user explicitly targets an agent instead of a "
    "project. Be concise and reply in the user's language."
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

    def _record_usage(self, usage: dict) -> None:
        """Persist one call's tokens + estimated cost for the dashboard."""
        if not usage:
            return
        pt = int(usage.get("prompt_tokens", 0) or 0)
        ct = int(usage.get("completion_tokens", 0) or 0)
        tt = int(usage.get("total_tokens", 0) or (pt + ct))
        model = getattr(self.provider, "model", None)
        pricing = self.hub.config.ai.get("pricing")
        db.log_usage(self.hub.db_path, None, model, pt, ct, tt,
                     cost_usd(model, pt, ct, pricing))

    def _mentioned_projects(self, user_text: str) -> list:
        text = (user_text or "").casefold()
        hits = []
        for row in db.list_projects(self.hub.db_path):
            name = (row.get("name") or "").strip()
            if name and name.casefold() in text:
                hits.append(row)
        hits.sort(key=lambda r: (-len(r.get("name") or ""), r.get("name") or ""))
        seen = set()
        out = []
        for row in hits:
            name = row.get("name")
            if name in seen:
                continue
            seen.add(name)
            out.append(row)
        return out[:3]

    async def _memory_prefetch(self, user_text: str) -> str:
        memory = getattr(self.hub, "memory", None)
        if memory is None:
            return ""
        sections = []
        try:
            generic = await memory.recall(user_text, scope="orchestrator")
            if generic:
                db.log_event(self.hub.db_path, None, "memory_recall",
                             f"[scope:orchestrator] {user_text[:120]}")
                sections.append("## Fleet\n" + generic)
        except Exception as e:
            db.log_event(self.hub.db_path, None, "memory_error",
                         f"[scope:orchestrator] recall failed: {e}"[:500])
        for row in self._mentioned_projects(user_text):
            try:
                scoped = await memory.recall_scoped(
                    user_text,
                    scope="agent",
                    agent_id=row.get("agent_id") or "",
                    project_name=row.get("name") or "",
                )
            except Exception as e:
                db.log_event(
                    self.hub.db_path,
                    row.get("agent_id"),
                    "memory_error",
                    f"[project:{row.get('name')}] scoped recall failed: {e}"[:500],
                )
                continue
            if not scoped:
                continue
            db.log_event(
                self.hub.db_path,
                row.get("agent_id"),
                "memory_recall",
                f"[project:{row.get('name')}] {user_text[:120]}",
            )
            sections.append(
                f"## Project {row.get('name')} / {row.get('agent_name')}\n{scoped}"
            )
        return "\n\n".join(sections).strip()

    async def handle(self, user_text: str) -> str:
        messages = [{"role": "system", "content": SYSTEM + "\n\nCurrent fleet:\n" + self._fleet_snapshot()}]
        memory_context = await self._memory_prefetch(user_text)
        if memory_context:
            messages.append(
                {
                    "role": "system",
                    "content": "<memory-context>\n" + memory_context + "\n</memory-context>",
                }
            )
        messages.append({"role": "user", "content": user_text})
        for _ in range(self.max_rounds):
            msg, usage = await self.provider.chat(messages, tools=tools.TOOL_SCHEMAS)
            self._record_usage(usage)
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final = msg.get("content") or "(no response)"
                memory = getattr(self.hub, "memory", None)
                if memory is not None:
                    try:
                        await memory.capture_orchestrator_turn(user_text, final)
                        db.log_event(self.hub.db_path, None, "memory_capture",
                                     "[scope:orchestrator] captured final reply")
                    except Exception:
                        db.log_event(self.hub.db_path, None, "memory_error",
                                     "[scope:orchestrator] capture failed")
                return final
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
