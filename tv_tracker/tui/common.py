"""Shared utilities for the TUI: formatting, error messages, progress bars."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from tv_tracker.models import MediaType, TrackedItem, WatchStatus

_BAR_WIDTH = 10
_BAR_FULL = "\u2588"
_BAR_EMPTY = "\u2591"

_STATUS_STYLES: dict[WatchStatus, str] = {
    WatchStatus.UPCOMING: "magenta",
    WatchStatus.PLANNING: "blue",
    WatchStatus.WATCHING: "bold green",
    WatchStatus.COMPLETED: "cyan",
    WatchStatus.ON_HOLD: "yellow",
    WatchStatus.DROPPED: "red",
}


def status_badge(status: WatchStatus) -> str:
    """Return a Rich markup badge for a watch status."""
    style = _STATUS_STYLES.get(status, "white")
    return f"[{style}]{status.value}[/{style}]"


def truncate(text: str | None, width: int = 60) -> str:
    if not text:
        return "[dim]—[/dim]"
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


def year_str(release_date: str | None) -> str:
    if not release_date:
        return "[dim]—[/dim]"
    return release_date[:4]


def progress_bar(watched: int, total: int | None) -> str:
    """Return a Rich-markup progress bar string."""
    if total is None or total == 0:
        return f"[dim]{watched}/?[/dim]"
    filled = _BAR_WIDTH * watched // total if total > 0 else 0
    bar = ""
    if filled > 0:
        if watched >= total:
            bar += f"[green]{_BAR_FULL * filled}[/green]"
        else:
            bar += f"[cyan]{_BAR_FULL * filled}[/cyan]"
    bar += f"[dim]{_BAR_EMPTY * (_BAR_WIDTH - filled)}[/dim]"
    bar += f" {watched}/{total}"
    return bar


def last_watched_label(item: TrackedItem) -> str:
    """Return a 'S01E05' label for the most recently watched episode, or '—'."""
    if not item.watched_episodes:
        return "[dim]—[/dim]"
    last = max(item.watched_episodes, key=lambda e: (e.season_number, e.episode_number))
    if last.season_number == 0:
        return "[green]watched[/green]"
    return f"S{last.season_number:02}E{last.episode_number:02}"


def next_episode_label(item: TrackedItem) -> str:
    """Estimate the next unwatched episode label from watched records."""
    if not item.watched_episodes:
        return "S01E01"
    last = max(item.watched_episodes, key=lambda e: (e.season_number, e.episode_number))
    if last.season_number == 0:
        return "[dim]—[/dim]"
    return f"S{last.season_number:02}E{last.episode_number + 1:02}"


def time_ago_label(dt: datetime | None) -> str:
    """Return a compact relative-time label like ``3w ago`` or ``2d ago``."""
    if dt is None:
        return "[dim]never[/dim]"
    now = datetime.now(UTC)
    delta = now - dt
    days = delta.days
    if days >= 365:
        years = days // 365
        return f"[yellow]{years}y ago[/yellow]"
    if days >= 30:
        months = days // 30
        return f"[yellow]{months}mo ago[/yellow]"
    if days >= 7:
        weeks = days // 7
        return f"[yellow]{weeks}w ago[/yellow]"
    if days >= 1:
        return f"[yellow]{days}d ago[/yellow]"
    hours = int(delta.total_seconds() // 3600)
    if hours >= 1:
        return f"[green]{hours}h ago[/green]"
    return "[green]just now[/green]"


def movie_progress(item: TrackedItem) -> str:
    is_watched = len(item.watched_episodes) > 0
    return "[green]watched[/green]" if is_watched else "[dim]not watched[/dim]"


def item_progress(item: TrackedItem) -> str:
    """Return progress text for a tracked item (movie or show)."""
    if item.media_type == MediaType.MOVIE:
        return movie_progress(item)
    return progress_bar(len(item.watched_episodes), item.total_episodes)


def format_api_error(action: str, exc: Exception) -> str:
    """Format an API exception into a user-friendly Rich markup string."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return (
                f"[red]Could not {action}: authentication failed (HTTP 401).[/red]\n"
                "[dim]Update your TMDB API key in the Config tab.[/dim]"
            )
        if status == 404:
            return f"[red]Could not {action}: title not found (HTTP 404).[/red]"
        if status == 429:
            return (
                f"[red]Could not {action}: rate limit exceeded (HTTP 429).[/red]\n"
                "[dim]Wait a moment and try again.[/dim]"
            )
        return f"[red]Could not {action}: HTTP {status}[/red]\n[dim]{exc}[/dim]"
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"[red]Could not {action}: request timed out.[/red]\n"
            "[dim]Check your network connection and try again.[/dim]"
        )
    if isinstance(exc, httpx.ConnectError):
        return (
            f"[red]Could not {action}: could not connect to the API.[/red]\n"
            "[dim]Check your network connection and try again.[/dim]"
        )
    if isinstance(exc, httpx.RequestError):
        return f"[red]Could not {action}: network error[/red]\n[dim]{exc}[/dim]"
    return f"[red]Could not {action}: {exc}[/red]"
