"""
services/run_manager.py — RunManager: AgentRun lifecycle and handoff context.

Owns the `agent_runs` and `agent_messages` tables. `agent_messages` is the
session-memory store: every message an agent run produces or receives gets
saved here, which is what makes `build_handoff_pack` possible — a fallback
agent gets the prior run's conversation plus its partial diff, not just a
bare task title.

`route_task` is the entry point used when a task needs to move to a
different agent (credential failure recovery, or a future manual reroute).
It deliberately resets the task's `branch`/`worktree_path` columns to NULL
before calling `WorkspaceManager.create_worktree` — that service treats a
non-null `worktree_path` as "already created, reuse it," which is correct
for the normal case but wrong here, since the whole point of routing is to
give the fallback agent its own worktree. The new worktree forks from the
outgoing agent's branch (if one exists) rather than the repo's base branch,
so the fallback agent inherits the actual partial work, not just a textual
description of it.

Out of scope for this session: actually spawning the fallback agent process
(no process spawner exists yet — `route_task` only does the bookkeeping a
spawner would need: a new run, a new worktree, and a context pack).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ulid
from git import GitCommandError

from ..adapters.base import TaskResult
from ..database import execute, fetch_all, fetch_one, get_db
from ..events import _BLOCKED_PAYLOAD_KEYS, event_ledger
from ..models import MessageRole
from .task_service import task_service
from .workspace_manager import WorkspaceError, find_repo, resolve_base_branch, workspace_manager

logger = logging.getLogger(__name__)

# Secret-shaped substrings to redact from handoff content before it's ever
# interpolated into a pack another agent will read. Mirrors events.py's
# _sanitize_payload, but operates on free-text message content rather than
# dict keys — a leaked credential printed to an agent's stdout is still a
# string, not a labeled field.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),  # Anthropic keys
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI keys
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),  # GitHub personal tokens
    re.compile(r"[a-zA-Z0-9+/]{40,}={0,2}"),  # base64 blobs >= 40 chars
]
_BLOCKED_KEY_PATTERN = re.compile(
    r"\b(\w*(?:" + "|".join(re.escape(k) for k in _BLOCKED_PAYLOAD_KEYS) + r")\w*)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _sanitize_text(text: str) -> str:
    """Redact secret-shaped substrings from free-text content before handoff."""
    sanitized = _BLOCKED_KEY_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


class RunManagerError(Exception):
    """Raised when a run/handoff operation can't be completed."""


class RunNotFoundError(RunManagerError):
    """Raised when a referenced run doesn't exist — maps to HTTP 404."""


