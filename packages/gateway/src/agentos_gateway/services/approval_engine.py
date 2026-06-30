"""
services/approval_engine.py — ApprovalEngine: risk scoring, action interception,
and the approve/deny decision flow.

`score()` is a deterministic function of the action plus whatever
`policies.yml` says is sensitive — no ML, no heuristics beyond glob/substring
matching, so the same inputs always produce the same score. `load_policies()`
deliberately never caches: it's re-read from disk on every `intercept()` call
so a user editing `policies.yml` takes effect on the very next action,
without restarting the gateway.

`intercept()` is the entry point an adapter calls (via `request_action()` on
`BaseAdapter`) before doing anything risky. For `medium`/`high` risk it
blocks the calling coroutine by polling `action_requests.status` every
500ms (not a real blocking sleep — `asyncio.sleep`) until a human resolves it
via `approve()`/`deny()`, or it times out after 300s and is treated as a
denial. `low` risk auto-approves immediately; `critical` (which is also what
every dangerous-command match maps to, since that bypasses scoring) blocks
immediately and never reaches a human.

Out of scope for this session: actually wiring `intercept()` into a running
adapter (no process spawner exists yet — see `credential_manager.py`'s and
`run_manager.py`'s docstrings for the same gap), and conflict detection
(`conflict_detector.py`, the other Day 5 service).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ulid
import yaml

from ..database import execute, fetch_all, fetch_one, get_db
from ..events import event_ledger
from ..models import (
    ApprovalScope,
    ApproveResponse,
    DenyResponse,
    InboxActionRequest,
    RiskLevel,
)
from .task_service import task_service

logger = logging.getLogger(__name__)

ACTION_TYPE_WEIGHTS: dict[str, int] = {
    "file_write": 20,
    "shell_cmd": 40,
    "network": 30,
    "deploy": 60,
    "migration": 70,
    "db": 50,
}

# Action types that aren't easily undone once they run.
_REVERSIBILITY_PENALTY_TYPES = frozenset({"deploy", "migration", "db"})

# Config-file-ish path patterns — simple glob matching, no parsing.
_CONFIG_FILE_GLOBS = ("*.config.*", "*.yml", "*.toml", "*.env*")

_API_CONTRACTS_GLOB = "api-contracts/**"

APPROVAL_TIMEOUT_SECONDS = 300
APPROVAL_POLL_INTERVAL_SECONDS = 0.5


class ApprovalEngineError(Exception):
    """Raised when an approval/denial can't be completed (e.g. unknown action_request_id)."""


