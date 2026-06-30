'use client'

import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type { Mission } from '@agent-os/shared'
import { gateway, useLiveEvents, type GatewayEvent } from '@/lib/gateway'
import { TypeBadge } from '@/components/Badges'

export default function TimelinePage(): ReactElement {
  const [mission, setMission] = useState<Mission | null>(null)
  const [history, setHistory] = useState<GatewayEvent[]>([])
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const isCancelled = (): boolean => controller.signal.aborted

    async function resolveMission(): Promise<void> {
      try {
        const running = await gateway.listMissions('running')
        let current = running[0] ?? null
        if (!current) {
          const all = await gateway.listMissions()
          current = all[0] ?? null
        }
        if (isCancelled()) return
        setMission(current)
        if (current) {
          const { events } = await gateway.listEvents({ mission_id: current.id, limit: 200 })
          if (!isCancelled()) setHistory(events)
        }
      } catch (err) {
        if (!isCancelled()) setError(err instanceof Error ? err.message : 'Failed to reach gateway')
      }
    }

    void resolveMission()
    return () => {
      controller.abort()
    }
  }, [])

  const live = useLiveEvents(mission ? { mission_id: mission.id } : {}, 200)

  const allEvents = useMemo(() => {
    const seen = new Set<string>()
    const merged: GatewayEvent[] = []
    for (const event of [...live, ...history]) {
      if (seen.has(event.id)) continue
      seen.add(event.id)
      merged.push(event)
    }
    return merged.sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))
  }, [live, history])

  const eventTypes = useMemo(
    () => Array.from(new Set(allEvents.map((e) => e.type))).sort(),
    [allEvents]
  )

  const filtered = typeFilter === 'all' ? allEvents : allEvents.filter((e) => e.type === typeFilter)

  const grouped = useMemo(() => {
    const groups = new Map<string, GatewayEvent[]>()
    for (const event of filtered) {
      const list = groups.get(event.source) ?? []
      list.push(event)
      groups.set(event.source, list)
    }
    return groups
  }, [filtered])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-100">
          Mission Timeline {mission && <span className="text-gray-500">— {mission.title}</span>}
        </h1>
        <select
          value={typeFilter}
          onChange={(e) => { setTypeFilter(e.target.value) }}
          className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-200"
        >
          <option value="all">All event types</option>
          {eventTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
      {!mission && !error && <p className="text-sm text-gray-500">No missions found yet.</p>}

      {Array.from(grouped.entries()).map(([source, sourceEvents]) => (
        <section key={source}>
          <h2 className="mb-2 text-sm font-semibold uppercase text-gray-400">{source}</h2>
          <div className="space-y-1 rounded border border-gray-800 bg-gray-950/60 p-3">
            {sourceEvents.map((event) => (
              <div key={event.id} className="flex items-center gap-3 font-mono text-xs text-gray-400">
                <span className="text-gray-600">{new Date(event.timestamp).toLocaleTimeString()}</span>
                <TypeBadge type={event.type} />
                <span className="truncate text-gray-500">
                  {event.payload ? JSON.stringify(event.payload).slice(0, 120) : '—'}
                </span>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
