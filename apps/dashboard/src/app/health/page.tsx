'use client'

import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { Agent, AgentHealth } from '@agent-os/shared'
import { gateway } from '@/lib/gateway'

const POLL_MS = 30_000

interface Row {
  agent: Agent
  health: AgentHealth | null
}

export default function HealthPage(): ReactElement {
  const [rows, setRows] = useState<Row[]>([])
  const [validating, setValidating] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function refresh(): Promise<void> {
      try {
        const agents = await gateway.listAgents()
        const healths = await Promise.all(
          agents.map((a) => gateway.getAgentHealth(a.id).catch((): AgentHealth | null => null))
        )
        if (!cancelled) {
          setRows(agents.map((agent, i) => ({ agent, health: healths[i] ?? null })))
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to reach gateway')
      }
    }

    void refresh()
    const interval = setInterval(() => void refresh(), POLL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  function validate(agentId: string): void {
    setValidating((prev) => ({ ...prev, [agentId]: 'checking…' }))
    gateway
      .validateAgent(agentId)
      .then((res) => {
        setValidating((prev) => ({
          ...prev,
          [agentId]: res.valid ? `✓ ${res.details}` : `✗ ${res.details}`,
        }))
      })
      .catch((err: unknown) => {
        setValidating((prev) => ({
          ...prev,
          [agentId]: err instanceof Error ? `✗ ${err.message}` : '✗ validation failed',
        }))
      })
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-gray-100">Agent Health</h1>
      {error && <p className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}

      <div className="overflow-x-auto rounded border border-gray-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-900 text-xs uppercase text-gray-400">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Adapter</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Credential</th>
              <th className="px-3 py-2">Last Validated</th>
              <th className="px-3 py-2">Fallback</th>
              <th className="px-3 py-2">Validate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ agent, health }) => (
              <tr key={agent.id} className="border-t border-gray-800">
                <td className="px-3 py-2 text-gray-100">{agent.display_name}</td>
                <td className="px-3 py-2 text-gray-400">{agent.role}</td>
                <td className="px-3 py-2 font-mono text-gray-400">{agent.adapter}</td>
                <td className="px-3 py-2 text-gray-300">{agent.status}</td>
                <td className="px-3 py-2 text-gray-300">{health?.status ?? 'unknown'}</td>
                <td className="px-3 py-2 text-gray-500">
                  {health?.last_validated ? new Date(health.last_validated).toLocaleString() : 'never'}
                </td>
                <td className="px-3 py-2 text-gray-500">{agent.fallback_agent_id ?? '—'}</td>
                <td className="px-3 py-2">
                  <button
                    onClick={() => { validate(agent.id) }}
                    className="rounded bg-indigo-800 px-2 py-1 text-xs text-indigo-100 hover:bg-indigo-700"
                  >
                    Validate
                  </button>
                  {validating[agent.id] && (
                    <span className="ml-2 text-xs text-gray-400">{validating[agent.id]}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
