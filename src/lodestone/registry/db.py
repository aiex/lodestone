import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import SCHEMA


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


def sync_from_config(db_path: str, config) -> None:
    """Rebuild agents/projects/permissions from config. logs are preserved."""
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
            cur.execute(
                "INSERT OR IGNORE INTO projects (name, agent_id) VALUES (?,?)",
                (proj, a.get("id")),
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
        "SELECT p.name AS name, p.agent_id AS agent_id, a.name AS agent_name "
        "FROM projects p JOIN agents a ON a.id = p.agent_id ORDER BY p.name"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_event(db_path: str, agent_id, kind: str, detail: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO logs (ts, agent_id, kind, detail) VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), agent_id, kind, detail),
    )
    conn.commit()
    conn.close()
