"""
adapters/codex.py — CodexAdapter

Wraps the OpenAI Codex CLI behind the BaseAdapter interface.
Monitors for OpenAI-specific credential and quota failure signatures.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from .base import (
    AdapterConfig,
    AgentOutput,
    BaseAdapter,
    CredentialFailureError,
    TaskPayload,
    TaskResult,
)

logger = logging.getLogger(__name__)


class CodexAdapter(BaseAdapter):
    """
    Adapter for OpenAI Codex CLI (`codex` command).

    Authentication model: OpenAI API key stored as OPENAI_API_KEY env var,
    or via Codex CLI's own auth flow. Failures manifest as API error messages.
    """

    # ============================================================
    # CREDENTIAL FAILURE SIGNATURES (OpenAI-specific)
    # ============================================================

    FAILURE_SIGNATURES = [
        "401 unauthorized",
        "invalid api key",
        "incorrect api key",
        "openai.authenticationerror",
        "authentication error",
        "api key not found",
        "invalid_api_key",
    ]

    RATE_LIMIT_SIGNATURES = [
        "429 too many requests",
        "rate limit exceeded",
        "rate_limit_exceeded",
        "openai.ratelimiterror",
        "too many requests",
    ]

    QUOTA_EXCEEDED_SIGNATURES = [
        "exceeded your current quota",
        "insufficient_quota",
        "openai.quotaexceedederror",
        "you've exceeded",
        "billing hard limit",
        "quota exceeded",
    ]

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self._process: asyncio.subprocess.Process | None = None
        self._output_buffer: list[str] = []
        self._files_changed: list[str] = []

    async def start_session(self) -> None:
        """Verify codex CLI is available."""
        if not shutil.which(self.config.command):
            raise RuntimeError(
                f"Codex CLI not found: '{self.config.command}'. "
                f"Install it with: npm install -g @openai/codex"
            )
        self._session_active = True
        self._output_buffer = []
        self._files_changed = []
        self.logger.info("Codex session initialized (command: %s)", self.config.command)

    async def send_task(self, payload: TaskPayload) -> None:
        """Spawn codex CLI in the agent's worktree with the task prompt."""
        if not self._session_active:
            raise RuntimeError("Call start_session() before send_task()")

        prompt = self._build_prompt(payload)

        self.logger.info(
            "Sending task to Codex: %s (worktree: %s)",
            payload.task_title,
            payload.workspace_path,
        )

        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            "--approval-mode", "auto-edit",   # Codex-specific: auto-approve edits
            "--quiet",
            prompt,
            cwd=payload.workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stream_events(self) -> AsyncGenerator[AgentOutput, None]:
        """Stream output from codex, monitoring for credential failures."""
        if self._process is None:
            raise RuntimeError("Call send_task() before stream_events()")

        assert self._process.stdout is not None
        assert self._process.stderr is not None

        async def read_stderr() -> None:
            while True:
                line = await self._process.stderr.readline()  # type: ignore[union-attr]
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    failure = self.detect_credential_failure(text)
                    if failure:
                        raise failure
                    self.logger.debug("stderr: %s", text)

        stderr_task = asyncio.create_task(read_stderr())

        try:
            async for line in self._process.stdout:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                failure = self.detect_credential_failure(text)
                if failure:
                    stderr_task.cancel()
                    raise failure

                self._output_buffer.append(text)
                yield AgentOutput(type="text", content=text)

        finally:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError, CredentialFailureError):
                await stderr_task

        await self._process.wait()

    async def request_action(
        self,
        action_type: str,
        command_or_tool: str,
        explanation: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        self.logger.info(
            "Action request: %s — %s (%s)",
            action_type,
            command_or_tool,
            explanation,
        )
        # TODO Phase 3: call gateway /inbox endpoint and await approval
        return True

    async def submit_result(self) -> TaskResult:
        exit_code = self._process.returncode if self._process else -1
        status = "completed" if exit_code == 0 else "failed"
        summary = "\n".join(self._output_buffer[-20:]) if self._output_buffer else "No output"

        return TaskResult(
            run_id="",
            status=status,
            result_summary=summary[:2000],
            files_changed=list(set(self._files_changed)),
            confidence_score=0.8 if status == "completed" else None,
            error_message=None if status == "completed" else f"Exit code: {exit_code}",
        )

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                self.logger.warning("Codex process killed (did not terminate in 5s)")
        self._session_active = False
        self.logger.info("Codex session stopped")

    def _build_prompt(self, payload: TaskPayload) -> str:
        return f"""You are working on a software development task as part of a multi-agent mission.

## Context
{payload.context_pack_content}

## Your Task
{payload.task_title}

## Workspace
You are working in: {payload.workspace_path}
Base branch: {payload.base_branch}

## Instructions
- Complete the task described above
- Work only within your assigned workspace
- Do not modify files outside your scope
- Commit your work when complete

Begin working on the task now."""
