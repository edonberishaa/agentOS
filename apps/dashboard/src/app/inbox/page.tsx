'use client'

import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import { gateway, type InboxResponse } from '@/lib/gateway'
import { RiskBadge } from '@/components/Badges'

const POLL_MS = 5_000

export default function InboxPage(): ReactElement {
  const [inbox, setInbox] = useState<InboxResponse | null>(null)
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function refresh(): Promise<void> {
      try {
        const data = await gateway.getInbox()
        if (!cancelled) {
          setInbox(data)
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

  function withBusy(id: string, fn: () => Promise<void>): void {
    setBusyIds((prev) => new Set(prev).add(id))
    fn()
      .catch((err: unknown) => { setError(err instanceof Error ? err.message : 'Action failed') })
      .finally(() => {
        setBusyIds((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
        void gateway.getInbox().then(setInbox)
      })
  }

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-bold text-gray-100">Approval Inbox</h1>
      {error && <p className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase text-gray-400">Action Requests</h2>
        {!inbox || inbox.action_requests.length === 0 ? (
          <p className="text-sm text-gray-500">No pending action requests.</p>
        ) : (
          <div className="space-y-3">
            {inbox.action_requests.map((req) => (
              <div key={req.id} className="rounded border border-gray-800 bg-gray-950/60 p-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-medium text-gray-100">
                    {req.agent_name} — {req.action_type}
                  </span>
                  <RiskBadge level={req.risk_level} />
                </div>
                <p className="text-sm text-gray-400">{req.explanation ?? 'No explanation provided.'}</p>
                <div className="mt-3 flex gap-2">
                  <button
                    disabled={busyIds.has(req.id)}
                    onClick={() => { withBusy(req.id, async () => { await gateway.approve(req.id, 'once') }) }}
                    className="rounded bg-green-800 px-3 py-1 text-xs text-green-100 hover:bg-green-700 disabled:opacity-50"
                  >
                    Approve
                  </button>
                  <button
                    disabled={busyIds.has(req.id)}
                    onClick={() => { withBusy(req.id, async () => { await gateway.deny(req.id) }) }}
                    className="rounded bg-red-800 px-3 py-1 text-xs text-red-100 hover:bg-red-700 disabled:opacity-50"
                  >
                    Deny
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase text-gray-400">Credential Events</h2>
        {!inbox || inbox.credential_events.length === 0 ? (
          <p className="text-sm text-gray-500">No unresolved credential events.</p>
        ) : (
          <div className="space-y-3">
            {inbox.credential_events.map((ev) => (
              <div key={ev.id} className="rounded border border-gray-800 bg-gray-950/60 p-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-medium text-gray-100">{ev.agent_name}</span>
                  <span className="text-xs uppercase text-yellow-400">{ev.event_type}</span>
                </div>
                <p className="text-sm text-gray-400">{ev.task_title ?? 'No associated task'}</p>
                <button
                  disabled={busyIds.has(ev.id)}
                  onClick={() => { withBusy(ev.id, async () => { await gateway.routeCredentialEvent(ev.id) }) }}
                  className="mt-3 rounded bg-indigo-800 px-3 py-1 text-xs text-indigo-100 hover:bg-indigo-700 disabled:opacity-50"
                >
                  Route to fallback
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase text-gray-400">Conflicts</h2>
        {!inbox || inbox.conflicts.length === 0 ? (
          <p className="text-sm text-gray-500">No active conflicts.</p>
        ) : (
          <div className="space-y-3">
            {inbox.conflicts.map((conflict) => (
              <div key={conflict.id} className="rounded border border-gray-800 bg-gray-950/60 p-4">
                <p className="font-medium text-gray-100">{conflict.type}</p>
                <p className="text-sm text-gray-400">Agents: {conflict.agents_involved.join(', ')}</p>
                <p className="font-mono text-xs text-gray-500">
                  Files: {conflict.files_affected.join(', ')}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
