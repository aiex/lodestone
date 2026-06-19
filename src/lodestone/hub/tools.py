"""Tool layer for deterministic hub operations exposed to the LLM.

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
                    "required_permissions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concrete permission scopes the task requires.",
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
                    "required_permissions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concrete permission scopes the task requires.",
                    },
                },
                "required": ["project_name", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_agent_memory",
            "description": "Search one agent's structured long-term memory before assigning work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "project_name": {"type": "string"},
                    "memory_type": {
                        "type": "string",
                        "enum": ["persona", "episodic", "instruction"],
                    },
                },
                "required": ["agent_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_agent_conversation",
            "description": "Search one agent's raw conversation history before assigning work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "project_name": {"type": "string"},
                },
                "required": ["agent_id", "query"],
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
                    "required_permissions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concrete permission scopes the loop requires.",
                    },
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
            required_permissions=args.get("required_permissions"),
            memory=getattr(hub, "memory", None),
        )
    if name == "dispatch_project":
        if hub.userbot is None:
            return "dispatch unavailable: userbot (account) is not connected"
        return await commands.cmd_dispatch_project(
            hub.userbot, hub.db_path, hub.config,
            args.get("project_name", ""), args.get("task", ""),
            required_permissions=args.get("required_permissions"),
            memory=getattr(hub, "memory", None),
        )
    if name == "search_agent_memory":
        memory = getattr(hub, "memory", None)
        if memory is None:
            return "Memory backend is not configured."
        try:
            result = await memory.search_agent_memory(
                args.get("agent_id", ""),
                args.get("query", ""),
                limit=args.get("limit", 5),
                memory_type=args.get("memory_type", ""),
                project_name=args.get("project_name", ""),
            )
        except Exception as e:
            db.log_event(
                hub.db_path,
                args.get("agent_id"),
                "memory_error",
                f"[project:{args.get('project_name', '')}] memory search failed: {e}"[:500],
            )
            return f"Memory search failed: {e}"
        db.log_event(
            hub.db_path,
            args.get("agent_id"),
            "memory_search",
            f"[project:{args.get('project_name', '')}] structured query={args.get('query', '')[:120]}",
        )
        return result or "(no structured memories found)"
    if name == "search_agent_conversation":
        memory = getattr(hub, "memory", None)
        if memory is None:
            return "Memory backend is not configured."
        try:
            result = await memory.search_agent_conversation(
                args.get("agent_id", ""),
                args.get("query", ""),
                limit=args.get("limit", 5),
                project_name=args.get("project_name", ""),
            )
        except Exception as e:
            db.log_event(
                hub.db_path,
                args.get("agent_id"),
                "memory_error",
                f"[project:{args.get('project_name', '')}] conversation search failed: {e}"[:500],
            )
            return f"Conversation search failed: {e}"
        db.log_event(
            hub.db_path,
            args.get("agent_id"),
            "memory_search",
            f"[project:{args.get('project_name', '')}] conversation query={args.get('query', '')[:120]}",
        )
        return result or "(no conversation memories found)"
    if name == "start_loop":
        # Estimate only — the human-facing confirm and the live PR gate are
        # enforced in the supervisor (code), never by the model's discretion.
        return await commands.cmd_loop(
            hub, args.get("project_name", ""), args.get("task", ""),
            required_permissions=args.get("required_permissions"),
        )
    if name == "loop_status":
        return await commands.cmd_loop_status(hub)
    return f"unknown tool: {name}"
