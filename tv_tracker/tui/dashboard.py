"""Dashboard panes: shows overview and unwatched movies."""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Button, DataTable, Static

from tv_tracker.models import MediaType, TrackedItem, WatchStatus
from tv_tracker.services import (
    get_currently_watching_shows,
    get_stale_shows,
    get_stats,
    get_unwatched_movies,
    mark_all_watched,
    mark_next_watched,
    run_sync,
    set_watch_status,
)
from tv_tracker.tui.common import (
    format_api_error,
    last_watched_label,
    next_episode_label,
    progress_bar,
    status_badge,
    time_ago_label,
)
from tv_tracker.tui.confirm import ConfirmScreen
from tv_tracker.tui.status_select import StatusSelectScreen


class _DashboardBase(VerticalScroll):
    """Shared base for dashboard panes — sync, status, and mark-watched actions."""

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
    _DashboardBase Static.dashboard-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar = [
        Binding("enter", "open_detail", "Open detail", show=True),
        Binding("s", "change_status", "Change status", show=True),
        Binding("w", "mark_next", "Mark next watched", show=True),
        Binding("W", "mark_all_watched", "Mark all watched", show=True),
    ]

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

    def _get_selected_item(self) -> TrackedItem | None:
        """Return the item selected in the focused dashboard table (overridden)."""
        raise NotImplementedError

    def action_open_detail(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return
        self.app.push_item_detail(item.id)  # type: ignore[attr-defined]

    def action_change_status(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return

        def on_result(result: WatchStatus | None) -> None:
            if result is not None and result != item.status:
                self._set_status(item, result)

        self.app.push_screen(StatusSelectScreen(item.title, item.status), on_result)

    @work(thread=True)
    def _set_status(self, item: TrackedItem, status: WatchStatus) -> None:
        try:
            updated = set_watch_status(item.id, status.value)
        except ValueError as exc:
            self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            return
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("update status", exc), timeout=5
            )
            return

        self.app.call_from_thread(
            self.app.notify,
            f"[green]Updated:[/green] {updated.title} -> {status_badge(updated.status)}",
            timeout=3,
        )
        if updated.media_type == MediaType.SHOW and updated.status == WatchStatus.COMPLETED:
            self.app.call_from_thread(
                self.app.notify,
                "[dim]All episodes marked as watched.[/dim]",
                timeout=4,
            )
        self.app.call_from_thread(self.app.refresh_all_tabs)  # type: ignore[attr-defined]

    def action_mark_next(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return
        if item.media_type == MediaType.MOVIE:
            self.app.notify(
                f"[yellow]'{item.title}' is a movie — open details to mark watched.[/yellow]",
                timeout=4,
            )
            return
        self._perform_mark_next(item)

    @work(thread=True)
    def _perform_mark_next(self, item: TrackedItem) -> None:
        self.app.notify(f"[cyan]Finding next episode for '{item.title}'…[/cyan]", timeout=2)
        try:
            tracked_item, season, episode = mark_next_watched(item.id)
        except ValueError as exc:
            self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            return
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("find next episode", exc), timeout=5
            )
            return

        self.app.call_from_thread(
            self.app.notify,
            f"[green]Marked watched:[/green] {tracked_item.title} S{season:02}E{episode:02}",
            timeout=4,
        )
        if tracked_item.status == WatchStatus.COMPLETED:
            self.app.call_from_thread(
                self.app.notify,
                f"[green]All episodes watched — marked completed:[/green] {tracked_item.title}",
                timeout=4,
            )
        self.app.call_from_thread(self.app.refresh_all_tabs)  # type: ignore[attr-defined]

    def action_mark_all_watched(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return

        if item.media_type == MediaType.MOVIE:
            message = f"Mark [bold]{item.title}[/bold] as watched and completed?"
        else:
            message = (
                f"Mark [bold]all episodes[/bold] of [bold]{item.title}[/bold] as watched?\n"
                "[dim]This will set the status to completed.[/dim]"
            )

        def _on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._perform_mark_all(item)

        self.app.push_screen(ConfirmScreen(message, title="Mark all watched"), _on_confirm)

    @work(thread=True)
    def _perform_mark_all(self, item: TrackedItem) -> None:
        try:
            updated, newly_marked = mark_all_watched(item.id)
        except ValueError as exc:
            self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            return
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("mark all watched", exc), timeout=5
            )
            return

        if newly_marked:
            self.app.call_from_thread(
                self.app.notify,
                f"[green]Marked watched:[/green] {updated.title} ({newly_marked} episode(s))",
                timeout=4,
            )
        else:
            self.app.call_from_thread(
                self.app.notify,
                f"[green]Already fully watched:[/green] {updated.title}",
                timeout=4,
            )
        self.app.call_from_thread(self.app.refresh_all_tabs)  # type: ignore[attr-defined]


class ShowsDashboardPane(_DashboardBase):
    """Shows dashboard — currently watching and haven't watched for a while."""

    def __init__(self) -> None:
        super().__init__()
        self._watching_items: list[TrackedItem] = []
        self._stale_items: list[TrackedItem] = []

    def compose(self) -> ComposeResult:
        yield Button("Sync & Check Alerts", id="sync-btn", classes="sync-button")
        yield Static(id="stats")
        yield Static("Currently Watching", classes="section-title", markup=True)
        yield DataTable(id="watching-table", cursor_type="row")
        yield Static("Haven't Watched For A While", classes="section-title", markup=True)
        yield DataTable(id="stale-table", cursor_type="row")
        yield Static(
            "[dim]Press [/dim][bold]Enter[/bold][dim] to open details, "
            "[/dim][bold]s[/bold][dim] to change status, "
            "[/dim][bold]w[/bold][dim] to mark next episode watched, "
            "[/dim][bold]W[/bold][dim] to mark whole show watched.[/dim]",
            id="shows-hint",
            classes="dashboard-hint",
        )

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

    def _get_selected_item(self) -> TrackedItem | None:
        """Return the item selected in whichever shows table is focused."""
        focused = self.app.focused
        if not isinstance(focused, DataTable):
            return None
        cursor_row = focused.cursor_row
        if cursor_row is None or cursor_row < 0:
            return None
        if focused.id == "watching-table":
            if cursor_row >= len(self._watching_items):
                return None
            return self._watching_items[cursor_row]
        if focused.id == "stale-table":
            if cursor_row >= len(self._stale_items):
                return None
            return self._stale_items[cursor_row]
        return None

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
        self._stale_items = items
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
        self._watching_items = items
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

    def __init__(self) -> None:
        super().__init__()
        self._movies_items: list[TrackedItem] = []

    def compose(self) -> ComposeResult:
        yield Button("Sync & Check Alerts", id="sync-btn", classes="sync-button")
        yield Static(id="stats")
        yield Static("Unwatched Movies", classes="section-title", markup=True)
        yield DataTable(id="movies-table", cursor_type="row")
        yield Static(
            "[dim]Press [/dim][bold]Enter[/bold][dim] to open details, "
            "[/dim][bold]s[/bold][dim] to change status, "
            "[/dim][bold]W[/bold][dim] to mark watched and completed.[/dim]",
            id="movies-hint",
            classes="dashboard-hint",
        )

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

    def _get_selected_item(self) -> TrackedItem | None:
        """Return the movie selected in the movies table."""
        focused = self.app.focused
        if not isinstance(focused, DataTable) or focused.id != "movies-table":
            return None
        cursor_row = focused.cursor_row
        if cursor_row is None or cursor_row < 0:
            return None
        if cursor_row >= len(self._movies_items):
            return None
        return self._movies_items[cursor_row]

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
            self._movies_items = []
            return
        self._movies_items = items
        for item in items:
            added = item.created_at.strftime("%Y-%m-%d") if item.created_at else "[dim]—[/dim]"
            table.add_row(
                item.title,
                status_badge(item.status),
                added,
            )
