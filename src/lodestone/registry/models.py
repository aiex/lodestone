"""SQLite schema for the Lodestone registry.

agents / projects / permissions are rebuilt from config on every sync
(config.yaml is the source of truth). logs is append-only runtime history,
preserved across syncs — and the table the Phase 3 dashboard reads from.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT,
    host          TEXT,
    telegram_peer TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    name     TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    PRIMARY KEY (name, agent_id),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS permissions (
    agent_id TEXT NOT NULL,
    scope    TEXT NOT NULL,
    PRIMARY KEY (agent_id, scope),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

CREATE TABLE IF NOT EXISTS logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    agent_id TEXT,
    kind     TEXT NOT NULL,
    detail   TEXT
);
"""
