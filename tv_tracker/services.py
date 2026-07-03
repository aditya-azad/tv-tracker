"""Service layer coordinating API clients and database operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.orm import selectinload

from tv_tracker.api import EpisodeInfo, MovieDetails, SearchResult, ShowDetails
from tv_tracker.api.jikan import JikanClient
from tv_tracker.api.tmdb import TMDBClient
from tv_tracker.db import session_scope
from tv_tracker.models import (
    MediaType,
    Source,
    TrackedItem,
    WatchedEpisode,
    WatchStatus,
)

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


def fetch_season_episodes(source: str, external_id: str, season_number: int) -> list[EpisodeInfo]:
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
            session.query(TrackedItem).filter_by(source=src, external_id=external_id).first()
        )
        if existing is not None:
            raise ValueError(f"Already tracking '{existing.title}' (ID {existing.id})")

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
    """Return all tracked items, optionally filtered by watch status.

    The ``watched_episodes`` relationship is eagerly loaded so callers can
    access :pyattr:`TrackedItem.watched_episodes` after the session closes.
    """
    with session_scope() as session:
        query = session.query(TrackedItem).options(selectinload(TrackedItem.watched_episodes))
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


# ---------------------------------------------------------------------------
# Watch tracking
# ---------------------------------------------------------------------------

# Sentinel values used to record a movie watch in the WatchedEpisode table
# (which is otherwise show-only).  Season 0 / episode 0 never collide with
# real episodes since real seasons start at 1.
_MOVIE_SEASON = 0
_MOVIE_EPISODE = 0


def set_watch_status(item_id: int, status: str) -> TrackedItem:
    """Update the watch status of a tracked item.

    Raises ``ValueError`` if the item does not exist or *status* is invalid.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        item.status = WatchStatus(status)
        return item


def mark_watched(
    item_id: int, season: int | None = None, episode: int | None = None
) -> TrackedItem:
    """Mark a movie or episode as watched.

    For movies, omit *season* and *episode*.
    For shows, both *season* and *episode* are required.

    Returns the updated :class:`TrackedItem`.
    Raises ``ValueError`` if the item doesn't exist, the arguments don't
    match the media type, or the episode/movie is already marked watched.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")

        if item.media_type == MediaType.MOVIE:
            if season is not None or episode is not None:
                raise ValueError(
                    "Movies don't have seasons/episodes — use "
                    "'tv-tracker watch <id>' without --season/--episode."
                )
            season_num = _MOVIE_SEASON
            episode_num = _MOVIE_EPISODE
        else:
            if season is None or episode is None:
                raise ValueError(
                    "Shows require --season and --episode — use "
                    "'tv-tracker watch <id> --season N --episode M'."
                )
            season_num = season
            episode_num = episode

        existing = (
            session.query(WatchedEpisode)
            .filter_by(
                tracked_item_id=item_id,
                season_number=season_num,
                episode_number=episode_num,
            )
            .first()
        )
        if existing is not None:
            if item.media_type == MediaType.MOVIE:
                raise ValueError(f"'{item.title}' is already marked as watched.")
            raise ValueError(
                f"'{item.title}' S{season_num:02}E{episode_num:02} is already watched."
            )

        session.add(
            WatchedEpisode(
                tracked_item_id=item_id,
                season_number=season_num,
                episode_number=episode_num,
            )
        )
        return item


def unmark_watched(item_id: int, season: int | None = None, episode: int | None = None) -> str:
    """Remove the watched mark from a movie or episode.

    Returns the title of the tracked item.
    Raises ``ValueError`` if the item or watched record doesn't exist.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")

        if item.media_type == MediaType.MOVIE:
            if season is not None or episode is not None:
                raise ValueError(
                    "Movies don't have seasons/episodes — use "
                    "'tv-tracker unwatch <id>' without --season/--episode."
                )
            season_num = _MOVIE_SEASON
            episode_num = _MOVIE_EPISODE
        else:
            if season is None or episode is None:
                raise ValueError(
                    "Shows require --season and --episode — use "
                    "'tv-tracker unwatch <id> --season N --episode M'."
                )
            season_num = season
            episode_num = episode

        existing = (
            session.query(WatchedEpisode)
            .filter_by(
                tracked_item_id=item_id,
                season_number=season_num,
                episode_number=episode_num,
            )
            .first()
        )
        if existing is None:
            if item.media_type == MediaType.MOVIE:
                raise ValueError(f"'{item.title}' is not marked as watched.")
            raise ValueError(
                f"'{item.title}' S{season_num:02}E{episode_num:02} is not marked as watched."
            )

        session.delete(existing)
        return item.title


def get_currently_watching() -> list[TrackedItem]:
    """Return tracked items with status *watching*, eagerly loaded with
    watched-episode data for progress display."""
    with session_scope() as session:
        items = (
            session.query(TrackedItem)
            .options(selectinload(TrackedItem.watched_episodes))
            .filter(TrackedItem.status == WatchStatus.WATCHING)
            .order_by(TrackedItem.title)
            .all()
        )
        return items


def get_recently_completed(limit: int = 5) -> list[TrackedItem]:
    """Return recently completed tracked items, most recently updated first."""
    with session_scope() as session:
        items = (
            session.query(TrackedItem)
            .options(selectinload(TrackedItem.watched_episodes))
            .filter(TrackedItem.status == WatchStatus.COMPLETED)
            .order_by(TrackedItem.updated_at.desc())
            .limit(limit)
            .all()
        )
        return items


def get_watched_episode_keys(item_id: int) -> set[tuple[int, int]]:
    """Return a set of ``(season, episode)`` tuples marked watched for *item_id*.

    For movies the sentinel ``(0, 0)`` is returned when the movie is watched.
    """
    with session_scope() as session:
        rows = (
            session.query(WatchedEpisode.season_number, WatchedEpisode.episode_number)
            .filter_by(tracked_item_id=item_id)
            .all()
        )
        return {(s, e) for s, e in rows}


def find_tracked_item(source: str, external_id: str) -> TrackedItem | None:
    """Return the tracked item matching *source* and *external_id*, or None."""
    with session_scope() as session:
        item = (
            session.query(TrackedItem)
            .filter_by(source=Source(source.lower()), external_id=external_id)
            .first()
        )
        return item
