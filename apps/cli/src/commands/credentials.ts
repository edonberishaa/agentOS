import type { Command } from 'commander'
import { display } from '../lib/display.js'

export function registerCredentialsCommand(program: Command): void {
  const creds = program.command('credentials').description('Manage agent credentials')

  creds
    .command('rotate <agent_id>')
    .description('Rotate the stored credential for an agent (prompts for the new value)')
    .action(async (agentId: string) => {
      const { default: inquirer } = await import('inquirer')
      const { gateway } = await import('../lib/gateway-client.js')

      const { credentialValue } = await inquirer.prompt<{ credentialValue: string }>([
        {
          type: 'password',
          name: 'credentialValue',
          message: `New credential value for agent ${agentId}:`,
          mask: '*',
        },
      ])

      try {
        const result = await gateway.rotateCredential(agentId, credentialValue)
        display.success(
          `Rotated credential for ${agentId} — validated: ${result.validated ? 'yes' : 'no'}`
        )
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
