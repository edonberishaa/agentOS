"""
adapters/base.py — BaseAdapter abstract base class.

Every agent adapter (ClaudeCode, Codex, Mock) implements this interface.
The gateway only ever calls methods on this interface — never agent-specific code.

Design principle: adapters are thin translators. They translate:
  - Agent-specific CLI commands → common start/send/stream/stop operations
  - Agent-specific output formats → normalized AgentOSEvent payloads
  - Agent-specific error signatures → CredentialFailureEvent
"""

from __future__ import annotations

import abc
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdapterConfig:
    """Configuration passed to an adapter at instantiation."""
    agent_id: str
    display_name: str
    command: str                          # CLI command e.g. 'claude', 'codex'
    role: str
    capabilities: list[str]
    worktree_path: str | None = None      # Set when a task is assigned


@dataclass
class TaskPayload:
    """What gets sent to an agent when a task is assigned."""
    task_id: str
    task_title: str
    context_pack_content: str            # Full context pack as formatted text
    workspace_path: str                  # Path to agent's Git worktree
    base_branch: str


@dataclass
class AgentOutput:
    """Normalized output from an agent during streaming."""
    type: str                             # 'text' | 'tool_request' | 'status' | 'error'
    content: str
    metadata: dict[str, Any] | None = None


@dataclass
class TaskResult:
    """What an agent returns when a task is complete."""
    run_id: str
    status: str                           # 'completed' | 'failed' | 'partial'
    result_summary: str
    files_changed: list[str]
    confidence_score: float | None = None
    error_message: str | None = None


class CredentialFailureError(Exception):
    """
    Raised by adapters when they detect an authentication/quota failure.
    The gateway catches this and triggers the credential recovery flow.
    """
    def __init__(
        self,
        agent_id: str,
        # 'subscription_expired' | 'quota_exceeded' | 'rate_limited' | 'auth_invalid'
        failure_type: str,
        message: str,
        retry_after_ms: int | None = None,
        reset_at: str | None = None,
    ) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.failure_type = failure_type
        self.message = message
        self.retry_after_ms = retry_after_ms
        self.reset_at = reset_at


class BaseAdapter(abc.ABC):
    """
    Abstract base class for all agent adapters.

    Lifecycle:
      1. start_session()   — initialize the agent process
      2. send_task()       — send task + context pack
      3. stream_events()   — yield normalized output until task completes
      4. submit_result()   — collect and return the final TaskResult
      5. stop()            — cleanly shut down the agent process

    Credential failure:
      - Adapters monitor stdout/stderr during stream_events()
      - On detection, raise CredentialFailureError — the gateway handles recovery
    """

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self._session_active = False
        self.logger = logging.getLogger(f"{__name__}.{config.agent_id}")

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def is_active(self) -> bool:
        return self._session_active

    @abc.abstractmethod
    async def start_session(self) -> None:
        """
        Initialize the agent process. Must be called before send_task().
        Sets self._session_active = True on success.
        Raises RuntimeError if the agent command is not found on PATH.
        """
        ...

    @abc.abstractmethod
    async def send_task(self, payload: TaskPayload) -> None:
        """
        Send a task and its context pack to the agent.
        Must be called after start_session().
        """
        ...

    @abc.abstractmethod
    def stream_events(self) -> AsyncGenerator[AgentOutput, None]:
        """
        Async generator that yields normalized AgentOutput objects
        as the agent works. Continues until the agent signals completion
        or raises CredentialFailureError.

        Implementations must:
        - Monitor stderr for credential failure signatures every 2 seconds
        - Raise CredentialFailureError immediately on detection
        - Yield intermediate output for observability
        """
        ...

    @abc.abstractmethod
    async def request_action(
        self,
        action_type: str,
        command_or_tool: str,
        explanation: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        """
        Called by the agent (via adapter) when it wants to perform a risky action.
        Returns True if the gateway approved, False if denied.
        This call BLOCKS until the gateway makes a decision.
        """
        ...

    @abc.abstractmethod
    async def submit_result(self) -> TaskResult:
        """
        Called after stream_events() completes.
        Collects and returns the final TaskResult.
        """
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """
        Cleanly shut down the agent process.
        Sets self._session_active = False.
        Safe to call even if the session was never started.
        """
        ...

    async def resume(self, from_commit_sha: str, prior_messages: list[dict[str, Any]]) -> None:
        """
        Resume a previously paused or handed-off task.
        Default implementation: restart session with prior message history injected.
        Adapters may override for more sophisticated resume behavior.
        """
        await self.start_session()
        self.logger.info(
            "Resumed from commit %s with %d prior messages",
            from_commit_sha[:8],
            len(prior_messages),
        )

    # ============================================================
    # CREDENTIAL FAILURE SIGNATURE DETECTION
    # These class-level constants are overridden in each subclass.
    # ============================================================

    FAILURE_SIGNATURES: list[str] = []
    """Strings in stdout/stderr that indicate a credential failure."""

    RATE_LIMIT_SIGNATURES: list[str] = []
    """Strings indicating rate limiting (retriable)."""

    QUOTA_EXCEEDED_SIGNATURES: list[str] = []
    """Strings indicating quota exhaustion."""

    def detect_credential_failure(self, output: str) -> CredentialFailureError | None:
        """
        Scan a line of output for known failure signatures.
        Returns a CredentialFailureError if detected, None otherwise.
        Called by stream_events() implementations on every output line.
        """
        lower = output.lower()

        for sig in self.RATE_LIMIT_SIGNATURES:
            if sig.lower() in lower:
                return CredentialFailureError(
                    agent_id=self.agent_id,
                    failure_type="rate_limited",
                    message=f"Rate limited: {output.strip()}",
                )

        for sig in self.QUOTA_EXCEEDED_SIGNATURES:
            if sig.lower() in lower:
                return CredentialFailureError(
                    agent_id=self.agent_id,
                    failure_type="quota_exceeded",
                    message=f"Quota exceeded: {output.strip()}",
                )

        for sig in self.FAILURE_SIGNATURES:
            if sig.lower() in lower:
                return CredentialFailureError(
                    agent_id=self.agent_id,
                    failure_type="subscription_expired",
                    message=f"Auth failure: {output.strip()}",
                )

        return None
