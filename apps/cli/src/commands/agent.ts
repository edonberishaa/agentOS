import type { Command } from 'commander'
import type { AgentAdapter, AgentHealth } from '@agent-os/shared'
import { display } from '../lib/display.js'

interface AddOptions {
  role?: string
  cmd?: string
  fallback?: string
  adapter: AgentAdapter
  capability: string[]
}

function collect(value: string, previous: string[]): string[] {
  return [...previous, value]
}

export function registerAgentCommand(program: Command): void {
  const agent = program.command('agent').description('Manage registered agents')

  agent
    .command('add <name>')
    .description('Register a new agent')
    .option('--role <role>', 'Role this agent plays (e.g. frontend, backend)', 'general')
    .option('--cmd <command>', 'CLI command to invoke the agent (e.g. claude, codex)')
    .option('--fallback <agent_id>', 'Agent to route to on credential failure')
    .option('--adapter <adapter>', 'Adapter type: claude-code | codex | mock', 'claude-code')
    .option('--capability <cap>', 'A capability this agent has (repeatable)', collect, [])
    .action(async (name: string, opts: AddOptions) => {
      const { gateway } = await import('../lib/gateway-client.js')
      if (!opts.cmd) {
        display.error('--cmd is required (the CLI command to invoke the agent)')
        process.exit(1)
      }
      try {
        const created = await gateway.registerAgent({
          display_name: name,
          adapter: opts.adapter,
          command: opts.cmd,
          role: opts.role ?? 'general',
          capabilities: opts.capability,
          ...(opts.fallback ? { fallback_agent_id: opts.fallback } : {}),
        })
        display.success(`Registered agent "${created.display_name}" (id: ${created.id})`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  agent
    .command('remove <id>')
    .description('Remove a registered agent')
    .action(async (id: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        await gateway.removeAgent(id)
        display.success(`Removed agent ${id}`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  const agents = program
    .command('agents')
    .description('List all agents')
    .action(async () => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const list = await gateway.listAgents()
        if (list.length === 0) {
          display.info('No agents registered. Run: agentos agent add')
          return
        }
        display.agentTable(list)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  agents
    .command('health')
    .description('Show credential health for every registered agent')
    .action(async () => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const list = await gateway.listAgents()
        if (list.length === 0) {
          display.info('No agents registered. Run: agentos agent add')
          return
        }
        const rows = await Promise.all(
          list.map(async (a) => {
            let health: AgentHealth | null = null
            try {
              health = await gateway.getAgentHealth(a.id)
            } catch {
              health = null
            }
            return { agent: a, health }
          })
        )
        display.agentHealthTable(rows)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
