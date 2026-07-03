"""Typer-based CLI for TV Tracker."""

from __future__ import annotations

import textwrap
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tv_tracker import __version__
from tv_tracker.api import EpisodeInfo, MovieDetails, ShowDetails
from tv_tracker.db import init_db
from tv_tracker.models import MediaType, TrackedItem, WatchStatus
from tv_tracker.services import (
    VALID_MEDIA_TYPES,
    VALID_SOURCES,
    VALID_STATUSES,
    add_tracked_item,
    fetch_details,
    fetch_season_episodes,
    find_tracked_item,
    get_currently_watching,
    get_recently_completed,
    get_shows_with_unwatched_episodes,
    get_stats,
    get_watched_episode_keys,
    list_tracked_items,
    mark_next_watched,
    mark_watched,
    remove_tracked_item,
    run_sync,
    set_watch_status,
    unmark_watched,
)
from tv_tracker.services import search as search_titles
from tv_tracker.settings_store import (
    delete_setting,
    get_tmdb_access_token,
    get_tmdb_api_key,
    set_tmdb_access_token,
    set_tmdb_api_key,
)

app = typer.Typer(
    name="tv-tracker",
    help="Track movies and shows (including anime) from the command line.",
    no_args_is_help=False,
)
console = Console()

_STATUS_STYLES: dict[WatchStatus, str] = {
    WatchStatus.PLANNING: "blue",
    WatchStatus.WATCHING: "bold green",
    WatchStatus.COMPLETED: "cyan",
    WatchStatus.ON_HOLD: "yellow",
    WatchStatus.DROPPED: "red",
}


def _status_badge(status: WatchStatus) -> str:
    style = _STATUS_STYLES.get(status, "white")
    return f"[{style}]{status.value}[/{style}]"


def _truncate(text: str | None, width: int = 60) -> str:
    if not text:
        return "[dim]—[/dim]"
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


def _year(release_date: str | None) -> str:
    if not release_date:
        return "[dim]—[/dim]"
    return release_date[:4]


def _validate_source(source: str) -> None:
    if source.lower() not in VALID_SOURCES:
        console.print(
            f"[red]Invalid source '{source}'. Choose from: {', '.join(VALID_SOURCES)}[/red]"
        )
        raise typer.Exit(1)


def _validate_media_type(media_type: str | None) -> None:
    if media_type is not None and media_type not in VALID_MEDIA_TYPES:
        console.print(
            f"[red]Invalid type '{media_type}'. Choose from: {', '.join(VALID_MEDIA_TYPES)}[/red]"
        )
        raise typer.Exit(1)


def _validate_status(status: str | None) -> None:
    if status is not None and status not in VALID_STATUSES:
        console.print(
            f"[red]Invalid status '{status}'. Choose from: {', '.join(VALID_STATUSES)}[/red]"
        )
        raise typer.Exit(1)


def _ensure_tmdb_credentials() -> None:
    """Prompt for a TMDB API key if none is stored in the database."""
    if get_tmdb_api_key() or get_tmdb_access_token():
        return

    console.print()
    console.print("[cyan]TMDB API key required to search and track titles.[/cyan]")
    console.print(
        "[dim]Get a free key at "
        "https://www.themoviedb.org/settings/api[/dim]"
    )
    console.print()
    key = typer.prompt("Enter your TMDB API key", hide_input=True, default="")
    key = key.strip()
    if not key:
        console.print("[red]No API key provided.[/red]")
        raise typer.Exit(1)
    set_tmdb_api_key(key)
    console.print("[green]API key saved to database.[/green]")
    console.print()


def _print_api_error(action: str, exc: Exception) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            console.print(
                f"[red]Could not {action}: authentication failed (HTTP 401).[/red]\n"
                "[dim]Run 'tv-tracker config --set-key' to update your TMDB API key.[/dim]"
            )
        elif status == 404:
            console.print(
                f"[red]Could not {action}: title not found (HTTP 404).[/red]"
            )
        elif status == 429:
            console.print(
                f"[red]Could not {action}: rate limit exceeded (HTTP 429).[/red]\n"
                "[dim]Wait a moment and try again.[/dim]"
            )
        else:
            console.print(
                f"[red]Could not {action}: HTTP {status}[/red]\n[dim]{exc}[/dim]"
            )
    elif isinstance(exc, httpx.TimeoutException):
        console.print(
            f"[red]Could not {action}: request timed out.[/red]\n"
            "[dim]Check your network connection and try again.[/dim]"
        )
    elif isinstance(exc, httpx.ConnectError):
        console.print(
            f"[red]Could not {action}: could not connect to the API.[/red]\n"
            "[dim]Check your network connection and try again.[/dim]"
        )
    elif isinstance(exc, httpx.RequestError):
        console.print(f"[red]Could not {action}: network error[/red]\n[dim]{exc}[/dim]")
    else:
        console.print(f"[red]Could not {action}: {exc}[/red]")


