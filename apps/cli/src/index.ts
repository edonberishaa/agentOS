#!/usr/bin/env node
/**
 * index.ts — Agent OS CLI entry point.
 *
 * Registers all commands. Auto-starts the gateway daemon if not running
 * when a command that requires it is issued.
 */

import { Command } from 'commander'
import chalk from 'chalk'

// Commands
import { registerInitCommand } from './commands/init.js'
import { registerOnboardCommand } from './commands/onboard.js'
import { registerDaemonCommand } from './commands/daemon.js'
import { registerAgentCommand } from './commands/agent.js'
import { registerMissionCommand } from './commands/mission.js'
import { registerInboxCommand } from './commands/inbox.js'
import { registerWorkspaceCommands } from './commands/workspace.js'
import { registerContextCommand } from './commands/context.js'
import { registerCredentialsCommand } from './commands/credentials.js'
import { registerConflictsCommand } from './commands/conflicts.js'
import { registerLogsCommand } from './commands/logs.js'

const program = new Command()

program
  .name('agentos')
  .description(
    chalk.bold('Agent OS') + ' — Personal control plane for AI coding agents'
  )
  .version('0.1.0', '-v, --version', 'Print version')
  .helpOption('-h, --help', 'Show help')

// Register all command groups
registerInitCommand(program)
registerOnboardCommand(program)
registerDaemonCommand(program)
registerAgentCommand(program)
registerMissionCommand(program)
registerInboxCommand(program)
registerWorkspaceCommands(program)
registerContextCommand(program)
registerCredentialsCommand(program)
registerConflictsCommand(program)
registerLogsCommand(program)

// Status shortcut (alias for mission status)
program
  .command('status')
  .description('Show current mission and agent status')
  .action(async () => {
    const { gateway } = await import('./lib/gateway-client.js')
    const { display } = await import('./lib/display.js')
    try {
      const health = await gateway.health()
      display.success(
        `Gateway v${health.version} — ${String(health.active_missions)} active missions, ` +
          `${String(health.active_agents)} active agents`
      )
      const agents = await gateway.listAgents()
      if (agents.length === 0) {
        display.info('No agents registered. Run: agentos agent add')
        return
      }
      display.agentTable(agents)
    } catch (err) {
      display.gatewayError(err)
    }
  })

// Global error handler
program.exitOverride()

try {
  await program.parseAsync(process.argv)
} catch (err: unknown) {
  if (err instanceof Error && err.name === 'CommanderError') {
    // Commander already printed the message; use the error's own exit code
    // (0 for --help/--version, 1 for parse errors).
    process.exit((err as NodeJS.ErrnoException & { exitCode?: number }).exitCode ?? 1)
  }
  console.error(chalk.red('Unexpected error:'), err)
  process.exit(1)
}
