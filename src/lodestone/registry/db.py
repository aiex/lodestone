import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import re

from .models import SCHEMA
from ..config import normalize_project


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _validate_unique_projects(config) -> None:
    seen = {}
    for agent in config.agents:
        agent_id = agent.get("id")
        for proj in agent.get("projects", []) or []:
            name, _status = normalize_project(proj)
            if not name:
                raise ValueError(f"Agent {agent_id} has a project with no name.")
            owner = seen.get(name)
            if owner and owner != agent_id:
                raise ValueError(
                    f"Project '{name}' is assigned to multiple agents: {owner}, {agent_id}"
                )
            seen[name] = agent_id


def sync_from_config(db_path: str, config) -> None:
    """Rebuild agents/projects/permissions from config. logs are preserved."""
    _validate_unique_projects(config)
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    cur = conn.cursor()
    cur.execute("DELETE FROM permissions;")
    cur.execute("DELETE FROM projects;")
    cur.execute("DELETE FROM agents;")
    for a in config.agents:
        cur.execute(
            "INSERT INTO agents (id, name, type, host, telegram_peer) VALUES (?,?,?,?,?)",
            (a.get("id"), a.get("name"), a.get("type"), a.get("host"), a.get("telegram_peer")),
        )
        for proj in a.get("projects", []) or []:
            name, status = normalize_project(proj)
            cur.execute(
                "INSERT OR IGNORE INTO projects (name, agent_id, status) VALUES (?,?,?)",
                (name, a.get("id"), status),
            )
        for scope in a.get("permissions", []) or []:
            cur.execute(
                "INSERT OR IGNORE INTO permissions (agent_id, scope) VALUES (?,?)",
                (a.get("id"), scope),
            )
    conn.commit()
    conn.close()


def list_agents(db_path: str) -> list:
    conn = _connect(db_path)
    agents = []
    for r in conn.execute("SELECT * FROM agents ORDER BY id").fetchall():
        projects = [p["name"] for p in conn.execute(
            "SELECT name FROM projects WHERE agent_id=? ORDER BY name", (r["id"],)).fetchall()]
        perms = [p["scope"] for p in conn.execute(
            "SELECT scope FROM permissions WHERE agent_id=? ORDER BY scope", (r["id"],)).fetchall()]
        agents.append({**dict(r), "projects": projects, "permissions": perms})
    conn.close()
    return agents


def get_agent(db_path: str, agent_id: str):
    conn = _connect(db_path)
    r = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not r:
        conn.close()
        return None
    projects = [p["name"] for p in conn.execute(
        "SELECT name FROM projects WHERE agent_id=? ORDER BY name", (agent_id,)).fetchall()]
    perms = [p["scope"] for p in conn.execute(
        "SELECT scope FROM permissions WHERE agent_id=? ORDER BY scope", (agent_id,)).fetchall()]
    recent = [dict(x) for x in conn.execute(
        "SELECT ts, kind, detail FROM logs WHERE agent_id=? ORDER BY id DESC LIMIT 5",
        (agent_id,)).fetchall()]
    conn.close()
    return {**dict(r), "projects": projects, "permissions": perms, "recent": recent}


def list_projects(db_path: str) -> list:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT p.name AS name, p.agent_id AS agent_id, p.status AS status, "
        "a.name AS agent_name "
        "FROM projects p JOIN agents a ON a.id = p.agent_id ORDER BY p.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_project(db_path: str, project_name: str):
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT p.name AS name, p.agent_id AS agent_id, p.status AS status, "
        "a.name AS agent_name "
        "FROM projects p JOIN agents a ON a.id = p.agent_id WHERE p.name=?",
        (project_name,),
    ).fetchall()
    conn.close()
    if len(rows) > 1:
        owners = ", ".join(f"{r['agent_name']} [{r['agent_id']}]" for r in rows)
        raise ValueError(f"Project '{project_name}' is assigned to multiple agents: {owners}")
    row = rows[0] if rows else None
    return dict(row) if row else None


def log_event(db_path: str, agent_id, kind: str, detail: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO logs (ts, agent_id, kind, detail) VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), agent_id, kind, detail),
    )
    conn.commit()
    conn.close()


def log_usage(db_path: str, agent_id, model, prompt_tokens: int,
              completion_tokens: int, total_tokens: int, cost_usd: float) -> None:
    """Record one LLM call's token usage and cost. Append-only."""
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO ai_usage "
        "(ts, agent_id, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES (?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), agent_id, model,
         int(prompt_tokens), int(completion_tokens), int(total_tokens), float(cost_usd)),
    )
    conn.commit()
    conn.close()


# --- Dashboard read views (Phase 3) ---------------------------------------
# All of these are read-only aggregations over the stable schema above. Adding
# a new chart means adding a new view here, never changing the tables.

