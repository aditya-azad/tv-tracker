"""TMDB API client — movies and TV shows."""

from __future__ import annotations

from tv_tracker.api.base import (
    BaseAPIClient,
    EpisodeInfo,
    MovieDetails,
    Params,
    SearchResult,
    SeasonInfo,
    ShowDetails,
)
from tv_tracker.config import RateLimit, settings
from tv_tracker.models import MediaType, Source


class TMDBClient(BaseAPIClient):
    """Async client for the TMDB v3 API (movies & TV shows)."""

    _source = Source.TMDB

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
        base_url: str | None = None,
        cache_ttl: int | None = None,
        timeout: float | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        super().__init__(
            base_url=base_url or settings.tmdb_base_url,
            rate_limit=settings.tmdb_rate_limit,
            cache_ttl=cache_ttl,
            timeout=timeout,
            headers=headers,
        )
        self._api_key: str | None = api_key

    # -- helpers -----------------------------------------------------------

    def _params(self, extra: Params | None = None) -> dict[str, str | int | float | bool | None]:
        params: dict[str, str | int | float | bool | None] = {"language": "en-US"}
        if self._api_key and "Authorization" not in self._client.headers:
            params["api_key"] = self._api_key
        if extra:
            params.update(extra)
        return params

    # -- search ------------------------------------------------------------

    async def search_movies(self, query: str, page: int = 1) -> list[SearchResult]:
        """Search TMDB for movies matching *query*."""
        data = await self._get("/search/movie", self._params({"query": query, "page": page}))
        results = data.get("results", [])
        return [
            SearchResult(
                external_id=str(r["id"]),
                source=Source.TMDB,
                media_type=MediaType.MOVIE,
                title=r.get("title", ""),
                overview=r.get("overview"),
                release_date=r.get("release_date"),
            )
            for r in results
        ]

    async def search_tv(self, query: str, page: int = 1) -> list[SearchResult]:
        """Search TMDB for TV shows matching *query*."""
        data = await self._get("/search/tv", self._params({"query": query, "page": page}))
        results = data.get("results", [])
        return [
            SearchResult(
                external_id=str(r["id"]),
                source=Source.TMDB,
                media_type=MediaType.SHOW,
                title=r.get("name", ""),
                overview=r.get("overview"),
                release_date=r.get("first_air_date"),
            )
            for r in results
        ]

    # -- find --------------------------------------------------------------

    async def find_by_tvdb_id(self, tvdb_id: int | str) -> ShowDetails | None:
        """Find a TV show on TMDB by its TheTVDB ID.

        Uses TMDB's ``/find`` endpoint with ``external_source=tvdb_id``.
        Returns full :class:`ShowDetails` (season summaries included) or
        ``None`` if no match is found.
        """
        data = await self._get(
            f"/find/{tvdb_id}",
            self._params({"external_source": "tvdb_id"}),
        )
        results = data.get("tv_results", [])
        if not results:
            return None
        return await self.get_tv(results[0]["id"])

    # -- details -----------------------------------------------------------

    async def get_movie(self, movie_id: int | str) -> MovieDetails:
        """Fetch full details for a single movie."""
        data = await self._get(f"/movie/{movie_id}", self._params())
        return MovieDetails(
            external_id=str(data["id"]),
            source=Source.TMDB,
            title=data.get("title", ""),
            overview=data.get("overview"),
            release_date=data.get("release_date"),
            runtime=data.get("runtime"),
        )

    async def get_tv(self, tv_id: int | str) -> ShowDetails:
        """Fetch full details for a TV show, including season summaries."""
        data = await self._get(
            f"/tv/{tv_id}",
            self._params({"append_to_response": "season/1"}),
        )
        seasons: list[SeasonInfo] = []
        for s in data.get("seasons", []):
            seasons.append(
                SeasonInfo(
                    season_number=s.get("season_number", 0),
                    name=s.get("name"),
                    episode_count=s.get("episode_count", 0),
                )
            )
        return ShowDetails(
            external_id=str(data["id"]),
            source=Source.TMDB,
            title=data.get("name", ""),
            overview=data.get("overview"),
            release_date=data.get("first_air_date"),
            number_of_seasons=data.get("number_of_seasons", 0),
            number_of_episodes=data.get("number_of_episodes", 0),
            seasons=seasons,
        )

    async def get_tv_season(self, tv_id: int | str, season_number: int) -> SeasonInfo:
        """Fetch details (including episodes) for a single season."""
        data = await self._get(f"/tv/{tv_id}/season/{season_number}", self._params())
        episodes: list[EpisodeInfo] = []
        for ep in data.get("episodes", []):
            episodes.append(
                EpisodeInfo(
                    episode_number=ep.get("episode_number", 0),
                    name=ep.get("name"),
                    air_date=ep.get("air_date"),
                    overview=ep.get("overview"),
                )
            )
        return SeasonInfo(
            season_number=data.get("season_number", season_number),
            name=data.get("name"),
            episode_count=len(episodes),
            episodes=episodes,
        )


def tmdb_rate_limit() -> RateLimit:
    """Return the configured TMDB rate limit."""
    return settings.tmdb_rate_limit
