"""
tests/test_performance.py — Phase 4 performance baseline.

Each assertion measures wall-clock time around the real operation (no
mocking of the thing being timed) and prints the measured value so a human
running the suite sees the actual number, not just pass/fail against the
threshold.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agentos_gateway.database import get_db
from agentos_gateway.events import event_ledger
from agentos_gateway.main import app, lifespan
from agentos_gateway.services.run_manager import run_manager
from agentos_gateway.services.task_service import task_service
from agentos_gateway.sse import sse_manager


@pytest.mark.asyncio
async def test_gateway_startup_under_500ms(db: Path) -> None:
    ctx = lifespan(app)
    start = time.perf_counter()
    await ctx.__aenter__()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert resp.status_code == 200
        print(f"\n[perf] gateway startup -> /health: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 500
    finally:
        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_sse_event_delivery_under_100ms(client: AsyncClient) -> None:
    subscriber = sse_manager.subscribe()
    try:
        start = time.perf_counter()
        await event_ledger.emit(
            source="gateway", type="mission.created", payload={"mission_id": "x"}
        )
        event = await asyncio.wait_for(subscriber.queue.get(), timeout=1.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert event is not None
        print(f"\n[perf] SSE event delivery: {elapsed_ms:.2f}ms")
        assert elapsed_ms < 100
    finally:
        sse_manager.unsubscribe(subscriber)


@pytest.mark.asyncio
async def test_sqlite_inbox_query_under_50ms(client: AsyncClient) -> None:
    rows = [
        (f"perf-event-{i}", "2026-01-01T00:00:00Z", "gateway", "mission.created", "{}", "info")
        for i in range(1000)
    ]
    async with get_db() as db:
        await db.executemany(
            "INSERT INTO events (id, timestamp, source, type, payload, severity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()

    start = time.perf_counter()
    resp = await client.get("/inbox")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert resp.status_code == 200
    print(f"\n[perf] GET /inbox with 1000 events in the table: {elapsed_ms:.2f}ms")
    assert elapsed_ms < 50


@pytest.mark.asyncio
async def test_context_pack_generation_under_3s(
    client: AsyncClient, created_mission: dict[str, Any]
) -> None:
    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    task = await task_service.create(
        task_id="perf-task-1",
        mission_id=created_mission["id"],
        title="Perf test task",
        assigned_agent_id=agent["id"],
        depends_on=[],
    )
    run_id = await run_manager.create_run(task.id, agent["id"])

    for i in range(50):
        await run_manager.save_message(
            run_id, agent["id"], task.id, "assistant", f"Message number {i} with some content."
        )

    start = time.perf_counter()
    _pack_id, content = await run_manager.build_handoff_pack(task.id, run_id, agent["id"])
    elapsed_s = time.perf_counter() - start
    assert content
    print(f"\n[perf] build_handoff_pack with 50 prior messages: {elapsed_s:.3f}s")
    assert elapsed_s < 3.0
