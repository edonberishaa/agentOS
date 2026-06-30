"""routers/tasks.py — Task operations (Phase 3 implementation)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import (
    DiffResponse,
    MergeResponse,
    RollbackResponse,
    RouteTaskRequest,
    RouteTaskResponse,
    TaskResponse,
)
from ..services.run_manager import RunManagerError, run_manager
from ..services.task_service import task_service
from ..services.workspace_manager import (
    WorkspaceError,
    WorkspaceNotFoundError,
    get_latest_run_id,
    workspace_manager,
)

router = APIRouter()


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    task = await task_service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/route", response_model=RouteTaskResponse)
async def route_task(task_id: str, body: RouteTaskRequest) -> RouteTaskResponse:
    run_id = await get_latest_run_id(task_id)
    if run_id is None:
        raise HTTPException(
            status_code=404, detail="No run found for this task — nothing to hand off"
        )
    try:
        new_run_id, context_pack_id = await run_manager.route_task(
            task_id, body.to_agent_id, run_id
        )
    except RunManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RouteTaskResponse(
        task_id=task_id, new_run_id=new_run_id, handoff_context_pack_id=context_pack_id
    )


@router.get("/{task_id}/diff", response_model=DiffResponse)
async def get_diff(task_id: str) -> DiffResponse:
    try:
        return await workspace_manager.get_diff(task_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/merge", response_model=MergeResponse)
async def merge_task(task_id: str) -> MergeResponse:
    try:
        return await workspace_manager.merge(task_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/rollback", response_model=RollbackResponse)
async def rollback_task(task_id: str) -> RollbackResponse:
    run_id = await get_latest_run_id(task_id)
    if run_id is None:
        raise HTTPException(
            status_code=404, detail="No run found for this task — nothing to roll back"
        )
    try:
        return await workspace_manager.rollback(task_id, run_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
