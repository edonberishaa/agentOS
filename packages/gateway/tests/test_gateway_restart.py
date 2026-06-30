"""
tests/test_gateway_restart.py — Phase 4 integration scenario 5: gateway
restart mid-mission.

`lifespan()` is an `@asynccontextmanager` generator — you can't call its
shutdown half without first entering it, so "simulate a crash" here means:
enter once (boot — redundant with what the `db`/`client` fixtures already
did by calling `init_db()` directly, but idempotent and harmless), exit
(shutdown), then enter a *second*, fresh context (restart). The `db` fixture
deliberately points the SQLite file at the same path `lifespan()` itself
computes (`resolve_agentos_dir() / "agent_os.db"`), so both lifespan
invocations and the test's own `client` calls are all reading/writing the
exact same file — this is what makes "state recovered from SQLite" a real
assertion instead of an accident of fixture ordering.

Orphan-run detection (flagging a `running` task whose process is gone after
a crash) is NOT implemented anywhere in this codebase — `GET /inbox` has no
notion of it (see `routers/inbox.py`). Per the scenario brief, that's
documented as a TODO below rather than asserted as if it existed.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from agentos_gateway.main import app, lifespan

from .conftest import patch_planner


@pytest.mark.asyncio
async def test_state_survives_restart(
    client: AsyncClient, created_mission: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Register, plan, run — mission and task both end up `running`
    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    mission_id = created_mission["id"]
    plan_json = f"""{{
      "tasks": [
        {{"key": "t1", "title": "Long-running task", "assigned_agent_id": "{agent['id']}",
          "depends_on": [], "risk_level": "low", "estimated_files": ["src/x.ts"]}}
      ]
    }}"""
    patch_planner(monkeypatch, plan_json)
    plan = (await client.post(f"/missions/{mission_id}/plan")).json()
    task_id = plan["tasks"][0]["id"]

    await client.post(f"/missions/{mission_id}/run", json={"parallel": True})

    mission_before = (await client.get(f"/missions/{mission_id}")).json()
    task_before = (await client.get(f"/tasks/{task_id}")).json()
    assert mission_before["status"] == "running"
    assert task_before["status"] == "running"

    # 2. Simulate crash: drive the real lifespan's shutdown half
    boot_ctx = lifespan(app)
    await boot_ctx.__aenter__()
    await boot_ctx.__aexit__(None, None, None)

    # 3. Restart: a fresh lifespan context, same DB file
    restart_ctx = lifespan(app)
    await restart_ctx.__aenter__()
    try:
        # 4. State recovered from SQLite — same client, same underlying DB
        mission_after = (await client.get(f"/missions/{mission_id}")).json()
        task_after = (await client.get(f"/tasks/{task_id}")).json()
        assert mission_after["status"] == "running"
        assert task_after["status"] == "running"
        assert task_after["id"] == task_before["id"]
        assert task_after["worktree_path"] == task_before["worktree_path"]

        # 5. TODO Phase 4: orphan detection isn't implemented anywhere yet —
        # GET /inbox has no concept of "running task, no live process" (see
        # routers/inbox.py: action_requests/credential_events/conflicts only).
        # The only thing we can assert today is that the task is still
        # queryable with its pre-crash status, which is what actually matters
        # for recovery — the orphan *signal* is future work.
        inbox = (await client.get("/inbox")).json()
        assert "action_requests" in inbox  # endpoint itself survives the restart
    finally:
        await restart_ctx.__aexit__(None, None, None)
