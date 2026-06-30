import type { Command } from 'commander'
import chalk from 'chalk'
import type { AgentOSEvent } from '@agent-os/shared'
import { display } from '../lib/display.js'

interface LogsOptions {
  follow?: boolean
  run?: string
  mission?: string
}

function severityColor(severity: string): (text: string) => string {
  switch (severity) {
    case 'warning':
      return chalk.yellow
    case 'error':
    case 'critical':
      return chalk.red
    default:
      return chalk.grey
  }
}

function formatEvent(event: AgentOSEvent): string {
  const color = severityColor(event.severity)
  const payload = JSON.stringify(event.payload)
  const truncated = payload.length > 120 ? `${payload.slice(0, 117)}...` : payload
  return (
    `[${new Date(event.timestamp).toLocaleTimeString()}] ` +
    color(`[${event.severity}]`) +
    ` ${event.source}: ${chalk.bold(event.type)} ${chalk.grey(truncated)}`
  )
}

export function registerLogsCommand(program: Command): void {
  program
    .command('logs')
    .description('Stream or view recent event logs')
    .option('--follow', 'Keep streaming live events instead of exiting after the last 50')
    .option('--run <id>', 'Filter by run ID')
    .option('--mission <id>', 'Filter by mission ID')
    .action(async (opts: LogsOptions) => {
      const { gateway } = await import('../lib/gateway-client.js')

      if (!opts.follow) {
        try {
          const result = await gateway.listEvents({
            ...(opts.run ? { run_id: opts.run } : {}),
            ...(opts.mission ? { mission_id: opts.mission } : {}),
            limit: 50,
          })
          if (result.events.length === 0) {
            display.info('No events recorded yet')
            return
          }
          for (const event of result.events) {
            console.log(formatEvent(event))
          }
        } catch (err) {
          display.gatewayError(err)
        }
        return
      }

      const { GATEWAY_BASE_URL } = await import('@agent-os/shared')
      const { EventSource } = await import('eventsource')

      const params = new URLSearchParams()
      if (opts.run) params.set('run_id', opts.run)
      if (opts.mission) params.set('mission_id', opts.mission)
      const query = params.toString()

      display.info('Following live events — press Ctrl+C to stop')
      const es = new EventSource(`${GATEWAY_BASE_URL}/events/stream${query ? `?${query}` : ''}`)

      es.onmessage = (msg: MessageEvent) => {
        try {
          const event = JSON.parse(String(msg.data)) as AgentOSEvent
          console.log(formatEvent(event))
        } catch {
          // heartbeat comments arrive as non-JSON, ignore
        }
      }

      await new Promise<void>(() => {
        // Runs until the process receives SIGINT (Ctrl+C) — EventSource keeps the loop alive.
      })
    })
}
