# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Agent OS is a personal control plane for orchestrating multiple AI coding agents (Claude Code, Codex, etc.) on a project: registering agents, planning/running "missions" composed of tasks, routing tasks between agents, approving risky actions, and monitoring credential health — all via a local-only FastAPI gateway, a CLI, and (eventually) a Next.js dashboard.

## Repo layout

pnpm workspace + a Python package, mixed in one monorepo:

- `apps/cli` — TypeScript CLI (`agentos` binary, Commander-based). All gateway access goes through `apps/cli/src/lib/gateway-client.ts`.
- `apps/dashboard` — Next.js dashboard (scaffolding only at present, no implementation yet).
- `packages/shared` — TypeScript types/constants/event shapes shared by the CLI and dashboard (`@agent-os/shared`).
- `packages/gateway` — Python/FastAPI backend (`agentos_gateway`), the actual orchestration engine. This is a separate Python package (uv/pip), not part of the pnpm workspace.

**Important:** `packages/gateway/src/agentos_gateway/models.py` (Pydantic) is meant to mirror `packages/shared/src/types.ts` exactly. When changing one, update the other.

## Commands

This is a polyglot repo: TypeScript via pnpm, Python via pip, run independently.

### TypeScript (apps/cli, apps/dashboard, packages/shared) — from repo root

```
pnpm install            # install all workspace deps
pnpm build              # build all packages (pnpm -r build)
pnpm dev                # run all packages in dev/watch mode
pnpm typecheck          # tsc --noEmit across workspace
pnpm lint               # eslint across workspace
pnpm test               # vitest across workspace
```

Per-package (run inside `apps/cli`, etc., or with `pnpm --filter`):

```
pnpm --filter @agent-os/cli test           # run all CLI tests (vitest)
pnpm --filter @agent-os/cli test -- <pattern>  # run a single test file/name
pnpm --filter @agent-os/cli build          # tsc build for just the CLI
```

`packages/shared` must be built (`pnpm --filter @agent-os/shared build`) before the CLI/dashboard can resolve `@agent-os/shared` import types, since it's consumed via its built `dist/` output, not source.

### Python (packages/gateway) — from `packages/gateway/`

```
pip install -e ".[dev]"               # install gateway + dev deps
ruff check src/                       # lint
mypy src/                             # typecheck (strict mode)
pytest tests/ -v                      # run all tests
pytest tests/test_foo.py::test_bar -v # run a single test
python -m agentos_gateway             # run the gateway directly (port 47821)
```

CI (`.github/workflows/ci.yml`) runs TS lint+typecheck+build, Python ruff+mypy+pytest, and a trufflehog secrets scan as three independent jobs — mirror these locally before pushing.

## Architecture

### The gateway is the source of truth; the CLI is a thin HTTP client

The CLI never touches agent processes, the database, or the filesystem state directly — every command in `apps/cli/src/commands/*` calls into `apps/cli/src/lib/gateway-client.ts`, which talks to the FastAPI gateway over HTTP (`localhost:47821` by default, see `GATEWAY_BASE_URL` in `packages/shared/src/constants.ts`). If the gateway isn't running, `gateway-client.ts` throws `GatewayNotRunningError`, and CLI commands surface "run `agentos daemon start`". When adding a CLI command, add the corresponding method to `gateway-client.ts` rather than calling `fetch` ad hoc.

### `.agentos/` project directory

Gateway state lives in a per-project `.agentos/` directory, resolved by walking up from CWD until one is found (`resolve_agentos_dir` in `main.py`), falling back to creating one in CWD. It contains the SQLite DB, `runs/`, `artifacts/`, `approvals/`, `context/`, `missions/`, `workspaces/`, and config files (`config.yml`, `agents.yml`, `policies.yml`) — see `packages/shared/src/constants.ts` for the canonical path list (TS) and `main.py`/`database.py` (Python) for where the gateway creates/reads them.

### Adapter pattern for agent integrations

Every supported coding agent (Claude Code, Codex, a mock for testing) is wrapped by a `BaseAdapter` subclass in `packages/gateway/src/agentos_gateway/adapters/`. The gateway core only ever calls the abstract interface (`start_session` → `send_task` → `stream_events` → `submit_result` → `stop`) defined in `adapters/base.py` — it never branches on agent type. Adapters are responsible for translating agent-specific CLI invocation and output parsing into normalized `AgentOutput` events, and for detecting credential/auth failures by scanning output against class-level `FAILURE_SIGNATURES` / `RATE_LIMIT_SIGNATURES` / `QUOTA_EXCEEDED_SIGNATURES`, raising `CredentialFailure` so the gateway can trigger recovery (fallback agent, retry, or pausing the task). Adding a new agent means adding a new adapter, not touching router/orchestration code.

### Risky-action approval flow

Adapters can call `request_action(...)`, which blocks until the gateway resolves an approval. Risk scoring thresholds (`RISK_AUTO_APPROVE`, `RISK_ASK_USER`, `RISK_BLOCK` in `constants.ts`, mirrored in `policies.yml`) determine whether an action is auto-approved, surfaced to the user's inbox (`/inbox` router, `agentos inbox` CLI command), or blocked outright. Approvals/denials flow through `routers/inbox.py`.

### Missions → Tasks → Runs

A **mission** (high-level objective) is planned into a DAG of **tasks** (`depends_on`, `assigned_agent_id`, `risk_level`), each task executes as one or more **runs** against an isolated Git worktree (`workspace_strategy: "git_worktree"` — currently the only supported strategy). Tasks can be rerouted to a different agent mid-flight (`routers/tasks.py`'s route/diff/merge/rollback endpoints), which hands off via a `ContextPack` (see `routers/context.py` / `BuildContextPackRequest`) rather than raw chat history.

### Events as the cross-cutting observability layer

All state changes (gateway startup, agent status changes, credential events, mission/task transitions) are emitted through `events.py`'s `event_ledger` and exposed both as a queryable list (`routers/events.py`) and as a live SSE stream (`sse.py`, `SSEManager`, closed on shutdown via `sse_manager.close_all()`). The CLI's `eventsource` dependency and the dashboard are expected to consume this stream rather than polling.

## Conventions

- TypeScript: strict mode with `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride` all on (`tsconfig.base.json`) — write code that satisfies these, don't relax them. No Prettier semicolons, single quotes (`.prettierrc`).
- Python: `mypy --strict`, ruff with `E, F, I, N, W, UP, B, SIM` rule sets, 100-char line length, `from __future__ import annotations` at the top of modules.
- The gateway binds to `127.0.0.1` only and CORS is restricted to localhost dashboard/dev ports (47822, 3000) — this is intentionally never exposed externally; don't widen it.
