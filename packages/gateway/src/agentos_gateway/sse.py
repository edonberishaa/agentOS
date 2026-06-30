"""
sse.py — Server-Sent Events stream manager.

Maintains a registry of active SSE connections and broadcasts events
to matching subscribers. Filters by mission_id, run_id, and agent_id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SSESubscriber:
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    mission_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None

    def matches(self, event: dict[str, Any]) -> bool:
        """Return True if this subscriber should receive the given event."""
        if self.mission_id and event.get("mission_id") != self.mission_id:
            return False
        if self.run_id and event.get("run_id") != self.run_id:
            return False
        return not (self.agent_id and event.get("source") != self.agent_id)


class SSEManager:
    """
    Singleton that manages all active SSE connections.
    Thread-safe via asyncio — all operations run in the same event loop.
    """

    def __init__(self) -> None:
        self._subscribers: list[SSESubscriber] = []

    def subscribe(
        self,
        mission_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> SSESubscriber:
        sub = SSESubscriber(mission_id=mission_id, run_id=run_id, agent_id=agent_id)
        self._subscribers.append(sub)
        logger.debug("SSE subscriber added (total: %d)", len(self._subscribers))
        return sub

    def unsubscribe(self, subscriber: SSESubscriber) -> None:
        try:
            self._subscribers.remove(subscriber)
            logger.debug("SSE subscriber removed (total: %d)", len(self._subscribers))
        except ValueError:
            pass

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Broadcast an event to all matching subscribers."""
        dead: list[SSESubscriber] = []
        for sub in self._subscribers:
            if sub.matches(event):
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("SSE queue full for subscriber — dropping event")
                    dead.append(sub)
        for sub in dead:
            self.unsubscribe(sub)

    async def event_stream(
        self,
        subscriber: SSESubscriber,
        heartbeat_interval: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """
        AsyncGenerator that yields SSE-formatted strings.
        Sends a heartbeat comment every `heartbeat_interval` seconds to keep
        the connection alive through proxies and load balancers.
        """
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        subscriber.queue.get(),
                        timeout=heartbeat_interval,
                    )
                    if event is None:
                        # Sentinel — stream is being closed
                        break
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        finally:
            self.unsubscribe(subscriber)

    async def close_all(self) -> None:
        """Signal all subscribers to close. Called on gateway shutdown."""
        for sub in self._subscribers:
            await sub.queue.put(None)
        self._subscribers.clear()


# Module-level singleton
sse_manager = SSEManager()
