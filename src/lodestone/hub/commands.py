import asyncio

from ..registry import db

HELP_TEXT = (
    "Lodestone — commands\n"
    "/agents — list all agents (projects + permission summary)\n"
    "/agent <id> — full detail + recent activity for one agent\n"
    "/projects — project -> agent map\n"
    "/dispatch <agent_id> <task> — send a task to an agent and report back\n"
    "/help — show this message"
)


def cmd_agents(db_path: str) -> str:
    agents = db.list_agents(db_path)
    if not agents:
        return "No agents yet. Fill in config and run: lodestone sync"
    lines = ["Agents"]
    for a in agents:
        projects = ", ".join(a["projects"]) or "—"
        perms = ", ".join(a["permissions"]) or "—"
        lines.append(
            f"\n• {a['name']} [{a['id']}] ({a['type'] or '?'})"
            f"\n  projects: {projects}"
            f"\n  perms: {perms}"
        )
    return "\n".join(lines)


def cmd_agent(db_path: str, agent_id: str) -> str:
    a = db.get_agent(db_path, agent_id)
    if not a:
        return f"No such agent: {agent_id}"
    projects = ", ".join(a["projects"]) or "—"
    perms = "\n  ".join(a["permissions"]) or "—"
    out = [
        f"{a['name']} [{a['id']}]",
        f"type:    {a['type'] or '?'}",
        f"host:    {a['host'] or '?'}",
        f"channel: {a['telegram_peer'] or '?'}",
        f"projects: {projects}",
        f"perms:\n  {perms}",
    ]
    if a["recent"]:
        out.append("recent:")
        for r in a["recent"]:
            detail = (r["detail"] or "")[:80]
            out.append(f"  [{r['ts']}] {r['kind']}: {detail}")
    return "\n".join(out)


def cmd_projects(db_path: str) -> str:
    rows = db.list_projects(db_path)
    if not rows:
        return "No projects yet."
    lines = ["Projects -> Agent"]
    for r in rows:
        lines.append(f"• {r['name']} -> {r['agent_name']} [{r['agent_id']}]")
    return "\n".join(lines)


async def cmd_dispatch(client, db_path: str, config, agent_id: str, task: str) -> str:
    agent = config.agent(agent_id)
    if not agent:
        return f"No such agent: {agent_id}"
    peer = agent.get("telegram_peer")
    if not peer:
        return f"{agent_id} has no telegram_peer configured."

    timeout = config.dispatch.get("reply_timeout", 60)
    db.log_event(db_path, agent_id, "dispatch", task)
    try:
        async with client.conversation(peer, timeout=timeout) as conv:
            await conv.send_message(task)
            resp = await conv.get_response()
            reply = resp.text or "(empty reply)"
    except asyncio.TimeoutError:
        db.log_event(db_path, agent_id, "timeout", f"no reply in {timeout}s")
        return f"{agent['name']} did not reply within {timeout}s."
    except Exception as e:  # noqa: BLE001 — surface any transport error to the hub
        db.log_event(db_path, agent_id, "error", str(e))
        return f"Dispatch to {agent['name']} failed: {e}"

    db.log_event(db_path, agent_id, "reply", reply[:500])
    return f"{agent['name']} replied:\n\n{reply}"
