#!/usr/bin/env python3
"""Import TV Time GDPR export data into the tv-tracker database.

Resolves TheTVDB show IDs to TMDB IDs via TMDB's ``/find`` endpoint,
maps watch statuses, imports watched episodes, and resolves movie
titles via TMDB search.  Entries that cannot be resolved are recorded
in ``import_report.json`` with all their original CSV data so they can
be imported manually or re-tried later.

Usage:
    uv run python scripts/import_tvtime.py [--data-dir asdf/] [--dry-run] [--verbose]

Prerequisites:
    TMDB API key or access token must be set in the app's Config tab
    (stored in the database).  No TVDB API key is needed — TMDB's
    ``/find?external_source=tvdb_id`` endpoint handles the conversion.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tv_tracker.api.tmdb import TMDBClient
from tv_tracker.db import init_db, session_scope
from tv_tracker.models import MediaType, Source, TrackedItem, WatchedEpisode, WatchStatus
from tv_tracker.settings_store import get_tmdb_access_token, get_tmdb_api_key

log = logging.getLogger("import_tvtime")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ShowRecord:
    """A merged show entry from multiple TV Time CSV sources."""

    tvdb_id: str
    name: str
    active: bool = True
    archived: bool = False
    is_followed: bool = True
    is_for_later: bool = False
    special_status: str | None = None
    nb_episodes_seen: int = 0
    watched_episodes: list[WatchedEpisodeRow] = field(default_factory=list)


@dataclass
class WatchedEpisodeRow:
    """A single watched-episode row from the v2 tracking export."""

    season_number: int
    episode_number: int
    created_at: datetime | None


@dataclass
class MovieRecord:
    """A movie entry from the v1 tracking export."""

    name: str
    watched: bool
    release_date: str | None = None
    watched_at: datetime | None = None


@dataclass
class ResolvedShow:
    """A show that has been resolved to a TMDB ID."""

    record: ShowRecord
    source: Source
    external_id: str
    title: str
    total_seasons: int | None = None
    total_episodes: int | None = None


@dataclass
class ResolvedMovie:
    """A movie that has been resolved to a TMDB ID."""

    record: MovieRecord
    source: Source
    external_id: str
    title: str


@dataclass
class ImportReport:
    """Accumulated results and failures for the import run."""

    shows_total: int = 0
    shows_imported: int = 0
    shows_skipped_existing: int = 0
    shows_failed: int = 0
    episodes_imported: int = 0
    episodes_skipped: int = 0
    movies_total: int = 0
    movies_imported: int = 0
    movies_skipped_existing: int = 0
    movies_failed: int = 0
    failed_shows: list[dict] = field(default_factory=list)
    failed_movies: list[dict] = field(default_factory=list)

    def dump(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {"failed_shows": self.failed_shows, "failed_movies": self.failed_movies},
                indent=2,
                ensure_ascii=False,
            )
        )

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("Import Summary")
        print("=" * 60)
        print(
            f"  Shows:   {self.shows_imported} imported, "
            f"{self.shows_skipped_existing} already existed, "
            f"{self.shows_failed} failed (of {self.shows_total})"
        )
        print(
            f"  Episodes: {self.episodes_imported} imported, "
            f"{self.episodes_skipped} already existed"
        )
        print(
            f"  Movies:  {self.movies_imported} imported, "
            f"{self.movies_skipped_existing} already existed, "
            f"{self.movies_failed} failed (of {self.movies_total})"
        )
        print(f"  Failed shows recorded:  {len(self.failed_shows)}")
        print(f"  Failed movies recorded: {len(self.failed_movies)}")
        print("=" * 60)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _parse_dt(value: str) -> datetime | None:
    """Parse a TV Time datetime string (``YYYY-MM-DD HH:MM:SS``) as UTC."""
    value = value.strip()
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_int(value: str) -> int:
    value = value.strip()
    if not value:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def load_shows(data_dir: Path) -> dict[str, ShowRecord]:
    """Build a merged show dict keyed by TVDB ID from all CSV sources.

    Combines:
      - followed_tv_show.csv (active/archived flags, TVDB ID)
      - tracking-prod-records-v2.csv user-series rows (is_for_later/is_archived/is_followed)
      - tracking-prod-records-v2.csv watch-episode rows (watched episodes)
      - user_show_special_status.csv (for_later/favorite)
      - user_tv_show_data.csv (nb_episodes_seen)
    """
    shows: dict[str, ShowRecord] = {}

    # 1. followed_tv_show.csv — primary show list
    with (data_dir / "followed_tv_show.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tvdb_id = row["tv_show_id"].strip()
            if not tvdb_id:
                continue
            shows[tvdb_id] = ShowRecord(
                tvdb_id=tvdb_id,
                name=row["tv_show_name"].strip(),
                active=row["active"].strip() == "1",
                archived=row["archived"].strip() == "1",
                is_followed=row["active"].strip() == "1",
            )

    # 2. v2 user-series rows — supplement with for_later/archived flags
    with (data_dir / "tracking-prod-records-v2.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row["key"].startswith("user-series-"):
                continue
            tvdb_id = row.get("s_id", "").strip()
            if not tvdb_id:
                continue
            name = row.get("series_name", "").strip()
            if tvdb_id in shows:
                rec = shows[tvdb_id]
                if not rec.name:
                    rec.name = name
            else:
                rec = ShowRecord(tvdb_id=tvdb_id, name=name)
                shows[tvdb_id] = rec
            rec.is_for_later = rec.is_for_later or row.get("is_for_later") == "true"
            rec.archived = rec.archived or row.get("is_archived") == "true"
            rec.is_followed = rec.is_followed or row.get("is_followed") == "true"

    # 3. v2 watch-episode rows — watched episodes
    with (data_dir / "tracking-prod-records-v2.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row["key"].startswith("watch-episode-"):
                continue
            tvdb_id = row.get("s_id", "").strip()
            season = _parse_int(row.get("season_number", ""))
            episode = _parse_int(row.get("episode_number", ""))
            if season <= 0 or episode <= 0:
                continue  # skip specials and invalid rows
            watched_at = _parse_dt(row.get("created_at", ""))
            if tvdb_id in shows:
                shows[tvdb_id].watched_episodes.append(
                    WatchedEpisodeRow(season, episode, watched_at)
                )
            else:
                # Show has watched episodes but wasn't in the followed list
                name = row.get("series_name", "").strip()
                rec = ShowRecord(tvdb_id=tvdb_id, name=name, is_followed=False, active=False)
                rec.watched_episodes.append(WatchedEpisodeRow(season, episode, watched_at))
                shows[tvdb_id] = rec

    # 4. user_show_special_status.csv — for_later / favorite
    special_path = data_dir / "user_show_special_status.csv"
    if special_path.exists():
        with special_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tvdb_id = row["tv_show_id"].strip()
                status = row["status"].strip()
                if tvdb_id in shows:
                    shows[tvdb_id].special_status = status
                    if status == "for_later":
                        shows[tvdb_id].is_for_later = True

    # 5. user_tv_show_data.csv — nb_episodes_seen (cross-check for status)
    utd_path = data_dir / "user_tv_show_data.csv"
    if utd_path.exists():
        with utd_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tvdb_id = row["tv_show_id"].strip()
                if tvdb_id in shows:
                    shows[tvdb_id].nb_episodes_seen = _parse_int(row.get("nb_episodes_seen", ""))

    return shows


def load_movies(data_dir: Path) -> list[MovieRecord]:
    """Parse movies from the v1 tracking export.

    Movies with a ``watch`` type row are watched (completed); movies with
    only a ``follow`` row are planning.
    """
    movies: dict[str, dict] = {}
    with (data_dir / "tracking-prod-records.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("movie_name", "").strip()
            if not name:
                continue
            rtype = row.get("type", "").strip()
            release = row.get("release_date", "").strip()
            if release.startswith("0001-01-01"):
                release = None
            elif release:
                release = release[:10]
            if name not in movies:
                movies[name] = {"watched": False, "release_date": release, "watched_at": None}
            if rtype == "watch":
                movies[name]["watched"] = True
                watched_at = _parse_dt(row.get("created_at", ""))
                if watched_at is not None:
                    movies[name]["watched_at"] = watched_at

    return [
        MovieRecord(
            name=name,
            watched=data["watched"],
            release_date=data.get("release_date"),
            watched_at=data.get("watched_at"),
        )
        for name, data in movies.items()
    ]


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def determine_show_status(rec: ShowRecord) -> WatchStatus:
    """Map TV Time flags to a :class:`WatchStatus` value.

    | TV Time signal                              | Status      |
    |---------------------------------------------|-------------|
    | is_for_later / special_status=for_later     | planning    |
    | archived                                    | on_hold     |
    | not followed                                | dropped     |
    | followed + has watched episodes             | watching    |
    | followed + no watched episodes              | planning    |
    """
    if rec.is_for_later or rec.special_status == "for_later":
        return WatchStatus.PLANNING

    if rec.archived:
        return WatchStatus.ON_HOLD

    has_progress = bool(rec.watched_episodes) or rec.nb_episodes_seen > 0

    if not rec.is_followed or not rec.active:
        return WatchStatus.DROPPED

    return WatchStatus.WATCHING if has_progress else WatchStatus.PLANNING


def determine_movie_status(rec: MovieRecord) -> WatchStatus:
    return WatchStatus.COMPLETED if rec.watched else WatchStatus.PLANNING


# ---------------------------------------------------------------------------
# Serialisation for failure report
# ---------------------------------------------------------------------------


def show_to_dict(rec: ShowRecord) -> dict:
    """Serialise a :class:`ShowRecord` with all data needed for a later import."""
    return {
        "tvdb_id": rec.tvdb_id,
        "name": rec.name,
        "active": rec.active,
        "archived": rec.archived,
        "is_followed": rec.is_followed,
        "is_for_later": rec.is_for_later,
        "special_status": rec.special_status,
        "nb_episodes_seen": rec.nb_episodes_seen,
        "mapped_status": determine_show_status(rec).value,
        "watched_episodes": [
            {
                "season_number": ep.season_number,
                "episode_number": ep.episode_number,
                "watched_at": ep.created_at.isoformat() if ep.created_at else None,
            }
            for ep in rec.watched_episodes
        ],
    }


def movie_to_dict(rec: MovieRecord) -> dict:
    """Serialise a :class:`MovieRecord` with all data needed for a later import."""
    return {
        "name": rec.name,
        "watched": rec.watched,
        "release_date": rec.release_date,
        "watched_at": rec.watched_at.isoformat() if rec.watched_at else None,
        "mapped_status": determine_movie_status(rec).value,
    }


# ---------------------------------------------------------------------------
# ID resolution (async)
# ---------------------------------------------------------------------------


def _make_tmdb_client() -> TMDBClient:
    api_key = get_tmdb_api_key()
    access_token = get_tmdb_access_token()
    if not api_key and not access_token:
        raise RuntimeError("No TMDB credentials found. Set them via the Config tab in the TUI.")
    return TMDBClient(api_key=api_key, access_token=access_token)


async def resolve_show(
    rec: ShowRecord,
    tmdb: TMDBClient,
    report: ImportReport,
) -> ResolvedShow | None:
    """Resolve a show's TVDB ID to a TMDB ID via ``/find?external_source=tvdb_id``.

    No title-based fallback is attempted.  If the TVDB ID cannot be
    resolved, all of the show's data (including watched episodes) is
    recorded in the failure report for a manual or later retry.
    """
    try:
        details = await tmdb.find_by_tvdb_id(rec.tvdb_id)
        if details is not None:
            return ResolvedShow(
                record=rec,
                source=Source.TMDB,
                external_id=details.external_id,
                title=details.title,
                total_seasons=details.number_of_seasons or None,
                total_episodes=details.number_of_episodes or None,
            )
    except Exception as exc:
        log.warning("TMDB find failed for tvdb_id=%s: %s", rec.tvdb_id, exc)

    report.failed_shows.append(show_to_dict(rec))
    return None


async def resolve_movie(
    rec: MovieRecord,
    tmdb: TMDBClient,
    report: ImportReport,
) -> ResolvedMovie | None:
    """Resolve a movie title to a TMDB ID via ``/search/movie``.

    If no result is returned, all of the movie's data is recorded in
    the failure report for a manual or later retry.
    """
    if not rec.name:
        report.failed_movies.append(movie_to_dict(rec))
        return None

    try:
        results = await tmdb.search_movies(rec.name)
        if results:
            best = results[0]
            return ResolvedMovie(
                record=rec,
                source=Source.TMDB,
                external_id=best.external_id,
                title=best.title,
            )
    except Exception as exc:
        log.warning("TMDB movie search failed for '%s': %s", rec.name, exc)

    report.failed_movies.append(movie_to_dict(rec))
    return None


async def resolve_all_shows(
    shows: dict[str, ShowRecord],
    report: ImportReport,
    concurrency: int = 20,
) -> list[ResolvedShow]:
    """Resolve all shows concurrently with bounded concurrency."""
    semaphore = asyncio.Semaphore(concurrency)
    resolved: list[ResolvedShow] = []

    async with _make_tmdb_client() as tmdb:

        async def _resolve(rec: ShowRecord) -> None:
            async with semaphore:
                result = await resolve_show(rec, tmdb, report)
                if result is not None:
                    resolved.append(result)

        tasks = [asyncio.create_task(_resolve(rec)) for rec in shows.values()]
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            await task
            if i % 50 == 0:
                print(f"  ...resolved {i}/{len(tasks)} shows")

    return resolved


async def resolve_all_movies(
    movies: list[MovieRecord],
    report: ImportReport,
    concurrency: int = 20,
) -> list[ResolvedMovie]:
    """Resolve all movies concurrently with bounded concurrency."""
    semaphore = asyncio.Semaphore(concurrency)
    resolved: list[ResolvedMovie] = []

    async with _make_tmdb_client() as tmdb:

        async def _resolve(rec: MovieRecord) -> None:
            async with semaphore:
                result = await resolve_movie(rec, tmdb, report)
                if result is not None:
                    resolved.append(result)

        tasks = [asyncio.create_task(_resolve(rec)) for rec in movies]
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            await task
            if i % 50 == 0:
                print(f"  ...resolved {i}/{len(tasks)} movies")

    return resolved


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------

_MOVIE_SEASON = 0
_MOVIE_EPISODE = 0


def import_shows(resolved: list[ResolvedShow], report: ImportReport, dry_run: bool) -> None:
    """Insert resolved shows and their watched episodes into the database."""
    for rs in resolved:
        status = determine_show_status(rs.record)

        if dry_run:
            report.shows_imported += 1
            report.episodes_imported += len(rs.record.watched_episodes)
            continue

        with session_scope() as session:
            existing = (
                session.query(TrackedItem)
                .filter_by(source=rs.source, external_id=rs.external_id)
                .first()
            )
            if existing is not None:
                report.shows_skipped_existing += 1
                item_id = existing.id
                if existing.status != status:
                    existing.status = status
            else:
                item = TrackedItem(
                    external_id=rs.external_id,
                    source=rs.source,
                    media_type=MediaType.SHOW,
                    title=rs.title,
                    status=status,
                    total_seasons=rs.total_seasons,
                    total_episodes=rs.total_episodes,
                )
                session.add(item)
                session.flush()
                item_id = item.id
                report.shows_imported += 1

            # Insert watched episodes
            existing_eps = {
                (we.season_number, we.episode_number)
                for we in session.query(WatchedEpisode).filter_by(tracked_item_id=item_id)
            }
            for ep in rs.record.watched_episodes:
                key = (ep.season_number, ep.episode_number)
                if key in existing_eps:
                    report.episodes_skipped += 1
                    continue
                session.add(
                    WatchedEpisode(
                        tracked_item_id=item_id,
                        season_number=ep.season_number,
                        episode_number=ep.episode_number,
                        watched_at=ep.created_at or datetime.now(UTC),
                    )
                )
                existing_eps.add(key)
                report.episodes_imported += 1

            # Auto-complete: if every available episode has been watched,
            # override the mapped status to completed.
            if rs.total_episodes and len(existing_eps) >= rs.total_episodes:
                item_ref = session.get(TrackedItem, item_id)
                if item_ref is not None and item_ref.status != WatchStatus.COMPLETED:
                    item_ref.status = WatchStatus.COMPLETED


def import_movies(resolved: list[ResolvedMovie], report: ImportReport, dry_run: bool) -> None:
    """Insert resolved movies into the database."""
    for rm in resolved:
        status = determine_movie_status(rm.record)

        if dry_run:
            report.movies_imported += 1
            continue

        with session_scope() as session:
            existing = (
                session.query(TrackedItem)
                .filter_by(source=rm.source, external_id=rm.external_id)
                .first()
            )
            if existing is not None:
                report.movies_skipped_existing += 1
                item_id = existing.id
                if existing.status != status:
                    existing.status = status
            else:
                item = TrackedItem(
                    external_id=rm.external_id,
                    source=rm.source,
                    media_type=MediaType.MOVIE,
                    title=rm.title,
                    status=status,
                )
                session.add(item)
                session.flush()
                item_id = item.id
                report.movies_imported += 1

            # For watched movies, insert the sentinel WatchedEpisode row
            if rm.record.watched:
                existing_ep = (
                    session.query(WatchedEpisode)
                    .filter_by(
                        tracked_item_id=item_id,
                        season_number=_MOVIE_SEASON,
                        episode_number=_MOVIE_EPISODE,
                    )
                    .first()
                )
                if existing_ep is None:
                    session.add(
                        WatchedEpisode(
                            tracked_item_id=item_id,
                            season_number=_MOVIE_SEASON,
                            episode_number=_MOVIE_EPISODE,
                            watched_at=rm.record.watched_at or datetime.now(UTC),
                        )
                    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import TV Time GDPR export data into tv-tracker."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("asdf"),
        help="Directory containing the TV Time CSV export (default: asdf)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve IDs and report without writing to the database",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=Path("import_report.json"),
        help="Where to write the failure report (default: import_report.json)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    data_dir = args.data_dir.resolve()
    if not data_dir.is_dir():
        print(f"Error: data directory '{data_dir}' not found", file=sys.stderr)
        return 1

    # Check required CSV files
    required = [
        "followed_tv_show.csv",
        "tracking-prod-records-v2.csv",
        "tracking-prod-records.csv",
    ]
    for fname in required:
        if not (data_dir / fname).exists():
            print(f"Error: required file '{fname}' not found in '{data_dir}'", file=sys.stderr)
            return 1

    # Initialise DB (creates tables if needed)
    init_db()

    # Check TMDB credentials early
    if not get_tmdb_api_key() and not get_tmdb_access_token():
        print(
            "Error: No TMDB credentials found. Set them via the Config tab "
            "in the TUI (press 4, then 'Set API Key' or 'Set Token').",
            file=sys.stderr,
        )
        return 1

    report = ImportReport()

    # --- Load CSV data ---
    print(f"Loading TV Time data from {data_dir} ...")
    shows = load_shows(data_dir)
    movies = load_movies(data_dir)
    print(f"  Found {len(shows)} shows, {len(movies)} movies")

    report.shows_total = len(shows)
    report.movies_total = len(movies)

    # --- Resolve IDs ---
    print(f"\nResolving {len(shows)} show IDs (TVDB -> TMDB) ...")
    resolved_shows = asyncio.run(resolve_all_shows(shows, report))
    report.shows_failed = len(shows) - len(resolved_shows)
    print(f"  Resolved {len(resolved_shows)}/{len(shows)} shows")

    print(f"\nResolving {len(movies)} movie IDs (title -> TMDB) ...")
    resolved_movies = asyncio.run(resolve_all_movies(movies, report))
    report.movies_failed = len(movies) - len(resolved_movies)
    print(f"  Resolved {len(resolved_movies)}/{len(movies)} movies")

    # --- Import to DB ---
    if args.dry_run:
        print("\n[dry-run] Skipping database writes.")
    else:
        print("\nImporting shows and episodes into database ...")
    import_shows(resolved_shows, report, dry_run=args.dry_run)

    print("Importing movies into database ...")
    import_movies(resolved_movies, report, dry_run=args.dry_run)

    # --- Report ---
    report.dump(args.report_file)
    report.print_summary()
    print(f"\nFailure details written to {args.report_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
