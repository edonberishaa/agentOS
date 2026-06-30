---
name: architecture
description: Gives Claude instant, complete context on the Agent OS system architecture — project identity, monorepo layout, communication model, key architectural decisions, core entities, ports, and current build status — so it never needs re-explaining. Use this skill whenever working anywhere in the Agent OS repo (packages/gateway, packages/shared, apps/cli, apps/dashboard), whenever the user asks "how does Agent OS work," "why did we choose X over Y," what port something runs on, where an entity is defined, what's in .agentos/, or whenever you're about to touch cross-cutting concerns like the gateway-CLI boundary, SSE events, adapters, or credential storage and need to confirm you're not violating an existing design decision.
---

# Agent OS Architecture

Load this skill any time you're working in this repo and need architectural grounding — it replaces having to re-derive or re-ask about system design.

## Project identity

Agent OS is a **local-first personal control plane for AI coding agents** (Claude Code, Codex). One developer orchestrates multiple coding agents from a single CLI and dashboard — registering agents, planning/running missions, routing tasks between agents, approving risky actions, monitoring credential health.

Stack:
- **Gateway**: FastAPI (Python) — the orchestration engine and single source of truth
- **CLI**: TypeScript, Commander.js
- **Dashboard**: Next.js (App Router)
- **DB**: SQLite via `aiosqlite`
- **Workspace isolation**: Git worktrees (one per task)
- **Credentials**: OS keychain via `keyring` — never SQLite, logs, or context packs
- **Repo**: pnpm monorepo (TS side) + a separate Python package (gateway)

## Monorepo structure

```
packages/gateway/   FastAPI Python backend — the only process that writes to SQLite
packages/shared/    TS types, discriminated-union events, constants — mirrors gateway's models.py
apps/cli/           TS CLI — talks to the gateway over HTTP only, never touches SQLite directly
apps/dashboard/     Next.js App Router — reads gateway via SSE + HTTP
```

`packages/gateway/src/agentos_gateway/models.py` (Pydantic) mirrors `packages/shared/src/types.ts` exactly — changing one means updating the other.

## Communication model

- CLI → Gateway: HTTP, `localhost:47821`
- Dashboard → Gateway: subscribes to SSE at `GET /events/stream`, plus HTTP for everything else
- Gateway → Agent CLIs: spawned via Python `subprocess` + `ptyprocess`
- **All writes go through the gateway.** Nothing else touches the SQLite DB or filesystem state directly.

Ports — both bind to **127.0.0.1 only**, never exposed externally:
- Gateway: **47821**
- Dashboard: **47822**

## Key architectural decisions (and why)

| Decision | Why |
|---|---|
| FastAPI over Node.js for the gateway | Async by default, SSE is first-class, Python's subprocess ecosystem suits spawning/managing agent CLIs |
| SQLite over PostgreSQL | Local-first, zero config, single user — no need for a server-backed DB |
| Git worktrees over Docker | No Docker dependency for v1; each task gets its own branch, `ag/{task_id}/{agent_id}` |
| OS keychain (`keyring`) for credentials | Credentials must never land in SQLite, logs, or context packs — keychain keeps them out of anything that gets persisted or shipped between agents |
| SSE over WebSockets | The dashboard is read-mostly; SSE is sufficient and avoids the complexity of bidirectional channels |

When a change would cut against one of these (e.g., writing credentials to a context pack, having the CLI query SQLite directly, introducing a second writer to the DB), treat that as a design violation worth flagging, not a detail to quietly work around.

## Core entities

All defined in `packages/shared/src/types.ts` (and mirrored in `packages/gateway/src/agentos_gateway/models.py`):

`Agent`, `Mission`, `Task`, `AgentRun`, `AgentMessage`, `ContextPack`, `ActionRequest`, `Approval`, `CredentialEvent`, `Event`

Relationship: a **Mission** is planned into a DAG of **Tasks**; each Task executes as one or more **AgentRuns** against an isolated Git worktree. Tasks can be rerouted to a different agent mid-flight, handing off via a **ContextPack** rather than raw chat history. Risky actions go through an **ActionRequest** → **Approval** flow. All state transitions emit an **Event**.

## The `.agentos/` directory

Lives inside the user's project repo, resolved by walking up from CWD until found (falls back to creating one in CWD).

Gitignored (contains local/sensitive/ephemeral state): `.secrets/`, `runs/`, `workspaces/`, `approvals/`, `*.db` files.

Everything else (config, schema-relevant non-secret state) is committed.

## Current build status: Phase 2 foundation complete

- Monorepo scaffold
- SQLite schema
- FastAPI skeleton with `/health`
- `EventLedger` (SQLite + JSONL + SSE)
- `SSEManager`
- `BaseAdapter` ABC, plus `ClaudeCodeAdapter`, `CodexAdapter`, `MockAdapter`
- `agentos init`
- `agentos daemon start/stop/status`
- GitHub Actions CI

Treat anything beyond this list (mission planning/execution, task routing, the inbox/approval UI, the dashboard itself) as not yet built — don't assume it exists just because it's described in design docs.
