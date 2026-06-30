/**
 * display.ts — Consistent terminal output helpers for all CLI commands.
 *
 * All output goes through these helpers — no raw console.log in commands.
 * This makes it easy to add JSON output mode (--json flag) in v2.
 */

import chalk from 'chalk'
import Table from 'cli-table3'
import type { Agent, AgentHealth, InboxActionRequest, InboxConflict, InboxCredentialEvent } from '@agent-os/shared'
import { GatewayNotRunningError, GatewayError } from './gateway-client.js'

// ============================================================
// STATUS MESSAGES
// ============================================================

export const display = {
  success(message: string): void {
    console.log(chalk.green('✓') + ' ' + message)
  },

  info(message: string): void {
    console.log(chalk.blue('ℹ') + ' ' + message)
  },

  warning(message: string): void {
    console.log(chalk.yellow('⚠') + ' ' + message)
  },

  error(message: string): void {
    console.error(chalk.red('✗') + ' ' + message)
  },

  // ---- Structured objects ----

  json(data: unknown): void {
    console.log(JSON.stringify(data, null, 2))
  },

  // ---- Agent table ----

  agentTable(agents: Agent[]): void {
    const table = new Table({
      head: [
        chalk.bold('Name'),
        chalk.bold('Role'),
        chalk.bold('Status'),
        chalk.bold('Capabilities'),
        chalk.bold('Last Health Check'),
      ],
      style: { head: [], border: ['grey'] },
    })

    for (const agent of agents) {
      const statusColor =
        agent.status === 'idle' ? chalk.green
        : agent.status === 'busy' ? chalk.blue
        : agent.status === 'degraded' ? chalk.yellow
        : agent.status === 'expired' ? chalk.red
        : chalk.grey

      table.push([
        agent.display_name,
        agent.role,
        statusColor(agent.status),
        agent.capabilities.join(', ') || '—',
        agent.last_health_check
          ? new Date(agent.last_health_check).toLocaleTimeString()
          : chalk.grey('never'),
      ])
    }

    console.log(table.toString())
  },

  // ---- Agent health table ----

  agentHealthTable(rows: { agent: Agent; health: AgentHealth | null }[]): void {
    const table = new Table({
      head: [
        chalk.bold('Name'),
        chalk.bold('Role'),
        chalk.bold('Adapter'),
        chalk.bold('Status'),
        chalk.bold('Credential'),
        chalk.bold('Last Validated'),
        chalk.bold('Fallback'),
      ],
      style: { head: [], border: ['grey'] },
    })

    for (const { agent, health } of rows) {
      const credColor =
        health?.status === 'healthy' ? chalk.green
        : health?.status === 'warning' ? chalk.yellow
        : health?.status === 'expired' ? chalk.red
        : chalk.grey

      table.push([
        agent.display_name,
        agent.role,
        agent.adapter,
        agent.status,
        credColor(health?.status ?? 'unknown'),
        health?.last_validated ? new Date(health.last_validated).toLocaleTimeString() : chalk.grey('never'),
        health?.fallback_agent_id ?? chalk.grey('none'),
      ])
    }

    console.log(table.toString())
  },

  // ---- Inbox sections ----

  inboxActionRequests(items: InboxActionRequest[]): void {
    display.divider('Action Requests')
    if (items.length === 0) {
      console.log(chalk.grey('  (none pending)'))
      return
    }
    for (const item of items) {
      console.log(
        `  ${display.riskBadge(item.risk_level)} ${chalk.bold(item.action_type)} ` +
          `via ${chalk.cyan(item.agent_name)} — ${chalk.grey(item.task_title)}`
      )
      if (item.command_or_tool) {
        console.log(chalk.grey(`    tool: ${item.command_or_tool}`))
      }
      if (item.explanation) {
        console.log(chalk.grey(`    ${item.explanation}`))
      }
      console.log(
        chalk.grey(`    id: ${item.id}  →  agentos approve ${item.id} | agentos deny ${item.id}`)
      )
    }
  },

  inboxCredentialEvents(items: InboxCredentialEvent[]): void {
    display.divider('Credential Events')
    if (items.length === 0) {
      console.log(chalk.grey('  (none unresolved)'))
      return
    }
    for (const item of items) {
      console.log(
        `  ${chalk.red('●')} ${chalk.bold(item.event_type)} — ${chalk.cyan(item.agent_name)} ` +
          `(${item.task_title ?? 'no task'})`
      )
      console.log(chalk.grey(`    id: ${item.id}  →  agentos route ${item.id} --to <fallback_agent_id>`))
    }
  },

  inboxConflicts(items: InboxConflict[]): void {
    display.divider('Conflicts')
    if (items.length === 0) {
      console.log(chalk.grey('  (none active)'))
      return
    }
    for (const item of items) {
      console.log(
        `  ${chalk.yellow('▲')} ${chalk.bold(item.type)} — agents: ${item.agents_involved.join(', ')}`
      )
      console.log(chalk.grey(`    files: ${item.files_affected.join(', ') || '—'}`))
    }
  },

  // ---- Risk badge ----

  riskBadge(level: string): string {
    switch (level) {
      case 'low': return chalk.green(`[${level.toUpperCase()}]`)
      case 'medium': return chalk.yellow(`[${level.toUpperCase()}]`)
      case 'high': return chalk.red(`[${level.toUpperCase()}]`)
      case 'critical': return chalk.bgRed.white(`[${level.toUpperCase()}]`)
      default: return `[${level.toUpperCase()}]`
    }
  },

  // ---- Divider ----

  divider(label?: string): void {
    const width = 60
    if (label) {
      const pad = Math.max(0, width - label.length - 4)
      console.log(chalk.grey('── ') + chalk.bold(label) + chalk.grey(' ' + '─'.repeat(pad)))
    } else {
      console.log(chalk.grey('─'.repeat(width)))
    }
  },

  // ---- Error handling ----

  gatewayError(err: unknown): never {
    if (err instanceof GatewayNotRunningError) {
      display.error(err.message)
    } else if (err instanceof GatewayError) {
      display.error(`Gateway returned ${String(err.status)}: ${err.detail}`)
    } else if (err instanceof Error) {
      display.error(err.message)
    } else {
      display.error('An unexpected error occurred')
    }
    process.exit(1)
  },
}
