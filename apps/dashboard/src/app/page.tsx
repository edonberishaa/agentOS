'use client'

import Link from 'next/link'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { Agent } from '@agent-os/shared'
import { gateway, useLiveEvents, type MissionStatusResponse } from '@/lib/gateway'
import { StatusBadge, TypeBadge } from '@/components/Badges'

const AGENT_POLL_MS = 10_000

export default function MissionControlPage(): ReactElement {
  const [agents, setAgents] = useState<Agent[]>([])
  const [busyTaskTitles, setBusyTaskTitles] = useState<Record<string, string>>({})
  const [pendingCount, setPendingCount] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const events = useLiveEvents({}, 20)

  useEffect(() => {
    const controller = new AbortController()
    const isCancelled = (): boolean => controller.signal.aborted

    async function refresh(): Promise<void> {
      try {
        const [agentList, inbox] = await Promise.all([gateway.listAgents(), gateway.getInbox()])
        if (isCancelled()) return
        setAgents(agentList)
        setPendingCount(inbox.action_requests.length)
        setError(null)

        const running = await gateway.listMissions('running')
        if (isCancelled()) return
        const titles: Record<string, string> = {}
        const statuses = await Promise.all(
          running.map((m) => gateway.getMissionStatus(m.id).catch((): MissionStatusResponse | null => null))
        )
        for (const status of statuses) {
          if (!status) continue
          for (const task of status.tasks) {
            if (task.agent_id && task.status === 'running') {
              titles[task.agent_id] = task.title
            }
          }
        }
        if (!isCancelled()) setBusyTaskTitles(titles)
      } catch (err) {
        if (!isCancelled()) setError(err instanceof Error ? err.message : 'Failed to reach gateway')
      }
    }

    void refresh()
    const interval = setInterval(() => void refresh(), AGENT_POLL_MS)
    return () => {
      controller.abort()
      clearInterval(interval)
    }
  }, [])

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-100">Mission Control</h1>
        <Link
          href="/inbox"
          className="rounded border border-accent/40 bg-accent/10 px-3 py-1 text-sm text-indigo-300 hover:bg-accent/20"
        >
          {pendingCount} pending approval{pendingCount === 1 ? '' : 's'}
        </Link>
      </div>

      {error && <p className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase text-gray-400">Agents</h2>
        {agents.length === 0 ? (
          <p className="text-sm text-gray-500">No agents registered. Run: agentos agent add</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <div key={agent.id} className="rounded border border-gray-800 bg-gray-950/60 p-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-medium text-gray-100">{agent.display_name}</span>
                  <StatusBadge status={agent.status} />
                </div>
                <p className="text-sm text-gray-400">{agent.role}</p>
                {agent.status === 'busy' && busyTaskTitles[agent.id] && (
                  <p className="mt-2 text-sm text-indigo-300">→ {busyTaskTitles[agent.id]}</p>
                )}
                <p className="mt-3 text-xs text-gray-500">
                  Last health check:{' '}
                  {agent.last_health_check ? new Date(agent.last_health_check).toLocaleTimeString() : 'never'}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase text-gray-400">Recent events</h2>
        <div className="space-y-1 rounded border border-gray-800 bg-gray-950/60 p-3 font-mono text-xs">
          {events.length === 0 ? (
            <p className="text-gray-500">No events yet.</p>
          ) : (
            events.map((event) => (
              <div key={event.id} className="flex items-center gap-2 text-gray-400">
                <span className="text-gray-600">{new Date(event.timestamp).toLocaleTimeString()}</span>
                <span className="text-gray-500">{event.source}</span>
                <TypeBadge type={event.type} />
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  )
}
