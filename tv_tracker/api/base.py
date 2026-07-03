"""Base API client with rate limiting, TTL caching, and shared data types."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from tv_tracker.config import RateLimit, settings
from tv_tracker.models import MediaType, Source

JSONDict = dict[str, Any]
Params = Mapping[str, str | int | float | bool | None]

# ---------------------------------------------------------------------------
# Shared data structures (normalised across TMDB and Jikan)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeInfo:
    """A single episode within a season."""

    episode_number: int
    name: str | None = None
    air_date: str | None = None
    overview: str | None = None


@dataclass(frozen=True)
class SeasonInfo:
    """A season of a show, with its episodes."""

    season_number: int
    name: str | None = None
    episode_count: int = 0
    episodes: list[EpisodeInfo] = field(default_factory=list)


@dataclass(frozen=True)
class ShowDetails:
    """Normalised details for a TV show (or anime)."""

    external_id: str
    source: Source
    title: str
    overview: str | None = None
    number_of_seasons: int = 0
    number_of_episodes: int = 0
    seasons: list[SeasonInfo] = field(default_factory=list)


@dataclass(frozen=True)
class MovieDetails:
    """Normalised details for a movie."""

    external_id: str
    source: Source
    title: str
    overview: str | None = None
    release_date: str | None = None
    runtime: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """A single search hit, normalised across providers."""

    external_id: str
    source: Source
    media_type: MediaType
    title: str
    overview: str | None = None
    release_date: str | None = None


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Async rate limiter supporting per-second and per-minute caps."""

    def __init__(self, max_per_second: float, max_per_minute: int = 0) -> None:
        self._min_interval: float = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._max_per_minute: int = max_per_minute
        self._lock: asyncio.Lock = asyncio.Lock()
        self._last_request: float = 0.0
        self._window: deque[float] = deque()

    async def acquire(self) -> None:
        """Block until it is safe to send the next request."""
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._min_interval - (now - self._last_request))

            if self._max_per_minute > 0:
                cutoff = now - 60.0
                while self._window and self._window[0] <= cutoff:
                    self._window.popleft()
                if len(self._window) >= self._max_per_minute:
                    window_wait = 60.0 - (now - self._window[0])
                    wait = max(wait, window_wait)

            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()

            self._last_request = now
            self._window.append(now)


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class TTLCache:
    """A minimal in-memory cache with per-entry time-to-live."""

    def __init__(self, ttl: int = 300) -> None:
        self._ttl: int = ttl
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() >= expiry:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl)

    def clear(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Base API client
# ---------------------------------------------------------------------------


class BaseAPIClient:
    """Common async HTTP client with caching and rate limiting.

    Subclasses set ``_source`` and implement provider-specific methods that
    call :meth:`_get` to fetch JSON from the API.
    """

    _source: Source

    def __init__(
        self,
        base_url: str,
        rate_limit: RateLimit | None = None,
        cache_ttl: int | None = None,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        rl = rate_limit or settings.tmdb_rate_limit
        self._rate_limiter: RateLimiter = RateLimiter(rl.max_per_second, rl.max_per_minute)
        self._cache: TTLCache = TTLCache(cache_ttl or settings.cache_ttl)
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout or settings.timeout,
            headers=headers or {},
        )

    async def _get(self, path: str, params: Params | None = None) -> JSONDict:
        """Fetch JSON from *path*, using the cache and respecting rate limits."""
        key = self._cache_key(path, params)
        cached = self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._rate_limiter.acquire()
        response = await self._client.get(path, params=dict(params) if params else None)
        response.raise_for_status()
        data: JSONDict = response.json()  # type: ignore[assignment]
        self._cache.set(key, data)
        return data

    def _cache_key(self, path: str, params: Params | None) -> str:
        if not params:
            return path
        items = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{path}?{items}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BaseAPIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
