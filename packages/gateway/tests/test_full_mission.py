"""
tests/test_full_mission.py — Phase 4 integration scenario 1: full mission loop.

Two agents, parallel execution, one approval, successful merge — exercised
entirely through the real HTTP endpoints (the only direct DB write is
inserting one `action_requests` row, since there's no endpoint that creates
one directly — that's normally `ApprovalEngine.intercept()`'s job, called by
an adapter mid-run, which nothing in this codebase spawns yet).

Deviation from the literal scenario: the planner here returns two
*independent* tasks (no `depends_on` between them) rather than task_2
depending on task_1. A real dependency would mean task_2 never becomes
`ready` until task_1 reaches `completed` (see `TaskService.list_ready`), so
asserting "both tasks running" after a single `/missions/{id}/run` call
would be impossible by design, not a bug. Independent tasks are what
actually let two agents run in parallel, which is the scenario's own premise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from agentos_gateway.database import execute, fetch_one, get_db

from .conftest import patch_planner


@pytest.mark.asyncio
async def test_full_mission_loop(
    client: AsyncClient, created_mission: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Register two agents
    backend = (
        await client.post(
            "/agents",
            json={
                "display_name": "Backend Agent",
                "adapter": "claude-code",
                "command": "claude",
                "role": "backend",
            },
        )
    ).json()
    frontend = (
        await client.post(
            "/agents",
            json={
                "display_name": "Frontend Agent",
                "adapter": "codex",
                "command": "codex",
                "role": "frontend",
            },
        )
    ).json()

    # 2. Plan the mission with a deterministic, mocked planner response
    mission_id = created_mission["id"]
    plan_json = f"""{{
      "tasks": [
        {{"key": "t1", "title": "Build backend API", "assigned_agent_id": "{backend['id']}",
          "depends_on": [], "risk_level": "medium", "estimated_files": ["src/api.ts"]}},
        {{"key": "t2", "title": "Build frontend UI", "assigned_agent_id": "{frontend['id']}",
          "depends_on": [], "risk_level": "low", "estimated_files": ["src/ui.ts"]}}
      ]
    }}"""
    patch_planner(monkeypatch, plan_json)

    plan_resp = await client.post(f"/missions/{mission_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert len(plan["tasks"]) == 2
    task_1_id = plan["tasks"][0]["id"]
    task_2_id = plan["tasks"][1]["id"]

    # 3. Run the mission — both tasks are independent, so both should start
    run_resp = await client.post(f"/missions/{mission_id}/run", json={"parallel": True})
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert set(run_body["started_tasks"]) == {task_1_id, task_2_id}
    assert run_body["skipped_tasks"] == []

    task_1 = (await client.get(f"/tasks/{task_1_id}")).json()
    task_2 = (await client.get(f"/tasks/{task_2_id}")).json()
    assert task_1["status"] == "running"
    assert task_2["status"] == "running"
    assert task_1["worktree_path"] is not None
    assert task_2["worktree_path"] is not None
    assert Path(task_1["worktree_path"]).exists()
    assert Path(task_2["worktree_path"]).exists()
    assert task_1["worktree_path"] != task_2["worktree_path"]

    # 4. Simulate an agent requesting a medium-risk action mid-run, then approve it
    async with get_db() as db:
        run_row = await fetch_one(
            db, "SELECT id FROM agent_runs WHERE task_id = ?", (task_1_id,)
        )
    assert run_row is not None
    run_id = run_row["id"]

    action_request_id = "test-action-request-1"
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO action_requests
              (id, run_id, action_type, command_or_tool, risk_score, risk_level,
               explanation, evidence, status, created_at)
            VALUES (?, ?, 'shell_cmd', 'npm install left-pad', 45, 'medium',
                    'installing a dependency', '{}', 'pending', '2026-01-01T00:00:00Z')
            """,
            (action_request_id, run_id),
        )

    inbox_before = (await client.get("/inbox")).json()
    assert any(ar["id"] == action_request_id for ar in inbox_before["action_requests"])

    approve_resp = await client.post(
        f"/inbox/approve/{action_request_id}", json={"scope": "once"}
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["approved"] is True

    async with get_db() as db:
        ar_row = await fetch_one(
            db, "SELECT status FROM action_requests WHERE id = ?", (action_request_id,)
        )
    assert ar_row is not None
    assert ar_row["status"] == "approved"

    # 5. Diff task_1's (empty) worktree against base — should succeed, not error
    diff_resp = await client.get(f"/tasks/{task_1_id}/diff")
    assert diff_resp.status_code == 200
    diff_body = diff_resp.json()
    assert diff_body["task_id"] == task_1_id
    assert "diff_text" in diff_body
    assert "files_changed" in diff_body

    # 6. Merge task_1
    merge_resp = await client.post(f"/tasks/{task_1_id}/merge")
    assert merge_resp.status_code == 200
    merge_body = merge_resp.json()
    assert merge_body["merged_sha"]
    assert merge_body["worktree_removed"] is True
    assert not Path(task_1["worktree_path"]).exists()

    task_1_after = (await client.get(f"/tasks/{task_1_id}")).json()
    assert task_1_after["status"] == "completed"

    # 7. Mission status: one completed, one still running
    status_resp = await client.get(f"/missions/{mission_id}/status")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    statuses_by_id = {t["id"]: t["status"] for t in status_body["tasks"]}
    assert statuses_by_id[task_1_id] == "completed"
    assert statuses_by_id[task_2_id] == "running"
