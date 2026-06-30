"""
tests/test_rollback.py — Phase 4 integration scenario 4: agent produces bad
output, user rolls back cleanly.

The "bad output" is simulated by writing a file directly into the task's
worktree on disk — there's no endpoint that does this (an agent process
would write it, but nothing spawns one yet). This is the documented
exception in the scenario brief.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from agentos_gateway.database import fetch_all, get_db

from .conftest import patch_planner


@pytest.mark.asyncio
async def test_rollback_discards_bad_work(
    client: AsyncClient, created_mission: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Register, plan, run
    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    mission_id = created_mission["id"]
    plan_json = f"""{{
      "tasks": [
        {{"key": "t1", "title": "Risky change", "assigned_agent_id": "{agent['id']}",
          "depends_on": [], "risk_level": "medium", "estimated_files": ["src/risky.ts"]}}
      ]
    }}"""
    patch_planner(monkeypatch, plan_json)
    plan = (await client.post(f"/missions/{mission_id}/plan")).json()
    task_id = plan["tasks"][0]["id"]

    run_resp = await client.post(f"/missions/{mission_id}/run", json={"parallel": True})
    assert run_resp.json()["started_tasks"] == [task_id]

    task = (await client.get(f"/tasks/{task_id}")).json()
    worktree_path = Path(task["worktree_path"])
    assert worktree_path.exists()

    # 2. Simulate bad agent work: a stray file written directly into the worktree
    bad_file = worktree_path / "broken_output.txt"
    bad_file.write_text("this should never have been written\n")
    assert bad_file.exists()

    # 3. Roll back
    rollback_resp = await client.post(f"/tasks/{task_id}/rollback")
    assert rollback_resp.status_code == 200
    rollback_body = rollback_resp.json()
    assert rollback_body["rolled_back_to_sha"]
    assert rollback_body["worktree_removed"] is True

    # 4. Verify: task failed, worktree gone, event recorded, bad file gone
    task_after = (await client.get(f"/tasks/{task_id}")).json()
    assert task_after["status"] == "failed"
    assert not worktree_path.exists()
    assert not bad_file.exists()

    async with get_db() as db:
        rollback_events = await fetch_all(
            db,
            "SELECT * FROM events WHERE type = 'workspace.rolled_back' AND task_id = ?",
            (task_id,),
        )
    assert len(rollback_events) == 1
