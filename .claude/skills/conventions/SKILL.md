---
name: conventions
description: Teaches Claude the exact coding conventions used in the Agent OS codebase (Python gateway + TypeScript CLI/shared + event system + Phase 3 stub pattern + file naming) so every new file matches the existing style perfectly. Use this skill whenever writing or editing any file in packages/gateway, packages/shared, or apps/cli, whenever adding a new router/service/adapter/command, whenever emitting an event, or whenever implementing a Phase 3 stub — even if the user just says "add an endpoint" or "write a new command" without mentioning conventions explicitly.
---

# Agent OS Coding Conventions

Load this skill before writing or editing code anywhere in this repo. The goal is zero-diff style: a reviewer should not be able to tell which files you wrote versus which were already there.

## Python (gateway)

- Every file starts with a module docstring explaining the file's role and design principles — not just a one-liner restating the filename.
- `from __future__ import annotations` is the first line of code in every file (after the docstring).
- Pydantic v2 models live in `models.py`. Use `Literal` types for fixed value sets, not Python `enum`.
- FastAPI routers are thin — they parse/validate the request and call a service class. No business logic in router functions.
- Service classes live in `services/`, one file per domain (e.g. `agent_registry.py`, `mission_planner.py`). If logic doesn't obviously belong to an existing service file, that's a signal to create a new one named after the domain, not to bolt it onto an unrelated one.
- All DB writes go through the `get_db()` async context manager from `database.py`. Never open a connection or cursor any other way.
- Events are emitted exclusively via `event_ledger.emit()`. Never write to the events table directly — `emit()` is what fans out to SQLite, JSONL, and SSE consistently.
- Credentials must never appear in logs, event payloads, or context packs. `_sanitize_payload()` is the mechanism that enforces this — route payloads through it rather than hand-rolling redaction.
- Primary keys are generated with `ulid.new()`. Never `uuid4()`, never DB autoincrement — ULIDs keep keys sortable by creation time.
- Timestamps are always ISO-8601 UTC strings: `datetime.now(UTC).isoformat()`. Never store `datetime` objects or naive/local timestamps.
- Adapters implement agent-specific behavior by overriding the class-level lists `FAILURE_SIGNATURES`, `RATE_LIMIT_SIGNATURES`, `QUOTA_EXCEEDED_SIGNATURES` — don't add ad hoc string-matching logic elsewhere.
- Adapters raise `CredentialFailure` when they detect a credential problem; the gateway (not the adapter) is responsible for catching it and driving recovery (fallback agent, retry, pause). Don't have an adapter try to handle recovery itself.

## TypeScript (CLI + shared)

- Strict TypeScript everywhere: no `any`. If you use `as` to cast, add a comment explaining why the cast is safe/necessary — an unexplained cast is a code smell here.
- All shared types come from `@agent-os/shared`. Never redefine a type locally that already exists there — if a type seems missing, add it to `packages/shared` (and mirror it in the Pydantic models per the gateway conventions) rather than duplicating it in the CLI.
- CLI output goes through `display.*` helpers exclusively. No raw `console.log` in command files — this keeps formatting (and future changes to it, like JSON output mode) centralized.
- All HTTP calls to the gateway go through `gateway.*` client methods in `gateway-client.ts`. Never call `fetch` directly from a command file — if the method you need doesn't exist yet, add it to `gateway-client.ts`.
- Command files are thin registration: define the command, wire flags/args, and call a separate action function. Keep the actual logic inside that action function, not inline in the `.action()` callback — it keeps command files skimmable and the logic testable in isolation.
- Catch gateway errors with `display.gatewayError(err)`, which already knows how to special-case `GatewayNotRunningError` and `GatewayError` (e.g. surfacing "run `agentos daemon start`"). Don't write bespoke catch blocks for these.

## Event system

- Every state change in the gateway emits an event via `event_ledger.emit()` — if you're mutating something a user or dashboard might care about, there should be a corresponding emit call right next to the write.
- `source` is always one of: an `agent_id`, `'gateway'`, or `'user'` — nothing else.
- `type` follows `domain.action` dot notation, e.g. `mission.created`, `credential.expired`, `workspace.frozen`. Pick the domain to match the entity being acted on, and keep the action a short verb/state.
- `payload` must never contain any of: `password`, `secret`, `token`, `api_key`, `credential`, `access_key`, `private_key`, `auth_token`, `bearer` (as keys or in a way that leaks the value) — this is exactly what `_sanitize_payload()` exists to guarantee, so route payloads through it.
- Events are append-only. Never update or delete an existing event row — if something needs correcting, emit a new event that reflects the correction.

## Phase 3 stub pattern

Many routers are currently stubbed pending Phase 3 implementation.

- Stubs raise `NotImplementedError("Phase 3")`. They do **not** return empty/fake data — the one exception is `GET /inbox`, which returns empty lists since an empty inbox is a legitimate real state, not a placeholder.
- When implementing a stub during Phase 3 work: replace the `raise` with the real call into the relevant service class, and add the service import at the top of the file. Don't leave the `NotImplementedError` in place "just in case," and don't build the implementation inline in the router — it still goes in `services/` per the conventions above.

## File naming

- Python: `snake_case.py`.
- TypeScript: `camelCase.ts` for lib files; `kebab-case.ts` is also acceptable for command files (match whichever convention the neighboring files in that directory already use).
- No `index.ts` files inside command directories — every command gets its own named file so it's discoverable by name alone.
