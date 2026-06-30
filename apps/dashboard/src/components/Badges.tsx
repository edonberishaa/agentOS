import type { ReactElement } from 'react'
import type { AgentStatus } from '@agent-os/shared'

const STATUS_COLORS: Record<AgentStatus, string> = {
  idle: 'bg-green-900 text-green-300 border-green-700',
  busy: 'bg-blue-900 text-blue-300 border-blue-700',
  degraded: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  expired: 'bg-red-900 text-red-300 border-red-700',
  error: 'bg-red-950 text-red-400 border-red-800',
}

export function StatusBadge({ status }: { status: AgentStatus }): ReactElement {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status]}`}
    >
      {status}
    </span>
  )
}

const RISK_COLORS: Record<string, string> = {
  low: 'bg-green-900 text-green-300 border-green-700',
  medium: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  high: 'bg-red-900 text-red-300 border-red-700',
  critical: 'bg-red-950 text-red-200 border-red-800',
}

export function RiskBadge({ level }: { level: string }): ReactElement {
  const cls = RISK_COLORS[level] ?? 'bg-gray-800 text-gray-300 border-gray-700'
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium uppercase ${cls}`}>
      {level}
    </span>
  )
}

export function TypeBadge({ type }: { type: string }): ReactElement {
  return (
    <span className="inline-block rounded border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs font-mono text-indigo-300">
      {type}
    </span>
  )
}
