"""Tracked items tab: list, filter, open detail, mark next, remove."""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Select, Static

from tv_tracker.models import MediaType, TrackedItem
from tv_tracker.services import (
    list_tracked_items,
    mark_all_watched,
    mark_next_watched,
    remove_tracked_item,
)
from tv_tracker.tui.common import format_api_error, item_progress, status_badge
from tv_tracker.tui.confirm import ConfirmScreen


class TrackedPane(Vertical):
    """Tracked items tab — browse, filter, and manage your watch list."""

    DEFAULT_CSS = """
    TrackedPane {
        padding: 1 2;
    }
    TrackedPane Horizontal {
        height: auto;
        margin-bottom: 1;
    }
    TrackedPane Select {
        width: 1fr;
        margin-right: 1;
    }
    TrackedPane Button {
        width: auto;
    }
    TrackedPane #tracked-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar = [
        Binding("enter", "open_detail", "Open detail", show=True),
        Binding("w", "mark_next", "Mark next watched", show=True),
        Binding("W", "mark_all_watched", "Mark all watched", show=True),
        Binding("r", "remove_item", "Remove", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._items: list[TrackedItem] = []

    def compose(self) -> ComposeResult:
        yield Label("[bold]Tracked Items[/bold]")
        with Horizontal():
            yield Select(
                [
                    ("All", None),
                    ("Planning", "planning"),
                    ("Watching", "watching"),
                    ("Completed", "completed"),
                    ("On Hold", "on_hold"),
                    ("Dropped", "dropped"),
                ],
                value=None,
                id="status-filter",
            )
            yield Button("Refresh", id="refresh-btn")
        yield DataTable(id="tracked-table", cursor_type="row")
        yield Static(
            "[dim]Press [/dim][bold]Enter[/bold][dim] to open details, "
            "[/dim][bold]w[/bold][dim] to mark next episode watched, "
            "[/dim][bold]W[/bold][dim] to mark whole show watched, "
            "[/dim][bold]r[/bold][dim] to remove.[/dim]",
            id="tracked-hint",
        )

    def on_mount(self) -> None:
        table = self.query_one("#tracked-table", DataTable)
        table.add_columns(
            "ID", "Source", "Type", "Title", "Status", "Seasons", "Episodes", "Progress"
        )
        self.refresh_data()

    def refresh_data(self) -> None:
        status_filter = self.query_one("#status-filter", Select).value
        status: str | None = None
        if status_filter in ("planning", "watching", "completed", "on_hold", "dropped"):
            status = status_filter

        try:
            items = list_tracked_items(status)
        except Exception as exc:
            self.app.notify(f"[red]Error loading items:[/red] {exc}", timeout=5)
            return

        self._items = items
        table = self.query_one("#tracked-table", DataTable)
        table.clear()
        for item in items:
            if item.media_type == MediaType.MOVIE:
                seasons = "[dim]—[/dim]"
                episodes = "[dim]—[/dim]"
            else:
                seasons = str(item.total_seasons) if item.total_seasons else "[dim]?[/dim]"
                episodes = str(item.total_episodes) if item.total_episodes else "[dim]?[/dim]"
            table.add_row(
                str(item.id),
                item.source.value,
                item.media_type.value,
                item.title,
                status_badge(item.status),
                seasons,
                episodes,
                item_progress(item),
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "status-filter":
            self.refresh_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            self.refresh_data()

    def _get_selected_item(self) -> TrackedItem | None:
        table = self.query_one("#tracked-table", DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            return None
        if table.cursor_row >= len(self._items):
            return None
        return self._items[table.cursor_row]

    def action_open_detail(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return
        self.app.push_item_detail(item.id)  # type: ignore[attr-defined]

    @work(thread=True)
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

    def action_remove_item(self) -> None:
        item = self._get_selected_item()
        if item is None:
            self.app.notify("[yellow]Select an item first.[/yellow]", timeout=3)
            return

        message = (
            f"Remove [bold]{item.title}[/bold] (ID {item.id}) from your tracked items?\n"
            "[dim]This cannot be undone.[/dim]"
        )

        def _on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._perform_remove(item)

        self.app.push_screen(ConfirmScreen(message, title="Remove show"), _on_confirm)

    @work(thread=True)
    def _perform_remove(self, item: TrackedItem) -> None:
        try:
            title = remove_tracked_item(item.id)
        except ValueError as exc:
            self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            return
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("remove item", exc), timeout=5
            )
            return

        self.app.call_from_thread(
            self.app.notify, f"[green]Removed:[/green] {title} (ID {item.id})", timeout=4
        )
        self.app.call_from_thread(self.app.refresh_all_tabs)  # type: ignore[attr-defined]
