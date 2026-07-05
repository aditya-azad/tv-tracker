"""Service layer coordinating API clients and database operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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
from tv_tracker.settings_store import get_tmdb_access_token, get_tmdb_api_key

VALID_SOURCES = ("tmdb", "jikan")
VALID_MEDIA_TYPES = ("movie", "show")
VALID_STATUSES = ("planning", "watching", "completed", "on_hold", "dropped")


def _make_tmdb_client() -> TMDBClient:
    """Create a TMDBClient using credentials stored in the database."""
    return TMDBClient(
        api_key=get_tmdb_api_key(),
        access_token=get_tmdb_access_token(),
    )


@dataclass
class SearchResponse:
    """Results from a multi-source search, including any source errors."""

    results: list[SearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """Summary of an on-demand sync run."""

    items_synced: int = 0
    resumed: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Stats:
    """Aggregate counts of tracked items by status and media type."""

    total: int = 0
    planning: int = 0
    watching: int = 0
    completed: int = 0
    on_hold: int = 0
    dropped: int = 0
    movies: int = 0
    shows: int = 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(query: str, media_type: str | None = None) -> SearchResponse:
    """Search TMDB and Jikan concurrently for titles matching *query*."""
    return asyncio.run(_search(query, media_type))


async def _search(query: str, media_type: str | None) -> SearchResponse:
    async with _make_tmdb_client() as tmdb, JikanClient() as jikan:
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


def fetch_details(
    source: str, external_id: str, media_type: str | None = None
) -> ShowDetails | MovieDetails:
    """Fetch details for a title from the appropriate API.

    *media_type* ("movie" or "show") disambiguates TMDB ids, which have
    separate namespaces for movies and TV shows.
    """
    return asyncio.run(_fetch_details(source, external_id, media_type))


async def _fetch_details(
    source: str, external_id: str, media_type: str | None = None
) -> ShowDetails | MovieDetails:
    async with _make_tmdb_client() as tmdb, JikanClient() as jikan:
        return await _fetch_details_with_clients(source, external_id, tmdb, jikan, media_type)


async def _fetch_details_with_clients(
    source: str,
    external_id: str,
    tmdb: TMDBClient,
    jikan: JikanClient,
    media_type: str | None = None,
) -> ShowDetails | MovieDetails:
    """Fetch details using pre-existing client instances (used by sync).

    TMDB movie and TV ids live in separate namespaces, so a single numeric
    id can identify a *different* movie and a *different* show.  When
    *media_type* is given only the matching endpoint is queried; otherwise
    both are tried and, if both succeed, an error is raised asking the
    caller to disambiguate rather than silently picking the wrong title.
    """
    src = source.lower()
    mt = media_type.lower() if media_type else None

    if src == "tmdb":
        if mt == "movie":
            return await tmdb.get_movie(external_id)
        if mt == "show":
            return await tmdb.get_tv(external_id)

        movie_r, tv_r = await asyncio.gather(
            tmdb.get_movie(external_id),
            tmdb.get_tv(external_id),
            return_exceptions=True,
        )
        if isinstance(movie_r, MovieDetails) and isinstance(tv_r, ShowDetails):
            raise ValueError(
                f"TMDB id {external_id} matches both a movie "
                f"({movie_r.title!r}) and a show ({tv_r.title!r}). "
                "Re-run with --type movie or --type show to choose."
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
        async with _make_tmdb_client() as tmdb:
            season = await tmdb.get_tv_season(external_id, season_number)
            return season.episodes
    if src == "jikan":
        async with JikanClient() as jikan:
            return await jikan.get_anime_episodes(external_id)
    raise ValueError(f"Unknown source: {source!r}")


# ---------------------------------------------------------------------------
# Tracking operations
# ---------------------------------------------------------------------------


def add_tracked_item(source: str, external_id: str, media_type: str | None = None) -> TrackedItem:
    """Fetch details for a title and add it to the tracking list.

    *media_type* disambiguates TMDB ids that match both a movie and a show.
    """
    details = fetch_details(source, external_id, media_type)

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


def _count_watched_episodes(session: Session, item_id: int) -> int:
    """Count watched episode rows for *item_id*, excluding the movie sentinel."""
    return (
        session.query(WatchedEpisode)
        .filter(
            WatchedEpisode.tracked_item_id == item_id,
            WatchedEpisode.season_number != _MOVIE_SEASON,
        )
        .count()
    )


def set_watch_status(item_id: int, status: str) -> TrackedItem:
    """Update the watch status of a tracked item.

    When a show is marked **completed**, all of its episodes are also marked
    watched (episode counts are fetched from the API so that episodes not yet
    aired locally are still recorded).

    Raises ``ValueError`` if the item does not exist or *status* is invalid.
    """
    new_status = WatchStatus(status)

    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")

        if item.media_type == MediaType.SHOW and new_status == WatchStatus.COMPLETED:
            source = item.source.value
            external_id = item.external_id
            title = item.title
            watched = {
                (we.season_number, we.episode_number)
                for we in session.query(WatchedEpisode).filter_by(tracked_item_id=item_id)
            }
        else:
            item.status = new_status
            return item

    # Show being marked completed — fetch full episode structure and fill in gaps.
    details = fetch_details(source, external_id, "show")
    if not isinstance(details, ShowDetails):
        raise ValueError(f"Could not load episode data for '{title}'.")

    seasons = sorted(
        (s for s in details.seasons if s.season_number > 0 and s.episode_count > 0),
        key=lambda s: s.season_number,
    )

    with session_scope() as session:
        for s in seasons:
            for ep_num in range(1, s.episode_count + 1):
                if (s.season_number, ep_num) not in watched:
                    session.add(
                        WatchedEpisode(
                            tracked_item_id=item_id,
                            season_number=s.season_number,
                            episode_number=ep_num,
                        )
                    )
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        item.status = new_status
        return item


def mark_watched(
    item_id: int, season: int | None = None, episode: int | None = None
) -> TrackedItem:
    """Mark a movie or episode as watched.

    For movies, omit *season* and *episode*.
    For shows, both *season* and *episode* are required.

    When a show's watched episode count reaches its ``total_episodes`` (the
    number currently available), its status is automatically set to
    ``COMPLETED`` — but only if it was already ``WATCHING``, so shows the
    user deliberately put on hold or dropped are left untouched.

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

        if item.media_type == MediaType.SHOW and item.status == WatchStatus.PLANNING:
            item.status = WatchStatus.WATCHING

        # Auto-complete a show once every available episode has been watched.
        # Only promoted from WATCHING so on_hold/dropped statuses are respected.
        if (
            item.media_type == MediaType.SHOW
            and item.status == WatchStatus.WATCHING
            and item.total_episodes is not None
            and _count_watched_episodes(session, item_id) >= item.total_episodes
        ):
            item.status = WatchStatus.COMPLETED

        return item


