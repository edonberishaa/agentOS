"""routers/credentials.py — Credential management (Phase 3 implementation)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import RotateCredentialRequest, RotateCredentialResponse
from ..services.credential_manager import CredentialManagerError, credential_manager

router = APIRouter()


@router.post("/rotate/{agent_id}", response_model=RotateCredentialResponse)
async def rotate_credential(
    agent_id: str, body: RotateCredentialRequest
) -> RotateCredentialResponse:
    try:
        return await credential_manager.rotate(agent_id, body.credential_value)
    except CredentialManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
