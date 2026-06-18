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
            "name": "get_project_owner",
            "description": "Look up which agent owns a project by its name.",
            "parameters": {
                "type": "object",
                "properties": {"project_name": {"type": "string"}},
                "required": ["project_name"],
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
    {
        "type": "function",
        "function": {
            "name": "dispatch_project",
            "description": "Dispatch a task by project name after validating its owning agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "task": {
                        "type": "string",
                        "description": "The task/message to send to the project's owning agent.",
                    },
                },
                "required": ["project_name", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_loop",
            "description": (
                "Begin an autonomous Agent Loop for a project: the owning agent "
                "works the task across many steps on its own. This only ESTIMATES "
                "the cost and returns a task id; the human must confirm before it "
                "runs, and a live project will pause for human PR approval. Use "
                "this when the user wants a whole task driven to completion "
                "without step-by-step babysitting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "task": {"type": "string",
                             "description": "The complete business task for the agent to finish."},
                },
                "required": ["project_name", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "loop_status",
            "description": "Show active Agent Loops and their budget consumption.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def run_tool(name: str, args: dict, hub) -> str:
    if name == "list_agents":
        return commands.cmd_agents(hub.db_path)
    if name == "get_agent":
        return commands.cmd_agent(hub.db_path, args.get("agent_id", ""))
    if name == "get_project_owner":
        return commands.cmd_project(hub.db_path, args.get("project_name", ""))
    if name == "dispatch":
        if hub.userbot is None:
            return "dispatch unavailable: userbot (account) is not connected"
        return await commands.cmd_dispatch(
            hub.userbot, hub.db_path, hub.config,
            args.get("agent_id", ""), args.get("task", ""),
        )
    if name == "dispatch_project":
        if hub.userbot is None:
            return "dispatch unavailable: userbot (account) is not connected"
        return await commands.cmd_dispatch_project(
            hub.userbot, hub.db_path, hub.config,
            args.get("project_name", ""), args.get("task", ""),
        )
    if name == "start_loop":
        # Estimate only — the human-facing confirm and the live PR gate are
        # enforced in the supervisor (code), never by the model's discretion.
        return await commands.cmd_loop(
            hub, args.get("project_name", ""), args.get("task", ""),
        )
    if name == "loop_status":
        return await commands.cmd_loop_status(hub)
    return f"unknown tool: {name}"
