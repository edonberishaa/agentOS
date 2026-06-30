# Agent OS

A local-first personal control plane for orchestrating AI coding agents — Claude Code, Codex, and whatever you wrap next.

**You're not using AI anymore. You're running a team.**

Agent OS turns "ask an AI to write some code" into "plan a mission, assign tasks to agents, watch them work in isolated Git worktrees, approve the risky parts, and merge what's good." One developer, multiple agents, one control plane, all running on your machine.

## Prerequisites

- Node.js >= 20
- Python >= 3.11
- pnpm >= 9
- git

## Installation

```bash
git clone <your-fork-url> agent-os
cd agent-os

# TypeScript side: CLI, dashboard, shared types
pnpm install
pnpm --filter @agent-os/shared build
pnpm build

# Python side: the gateway
cd packages/gateway
pip install -e ".[dev]"
cd ../..

# Make `agentos` available globally
cd apps/cli
pnpm link --global
cd ../..
```

Set your Anthropic API key if you want mission planning (the planner calls `claude-sonnet-4-6` to decompose an objective into a task DAG):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Quickstart

```bash
# 1. Initialize Agent OS in your project repo
cd your-project
agentos init

# 2. Start the gateway daemon
agentos daemon start

# 3. Register an agent (Claude Code, in this example)
agentos agent add claude --role frontend --cmd claude --adapter claude-code

# 4. Store its credential in your OS keychain
agentos credentials rotate <agent_id>

# 5. Create a mission
agentos mission create "Add dark mode toggle to settings page"

# 6. Plan it — the planner decomposes it into a task DAG
agentos mission plan <mission_id>

# 7. Run it — tasks start, worktrees get created, agents go to work
agentos mission run <mission_id> --parallel

# 8. Watch it happen
agentos logs --follow --mission <mission_id>
# or open the dashboard at http://localhost:47822
```

When a task finishes, review it with `agentos diff <task_id>` and merge with `agentos merge <task_id>`. If an agent's credential expires mid-run, check `agentos inbox` — failed runs get frozen, not lost.

## Command reference

| Command | Description | Example |
|---|---|---|
| `agentos init` | Scaffold `.agentos/` in the current repo | `agentos init` |
| `agentos daemon start\|stop\|status` | Manage the gateway process | `agentos daemon start` |
| `agentos agent add <name>` | Register a new agent | `agentos agent add claude --role backend` |
| `agentos agent remove <id>` | Unregister an agent | `agentos agent remove ag_01...` |
| `agentos agents` | List registered agents | `agentos agents` |
| `agentos agents health` | Credential health per agent | `agentos agents health` |
| `agentos mission create <objective>` | Create a mission | `agentos mission create "Fix login bug"` |
| `agentos mission plan <id>` | Decompose into a task DAG | `agentos mission plan ms_01...` |
| `agentos mission run <id>` | Start ready tasks | `agentos mission run ms_01... --parallel` |
| `agentos mission pause <id>` | Pause a running mission | `agentos mission pause ms_01...` |
| `agentos diff <task_id>` | View an agent's changes | `agentos diff tk_01...` |
| `agentos merge <task_id>` | Merge approved work | `agentos merge tk_01...` |
| `agentos rollback <task_id>` | Discard a task's work | `agentos rollback tk_01...` |
| `agentos route <task_id> --to <agent_id>` | Hand a task to another agent | `agentos route tk_01... --to ag_02...` |
| `agentos inbox` | Pending approvals, credential events, conflicts | `agentos inbox` |
| `agentos approve <id>` | Approve a pending action | `agentos approve ar_01... --once` |
| `agentos deny <id>` | Deny a pending action | `agentos deny ar_01...` |
| `agentos conflicts` | List active conflicts | `agentos conflicts` |
| `agentos context add <file>` | Add a doc to the context vault | `agentos context add docs/api.md` |
| `agentos context show <pack_id>` | Inspect a handoff context pack | `agentos context show cp_01...` |
| `agentos logs [--follow]` | Stream or view event history | `agentos logs --follow` |
| `agentos credentials rotate <agent_id>` | Rotate a stored credential | `agentos credentials rotate ag_01...` |

Run `agentos <command> --help` for full flag documentation on any command.

## Architecture

```
 ┌──────────┐        HTTP/SSE        ┌──────────────────┐
 │   CLI    │ ─────────────────────> │                  │
 │ (agentos)│ <───────────────────── │   FastAPI        │
 └──────────┘                        │   Gateway        │
                                      │  (127.0.0.1      │
 ┌──────────┐        HTTP/SSE        │   :47821)        │
 │Dashboard │ ─────────────────────> │                  │
 │(Next.js) │ <───────────────────── │  source of truth │
 └──────────┘                        └─────────┬────────┘
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          │                     │                     │
                    ┌─────▼─────┐        ┌──────▼──────┐       ┌──────▼──────┐
                    │  SQLite   │        │  .agentos/  │       │  Adapters   │
                    │ (state,   │        │  (worktrees,│       │ (translate  │
                    │  events)  │        │  context,   │       │  gateway <->│
                    └───────────┘        │  policies)  │       │  agent CLI) │
                                          └─────────────┘       └──────┬──────┘
                                                                        │
                                                                 ┌──────▼──────┐
                                                                 │ Agent CLIs  │
                                                                 │ (claude,    │
                                                                 │  codex, …)  │
                                                                 └─────────────┘
```

The gateway is the only process that writes to SQLite. The CLI and dashboard are thin clients — every command goes over HTTP, every live update comes over SSE. Adapters are the only thing that knows how to actually talk to a given agent's CLI; the gateway core never branches on agent type.

## Further reading

- [`docs/architecture.md`](docs/architecture.md) — system design, the five core decisions, data flow, event system, security model
- [`docs/cli-reference.md`](docs/cli-reference.md) — every command, every flag, with examples
- [`docs/adapters.md`](docs/adapters.md) — how to wrap a new agent CLI
