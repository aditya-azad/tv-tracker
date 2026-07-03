"""Jikan (MyAnimeList) API client — anime."""

from __future__ import annotations

from tv_tracker.api.base import (
    BaseAPIClient,
    EpisodeInfo,
    JSONDict,
    MovieDetails,
    SearchResult,
    SeasonInfo,
    ShowDetails,
)
from tv_tracker.config import RateLimit, settings
from tv_tracker.models import MediaType, Source

_MOVIE_TYPES = {"Movie", "cm", "pv"}


class JikanClient(BaseAPIClient):
    """Async client for the Jikan v4 API (MyAnimeList anime data)."""

    _source = Source.JIKAN

    def __init__(
        self,
        base_url: str | None = None,
        cache_ttl: int | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url or settings.jikan_base_url,
            rate_limit=settings.jikan_rate_limit,
            cache_ttl=cache_ttl,
            timeout=timeout,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _is_movie(anime_type: str | None) -> bool:
        return (anime_type or "") in _MOVIE_TYPES

    @staticmethod
    def _title(entry: JSONDict) -> str:
        return (
            entry.get("title_english") or entry.get("title") or entry.get("title_japanese") or ""
        )

    # -- search ------------------------------------------------------------

    async def search_anime(self, query: str, limit: int = 10, page: int = 1) -> list[SearchResult]:
        """Search Jikan for anime matching *query*."""
        data = await self._get(
            "/anime",
            {"q": query, "limit": limit, "page": page, "sfw": "true"},
        )
        results = data.get("data", [])
        return [
            SearchResult(
                external_id=str(r["mal_id"]),
                source=Source.JIKAN,
                media_type=(MediaType.MOVIE if self._is_movie(r.get("type")) else MediaType.SHOW),
                title=self._title(r),
                overview=r.get("synopsis"),
                release_date=((r.get("aired") or {}).get("from", "")[:10] or None),
            )
            for r in results
        ]

    # -- details -----------------------------------------------------------

    async def get_anime(self, anime_id: int | str) -> ShowDetails | MovieDetails:
        """Fetch full details for a single anime entry.

        Returns a :class:`MovieDetails` for anime movies, otherwise a
        :class:`ShowDetails` with a single season containing all episodes.
        """
        data = await self._get(f"/anime/{anime_id}")
        entry = data.get("data", data)
        base = {
            "external_id": str(entry["mal_id"]),
            "source": Source.JIKAN,
            "title": self._title(entry),
            "overview": entry.get("synopsis"),
        }

        if self._is_movie(entry.get("type")):
            aired = entry.get("aired") or {}
            return MovieDetails(
                **base,
                release_date=(aired.get("from") or "")[:10] or None,
                runtime=entry.get("duration"),
            )

        episode_count = entry.get("episodes") or 0
        season = SeasonInfo(
            season_number=1,
            name=base["title"],
            episode_count=episode_count,
        )
        return ShowDetails(
            **base,
            number_of_seasons=1,
            number_of_episodes=episode_count,
            seasons=[season],
        )

    async def get_anime_episodes(self, anime_id: int | str, page: int = 1) -> list[EpisodeInfo]:
        """Fetch the episode list for an anime (paginated)."""
        data = await self._get(f"/anime/{anime_id}/episodes", {"page": page})
        episodes = data.get("data", [])
        return [
            EpisodeInfo(
                episode_number=ep.get("mal_id", idx + 1),
                name=ep.get("title"),
                air_date=(ep.get("aired") or "")[:10] or None,
            )
            for idx, ep in enumerate(episodes)
        ]

    async def get_seasonal_anime(
        self, year: int, season: str, limit: int = 25
    ) -> list[SearchResult]:
        """Fetch seasonal anime for a given *year* and *season* name."""
        season = season.lower()
        data = await self._get(f"/seasons/{year}/{season}", {"limit": limit, "sfw": "true"})
        results = data.get("data", [])
        return [
            SearchResult(
                external_id=str(r["mal_id"]),
                source=Source.JIKAN,
                media_type=(MediaType.MOVIE if self._is_movie(r.get("type")) else MediaType.SHOW),
                title=self._title(r),
                overview=r.get("synopsis"),
                release_date=((r.get("aired") or {}).get("from", "")[:10] or None),
            )
            for r in results
        ]


def jikan_rate_limit() -> RateLimit:
    """Return the configured Jikan rate limit."""
    return settings.jikan_rate_limit
