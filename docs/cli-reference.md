# CLI Reference

All commands talk to the gateway over HTTP at `http://localhost:47821` by default. If the gateway isn't running, every command fails fast with `Agent OS gateway is not running. Start it with: agentos daemon start` rather than hanging.

## `agentos init`

Scaffolds `.agentos/` in the current directory: config files (`config.yml`, `agents.yml`, `policies.yml`), context-vault starter docs (`product.md`, `architecture.md`, `constraints.md`, `glossary.md`), and the runtime subdirectories (`runs/`, `workspaces/`, `approvals/`, `.secrets/`). Idempotent — safe to run again.

```
agentos init [--force]
```

- `--force` — overwrite existing config files instead of skipping them.

```bash
agentos init
agentos init --force   # re-scaffold, overwriting edited config files
```

## `agentos daemon`

Manages the gateway process lifecycle. The gateway runs as a detached background process; its PID is tracked at `.agentos/gateway.pid` and its output logged to `.agentos/gateway.log`.

```
agentos daemon start [--port <port>]
agentos daemon stop
agentos daemon status
```

```bash
agentos daemon start
agentos daemon start --port 48000
agentos daemon status
agentos daemon stop
```

## `agentos agent add <name>`

Registers a new agent. Stores the agent's display name, adapter, command, role, and capabilities. Does **not** store a credential — use `agentos credentials rotate` for that.

```
agentos agent add <name> [--role <role>] [--cmd <command>] [--adapter <adapter>] [--capability <cap>] [--fallback <agent_id>]
```

- `--role <role>` — the agent's role label (e.g. `frontend`, `backend`).
- `--cmd <command>` — the CLI command to invoke (e.g. `claude`, `codex`). Defaults to `<name>`.
- `--adapter <adapter>` — which adapter wraps this agent: `claude-code` | `codex` | `mock`. Defaults to `claude-code`.
- `--capability <cap>` — repeatable; a capability tag the planner uses when assigning tasks.
- `--fallback <agent_id>` — an agent to route tasks to automatically on credential failure.

```bash
agentos agent add claude --role frontend --cmd claude --adapter claude-code --capability react --capability css
agentos agent add codex --role backend --cmd codex --adapter codex --fallback ag_01HZX...
```

## `agentos agent remove <id>`

Unregisters an agent.

```bash
agentos agent remove ag_01HZX9K2J3...
```

## `agentos agents`

Lists all registered agents with status, capabilities, and last health check.

```bash
agentos agents
```

## `agentos agents health`

Per-agent credential health: credential type, last validated timestamp, and configured fallback agent.

```bash
agentos agents health
```

## `agentos mission create <objective>`

Creates a mission. Prints the new mission's id — you'll need it for every subsequent mission/task command.

```
agentos mission create <objective> [--title <title>]
```

- `--title <title>` — explicit mission title. If omitted, derived from the objective.

```bash
agentos mission create "Add dark mode toggle to the settings page"
agentos mission create "Migrate the billing service to Stripe" --title "Stripe migration"
```

## `agentos mission plan <mission_id>`

Calls the planner, which decomposes the mission's objective into a task DAG via the Anthropic API. Prints each task's title, assigned agent, dependencies, and risk level. Requires `ANTHROPIC_API_KEY` to be set in the gateway's environment.

```bash
agentos mission plan ms_01HZX9K2J3...
```

## `agentos mission run <mission_id>`

Starts every ready task (dependencies satisfied, agent assigned): creates a Git worktree per task and streams live events until the mission's status changes from `running`.

```
agentos mission run <mission_id> [--parallel]
```

- `--parallel` — run independent tasks concurrently rather than strictly in dependency order.

```bash
agentos mission run ms_01HZX9K2J3... --parallel
```

## `agentos mission pause <mission_id>`

Pauses a running mission. **Deferred feature** — the gateway endpoint currently returns a not-implemented error pending the process-spawner work; the command surfaces that error clearly rather than silently no-opping.

```bash
agentos mission pause ms_01HZX9K2J3...
```

## `agentos diff <task_id>`

Shows the diff of a task's worktree against its base branch, with additions in green and removals in red.

```bash
agentos diff tk_01HZX9K2J3...
```

## `agentos merge <task_id>`

Merges a task's branch into the base branch. Fails if the main repo isn't currently checked out on the base branch — Agent OS will not switch your active branch for you.

```bash
agentos merge tk_01HZX9K2J3...
```

## `agentos rollback <task_id>`

Discards a task's work and restores the last clean state on its branch.

```bash
agentos rollback tk_01HZX9K2J3...
```

## `agentos route <task_id> --to <agent_id>`

Hands a task off to a different agent, generating a sanitized context pack (prior messages + diff) for the new agent to pick up from.

```bash
agentos route tk_01HZX9K2J3... --to ag_02HZX9K2J3...
```

## `agentos inbox`

Renders all three inbox sections: pending action requests (with risk badges), unresolved credential events, and active conflicts.

```bash
agentos inbox
```

## `agentos approve <id>`

Approves a pending action request.

```
agentos approve <id> [--once | --mission | --always]
```

- `--once` (default) — approve this single occurrence.
- `--mission` — approve for the remainder of the current mission.
- `--always` — add a standing auto-approve pattern to `policies.yml`.

```bash
agentos approve ar_01HZX9K2J3... --once
agentos approve ar_01HZX9K2J3... --always
```

## `agentos deny <id>`

Denies a pending action request.

```bash
agentos deny ar_01HZX9K2J3...
```

## `agentos conflicts`

Lists active conflicts (file overlap, API contract mismatches, colliding migrations) — the conflicts section of the inbox, on its own.

```bash
agentos conflicts
```

## `agentos context add <file>`

Copies a file into `.agentos/context/` for inclusion in future context packs. Purely local — no gateway call.

```bash
agentos context add docs/api-design.md
```

## `agentos context show <pack_id>`

Shows a previously generated context pack's document list and token usage. Note: this takes a context-pack id, not a task id — there is no gateway endpoint to look up a pack by task, only by its own id (returned by `agentos route`/`agentos mission run` events).

```bash
agentos context show cp_01HZX9K2J3...
```

## `agentos logs`

Streams or lists event history.

```
agentos logs [--follow] [--run <run_id>] [--mission <mission_id>]
```

- `--follow` — keep streaming live events (SSE) instead of fetching and exiting.
- `--run <run_id>` — filter to a single run.
- `--mission <mission_id>` — filter to a single mission.

Without `--follow`, fetches the last 50 matching events and exits. Each line is formatted as `[timestamp] [severity] source: type payload`.

```bash
agentos logs
agentos logs --follow --mission ms_01HZX9K2J3...
```

## `agentos credentials rotate <agent_id>`

Prompts for a new credential value (hidden input) and stores it in the OS keychain, then re-validates the agent.

```bash
agentos credentials rotate ag_01HZX9K2J3...
```

## `agentos status`

Shortcut: gateway health summary plus the registered-agent table.

```bash
agentos status
```