def _resolve_agentos_dir() -> Path:
    """Walk up from CWD until a `.agentos/` dir is found, mirroring main.py's
    resolve_agentos_dir(). Duplicated locally rather than imported, since
    importing from main.py would create a circular import (main -> routers ->
    this service -> main)."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".agentos"
        if candidate.is_dir():
            return candidate
    return cwd / ".agentos"


class ApprovalEngine:
    """Risk scoring and the approve/deny lifecycle for risky agent actions."""

    def __init__(self) -> None:
        # Serializes policies.yml read-modify-write within this process — the
        # gateway is the only writer (per architecture), so this is the only
        # concurrency that can actually clobber a write.
        self._policies_lock = asyncio.Lock()

    def load_policies(self) -> dict[str, Any]:
        """Fresh read of .agentos/policies.yml. Never cached — see module docstring."""
        path = _resolve_agentos_dir() / "policies.yml"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}

    def score(
        self,
        action_type: str,
        command_or_tool: str | None,
        file_paths: list[str] | None,
        environment: str | None,
        policies: dict[str, Any] | None = None,
    ) -> int:
        """Deterministic 0-100 risk score. Dangerous commands short-circuit to 100."""
        policies = policies if policies is not None else self.load_policies()

        dangerous_commands = policies.get("dangerous_commands", [])
        if command_or_tool and any(
            self._matches_dangerous(command_or_tool, pattern) for pattern in dangerous_commands
        ):
            return 100

        total = ACTION_TYPE_WEIGHTS.get(action_type, 0)

        sensitive_paths = policies.get("sensitive_paths", [])
        paths = file_paths or []
        path_sensitivity = 0
        if paths:
            if any(any(fnmatch.fnmatch(p, pat) for pat in sensitive_paths) for p in paths):
                path_sensitivity += 40
            if any(fnmatch.fnmatch(p, _API_CONTRACTS_GLOB) for p in paths):
                path_sensitivity += 20
            if any(any(fnmatch.fnmatch(p, pat) for pat in _CONFIG_FILE_GLOBS) for p in paths):
                path_sensitivity += 15
        total += path_sensitivity

        if environment == "production":
            total += 20

        if action_type in _REVERSIBILITY_PENALTY_TYPES:
            total += 20

        return min(total, 100)

    def decide(self, risk_score: int) -> RiskLevel:
        """Maps a score to a risk level: <30 low (auto-approve), 30-69 medium /
        70-99 high (ask user), 100 critical (block)."""
        if risk_score >= 100:
            return "critical"
        if risk_score >= 70:
            return "high"
        if risk_score >= 30:
            return "medium"
        return "low"

    async def list_pending(self, limit: int = 50) -> list[InboxActionRequest]:
        """Pending action requests joined with run/agent/task context, for the inbox."""
        async with get_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT ar.id, ar.run_id, ar.action_type, ar.command_or_tool, ar.risk_score,
                       ar.risk_level, ar.explanation, ar.evidence, ar.status, ar.created_at,
                       r.agent_id AS agent_id, a.display_name AS agent_name, t.title AS task_title
                FROM action_requests ar
                JOIN agent_runs r ON ar.run_id = r.id
                JOIN agents a ON r.agent_id = a.id
                JOIN tasks t ON r.task_id = t.id
                WHERE ar.status = 'pending'
                ORDER BY ar.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [
            InboxActionRequest(
                id=row["id"],
                run_id=row["run_id"],
                agent_id=row["agent_id"],
                agent_name=row["agent_name"],
                task_title=row["task_title"],
                action_type=row["action_type"],
                command_or_tool=row["command_or_tool"],
                risk_score=row["risk_score"],
                risk_level=row["risk_level"],
                explanation=row["explanation"],
                evidence=json.loads(row["evidence"]) if row["evidence"] else None,
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def intercept(
        self,
        run_id: str,
        action_type: str,
        command_or_tool: str | None,
        file_paths: list[str] | None,
        explanation: str | None,
        evidence: dict[str, Any] | None,
        environment: str | None,
    ) -> bool:
        """Score an action and either auto-approve, block, or wait on a human decision."""
        policies = self.load_policies()
        risk_score = self.score(action_type, command_or_tool, file_paths, environment, policies)
        risk_level = self.decide(risk_score)

        task_id, mission_id = await self._scope_for_run(run_id)

        action_request_id = str(ulid.new())
        created_at = datetime.now(UTC).isoformat()
        full_evidence = {
            **(evidence or {}),
            "file_paths": file_paths or [],
            "environment": environment,
        }

        if risk_level == "low":
            status = "auto_approved"
        elif risk_level == "critical":
            status = "blocked"
        else:
            status = "pending"

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO action_requests
                  (id, run_id, action_type, command_or_tool, risk_score, risk_level,
                   explanation, evidence, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_request_id,
                    run_id,
                    action_type,
                    command_or_tool,
                    risk_score,
                    risk_level,
                    explanation,
                    json.dumps(full_evidence),
                    status,
                    created_at,
                ),
            )

        if status == "auto_approved":
            await event_ledger.emit(
                source="gateway",
                type="tool.auto_approved",
                payload={"action_request_id": action_request_id, "risk_score": risk_score},
                mission_id=mission_id,
                task_id=task_id,
                run_id=run_id,
            )
            return True

        if status == "blocked":
            await event_ledger.emit(
                source="gateway",
                type="tool.blocked",
                payload={
                    "action_request_id": action_request_id,
                    "reason": f"risk_score={risk_score} (critical or dangerous-command match)",
                },
                severity="critical",
                mission_id=mission_id,
                task_id=task_id,
                run_id=run_id,
            )
            return False

        await event_ledger.emit(
            source="gateway",
            type="tool.requested",
            payload={
                "action_request_id": action_request_id,
                "action_type": action_type,
                "risk_level": risk_level,
                "risk_score": risk_score,
            },
            mission_id=mission_id,
            task_id=task_id,
            run_id=run_id,
        )

        return await self._await_decision(action_request_id, mission_id, task_id, run_id)

    async def _await_decision(
        self, action_request_id: str, mission_id: str | None, task_id: str | None, run_id: str
    ) -> bool:
        elapsed = 0.0
        while elapsed < APPROVAL_TIMEOUT_SECONDS:
            await asyncio.sleep(APPROVAL_POLL_INTERVAL_SECONDS)
            elapsed += APPROVAL_POLL_INTERVAL_SECONDS

            async with get_db() as db:
                row = await fetch_one(
                    db, "SELECT status FROM action_requests WHERE id = ?", (action_request_id,)
                )
            if row is None:
                return False
            if row["status"] != "pending":
                return bool(row["status"] == "approved")

        async with get_db() as db:
            await execute(
                db,
                "UPDATE action_requests SET status = 'expired' WHERE id = ?",
                (action_request_id,),
            )
        await event_ledger.emit(
            source="gateway",
            type="tool.denied",
            payload={
                "action_request_id": action_request_id,
                "reason": "approval timed out after 300s",
            },
            severity="warning",
            mission_id=mission_id,
            task_id=task_id,
            run_id=run_id,
        )
        return False

    async def approve(
        self,
        action_request_id: str,
        scope: ApprovalScope = "once",
        note: str | None = None,
        decided_by: str = "user",
    ) -> ApproveResponse:
        ar = await self._get_action_request_or_raise(action_request_id)

        decided_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                "UPDATE action_requests SET status = 'approved' WHERE id = ?",
                (action_request_id,),
            )
            await execute(
                db,
                """
                INSERT INTO approvals
                  (id, action_request_id, decision, decided_by, decided_at, scope, note)
                VALUES (?, ?, 'approved', ?, ?, ?, ?)
                """,
                (str(ulid.new()), action_request_id, decided_by, decided_at, scope, note),
            )

        if scope == "always" and ar["command_or_tool"]:
            await self._append_auto_approve_pattern(str(ar["command_or_tool"]))

        await event_ledger.emit(
            source="gateway",
            type="tool.approved",
            payload={"action_request_id": action_request_id, "scope": scope},
            run_id=str(ar["run_id"]),
        )

        return ApproveResponse(approved=True, action_request_id=action_request_id, scope=scope)

    async def deny(
        self, action_request_id: str, note: str | None = None, decided_by: str = "user"
    ) -> DenyResponse:
        ar = await self._get_action_request_or_raise(action_request_id)

        decided_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                "UPDATE action_requests SET status = 'denied' WHERE id = ?",
                (action_request_id,),
            )
            await execute(
                db,
                """
                INSERT INTO approvals
                  (id, action_request_id, decision, decided_by, decided_at, scope, note)
                VALUES (?, ?, 'denied', ?, ?, 'once', ?)
                """,
                (str(ulid.new()), action_request_id, decided_by, decided_at, note),
            )

        await event_ledger.emit(
            source="gateway",
            type="tool.denied",
            payload={"action_request_id": action_request_id, "reason": note},
            run_id=str(ar["run_id"]),
        )

        return DenyResponse(denied=True, action_request_id=action_request_id)

    async def _get_action_request_or_raise(self, action_request_id: str) -> dict[str, Any]:
        async with get_db() as db:
            ar = await fetch_one(
                db, "SELECT * FROM action_requests WHERE id = ?", (action_request_id,)
            )
        if ar is None:
            raise ApprovalEngineError(f"Action request {action_request_id} not found")
        return ar

    async def _scope_for_run(self, run_id: str) -> tuple[str | None, str | None]:
        async with get_db() as db:
            run_row = await fetch_one(db, "SELECT task_id FROM agent_runs WHERE id = ?", (run_id,))
        if run_row is None:
            return None, None
        task = await task_service.get(str(run_row["task_id"]))
        return (task.id, task.mission_id) if task else (str(run_row["task_id"]), None)

    async def _append_auto_approve_pattern(self, pattern: str) -> None:
        """Read -> modify -> write policies.yml, serialized so two concurrent
        scope="always" approvals can't clobber each other's append."""
        async with self._policies_lock:
            policies = self.load_policies()
            patterns: list[str] = policies.setdefault("auto_approve_patterns", [])
            if pattern not in patterns:
                patterns.append(pattern)

            path = _resolve_agentos_dir() / "policies.yml"
            if not path.parent.exists():
                logger.warning(
                    "Cannot persist auto-approve pattern %r — %s does not exist",
                    pattern,
                    path.parent,
                )
                return
            with path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(policies, f, default_flow_style=False, sort_keys=False)

    def _matches_dangerous(self, command: str, pattern: str) -> bool:
        return fnmatch.fnmatch(command.lower(), f"*{pattern.lower()}*")


# Module-level singleton — imported directly by routers, mirroring other services.
approval_engine = ApprovalEngine()
