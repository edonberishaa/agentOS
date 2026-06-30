"""
services/mission_service.py — MissionService: mission CRUD and AI-powered task planning.

Owns all reads/writes to the `missions` table. The planner half calls the
Anthropic API (claude-sonnet-4-6) to decompose a mission's natural-language
objective into a task DAG, given the currently registered agents, and
delegates actual task persistence to TaskService. The planner only proposes
the DAG — it does not start any work. Running a planned mission (spawning
agent runs against Git worktrees) is Phase 3 Day 3+ scope and is not
implemented here.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import ulid
from anthropic import AsyncAnthropic

from ..database import execute, fetch_all, fetch_one, get_db
from ..events import event_ledger
from ..models import (
    CreateMissionRequest,
    MissionPlanResponse,
    MissionResponse,
    MissionStatus,
    MissionStatusResponse,
    PlannedTask,
    RiskLevel,
    RunMissionRequest,
    TaskStatusItem,
)
from .agent_registry import agent_registry
from .task_service import task_service
from .workspace_manager import WorkspaceError, find_repo, resolve_base_branch, workspace_manager

logger = logging.getLogger(__name__)

PLANNER_MODEL = "claude-sonnet-4-6"
_VALID_RISK_LEVELS: tuple[RiskLevel, ...] = ("low", "medium", "high", "critical")
_RISK_ORDER: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class MissionPlanningError(Exception):
    """Raised when the planner LLM call fails or returns an unusable plan."""


class MissionNotFoundError(Exception):
    """Raised when a referenced mission doesn't exist — maps to HTTP 404."""


class MissionAlreadyTerminalError(Exception):
    """Raised when run_mission is called on a completed/failed mission — maps to HTTP 409."""


def _row_to_mission(record: dict[str, Any]) -> MissionResponse:
    return MissionResponse(**record)


def _coerce_risk_level(value: Any) -> RiskLevel:
    return value if value in _VALID_RISK_LEVELS else "medium"


def _build_planner_prompt(objective: str, agents: list[dict[str, Any]]) -> str:
    agents_json = json.dumps(agents, indent=2)
    return f"""You are decomposing a software engineering mission into a task DAG \
for a team of AI coding agents.

Mission objective:
{objective}

Available agents (assign each task to the best-matching agent by role and \
capabilities, or null if none fit):
{agents_json}

Return ONLY valid JSON (no markdown fences, no commentary) matching exactly \
this shape:
{{
  "tasks": [
    {{
      "key": "t1",
      "title": "short imperative task title",
      "assigned_agent_id": "<one of the agent ids above, or null>",
      "depends_on": ["t1"],
      "risk_level": "low|medium|high|critical",
      "estimated_files": ["path/to/file.ts"]
    }}
  ]
}}

Rules:
- "key" values are your own short identifiers (t1, t2, ...), unique in this response.
- "depends_on" may only reference "key" values defined elsewhere in this list, \
and must not form a cycle.
- "assigned_agent_id" must be exactly one of the agent ids provided above, or null.
- Keep the task list focused on only what's needed to achieve the objective.
"""


