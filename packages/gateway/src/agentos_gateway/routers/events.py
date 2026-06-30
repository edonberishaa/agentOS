"""routers/events.py — Event stream and history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..events import event_ledger
from ..models import EventListResponse
from ..sse import sse_manager

router = APIRouter()


@router.get("/stream")
async def stream_events(
    mission_id: str | None = Query(None),
    run_id: str | None = Query(None),
    agent_id: str | None = Query(None),
) -> StreamingResponse:
    """
    SSE stream of live events. Connect from dashboard with EventSource.
    Filters by mission_id, run_id, and/or agent_id if provided.
    Sends a heartbeat comment every 15 seconds to keep connection alive.
    """
    subscriber = sse_manager.subscribe(
        mission_id=mission_id,
        run_id=run_id,
        agent_id=agent_id,
    )

    return StreamingResponse(
        sse_manager.event_stream(subscriber),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.get("", response_model=EventListResponse)
async def list_events(
    mission_id: str | None = Query(None),
    run_id: str | None = Query(None),
    agent_id: str | None = Query(None),
    event_type: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    before: str | None = Query(None),
) -> EventListResponse:
    """Paginated event history with optional filters."""
    events, total = await event_ledger.get_events(
        mission_id=mission_id,
        run_id=run_id,
        agent_id=agent_id,
        event_type=event_type,
        severity=severity,
        limit=limit,
        before=before,
    )

    return EventListResponse(
        events=events,  # type: ignore[arg-type]
        total=total,
        has_more=total > limit,
    )
