"""
tests/test_credential_failure.py — Phase 4 integration scenario 2: credential
failure mid-run, with fallback routing.

`credential_manager.on_failure()` and `run_manager.save_message()` are called
directly rather than through an HTTP endpoint, because neither has one — they
exist to be called by an adapter mid-run (failure detection) or by an agent
process (message logging), and no process spawner exists yet to drive either
through a real run. This is the documented exception in the scenario brief.

Deviation from the literal scenario: `POST /inbox/route/{credential_event_id}`
takes no request body — `routers/inbox.py` always routes to the failed
agent's configured `fallback_agent_id`, there's no way to specify a different
target. So "with to_agent_id=fallback" is satisfied by registering the
primary agent with `fallback_agent_id` pointed at the fallback agent up
front, not by passing it on the route call.

Also verified here, not just asserted as already-fixed: the two Phase 3 bugs
(workspace_state never persisted by `freeze()`, wrong event-type vocabulary
in `credential_events`) stay fixed — this test would fail loudly if either
regressed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from agentos_gateway.adapters.base import CredentialFailureError
from agentos_gateway.database import fetch_all, fetch_one, get_db
from agentos_gateway.services.credential_manager import credential_manager
from agentos_gateway.services.run_manager import run_manager

from .conftest import patch_planner


@pytest.mark.asyncio
async def test_credential_failure_routes_to_fallback(
    client: AsyncClient, created_mission: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Register fallback first (so its id exists), then primary pointing at it
    fallback = (
        await client.post(
            "/agents",
            json={
                "display_name": "Fallback Agent",
                "adapter": "codex",
                "command": "codex",
                "role": "backend",
            },
        )
    ).json()
    primary = (
        await client.post(
            "/agents",
            json={
                "display_name": "Primary Agent",
                "adapter": "claude-code",
                "command": "claude",
                "role": "backend",
                "fallback_agent_id": fallback["id"],
            },
        )
    ).json()
    assert primary["fallback_agent_id"] == fallback["id"]

    # 2. Plan with one task assigned to the primary agent
    mission_id = created_mission["id"]
    plan_json = f"""{{
      "tasks": [
        {{"key": "t1", "title": "Implement login endpoint",
          "assigned_agent_id": "{primary['id']}", "depends_on": [],
          "risk_level": "medium", "estimated_files": ["src/auth.ts"]}}
      ]
    }}"""
    patch_planner(monkeypatch, plan_json)
    plan = (await client.post(f"/missions/{mission_id}/plan")).json()
    task_id = plan["tasks"][0]["id"]

    # 3. Run
    run_resp = await client.post(f"/missions/{mission_id}/run", json={"parallel": True})
    assert run_resp.json()["started_tasks"] == [task_id]

    task = (await client.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "running"
    primary_worktree = task["worktree_path"]
    assert Path(primary_worktree).exists()

    async with get_db() as db:
        run_row = await fetch_one(db, "SELECT id FROM agent_runs WHERE task_id = ?", (task_id,))
    assert run_row is not None
    run_id = run_row["id"]

    # Log a prior message before the failure, so the handoff pack has
    # session memory to carry forward.
    await run_manager.save_message(
        run_id,
        primary["id"],
        task_id,
        "assistant",
        "I was midway through implementing the login endpoint.",
    )

    # 4. Simulate a credential failure mid-run
    failure = CredentialFailureError(
        agent_id=primary["id"], failure_type="subscription_expired", message="auth required"
    )
    result = await credential_manager.on_failure(
        primary["id"], failure, run_id, task_id, mission_id
    )
    assert result["new_agent_status"] == "expired"

    # 5. Verify the failure was handled correctly
    task_after_failure = (await client.get(f"/tasks/{task_id}")).json()
    assert task_after_failure["status"] == "paused"

    primary_after_failure = (await client.get(f"/agents/{primary['id']}")).json()
    assert primary_after_failure["status"] == "expired"

    async with get_db() as db:
        cred_events = await fetch_all(
            db, "SELECT * FROM credential_events WHERE agent_id = ?", (primary["id"],)
        )
    assert len(cred_events) == 1
    assert cred_events[0]["event_type"] == "expired"  # not "subscription_expired" — bug fix check

    async with get_db() as db:
        run_after_failure = await fetch_one(
            db, "SELECT workspace_state FROM agent_runs WHERE id = ?", (run_id,)
        )
    assert run_after_failure is not None
    assert run_after_failure["workspace_state"] is not None  # bug fix check

    inbox = (await client.get("/inbox")).json()
    assert len(inbox["credential_events"]) == 1
    cred_event_in_inbox = inbox["credential_events"][0]
    assert cred_event_in_inbox["event_type"] == "expired"
    assert cred_event_in_inbox["branch_state"] == run_after_failure["workspace_state"]
    credential_event_id = cred_event_in_inbox["id"]

    # 6. Route to the fallback agent
    route_resp = await client.post(f"/inbox/route/{credential_event_id}")
    assert route_resp.status_code == 200
    route_body = route_resp.json()
    assert route_body["routed"] is True
    new_run_id = route_body["new_run_id"]
    assert new_run_id and new_run_id != run_id

    # 7. Verify the handoff
    async with get_db() as db:
        new_run_row = await fetch_one(
            db, "SELECT * FROM agent_runs WHERE id = ?", (new_run_id,)
        )
    assert new_run_row is not None
    assert new_run_row["agent_id"] == fallback["id"]

    task_after_route = (await client.get(f"/tasks/{task_id}")).json()
    assert task_after_route["assigned_agent_id"] == fallback["id"]
    assert task_after_route["worktree_path"] is not None
    assert Path(task_after_route["worktree_path"]).exists()

    # context_packs row exists for the handoff (metadata only — see module
    # docstring for why prior-message *content* is checked separately below)
    async with get_db() as db:
        packs = await fetch_all(db, "SELECT * FROM context_packs WHERE task_id = ?", (task_id,))
    assert len(packs) == 1
    assert packs[0]["agent_id"] == fallback["id"]

    # The actual handoff *content* (with the prior message) is never persisted
    # — route_task() discards it after building the pack (see module docstring
    # and this session's final report). Verify the underlying mechanism
    # directly instead of asserting against a DB column that structurally
    # can't hold it.
    _pack_id, content = await run_manager.build_handoff_pack(task_id, run_id, fallback["id"])
    assert "I was midway through implementing the login endpoint." in content
