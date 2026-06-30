"""
services/credential_manager.py — CredentialManager: credential lifecycle and
failure recovery.

Owns the OS keychain (via `keyring`) as the only place a credential value is
ever stored. Nothing in this file (or anywhere else) writes a credential
value to SQLite, a log statement, or an event payload — `store()`/`retrieve()`
pass the value through to/from `keyring` and otherwise never touch it.

`keyring` is a synchronous library; every call into it is pushed onto the
default executor via `run_in_executor` so it never blocks the event loop.

Failure detection delegates to the registered agent's own adapter
(`BaseAdapter.detect_credential_failure`) rather than re-implementing
signature matching here — adapters own their failure signatures, this
service only orchestrates what happens once a failure is found:
freeze the task's workspace, record a `credential_events` row, update the
agent's status, emit the matching event, and (for rate limiting only)
schedule a bounded retry.

Out of scope for this session (Phase 3 Day 5+/later): the approval engine,
conflict detection, context vault document ranking, and actually spawning or
re-invoking an agent process — `on_failure`'s rate-limit retry schedules a
timer and emits an event when the backoff window elapses, but does not
restart the agent itself, since there is no process spawner yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime
from typing import Any

import keyring
import ulid

from ..adapters.base import AdapterConfig, BaseAdapter, CredentialFailureError
from ..adapters.claude_code import ClaudeCodeAdapter
from ..adapters.codex import CodexAdapter
from ..adapters.mock import MockAdapter
from ..database import execute, fetch_all, fetch_one, get_db
from ..events import event_ledger
from ..models import (
    AgentHealthResponse,
    AgentStatus,
    InboxCredentialEvent,
    RotateCredentialResponse,
    RouteCredentialEventResponse,
)
from .agent_registry import agent_registry
from .run_manager import run_manager
from .workspace_manager import workspace_manager

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "agentos"

# Mirrors RATE_LIMIT_RETRY_DELAYS_MS in packages/shared/src/constants.ts —
# kept as a plain Python constant since the gateway can't import TS.
RATE_LIMIT_RETRY_DELAYS_MS = [1000, 2000, 4000]

_ADAPTER_CLASSES: dict[str, type[BaseAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "mock": MockAdapter,
}

# failure_type -> resulting agent status
_STATUS_BY_FAILURE_TYPE: dict[str, AgentStatus] = {
    "subscription_expired": "expired",
    "auth_invalid": "expired",
    "quota_exceeded": "degraded",
    "rate_limited": "degraded",
}

# failure_type -> credential event type to emit
_EVENT_TYPE_BY_FAILURE: dict[str, str] = {
    "subscription_expired": "credential.expired",
    "auth_invalid": "credential.expired",
    "quota_exceeded": "credential.quota_exceeded",
    "rate_limited": "credential.rate_limited",
}


class CredentialManagerError(Exception):
    """Raised when a credential operation can't be completed."""


class CredentialEventNotFoundError(CredentialManagerError):
    """Raised when a referenced credential event/run doesn't exist — maps to HTTP 404."""


class CredentialRoutingError(CredentialManagerError):
    """Raised when a credential event can't be routed to a fallback agent — maps to HTTP 409."""


def _keyring_key(agent_id: str) -> str:
    return f"agent-os:{agent_id}"


