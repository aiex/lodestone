"""Tool layer: the deterministic Phase 1 operations, exposed to the LLM.

The same functions back both the slash commands and the LLM's tool calls, so
there is one source of truth for what the hub can actually do.
"""

from ..registry import db
from . import commands

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "List all agents with their projects and permissions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent",
            "description": "Get full detail and recent activity for one agent by id.",
            "parameters": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dispatch",
            "description": "Send a task to a specific agent and return its reply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": "The task/message to send to the agent.",
                    },
                },
                "required": ["agent_id", "task"],
            },
        },
    },
]


async def run_tool(name: str, args: dict, hub) -> str:
    if name == "list_agents":
        return commands.cmd_agents(hub.db_path)
    if name == "get_agent":
        return commands.cmd_agent(hub.db_path, args.get("agent_id", ""))
    if name == "dispatch":
        if hub.userbot is None:
            return "dispatch unavailable: userbot (account) is not connected"
        return await commands.cmd_dispatch(
            hub.userbot, hub.db_path, hub.config,
            args.get("agent_id", ""), args.get("task", ""),
        )
    return f"unknown tool: {name}"
