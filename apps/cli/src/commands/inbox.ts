import type { Command } from 'commander'
import type { ApprovalScope } from '@agent-os/shared'
import { display } from '../lib/display.js'

interface ApproveOptions {
  once?: boolean
  mission?: boolean
  always?: boolean
}

function scopeFromOptions(opts: ApproveOptions): ApprovalScope {
  if (opts.always) return 'always'
  if (opts.mission) return 'mission'
  return 'once'
}

export function registerInboxCommand(program: Command): void {
  program
    .command('inbox')
    .description('Show pending approvals, credential failures, and conflicts')
    .action(async () => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const inbox = await gateway.getInbox()
        if (
          inbox.action_requests.length === 0 &&
          inbox.credential_events.length === 0 &&
          inbox.conflicts.length === 0
        ) {
          display.success('Inbox is empty — no pending items')
          return
        }
        display.inboxActionRequests(inbox.action_requests)
        display.inboxCredentialEvents(inbox.credential_events)
        display.inboxConflicts(inbox.conflicts)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  program
    .command('approve <id>')
    .description('Approve a pending action request')
    .option('--once', 'Approve this single occurrence only (default)')
    .option('--mission', 'Approve for the rest of the current mission')
    .option('--always', 'Always auto-approve this pattern going forward')
    .action(async (id: string, opts: ApproveOptions) => {
      const { gateway } = await import('../lib/gateway-client.js')
      const scope = scopeFromOptions(opts)
      try {
        await gateway.approve(id, { scope })
        display.success(`Approved ${id} (scope: ${scope})`)
      } catch (err) {
        display.gatewayError(err)
      }
    })

  program
    .command('deny <id>')
    .description('Deny a pending action request')
    .action(async (id: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        await gateway.deny(id)
        display.success(`Denied ${id}`)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