class CredentialManager:
    """Credential storage, health probing, and failure recovery for agents."""

    def __init__(self) -> None:
        # run_id -> number of rate-limit retries already scheduled. In-memory
        # only — bounded backoff sequences are short-lived, so this doesn't
        # need to survive a gateway restart.
        self._rate_limit_attempts: dict[str, int] = {}

    async def probe(self, agent_id: str) -> AgentHealthResponse | None:
        """Check PATH + keychain presence, update agent status, emit a credential event."""
        agent = await agent_registry.get(agent_id)
        if agent is None:
            return None

        checked_at = datetime.now(UTC).isoformat()
        command_found = shutil.which(agent.command) is not None
        credential_value = await self.retrieve(agent_id)
        credential_found = credential_value is not None
        healthy = command_found and credential_found

        new_status: AgentStatus = "idle" if healthy else "degraded"
        async with get_db() as db:
            await execute(
                db,
                "UPDATE agents SET last_health_check = ?, status = ? WHERE id = ?",
                (checked_at, new_status, agent_id),
            )

        if healthy:
            await event_ledger.emit(
                source="gateway",
                type="credential.validated",
                payload={"agent_id": agent_id},
            )
        else:
            reason = (
                "command not found on PATH"
                if not command_found
                else "no credential stored in keychain"
            )
            await event_ledger.emit(
                source="gateway",
                type="credential.warning",
                payload={"agent_id": agent_id, "reason": reason},
                severity="warning",
            )

        return AgentHealthResponse(
            agent_id=agent_id,
            status="healthy" if healthy else "warning" if command_found else "unknown",
            credential_type="local_auth",
            last_validated=checked_at if healthy else None,
            quota_remaining=None,
            expiry_hint=None,
            auto_recovery_eligible=agent.fallback_agent_id is not None,
            fallback_agent_id=agent.fallback_agent_id,
        )

    async def store(self, agent_id: str, credential_value: str) -> None:
        """Save a credential to the OS keychain. Never persisted anywhere else."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, keyring.set_password, _KEYRING_SERVICE, _keyring_key(agent_id), credential_value
        )

    async def retrieve(self, agent_id: str) -> str | None:
        """Fetch a credential from the OS keychain, or None if not found."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, keyring.get_password, _KEYRING_SERVICE, _keyring_key(agent_id)
        )

    async def rotate(self, agent_id: str, new_value: str) -> RotateCredentialResponse:
        """Store a new credential value and re-probe to confirm it's usable."""
        agent = await agent_registry.get(agent_id)
        if agent is None:
            raise CredentialManagerError(f"Agent {agent_id} not found")

        await self.store(agent_id, new_value)
        health = await self.probe(agent_id)
        validated = health is not None and health.status == "healthy"

        await event_ledger.emit(
            source="gateway",
            type="credential.rotated",
            payload={"agent_id": agent_id},
        )

        return RotateCredentialResponse(rotated=True, validated=validated)

    async def list_unresolved(self, limit: int = 50) -> list[InboxCredentialEvent]:
        """Unresolved credential events joined with agent/run/task context, for the inbox."""
        async with get_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT ce.id, ce.agent_id, ce.event_type, ce.mission_id, ce.details, ce.created_at,
                       a.display_name AS agent_name, r.task_id AS task_id,
                       r.workspace_state AS branch_state, t.title AS task_title
                FROM credential_events ce
                JOIN agents a ON ce.agent_id = a.id
                LEFT JOIN agent_runs r ON ce.run_id = r.id
                LEFT JOIN tasks t ON r.task_id = t.id
                WHERE ce.resolved_at IS NULL
                ORDER BY ce.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [
            InboxCredentialEvent(
                id=row["id"],
                agent_id=row["agent_id"],
                agent_name=row["agent_name"],
                event_type=row["event_type"],
                task_id=row["task_id"],
                task_title=row["task_title"],
                task_progress_pct=None,
                branch_state=row["branch_state"],
                mission_id=row["mission_id"],
                details=json.loads(row["details"]) if row["details"] else None,
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def route_credential_event(
        self, credential_event_id: str
    ) -> RouteCredentialEventResponse:
        """Route a credential event's run to its agent's configured fallback_agent_id.

        There's no request body on the inbox route endpoint to specify a target
        explicitly, so this always uses `fallback_agent_id`.
        """
        async with get_db() as db:
            cred_event = await fetch_one(
                db, "SELECT * FROM credential_events WHERE id = ?", (credential_event_id,)
            )
        if cred_event is None:
            raise CredentialEventNotFoundError("Credential event not found")
        if not cred_event["run_id"]:
            raise CredentialRoutingError(
                "Credential event has no associated run to route from"
            )

        agent = await agent_registry.get(cred_event["agent_id"])
        if agent is None or not agent.fallback_agent_id:
            raise CredentialRoutingError("Agent has no fallback_agent_id configured")

        async with get_db() as db:
            run_row = await fetch_one(
                db, "SELECT task_id FROM agent_runs WHERE id = ?", (cred_event["run_id"],)
            )
        if run_row is None:
            raise CredentialEventNotFoundError("Run for this credential event not found")

        new_run_id, _context_pack_id = await run_manager.route_task(
            run_row["task_id"], agent.fallback_agent_id, cred_event["run_id"]
        )

        async with get_db() as db:
            await execute(
                db,
                "UPDATE credential_events SET resolved_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), credential_event_id),
            )

        return RouteCredentialEventResponse(routed=True, new_run_id=new_run_id)

    async def detect_failure(
        self, agent_id: str, output_line: str
    ) -> CredentialFailureError | None:
        """Delegate failure-signature matching to the agent's own adapter class."""
        agent = await agent_registry.get(agent_id)
        if agent is None:
            return None

        adapter_cls = _ADAPTER_CLASSES.get(agent.adapter)
        if adapter_cls is None:
            return None

        config = AdapterConfig(
            agent_id=agent.id,
            display_name=agent.display_name,
            command=agent.command,
            role=agent.role,
            capabilities=agent.capabilities,
        )
        adapter = adapter_cls(config)
        return adapter.detect_credential_failure(output_line)

    async def on_failure(
        self,
        agent_id: str,
        failure: CredentialFailureError,
        run_id: str,
        task_id: str,
        mission_id: str,
    ) -> dict[str, Any]:
        """Recovery orchestrator: freeze the workspace, record/emit the failure, recover."""
        freeze_result = await workspace_manager.freeze(task_id, reason=failure.failure_type)

        # event_type stored in credential_events must use the CredentialEventType
        # vocabulary ("expired"/"quota_exceeded"/"rate_limited"/...), not the
        # CredentialFailureError.failure_type vocabulary ("subscription_expired"/
        # "auth_invalid"/...) — these are deliberately different names for
        # different things, and storing the latter broke the inbox query that
        # reads this column back through the Pydantic model.
        event_type = _EVENT_TYPE_BY_FAILURE.get(failure.failure_type, "credential.expired")
        credential_event_type = event_type.removeprefix("credential.")

        event_id = str(ulid.new())
        created_at = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO credential_events
                  (id, agent_id, event_type, run_id, mission_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    agent_id,
                    credential_event_type,
                    run_id,
                    mission_id,
                    json.dumps({"message": failure.message}),
                    created_at,
                ),
            )

        new_status = _STATUS_BY_FAILURE_TYPE.get(failure.failure_type, "degraded")
        async with get_db() as db:
            await execute(db, "UPDATE agents SET status = ? WHERE id = ?", (new_status, agent_id))

        payload: dict[str, Any]
        if event_type == "credential.expired":
            payload = {
                "agent_id": agent_id,
                "credential_type": "subscription_session",
                "task_id": task_id,
            }
        elif event_type == "credential.quota_exceeded":
            payload = {"agent_id": agent_id, "reset_at": failure.reset_at}
        else:  # rate_limited
            payload = {
                "agent_id": agent_id,
                "retry_after_ms": failure.retry_after_ms or RATE_LIMIT_RETRY_DELAYS_MS[0],
                "attempt": self._rate_limit_attempts.get(run_id, 0) + 1,
            }

        await event_ledger.emit(
            source="gateway",
            type=event_type,
            payload=payload,
            severity="warning" if event_type == "credential.rate_limited" else "error",
            mission_id=mission_id,
            task_id=task_id,
            run_id=run_id,
        )

        retry_scheduled = False
        retries_exhausted = False
        if failure.failure_type == "rate_limited":
            retry_scheduled = await self._schedule_rate_limit_retry(
                agent_id, run_id, task_id, mission_id
            )
            retries_exhausted = not retry_scheduled

        return {
            "agent_id": agent_id,
            "failure_type": failure.failure_type,
            "credential_event_id": event_id,
            "workspace_state": freeze_result["workspace_state"],
            "wip_commit_sha": freeze_result["wip_commit_sha"],
            "new_agent_status": new_status,
            "retry_scheduled": retry_scheduled,
            "retries_exhausted": retries_exhausted,
        }

    async def _schedule_rate_limit_retry(
        self, agent_id: str, run_id: str, task_id: str, mission_id: str
    ) -> bool:
        """Schedule the next backoff step. Returns False once retries are exhausted."""
        attempt = self._rate_limit_attempts.get(run_id, 0)
        if attempt >= len(RATE_LIMIT_RETRY_DELAYS_MS):
            logger.warning(
                "Rate limit retries exhausted for run %s (agent %s)", run_id, agent_id
            )
            await event_ledger.emit(
                source="gateway",
                type="run.failed",
                payload={"run_id": run_id, "reason": "rate_limited: max retries exceeded"},
                severity="error",
                mission_id=mission_id,
                task_id=task_id,
                run_id=run_id,
            )
            self._rate_limit_attempts.pop(run_id, None)
            return False

        delay_ms = RATE_LIMIT_RETRY_DELAYS_MS[attempt]
        self._rate_limit_attempts[run_id] = attempt + 1
        logger.info(
            "Scheduling rate-limit retry %d/%d for run %s in %dms",
            attempt + 1,
            len(RATE_LIMIT_RETRY_DELAYS_MS),
            run_id,
            delay_ms,
        )
        asyncio.create_task(
            self._retry_after_delay(delay_ms, agent_id, run_id, task_id, mission_id, attempt + 1)
        )
        return True

    async def _retry_after_delay(
        self,
        delay_ms: int,
        agent_id: str,
        run_id: str,
        task_id: str,
        mission_id: str,
        attempt: int,
    ) -> None:
        await asyncio.sleep(delay_ms / 1000)
        # Re-invoking the agent process itself is Phase 3 Day 4+ scope (no
        # spawner exists yet) — this just marks that the backoff window
        # elapsed so the inbox/dashboard can reflect it.
        await event_ledger.emit(
            source="gateway",
            type="credential.rate_limited",
            payload={"agent_id": agent_id, "retry_after_ms": 0, "attempt": attempt},
            mission_id=mission_id,
            task_id=task_id,
            run_id=run_id,
        )
        logger.info(
            "Retry window elapsed for run %s (attempt %d) — agent re-invocation not yet wired",
            run_id,
            attempt,
        )


# Module-level singleton — imported directly by routers, mirroring other services.
credential_manager = CredentialManager()