def _parse_planner_response(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MissionPlanningError(f"Planner returned invalid JSON: {exc}") from exc

    tasks = parsed.get("tasks") if isinstance(parsed, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise MissionPlanningError("Planner response did not contain a non-empty 'tasks' list")
    return tasks


def _has_cycle(keys: list[str], depends_on_by_key: dict[str, list[str]]) -> bool:
    """DFS cycle check over the planner's key-based dependency graph."""
    white, gray, black = 0, 1, 2
    color = {key: white for key in keys}

    def visit(key: str) -> bool:
        color[key] = gray
        for dep in depends_on_by_key.get(key, []):
            if dep not in color:
                continue
            if color[dep] == gray:
                return True
            if color[dep] == white and visit(dep):
                return True
        color[key] = black
        return False

    return any(color[key] == white and visit(key) for key in keys)


class MissionService:
    """CRUD for missions, plus the AI-powered task planner."""

    async def create(self, body: CreateMissionRequest) -> MissionResponse:
        mission_id = str(ulid.new())
        created_at = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO missions (id, title, objective, status, created_at)
                VALUES (?, ?, ?, 'planning', ?)
                """,
                (mission_id, body.title, body.objective, created_at),
            )

        await event_ledger.emit(
            source="gateway",
            type="mission.created",
            payload={"mission_id": mission_id, "title": body.title, "objective": body.objective},
            mission_id=mission_id,
        )

        mission = await self.get(mission_id)
        assert mission is not None
        return mission

    async def get(self, mission_id: str) -> MissionResponse | None:
        async with get_db() as db:
            row = await fetch_one(db, "SELECT * FROM missions WHERE id = ?", (mission_id,))
        return _row_to_mission(row) if row else None

    async def list_all(self, status: MissionStatus | None = None) -> list[MissionResponse]:
        """List missions, newest first, optionally filtered by status."""
        async with get_db() as db:
            if status is not None:
                rows = await fetch_all(
                    db,
                    "SELECT * FROM missions WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                )
            else:
                rows = await fetch_all(db, "SELECT * FROM missions ORDER BY created_at DESC")
        return [_row_to_mission(row) for row in rows]

    async def plan(self, mission_id: str) -> MissionPlanResponse | None:
        mission = await self.get(mission_id)
        if mission is None:
            return None

        await event_ledger.emit(
            source="gateway",
            type="mission.planning_started",
            payload={"mission_id": mission_id},
            mission_id=mission_id,
        )

        agents = await agent_registry.list_all()
        agent_summaries = [
            {
                "id": agent.id,
                "display_name": agent.display_name,
                "role": agent.role,
                "capabilities": agent.capabilities,
            }
            for agent in agents
        ]
        known_agent_ids = {agent.id for agent in agents}

        raw_tasks = await self._call_planner(mission.objective, agent_summaries)

        keys = [str(t.get("key")) for t in raw_tasks]
        depends_on_by_key = {
            str(t.get("key")): [str(d) for d in (t.get("depends_on") or [])] for t in raw_tasks
        }
        if _has_cycle(keys, depends_on_by_key):
            raise MissionPlanningError("Planner returned a cyclic task dependency graph")

        key_to_id = {key: str(ulid.new()) for key in keys}
        planned_tasks: list[PlannedTask] = []

        for raw_task in raw_tasks:
            key = str(raw_task.get("key"))
            task_id = key_to_id[key]

            assigned_agent_id = raw_task.get("assigned_agent_id")
            if assigned_agent_id not in known_agent_ids:
                if assigned_agent_id is not None:
                    logger.warning(
                        "Planner assigned unknown agent_id %s for task %s — leaving unassigned",
                        assigned_agent_id,
                        key,
                    )
                assigned_agent_id = None

            depends_on_ids = [
                key_to_id[dep] for dep in depends_on_by_key.get(key, []) if dep in key_to_id
            ]
            risk_level = _coerce_risk_level(raw_task.get("risk_level"))
            estimated_files = [str(f) for f in (raw_task.get("estimated_files") or [])]
            title = str(raw_task.get("title") or "Untitled task")

            task = await task_service.create(
                task_id=task_id,
                mission_id=mission_id,
                title=title,
                assigned_agent_id=assigned_agent_id,
                depends_on=depends_on_ids,
                files_owned=estimated_files,
            )

            if assigned_agent_id:
                await event_ledger.emit(
                    source="gateway",
                    type="task.assigned",
                    payload={"task_id": task.id, "agent_id": assigned_agent_id},
                    mission_id=mission_id,
                    task_id=task.id,
                )

            planned_tasks.append(
                PlannedTask(
                    id=task.id,
                    title=task.title,
                    assigned_agent_id=task.assigned_agent_id,
                    depends_on=task.depends_on,
                    risk_level=risk_level,
                    estimated_files=estimated_files,
                )
            )

        if planned_tasks:
            max_risk = max(planned_tasks, key=lambda t: _RISK_ORDER[t.risk_level]).risk_level
            async with get_db() as db:
                await execute(
                    db, "UPDATE missions SET risk_level = ? WHERE id = ?", (max_risk, mission_id)
                )

        return MissionPlanResponse(mission_id=mission_id, tasks=planned_tasks)

    async def run_mission(self, mission_id: str, body: RunMissionRequest) -> dict[str, Any]:
        """Start ready tasks: create worktrees + bookkeeping AgentRun rows.

        Does not spawn an actual agent process — no process spawner exists yet
        (deferred; see the phase-tracker skill).
        """
        mission = await self.get(mission_id)
        if mission is None:
            raise MissionNotFoundError(f"Mission {mission_id} not found")
        if mission.status in ("completed", "failed"):
            raise MissionAlreadyTerminalError(f"Mission already {mission.status}")

        all_tasks = await task_service.list_by_mission(mission_id)
        ready_tasks = await task_service.list_ready(mission_id)

        if mission.status == "planning":
            async with get_db() as db:
                await execute(
                    db, "UPDATE missions SET status = 'running' WHERE id = ?", (mission_id,)
                )
            await event_ledger.emit(
                source="gateway",
                type="mission.running",
                payload={
                    "mission_id": mission_id,
                    "task_count": len(all_tasks),
                    "parallel": body.parallel,
                },
                mission_id=mission_id,
            )

        repo = find_repo()
        base_branch = resolve_base_branch(repo)

        started_tasks: list[str] = []
        skipped_tasks: list[dict[str, str]] = []

        for task in ready_tasks:
            if not task.assigned_agent_id:
                skipped_tasks.append({"task_id": task.id, "reason": "no agent assigned"})
                continue

            try:
                await workspace_manager.create_worktree(
                    task.id, task.assigned_agent_id, base_branch
                )
            except WorkspaceError as exc:
                skipped_tasks.append({"task_id": task.id, "reason": str(exc)})
                continue

            run_id = str(ulid.new())
            started_at = datetime.now(UTC).isoformat()
            async with get_db() as db:
                await execute(
                    db,
                    """
                    INSERT INTO agent_runs (id, task_id, agent_id, started_at, status)
                    VALUES (?, ?, ?, ?, 'running')
                    """,
                    (run_id, task.id, task.assigned_agent_id, started_at),
                )
                await execute(db, "UPDATE tasks SET status = 'running' WHERE id = ?", (task.id,))

            updated_task = await task_service.get(task.id)
            await event_ledger.emit(
                source="gateway",
                type="task.started",
                payload={
                    "task_id": task.id,
                    "agent_id": task.assigned_agent_id,
                    "branch": updated_task.branch if updated_task else None,
                    "worktree_path": updated_task.worktree_path if updated_task else None,
                },
                mission_id=mission_id,
                task_id=task.id,
            )
            started_tasks.append(task.id)

        return {"started_tasks": started_tasks, "skipped_tasks": skipped_tasks}

    async def get_status(self, mission_id: str) -> MissionStatusResponse | None:
        mission = await self.get(mission_id)
        if mission is None:
            return None

        tasks = await task_service.list_by_mission(mission_id)

        agent_names: dict[str, str] = {}
        for task in tasks:
            if task.assigned_agent_id and task.assigned_agent_id not in agent_names:
                agent = await agent_registry.get(task.assigned_agent_id)
                if agent:
                    agent_names[task.assigned_agent_id] = agent.display_name

        task_items = [
            TaskStatusItem(
                id=task.id,
                title=task.title,
                agent_id=task.assigned_agent_id,
                agent_name=(
                    agent_names.get(task.assigned_agent_id) if task.assigned_agent_id else None
                ),
                status=task.status,
                # TODO Phase 3 Day 3+: derive from the active AgentRun once runs exist.
                progress_pct=None,
            )
            for task in tasks
        ]

        return MissionStatusResponse(mission_id=mission_id, status=mission.status, tasks=task_items)

    async def _call_planner(
        self, objective: str, agent_summaries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise MissionPlanningError("ANTHROPIC_API_KEY is not set")

        prompt = _build_planner_prompt(objective, agent_summaries)
        client = AsyncAnthropic(api_key=api_key)

        try:
            response = await client.messages.create(
                model=PLANNER_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise MissionPlanningError(f"Anthropic API call failed: {exc}") from exc

        raw_text = "".join(block.text for block in response.content if block.type == "text")
        return _parse_planner_response(raw_text)


mission_service = MissionService()
