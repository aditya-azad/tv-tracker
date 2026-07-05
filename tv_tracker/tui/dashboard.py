"""Dashboard panes: shows overview and unwatched movies."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Static

from tv_tracker.models import WatchStatus
from tv_tracker.services import (
    get_currently_watching_shows,
    get_stale_shows,
    get_stats,
    get_unwatched_movies,
    run_sync,
)
from tv_tracker.tui.common import (
    last_watched_label,
    next_episode_label,
    progress_bar,
    status_badge,
    time_ago_label,
)


class _DashboardBase(VerticalScroll):
    """Shared base for dashboard panes — provides the sync button and logic."""

    DEFAULT_CSS = """
    _DashboardBase {
        padding: 1 2;
    }
    _DashboardBase Button.sync-button {
        margin-bottom: 1;
    }
    _DashboardBase Static.section-title {
        text-style: bold;
        margin-top: 1;
    }
    """

    def _on_sync_pressed(self) -> None:
        self._run_sync()

    @work(thread=True)
    def _run_sync(self) -> None:
        self.app.notify("[cyan]Syncing tracked items…[/cyan]", timeout=2)
        try:
            result = run_sync()
        except Exception as exc:
            self.app.notify(f"[red]Sync failed:[/red] {exc}", timeout=5)
            return

        self.app.notify(f"[green]Synced {result.items_synced} item(s).[/green]", timeout=3)
        for title in result.completed:
            self.app.notify(
                f"[cyan]All episodes watched — marked completed:[/cyan] {title}", timeout=5
            )
        for title in result.resumed:
            self.app.notify(f"[cyan]New episodes — resumed watching:[/cyan] {title}", timeout=5)
        for title in result.released:
            self.app.notify(f"[magenta]Released — now available:[/magenta] {title}", timeout=5)
        for err in result.errors:
            self.app.notify(f"[red]Error: {err}[/red]", timeout=5)

        self.app.call_from_thread(self.refresh_data)

    def refresh_data(self) -> None:
        """Reload all dashboard data from the database (overridden by subclasses)."""
        raise NotImplementedError


class ShowsDashboardPane(_DashboardBase):
    """Shows dashboard — currently watching and haven't watched for a while."""

    def compose(self) -> ComposeResult:
        yield Button("Sync & Check Alerts", id="sync-btn", classes="sync-button")
        yield Static(id="stats")
        yield Static("Currently Watching", classes="section-title", markup=True)
        yield DataTable(id="watching-table", cursor_type="row")
        yield Static("Haven't Watched For A While", classes="section-title", markup=True)
        yield DataTable(id="stale-table", cursor_type="row")

    def on_mount(self) -> None:
        self._setup_tables()
        self.refresh_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync-btn":
            self._on_sync_pressed()

    def _setup_tables(self) -> None:
        stale = self.query_one("#stale-table", DataTable)
        stale.add_columns("Title", "Progress", "Last Episode", "Last Watched")

        watching = self.query_one("#watching-table", DataTable)
        watching.add_columns("Title", "Progress", "Last", "Next", "Last Watched")

    def refresh_data(self) -> None:
        """Reload all shows dashboard data from the database."""
        self._load_stats()
        self._load_watching()
        self._load_stale()

    def _load_stats(self) -> None:
        try:
            stats = get_stats()
        except Exception:
            return

        stats_label = self.query_one("#stats", Static)
        if stats.total == 0:
            stats_label.update(
                "[dim]No tracked items yet. Use the [/dim][bold]Search[/bold]"
                "[dim] tab to find titles to track.[/dim]"
            )
            return

        parts = [
            f"[bold]{stats.shows}[/bold] shows",
            f"[green]{stats.watching}[/green] watching",
            f"[blue]{stats.planning}[/blue] planning",
            f"[cyan]{stats.completed}[/cyan] completed",
        ]
        if stats.upcoming:
            parts.append(f"[magenta]{stats.upcoming}[/magenta] upcoming")
        if stats.on_hold:
            parts.append(f"[yellow]{stats.on_hold}[/yellow] on hold")
        if stats.dropped:
            parts.append(f"[red]{stats.dropped}[/red] dropped")

        stats_label.update("  ".join(parts))

    def _load_stale(self) -> None:
        table = self.query_one("#stale-table", DataTable)
        table.clear()
        items = get_stale_shows()
        for item in items:
            progress = progress_bar(len(item.watched_episodes), item.total_episodes)
            last_ep = last_watched_label(item)
            last_watched = max(
                (we.watched_at for we in item.watched_episodes if we.season_number != 0),
                default=None,
            )
            table.add_row(
                item.title,
                progress,
                last_ep,
                time_ago_label(last_watched),
            )

    def _load_watching(self) -> None:
        table = self.query_one("#watching-table", DataTable)
        table.clear()
        items = get_currently_watching_shows()
        for item in items:
            progress = progress_bar(len(item.watched_episodes), item.total_episodes)
            last = last_watched_label(item)
            next_ep = next_episode_label(item)
            last_watched = max(
                (we.watched_at for we in item.watched_episodes if we.season_number != 0),
                default=None,
            )
            table.add_row(
                item.title,
                progress,
                last,
                next_ep,
                time_ago_label(last_watched),
            )


class MoviesDashboardPane(_DashboardBase):
    """Movies dashboard — only movies that haven't been watched yet."""

    def compose(self) -> ComposeResult:
        yield Button("Sync & Check Alerts", id="sync-btn", classes="sync-button")
        yield Static(id="stats")
        yield Static("Unwatched Movies", classes="section-title", markup=True)
        yield DataTable(id="movies-table", cursor_type="row")

    def on_mount(self) -> None:
        self._setup_tables()
        self.refresh_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync-btn":
            self._on_sync_pressed()

    def _setup_tables(self) -> None:
        table = self.query_one("#movies-table", DataTable)
        table.add_columns("Title", "Status", "Added")

    def refresh_data(self) -> None:
        """Reload movies dashboard data from the database."""
        self._load_stats()
        self._load_movies()

    def _load_stats(self) -> None:
        try:
            stats = get_stats()
        except Exception:
            return

        stats_label = self.query_one("#stats", Static)
        if stats.movies == 0:
            stats_label.update(
                "[dim]No tracked movies yet. Use the [/dim][bold]Search[/bold]"
                "[dim] tab to find titles to track.[/dim]"
            )
            return

        try:
            unwatched_items = get_unwatched_movies()
        except Exception:
            unwatched_items = []
        unwatched = len(unwatched_items)
        watched = stats.movies - unwatched
        upcoming = sum(1 for m in unwatched_items if m.status == WatchStatus.UPCOMING)

        parts = [
            f"[bold]{stats.movies}[/bold] movies",
            f"[yellow]{unwatched}[/yellow] unwatched",
            f"[cyan]{watched}[/cyan] watched",
        ]
        if upcoming:
            parts.append(f"[magenta]{upcoming}[/magenta] upcoming")
        stats_label.update("  ".join(parts))

    def _load_movies(self) -> None:
        table = self.query_one("#movies-table", DataTable)
        table.clear()
        try:
            items = get_unwatched_movies()
        except Exception:
            return
        for item in items:
            added = item.created_at.strftime("%Y-%m-%d") if item.created_at else "[dim]—[/dim]"
            table.add_row(
                item.title,
                status_badge(item.status),
                added,
            )
