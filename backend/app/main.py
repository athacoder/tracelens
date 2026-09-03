"""The FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import __version__
from .api.v1 import router as v1_router
from .core.config import get_settings
from .storage.database import create_all

logger = logging.getLogger("tracelens.api")

DESCRIPTION = """
Failure forensics for multi-stage AI pipelines.

Ingest a trace, and TraceLens locates the earliest stage whose behaviour is not
explained by anything upstream, ranks the candidates, and returns an
evidence-backed diagnosis that also says why the stages downstream of it are
not to blame.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ensure the schema exists before serving.

    Convenience for local runs and tests. Alembic remains the source of truth
    for migrations; ``create_all`` is a no-op once they have been applied.
    """
    create_all()
    logger.info("TraceLens API ready (%s)", get_settings().database_url.split("://")[0])
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TraceLens",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValidationError)
    async def _malformed_payload(request: Request, error: ValidationError) -> JSONResponse:
        """Turn a domain-model rejection into a 422 rather than a 500.

        A trace that fails the model's own validators — a span ending before it
        starts, a duplicate span id — is a bad request, not a server fault, and
        the caller needs to see which rule it broke.
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": error.errors(include_url=False), "error_type": "validation_error"},
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": "TraceLens",
            "version": __version__,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    app.include_router(v1_router)
    return app


app = create_app()
