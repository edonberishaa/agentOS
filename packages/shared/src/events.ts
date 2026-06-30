import type { RiskLevel, WorkspaceState } from './types.js'

// ============================================================
// BASE EVENT
// ============================================================

export interface BaseEvent {
  id: string
  timestamp: string          // ISO-8601 UTC
  source: string             // agent_id | 'gateway' | 'user'
  severity: 'info' | 'warning' | 'error' | 'critical'
  mission_id: string | null
  task_id: string | null
  run_id: string | null
}

// ============================================================
// MISSION EVENTS
// ============================================================

export interface MissionCreatedEvent extends BaseEvent {
  type: 'mission.created'
  payload: { mission_id: string; title: string; objective: string }
}

export interface MissionPlanningStartedEvent extends BaseEvent {
  type: 'mission.planning_started'
  payload: { mission_id: string }
}

export interface MissionRunningEvent extends BaseEvent {
  type: 'mission.running'
  payload: { mission_id: string; task_count: number; parallel: boolean }
}

export interface MissionPausedEvent extends BaseEvent {
  type: 'mission.paused'
  payload: { mission_id: string; reason: string }
}

export interface MissionCompletedEvent extends BaseEvent {
  type: 'mission.completed'
  payload: { mission_id: string; tasks_completed: number; tasks_failed: number }
}

export interface MissionFailedEvent extends BaseEvent {
  type: 'mission.failed'
  payload: { mission_id: string; reason: string }
}

// ============================================================
// TASK EVENTS
// ============================================================

export interface TaskCreatedEvent extends BaseEvent {
  type: 'task.created'
  payload: { task_id: string; title: string; assigned_agent_id: string | null }
}

export interface TaskAssignedEvent extends BaseEvent {
  type: 'task.assigned'
  payload: { task_id: string; agent_id: string }
}

export interface TaskStartedEvent extends BaseEvent {
  type: 'task.started'
  payload: { task_id: string; agent_id: string; branch: string; worktree_path: string }
}

export interface TaskPausedEvent extends BaseEvent {
  type: 'task.paused'
  payload: { task_id: string; reason: string }
}

export interface TaskCompletedEvent extends BaseEvent {
  type: 'task.completed'
  payload: { task_id: string; agent_id: string; files_changed: number }
}

export interface TaskFailedEvent extends BaseEvent {
  type: 'task.failed'
  payload: { task_id: string; reason: string }
}

// ============================================================
// AGENT EVENTS
// ============================================================

export interface AgentRegisteredEvent extends BaseEvent {
  type: 'agent.registered'
  payload: { agent_id: string; display_name: string; adapter: string }
}

export interface AgentRemovedEvent extends BaseEvent {
  type: 'agent.removed'
  payload: { agent_id: string }
}

export interface AgentStartedEvent extends BaseEvent {
  type: 'agent.started'
  payload: { agent_id: string; task_id: string; run_id: string }
}

export interface AgentIdleEvent extends BaseEvent {
  type: 'agent.idle'
  payload: { agent_id: string }
}

export interface AgentDegradedEvent extends BaseEvent {
  type: 'agent.degraded'
  payload: { agent_id: string; reason: string }
}

export interface AgentExpiredEvent extends BaseEvent {
  type: 'agent.expired'
  payload: { agent_id: string; credential_type: string }
}

// ============================================================
// TOOL / ACTION EVENTS
// ============================================================

export interface ToolRequestedEvent extends BaseEvent {
  type: 'tool.requested'
  payload: { action_request_id: string; action_type: string; risk_level: RiskLevel; risk_score: number }
}

export interface ToolAutoApprovedEvent extends BaseEvent {
  type: 'tool.auto_approved'
  payload: { action_request_id: string; risk_score: number }
}

export interface ToolApprovedEvent extends BaseEvent {
  type: 'tool.approved'
  payload: { action_request_id: string; scope: string }
}

export interface ToolDeniedEvent extends BaseEvent {
  type: 'tool.denied'
  payload: { action_request_id: string; reason?: string }
}

export interface ToolBlockedEvent extends BaseEvent {
  type: 'tool.blocked'
  payload: { action_request_id: string; reason: string }
}

// ============================================================
// CREDENTIAL EVENTS
// ============================================================

export interface CredentialValidatedEvent extends BaseEvent {
  type: 'credential.validated'
  payload: { agent_id: string }
}

export interface CredentialExpiredEvent extends BaseEvent {
  type: 'credential.expired'
  payload: { agent_id: string; credential_type: string; task_id: string | null }
}