def mark_next_watched(item_id: int, season: int | None = None) -> tuple[TrackedItem, int, int]:
    """Mark the next unwatched episode of a show as watched.

    If *season* is given, the first unwatched episode within that season is
    marked.  If *season* is None the show's season structure is fetched from
    the API and the first unwatched episode across all non-special seasons
    is marked.

    Returns ``(item, season_number, episode_number)``.
    Raises ``ValueError`` if the item doesn't exist, is a movie, has no
    episode data, or every relevant episode is already watched.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        if item.media_type == MediaType.MOVIE:
            raise ValueError(
                f"'{item.title}' is a movie — use 'tv-tracker watch {item_id}' "
                "without --episode to mark it watched."
            )
        watched = {
            (we.season_number, we.episode_number)
            for we in session.query(WatchedEpisode).filter_by(tracked_item_id=item_id)
        }

    details = fetch_details(item.source.value, item.external_id, "show")
    if not isinstance(details, ShowDetails):
        raise ValueError(f"Could not load season data for '{item.title}'.")

    seasons = sorted(
        (s for s in details.seasons if s.season_number > 0 and s.episode_count > 0),
        key=lambda s: s.season_number,
    )
    if season is not None:
        seasons = [s for s in seasons if s.season_number == season]
        if not seasons:
            raise ValueError(f"'{item.title}' has no season {season} with episodes.")
    if not seasons:
        raise ValueError(
            f"Could not determine episode counts for '{item.title}'. "
            "Specify --season and --episode explicitly."
        )

    target: tuple[int, int] | None = None
    for s in seasons:
        for ep_num in range(1, s.episode_count + 1):
            if (s.season_number, ep_num) not in watched:
                target = (s.season_number, ep_num)
                break
        if target is not None:
            break

    if target is None:
        if season is not None:
            raise ValueError(
                f"All episodes of '{item.title}' season {season} are already watched."
            )
        raise ValueError(f"All episodes of '{item.title}' are already watched.")

    target_season, target_episode = target
    updated = mark_watched(item_id, target_season, target_episode)
    return updated, target_season, target_episode


def mark_all_watched(item_id: int) -> tuple[TrackedItem, int]:
    """Mark every episode of a show (or a movie itself) as watched.

    For shows, fetches the full episode structure from the API and inserts
    ``WatchedEpisode`` rows for every episode not already marked.  For
    movies, marks the movie as watched (if not already).  In both cases the
    watch status is set to ``COMPLETED``.

    Returns ``(item, newly_marked)`` where *newly_marked* is the number of
    episodes/movie newly recorded as watched (0 when already fully watched).
    Raises ``ValueError`` if the item doesn't exist or its episode data
    can't be loaded.
    """
    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        is_movie = item.media_type == MediaType.MOVIE
        source = item.source.value
        external_id = item.external_id
        title = item.title
        watched = {
            (we.season_number, we.episode_number)
            for we in session.query(WatchedEpisode).filter_by(tracked_item_id=item_id)
        }

    if is_movie:
        newly_marked = 0
        if (_MOVIE_SEASON, _MOVIE_EPISODE) not in watched:
            with session_scope() as session:
                session.add(
                    WatchedEpisode(
                        tracked_item_id=item_id,
                        season_number=_MOVIE_SEASON,
                        episode_number=_MOVIE_EPISODE,
                    )
                )
            newly_marked = 1
    else:
        details = fetch_details(source, external_id, "show")
        if not isinstance(details, ShowDetails):
            raise ValueError(f"Could not load episode data for '{title}'.")

        seasons = sorted(
            (s for s in details.seasons if s.season_number > 0 and s.episode_count > 0),
            key=lambda s: s.season_number,
        )

        to_add: list[tuple[int, int]] = []
        for s in seasons:
            for ep_num in range(1, s.episode_count + 1):
                if (s.season_number, ep_num) not in watched:
                    to_add.append((s.season_number, ep_num))

        if to_add:
            with session_scope() as session:
                for season_number, episode_number in to_add:
                    session.add(
                        WatchedEpisode(
                            tracked_item_id=item_id,
                            season_number=season_number,
                            episode_number=episode_number,
                        )
                    )
        newly_marked = len(to_add)

    with session_scope() as session:
        item = session.get(TrackedItem, item_id)
        if item is None:
            raise ValueError(f"No tracked item with ID {item_id}")
        item.status = WatchStatus.COMPLETED
        return item, newly_marked


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


# ---------------------------------------------------------------------------
# On-demand sync
# ---------------------------------------------------------------------------


@dataclass
class _SyncItemData:
    """Lightweight snapshot of a tracked item needed for syncing."""

    id: int
    source: str
    external_id: str
    media_type: str


@dataclass
class _SyncFetchResult:
    """Result of fetching fresh details for one tracked item during sync."""

    item_id: int
    details: ShowDetails | MovieDetails | None = None
    error: str | None = None


def _get_sync_items() -> list[_SyncItemData]:
    """Return snapshots of items to sync (watching, planning, or completed).

    Completed shows are included so that newly-aired episodes can be detected
    and the show flipped back to *watching*.
    """
    with session_scope() as session:
        items = (
            session.query(TrackedItem)
            .filter(
                TrackedItem.status.in_(
                    [WatchStatus.WATCHING, WatchStatus.PLANNING, WatchStatus.COMPLETED]
                )
            )
            .all()
        )
        return [
            _SyncItemData(
                id=item.id,
                source=item.source.value,
                external_id=item.external_id,
                media_type=item.media_type.value,
            )
            for item in items
        ]


async def _fetch_all_details(items: list[_SyncItemData]) -> list[_SyncFetchResult]:
    """Fetch fresh details for every sync item, sequentially.

    Both API clients are opened once and reused across all items so that
    rate-limiting and caching work across the entire sync run.
    """
    results: list[_SyncFetchResult] = []
    async with _make_tmdb_client() as tmdb, JikanClient() as jikan:
        for item in items:
            try:
                details = await _fetch_details_with_clients(
                    item.source, item.external_id, tmdb, jikan, item.media_type
                )
                results.append(_SyncFetchResult(item_id=item.id, details=details))
            except Exception as exc:
                results.append(_SyncFetchResult(item_id=item.id, error=str(exc)))
    return results


def _process_sync_results(results: list[_SyncFetchResult]) -> SyncResult:
    """Update tracked item totals from fetched details."""
    summary = SyncResult()
    now = datetime.now(UTC)

    with session_scope() as session:
        for fr in results:
            item = session.get(TrackedItem, fr.item_id)
            if item is None:
                continue

            if fr.error is not None:
                summary.errors.append(f"{item.title}: {fr.error}")
                continue

            if fr.details is None:
                summary.errors.append(f"{item.title}: no details returned")
                continue

            summary.items_synced += 1

            if isinstance(fr.details, MovieDetails):
                item.last_synced_at = now
                continue

            item.total_seasons = fr.details.number_of_seasons or None
            item.total_episodes = fr.details.number_of_episodes or None
            item.last_synced_at = now

            watched_count = _count_watched_episodes(session, item.id)

            # All available episodes watched — auto-complete.
            if (
                item.status == WatchStatus.WATCHING
                and item.total_episodes is not None
                and watched_count >= item.total_episodes
            ):
                item.status = WatchStatus.COMPLETED
                summary.completed.append(item.title)

            # New episodes arrived for a completed show — resume watching.
            elif (
                item.status == WatchStatus.COMPLETED
                and item.total_episodes is not None
                and watched_count < item.total_episodes
            ):
                item.status = WatchStatus.WATCHING
                summary.resumed.append(item.title)

    return summary


def run_sync() -> SyncResult:
    """Run an on-demand sync of all watching/planning/completed tracked items.

    Fetches fresh data from TMDB/Jikan for each item and updates
    ``total_seasons``, ``total_episodes``, and ``last_synced_at``.  Shows
    that have watched every available episode are auto-completed (listed in
    :pyattr:`SyncResult.completed`); completed shows that gain new episodes
    are flipped back to *watching* (listed in :pyattr:`SyncResult.resumed`).
    """
    items = _get_sync_items()
    if not items:
        return SyncResult()

    fetch_results = asyncio.run(_fetch_all_details(items))
    return _process_sync_results(fetch_results)


def get_shows_with_unwatched_episodes() -> list[TrackedItem]:
    """Return shows where the watched episode count is less than the total.

    Only items with ``media_type = show`` and a non-null ``total_episodes``
    are considered.  Movies are excluded — they are either watched or not.
    """
    with session_scope() as session:
        watched_counts = (
            session.query(
                WatchedEpisode.tracked_item_id,
                func.count(WatchedEpisode.id).label("watched_count"),
            )
            .filter(WatchedEpisode.season_number != 0)
            .group_by(WatchedEpisode.tracked_item_id)
            .subquery()
        )

        items = (
            session.query(TrackedItem)
            .outerjoin(
                watched_counts,
                watched_counts.c.tracked_item_id == TrackedItem.id,
            )
            .options(selectinload(TrackedItem.watched_episodes))
            .filter(
                TrackedItem.media_type == MediaType.SHOW,
                TrackedItem.total_episodes.is_not(None),
                TrackedItem.total_episodes > 0,
            )
            .filter(
                (watched_counts.c.watched_count.is_(None))
                | (watched_counts.c.watched_count < TrackedItem.total_episodes)
            )
            .order_by(TrackedItem.title)
            .all()
        )
        return items


def get_unwatched_movies() -> list[TrackedItem]:
    """Return movies that have not been marked as watched.

    A movie is considered watched when it has the sentinel
    ``(season 0, episode 0)`` :class:`WatchedEpisode` row.
    """
    with session_scope() as session:
        watched_ids = (
            session.query(WatchedEpisode.tracked_item_id)
            .filter(WatchedEpisode.season_number == _MOVIE_SEASON)
            .subquery()
        )
        items = (
            session.query(TrackedItem)
            .filter(
                TrackedItem.media_type == MediaType.MOVIE,
                ~TrackedItem.id.in_(select(watched_ids.c.tracked_item_id)),
            )
            .order_by(TrackedItem.title)
            .all()
        )
        return items


def get_stats() -> Stats:
    """Return aggregate counts of tracked items grouped by status and type."""
    with session_scope() as session:
        total = session.query(TrackedItem).count()
        movies = (
            session.query(TrackedItem).filter(TrackedItem.media_type == MediaType.MOVIE).count()
        )
        shows = session.query(TrackedItem).filter(TrackedItem.media_type == MediaType.SHOW).count()
        status_counts: dict[WatchStatus, int] = {}
        for row in (
            session.query(TrackedItem.status, func.count(TrackedItem.id))
            .group_by(TrackedItem.status)
            .all()
        ):
            status_counts[row[0]] = row[1]

    return Stats(
        total=total,
        movies=movies,
        shows=shows,
        planning=status_counts.get(WatchStatus.PLANNING, 0),
        watching=status_counts.get(WatchStatus.WATCHING, 0),
        completed=status_counts.get(WatchStatus.COMPLETED, 0),
        on_hold=status_counts.get(WatchStatus.ON_HOLD, 0),
        dropped=status_counts.get(WatchStatus.DROPPED, 0),
    )
