"""routers/missions.py — Mission lifecycle endpoints (Phase 3 implementation)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..models import (
    CreateMissionRequest,
    MissionPlanResponse,
    MissionResponse,
    MissionStatus,
    MissionStatusResponse,
    RunMissionRequest,
)
from ..services.mission_service import (
    MissionAlreadyTerminalError,
    MissionNotFoundError,
    MissionPlanningError,
    mission_service,
)

router = APIRouter()

# Module-level singleton — avoids ruff B008 (Literal type aliases aren't
# recognized as immutable annotations, unlike `str | None`).
_STATUS_QUERY = Query(None)


@router.post("", response_model=MissionResponse, status_code=201)
async def create_mission(body: CreateMissionRequest) -> MissionResponse:
    return await mission_service.create(body)


@router.get("", response_model=list[MissionResponse])
async def list_missions(
    status: MissionStatus | None = _STATUS_QUERY,
) -> list[MissionResponse]:
    return await mission_service.list_all(status=status)


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission(mission_id: str) -> MissionResponse:
    mission = await mission_service.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("/{mission_id}/plan", response_model=MissionPlanResponse)
async def plan_mission(mission_id: str) -> MissionPlanResponse:
    try:
        plan = await mission_service.plan(mission_id)
    except MissionPlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return plan


@router.post("/{mission_id}/run")
async def run_mission(mission_id: str, body: RunMissionRequest) -> dict[str, Any]:
    try:
        return await mission_service.run_mission(mission_id, body)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MissionAlreadyTerminalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{mission_id}/pause")
async def pause_mission(mission_id: str) -> dict[str, Any]:
    # TODO Phase 3 Day 4+: depends on agent runs existing to pause — call
    # WorkspaceManager.freeze() per active task once real runs exist.
    raise NotImplementedError("Phase 3 Day 4+")


@router.get("/{mission_id}/status", response_model=MissionStatusResponse)
async def mission_status(mission_id: str) -> MissionStatusResponse:
    status = await mission_service.get_status(mission_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return status
