"""
events.py — EventLedger: the single place all events are written.

Every event goes to:
  1. SQLite events table (queryable, indexed)
  2. .agentos/runs/{run_id}/events.jsonl (append-only audit trail)
  3. SSE broadcast (real-time dashboard updates)

No event is ever deleted or modified. This is the system's source of truth.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ulid

from .database import get_db
from .sse import sse_manager

logger = logging.getLogger(__name__)

# Resolved at runtime from config
_RUNS_DIR: Path | None = None


def set_runs_dir(path: Path) -> None:
    global _RUNS_DIR
    _RUNS_DIR = path


def get_runs_dir() -> Path:
    if _RUNS_DIR is None:
        raise RuntimeError("Runs directory not configured. Call set_runs_dir() first.")
    return _RUNS_DIR


# Fields that must NEVER appear in event payloads
_BLOCKED_PAYLOAD_KEYS = frozenset({
    "password", "secret", "token", "api_key", "credential",
    "access_key", "private_key", "auth_token", "bearer",
})


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively remove any keys that look like credentials.
    This is a safety net — adapters should never produce these in the first place.
    """
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if any(blocked in key.lower() for blocked in _BLOCKED_PAYLOAD_KEYS):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_payload(value)
        else:
            sanitized[key] = value
    return sanitized


class EventLedger:
    """
    Writes events to SQLite + JSONL + SSE.
    Instantiated once and injected into routers via FastAPI dependency.
    """

    async def emit(
        self,
        *,
        source: str,
        type: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
        mission_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """
        Emit an event. Returns the generated event ID.

        Args:
            source:     agent_id, 'gateway', or 'user'
            type:       event type string e.g. 'mission.created'
            payload:    arbitrary dict — will be sanitized before storage
            severity:   'info' | 'warning' | 'error' | 'critical'
            mission_id: optional — links event to a mission
            task_id:    optional — links event to a task
            run_id:     optional — links event to a run
        """
        event_id = str(ulid.new())
        timestamp = datetime.now(UTC).isoformat()
        safe_payload = _sanitize_payload(payload) if payload else None
        payload_json = json.dumps(safe_payload) if safe_payload is not None else None

        event_dict: dict[str, Any] = {
            "id": event_id,
            "timestamp": timestamp,
            "source": source,
            "type": type,
            "payload": safe_payload,
            "severity": severity,
            "mission_id": mission_id,
            "task_id": task_id,
            "run_id": run_id,
        }

        # 1. Write to SQLite
        try:
            async with get_db() as db:
                await db.execute(
                    """
                    INSERT INTO events
                      (id, timestamp, source, type, payload, severity, mission_id, task_id, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, timestamp, source, type,
                        payload_json, severity,
                        mission_id, task_id, run_id,
                    ),
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to write event %s to SQLite", event_id)

        # 2. Write to JSONL (if run_id known)
        if run_id:
            try:
                self._append_jsonl(run_id, event_dict)
            except Exception:
                logger.exception("Failed to write event %s to JSONL", event_id)

        # 3. Broadcast to SSE subscribers
        try:
            await sse_manager.broadcast(event_dict)
        except Exception:
            logger.exception("Failed to broadcast event %s via SSE", event_id)

        logger.debug("Event emitted: %s [%s]", type, event_id)
        return event_id

    def _append_jsonl(self, run_id: str, event: dict[str, Any]) -> None:
        run_dir = get_runs_dir() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = run_dir / "events.jsonl"
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    async def get_events(
        self,
        *,
        mission_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        before: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Query events from SQLite with optional filters.
        Returns (events, total_count).
        """
        conditions: list[str] = []
        params: list[Any] = []

        if mission_id:
            conditions.append("mission_id = ?")
            params.append(mission_id)
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if agent_id:
            conditions.append("source = ?")
            params.append(agent_id)
        if event_type:
            conditions.append("type = ?")
            params.append(event_type)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        if before:
            conditions.append("timestamp < ?")
            params.append(before)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with get_db() as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM events {where}", tuple(params)
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"""
                SELECT id, timestamp, source, type, payload, severity,
                       mission_id, task_id, run_id
                FROM events {where}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                tuple(params) + (limit,),
            ) as cursor:
                rows = await cursor.fetchall()

        events = []
        for row in rows:
            event = dict(row)
            if event.get("payload"):
                try:
                    event["payload"] = json.loads(event["payload"])
                except json.JSONDecodeError:
                    event["payload"] = None
            events.append(event)

        return events, total


# Module-level singleton — injected into routers via FastAPI Depends
event_ledger = EventLedger()
