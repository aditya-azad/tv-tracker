"""Typer-based CLI for TV Tracker."""

from __future__ import annotations

import textwrap

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tv_tracker import __version__
from tv_tracker.api import EpisodeInfo, MovieDetails, ShowDetails
from tv_tracker.db import init_db
from tv_tracker.models import MediaType, WatchStatus
from tv_tracker.services import (
    VALID_MEDIA_TYPES,
    VALID_SOURCES,
    VALID_STATUSES,
    add_tracked_item,
    fetch_details,
    fetch_season_episodes,
    list_tracked_items,
    remove_tracked_item,
)
from tv_tracker.services import search as search_titles

app = typer.Typer(
    name="tv-tracker",
    help="Track movies and shows (including anime) from the command line.",
    no_args_is_help=True,
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


def _print_api_error(action: str, exc: Exception) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        console.print(
            f"[red]Could not {action}: HTTP {exc.response.status_code}[/red]\n"
            f"[dim]{exc}[/dim]"
        )
    elif isinstance(exc, httpx.RequestError):
        console.print(f"[red]Could not {action}: network error[/red]\n[dim]{exc}[/dim]")
    else:
        console.print(f"[red]Could not {action}: {exc}[/red]")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Open the dashboard (triggers async sync)."""
    if ctx.invoked_subcommand is not None:
        return
    init_db()
    console.print(
        Panel.fit(
            "[bold cyan]TV Tracker[/bold cyan] v"
            f"{__version__}\n\n"
            "[dim]Dashboard coming in Phase 3.[/dim]\n"
            "[dim]Run [bold]tv-tracker --help[/bold] to see available commands.[/dim]",
            border_style="cyan",
        )
    )


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
def search(
    query: str = typer.Argument(..., help="Title to search for"),
    media_type: str = typer.Option(None, "--type", "-t", help="Filter: movie | show"),
) -> None:
    """Search TMDB / Jikan for titles to track."""
    _validate_media_type(media_type)
    init_db()

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
        "\n[dim]Use [/dim][bold]tv-tracker details <source> <id>[/bold]"
        "[dim] for more info or [/dim]"
        "[bold]tv-tracker add <source> <id>[/bold][dim] to start tracking.[/dim]"
    )


@app.command()
def details(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
    season: int = typer.Option(None, "--season", "-s", help="Show episodes for a season"),
) -> None:
    """View details for a title (seasons, episodes, metadata)."""
    _validate_source(source)
    init_db()

    try:
        info = fetch_details(source, external_id)
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
            _render_episodes_table(info.title, season, episodes)


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
    title: str, season_number: int, episodes: list[EpisodeInfo]
) -> None:
    table = Table(title=f"{title} — Season {season_number} Episodes")
    table.add_column("#", width=4)
    table.add_column("Title", ratio=2)
    table.add_column("Air Date", width=12)

    if not episodes:
        console.print(table)
        console.print("[yellow]No episode data available.[/yellow]")
        return

    for ep in episodes:
        table.add_row(
            str(ep.episode_number),
            ep.name or "[dim]—[/dim]",
            ep.air_date or "[dim]—[/dim]",
        )
    console.print(table)


@app.command()
def add(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
) -> None:
    """Add a title to your tracking list."""
    _validate_source(source)
    init_db()

    try:
        item = add_tracked_item(source, external_id)
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
    table.add_column("Last Synced", width=20)

    for item in items:
        if item.media_type == MediaType.MOVIE:
            seasons = "[dim]—[/dim]"
            episodes = "[dim]—[/dim]"
        else:
            seasons = str(item.total_seasons) if item.total_seasons else "[dim]?[/dim]"
            episodes = (
                str(item.total_episodes) if item.total_episodes else "[dim]?[/dim]"
            )

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
    """Update the watch status of a tracked item. (Phase 3)"""
    console.print("[yellow]Status update will be implemented in Phase 3.[/yellow]")


@app.command()
def watch(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
    season: int = typer.Option(None, "--season", "-s"),
    episode: int = typer.Option(None, "--episode", "-e"),
) -> None:
    """Mark a movie or episode as watched. (Phase 3)"""
    console.print("[yellow]Watch tracking will be implemented in Phase 3.[/yellow]")


@app.command()
def unwatch(
    item_id: int = typer.Argument(..., help="Tracked item ID"),
    season: int = typer.Option(None, "--season", "-s"),
    episode: int = typer.Option(None, "--episode", "-e"),
) -> None:
    """Unmark a movie or episode as watched. (Phase 3)"""
    console.print("[yellow]Unwatch will be implemented in Phase 3.[/yellow]")


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
    """List new content alerts. (Phase 4)"""
    console.print("[yellow]Alerts will be implemented in Phase 4.[/yellow]")


@app.command()
def dismiss(
    alert_id: int = typer.Argument(..., help="Alert ID to dismiss"),
) -> None:
    """Dismiss a new content alert. (Phase 4)"""
    console.print("[yellow]Alert dismissal will be implemented in Phase 4.[/yellow]")
