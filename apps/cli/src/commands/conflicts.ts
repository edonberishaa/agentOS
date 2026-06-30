import type { Command } from 'commander'
import { display } from '../lib/display.js'

export function registerConflictsCommand(program: Command): void {
  program
    .command('conflicts')
    .description('List all active conflicts in the current mission')
    .action(async () => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const inbox = await gateway.getInbox()
        display.inboxConflicts(inbox.conflicts)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