def recent_logs(db_path: str, limit: int = 50) -> list:
    """Most recent activity across the whole fleet, newest first."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT l.ts AS ts, l.agent_id AS agent_id, a.name AS agent_name, "
        "       l.kind AS kind, l.detail AS detail "
        "FROM logs l LEFT JOIN agents a ON a.id = l.agent_id "
        "ORDER BY l.id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_logs_by_kind(db_path: str, kind_prefix: str, limit: int = 50) -> list:
    """Recent logs filtered by a kind prefix, newest first."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT l.ts AS ts, l.agent_id AS agent_id, a.name AS agent_name, "
        "       l.kind AS kind, l.detail AS detail "
        "FROM logs l LEFT JOIN agents a ON a.id = l.agent_id "
        "WHERE l.kind LIKE ? ORDER BY l.id DESC LIMIT ?",
        (f"{kind_prefix}%", int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def activity_by_kind(db_path: str) -> list:
    """Count of log events grouped by kind (dispatch / reply / error / …)."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT kind, COUNT(*) AS count FROM logs GROUP BY kind ORDER BY count DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def activity_by_kind_prefix(db_path: str, kind_prefix: str) -> list:
    """Count log events grouped by kind for one prefix family."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT kind, COUNT(*) AS count FROM logs WHERE kind LIKE ? "
        "GROUP BY kind ORDER BY count DESC, kind ASC",
        (f"{kind_prefix}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def activity_by_agent(db_path: str, limit: int = 10) -> list:
    """Most active agents by event count, for fleet-level charts."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT COALESCE(a.name, l.agent_id, '(system)') AS agent_name, "
        "       l.agent_id AS agent_id, COUNT(*) AS count "
        "FROM logs l LEFT JOIN agents a ON a.id = l.agent_id "
        "GROUP BY l.agent_id, agent_name ORDER BY count DESC, agent_name ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def activity_by_agent_prefix(db_path: str, kind_prefix: str, limit: int = 10) -> list:
    """Most active agents by event count inside one kind family."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT COALESCE(a.name, l.agent_id, '(system)') AS agent_name, "
        "       l.agent_id AS agent_id, COUNT(*) AS count "
        "FROM logs l LEFT JOIN agents a ON a.id = l.agent_id "
        "WHERE l.kind LIKE ? "
        "GROUP BY l.agent_id, agent_name ORDER BY count DESC, agent_name ASC LIMIT ?",
        (f"{kind_prefix}%", int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def activity_daily(db_path: str, days: int = 30) -> list:
    """Per-day event counts, split into dispatches vs everything else.

    A chart-ready time series; days with no activity are simply absent (the
    front-end fills gaps), keeping the query a plain GROUP BY.
    """
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT substr(ts,1,10) AS day, "
        "       SUM(CASE WHEN kind='dispatch' THEN 1 ELSE 0 END) AS dispatches, "
        "       SUM(CASE WHEN kind!='dispatch' THEN 1 ELSE 0 END) AS other, "
        "       COUNT(*) AS total "
        "FROM logs GROUP BY day ORDER BY day DESC LIMIT ?",
        (int(days),),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def activity_daily_prefix(db_path: str, kind_prefix: str, days: int = 30) -> list:
    """Per-day event counts for one log family."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT substr(ts,1,10) AS day, COUNT(*) AS total "
        "FROM logs WHERE kind LIKE ? GROUP BY day ORDER BY day DESC LIMIT ?",
        (f"{kind_prefix}%", int(days)),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


_PROJECT_TAG_RE = re.compile(r"\[project:([^\]]+)\]")


def activity_by_project_prefix(db_path: str, kind_prefix: str, limit: int = 10) -> list:
    """Group one log family by project tag embedded in detail."""
    rows = recent_logs_by_kind(db_path, kind_prefix, limit=5000)
    counts = {}
    for row in rows:
        match = _PROJECT_TAG_RE.search(row.get("detail") or "")
        if not match:
            continue
        project = match.group(1).strip()
        counts[project] = counts.get(project, 0) + 1
    items = [{"project": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda r: (-r["count"], r["project"]))
    return items[: int(limit)]


def usage_totals(db_path: str) -> dict:
    """Lifetime token + cost + call totals for the AI brain."""
    conn = _connect(db_path)
    r = conn.execute(
        "SELECT COUNT(*) AS calls, "
        "       COALESCE(SUM(prompt_tokens),0)     AS prompt_tokens, "
        "       COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
        "       COALESCE(SUM(total_tokens),0)      AS total_tokens, "
        "       COALESCE(SUM(cost_usd),0)          AS cost_usd "
        "FROM ai_usage"
    ).fetchone()
    conn.close()
    return dict(r)


def usage_by_model(db_path: str) -> list:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT COALESCE(model,'(unknown)') AS model, COUNT(*) AS calls, "
        "       COALESCE(SUM(total_tokens),0) AS total_tokens, "
        "       COALESCE(SUM(cost_usd),0)     AS cost_usd "
        "FROM ai_usage GROUP BY model ORDER BY cost_usd DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def usage_by_agent(db_path: str) -> list:
    """Aggregate LLM usage by agent when agent_id is known."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT COALESCE(a.name, u.agent_id, '(brain)') AS agent_name, "
        "       u.agent_id AS agent_id, COUNT(*) AS calls, "
        "       COALESCE(SUM(u.total_tokens),0) AS total_tokens, "
        "       COALESCE(SUM(u.cost_usd),0)     AS cost_usd "
        "FROM ai_usage u LEFT JOIN agents a ON a.id = u.agent_id "
        "GROUP BY u.agent_id, agent_name ORDER BY cost_usd DESC, total_tokens DESC, agent_name ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def usage_daily(db_path: str, days: int = 30) -> list:
    """Per-day token and cost series for the cost chart."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT substr(ts,1,10) AS day, COUNT(*) AS calls, "
        "       COALESCE(SUM(total_tokens),0) AS total_tokens, "
        "       COALESCE(SUM(cost_usd),0)     AS cost_usd "
        "FROM ai_usage GROUP BY day ORDER BY day DESC LIMIT ?",
        (int(days),),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def recent_usage(db_path: str, limit: int = 20) -> list:
    """Latest LLM calls with model, token, and cost detail."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT u.ts AS ts, u.agent_id AS agent_id, "
        "       COALESCE(a.name, u.agent_id, '(brain)') AS agent_name, "
        "       COALESCE(u.model, '(unknown)') AS model, "
        "       u.prompt_tokens AS prompt_tokens, "
        "       u.completion_tokens AS completion_tokens, "
        "       u.total_tokens AS total_tokens, "
        "       u.cost_usd AS cost_usd "
        "FROM ai_usage u LEFT JOIN agents a ON a.id = u.agent_id "
        "ORDER BY u.id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Agent Loop (Phase 4) --------------------------------------------------

# Loop run lifecycle. Active states are the non-terminal ones a /loop_status
# without a task id should surface.
RUN_ACTIVE = ("estimated", "running", "awaiting_pr_approval", "awaiting_input")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_loop_run(db_path: str, task_id: str, agent_id, project, project_status,
                    task: str, est_tokens: int, status: str = "estimated") -> None:
    """Persist a new loop run (before work starts, at estimate time)."""
    conn = _connect(db_path)
    now = _now()
    conn.execute(
        "INSERT INTO loop_runs "
        "(task_id, agent_id, project, project_status, task, status, est_tokens, "
        " used_tokens, steps_done, last_seq, created_ts, updated_ts) "
        "VALUES (?,?,?,?,?,?,?,0,0,0,?,?)",
        (task_id, agent_id, project, project_status, task, status,
         int(est_tokens), now, now),
    )
    conn.commit()
    conn.close()


def get_loop_run(db_path: str, task_id: str):
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM loop_runs WHERE task_id=?", (task_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_loop_runs(db_path: str, active_only: bool = False, limit: int = 50) -> list:
    conn = _connect(db_path)
    sql = "SELECT * FROM loop_runs"
    if active_only:
        placeholders = ",".join("?" * len(RUN_ACTIVE))
        sql += f" WHERE status IN ({placeholders})"
    sql += " ORDER BY id DESC LIMIT ?"
    params = (*RUN_ACTIVE, int(limit)) if active_only else (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_loop_run(db_path: str, task_id: str, **fields) -> None:
    """Patch mutable columns on a loop run. updated_ts is always bumped."""
    allowed = {"status", "est_tokens", "used_tokens", "steps_done",
               "last_seq", "pr_url"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    conn = _connect(db_path)
    conn.execute(
        f"UPDATE loop_runs SET {cols}, updated_ts=? WHERE task_id=?",
        (*sets.values(), _now(), task_id),
    )
    conn.commit()
    conn.close()


def log_loop_event(db_path: str, task_id: str, seq, kind: str, detail: str,
                   tokens: int = 0) -> None:
    """Append one checkpoint to a loop's audit trail."""
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO loop_events (task_id, seq, ts, kind, detail, tokens) "
        "VALUES (?,?,?,?,?,?)",
        (task_id, seq, _now(), kind, (detail or "")[:1000], int(tokens or 0)),
    )
    conn.commit()
    conn.close()


def avg_tokens_per_call(db_path: str, default: int = 1500) -> int:
    """Rolling mean total_tokens across recorded LLM calls.

    Feeds the loop's pre-flight estimate. Falls back to a default when there is
    no history yet (a fresh install), so estimation never divides by zero.
    """
    conn = _connect(db_path)
    r = conn.execute(
        "SELECT AVG(total_tokens) AS avg, COUNT(*) AS n FROM ai_usage "
        "WHERE total_tokens > 0"
    ).fetchone()
    conn.close()
    if not r or not r["n"]:
        return int(default)
    return int(r["avg"] or default)


def loop_events(db_path: str, task_id: str, limit: int = 100) -> list:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT seq, ts, kind, detail, tokens FROM loop_events "
        "WHERE task_id=? ORDER BY id ASC LIMIT ?",
        (task_id, int(limit)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
