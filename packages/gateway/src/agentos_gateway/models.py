"""
models.py — Pydantic v2 request/response models for all gateway endpoints.
These mirror the TypeScript types in packages/shared/src/types.ts exactly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ============================================================
# ENUMS (as Literal types — Pydantic validates them automatically)
# ============================================================

AgentAdapter = Literal["claude-code", "codex", "mock"]
WorkspaceStrategy = Literal["git_worktree"]
AgentStatus = Literal["idle", "busy", "degraded", "expired", "error"]
CredentialStatus = Literal["healthy", "warning", "expired", "unknown"]
MissionStatus = Literal["planning", "running", "paused", "completed", "failed"]
TaskStatus = Literal["pending", "running", "paused", "completed", "failed"]
RunStatus = Literal["running", "completed", "failed", "paused", "cancelled"]
WorkspaceState = Literal["clean", "partially_modified", "unknown"]
ActionType = Literal["file_write", "shell_cmd", "network", "deploy", "migration", "db"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ActionRequestStatus = Literal[
    "pending", "approved", "denied", "expired", "auto_approved", "blocked"
]
ApprovalScope = Literal["once", "mission", "always"]
CredentialEventType = Literal[
    "validated", "expired", "quota_exceeded", "rate_limited", "rotated", "warning"
]
EventSeverity = Literal["info", "warning", "error", "critical"]
MessageRole = Literal["user", "assistant", "system", "tool"]


# ============================================================
# GATEWAY HEALTH
# ============================================================

class GatewayHealth(BaseModel):
    version: str
    status: Literal["ok", "degraded"]
    uptime_seconds: float
    active_missions: int
    active_agents: int


# ============================================================
# AGENTS
# ============================================================

class AgentResponse(BaseModel):
    id: str
    display_name: str
    adapter: AgentAdapter
    command: str
    role: str
    capabilities: list[str]
    workspace_strategy: WorkspaceStrategy
    fallback_agent_id: str | None
    status: AgentStatus
    last_health_check: str | None
    created_at: str


class AgentHealthResponse(BaseModel):
    agent_id: str
    status: CredentialStatus
    credential_type: Literal["api_key", "subscription_session", "local_auth"]
    last_validated: str | None
    quota_remaining: int | None
    expiry_hint: str | None
    auto_recovery_eligible: bool
    fallback_agent_id: str | None


class RegisterAgentRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    adapter: AgentAdapter
    command: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    fallback_agent_id: str | None = None


class ValidateAgentResponse(BaseModel):
    valid: bool
    details: str
    checked_at: str


# ============================================================
# MISSIONS
# ============================================================

class CreateMissionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    objective: str = Field(..., min_length=1)


class MissionResponse(BaseModel):
    id: str
    title: str
    objective: str
    status: MissionStatus
    risk_level: RiskLevel | None
    created_at: str
    completed_at: str | None


class PlannedTask(BaseModel):
    id: str
    title: str
    assigned_agent_id: str | None
    depends_on: list[str]
    risk_level: RiskLevel
    estimated_files: list[str]


class MissionPlanResponse(BaseModel):
    mission_id: str
    tasks: list[PlannedTask]


class RunMissionRequest(BaseModel):
    parallel: bool = True


class MissionStatusResponse(BaseModel):
    mission_id: str
    status: MissionStatus
    tasks: list[TaskStatusItem]


class TaskStatusItem(BaseModel):
    id: str
    title: str
    agent_id: str | None
    agent_name: str | None
    status: TaskStatus
    progress_pct: int | None


# ============================================================
# TASKS
# ============================================================

class TaskResponse(BaseModel):
    id: str
    mission_id: str
    title: str
    assigned_agent_id: str | None
    depends_on: list[str]
    status: TaskStatus
    branch: str | None
    worktree_path: str | None
    files_owned: list[str]
    created_at: str
    completed_at: str | None


class RouteTaskRequest(BaseModel):
    to_agent_id: str


class RouteTaskResponse(BaseModel):
    task_id: str
    new_run_id: str
    handoff_context_pack_id: str


class DiffResponse(BaseModel):
    task_id: str
    branch: str
    base_branch: str
    diff_text: str
    files_changed: list[str]


class MergeResponse(BaseModel):
    task_id: str
    merged_sha: str
    worktree_removed: bool


class RollbackResponse(BaseModel):
    task_id: str
    rolled_back_to_sha: str
    worktree_removed: bool


# ============================================================
# INBOX
# ============================================================

class InboxActionRequest(BaseModel):
    id: str
    run_id: str
    agent_id: str
    agent_name: str
    task_title: str
    action_type: ActionType
    command_or_tool: str | None
    risk_score: int
    risk_level: RiskLevel
    explanation: str | None
    evidence: dict[str, Any] | None
    status: ActionRequestStatus
    created_at: str


class InboxCredentialEvent(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    event_type: CredentialEventType
    task_id: str | None
    task_title: str | None
    task_progress_pct: int | None
    branch_state: WorkspaceState | None
    mission_id: str | None
    details: dict[str, Any] | None
    created_at: str


class InboxConflict(BaseModel):
    id: str
    type: Literal["file_overlap", "api_contract", "migration", "dependency"]
    agents_involved: list[str]
    files_affected: list[str]
    created_at: str


class InboxResponse(BaseModel):
    action_requests: list[InboxActionRequest]
    credential_events: list[InboxCredentialEvent]
    conflicts: list[InboxConflict]


class ApproveRequest(BaseModel):
    scope: ApprovalScope = "once"
    note: str | None = None


class ApproveResponse(BaseModel):
    approved: bool
    action_request_id: str
    scope: ApprovalScope


class DenyResponse(BaseModel):
    denied: bool
    action_request_id: str


class RouteCredentialEventResponse(BaseModel):
    routed: bool
    new_run_id: str


# ============================================================
# CONTEXT
# ============================================================

class ContextDocumentItem(BaseModel):
    path: str
    title: str
    last_modified: str
    token_estimate: int


class ContextListResponse(BaseModel):
    documents: list[ContextDocumentItem]


class BuildContextPackRequest(BaseModel):
    task_id: str
    agent_id: str
    token_budget: int = 8000


class ContextPackResponse(BaseModel):
    id: str
    task_id: str
    agent_id: str
    run_id: str
    documents: list[str]
    constraints: list[str]
    token_budget: int
    tokens_used: int
    generated_at: str


# ============================================================
# CREDENTIALS
# ============================================================

class RotateCredentialRequest(BaseModel):
    credential_value: str = Field(..., min_length=1)


class RotateCredentialResponse(BaseModel):
    rotated: bool
    validated: bool


# ============================================================
# EVENTS
# ============================================================

class EventResponse(BaseModel):
    id: str
    timestamp: str
    source: str
    type: str
    payload: dict[str, Any] | None
    severity: EventSeverity
    mission_id: str | None
    task_id: str | None
    run_id: str | None


class EventListResponse(BaseModel):
    events: list[EventResponse]
    total: int
    has_more: bool
