"""
adapters/mock.py — MockAdapter

Deterministic adapter for integration tests and CI.
Simulates real agent behavior without spawning any processes.

Configure via AdapterConfig metadata:
  - "mock_outcome": "success" | "fail" | "credential_expired" | "quota_exceeded" | "rate_limited"
  - "mock_delay_ms": int — simulated work delay per output line
  - "mock_files": list[str] — files the mock "changes"
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
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

# Default mock behavior
DEFAULT_OUTCOME = "success"
DEFAULT_DELAY_MS = 50
DEFAULT_FILES = ["src/mock-output.ts"]

MOCK_OUTPUT_LINES = [
    "Analyzing task requirements...",
    "Reading project context...",
    "Planning implementation approach...",
    "Writing initial code structure...",
    "Implementing core logic...",
    "Adding error handling...",
    "Writing tests...",
    "Running validation...",
    "Finalizing changes...",
    "Task complete.",
]


class MockAdapter(BaseAdapter):
    """
    Deterministic mock adapter for testing.

    Set outcome via agent registration metadata or environment:
    - "success": completes normally
    - "fail": raises RuntimeError mid-stream
    - "credential_expired": raises CredentialFailureError(subscription_expired)
    - "quota_exceeded": raises CredentialFailureError(quota_exceeded)
    - "rate_limited": raises CredentialFailureError(rate_limited) then succeeds on retry
    """

    FAILURE_SIGNATURES = []
    RATE_LIMIT_SIGNATURES = []
    QUOTA_EXCEEDED_SIGNATURES = []

    def __init__(self, config: AdapterConfig, outcome: str = DEFAULT_OUTCOME) -> None:
        super().__init__(config)
        self._outcome = outcome
        self._delay_ms = DEFAULT_DELAY_MS
        self._mock_files = DEFAULT_FILES
        self._task: TaskPayload | None = None

    async def start_session(self) -> None:
        await asyncio.sleep(self._delay_ms / 1000)
        self._session_active = True
        self.logger.info("Mock session started (outcome: %s)", self._outcome)

    async def send_task(self, payload: TaskPayload) -> None:
        self._task = payload
        self.logger.info("Mock task received: %s", payload.task_title)

    async def stream_events(self) -> AsyncGenerator[AgentOutput, None]:
        if self._task is None:
            raise RuntimeError("Call send_task() before stream_events()")

        # Determine at which line to inject a failure (if any)
        fail_at_line = len(MOCK_OUTPUT_LINES) // 2  # halfway through

        for i, line in enumerate(MOCK_OUTPUT_LINES):
            await asyncio.sleep(self._delay_ms / 1000)

            # Inject failure at the halfway point for realism
            if i == fail_at_line:
                if self._outcome == "credential_expired":
                    raise CredentialFailureError(
                        agent_id=self.agent_id,
                        failure_type="subscription_expired",
                        message="Mock: Authentication required — subscription expired",
                    )
                elif self._outcome == "quota_exceeded":
                    raise CredentialFailureError(
                        agent_id=self.agent_id,
                        failure_type="quota_exceeded",
                        message="Mock: You exceeded your current quota",
                        reset_at="2026-06-22T00:00:00Z",
                    )
                elif self._outcome == "rate_limited":
                    raise CredentialFailureError(
                        agent_id=self.agent_id,
                        failure_type="rate_limited",
                        message="Mock: Rate limit exceeded",
                        retry_after_ms=1000,
                    )
                elif self._outcome == "fail":
                    yield AgentOutput(type="error", content="Mock: Task failed unexpectedly")
                    return

            yield AgentOutput(type="text", content=f"[{self.agent_id}] {line}")

        yield AgentOutput(type="status", content="completed")

    async def request_action(
        self,
        action_type: str,
        command_or_tool: str,
        explanation: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        # Mock always approves in tests — override for specific test cases
        self.logger.info("Mock action approved: %s — %s", action_type, command_or_tool)
        return True

    async def submit_result(self) -> TaskResult:
        success = self._outcome == "success"
        return TaskResult(
            run_id="",
            status="completed" if success else "failed",
            result_summary=f"Mock task {'completed successfully' if success else 'failed'}",
            files_changed=self._mock_files if success else [],
            confidence_score=0.95 if success else None,
            error_message=None if success else f"Mock failure: {self._outcome}",
        )

    async def stop(self) -> None:
        self._session_active = False
        self.logger.info("Mock session stopped")
