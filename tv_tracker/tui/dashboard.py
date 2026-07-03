"""Dashboard tab: stats summary, currently watching, unwatched, recently completed."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Static

from tv_tracker.models import MediaType
from tv_tracker.services import (
    get_currently_watching,
    get_recently_completed,
    get_shows_with_unwatched_episodes,
    get_stats,
    run_sync,
)
from tv_tracker.tui.common import (
    item_progress,
    last_watched_label,
    next_episode_label,
    progress_bar,
)


class DashboardPane(VerticalScroll):
    """Dashboard tab showing an at-a-glance overview of tracked items."""

    DEFAULT_CSS = """
    DashboardPane {
        padding: 1 2;
    }
    DashboardPane Button.sync-button {
        margin-bottom: 1;
    }
    DashboardPane Static.section-title {
        text-style: bold;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Button("Sync & Check Alerts", id="sync-btn", classes="sync-button")
        yield Static(id="stats")
        yield Static("Currently Watching", classes="section-title", markup=True)
        yield DataTable(id="watching-table", cursor_type="row")
        yield Static("Unwatched Episodes", classes="section-title", markup=True)
        yield DataTable(id="unwatched-table", cursor_type="row")
        yield Static("Recently Completed", classes="section-title", markup=True)
        yield DataTable(id="completed-table", cursor_type="row")

    def on_mount(self) -> None:
        self._setup_tables()
        self.refresh_data()

    def _setup_tables(self) -> None:
        watching = self.query_one("#watching-table", DataTable)
        watching.add_columns("ID", "Title", "Type", "Progress", "Last", "Next")

        unwatched = self.query_one("#unwatched-table", DataTable)
        unwatched.add_columns("ID", "Title", "Progress", "Unwatched")

        completed = self.query_one("#completed-table", DataTable)
        completed.add_columns("ID", "Title", "Type", "Progress", "Updated")

    def refresh_data(self) -> None:
        """Reload all dashboard data from the database."""
        self._load_stats()
        self._load_watching()
        self._load_unwatched()
        self._load_completed()

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
        stats_label.update(summary)

    def _load_watching(self) -> None:
        table = self.query_one("#watching-table", DataTable)
        table.clear()
        items = get_currently_watching()
        for item in items:
            if item.media_type == MediaType.MOVIE:
                progress = item_progress(item)
                last = last_watched_label(item)
                next_ep = "[dim]—[/dim]"
            else:
                progress = progress_bar(len(item.watched_episodes), item.total_episodes)
                last = last_watched_label(item)
                next_ep = next_episode_label(item)
            table.add_row(
                str(item.id),
                item.title,
                item.media_type.value,
                progress,
                last,
                next_ep,
            )

    def _load_unwatched(self) -> None:
        table = self.query_one("#unwatched-table", DataTable)
        table.clear()
        try:
            items = get_shows_with_unwatched_episodes()
        except Exception:
            return
        for item in items:
            watched_count = len(item.watched_episodes)
            total = item.total_episodes or 0
            unwatched = total - watched_count
            table.add_row(
                str(item.id),
                item.title,
                progress_bar(watched_count, total),
                f"[yellow]{unwatched}[/yellow]",
            )

    def _load_completed(self) -> None:
        table = self.query_one("#completed-table", DataTable)
        table.clear()
        items = get_recently_completed()
        for item in items:
            progress = item_progress(item)
            updated = item.updated_at.strftime("%Y-%m-%d") if item.updated_at else "[dim]—[/dim]"
            table.add_row(
                str(item.id),
                item.title,
                item.media_type.value,
                progress,
                updated,
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sync-btn":
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
        for err in result.errors:
            self.app.notify(f"[red]Error: {err}[/red]", timeout=5)

        self.app.call_from_thread(self.refresh_data)
