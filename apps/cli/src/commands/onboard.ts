import type { Command } from 'commander'
import { display } from '../lib/display.js'
export function registerOnboardCommand(program: Command): void {
  program.command('onboard').description('Guided setup wizard for agents and providers').action(() => {
    display.info('Phase 3: guided onboard wizard')
  })
}
