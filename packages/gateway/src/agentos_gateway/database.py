"""
database.py — aiosqlite setup, schema initialization, and query helpers.

All writes go through the gateway (single writer).
The CLI reads via HTTP only — never touches SQLite directly.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# Resolved at runtime from config
_DB_PATH: Path | None = None


def set_db_path(path: Path) -> None:
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    if _DB_PATH is None:
        raise RuntimeError("Database path not configured. Call set_db_path() first.")
    return _DB_PATH


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that yields a connected, row-factory-enabled DB connection."""
    db = await aiosqlite.connect(get_db_path())
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA synchronous=NORMAL")
        yield db
    finally:
        await db.close()


SCHEMA_SQL = """
-- ============================================================
-- AGENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
  id                TEXT PRIMARY KEY,
  display_name      TEXT NOT NULL,
  adapter           TEXT NOT NULL,
  command           TEXT NOT NULL,
  role              TEXT NOT NULL,
  capabilities      TEXT NOT NULL DEFAULT '[]',
  workspace_strategy TEXT NOT NULL DEFAULT 'git_worktree',
  fallback_agent_id TEXT REFERENCES agents(id),
  status            TEXT NOT NULL DEFAULT 'idle',
  last_health_check TEXT,
  created_at        TEXT NOT NULL
);

-- ============================================================
-- MISSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS missions (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  objective    TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'planning',
  risk_level   TEXT,
  created_at   TEXT NOT NULL,
  completed_at TEXT
);

-- ============================================================
-- TASKS
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
  id                TEXT PRIMARY KEY,
  mission_id        TEXT NOT NULL REFERENCES missions(id),
  title             TEXT NOT NULL,
  assigned_agent_id TEXT REFERENCES agents(id),
  depends_on        TEXT NOT NULL DEFAULT '[]',
  status            TEXT NOT NULL DEFAULT 'pending',
  branch            TEXT,
  worktree_path     TEXT,
  files_owned       TEXT NOT NULL DEFAULT '[]',
  created_at        TEXT NOT NULL,
  completed_at      TEXT
);

-- ============================================================
-- AGENT RUNS
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_runs (
  id                TEXT PRIMARY KEY,
  task_id           TEXT NOT NULL REFERENCES tasks(id),
  agent_id          TEXT NOT NULL REFERENCES agents(id),
  started_at        TEXT NOT NULL,
  ended_at          TEXT,
  status            TEXT NOT NULL DEFAULT 'running',
  workspace_state   TEXT,
  last_commit_sha   TEXT,
  confidence_score  REAL,
  result_summary    TEXT,
  took_over_from    TEXT
);

-- ============================================================
-- AGENT MESSAGES (session history — enables handoff memory)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_messages (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES agent_runs(id),
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  task_id     TEXT NOT NULL REFERENCES tasks(id),
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  token_count INTEGER,
  created_at  TEXT NOT NULL
);

-- ============================================================
-- CONTEXT PACKS (snapshot of what was fed to each agent)
-- ============================================================
CREATE TABLE IF NOT EXISTS context_packs (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL REFERENCES tasks(id),
  agent_id     TEXT NOT NULL REFERENCES agents(id),
  run_id       TEXT NOT NULL REFERENCES agent_runs(id),
  documents    TEXT NOT NULL DEFAULT '[]',
  constraints  TEXT NOT NULL DEFAULT '[]',
  token_budget INTEGER NOT NULL DEFAULT 8000,
  tokens_used  INTEGER NOT NULL DEFAULT 0,
  content      TEXT,
  generated_at TEXT NOT NULL
);

-- ============================================================
-- ACTION REQUESTS (approval queue)
-- ============================================================
CREATE TABLE IF NOT EXISTS action_requests (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES agent_runs(id),
  action_type     TEXT NOT NULL,
  command_or_tool TEXT,
  risk_score      INTEGER NOT NULL,
  risk_level      TEXT NOT NULL,
  explanation     TEXT,
  evidence        TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  created_at      TEXT NOT NULL
);

-- ============================================================
-- APPROVALS
-- ============================================================
CREATE TABLE IF NOT EXISTS approvals (
  id                TEXT PRIMARY KEY,
  action_request_id TEXT NOT NULL REFERENCES action_requests(id),
  decision          TEXT NOT NULL,
  decided_by        TEXT NOT NULL DEFAULT 'user',
  decided_at        TEXT NOT NULL,
  scope             TEXT NOT NULL DEFAULT 'once',
  note              TEXT
);

-- ============================================================
-- CREDENTIAL EVENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS credential_events (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL REFERENCES agents(id),
  event_type  TEXT NOT NULL,
  run_id      TEXT REFERENCES agent_runs(id),
  mission_id  TEXT REFERENCES missions(id),
  details     TEXT,
  resolved_at TEXT,
  created_at  TEXT NOT NULL
);

-- ============================================================
-- EVENTS (universal append-only ledger)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
  id         TEXT PRIMARY KEY,
  timestamp  TEXT NOT NULL,
  source     TEXT NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT,
  severity   TEXT NOT NULL DEFAULT 'info',
  mission_id TEXT,
  task_id    TEXT,
  run_id     TEXT
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_events_mission     ON events(mission_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_run         ON events(run_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type        ON events(type, timestamp);
CREATE INDEX IF NOT EXISTS idx_action_req_status  ON action_requests(status);
CREATE INDEX IF NOT EXISTS idx_tasks_mission      ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_messages_run       ON agent_messages(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cred_events_agent  ON credential_events(agent_id, created_at);
"""


async def init_db(db_path: Path) -> None:
    """Create the database and initialize the schema. Idempotent — safe to call on every startup."""
    set_db_path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with get_db() as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()
        # Migration guard: existing DBs created before the `content` column was
        # added need it backfilled in place. `CREATE TABLE IF NOT EXISTS` above
        # is a no-op for a table that already exists, so this can't be folded
        # into SCHEMA_SQL.
        try:
            await db.execute("ALTER TABLE context_packs ADD COLUMN content TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        logger.info("Database initialized at %s", db_path)


# ============================================================
# QUERY HELPERS
# ============================================================

def row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert an aiosqlite Row to a plain dict."""
    return dict(row)


def parse_json_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Parse JSON string fields in a record dict into Python objects."""
    for field in fields:
        if field in record and isinstance(record[field], str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                record[field] = json.loads(record[field])
    return record


async def fetch_one(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    async with db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
        return row_to_dict(row) if row else None


async def fetch_all(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    async with db.execute(sql, params) as cursor:
        rows = await cursor.fetchall()
        return [row_to_dict(r) for r in rows]


async def execute(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> None:
    await db.execute(sql, params)
    await db.commit()
