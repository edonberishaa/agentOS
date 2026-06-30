"""routers/inbox.py — Approval inbox (Phase 3 implementation)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import (
    ApproveRequest,
    ApproveResponse,
    DenyResponse,
    InboxResponse,
    RouteCredentialEventResponse,
)
from ..services.approval_engine import ApprovalEngineError, approval_engine
from ..services.conflict_detector import conflict_detector
from ..services.credential_manager import (
    CredentialEventNotFoundError,
    CredentialRoutingError,
    credential_manager,
)
from ..services.run_manager import RunManagerError
from ..services.workspace_manager import WorkspaceError

router = APIRouter()


@router.get("", response_model=InboxResponse)
async def get_inbox() -> InboxResponse:
    return InboxResponse(
        action_requests=await approval_engine.list_pending(),
        credential_events=await credential_manager.list_unresolved(),
        conflicts=await conflict_detector.list_active(),
    )


@router.post("/approve/{action_request_id}", response_model=ApproveResponse)
async def approve_action(action_request_id: str, body: ApproveRequest) -> ApproveResponse:
    try:
        return await approval_engine.approve(action_request_id, scope=body.scope, note=body.note)
    except ApprovalEngineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deny/{action_request_id}", response_model=DenyResponse)
async def deny_action(action_request_id: str) -> DenyResponse:
    try:
        return await approval_engine.deny(action_request_id)
    except ApprovalEngineError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/route/{credential_event_id}", response_model=RouteCredentialEventResponse)
async def route_credential_event(credential_event_id: str) -> RouteCredentialEventResponse:
    try:
        return await credential_manager.route_credential_event(credential_event_id)
    except CredentialEventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CredentialRoutingError, RunManagerError, WorkspaceError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
