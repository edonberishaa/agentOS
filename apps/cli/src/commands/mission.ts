import type { Command } from 'commander'
import { display } from '../lib/display.js'

interface CreateOptions {
  title?: string
}

interface RunOptions {
  parallel: boolean
}

function deriveTitle(objective: string): string {
  return objective.length > 60 ? `${objective.slice(0, 57)}...` : objective
}

export function registerMissionCommand(program: Command): void {
  const mission = program.command('mission').description('Manage missions')

  mission
    .command('create <objective>')
    .description('Create a new mission from a natural-language objective')
    .option('--title <title>', 'Short title for the mission (derived from objective if omitted)')
    .action(async (objective: string, opts: CreateOptions) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const created = await gateway.createMission(opts.title ?? deriveTitle(objective), objective)
        display.success(`Created mission "${created.title}" (id: ${created.id})`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  mission
    .command('plan <mission_id>')
    .description('Generate the task breakdown for a mission')
    .action(async (missionId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const [plan, agents] = await Promise.all([
          gateway.planMission(missionId),
          gateway.listAgents(),
        ])
        const nameById = new Map(agents.map((a) => [a.id, a.display_name]))
        display.success(`Planned ${String(plan.tasks.length)} tasks`)
        for (const task of plan.tasks) {
          const agentName = task.assigned_agent_id
            ? nameById.get(task.assigned_agent_id) ?? task.assigned_agent_id
            : 'unassigned'
          console.log(
            `  ${display.riskBadge(task.risk_level)} ${task.title} — ${agentName}` +
              (task.depends_on.length > 0 ? ` (depends on: ${task.depends_on.join(', ')})` : '')
          )
        }
      } catch (err) {
        display.gatewayError(err)
      }
    })

  mission
    .command('run <mission_id>')
    .description('Start mission execution and stream live events until it stops running')
    .option('--parallel', 'Run ready tasks in parallel', true)
    .action(async (missionId: string, opts: RunOptions) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const result = await gateway.runMission(missionId, opts.parallel)
        display.success(`Started ${String(result.started_tasks.length)} task(s)`)
      } catch (err) {
        display.gatewayError(err)
      }

      const { GATEWAY_BASE_URL } = await import('@agent-os/shared')
      const { EventSource } = await import('eventsource')

      display.info('Streaming live events — press Ctrl+C to stop watching')
      const es = new EventSource(`${GATEWAY_BASE_URL}/events/stream?mission_id=${missionId}`)

      let stopped = false
      const stop = (): void => {
        if (stopped) return
        stopped = true
        es.close()
        clearInterval(poll)
      }

      es.onmessage = (msg: MessageEvent) => {
        try {
          const event = JSON.parse(String(msg.data)) as {
            type: string
            source: string
            timestamp: string
          }
          console.log(`  ${chalkTime(event.timestamp)} ${event.source}: ${event.type}`)
        } catch {
          // heartbeat comments arrive as non-JSON, ignore
        }
      }
      es.onerror = () => {
        // EventSource auto-reconnects; only fatal if the mission already finished
      }

      const poll = setInterval(() => {
        void gateway
          .getMissionStatus(missionId)
          .then((status) => {
            if (status.status !== 'running') {
              display.success(`Mission ${status.status}`)
              stop()
            }
          })
          .catch(() => {
            stop()
          })
      }, 2000)

      await new Promise<void>((resolve) => {
        const check = setInterval(() => {
          if (stopped) {
            clearInterval(check)
            resolve()
          }
        }, 250)
      })
    })

  mission
    .command('pause <mission_id>')
    .description('Pause all running tasks in a mission')
    .action(async (missionId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        await gateway.pauseMission(missionId)
        display.success(`Paused mission ${missionId}`)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}

function chalkTime(iso: string): string {
  return new Date(iso).toLocaleTimeString()
}