_BAR_WIDTH = 10
_BAR_FULL = "\u2588"
_BAR_EMPTY = "\u2591"


def _progress_bar(watched: int, total: int | None) -> Text:
    """Return a Rich ``Text`` progress bar suitable for a table cell."""
    if total is None or total == 0:
        return Text(f"{watched}/?", style="dim")
    filled = _BAR_WIDTH * watched // total if total > 0 else 0
    bar = Text()
    if filled > 0:
        if watched >= total:
            bar.append(_BAR_FULL * filled, style="green")
        else:
            bar.append(_BAR_FULL * filled, style="cyan")
    bar.append(_BAR_EMPTY * (_BAR_WIDTH - filled), style="dim")
    bar.append(f" {watched}/{total}")
    return bar


def _last_watched_label(item: TrackedItem) -> str:
    """Return a 'S01E05' label for the most recently watched episode, or '—'."""
    if not item.watched_episodes:
        return "[dim]—[/dim]"
    last = max(item.watched_episodes, key=lambda e: (e.season_number, e.episode_number))
    if last.season_number == 0:
        return "[green]watched[/green]"
    return f"S{last.season_number:02}E{last.episode_number:02}"


def _next_episode_label(item: TrackedItem) -> str:
    """Estimate the next unwatched episode label from watched records."""
    if not item.watched_episodes:
        return "S01E01"
    last = max(item.watched_episodes, key=lambda e: (e.season_number, e.episode_number))
    if last.season_number == 0:
        return "[dim]—[/dim]"
    return f"S{last.season_number:02}E{last.episode_number + 1:02}"


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Open the dashboard (uses cached data, no sync)."""
    if ctx.invoked_subcommand is not None:
        return
    init_db()

    console.print(
        Panel.fit(
            f"[bold cyan]TV Tracker[/bold cyan] v{__version__}",
            border_style="cyan",
        )
    )

    _render_stats_summary()
    _render_currently_watching()
    _render_unwatched_shows()
    _render_recently_completed()

    console.print(
        "\n[dim]Run [/dim][bold]tv-tracker alerts[/bold]"
        "[dim] to sync and check for new content.[/dim]"
    )


def _render_stats_summary() -> None:
    """Render a one-line stats summary of the tracking list."""
    try:
        stats = get_stats()
    except Exception:
        return

    if stats.total == 0:
        console.print(
            "[dim]No tracked items yet. Use [/dim]"
            "[bold]tv-tracker search <query>[/bold]"
            "[dim] to find titles to track.[/dim]"
        )
        return

    parts = [
        f"[bold]{stats.total}[/bold] tracked",
        f"[green]{stats.watching}[/green] watching",
        f"[blue]{stats.planning}[/blue] planning",
        f"[cyan]{stats.completed}[/cyan] completed",
    ]
    if stats.on_hold:
        parts.append(f"[yellow]{stats.on_hold}[/yellow] on hold")
    if stats.dropped:
        parts.append(f"[red]{stats.dropped}[/red] dropped")

    type_parts: list[str] = []
    if stats.movies:
        type_parts.append(f"{stats.movies} movie{'s' if stats.movies != 1 else ''}")
    if stats.shows:
        type_parts.append(f"{stats.shows} show{'s' if stats.shows != 1 else ''}")

    summary = "  ".join(parts)
    if type_parts:
        summary += f"  [dim]({', '.join(type_parts)})[/dim]"

    console.print()
    console.print(summary)


def _render_currently_watching() -> None:
    """Render the 'Currently Watching' dashboard section."""
    items = get_currently_watching()
    console.print()
    if not items:
        console.print("[dim]No items currently watching.[/dim]")
        return

    table = Table(
        title="Currently Watching",
        title_style="bold green",
        border_style="green",
    )
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title", min_width=20)
    table.add_column("Type", width=5)
    table.add_column("Progress", width=18)
    table.add_column("Last", width=9)
    table.add_column("Next", width=9)

    for item in items:
        if item.media_type == MediaType.MOVIE:
            is_watched = len(item.watched_episodes) > 0
            progress: Any = (
                Text("watched", style="green")
                if is_watched
                else Text("not watched", style="dim")
            )
            last = _last_watched_label(item)
            next_ep = "[dim]—[/dim]"
        else:
            watched_count = len(item.watched_episodes)
            total = item.total_episodes
            progress = _progress_bar(watched_count, total)
            last = _last_watched_label(item)
            next_ep = _next_episode_label(item)

        table.add_row(
            str(item.id),
            item.title,
            item.media_type.value,
            progress,
            last,
            next_ep,
        )

    console.print(table)


def _render_recently_completed() -> None:
    """Render the 'Recently Completed' dashboard section."""
    items = get_recently_completed()
    console.print()
    if not items:
        return

    table = Table(
        title="Recently Completed",
        title_style="bold cyan",
        border_style="cyan",
    )
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title", min_width=20)
    table.add_column("Type", width=5)
    table.add_column("Progress", width=18)
    table.add_column("Updated", width=12)

    for item in items:
        if item.media_type == MediaType.MOVIE:
            is_watched = len(item.watched_episodes) > 0
            progress: Any = (
                Text("watched", style="green")
                if is_watched
                else Text("not watched", style="dim")
            )
        else:
            watched_count = len(item.watched_episodes)
            total = item.total_episodes
            progress = _progress_bar(watched_count, total)

        updated = item.updated_at.strftime("%Y-%m-%d") if item.updated_at else "[dim]—[/dim]"

        table.add_row(
            str(item.id),
            item.title,
            item.media_type.value,
            progress,
            updated,
        )

    console.print(table)


def _build_unwatched_table(items: list[TrackedItem]) -> Table:
    """Build a Rich table showing shows with unwatched episodes."""
    table = Table(
        title="Unwatched Episodes",
        title_style="bold yellow",
        border_style="yellow",
    )
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title", min_width=20)
    table.add_column("Progress", width=18)
    table.add_column("Unwatched", width=10, justify="right")

    for item in items:
        watched_count = len(item.watched_episodes)
        total = item.total_episodes or 0
        unwatched = total - watched_count
        table.add_row(
            str(item.id),
            item.title,
            _progress_bar(watched_count, total),
            f"[yellow]{unwatched}[/yellow]",
        )
    return table


def _render_unwatched_shows() -> None:
    """Render the 'Unwatched Episodes' dashboard section (uses cached data)."""
    try:
        items = get_shows_with_unwatched_episodes()
    except Exception:
        return

    if not items:
        return

    console.print()
    console.print(_build_unwatched_table(items))


@app.command()
def version() -> None:
    """Show the installed version."""
    console.print(f"TV Tracker v{__version__}")


@app.command()
def init() -> None:
    """Initialise the SQLite database and create all tables."""
    engine = init_db()
    console.print(f"[green]Database initialised:[/green] {engine.url}")


@app.command()
def config(
    set_key: bool = typer.Option(False, "--set-key", help="Prompt to set the TMDB API key"),
    set_token: bool = typer.Option(
        False, "--set-token", help="Prompt to set the TMDB read access token"
    ),
    clear_key: bool = typer.Option(False, "--clear-key", help="Remove the stored TMDB API key"),
    clear_token: bool = typer.Option(
        False, "--clear-token", help="Remove the stored TMDB access token"
    ),
) -> None:
    """Show or update stored TMDB credentials."""
    init_db()

    if clear_key:
        delete_setting("tmdb_api_key")
        console.print("[green]TMDB API key removed.[/green]")

    if clear_token:
        delete_setting("tmdb_access_token")
        console.print("[green]TMDB access token removed.[/green]")

    if set_key:
        key = typer.prompt("Enter TMDB API key", hide_input=True, default="")
        key = key.strip()
        if not key:
            console.print("[red]No API key provided.[/red]")
            raise typer.Exit(1)
        set_tmdb_api_key(key)
        console.print("[green]TMDB API key saved.[/green]")

    if set_token:
        token = typer.prompt("Enter TMDB read access token", hide_input=True, default="")
        token = token.strip()
        if not token:
            console.print("[red]No access token provided.[/red]")
            raise typer.Exit(1)
        set_tmdb_access_token(token)
        console.print("[green]TMDB access token saved.[/green]")

    if not any((set_key, set_token, clear_key, clear_token)):
        has_key = get_tmdb_api_key() is not None
        has_token = get_tmdb_access_token() is not None
        console.print(
            f"TMDB API key:     [{'green]set[/green]' if has_key else '[red]not set[/red]'}"
        )
        console.print(
            f"TMDB access token: [{'green]set[/green]' if has_token else '[red]not set[/red]'}"
        )
        if not has_key and not has_token:
            console.print(
                "\n[dim]Run [/dim][bold]tv-tracker config --set-key[/bold]"
                "[dim] to add your TMDB API key.[/dim]"
            )


@app.command()
def search(
    query: str = typer.Argument(..., help="Title to search for"),
    media_type: str = typer.Option(None, "--type", "-t", help="Filter: movie | show"),
) -> None:
    """Search TMDB / Jikan for titles to track."""
    _validate_media_type(media_type)
    init_db()
    _ensure_tmdb_credentials()

    try:
        response = search_titles(query, media_type)
    except Exception as exc:
        _print_api_error("search", exc)
        raise typer.Exit(1) from exc

    for err in response.errors:
        console.print(f"[yellow]Warning: {err}[/yellow]")

    if not response.results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Search: {query}")
    table.add_column("#", style="dim", width=3)
    table.add_column("Source", width=6)
    table.add_column("Type", width=5)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Title", min_width=20)
    table.add_column("Year", width=5)
    table.add_column("Overview", ratio=1)

    for idx, r in enumerate(response.results, 1):
        table.add_row(
            str(idx),
            r.source.value,
            r.media_type.value,
            r.external_id,
            r.title or "[dim]Untitled[/dim]",
            _year(r.release_date),
            _truncate(r.overview),
        )

    console.print(table)
    console.print(
        "\n[dim]Use [/dim][bold]tv-tracker details <source> <id> --type <type>[/bold]"
        "[dim] for more info or [/dim]"
        "[bold]tv-tracker add <source> <id> --type <type>[/bold]"
        "[dim] to start tracking.[/dim]"
    )


@app.command()
def details(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
    media_type: str = typer.Option(
        None,
        "--type",
        "-t",
        help="movie | show (required when a TMDB id matches both)",
    ),
    season: int = typer.Option(None, "--season", "-s", help="Show episodes for a season"),
) -> None:
    """View details for a title (seasons, episodes, metadata)."""
    _validate_source(source)
    _validate_media_type(media_type)
    init_db()
    _ensure_tmdb_credentials()

    try:
        info = fetch_details(source, external_id, media_type)
    except Exception as exc:
        _print_api_error("fetch details", exc)
        raise typer.Exit(1) from exc

    if isinstance(info, MovieDetails):
        _render_movie_details(info, source, external_id)
    else:
        _render_show_details(info, source, external_id)
        if season is not None:
            console.print()
            try:
                episodes = fetch_season_episodes(source, external_id, season)
            except Exception as exc:
                _print_api_error(f"fetch season {season} episodes", exc)
                raise typer.Exit(1) from exc

            tracked = find_tracked_item(source, external_id)
            watched_keys: set[tuple[int, int]] = (
                get_watched_episode_keys(tracked.id) if tracked else set()
            )
            _render_episodes_table(info.title, season, episodes, watched_keys)


def _render_movie_details(info: MovieDetails, source: str, external_id: str) -> None:
    meta_parts = [f"Source: [bold]{source}[/bold]", "Type: [bold]movie[/bold]"]
    meta_parts.append(f"ID: [dim]{external_id}[/dim]")
    if info.release_date:
        meta_parts.append(f"Released: {info.release_date}")
    if info.runtime:
        meta_parts.append(f"Runtime: {info.runtime} min")

    body = "  |  ".join(meta_parts)
    if info.overview:
        body += "\n\n" + textwrap.fill(info.overview, width=80)

    console.print(Panel(body, title=info.title or "Untitled", border_style="cyan"))


def _render_show_details(info: ShowDetails, source: str, external_id: str) -> None:
    meta_parts = [f"Source: [bold]{source}[/bold]", "Type: [bold]show[/bold]"]
    meta_parts.append(f"ID: [dim]{external_id}[/dim]")
    meta_parts.append(f"Seasons: [bold]{info.number_of_seasons}[/bold]")
    meta_parts.append(f"Episodes: [bold]{info.number_of_episodes}[/bold]")

    body = "  |  ".join(meta_parts)
    if info.overview:
        body += "\n\n" + textwrap.fill(info.overview, width=80)

    console.print(Panel(body, title=info.title or "Untitled", border_style="cyan"))

    if info.seasons:
        table = Table(title="Seasons")
        table.add_column("Season", width=6)
        table.add_column("Episodes", width=8)
        table.add_column("Name", ratio=1)
        for s in sorted(info.seasons, key=lambda x: x.season_number):
            table.add_row(
                str(s.season_number),
                str(s.episode_count),
                s.name or "[dim]—[/dim]",
            )
        console.print(table)


def _render_episodes_table(
    title: str,
    season_number: int,
    episodes: list[EpisodeInfo],
    watched_keys: set[tuple[int, int]] | None = None,
) -> None:
    table = Table(title=f"{title} — Season {season_number} Episodes")
    table.add_column("#", width=4)
    table.add_column("Title", ratio=2)
    table.add_column("Air Date", width=12)
    table.add_column("Watched", width=8, justify="center")

    if not episodes:
        console.print(table)
        console.print("[yellow]No episode data available.[/yellow]")
        return

    for ep in episodes:
        is_watched = (
            watched_keys is not None and (season_number, ep.episode_number) in watched_keys
        )
        watched_mark = "[green]✓[/green]" if is_watched else "[dim]·[/dim]"
        table.add_row(
            str(ep.episode_number),
            ep.name or "[dim]—[/dim]",
            ep.air_date or "[dim]—[/dim]",
            watched_mark,
        )
    console.print(table)


@app.command()
def add(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
    media_type: str = typer.Option(
        None,
        "--type",
        "-t",
        help="movie | show (required when a TMDB id matches both)",
    ),
) -> None:
    """Add a title to your tracking list."""
    _validate_source(source)
    _validate_media_type(media_type)
    init_db()
    _ensure_tmdb_credentials()

    try:
        item = add_tracked_item(source, external_id, media_type)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        _print_api_error("add item", exc)
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Added:[/green] {item.title} "
        f"([dim]{item.source} {item.media_type}, ID {item.id}[/dim])"
    )
    console.print(
        f"[dim]Status: {item.status.value} — use "
        f"'tv-tracker status {item.id} <status>' to update.[/dim]"
    )


@app.command(name="list")
def list_items(
    status: str = typer.Option(None, "--status", "-s", help="Filter by watch status"),
) -> None:
    """List tracked items with status badges."""
    _validate_status(status)
    init_db()

    try:
        items = list_tracked_items(status)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        _print_api_error("list items", exc)
        raise typer.Exit(1) from exc

    if not items:
        label = f" with status '{status}'" if status else ""
        console.print(f"[yellow]No tracked items{label}.[/yellow]")
        return

    table = Table(title="Tracked Items")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Source", width=6)
    table.add_column("Type", width=5)
    table.add_column("Title", min_width=20)
    table.add_column("Status", width=12)
    table.add_column("Seasons", width=7, justify="right")
    table.add_column("Episodes", width=8, justify="right")
    table.add_column("Progress", width=18)
    table.add_column("Last Synced", width=20)

    for item in items:
        if item.media_type == MediaType.MOVIE:
            seasons = "[dim]—[/dim]"
            episodes = "[dim]—[/dim]"
            is_watched = len(item.watched_episodes) > 0
            progress: Any = (
                Text("watched", style="green")
                if is_watched
                else Text("not watched", style="dim")
            )
        else:
            seasons = str(item.total_seasons) if item.total_seasons else "[dim]?[/dim]"
            episodes = str(item.total_episodes) if item.total_episodes else "[dim]?[/dim]"
            watched_count = len(item.watched_episodes)
            progress = _progress_bar(watched_count, item.total_episodes)

        synced = (
            item.last_synced_at.strftime("%Y-%m-%d %H:%M")
            if item.last_synced_at
            else "[dim]never[/dim]"
        )

        table.add_row(
            str(item.id),
            item.source.value,
            item.media_type.value,
            item.title,
            _status_badge(item.status),
            seasons,
            episodes,
            progress,
            synced,
        )

    console.print(table)


@app.command()
def status(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
    new_status: str = typer.Argument(
        ..., help="planning | watching | completed | on_hold | dropped"
    ),
) -> None:
    """Update the watch status of a tracked item."""
    _validate_status(new_status)
    init_db()
    _ensure_tmdb_credentials()

    try:
        item = set_watch_status(item_id, new_status)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        _print_api_error("update status", exc)
        raise typer.Exit(1) from exc

    console.print(f"[green]Updated:[/green] {item.title} → {_status_badge(item.status)}")
    if item.media_type == MediaType.SHOW and item.status == WatchStatus.COMPLETED:
        console.print("[dim]All episodes marked as watched.[/dim]")


@app.command()
def watch(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
    season: int = typer.Option(None, "--season", "-s"),
    episode: str = typer.Option(
        None,
        "--episode",
        "-e",
        help="Episode number, or 'next' to mark the next unwatched episode",
    ),
) -> None:
    """Mark a movie or episode as watched.

    For movies:  tv-tracker watch <id>
    For shows:   tv-tracker watch <id> --season N --episode M
    Next episode: tv-tracker watch <id> --episode next
    """
    init_db()

    is_next = episode is not None and episode.lower() == "next"
    episode_num: int | None = None
    if episode is not None and not is_next:
        try:
            episode_num = int(episode)
        except ValueError:
            console.print(f"[red]Invalid episode '{episode}'. Use a number or 'next'.[/red]")
            raise typer.Exit(1) from None

    if is_next:
        _ensure_tmdb_credentials()

    try:
        if is_next:
            item, season, episode_num = mark_next_watched(item_id, season)
        else:
            item = mark_watched(item_id, season, episode_num)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        _print_api_error("find next episode", exc)
        raise typer.Exit(1) from exc

    if item.media_type == MediaType.MOVIE:
        console.print(f"[green]Marked watched:[/green] {item.title}")
    else:
        console.print(f"[green]Marked watched:[/green] {item.title} S{season:02}E{episode_num:02}")


@app.command()
def unwatch(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
    season: int = typer.Option(None, "--season", "-s"),
    episode: int = typer.Option(None, "--episode", "-e"),
) -> None:
    """Remove the watched mark from a movie or episode.

    For movies:  tv-tracker unwatch <id>
    For shows:   tv-tracker unwatch <id> --season N --episode M
    """
    init_db()

    try:
        title = unmark_watched(item_id, season, episode)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if season is not None and episode is not None:
        console.print(f"[green]Unmarked:[/green] {title} S{season:02}E{episode:02}")
    else:
        console.print(f"[green]Unmarked:[/green] {title}")


@app.command()
def remove(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
) -> None:
    """Remove a title from your tracking list."""
    init_db()

    try:
        title = remove_tracked_item(item_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        _print_api_error("remove item", exc)
        raise typer.Exit(1) from exc

    console.print(f"[green]Removed:[/green] {title} (ID {item_id})")


@app.command()
def alerts() -> None:
    """Run sync, then list shows with unwatched episodes."""
    init_db()
    _ensure_tmdb_credentials()

    with console.status("[cyan]Syncing tracked items…[/cyan]"):
        try:
            result = run_sync()
        except Exception as exc:
            _print_api_error("sync", exc)
            raise typer.Exit(1) from exc

    console.print(f"[green]Synced {result.items_synced} item(s).[/green]")
    for err in result.errors:
        console.print(f"[red]Error: {err}[/red]")

    try:
        items = get_shows_with_unwatched_episodes()
    except Exception as exc:
        _print_api_error("fetch unwatched", exc)
        raise typer.Exit(1) from exc

    console.print()
    if not items:
        console.print("[dim]No unwatched episodes — all caught up![/dim]")
        return

    console.print(_build_unwatched_table(items))
    console.print(
        "\n[dim]Use [/dim][bold]tv-tracker watch <id> --season N --episode M[/bold]"
        "[dim] to mark episodes as watched.[/dim]"
    )
