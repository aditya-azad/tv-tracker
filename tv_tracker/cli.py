"""Typer-based CLI for TV Tracker."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tv_tracker import __version__
from tv_tracker.db import init_db

app = typer.Typer(
    name="tv-tracker",
    help="Track movies and shows (including anime) from the command line.",
    no_args_is_help=True,
)
console = Console()


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
    """Search TMDB / Jikan for titles. (Phase 2)"""
    console.print("[yellow]Search will be implemented in Phase 2.[/yellow]")


@app.command()
def details(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
) -> None:
    """View details for a title. (Phase 2)"""
    console.print("[yellow]Details will be implemented in Phase 2.[/yellow]")


@app.command()
def add(
    source: str = typer.Argument(..., help="tmdb | jikan"),
    external_id: str = typer.Argument(..., help="TMDB id or MAL id"),
) -> None:
    """Add a title to your tracking list. (Phase 2)"""
    console.print("[yellow]Add will be implemented in Phase 2.[/yellow]")


@app.command()
def list(
    status: str = typer.Option(None, "--status", "-s", help="Filter by watch status"),
) -> None:
    """List tracked items. (Phase 2)"""
    init_db()
    table = Table(title="Tracked Items")
    table.add_column("ID", style="dim")
    table.add_column("Source")
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("Status")
    table.add_row("[dim]—[/dim]", "—", "—", "No items tracked yet", "—")
    console.print(table)
    console.print("[yellow]List will be fully implemented in Phase 2.[/yellow]")


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
    """Remove a title from your tracking list. (Phase 2)"""
    console.print("[yellow]Remove will be implemented in Phase 2.[/yellow]")


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
