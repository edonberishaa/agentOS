"""
tests/conftest.py — Shared fixtures for the gateway integration test suite.

Every test gets its own temp Git repo (so `WorkspaceManager.find_repo()` can
locate it via `search_parent_directories=True`) and its own temp SQLite DB —
no test shares state with another, and nothing here touches the developer's
real `.agentos/` directory.

The `db` fixture deliberately places the SQLite file at
`<repo>/.agentos/agent_os.db` — the exact path `main.py`'s real `lifespan()`
computes from `resolve_agentos_dir()`. This isn't just tidiness: it's load-
bearing for `test_gateway_restart.py`, which drives the real lifespan
startup/shutdown directly and needs it to resolve to the same DB file the
rest of the test already wrote to.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from agentos_gateway.database import init_db
from agentos_gateway.events import set_runs_dir
from agentos_gateway.main import app

# Mirrors apps/cli/src/commands/init.ts's POLICIES_YML template exactly —
# the canonical default policy set every real `agentos init` writes.
POLICIES_YML_DEFAULT = """# Risk policies for Agent OS
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
"""

# Mirrors apps/cli/src/commands/init.ts's GITIGNORE template exactly.
GITIGNORE_DEFAULT = """# Agent OS — never commit these
.secrets/
runs/
workspaces/
approvals/
*.db
*.db-wal
*.db-shm
"""


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """A real Git repo with an initial commit, CWD set to its root for the test."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    (repo_dir / "README.md").write_text("test repo\n")
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "init"], check=True, capture_output=True
    )

    original_cwd = Path.cwd()
    os.chdir(repo_dir)
    try:
        yield repo_dir
    finally:
        os.chdir(original_cwd)


@pytest_asyncio.fixture
async def db(tmp_git_repo: Path) -> AsyncGenerator[Path, None]:
    """Fresh SQLite DB at the exact path the real gateway would use."""
    db_path = tmp_git_repo / ".agentos" / "agent_os.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    await init_db(db_path)
    yield db_path


@pytest.fixture
def agentos_dir(tmp_git_repo: Path) -> Path:
    """A minimal but valid `.agentos/` directory with the canonical default policies.yml."""
    base = tmp_git_repo / ".agentos"
    for sub in ("context", "context/api-contracts", "runs", "workspaces", ".secrets"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    (base / "policies.yml").write_text(POLICIES_YML_DEFAULT)
    (base / ".gitignore").write_text(GITIGNORE_DEFAULT)
    return base


@pytest_asyncio.fixture
async def client(db: Path, agentos_dir: Path) -> AsyncGenerator[AsyncClient, None]:
    """ASGI-transport client against the real app, with a clean DB and .agentos/ per test."""
    set_runs_dir(agentos_dir / "runs")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def registered_agent(client: AsyncClient) -> dict[str, Any]:
    """A single MockAdapter-backed agent, registered via the real endpoint."""
    response = await client.post(
        "/agents",
        json={
            "display_name": "Test Agent",
            "adapter": "mock",
            "command": "echo",
            "role": "coder",
        },
    )
    assert response.status_code == 201
    return cast("dict[str, Any]", response.json())


@pytest_asyncio.fixture
async def created_mission(client: AsyncClient) -> dict[str, Any]:
    """A single mission in `planning` status, created via the real endpoint."""
    response = await client.post(
        "/missions",
        json={"title": "Test mission", "objective": "Build a test feature"},
    )
    assert response.status_code == 201
    return cast("dict[str, Any]", response.json())


# ============================================================
# PLANNER MOCKING
# ============================================================
#
# MissionService._call_planner() calls AsyncAnthropic(...).messages.create(...)
# and reads response.content (a list of blocks with .type/.text). These fakes
# satisfy exactly that shape so patch_planner() can swap in a deterministic
# plan without a real ANTHROPIC_API_KEY or network call.


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **_kwargs: Any) -> _FakeMessage:
        return _FakeMessage(self._text)


class _FakeAsyncAnthropic:
    def __init__(self, plan_json: str) -> None:
        self.messages = _FakeMessages(plan_json)


def patch_planner(monkeypatch: pytest.MonkeyPatch, plan_json: str) -> None:
    """Replace MissionService's AsyncAnthropic client with a deterministic fake.

    `plan_json` must match the exact shape `_parse_planner_response()` expects:
    `{"tasks": [{"key", "title", "assigned_agent_id", "depends_on", "risk_level",
    "estimated_files"}, ...]}`.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "agentos_gateway.services.mission_service.AsyncAnthropic",
        lambda **_kwargs: _FakeAsyncAnthropic(plan_json),
    )
