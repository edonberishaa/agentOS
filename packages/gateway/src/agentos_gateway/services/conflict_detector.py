"""
services/conflict_detector.py — ConflictDetector: file-level, string-matching
conflict detection between agents working in parallel.

Deliberately shallow by design (per Day 5 scope): every check here is a glob
or substring match, never an AST parse or semantic diff. The goal is to catch
the cheap, obvious collisions — two tasks claiming the same file, two agents
publishing different contracts for the same endpoint, two migrations stamped
with the same timestamp — not to understand code.

`check_api_contracts` assumes each agent publishes its contract as
`.agentos/context/api-contracts/{agent_id}.md` — nothing in the codebase
writes these files yet (that's context-vault/document-ranking territory,
still out of scope), so this check is forward-looking scaffolding: it works
correctly against whatever's already in that directory, however it got
there.

`record_conflict`/`resolve_conflict` go through `event_ledger.emit()` like
everything else — a conflict's identity *is* its `conflict.detected` event's
id, there's no separate conflicts table. `routers/inbox.py` relies on that:
it excludes any `conflict.detected` event whose id shows up as the
`conflict_id` in some later `conflict.resolved` event's payload.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..database import fetch_all, get_db
from ..events import event_ledger
from ..models import InboxConflict
from .task_service import task_service

_HTTP_METHOD_PATH_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)", re.IGNORECASE)

_MIGRATION_GLOBS = ("db/migrations/*.sql", "migrations/*.py")

_TIMESTAMP_PREFIX_LEN = 14


def _resolve_agentos_dir() -> Path:
    """Walk up from CWD until a `.agentos/` dir is found, mirroring main.py's
    resolve_agentos_dir(). Duplicated locally (see approval_engine.py for why —
    importing from main.py would create a circular import)."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".agentos"
        if candidate.is_dir():
            return candidate
    return cwd / ".agentos"


