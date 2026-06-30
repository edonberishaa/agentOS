"""routers/agents.py — Agent registry endpoints (Phase 3 implementation)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import AgentHealthResponse, AgentResponse, RegisterAgentRequest, ValidateAgentResponse
from ..services.agent_registry import agent_registry

router = APIRouter()

@router.get("", response_model=list[AgentResponse])
async def list_agents() -> list[AgentResponse]:
    return await agent_registry.list_all()

@router.post("", response_model=AgentResponse, status_code=201)
async def register_agent(body: RegisterAgentRequest) -> AgentResponse:
    return await agent_registry.register(body)

@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str) -> AgentResponse:
    agent = await agent_registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@router.get("/{agent_id}/health", response_model=AgentHealthResponse)
async def get_agent_health(agent_id: str) -> AgentHealthResponse:
    health = await agent_registry.check_health(agent_id)
    if health is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return health

@router.post("/{agent_id}/validate", response_model=ValidateAgentResponse)
async def validate_agent(agent_id: str) -> ValidateAgentResponse:
    result = await agent_registry.validate(agent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return result

@router.delete("/{agent_id}")
async def remove_agent(agent_id: str) -> dict[str, bool]:
    removed = await agent_registry.remove(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"removed": True}
