import asyncio

from ..config import normalize_project
from ..registry import db
from . import permissions as permission_mod

HELP_TEXT = (
    "Lodestone — commands\n"
    "/agents — list all agents (projects + permission summary)\n"
    "/agent <id> — full detail + recent activity for one agent\n"
    "/project <name> — show which agent owns one project\n"
    "/projects — project -> agent map\n"
    "/memory_status — check the memory backend health\n"
    "/memory_search <agent_id> <query> — search one agent's memory\n"
    "/memory_search_project <project> <query> — search memory scoped to one project owner\n"
    "/dispatch <agent_id> <task> — send a task to an agent and report back\n"
    "/dispatch_project <project> <task> — route by project owner and dispatch\n"
    "/loop <project> <task> — estimate an autonomous Agent Loop (then confirm)\n"
    "/loop_confirm <task_id> — start the estimated loop\n"
    "/loop_status [task_id] — show running loops + budget\n"
    "/loop_input <task_id> <text> — answer a BLOCKED loop\n"
    "/approve <task_id> — approve a live project's PR so it can deploy\n"
    "/reject <task_id> — reject a live project's PR and stop the loop\n"
    "/loop_stop <task_id> — stop a running loop\n"
    "Prefix a task with [requires:scope1,scope2] to enforce agent permissions\n"
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


def cmd_project(db_path: str, project_name: str) -> str:
    try:
        row = db.get_project(db_path, project_name)
    except ValueError as e:
        return str(e)
    if not row:
        return f"No such project: {project_name}"
    return f"{row['name']} -> {row['agent_name']} [{row['agent_id']}]"


async def cmd_dispatch(client, db_path: str, config, agent_id: str, task: str,
                       project_name: str = None, required_permissions=None, memory=None) -> str:
    agent = config.agent(agent_id)
    if not agent:
        return f"No such agent: {agent_id}"
    clean_task, scopes = permission_mod.extract_task_requirements(task, required_permissions)
    if not clean_task:
        return "Task cannot be empty."
    peer = agent.get("telegram_peer")
    if not peer:
        return f"{agent_id} has no telegram_peer configured."
    if project_name:
        owned = [normalize_project(p)[0] for p in agent.get("projects", []) or []]
        if project_name not in owned:
            return f"{agent_id} does not own project: {project_name}"
    missing = permission_mod.missing_permissions(agent, scopes)
    if missing:
        return permission_mod.permission_denied_message(agent_id, agent, missing)

    timeout = config.dispatch.get("reply_timeout", 60)
    detail = permission_mod.annotate_detail(clean_task, project_name, scopes)
    db.log_event(db_path, agent_id, "dispatch", detail)
    try:
        async with client.conversation(peer, timeout=timeout) as conv:
            await conv.send_message(clean_task)
            resp = await conv.get_response()
            reply = resp.text or "(empty reply)"
    except asyncio.TimeoutError:
        db.log_event(db_path, agent_id, "timeout", f"no reply in {timeout}s")
        return f"{agent['name']} did not reply within {timeout}s."
    except Exception as e:  # noqa: BLE001 — surface any transport error to the hub
        db.log_event(db_path, agent_id, "error", str(e))
        return f"Dispatch to {agent['name']} failed: {e}"

    if memory is not None:
        try:
            await memory.capture_agent_turn(
                agent_id,
                clean_task,
                reply,
                project_name=project_name or "",
                run_kind="dispatch",
            )
            db.log_event(
                db_path,
                agent_id,
                "memory_capture",
                permission_mod.annotate_detail("captured dispatch exchange", project_name),
            )
        except Exception:
            db.log_event(
                db_path,
                agent_id,
                "memory_error",
                permission_mod.annotate_detail("dispatch capture failed", project_name),
            )

    db.log_event(db_path, agent_id, "reply", reply[:500])
    return f"{agent['name']} replied:\n\n{reply}"


async def cmd_dispatch_project(client, db_path: str, config, project_name: str, task: str,
                               required_permissions=None, memory=None) -> str:
    try:
        row = db.get_project(db_path, project_name)
    except ValueError as e:
        return str(e)
    if not row:
        return f"No such project: {project_name}"
    return await cmd_dispatch(
        client, db_path, config, row["agent_id"], task,
        project_name=project_name, required_permissions=required_permissions, memory=memory
    )


async def cmd_memory_status(hub) -> str:
    memory = getattr(hub, "memory", None)
    if memory is None:
        return "Memory backend is disabled in config."
    status = await memory.health()
    lines = [
        "Memory backend",
        f"configured: yes",
        f"healthy: {'yes' if status.get('ok') else 'no'}",
        f"base_url: {status.get('base_url') or '—'}",
        f"namespace: {status.get('namespace') or '—'}",
        f"detail: {status.get('detail') or '—'}",
    ]
    return "\n".join(lines)


async def cmd_memory_search_agent(hub, agent_id: str, query: str, project_name: str = "") -> str:
    memory = getattr(hub, "memory", None)
    if memory is None:
        return "Memory backend is disabled in config."
    agent = hub.config.agent(agent_id)
    if not agent:
        return f"No such agent: {agent_id}"
    try:
        result = await memory.search_agent_memory(
            agent_id, query, limit=5, project_name=project_name or ""
        )
    except Exception as e:
        db.log_event(
            hub.db_path,
            agent_id,
            "memory_error",
            permission_mod.annotate_detail(f"memory search failed: {e}", project_name or None),
        )
        return f"Memory search failed: {e}"
    db.log_event(
        hub.db_path,
        agent_id,
        "memory_search",
        permission_mod.annotate_detail(f"structured query={query[:120]}", project_name or None),
    )
    title = f"Memory for {agent.get('name') or agent_id} [{agent_id}]"
    if project_name:
        title += f" / {project_name}"
    return title + "\n" + (result or "(no structured memories found)")


async def cmd_memory_search_project(hub, project_name: str, query: str) -> str:
    try:
        row = db.get_project(hub.db_path, project_name)
    except ValueError as e:
        return str(e)
    if not row:
        return f"No such project: {project_name}"
    return await cmd_memory_search_agent(hub, row["agent_id"], query, project_name=project_name)


# --- Agent Loop commands (Phase 4) -----------------------------------------
# These delegate to the supervisor carried on the hub. The supervisor is built
# lazily (it needs the connected account client as its transport), so a hub with
# no userbot reports the loop surface as unavailable rather than crashing.

def _supervisor(hub):
    if not getattr(hub.config, "loop_enabled", False):
        return None
    if getattr(hub, "userbot", None) is None:
        return None
    return hub.loop_supervisor()


_UNAVAILABLE = "Agent Loop unavailable: userbot (account) is not connected."
_DISABLED = "Agent Loop is disabled in config. Set loop.enabled: true to enable it."


def _loop_unavailable_reason(hub) -> str:
    if not getattr(hub.config, "loop_enabled", False):
        return _DISABLED
    if getattr(hub, "userbot", None) is None:
        return _UNAVAILABLE
    return _UNAVAILABLE


async def _delegate(hub, method, *args) -> str:
    """Run one supervisor method that returns a LoopResult, surfacing .message."""
    sup = _supervisor(hub)
    if sup is None:
        return _loop_unavailable_reason(hub)
    res = await getattr(sup, method)(*args)
    return res.message


async def cmd_loop(hub, project_name: str, task: str, required_permissions=None) -> str:
    sup = _supervisor(hub)
    if sup is None:
        return _loop_unavailable_reason(hub)
    clean_task, scopes = permission_mod.extract_task_requirements(task, required_permissions)
    task_id, est = sup.estimate(project_name, clean_task, required_permissions=scopes)
    if task_id is None:
        return est
    row = db.get_project(hub.db_path, project_name)
    gate = ("LIVE — will pause at PR creation for your /approve."
            if row and row["status"] == "live"
            else "dev — runs straight through.")
    lines = [
        f"Loop estimated for '{project_name}' ({gate})",
        est.summary(),
    ]
    if scopes:
        lines.append(f"Required permissions: {', '.join(scopes)}")
    lines.extend([
        f"Task id: {task_id}",
        f"Start it with: /loop_confirm {task_id}   (or /loop_stop {task_id} to cancel)",
    ])
    return "\n".join(lines)


async def cmd_loop_confirm(hub, task_id: str) -> str:
    return await _delegate(hub, "confirm_and_run", task_id)


async def cmd_loop_status(hub, task_id: str = None) -> str:
    runs = db.list_loop_runs(hub.db_path, active_only=(task_id is None))
    if task_id:
        run = db.get_loop_run(hub.db_path, task_id)
        if not run:
            return f"No such loop: {task_id}"
        runs = [run]
    if not runs:
        return "No active loops."
    lines = ["Agent Loops"]
    for r in runs:
        pct = int((r["used_tokens"] / r["est_tokens"] * 100)) if r["est_tokens"] else 0
        lines.append(
            f"• {r['task_id']} [{r['status']}] {r['project']} ({r['project_status']})"
            f"\n  steps={r['steps_done']} tokens={r['used_tokens']:,}/{r['est_tokens']:,} (~{pct}% of est)"
            + (f"\n  PR: {r['pr_url']}" if r.get("pr_url") else "")
        )
    return "\n".join(lines)


async def cmd_loop_approve(hub, task_id: str) -> str:
    return await _delegate(hub, "approve_pr", task_id)


async def cmd_loop_reject(hub, task_id: str, reason: str = "") -> str:
    return await _delegate(hub, "reject_pr", task_id, reason)


async def cmd_loop_input(hub, task_id: str, text: str) -> str:
    return await _delegate(hub, "provide_input", task_id, text)


async def cmd_loop_stop(hub, task_id: str) -> str:
    return await _delegate(hub, "stop", task_id)
