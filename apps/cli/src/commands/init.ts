/**
 * commands/init.ts — `agentos init`
 *
 * Creates the .agentos/ directory structure in the current repo.
 * Idempotent — safe to run multiple times.
 */

import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import type { Command } from 'commander'
import chalk from 'chalk'
import { display } from '../lib/display.js'

const AGENTOS_DIR = '.agentos'

export function registerInitCommand(program: Command): void {
  program
    .command('init')
    .description('Initialize Agent OS in the current repository')
    .option('--force', 'Overwrite existing configuration files')
    .action((opts: { force: boolean }) => {
      runInit(opts.force)
    })
}

function runInit(force = false): void {
  const cwd = process.cwd()

  // Verify we're in a Git repo
  if (!existsSync(join(cwd, '.git'))) {
    display.warning('No .git directory found. Agent OS works best inside a Git repository.')
    console.log(chalk.grey('  Continuing anyway...\n'))
  }

  console.log(chalk.bold('\n  Initializing Agent OS\n'))

  const dirs = [
    AGENTOS_DIR,
    join(AGENTOS_DIR, 'context'),
    join(AGENTOS_DIR, 'context', 'api-contracts'),
    join(AGENTOS_DIR, 'context', 'decisions'),
    join(AGENTOS_DIR, 'missions'),
    join(AGENTOS_DIR, 'runs'),
    join(AGENTOS_DIR, 'artifacts'),
    join(AGENTOS_DIR, 'approvals'),
    join(AGENTOS_DIR, 'workspaces'),
    join(AGENTOS_DIR, '.secrets'),
  ]

  for (const dir of dirs) {
    mkdirSync(join(cwd, dir), { recursive: true })
    console.log(chalk.grey(`  created  `) + dir + '/')
  }

  // Write starter files
  const files: { path: string; content: string }[] = [
    {
      path: join(AGENTOS_DIR, 'config.yml'),
      content: CONFIG_YML,
    },
    {
      path: join(AGENTOS_DIR, 'agents.yml'),
      content: AGENTS_YML,
    },
    {
      path: join(AGENTOS_DIR, 'policies.yml'),
      content: POLICIES_YML,
    },
    {
      path: join(AGENTOS_DIR, 'context', 'product.md'),
      content: PRODUCT_MD,
    },
    {
      path: join(AGENTOS_DIR, 'context', 'architecture.md'),
      content: ARCHITECTURE_MD,
    },
    {
      path: join(AGENTOS_DIR, 'context', 'constraints.md'),
      content: CONSTRAINTS_MD,
    },
    {
      path: join(AGENTOS_DIR, 'context', 'glossary.md'),
      content: GLOSSARY_MD,
    },
    {
      path: join(AGENTOS_DIR, '.gitignore'),
      content: GITIGNORE,
    },
  ]

  for (const file of files) {
    const fullPath = join(cwd, file.path)
    if (existsSync(fullPath) && !force) {
      console.log(chalk.grey(`  exists   `) + file.path)
    } else {
      writeFileSync(fullPath, file.content, 'utf-8')
      console.log(chalk.green(`  created  `) + file.path)
    }
  }

  console.log('\n' + chalk.bold.green('  ✓ Agent OS initialized\n'))
  console.log(chalk.grey('  Next steps:'))
  console.log(chalk.grey('    1. Edit .agentos/context/product.md with your project description'))
  console.log(chalk.grey('    2. Run: ') + chalk.white('agentos daemon start'))
  console.log(chalk.grey('    3. Run: ') + chalk.white('agentos agent add claude --role frontend'))
  console.log()
}

// ============================================================
// STARTER FILE TEMPLATES
// ============================================================

const CONFIG_YML = `# Agent OS configuration
# This file is committed to your repository.
version: "1"

gateway:
  port: 47821
  log_level: info

dashboard:
  port: 47822

context:
  token_budget_default: 8000
  token_budget_max: 16000

workspace:
  base_path: .agentos/workspaces
  strategy: git_worktree
`

const AGENTS_YML = `# Registered agents
# This file is committed to your repository.
# Credentials are stored in the OS keychain — never here.
version: "1"
agents: []
`

const POLICIES_YML = `# Risk policies for Agent OS
# This file is committed to your repository.
version: "1"

risk_thresholds:
  auto_approve: 30
  ask_user: 70
  block: 100

sensitive_paths:
  - ".env*"
  - "**/secrets/**"
  - "infra/**"
  - "db/migrations/**"
  - "auth/**"
  - "payments/**"

dangerous_commands:
  - "rm -rf"
  - "drop database"
  - "git push --force"
  - "curl * | sh"
  - "chmod -R 777"
  - "npm publish"
  - "vercel --prod"
  - "supabase db push --linked"

auto_approve_patterns:
  - "npm test"
  - "pytest"
  - "git status"
  - "git diff"
`

const PRODUCT_MD = `# Product Context

<!-- 
  Fill this in with your project's product context.
  This document is injected into every agent's context pack.
-->

## What this product is
[Describe what the app does and who it serves]

## Target users
[Who uses this app and what are their main goals]

## Key features
[List the main features]

## Current status
[What phase of development are you in]
`

const ARCHITECTURE_MD = `# Architecture Context

<!--
  Fill this in with your project's technical architecture.
  Agents use this to understand the codebase structure and conventions.
-->

## Stack
[List your tech stack]

## Folder structure
[Describe your key directories]

## Conventions
[List coding conventions, naming patterns, etc.]

## Key dependencies
[List important libraries and why they're used]
`

const CONSTRAINTS_MD = `# Agent Constraints

<!--
  Hard rules that ALL agents must follow, regardless of task.
  These are injected into every context pack.
-->

## Always
- Follow existing code style and conventions
- Write tests for new functionality
- Keep changes within your assigned scope
- Commit working code only — no broken states

## Never
- Modify files outside your assigned scope
- Remove existing tests
- Change configuration that affects other agents' work
- Deploy to production without explicit approval
`

const GLOSSARY_MD = `# Project Glossary

<!--
  Define project-specific terms agents need to understand.
  Add entries as your project develops domain-specific vocabulary.
-->

| Term | Definition |
|------|-----------|
| [term] | [definition] |
`

const GITIGNORE = `# Agent OS — never commit these
.secrets/
runs/
workspaces/
approvals/
*.db
*.db-wal
*.db-shm
`
