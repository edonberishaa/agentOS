# Changelog

All notable changes to Agent OS are documented here.

## v0.1.0 — 2026-06-25

First release. Agent registry, mission planning, workspace isolation, credential lifecycle management, the approval engine, conflict detection, a CLI covering the full feature set, a dashboard MVP, and documentation, all running locally against a FastAPI gateway.

### Added

- **Gateway** (`packages/gateway`): FastAPI backend, SQLite-backed state, `EventLedger` (SQLite + JSONL + SSE fan-out), agent registry with PATH/keychain health probing, Anthropic-backed mission planner that decomposes an objective into a task DAG, `WorkspaceManager` for Git-worktree-based task isolation, `CredentialManager` for OS-keychain credential storage and failure recovery (workspace freeze, fallback routing, bounded rate-limit retry), `ApprovalEngine` for deterministic risk scoring and the approve/deny flow, `ConflictDetector` for file-overlap/API-contract/migration conflict checks, and a `GET /missions` list endpoint.
- **CLI** (`apps/cli`): full command set — `init`, `daemon`, `agent add/remove`, `agents`/`agents health`, `mission create/plan/run/pause`, `diff`/`merge`/`rollback`/`route`, `inbox`/`approve`/`deny`/`conflicts`, `context add/show`, `logs` (with `--follow` SSE streaming), `credentials rotate`, and `status`.
- **Dashboard** (`apps/dashboard`): Next.js App Router MVP — Mission Control home, Approval Inbox, Mission Timeline, and Agent Health pages, all reading the gateway over HTTP and SSE.
- **Adapters** (`packages/gateway/src/agentos_gateway/adapters`): `ClaudeCodeAdapter`, `CodexAdapter`, `MockAdapter` behind a shared `BaseAdapter` interface; `docs/adapters.md` documents how to add a new one.
- **Tests**: 16 integration/security/performance tests in `packages/gateway/tests/`, covering the 5 core scenarios (full mission loop, credential failure handoff, conflict detection, rollback, gateway restart) plus a security checklist and performance baselines.
- **Docs**: `README.md`, `docs/architecture.md`, `docs/cli-reference.md`, `docs/adapters.md`.

### Fixed

- All pre-existing `ruff` findings across the gateway source tree (`UP035`, `N818`, `E501`, `SIM105`, `UP041`, `I001`) — `ruff check` now returns zero findings on the full tree. `CredentialFailure` was renamed to `CredentialFailureError` to satisfy `N818` (exception naming convention), updated consistently across adapters, `credential_manager.py`, and tests.
- Router-embedded SQL and orchestration logic moved into owning service classes (`MissionService.run_mission`, `ApprovalEngine.list_pending`, `CredentialManager.list_unresolved`/`route_credential_event`, `ConflictDetector.list_active`, `RunManager.get_pack`) — no router in the gateway contains a direct DB query or SQL string.

### Known limitations (deferred past v0.1.0)

- **No agent process spawner.** `mission run` creates worktrees and run bookkeeping but does not yet invoke a real agent subprocess; `ApprovalEngine.intercept()` and the credential rate-limit retry are wired but never called by a live process. This is the largest piece of work for the next release.
- **No orphan-run detection** in the inbox — blocked on the spawner above existing at all.
- No shell autocomplete for the CLI.
- Windows is not a supported runtime target beyond local development (the gateway and CLI work here, but this hasn't been validated as a release target).
