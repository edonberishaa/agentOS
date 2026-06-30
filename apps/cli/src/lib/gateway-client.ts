/**
 * gateway-client.ts — Typed HTTP client for all Agent OS Gateway calls.
 *
 * Every CLI command goes through this client.
 * Handles: base URL resolution, error formatting, response typing.
 */

import { GATEWAY_BASE_URL } from '@agent-os/shared'
import type {
  Agent,
  AgentHealth,
  AgentOSEvent,
  ApproveRequest,
  GatewayHealth,
  InboxItem,
  Mission,
  MissionPlan,
  RegisterAgentRequest,
} from '@agent-os/shared'

export interface EventListResponse {
  events: AgentOSEvent[]
  total: number
  has_more: boolean
}

export interface EventListFilters {
  mission_id?: string
  run_id?: string
  agent_id?: string
  event_type?: string
  severity?: string
  limit?: number
}

export interface ContextPackRecord {
  id: string
  task_id: string
  agent_id: string
  run_id: string
  documents: string[]
  constraints: string[]
  token_budget: number
  tokens_used: number
  content: string | null
  generated_at: string
}

// ============================================================
// ERROR TYPES
// ============================================================

export class GatewayNotRunningError extends Error {
  constructor() {
    super(
      'Agent OS gateway is not running.\n' +
        'Start it with: agentos daemon start'
    )
    this.name = 'GatewayNotRunningError'
  }
}

export class GatewayError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string
  ) {
    super(`Gateway error ${String(status)}: ${detail}`)
    this.name = 'GatewayError'
  }
}

// ============================================================
// CLIENT
// ============================================================

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const url = `${GATEWAY_BASE_URL}${path}`

  const init: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(10_000), // 10s timeout for local calls
  }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(url, init)
  } catch (err) {
    if (err instanceof TypeError && err.message.includes('fetch')) {
      throw new GatewayNotRunningError()
    }
    throw err
  }

  if (!response.ok) {
    const text = await response.text().catch(() => 'Unknown error')
    let detail = text
    try {
      const json = JSON.parse(text) as { detail?: string; error?: string }
      detail = json.detail ?? json.error ?? text
    } catch {
      // use raw text
    }
    throw new GatewayError(response.status, detail)
  }

  return response.json() as Promise<T>
}

// ============================================================
// API METHODS
// ============================================================

export const gateway = {
  // ---- System ----

  health(): Promise<GatewayHealth> {
    return request('GET', '/health')
  },

  // ---- Agents ----

  listAgents(): Promise<Agent[]> {
    return request('GET', '/agents')
  },

  registerAgent(body: RegisterAgentRequest): Promise<Agent> {
    return request('POST', '/agents', body)
  },

  getAgentHealth(agentId: string): Promise<AgentHealth> {
    return request('GET', `/agents/${agentId}/health`)
  },

  validateAgent(agentId: string): Promise<{ valid: boolean; details: string }> {
    return request('POST', `/agents/${agentId}/validate`)
  },

  removeAgent(agentId: string): Promise<{ deleted: boolean }> {
    return request('DELETE', `/agents/${agentId}`)
  },

  // ---- Missions ----

  createMission(title: string, objective: string): Promise<{ id: string; title: string; status: string }> {
    return request('POST', '/missions', { title, objective })
  },

  listMissions(status?: string): Promise<Mission[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    return request('GET', `/missions${query}`)
  },

  planMission(missionId: string): Promise<MissionPlan> {
    return request('POST', `/missions/${missionId}/plan`)
  },

  runMission(missionId: string, parallel = true): Promise<{ started_tasks: string[] }> {
    return request('POST', `/missions/${missionId}/run`, { parallel })
  },

  pauseMission(missionId: string): Promise<{ paused_tasks: string[] }> {
    return request('POST', `/missions/${missionId}/pause`)
  },

  getMissionStatus(missionId: string): Promise<{ status: string; tasks: unknown[] }> {
    return request('GET', `/missions/${missionId}/status`)
  },

  // ---- Tasks ----

  getDiff(taskId: string): Promise<{ diff_text: string; files_changed: string[] }> {
    return request('GET', `/tasks/${taskId}/diff`)
  },

  mergeTask(taskId: string): Promise<{ merged_sha: string }> {
    return request('POST', `/tasks/${taskId}/merge`)
  },

  rollbackTask(taskId: string): Promise<{ rolled_back_to_sha: string }> {
    return request('POST', `/tasks/${taskId}/rollback`)
  },

  routeTask(taskId: string, toAgentId: string): Promise<{ new_run_id: string }> {
    return request('POST', `/tasks/${taskId}/route`, { to_agent_id: toAgentId })
  },

  // ---- Inbox ----

  getInbox(): Promise<InboxItem> {
    return request('GET', '/inbox')
  },

  approve(actionRequestId: string, body: ApproveRequest): Promise<{ approved: boolean }> {
    return request('POST', `/inbox/approve/${actionRequestId}`, body)
  },

  deny(actionRequestId: string, note?: string): Promise<{ denied: boolean }> {
    return request('POST', `/inbox/deny/${actionRequestId}`, { note })
  },

  // ---- Credentials ----

  rotateCredential(
    agentId: string,
    credentialValue: string
  ): Promise<{ rotated: boolean; validated: boolean }> {
    return request('POST', `/credentials/rotate/${agentId}`, { credential_value: credentialValue })
  },

  // ---- Context ----

  getContextPack(packId: string): Promise<ContextPackRecord> {
    return request('GET', `/context/pack/${packId}`)
  },

  // ---- Events ----

  listEvents(filters: EventListFilters = {}): Promise<EventListResponse> {
    const params = new URLSearchParams()
    if (filters.mission_id) params.set('mission_id', filters.mission_id)
    if (filters.run_id) params.set('run_id', filters.run_id)
    if (filters.agent_id) params.set('agent_id', filters.agent_id)
    if (filters.event_type) params.set('event_type', filters.event_type)
    if (filters.severity) params.set('severity', filters.severity)
    params.set('limit', String(filters.limit ?? 50))
    const query = params.toString()
    return request('GET', `/events${query ? `?${query}` : ''}`)
  },
}
