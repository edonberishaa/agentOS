"""
main.py — FastAPI application entry point.

Responsibilities:
- App creation and configuration
- Lifespan: database init, directory setup, startup events
- Router registration
- /health endpoint
- CORS (localhost only)
- Exception handlers
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import init_db
from .events import event_ledger, set_runs_dir
from .models import GatewayHealth
from .routers import agents, context, credentials, events, inbox, missions, tasks

logger = logging.getLogger(__name__)

# ============================================================
# STARTUP CONFIGURATION
# ============================================================

VERSION = "0.1.0"
_startup_time = time.time()


def resolve_agentos_dir() -> Path:
    """
    Resolve the .agentos directory.
    Walks up from CWD until it finds one, or creates it in CWD.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".agentos"
        if candidate.is_dir():
            return candidate
    # Not found — will be created by `agentos init`
    return cwd / ".agentos"


# ============================================================
# LIFESPAN (replaces @app.on_event deprecated pattern)
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown."""
    # ---- STARTUP ----
    agentos_dir = resolve_agentos_dir()

    # Required directories
    db_path = agentos_dir / "agent_os.db"
    runs_dir = agentos_dir / "runs"
    artifacts_dir = agentos_dir / "artifacts"
    approvals_dir = agentos_dir / "approvals"

    for directory in [runs_dir, artifacts_dir, approvals_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    # Initialize database
    await init_db(db_path)
    set_runs_dir(runs_dir)

    logger.info("Agent OS Gateway v%s starting", VERSION)
    logger.info("Project directory: %s", agentos_dir)
    logger.info("Database: %s", db_path)

    await event_ledger.emit(
        source="gateway",
        type="gateway.started",
        payload={"version": VERSION, "agentos_dir": str(agentos_dir)},
        severity="info",
    )

    yield  # Application runs

    # ---- SHUTDOWN ----
    logger.info("Agent OS Gateway shutting down")
    from .sse import sse_manager
    await sse_manager.close_all()


# ============================================================
# APP CREATION
# ============================================================

def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent OS Gateway",
        description="Personal control plane for AI coding agents",
        version=VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — localhost only, dashboard on port 47822
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:47822",
            "http://127.0.0.1:47822",
            "http://localhost:3000",   # Next.js dev server
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- ROUTERS ----
    app.include_router(agents.router, prefix="/agents", tags=["agents"])
    app.include_router(missions.router, prefix="/missions", tags=["missions"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(inbox.router, prefix="/inbox", tags=["inbox"])
    app.include_router(events.router, prefix="/events", tags=["events"])
    app.include_router(context.router, prefix="/context", tags=["context"])
    app.include_router(credentials.router, prefix="/credentials", tags=["credentials"])

    # ---- HEALTH ----
    @app.get("/health", response_model=GatewayHealth, tags=["system"])
    async def health() -> GatewayHealth:
        """Gateway health check — responds in < 10ms."""
        from .database import get_db

        active_missions = 0
        active_agents = 0

        try:
            async with get_db() as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM missions WHERE status = 'running'"
                ) as cur:
                    row = await cur.fetchone()
                    active_missions = row[0] if row else 0

                async with db.execute(
                    "SELECT COUNT(*) FROM agents WHERE status = 'busy'"
                ) as cur:
                    row = await cur.fetchone()
                    active_agents = row[0] if row else 0
        except Exception:
            return GatewayHealth(
                version=VERSION,
                status="degraded",
                uptime_seconds=time.time() - _startup_time,
                active_missions=0,
                active_agents=0,
            )

        return GatewayHealth(
            version=VERSION,
            status="ok",
            uptime_seconds=time.time() - _startup_time,
            active_missions=active_missions,
            active_agents=active_agents,
        )

    # ---- EXCEPTION HANDLERS ----
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "path": str(request.url.path)},
        )

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )

    return app


app = create_app()


# ============================================================
# ENTRY POINT (for `python -m agentos_gateway`)
# ============================================================

def main() -> None:
    port = int(os.environ.get("AGENTOS_PORT", "47821"))
    log_level = os.environ.get("AGENTOS_LOG_LEVEL", "info").lower()

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    uvicorn.run(
        "agentos_gateway.main:app",
        host="127.0.0.1",   # localhost only — never expose externally
        port=port,
        reload=False,
        log_level=log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
