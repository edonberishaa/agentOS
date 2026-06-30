"""
tests/test_conflict_detection.py — Phase 4 integration scenario 3: two tasks
targeting the same file get a conflict raised, surfaced in the inbox, and
resolved.

`files_owned` and task `status` are set directly via SQL — there is no
endpoint for either (tasks are only ever created by the planner, and status
transitions happen as side effects of `/run`/`/merge`/`/rollback`, none of
which fit "two tasks independently claim the same file"). This is the
documented exception in the scenario brief.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from agentos_gateway.database import execute, get_db
from agentos_gateway.services.conflict_detector import conflict_detector
from agentos_gateway.services.task_service import task_service


@pytest.mark.asyncio
async def test_file_overlap_conflict_lifecycle(
    client: AsyncClient, created_mission: dict[str, Any]
) -> None:
    # 1. Register two agents
    agent_a = (
        await client.post(
            "/agents",
            json={"display_name": "Agent A", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    agent_b = (
        await client.post(
            "/agents",
            json={"display_name": "Agent B", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()

    # 2. Two tasks in the same mission, both owning src/shared.ts
    mission_id = created_mission["id"]
    task_a = await task_service.create(
        task_id="conflict-task-a",
        mission_id=mission_id,
        title="Task A",
        assigned_agent_id=agent_a["id"],
        depends_on=[],
        files_owned=["src/shared.ts", "src/a.ts"],
    )
    task_b = await task_service.create(
        task_id="conflict-task-b",
        mission_id=mission_id,
        title="Task B",
        assigned_agent_id=agent_b["id"],
        depends_on=[],
        files_owned=["src/shared.ts", "src/b.ts"],
    )

    # 3. Both running
    async with get_db() as db:
        await execute(
            db,
            "UPDATE tasks SET status = 'running' WHERE id IN (?, ?)",
            (task_a.id, task_b.id),
        )

    # 4 & 5. Overlap detected
    overlap = await conflict_detector.check_file_overlap(task_a.id)
    assert overlap == ["src/shared.ts"]

    # A task with no shared files reports no overlap
    task_c = await task_service.create(
        task_id="conflict-task-c",
        mission_id=mission_id,
        title="Task C",
        assigned_agent_id=agent_a["id"],
        depends_on=[],
        files_owned=["src/unique.ts"],
    )
    async with get_db() as db:
        await execute(db, "UPDATE tasks SET status = 'running' WHERE id = ?", (task_c.id,))
    assert await conflict_detector.check_file_overlap(task_c.id) == []

    # 6. Record the conflict
    conflict_id = await conflict_detector.record_conflict(
        "file_overlap",
        [agent_a["id"], agent_b["id"]],
        overlap,
        task_a.id,
        mission_id,
    )
    assert conflict_id

    # 7. Inbox shows it
    inbox = (await client.get("/inbox")).json()
    matching = [c for c in inbox["conflicts"] if c["id"] == conflict_id]
    assert len(matching) == 1
    assert matching[0]["type"] == "file_overlap"
    assert set(matching[0]["agents_involved"]) == {agent_a["id"], agent_b["id"]}
    assert matching[0]["files_affected"] == ["src/shared.ts"]

    # 8. Resolve it
    await conflict_detector.resolve_conflict(conflict_id)

    # 9. Inbox no longer shows it
    inbox_after = (await client.get("/inbox")).json()
    assert not any(c["id"] == conflict_id for c in inbox_after["conflicts"])


@pytest.mark.asyncio
async def test_files_owned_parsed_from_json_string(
    client: AsyncClient, created_mission: dict[str, Any]
) -> None:
    """files_owned is stored as a JSON TEXT column — check_file_overlap must
    parse it back, not just handle the case where TaskResponse already did."""
    agent_a = (
        await client.post(
            "/agents",
            json={"display_name": "Agent A", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    mission_id = created_mission["id"]

    task_a = await task_service.create(
        task_id="json-task-a",
        mission_id=mission_id,
        title="A",
        assigned_agent_id=agent_a["id"],
        depends_on=[],
        files_owned=["src/x.ts"],
    )
    task_b = await task_service.create(
        task_id="json-task-b",
        mission_id=mission_id,
        title="B",
        assigned_agent_id=agent_a["id"],
        depends_on=[],
        files_owned=["src/x.ts"],
    )
    async with get_db() as db:
        # files_owned column really is a raw JSON string on disk
        await execute(
            db,
            "UPDATE tasks SET status = 'running', files_owned = ? WHERE id IN (?, ?)",
            (json.dumps(["src/x.ts"]), task_a.id, task_b.id),
        )

    overlap = await conflict_detector.check_file_overlap(task_a.id)
    assert overlap == ["src/x.ts"]
