"""SQLite schema for the Lodestone registry.

agents / projects / permissions are rebuilt from config on every sync
(config.yaml is the source of truth). logs and ai_usage are append-only runtime
history, preserved across syncs — the tables the Phase 3 dashboard reads from.
loop_runs / loop_events back the Phase 4 Agent Loop and are likewise preserved.

The schema is stable and extended only additively (new tables / columns), so the
dashboard's charts — which are just views over these tables — keep working as we
add to it. Every statement uses IF NOT EXISTS, so re-running it on an existing
database is a safe no-op migration.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT,
    host          TEXT,
    telegram_peer TEXT
);

-- status is 'dev' or 'live'. Live projects make the Agent Loop halt at PR
-- creation for human approval before deploy; dev projects run straight through.
CREATE TABLE IF NOT EXISTS projects (
    name     TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'dev',
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

-- One row per LLM call the AI brain makes. agent_id is the agent a call was
-- about when known (else NULL = brain-level). cost_usd is computed at write
-- time from the model's pricing so the dashboard never needs a price table.
CREATE TABLE IF NOT EXISTS ai_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    agent_id          TEXT,
    model             TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0
);

-- One row per Agent Loop run. The supervisor (hub/loop.py) owns this: it tracks
-- whether work continues, the dev/live gate, and the live budget. status moves
-- through running -> (awaiting_pr_approval | awaiting_input) -> done|stopped|error.
-- task_id is a stable client-side id (not the autoincrement) so commands and
-- gate approvals can reference a run idempotently.
CREATE TABLE IF NOT EXISTS loop_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL UNIQUE,
    agent_id    TEXT,
    project     TEXT,
    project_status TEXT,
    task        TEXT,
    status      TEXT NOT NULL DEFAULT 'estimated',
    est_tokens  INTEGER NOT NULL DEFAULT 0,
    used_tokens INTEGER NOT NULL DEFAULT 0,
    steps_done  INTEGER NOT NULL DEFAULT 0,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    pr_url      TEXT,
    created_ts  TEXT NOT NULL,
    updated_ts  TEXT NOT NULL
);

-- Append-only audit trail of every checkpoint within a loop run. Powers a future
-- dashboard panel and lets a run be reconstructed. seq mirrors the agent's
-- monotonic checkpoint counter (gaps => a lost message).
CREATE TABLE IF NOT EXISTS loop_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    seq     INTEGER,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    detail  TEXT,
    tokens  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_logs_ts        ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_ai_usage_ts    ON ai_usage(ts);
CREATE INDEX IF NOT EXISTS idx_loop_events_tid ON loop_events(task_id);
"""
