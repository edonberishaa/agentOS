"""
services/agent_registry.py — AgentRegistry: agent CRUD and health probing.

Owns all reads/writes to the `agents` table; routers call this service instead
of touching the database directly. Health probing here is the Phase 3 Day 1
"happy path" only: it confirms the agent's CLI command resolves on PATH and
reports that as the credential status. Real credential validation (API key /
subscription probing, expiry detection, automatic recovery) is Phase 3 Day 4
scope and is not implemented here.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from typing import Any

import ulid

from ..database import execute, fetch_all, fetch_one, get_db, parse_json_fields
from ..events import event_ledger
from ..models import (
    AgentHealthResponse,
    AgentResponse,
    RegisterAgentRequest,
    ValidateAgentResponse,
)

logger = logging.getLogger(__name__)

_JSON_FIELDS = ["capabilities"]


def _row_to_agent(record: dict[str, Any]) -> AgentResponse:
    return AgentResponse(**parse_json_fields(record, _JSON_FIELDS))


class AgentRegistry:
    """CRUD and health probing for registered agents."""

    async def list_all(self) -> list[AgentResponse]:
        async with get_db() as db:
            rows = await fetch_all(db, "SELECT * FROM agents ORDER BY created_at")
        return [_row_to_agent(row) for row in rows]

    async def get(self, agent_id: str) -> AgentResponse | None:
        async with get_db() as db:
            row = await fetch_one(db, "SELECT * FROM agents WHERE id = ?", (agent_id,))
        return _row_to_agent(row) if row else None

    async def register(self, body: RegisterAgentRequest) -> AgentResponse:
        agent_id = str(ulid.new())
        created_at = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO agents
                  (id, display_name, adapter, command, role, capabilities,
                   fallback_agent_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?)
                """,
                (
                    agent_id,
                    body.display_name,
                    body.adapter,
                    body.command,
                    body.role,
                    json.dumps(body.capabilities),
                    body.fallback_agent_id,
                    created_at,
                ),
            )

        await event_ledger.emit(
            source="gateway",
            type="agent.registered",
            payload={
                "agent_id": agent_id,
                "display_name": body.display_name,
                "adapter": body.adapter,
            },
        )

        agent = await self.get(agent_id)
        assert agent is not None
        return agent

    async def remove(self, agent_id: str) -> bool:
        existing = await self.get(agent_id)
        if existing is None:
            return False

        async with get_db() as db:
            await execute(db, "DELETE FROM agents WHERE id = ?", (agent_id,))

        await event_ledger.emit(
            source="gateway",
            type="agent.removed",
            payload={"agent_id": agent_id},
        )
        return True

    async def check_health(self, agent_id: str) -> AgentHealthResponse | None:
        agent = await self.get(agent_id)
        if agent is None:
            return None

        checked_at = datetime.now(UTC).isoformat()
        command_found = shutil.which(agent.command) is not None

        async with get_db() as db:
            await execute(
                db,
                "UPDATE agents SET last_health_check = ?, status = ? WHERE id = ?",
                (checked_at, "idle" if command_found else "error", agent_id),
            )

        await event_ledger.emit(
            source="gateway",
            type="agent.idle" if command_found else "agent.degraded",
            payload=(
                {"agent_id": agent_id}
                if command_found
                else {"agent_id": agent_id, "reason": "command not found on PATH"}
            ),
        )

        return AgentHealthResponse(
            agent_id=agent_id,
            status="healthy" if command_found else "unknown",
            credential_type="local_auth",
            last_validated=checked_at if command_found else None,
            quota_remaining=None,
            expiry_hint=None,
            auto_recovery_eligible=False,
            fallback_agent_id=agent.fallback_agent_id,
        )

    async def validate(self, agent_id: str) -> ValidateAgentResponse | None:
        agent = await self.get(agent_id)
        if agent is None:
            return None

        checked_at = datetime.now(UTC).isoformat()
        command_found = shutil.which(agent.command) is not None

        await event_ledger.emit(
            source="gateway",
            type="credential.validated" if command_found else "agent.degraded",
            payload=(
                {"agent_id": agent_id}
                if command_found
                else {"agent_id": agent_id, "reason": "command not found on PATH"}
            ),
        )

        return ValidateAgentResponse(
            valid=command_found,
            details=(
                f"Command '{agent.command}' found on PATH"
                if command_found
                else f"Command '{agent.command}' not found on PATH"
            ),
            checked_at=checked_at,
        )


# Module-level singleton — imported directly by routers, mirroring event_ledger.
agent_registry = AgentRegistry()
