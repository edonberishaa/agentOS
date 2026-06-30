/**
 * commands/workspace.ts — diff, merge, rollback, route (Phase 3/5 implementation)
 */
import type { Command } from 'commander'
import chalk from 'chalk'
import { display } from '../lib/display.js'

function printDiff(diffText: string): void {
  for (const line of diffText.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      console.log(chalk.green(line))
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      console.log(chalk.red(line))
    } else {
      console.log(chalk.grey(line))
    }
  }
}

export function registerWorkspaceCommands(program: Command): void {
  program
    .command('diff <task_id>')
    .description('View agent changes for a task')
    .action(async (taskId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const diff = await gateway.getDiff(taskId)
        if (!diff.diff_text) {
          display.info('No changes yet for this task')
          return
        }
        printDiff(diff.diff_text)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  program
    .command('merge <task_id>')
    .description('Merge approved task work into the base branch')
    .action(async (taskId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const result = await gateway.mergeTask(taskId)
        display.success(`Merged task ${taskId} (sha: ${result.merged_sha.slice(0, 8)})`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  program
    .command('rollback <task_id>')
    .description('Discard a task\'s latest run and restore the last clean state')
    .action(async (taskId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const result = await gateway.rollbackTask(taskId)
        display.success(`Rolled back task ${taskId} to ${result.rolled_back_to_sha.slice(0, 8)}`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  program
    .command('route <task_id>')
    .description('Reroute a task to a different agent')
    .requiredOption('--to <agent_id>', 'Target agent ID')
    .action(async (taskId: string, opts: { to: string }) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const result = await gateway.routeTask(taskId, opts.to)
        display.success(`Routed task ${taskId} to ${opts.to} (new run: ${result.new_run_id})`)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
