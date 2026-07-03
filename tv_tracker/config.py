"""Application configuration: paths, API settings, and rate limits."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _data_dir() -> Path:
    """Return the directory where tv-tracker stores its data."""
    env = os.environ.get("TV_TRACKER_HOME")
    if env:
        return Path(env)
    return Path.home() / ".tv-tracker"


DATA_DIR: Path = _data_dir()
DB_PATH: Path = Path(os.environ.get("TV_TRACKER_DB_PATH", str(DATA_DIR / "tracker.db")))

TMDB_BASE_URL: str = os.environ.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")

JIKAN_BASE_URL: str = os.environ.get("JIKAN_BASE_URL", "https://api.jikan.moe/v4")

DEFAULT_CACHE_TTL: int = 300  # 5 minutes
DEFAULT_TIMEOUT: float = 30.0

MAX_RETRIES: int = 3
RETRY_BACKOFF_BASE: float = 0.5  # seconds; 0.5, 1.0, 2.0 …


@dataclass(frozen=True)
class RateLimit:
    """A simple rate limit descriptor.

    Attributes:
        max_per_second: Maximum number of requests allowed per second.
        max_per_minute: Maximum number of requests allowed per minute (0 = unlimited).
    """

    max_per_second: float
    max_per_minute: int = 0


@dataclass(frozen=True)
class Settings:
    """Centralised runtime settings."""

    db_path: Path = field(default_factory=lambda: DB_PATH)
    tmdb_base_url: str = TMDB_BASE_URL
    jikan_base_url: str = JIKAN_BASE_URL
    cache_ttl: int = DEFAULT_CACHE_TTL
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = MAX_RETRIES
    retry_backoff_base: float = RETRY_BACKOFF_BASE
    tmdb_rate_limit: RateLimit = field(default_factory=lambda: RateLimit(50.0))
    jikan_rate_limit: RateLimit = field(default_factory=lambda: RateLimit(3.0, 60))


settings: Settings = Settings()
