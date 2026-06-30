"""routers/context.py — Context vault (Phase 3 implementation)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..models import BuildContextPackRequest, ContextListResponse, ContextPackResponse
from ..services.run_manager import run_manager

router = APIRouter()

@router.get("", response_model=ContextListResponse)
async def list_context() -> ContextListResponse:
    return ContextListResponse(documents=[])

@router.post("/pack", response_model=ContextPackResponse)
async def build_context_pack(body: BuildContextPackRequest) -> ContextPackResponse:
    raise NotImplementedError("Phase 3")

@router.get("/pack/{pack_id}")
async def get_context_pack(pack_id: str) -> dict[str, Any]:
    """Full stored context pack, including the persisted handoff content."""
    row = await run_manager.get_pack(pack_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Context pack {pack_id} not found")
    return row