class ConflictDetector:
    """File-overlap, API-contract, and migration conflict checks."""

    async def check_file_overlap(self, task_id: str) -> list[str]:
        """Files this task owns that an OTHER active task in the same mission also owns."""
        task = await task_service.get(task_id)
        if task is None:
            return []

        async with get_db() as db:
            others = await fetch_all(
                db,
                """
                SELECT files_owned FROM tasks
                WHERE mission_id = ? AND id != ? AND status IN ('running', 'paused')
                """,
                (task.mission_id, task_id),
            )

        own_files = set(task.files_owned)
        if not own_files:
            return []

        overlapping: set[str] = set()
        for row in others:
            other_files = self._parse_files_owned(row["files_owned"])
            overlapping.update(own_files & other_files)

        return sorted(overlapping)

    async def check_api_contracts(self, task_id: str, agent_id: str) -> dict[str, Any] | None:
        """Conflicting endpoint definitions for the same path across api-contracts/*.md."""
        task = await task_service.get(task_id)
        if task is None:
            return None

        contracts_dir = _resolve_agentos_dir() / "context" / "api-contracts"
        if not contracts_dir.is_dir():
            return None

        declarations: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for md_file in sorted(contracts_dir.glob("*.md")):
            file_agent_id = md_file.stem
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                match = _HTTP_METHOD_PATH_RE.search(line)
                if match:
                    method = match.group(1).upper()
                    path = match.group(2).rstrip(".,;:")
                    declarations.setdefault((method, path), []).append(
                        (file_agent_id, line.strip())
                    )

        for (method, path), entries in declarations.items():
            distinct_agents = {a for a, _ in entries}
            distinct_lines = {ln for _, ln in entries}
            if len(distinct_agents) < 2 or len(distinct_lines) < 2:
                continue  # same agent, or every agent wrote the identical line
            if agent_id not in distinct_agents:
                continue  # only surface conflicts involving the agent we were asked about

            return {
                "type": "api_contract",
                "method": method,
                "path": path,
                "agents_involved": sorted(distinct_agents),
                "files_affected": [f"{a}.md" for a in sorted(distinct_agents)],
            }

        return None

    async def check_migrations(self, task_id: str) -> dict[str, Any] | None:
        """Two active worktrees in this mission with migration files sharing a timestamp prefix."""
        task = await task_service.get(task_id)
        if task is None:
            return None

        async with get_db() as db:
            active = await fetch_all(
                db,
                """
                SELECT id, assigned_agent_id, worktree_path FROM tasks
                WHERE mission_id = ? AND worktree_path IS NOT NULL
                  AND status IN ('running', 'paused')
                """,
                (task.mission_id,),
            )

        by_prefix: dict[str, list[tuple[str, str | None, str]]] = {}
        for row in active:
            worktree = Path(str(row["worktree_path"]))
            if not worktree.exists():
                continue
            for pattern in _MIGRATION_GLOBS:
                for migration_file in worktree.glob(pattern):
                    prefix = migration_file.name[:_TIMESTAMP_PREFIX_LEN]
                    by_prefix.setdefault(prefix, []).append(
                        (str(row["id"]), row["assigned_agent_id"], str(migration_file))
                    )

        for prefix, entries in by_prefix.items():
            distinct_tasks = {t for t, _, _ in entries}
            if len(distinct_tasks) < 2:
                continue
            return {
                "type": "migration",
                "timestamp_prefix": prefix,
                "agents_involved": sorted({a for _, a, _ in entries if a}),
                "files_affected": [f for _, _, f in entries],
            }

        return None

    async def list_active(self, limit: int = 50) -> list[InboxConflict]:
        """Detected conflicts that haven't been resolved yet, for the inbox."""
        async with get_db() as db:
            detected_rows = await fetch_all(
                db,
                "SELECT * FROM events WHERE type = 'conflict.detected' "
                "ORDER BY timestamp DESC LIMIT 200",
            )
            resolved_rows = await fetch_all(
                db, "SELECT payload FROM events WHERE type = 'conflict.resolved'"
            )

        resolved_conflict_ids: set[str] = set()
        for row in resolved_rows:
            if not row["payload"]:
                continue
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            conflict_id = payload.get("conflict_id")
            if conflict_id:
                resolved_conflict_ids.add(conflict_id)

        conflicts: list[InboxConflict] = []
        for row in detected_rows:
            if row["id"] in resolved_conflict_ids:
                continue
            payload = json.loads(row["payload"]) if row["payload"] else {}
            conflicts.append(
                InboxConflict(
                    id=row["id"],
                    type=payload.get("conflict_type", "file_overlap"),
                    agents_involved=payload.get("agents_involved", []),
                    files_affected=payload.get("files_affected", []),
                    created_at=row["timestamp"],
                )
            )
            if len(conflicts) >= limit:
                break

        return conflicts

    async def record_conflict(
        self,
        conflict_type: str,
        agents_involved: list[str],
        files_affected: list[str],
        task_id: str | None,
        mission_id: str | None,
    ) -> str:
        """Emit conflict.detected. The returned event id IS the conflict_id."""
        return await event_ledger.emit(
            source="gateway",
            type="conflict.detected",
            payload={
                "conflict_type": conflict_type,
                "agents_involved": agents_involved,
                "files_affected": files_affected,
            },
            severity="warning",
            mission_id=mission_id,
            task_id=task_id,
        )

    async def resolve_conflict(self, conflict_id: str, resolution: str = "resolved") -> str:
        return await event_ledger.emit(
            source="gateway",
            type="conflict.resolved",
            payload={"conflict_id": conflict_id, "resolution": resolution},
        )

    def _parse_files_owned(self, raw: Any) -> set[str]:
        if isinstance(raw, list):
            return set(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return set()
            return set(parsed) if isinstance(parsed, list) else set()
        return set()


# Module-level singleton — imported directly by routers, mirroring other services.
conflict_detector = ConflictDetector()
