---
name: testing
description: Defines the testing strategy, fixture patterns, MockAdapter usage, and Phase 4 test scenarios/checklists for Agent OS so every test written follows the same conventions and lands in the right location. Use this skill whenever writing or editing a test file in packages/gateway/tests or apps/cli/src/__tests__, whenever the user asks to "add a test," "write tests for X," asks about test coverage, mentions MockAdapter, or is working on Phase 4 integration/security/performance testing.
---

# Agent OS Testing

Load this skill before writing any test in this repo. The goal is that a new test file is indistinguishable in style from the ones already there, and that you're testing the right thing for the current phase.

## Test stack

- **Python (gateway)**: pytest + pytest-asyncio, `httpx.AsyncClient` for endpoint tests, an in-memory/temp-file aiosqlite DB per test for isolation
- **TypeScript (CLI)**: vitest
- CI runs both suites on every push via GitHub Actions

## Python test setup pattern

Use this fixture pattern in every gateway test file — it gives each test a fresh, isolated DB and a client wired to the real FastAPI app (via ASGI transport, no real network):

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from agentos_gateway.main import app
from agentos_gateway.database import init_db, set_db_path
from pathlib import Path

@pytest_asyncio.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    yield db_path

@pytest_asyncio.fixture
async def client(db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

Reuse these two fixtures rather than rewriting variants per file — if a test needs something extra (seeded rows, a specific adapter), layer an additional fixture on top of `db`/`client` instead of duplicating the setup.

## MockAdapter usage in tests

`MockAdapter` exists specifically so agent-related tests can run in CI without real API keys or spawning real processes.

```python
from agentos_gateway.adapters.mock import MockAdapter

adapter = MockAdapter(config, outcome="credential_expired")
```

Available `outcome` values: `"success"`, `"fail"`, `"credential_expired"`, `"quota_exceeded"`, `"rate_limited"`.

Any test that exercises agent run behavior — success paths, failure handling, credential recovery — should drive it through `MockAdapter` with the matching outcome rather than mocking at a lower level. This keeps tests exercising the real adapter interface (`start_session` → `send_task` → `stream_events` → `submit_result` → `stop`) instead of bypassing it.

## Phase 4 integration test scenarios

These are end-to-end scenarios to implement once Phase 4 (Integration, Testing & Hardening) starts — not before, since they depend on Phase 3 features (mission planning, workspace isolation, credential lifecycle, approval engine, conflict detection) actually existing. Check the `phase-tracker` skill if unsure whether a prerequisite is built yet.

1. Full mission loop: 2 agents running in parallel, one approval required, successful merge
2. Credential failure mid-run: workspace gets frozen, fallback agent gets routed, mission still completes
3. Conflict detection: two agents target the same file, a conflict card gets raised
4. Rollback: an agent produces bad output, the user rolls back cleanly
5. Gateway restart mid-mission: state is recovered from SQLite, orphaned runs are surfaced rather than silently lost

## Security test checklist (Phase 4)

- No credential value appears in any event payload — verify `_sanitize_payload()` actually strips them, don't just assert the function exists
- `.secrets/` is gitignored in every `agentos init` scenario (fresh repo, existing repo, nested repo)
- Dangerous commands are blocked for every entry in the `policies.yml` blocklist — test each entry, not just one representative example
- Sensitive paths trigger the correct risk score, matching the thresholds in `constants.ts`/`policies.yml`
- The context pack builder strips content matching secret patterns before a pack is ever handed to another agent

## Performance targets (Phase 4)

Validate these as actual assertions, not just informal observation:

| Target | Threshold |
|---|---|
| Gateway startup | < 500ms |
| SSE event delivery | < 100ms |
| SQLite queries | < 50ms |
| Context pack generation | < 3s |
| Credential failure detection | < 5s |

## File locations and naming

- Python tests: `packages/gateway/tests/`, named `test_{feature}.py`
- TypeScript tests: `apps/cli/src/__tests__/`, named `{feature}.test.ts`

Match an existing test's feature name to the source file it covers (e.g. `mission_planner.py` → `test_mission_planner.py`) so tests stay easy to locate by name alone.
