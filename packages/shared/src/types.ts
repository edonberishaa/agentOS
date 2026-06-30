// ============================================================
// ENUMS & UNION TYPES
// ============================================================

export type AgentAdapter = 'claude-code' | 'codex' | 'mock'
export type WorkspaceStrategy = 'git_worktree'

export type AgentStatus = 'idle' | 'busy' | 'degraded' | 'expired' | 'error'
export type CredentialStatus = 'healthy' | 'warning' | 'expired' | 'unknown'

export type MissionStatus = 'planning' | 'running' | 'paused' | 'completed' | 'failed'
export type TaskStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed'
export type RunStatus = 'running' | 'completed' | 'failed' | 'paused' | 'cancelled'
export type WorkspaceState = 'clean' | 'partially_modified' | 'unknown'

export type ActionType = 'file_write' | 'shell_cmd' | 'network' | 'deploy' | 'migration' | 'db'
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type ActionRequestStatus =
  | 'pending'
  | 'approved'
  | 'denied'
  | 'expired'
  | 'auto_approved'
  | 'blocked'
export type ApprovalScope = 'once' | 'mission' | 'always'

export type CredentialEventType =
  | 'validated'
  | 'expired'
  | 'quota_exceeded'
  | 'rate_limited'
  | 'rotated'
  | 'warning'

export type EventSeverity = 'info' | 'warning' | 'error' | 'critical'

// ============================================================
// CORE ENTITIES
// ============================================================

export interface Agent {
  id: string
  display_name: string
  adapter: AgentAdapter
  command: string
  role: string
  capabilities: string[]
  workspace_strategy: WorkspaceStrategy
  fallback_agent_id: string | null
  status: AgentStatus
  last_health_check: string | null
  created_at: string
}

export interface AgentHealth {
  agent_id: string
  status: CredentialStatus
  credential_type: 'api_key' | 'subscription_session' | 'local_auth'
  last_validated: string | null
  quota_remaining: number | null
  expiry_hint: string | null
  auto_recovery_eligible: boolean
  fallback_agent_id: string | null
}

export interface Mission {
  id: string
  title: string
  objective: string
  status: MissionStatus
  risk_level: RiskLevel | null
  created_at: string
  completed_at: string | null
}

export interface Task {
  id: string
  mission_id: string
  title: string
  assigned_agent_id: string | null
  depends_on: string[]
  status: TaskStatus
  branch: string | null
  worktree_path: string | null
  files_owned: string[]
  created_at: string
  completed_at: string | null
}

export interface AgentRun {
  id: string
  task_id: string
  agent_id: string
  started_at: string
  ended_at: string | null
  status: RunStatus
  workspace_state: WorkspaceState | null
  last_commit_sha: string | null
  confidence_score: number | null
  result_summary: string | null
  took_over_from: string | null
}

export interface AgentMessage {
  id: string
  run_id: string
  agent_id: string
  task_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  token_count: number | null
  created_at: string
}

export interface ContextPack {
  id: string
  task_id: string
  agent_id: string
  run_id: string
  documents: string[]
  constraints: string[]
  token_budget: number
  tokens_used: number
  generated_at: string
}

export interface ActionRequest {
  id: string
  run_id: string
  action_type: ActionType
  command_or_tool: string | null
  risk_score: number
  risk_level: RiskLevel
  explanation: string | null
  evidence: Record<string, unknown> | null
  status: ActionRequestStatus
  created_at: string
}

export interface Approval {
  id: string
  action_request_id: string
  decision: 'approved' | 'denied'
  decided_by: string
  decided_at: string
  scope: ApprovalScope
  note: string | null
}

export interface CredentialEvent {
  id: string
  agent_id: string
  event_type: CredentialEventType
  run_id: string | null
  mission_id: string | null
  details: Record<string, unknown> | null
  resolved_at: string | null
  created_at: string
}

// ============================================================
// API REQUEST / RESPONSE SHAPES
// ============================================================

// Gateway health
export interface GatewayHealth {
  version: string
  status: 'ok' | 'degraded'
  uptime_seconds: number
  active_missions: number
  active_agents: number
}

// Agent registration
export interface RegisterAgentRequest {
  display_name: string
  adapter: AgentAdapter
  command: string
  role: string
  capabilities: string[]
  fallback_agent_id?: string
}

// Mission creation
export interface CreateMissionRequest {
  title: string
  objective: string
}

export interface MissionPlan {
  mission_id: string
  tasks: PlannedTask[]
}

export interface PlannedTask {
  id: string
  title: string
  assigned_agent_id: string | null
  depends_on: string[]
  risk_level: RiskLevel
  estimated_files: string[]
}

// Inbox
export interface InboxItem {
  action_requests: InboxActionRequest[]
  credential_events: InboxCredentialEvent[]
  conflicts: InboxConflict[]
}

export interface InboxActionRequest extends ActionRequest {
  agent_id: string
  agent_name: string
  task_title: string
}

export interface InboxCredentialEvent extends CredentialEvent {
  agent_name: string
  task_title: string | null
  task_progress_pct: number | null
  branch_state: WorkspaceState | null
}

export interface InboxConflict {
  id: string
  type: 'file_overlap' | 'api_contract' | 'migration' | 'dependency'
  agents_involved: string[]
  files_affected: string[]
  created_at: string
}

// Approve / deny
export interface ApproveRequest {
  scope: ApprovalScope
  note?: string
}

// Context pack request
export interface BuildContextPackRequest {
  task_id: string
  agent_id: string
  token_budget?: number
}
