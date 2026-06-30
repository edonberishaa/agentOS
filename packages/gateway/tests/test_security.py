"""
tests/test_security.py — Phase 4 security audit.

Per the testing skill's checklist: verify the actual guarantees, not that
the guarding function merely exists. Where a guarantee genuinely isn't
implemented yet (context pack sanitization), the test still exercises the
real mechanism and is marked `xfail` rather than skipped or weakened to
something that would trivially pass.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import keyring
import pytest
import yaml
from httpx import AsyncClient

from agentos_gateway.database import fetch_all, fetch_one, get_db
from agentos_gateway.services.approval_engine import approval_engine
from agentos_gateway.services.credential_manager import credential_manager
from agentos_gateway.services.run_manager import _sanitize_text, run_manager
from agentos_gateway.services.task_service import task_service

SECRET_VALUE = "test-secret-value"


@pytest.mark.asyncio
async def test_credential_value_never_appears_in_events(
    client: AsyncClient, agentos_dir: Path
) -> None:
    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()

    await credential_manager.store(agent["id"], SECRET_VALUE)
    try:
        await credential_manager.rotate(agent["id"], "a-different-new-value")

        async with get_db() as db:
            all_events = await fetch_all(db, "SELECT payload FROM events")

        for row in all_events:
            payload_text = row["payload"] or ""
            assert SECRET_VALUE not in payload_text
    finally:
        with contextlib.suppress(Exception):
            keyring.delete_password("agentos", f"agent-os:{agent['id']}")


def test_secrets_dir_is_gitignored(agentos_dir: Path) -> None:
    gitignore_text = (agentos_dir / ".gitignore").read_text()
    assert ".secrets/" in gitignore_text


@pytest.mark.asyncio
async def test_dangerous_commands_all_blocked(
    client: AsyncClient, created_mission: dict[str, Any], agentos_dir: Path
) -> None:
    policies = yaml.safe_load((agentos_dir / "policies.yml").read_text())
    dangerous_commands = policies["dangerous_commands"]
    assert dangerous_commands  # the fixture's policies.yml must actually have entries to test

    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    task = await task_service.create(
        task_id="security-task-1",
        mission_id=created_mission["id"],
        title="Security test task",
        assigned_agent_id=agent["id"],
        depends_on=[],
    )
    run_id = await run_manager.create_run(task.id, agent["id"])

    for command in dangerous_commands:
        result = await approval_engine.intercept(
            run_id, "shell_cmd", command, [], "test", None, None
        )
        assert result is False, f"dangerous command not blocked: {command!r}"

    async with get_db() as db:
        pending_rows = await fetch_all(
            db, "SELECT * FROM action_requests WHERE run_id = ? AND status = 'pending'", (run_id,)
        )
    assert pending_rows == []

    async with get_db() as db:
        blocked_rows = await fetch_all(
            db, "SELECT * FROM action_requests WHERE run_id = ? AND status = 'blocked'", (run_id,)
        )
    assert len(blocked_rows) == len(dangerous_commands)


def test_sensitive_path_triggers_high_risk(agentos_dir: Path) -> None:
    score = approval_engine.score("file_write", None, [".env.production"], None)
    assert score >= 70


def test_sanitize_text_catches_each_secret_pattern() -> None:
    anthropic_key = "sk-ant-api03-fakesecretvalue1234567890"
    openai_key = "sk-fakesecretvalue1234567890123456"
    github_token = "ghp_" + "a" * 36
    base64_blob = "QWxhZGRpbjpvcGVuIHNlc2FtZQQWxhZGRpbjpvcGVuIHNlc2FtZQ=="

    assert anthropic_key not in _sanitize_text(f"key: {anthropic_key}")
    assert openai_key not in _sanitize_text(f"key: {openai_key}")
    assert github_token not in _sanitize_text(f"token: {github_token}")
    assert base64_blob not in _sanitize_text(f"blob: {base64_blob}")


@pytest.mark.asyncio
async def test_context_pack_sanitizes_secrets(
    client: AsyncClient, created_mission: dict[str, Any]
) -> None:
    agent = (
        await client.post(
            "/agents",
            json={"display_name": "Agent", "adapter": "mock", "command": "echo", "role": "coder"},
        )
    ).json()
    task = await task_service.create(
        task_id="security-task-2",
        mission_id=created_mission["id"],
        title="Sanitization test task",
        assigned_agent_id=agent["id"],
        depends_on=[],
    )
    fake_secret = "sk-ant-api03-fakesecretvalue1234567890"
    run_id = await run_manager.create_run(task.id, agent["id"])
    await run_manager.save_message(
        run_id,
        agent["id"],
        task.id,
        "assistant",
        f"Here is my API key: {fake_secret}",
    )

    pack_id, content = await run_manager.build_handoff_pack(task.id, run_id, agent["id"])
    assert fake_secret not in content

    async with get_db() as db:
        pack_row = await fetch_one(db, "SELECT content FROM context_packs WHERE id = ?", (pack_id,))
    assert pack_row is not None
    assert fake_secret not in (pack_row["content"] or "")