export interface CredentialQuotaExceededEvent extends BaseEvent {
  type: 'credential.quota_exceeded'
  payload: { agent_id: string; reset_at: string | null }
}

export interface CredentialRateLimitedEvent extends BaseEvent {
  type: 'credential.rate_limited'
  payload: { agent_id: string; retry_after_ms: number; attempt: number }
}

export interface CredentialRotatedEvent extends BaseEvent {
  type: 'credential.rotated'
  payload: { agent_id: string }
}

export interface CredentialWarningEvent extends BaseEvent {
  type: 'credential.warning'
  payload: { agent_id: string; reason: string; expires_in_days?: number }
}

// ============================================================
// WORKSPACE EVENTS
// ============================================================

export interface WorkspaceCreatedEvent extends BaseEvent {
  type: 'workspace.created'
  payload: { task_id: string; branch: string; worktree_path: string }
}

export interface WorkspaceFrozenEvent extends BaseEvent {
  type: 'workspace.frozen'
  payload: { task_id: string; reason: string; wip_commit_sha: string | null; workspace_state: WorkspaceState }
}

export interface WorkspaceMergedEvent extends BaseEvent {
  type: 'workspace.merged'
  payload: { task_id: string; merged_sha: string; files_changed: number }
}

export interface WorkspaceRolledBackEvent extends BaseEvent {
  type: 'workspace.rolled_back'
  payload: { task_id: string; run_id: string; rolled_back_to_sha: string }
}

// ============================================================
// CONFLICT EVENTS
// ============================================================

export interface ConflictDetectedEvent extends BaseEvent {
  type: 'conflict.detected'
  // No conflict_id here — the conflict's identity IS this event's own id
  // (see BaseEvent.id), consistent with every other event in this file.
  payload: { conflict_type: string; agents_involved: string[]; files_affected: string[] }
}

export interface ConflictResolvedEvent extends BaseEvent {
  type: 'conflict.resolved'
  payload: { conflict_id: string; resolution: string }
}

// ============================================================
// RUN EVENTS
// ============================================================

export interface RunStartedEvent extends BaseEvent {
  type: 'run.started'
  payload: { run_id: string; task_id: string; agent_id: string }
}

export interface RunCompletedEvent extends BaseEvent {
  type: 'run.completed'
  payload: { run_id: string; confidence_score: number | null }
}

export interface RunFailedEvent extends BaseEvent {
  type: 'run.failed'
  payload: { run_id: string; reason: string }
}

export interface RunHandedOffEvent extends BaseEvent {
  type: 'run.handed_off'
  payload: { from_run_id: string; to_run_id: string; from_agent_id: string; to_agent_id: string }
}

// ============================================================
// CONTEXT EVENTS
// ============================================================

export interface ContextPackGeneratedEvent extends BaseEvent {
  type: 'context_pack.generated'
  payload: { context_pack_id: string; task_id: string; documents_count: number; tokens_used: number }
}

// ============================================================
// DISCRIMINATED UNION — all event types
// ============================================================

export type AgentOSEvent =
  | MissionCreatedEvent
  | MissionPlanningStartedEvent
  | MissionRunningEvent
  | MissionPausedEvent
  | MissionCompletedEvent
  | MissionFailedEvent
  | TaskCreatedEvent
  | TaskAssignedEvent
  | TaskStartedEvent
  | TaskPausedEvent
  | TaskCompletedEvent
  | TaskFailedEvent
  | AgentRegisteredEvent
  | AgentRemovedEvent
  | AgentStartedEvent
  | AgentIdleEvent
  | AgentDegradedEvent
  | AgentExpiredEvent
  | ToolRequestedEvent
  | ToolAutoApprovedEvent
  | ToolApprovedEvent
  | ToolDeniedEvent
  | ToolBlockedEvent
  | CredentialValidatedEvent
  | CredentialExpiredEvent
  | CredentialQuotaExceededEvent
  | CredentialRateLimitedEvent
  | CredentialRotatedEvent
  | CredentialWarningEvent
  | WorkspaceCreatedEvent
  | WorkspaceFrozenEvent
  | WorkspaceMergedEvent
  | WorkspaceRolledBackEvent
  | ConflictDetectedEvent
  | ConflictResolvedEvent
  | RunStartedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunHandedOffEvent
  | ContextPackGeneratedEvent

export type EventType = AgentOSEvent['type']
