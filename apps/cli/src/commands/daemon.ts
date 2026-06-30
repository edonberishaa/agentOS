/**
 * commands/daemon.ts — `agentos daemon start|stop|status`
 *
 * Manages the FastAPI gateway process lifecycle.
 * The PID file lives at .agentos/gateway.pid
 */

import { existsSync, readFileSync, writeFileSync, unlinkSync } from 'fs'
import { join } from 'path'
import { spawn } from 'child_process'
import type { Command } from 'commander'
import ora from 'ora'
import { display } from '../lib/display.js'
import { gateway, GatewayNotRunningError } from '../lib/gateway-client.js'

const PID_FILE = '.agentos/gateway.pid'
const LOG_FILE = '.agentos/gateway.log'

export function registerDaemonCommand(program: Command): void {
  const daemon = program
    .command('daemon')
    .description('Manage the Agent OS gateway daemon')

  daemon
    .command('start')
    .description('Start the gateway daemon in the background')
    .option('--port <port>', 'Port to run on (default: 47821)')
    .action(async (opts: { port?: string }) => {
      await startDaemon(opts.port ? parseInt(opts.port) : undefined)
    })

  daemon
    .command('stop')
    .description('Stop the running gateway daemon')
    .action(stopDaemon)

  daemon
    .command('status')
    .description('Check if the gateway daemon is running')
    .action(daemonStatus)
}

async function startDaemon(port?: number): Promise<void> {
  const spinner = ora('Starting Agent OS gateway...').start()

  // Check if already running
  try {
    const health = await gateway.health()
    spinner.succeed(
      `Gateway already running (v${health.version}, uptime: ${String(Math.floor(health.uptime_seconds))}s)`
    )
    return
  } catch (err) {
    if (!(err instanceof GatewayNotRunningError)) {
      spinner.fail('Unexpected error checking gateway status')
      display.error(String(err))
      process.exit(1)
    }
  }

  // The gateway runs in the user's project directory so resolve_agentos_dir()
  // finds their .agentos/ (not the one inside the agent-os source repo).
  const userProjectDir = process.cwd()

  const env = {
    ...process.env,
    AGENTOS_PORT: String(port ?? 47821),
    AGENTOS_LOG_LEVEL: 'info',
  }

  const logPath = join(userProjectDir, LOG_FILE)
  const pidPath = join(userProjectDir, PID_FILE)

  const proc = spawn(
    'python',
    ['-m', 'agentos_gateway'],
    {
      cwd: userProjectDir,
      env,
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  )

  // Write PID immediately
  writeFileSync(pidPath, String(proc.pid), 'utf-8')

  // Pipe output to log file
  const logStream = (await import('fs')).createWriteStream(logPath, { flags: 'a' })
  proc.stdout.pipe(logStream)
  proc.stderr.pipe(logStream)

  proc.unref() // Don't keep CLI process alive

  // Wait for gateway to become healthy (up to 10 seconds)
  const maxWait = 10_000
  const interval = 200
  let elapsed = 0

  while (elapsed < maxWait) {
    await new Promise((r) => setTimeout(r, interval))
    elapsed += interval
    try {
      const health = await gateway.health()
      spinner.succeed(`Gateway started (v${health.version}, port: ${String(port ?? 47821)})`)
      display.info(`Logs: ${logPath}`)
      display.info(`PID: ${String(proc.pid)}`)
      return
    } catch {
      // still starting up
    }
  }

  spinner.fail('Gateway did not start within 10 seconds')
  display.info(`Check logs: ${logPath}`)
  process.exit(1)
}

function stopDaemon(): void {
  const pidPath = join(process.cwd(), PID_FILE)

  if (!existsSync(pidPath)) {
    display.warning('No gateway PID file found. Is the daemon running?')
    process.exit(1)
  }

  const pid = parseInt(readFileSync(pidPath, 'utf-8').trim())

  try {
    process.kill(pid, 'SIGTERM')
    unlinkSync(pidPath)
    display.success(`Gateway stopped (PID: ${String(pid)})`)
  } catch (err: unknown) {
    if (err instanceof Error && 'code' in err && err.code === 'ESRCH') {
      display.warning(`Process ${String(pid)} not found — removing stale PID file`)
      unlinkSync(pidPath)
    } else {
      display.error(`Failed to stop gateway: ${String(err)}`)
      process.exit(1)
    }
  }
}

async function daemonStatus(): Promise<void> {
  const pidPath = join(process.cwd(), PID_FILE)
  const pidExists = existsSync(pidPath)

  try {
    const health = await gateway.health()
    display.success('Gateway is running')
    display.info(`Version: ${health.version}`)
    display.info(`Uptime: ${String(Math.floor(health.uptime_seconds))}s`)
    display.info(`Active missions: ${String(health.active_missions)}`)
    display.info(`Active agents: ${String(health.active_agents)}`)
    if (pidExists) {
      const pid = readFileSync(pidPath, 'utf-8').trim()
      display.info(`PID: ${pid}`)
    }
  } catch (err) {
    if (err instanceof GatewayNotRunningError) {
      display.error('Gateway is not running')
      if (pidExists) {
        display.warning('Stale PID file found. Run: agentos daemon start')
      }
      process.exit(1)
    }
    throw err
  }
}