class RunManager:
    """AgentRun lifecycle, message history, and handoff context pack building."""

    async def create_run(self, task_id: str, agent_id: str) -> str:
        run_id = str(ulid.new())
        started_at = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO agent_runs (id, task_id, agent_id, started_at, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (run_id, task_id, agent_id, started_at),
            )

        task = await task_service.get(task_id)
        await event_ledger.emit(
            source="gateway",
            type="run.started",
            payload={"run_id": run_id, "task_id": task_id, "agent_id": agent_id},
            mission_id=task.mission_id if task else None,
            task_id=task_id,
            run_id=run_id,
        )
        return run_id

    async def complete_run(self, run_id: str, result: TaskResult) -> None:
        async with get_db() as db:
            run_row = await fetch_one(db, "SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        if run_row is None:
            raise RunNotFoundError(f"Run {run_id} not found")

        ended_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                """
                UPDATE agent_runs
                SET ended_at = ?, status = ?, confidence_score = ?, result_summary = ?
                WHERE id = ?
                """,
                (ended_at, result.status, result.confidence_score, result.result_summary, run_id),
            )

        task = await task_service.get(run_row["task_id"])
        mission_id = task.mission_id if task else None

        if result.status == "failed":
            await event_ledger.emit(
                source="gateway",
                type="run.failed",
                payload={"run_id": run_id, "reason": result.error_message or "unknown"},
                severity="error",
                mission_id=mission_id,
                task_id=run_row["task_id"],
                run_id=run_id,
            )
        else:
            await event_ledger.emit(
                source="gateway",
                type="run.completed",
                payload={"run_id": run_id, "confidence_score": result.confidence_score},
                mission_id=mission_id,
                task_id=run_row["task_id"],
                run_id=run_id,
            )

    async def save_message(
        self,
        run_id: str,
        agent_id: str,
        task_id: str,
        role: MessageRole,
        content: str,
        token_count: int | None = None,
    ) -> str:
        """Append a message to a run's session history — the handoff memory store."""
        message_id = str(ulid.new())
        created_at = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO agent_messages
                  (id, run_id, agent_id, task_id, role, content, token_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, run_id, agent_id, task_id, role, content, token_count, created_at),
            )
        return message_id

    async def build_handoff_pack(
        self, task_id: str, from_run_id: str, to_agent_id: str
    ) -> tuple[str, str]:
        """Assemble handoff context for a fallback agent. Returns (pack_id, content)."""
        task = await task_service.get(task_id)
        if task is None:
            raise RunManagerError(f"Task {task_id} not found")

        async with get_db() as db:
            messages = await fetch_all(
                db,
                "SELECT * FROM agent_messages WHERE run_id = ? ORDER BY created_at",
                (from_run_id,),
            )

        diff_text = "(no diff available — task never produced a worktree)"
        if task.branch:
            try:
                repo = find_repo()
                base_branch = resolve_base_branch(repo)
                diff_text = repo.git.diff(f"{base_branch}...{task.branch}") or "(no changes yet)"
            except WorkspaceError as exc:
                diff_text = f"(diff unavailable: {exc})"

        sections = [
            f"# Handoff context for task: {task.title}",
            f"Remaining objective: {task.title}",
            "## Prior session messages",
        ]
        if messages:
            sections.extend(f"[{m['role']}] {_sanitize_text(m['content'])}" for m in messages)
        else:
            sections.append("(no prior messages recorded)")
        sections.append("## Partial work (diff against base branch)")
        sections.append(diff_text)
        content = "\n\n".join(sections)

        pack_id = str(ulid.new())
        generated_at = datetime.now(UTC).isoformat()
        # Rough token estimate (chars/4) — good enough for the budget bookkeeping
        # this column exists for; precise tokenization isn't required here.
        tokens_used = max(1, len(content) // 4)

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO context_packs
                  (id, task_id, agent_id, run_id, documents, constraints,
                   token_budget, tokens_used, content, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    task_id,
                    to_agent_id,
                    from_run_id,
                    json.dumps([]),
                    json.dumps([]),
                    8000,
                    tokens_used,
                    content,
                    generated_at,
                ),
            )

        await event_ledger.emit(
            source="gateway",
            type="context_pack.generated",
            payload={
                "context_pack_id": pack_id,
                "task_id": task_id,
                "documents_count": 0,
                "tokens_used": tokens_used,
            },
            mission_id=task.mission_id,
            task_id=task_id,
            run_id=from_run_id,
        )

        return pack_id, content

    async def get_pack(self, pack_id: str) -> dict[str, Any] | None:
        """Full stored context pack row, including the persisted handoff content."""
        async with get_db() as db:
            return await fetch_one(db, "SELECT * FROM context_packs WHERE id = ?", (pack_id,))

    async def route_task(
        self, task_id: str, to_agent_id: str, from_run_id: str
    ) -> tuple[str, str]:
        """Hand a task off to a fallback agent. Returns (new_run_id, context_pack_id)."""
        task = await task_service.get(task_id)
        if task is None:
            raise RunManagerError(f"Task {task_id} not found")

        async with get_db() as db:
            from_run = await fetch_one(
                db, "SELECT agent_id FROM agent_runs WHERE id = ?", (from_run_id,)
            )
        if from_run is None:
            raise RunNotFoundError(f"Run {from_run_id} not found")
        from_agent_id = str(from_run["agent_id"])

        # Build the pack before resetting branch/worktree — it needs the
        # outgoing agent's branch to compute the partial-work diff.
        context_pack_id, _content = await self.build_handoff_pack(
            task_id, from_run_id, to_agent_id
        )
        new_run_id = await self.create_run(task_id, to_agent_id)

        fork_branch = task.branch
        old_worktree_path = task.worktree_path
        async with get_db() as db:
            await execute(
                db,
                "UPDATE tasks SET assigned_agent_id = ?, branch = NULL, worktree_path = NULL "
                "WHERE id = ?",
                (to_agent_id, task_id),
            )

        repo = find_repo()
        base_branch = fork_branch or resolve_base_branch(repo)

        # The outgoing worktree lives at the same path a new worktree for this
        # task_id would need (path is keyed by task_id, not agent_id) — the
        # outgoing branch already has the partial work committed (via freeze's
        # WIP commit), so it's safe to remove the directory before forking the
        # fallback agent's worktree from that same branch.
        if old_worktree_path and Path(old_worktree_path).exists():
            try:
                repo.git.worktree("remove", old_worktree_path, "--force")
            except GitCommandError as exc:
                logger.warning(
                    "Failed to remove outgoing worktree for task %s before handoff: %s",
                    task_id,
                    exc,
                )

        await workspace_manager.create_worktree(task_id, to_agent_id, base_branch)

        await event_ledger.emit(
            source="gateway",
            type="run.handed_off",
            payload={
                "from_run_id": from_run_id,
                "to_run_id": new_run_id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
            },
            mission_id=task.mission_id,
            task_id=task_id,
            run_id=new_run_id,
        )

        return new_run_id, context_pack_id


# Module-level singleton — imported directly by routers, mirroring other services.
run_manager = RunManager()
