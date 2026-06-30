# Writing a new agent adapter

Agent OS talks to every coding agent — Claude Code, Codex, anything else — through the same interface: `BaseAdapter`. The gateway's orchestration code (mission planning, task routing, conflict detection) never branches on which agent it's talking to; it only ever calls methods on this interface. Adding support for a new agent CLI means writing one new adapter class, not touching any router or service.

## The `BaseAdapter` interface

Defined in `packages/gateway/src/agentos_gateway/adapters/base.py`. Every method below is abstract unless noted — your subclass must implement all of them.

**`async def start_session(self) -> None`**
Called once before any task is sent. Verify the agent's CLI command is actually available (typically `shutil.which(self.config.command)`), raise `RuntimeError` with a helpful install hint if it isn't, and set `self._session_active = True` on success. Don't spawn the actual agent process here — that happens per-task in `send_task`.

**`async def send_task(self, payload: TaskPayload) -> None`**
Called after `start_session()`, once per task. `TaskPayload` carries `task_id`, `task_title`, `context_pack_content` (the full handoff context as formatted text), `workspace_path` (the agent's dedicated Git worktree), and `base_branch`. This is where you actually spawn the agent process — build a prompt from the payload, `cwd` into `workspace_path`, and invoke the CLI.

**`def stream_events(self) -> AsyncGenerator[AgentOutput, None]`**
An async generator yielding normalized `AgentOutput(type, content, metadata)` objects as the agent works (`type` is one of `'text' | 'tool_request' | 'status' | 'error'`). This is also where credential-failure detection lives: scan every line of stdout *and* stderr through `self.detect_credential_failure(line)` (inherited from `BaseAdapter` — don't reimplement signature matching, just call it) and `raise` the resulting `CredentialFailureError` immediately if it returns one. The gateway catches that exception and drives recovery (workspace freeze, fallback routing) — your adapter's only job is to detect and raise, not to recover.

**`async def request_action(self, action_type, command_or_tool, explanation, evidence=None) -> bool`**
Called when the agent wants to do something the gateway's approval engine should weigh in on. Should block until a decision is made and return whether it was approved. In practice this means calling into the gateway's `ApprovalEngine.intercept()` and awaiting its result.

**`async def submit_result(self) -> TaskResult`**
Called once `stream_events()` is exhausted. Build and return a `TaskResult(run_id, status, result_summary, files_changed, confidence_score, error_message)` from whatever your adapter buffered during streaming.

**`async def stop(self) -> None`**
Cleanly terminate the agent process if one is running, and set `self._session_active = False`. Must be safe to call even if a session was never started.

**`async def resume(self, from_commit_sha, prior_messages) -> None`** *(has a default implementation — override only if you need something smarter)*
The default just calls `start_session()` again and logs that a resume happened. Override this if your agent CLI has a real resume/continue mode that benefits from the prior commit SHA and message history rather than starting fresh.

### Failure signature lists

Three class-level list attributes drive `detect_credential_failure()` (inherited, not something you implement yourself): `FAILURE_SIGNATURES` (auth/subscription failures), `RATE_LIMIT_SIGNATURES`, and `QUOTA_EXCEEDED_SIGNATURES`. Each is a list of lowercase substrings checked against every output line. Populate these with whatever your agent's CLI actually prints on each failure mode — check its real error output, don't guess. `detect_credential_failure()` checks rate-limit signatures first, then quota, then generic auth failures, and returns a `CredentialFailureError` (or `None`) accordingly.

## Minimal working example: a `myagent` adapter

Suppose you're wrapping a fictional `myagent` CLI that takes a prompt as its last argument, prints plain text to stdout, and exits non-zero on failure. On auth failure it prints `Error: invalid API key` to stderr.

```python
"""
adapters/myagent.py — MyAgentAdapter

Wraps the fictional `myagent` CLI behind the BaseAdapter interface.
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


class MyAgentAdapter(BaseAdapter):
    """Adapter for the `myagent` CLI."""

    FAILURE_SIGNATURES = ["invalid api key", "not authenticated"]
    RATE_LIMIT_SIGNATURES = ["rate limit exceeded"]
    QUOTA_EXCEEDED_SIGNATURES = ["quota exceeded"]

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self._process: asyncio.subprocess.Process | None = None
        self._output_buffer: list[str] = []

    async def start_session(self) -> None:
        if not shutil.which(self.config.command):
            raise RuntimeError(
                f"myagent CLI not found: '{self.config.command}'. "
                f"Install it with: npm install -g myagent-cli"
            )
        self._session_active = True
        self._output_buffer = []
        self.logger.info("myagent session initialized")

    async def send_task(self, payload: TaskPayload) -> None:
        if not self._session_active:
            raise RuntimeError("Call start_session() before send_task()")

        prompt = f"{payload.context_pack_content}\n\nTask: {payload.task_title}"
        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            "--workdir", payload.workspace_path,
            prompt,
            cwd=payload.workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stream_events(self) -> AsyncGenerator[AgentOutput, None]:
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
        self.logger.info("Action request: %s — %s", action_type, command_or_tool)
        return True  # wire to ApprovalEngine.intercept() for real approval gating

    async def submit_result(self) -> TaskResult:
        exit_code = self._process.returncode if self._process else -1
        status = "completed" if exit_code == 0 else "failed"
        return TaskResult(
            run_id="",
            status=status,
            result_summary="\n".join(self._output_buffer[-20:]) or "No output",
            files_changed=[],
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
        self._session_active = False
```

## Registering the new adapter

Three places need to know about a new adapter class:

1. **`packages/gateway/src/agentos_gateway/services/credential_manager.py`** — add it to the `_ADAPTER_CLASSES` dict, which is how `CredentialManager.detect_failure()` instantiates the right adapter transiently to check its failure signatures:

   ```python
   _ADAPTER_CLASSES: dict[str, type[BaseAdapter]] = {
       "claude-code": ClaudeCodeAdapter,
       "codex": CodexAdapter,
       "mock": MockAdapter,
       "myagent": MyAgentAdapter,
   }
   ```

2. **`packages/shared/src/types.ts`** — widen the `AgentAdapter` union so the CLI and dashboard can register and display the new adapter type:

   ```typescript
   export type AgentAdapter = 'claude-code' | 'codex' | 'mock' | 'myagent'
   ```

3. **`packages/gateway/src/agentos_gateway/models.py`** — mirror the same change in the `AgentAdapter` `Literal` type, since this file is meant to track `types.ts` exactly:

   ```python
   AgentAdapter = Literal["claude-code", "codex", "mock", "myagent"]
   ```

After that, `agentos agent add <name> --adapter myagent --cmd myagent` registers an agent that uses your new adapter — no other code changes needed. Wherever the gateway eventually spawns a real agent process (the process-spawner work, not yet built as of this release — see the architecture doc's data-flow section), it will resolve the adapter class the same way `CredentialManager` already does: by looking up `agent.adapter` in a class map.
