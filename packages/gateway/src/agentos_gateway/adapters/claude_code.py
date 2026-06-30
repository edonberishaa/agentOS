"""
adapters/claude_code.py — ClaudeCodeAdapter

Wraps the `claude` CLI (Claude Code) behind the BaseAdapter interface.
Monitors output for Anthropic-specific credential failure signatures.
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


class ClaudeCodeAdapter(BaseAdapter):
    """
    Adapter for Claude Code CLI (`claude` command).

    Authentication model: Anthropic account subscription session.
    The `claude` CLI manages its own auth state in ~/.claude/.
    Failures manifest as stderr messages or non-zero exit codes.
    """

    # ============================================================
    # CREDENTIAL FAILURE SIGNATURES (Anthropic-specific)
    # ============================================================

    FAILURE_SIGNATURES = [
        "authentication required",
        "subscription has expired",
        "please run: claude auth login",
        "claude auth login",
        "unauthorized",
        "403 forbidden",
        "402 payment required",
        "your account",
        "billing",
    ]

    RATE_LIMIT_SIGNATURES = [
        "rate limit",
        "too many requests",
        "429",
    ]

    QUOTA_EXCEEDED_SIGNATURES = [
        "usage limit",
        "quota exceeded",
        "exceeded your",
    ]

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self._process: asyncio.subprocess.Process | None = None
        self._output_buffer: list[str] = []
        self._files_changed: list[str] = []

    async def start_session(self) -> None:
        """Verify claude CLI is available. Process is spawned per-task in send_task()."""
        if not shutil.which(self.config.command):
            raise RuntimeError(
                f"Claude Code CLI not found: '{self.config.command}'. "
                f"Install it from https://claude.ai/code"
            )
        self._session_active = True
        self._output_buffer = []
        self._files_changed = []
        self.logger.info("Claude Code session initialized (command: %s)", self.config.command)

    async def send_task(self, payload: TaskPayload) -> None:
        """
        Spawn claude CLI in the agent's worktree with the task as a prompt.
        Uses --output-format stream-json for structured output parsing.
        """
        if not self._session_active:
            raise RuntimeError("Call start_session() before send_task()")

        # Build the full prompt combining context pack + task instruction
        prompt = self._build_prompt(payload)

        self.logger.info(
            "Sending task to Claude Code: %s (worktree: %s)",
            payload.task_title,
            payload.workspace_path,
        )

        # Spawn the claude process in the worktree directory
        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            "--print",               # non-interactive mode
            "--output-format", "stream-json",
            prompt,
            cwd=payload.workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stream_events(self) -> AsyncGenerator[AgentOutput, None]:
        """
        Stream output from the claude process.
        Monitors for credential failures on every line.
        """
        if self._process is None:
            raise RuntimeError("Call send_task() before stream_events()")

        assert self._process.stdout is not None
        assert self._process.stderr is not None

        async def read_stderr() -> None:
            """Background task to drain stderr and check for failures."""
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

                # Check stdout for failure signatures too
                failure = self.detect_credential_failure(text)
                if failure:
                    stderr_task.cancel()
                    raise failure

                self._output_buffer.append(text)

                # Parse stream-json format if possible, otherwise emit raw
                output = self._parse_stream_line(text)
                yield output

        finally:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError, CredentialFailureError):
                await stderr_task

        await self._process.wait()

    def _parse_stream_line(self, line: str) -> AgentOutput:
        """
        Parse a line from --output-format stream-json.
        Falls back to raw text output if parsing fails.
        """
        import json
        try:
            data = json.loads(line)
            output_type = data.get("type", "text")

            if output_type == "content_block_delta":
                content = data.get("delta", {}).get("text", "")
                return AgentOutput(type="text", content=content)

            elif output_type == "tool_use":
                tool_name = data.get("name", "unknown")
                tool_input = data.get("input", {})
                # Track file modifications
                if "file_path" in tool_input:
                    self._files_changed.append(tool_input["file_path"])
                return AgentOutput(
                    type="tool_request",
                    content=f"Tool: {tool_name}",
                    metadata={"tool_name": tool_name, "input": tool_input},
                )

            elif output_type == "message_stop":
                return AgentOutput(type="status", content="completed")

            else:
                return AgentOutput(type="text", content=line)

        except (json.JSONDecodeError, KeyError):
            return AgentOutput(type="text", content=line)

    async def request_action(
        self,
        action_type: str,
        command_or_tool: str,
        explanation: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        """
        In v1, this is a stub that always returns True for low-risk actions.
        Full implementation: gateway HTTP call to approval engine.
        The adapter pauses execution and waits for the decision.
        """
        self.logger.info(
            "Action request: %s — %s (%s)",
            action_type,
            command_or_tool,
            explanation,
        )
        # TODO Phase 3: call gateway /inbox endpoint and await approval
        return True

    async def submit_result(self) -> TaskResult:
        """Build TaskResult from buffered output."""
        exit_code = self._process.returncode if self._process else -1
        status = "completed" if exit_code == 0 else "failed"

        summary = "\n".join(self._output_buffer[-20:]) if self._output_buffer else "No output"

        return TaskResult(
            run_id="",  # Set by RunManager after creation
            status=status,
            result_summary=summary[:2000],
            files_changed=list(set(self._files_changed)),
            confidence_score=0.8 if status == "completed" else None,
            error_message=None if status == "completed" else f"Exit code: {exit_code}",
        )

    async def stop(self) -> None:
        """Terminate the claude process if running."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                self.logger.warning("Claude Code process killed (did not terminate in 5s)")
        self._session_active = False
        self.logger.info("Claude Code session stopped")

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
- If you need to perform a risky operation (migrations, deployments, .env changes), \
request approval first
- Commit your work when complete

Begin working on the task now."""
