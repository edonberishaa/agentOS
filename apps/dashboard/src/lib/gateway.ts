/**
 * lib/gateway.ts — Typed fetch wrapper for the Agent OS gateway, plus the
 * useLiveEvents SSE hook. Every dashboard page talks to the gateway only
 * through this module — no raw fetch() in components, no direct DB access.
 */
'use client'

import { useEffect, useState } from 'react'
import type {
  Agent,
  AgentHealth,
  ApprovalScope,
  Mission,
  MissionPlan,
  MissionStatus,
} from '@agent-os/shared'

// The only place `localhost:47821` may appear in this app — every other
// call site goes through GATEWAY_BASE_URL.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_URL ?? 'http://localhost:47821'

export class GatewayError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string
  ) {
    super(`Gateway error ${String(status)}: ${detail}`)
    this.name = 'GatewayError'
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
  }

  const response = await fetch(`${GATEWAY_BASE_URL}${path}`, init)

  if (!response.ok) {
    const text = await response.text().catch(() => 'Unknown error')
    let detail = text
    try {
      const json = JSON.parse(text) as { detail?: string }
      detail = json.detail ?? text
    } catch {
      // use raw text
    }
    throw new GatewayError(response.status, detail)
  }

  return response.json() as Promise<T>
}

// ============================================================
// RESPONSE SHAPES NOT EXPORTED FROM @agent-os/shared
// (gateway-only response models — see models.py)
// ============================================================

export interface GatewayHealth {
  version: string
  status: 'ok' | 'degraded'
  uptime_seconds: number
  active_missions: number
  active_agents: number
}

export interface ValidateAgentResponse {
  valid: boolean
  details: string
  checked_at: string
}

export interface TaskStatusItem {
  id: string
  title: string
  agent_id: string | null
  agent_name: string | null
  status: string
  progress_pct: number | null
}

export interface MissionStatusResponse {
  mission_id: string
  status: MissionStatus
  tasks: TaskStatusItem[]
}

export interface InboxActionRequestItem {
  id: string
  run_id: string
  agent_id: string
  agent_name: string
  task_title: string
  action_type: string
  command_or_tool: string | null
  risk_score: number
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  explanation: string | null
  evidence: Record<string, unknown> | null
  status: string
  created_at: string
}

export interface InboxCredentialEventItem {
  id: string
  agent_id: string
  agent_name: string
  event_type: string
  task_id: string | null
  task_title: string | null
  task_progress_pct: number | null
  branch_state: string | null
  mission_id: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export interface InboxConflictItem {
  id: string
  type: string
  agents_involved: string[]
  files_affected: string[]
  created_at: string
}

export interface InboxResponse {
  action_requests: InboxActionRequestItem[]
  credential_events: InboxCredentialEventItem[]
  conflicts: InboxConflictItem[]
}

export interface GatewayEvent {
  id: string
  timestamp: string
  source: string
  type: string
  payload: Record<string, unknown> | null
  severity: 'info' | 'warning' | 'error' | 'critical'
  mission_id: string | null
  task_id: string | null
  run_id: string | null
}

export interface EventListResponse {
  events: GatewayEvent[]
  total: number
  has_more: boolean
}

// ============================================================
// CLIENT
// ============================================================

export const gateway = {
  health(): Promise<GatewayHealth> {
    return request('GET', '/health')
  },

  // ---- Agents ----

  listAgents(): Promise<Agent[]> {
    return request('GET', '/agents')
  },

  getAgentHealth(agentId: string): Promise<AgentHealth> {
    return request('GET', `/agents/${agentId}/health`)
  },

  validateAgent(agentId: string): Promise<ValidateAgentResponse> {
    return request('POST', `/agents/${agentId}/validate`)
  },

  removeAgent(agentId: string): Promise<{ removed: boolean }> {
    return request('DELETE', `/agents/${agentId}`)
  },

  // ---- Missions ----

  listMissions(status?: MissionStatus): Promise<Mission[]> {
    const qs = status ? `?status=${status}` : ''
    return request('GET', `/missions${qs}`)
  },

  getMission(missionId: string): Promise<Mission> {
    return request('GET', `/missions/${missionId}`)
  },

  planMission(missionId: string): Promise<MissionPlan> {
    return request('POST', `/missions/${missionId}/plan`)
  },

  getMissionStatus(missionId: string): Promise<MissionStatusResponse> {
    return request('GET', `/missions/${missionId}/status`)
  },

  // ---- Inbox ----

  getInbox(): Promise<InboxResponse> {
    return request('GET', '/inbox')
  },

  approve(
    actionRequestId: string,
    scope: ApprovalScope = 'once',
    note?: string
  ): Promise<{ approved: boolean }> {
    return request('POST', `/inbox/approve/${actionRequestId}`, { scope, note })
  },

  deny(actionRequestId: string): Promise<{ denied: boolean }> {
    return request('POST', `/inbox/deny/${actionRequestId}`)
  },

  routeCredentialEvent(credentialEventId: string): Promise<{ routed: boolean; new_run_id: string }> {
    return request('POST', `/inbox/route/${credentialEventId}`)
  },

  // ---- Events ----

  listEvents(params: { mission_id?: string; limit?: number } = {}): Promise<EventListResponse> {
    const qs = new URLSearchParams()
    if (params.mission_id) qs.set('mission_id', params.mission_id)
    if (params.limit) qs.set('limit', String(params.limit))
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request('GET', `/events${suffix}`)
  },
}

// ============================================================
// useLiveEvents — SSE hook backed by GET /events/stream
// ============================================================

export interface LiveEventFilters {
  mission_id?: string
  run_id?: string
  agent_id?: string
}

export function useLiveEvents(filters: LiveEventFilters, maxEvents = 20): GatewayEvent[] {
  const [events, setEvents] = useState<GatewayEvent[]>([])
  const filterKey = JSON.stringify(filters)

  useEffect(() => {
    const qs = new URLSearchParams()
    if (filters.mission_id) qs.set('mission_id', filters.mission_id)
    if (filters.run_id) qs.set('run_id', filters.run_id)
    if (filters.agent_id) qs.set('agent_id', filters.agent_id)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''

    const source = new EventSource(`${GATEWAY_BASE_URL}/events/stream${suffix}`)

    source.onmessage = (msg: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(msg.data) as GatewayEvent
        setEvents((prev) => [parsed, ...prev].slice(0, maxEvents))
      } catch {
        // heartbeat comments and malformed payloads are ignored
      }
    }

    source.onerror = () => {
      // EventSource auto-reconnects; nothing to do here beyond letting it retry.
    }

    return () => {
      source.close()
    }
  }, [filterKey, maxEvents])

  return events
}
