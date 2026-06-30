import { copyFileSync, existsSync, mkdirSync } from 'fs'
import { basename, join } from 'path'
import type { Command } from 'commander'
import { display } from '../lib/display.js'

const CONTEXT_DIR = '.agentos/context'

export function registerContextCommand(program: Command): void {
  const context = program.command('context').description('Manage the context vault')

  context
    .command('add <file>')
    .description('Copy a document into the local context vault (.agentos/context/)')
    .action((file: string) => {
      const cwd = process.cwd()
      if (!existsSync(file)) {
        display.error(`File not found: ${file}`)
        process.exit(1)
      }
      const destDir = join(cwd, CONTEXT_DIR)
      mkdirSync(destDir, { recursive: true })
      const dest = join(destDir, basename(file))
      copyFileSync(file, dest)
      display.success(`Added ${basename(file)} to ${CONTEXT_DIR}/`)
    })

  context
    .command('show <pack_id>')
    .description(
      'Show a context pack by id (documents + token usage). ' +
        'Note: the gateway looks packs up by pack_id, not task_id — ' +
        'find a task\'s pack_id via the run.handed_off / context_pack.generated event payloads.'
    )
    .action(async (packId: string) => {
      const { gateway } = await import('../lib/gateway-client.js')
      try {
        const pack = await gateway.getContextPack(packId)
        display.info(`Context pack ${pack.id} for task ${pack.task_id}`)
        console.log(`  documents: ${pack.documents.length > 0 ? pack.documents.join(', ') : '(none)'}`)
        console.log(`  tokens used: ${String(pack.tokens_used)} / ${String(pack.token_budget)}`)
      } catch (err) {
        display.gatewayError(err)
      }
    })
}
