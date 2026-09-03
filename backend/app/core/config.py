"""Runtime configuration, read once from the environment.

Every setting has a working default, so `uvicorn app.main:app` runs with no
`.env` at all. That is what makes the quickstart in the README honest.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]

# Loaded once at import. override=False so a real environment variable always
# beats a stale .env file left in the checkout.
load_dotenv(REPO_ROOT / ".env", override=False)


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Effective configuration for one process."""

    database_url: str = field(
        default_factory=lambda: (
            os.getenv("DATABASE_URL") or f"sqlite:///{REPO_ROOT / 'tracelens.db'}"
        )
    )
    #: Origins allowed to call the API from a browser. The Next.js dev server
    #: by default; set CORS_ORIGINS to a comma-separated list to change it.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip()
            for origin in (
                os.getenv("CORS_ORIGINS") or "http://localhost:3000,http://127.0.0.1:3000"
            ).split(",")
            if origin.strip()
        )
    )
    #: Analyse each trace at ingest time (D-003). Turning this off stores
    #: traces without diagnosing them, which is useful for bulk backfill.
    analyse_on_ingest: bool = field(default_factory=lambda: _flag("ANALYSE_ON_INGEST", True))
    #: Guard rails on list endpoints so one request cannot read the table.
    default_page_size: int = 25
    max_page_size: int = 200
    llm_provider: str = field(default_factory=lambda: os.getenv("TRACELENS_LLM_PROVIDER") or "mock")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached, so the environment is read once."""
    return Settings()
