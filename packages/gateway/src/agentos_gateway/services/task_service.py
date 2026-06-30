"""
services/task_service.py — TaskService: task CRUD and dependency resolution.

Owns all reads/writes to the `tasks` table. Tasks are created exclusively by
MissionService's planner (there is no standalone "create task" endpoint) — see
mission_service.py — but every read and dependency check lives here so other
services never reach into the tasks table directly.

Dependency resolution answers "is this task allowed to start yet" — i.e. have
all of its `depends_on` tasks reached 'completed'. Actually starting a ready
task (spawning an agent run against a Git worktree) is Phase 3 Day 3+ scope
and isn't implemented here; `is_ready`/`list_ready` exist so Day 3's run
scheduler has something to call.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from ..database import execute, fetch_all, fetch_one, get_db, parse_json_fields
from ..events import event_ledger
from ..models import TaskResponse

logger = logging.getLogger(__name__)

_JSON_FIELDS = ["depends_on", "files_owned"]


def _row_to_task(record: dict[str, Any]) -> TaskResponse:
    return TaskResponse(**parse_json_fields(record, _JSON_FIELDS))


class TaskService:
    """CRUD and dependency resolution for tasks."""

    async def create(
        self,
        *,
        task_id: str,
        mission_id: str,
        title: str,
        assigned_agent_id: str | None,
        depends_on: list[str],
        files_owned: list[str] | None = None,
    ) -> TaskResponse:
        created_at = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO tasks
                  (id, mission_id, title, assigned_agent_id, depends_on,
                   status, files_owned, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    task_id,
                    mission_id,
                    title,
                    assigned_agent_id,
                    json.dumps(depends_on),
                    json.dumps(files_owned or []),
                    created_at,
                ),
            )

        await event_ledger.emit(
            source="gateway",
            type="task.created",
            payload={
                "task_id": task_id,
                "title": title,
                "assigned_agent_id": assigned_agent_id,
            },
            mission_id=mission_id,
            task_id=task_id,
        )

        task = await self.get(task_id)
        assert task is not None
        return task

    async def get(self, task_id: str) -> TaskResponse | None:
        async with get_db() as db:
            row = await fetch_one(db, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_task(row) if row else None

    async def list_by_mission(self, mission_id: str) -> list[TaskResponse]:
        async with get_db() as db:
            rows = await fetch_all(
                db,
                "SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_at",
                (mission_id,),
            )
        return [_row_to_task(row) for row in rows]

    async def is_ready(self, task_id: str) -> bool:
        """True if every task in this task's depends_on list has status 'completed'."""
        task = await self.get(task_id)
        if task is None:
            return False
        if not task.depends_on:
            return True

        async with get_db() as db:
            placeholders = ",".join("?" for _ in task.depends_on)
            rows = await fetch_all(
                db,
                f"SELECT status FROM tasks WHERE id IN ({placeholders})",
                tuple(task.depends_on),
            )
        return len(rows) == len(task.depends_on) and all(
            row["status"] == "completed" for row in rows
        )

    async def list_ready(self, mission_id: str) -> list[TaskResponse]:
        """Pending tasks in this mission whose dependencies are all completed."""
        tasks = await self.list_by_mission(mission_id)
        ready: list[TaskResponse] = []
        for task in tasks:
            if task.status != "pending":
                continue
            if await self.is_ready(task.id):
                ready.append(task)
        return ready


# Module-level singleton — imported directly by routers and MissionService.
task_service = TaskService()
