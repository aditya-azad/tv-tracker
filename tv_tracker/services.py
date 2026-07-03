"""Service layer coordinating API clients and database operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from tv_tracker.api import EpisodeInfo, MovieDetails, SearchResult, ShowDetails
from tv_tracker.api.jikan import JikanClient
from tv_tracker.api.tmdb import TMDBClient
from tv_tracker.db import session_scope
from tv_tracker.models import MediaType, Source, TrackedItem, WatchStatus

VALID_SOURCES = ("tmdb", "jikan")
VALID_MEDIA_TYPES = ("movie", "show")
VALID_STATUSES = ("planning", "watching", "completed", "on_hold", "dropped")


@dataclass
class SearchResponse:
    """Results from a multi-source search, including any source errors."""

    results: list[SearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(query: str, media_type: str | None = None) -> SearchResponse:
    """Search TMDB and Jikan concurrently for titles matching *query*."""
    return asyncio.run(_search(query, media_type))


async def _search(query: str, media_type: str | None) -> SearchResponse:
    async with TMDBClient() as tmdb, JikanClient() as jikan:
        tasks: list = []
        labels: list[str] = []

        if media_type in (None, "movie"):
            tasks.append(tmdb.search_movies(query))
            labels.append("TMDB movies")
        if media_type in (None, "show"):
            tasks.append(tmdb.search_tv(query))
            labels.append("TMDB TV")
        tasks.append(jikan.search_anime(query))
        labels.append("Jikan anime")

        raw = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[SearchResult] = []
    errors: list[str] = []
    for label, r in zip(labels, raw, strict=False):
        if isinstance(r, list):
            results.extend(r)
        else:
            errors.append(f"{label}: {r}")

    if media_type is not None:
        results = [r for r in results if r.media_type.value == media_type]

    return SearchResponse(results=results, errors=errors)


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------


def fetch_details(source: str, external_id: str) -> ShowDetails | MovieDetails:
    """Fetch details for a title from the appropriate API."""
    return asyncio.run(_fetch_details(source, external_id))


async def _fetch_details(source: str, external_id: str) -> ShowDetails | MovieDetails:
    src = source.lower()
    if src == "tmdb":
        async with TMDBClient() as tmdb:
            movie_r, tv_r = await asyncio.gather(
                tmdb.get_movie(external_id),
                tmdb.get_tv(external_id),
                return_exceptions=True,
            )
            if isinstance(movie_r, MovieDetails):
                return movie_r
            if isinstance(tv_r, ShowDetails):
                return tv_r
            if isinstance(tv_r, Exception):
                raise tv_r
            if isinstance(movie_r, Exception):
                raise movie_r
            raise ValueError(f"Title {external_id} not found on TMDB")

    if src == "jikan":
        async with JikanClient() as jikan:
            return await jikan.get_anime(external_id)

    raise ValueError(f"Unknown source: {source!r}")


def fetch_season_episodes(
    source: str, external_id: str, season_number: int
) -> list[EpisodeInfo]:
    """Fetch the episode list for a specific season of a show."""
    return asyncio.run(_fetch_season_episodes(source, external_id, season_number))


async def _fetch_season_episodes(
    source: str, external_id: str, season_number: int
) -> list[EpisodeInfo]:
    src = source.lower()
    if src == "tmdb":
        async with TMDBClient() as tmdb:
            season = await tmdb.get_tv_season(external_id, season_number)
            return season.episodes
    if src == "jikan":
        async with JikanClient() as jikan:
            return await jikan.get_anime_episodes(external_id)
    raise ValueError(f"Unknown source: {source!r}")


# ---------------------------------------------------------------------------
# Tracking operations
# ---------------------------------------------------------------------------


def add_tracked_item(source: str, external_id: str) -> TrackedItem:
    """Fetch details for a title and add it to the tracking list."""
    details = fetch_details(source, external_id)

    src = Source(source.lower())
    if isinstance(details, MovieDetails):
        media_type = MediaType.MOVIE
        total_seasons: int | None = None
        total_episodes: int | None = None
    else:
        media_type = MediaType.SHOW
        total_seasons = details.number_of_seasons or None
        total_episodes = details.number_of_episodes or None

    with session_scope() as session:
        existing = (
            session.query(TrackedItem)
            .filter_by(source=src, external_id=external_id)
            .first()
        )
        if existing is not None:
            raise ValueError(
                f"Already tracking '{existing.title}' (ID {existing.id})"
            )

        item = TrackedItem(
            external_id=external_id,
            source=src,
            media_type=media_type,
            title=details.title,
            total_seasons=total_seasons,
            total_episodes=total_episodes,
        )
        session.add(item)
        session.flush()
        return item


def list_tracked_items(status: str | None = None) -> list[TrackedItem]:
    """Return all tracked items, optionally filtered by watch status."""
    with session_scope() as session:
        query = session.query(TrackedItem)
        if status is not None:
            query = query.filter(TrackedItem.status == WatchStatus(status))
        items = query.order_by(TrackedItem.title).all()
        return items


def remove_tracked_item(item_id: int) -> str:
    """Remove a tracked item by ID.

    Returns the title of the removed item.
    Raises ``ValueError`` if no item has *item_id*.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        title = item.title
        session.delete(item)
        return title
