"""Item detail screen: view show/movie details, manage episodes and status."""

from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Static

from tv_tracker.api import EpisodeInfo, MovieDetails, ShowDetails
from tv_tracker.models import MediaType, TrackedItem, WatchStatus
from tv_tracker.services import (
    fetch_details,
    fetch_season_episodes,
    get_watched_episode_keys,
    list_tracked_items,
    mark_all_watched,
    mark_next_watched,
    mark_watched,
    set_watch_status,
    unmark_watched,
)
from tv_tracker.tui.common import format_api_error, status_badge
from tv_tracker.tui.confirm import ConfirmScreen
from tv_tracker.tui.status_select import StatusSelectScreen


class ItemDetailScreen(Screen):
    """Push screen showing details for a tracked item and its episodes."""

    BINDINGS: ClassVar = [
        Binding("escape", "app.pop_screen", "Back", show=True),
        Binding("s", "change_status", "Change status", show=True),
        Binding("n", "mark_next", "Mark next watched", show=True),
        Binding("w", "toggle_watched", "Toggle watched", show=True),
        Binding("W", "mark_all_watched", "Mark all watched", show=True),
        Binding("u", "toggle_watched", "Unwatch", show=True),
    ]

    DEFAULT_CSS = """
    ItemDetailScreen {
        background: $surface;
    }
    ItemDetailScreen VerticalScroll {
        padding: 1 2;
    }
    ItemDetailScreen Static.detail-header {
        text-style: bold;
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }
    ItemDetailScreen Static.detail-meta {
        margin-bottom: 1;
    }
    ItemDetailScreen Static.detail-overview {
        margin-bottom: 1;
        color: $text-muted;
    }
    ItemDetailScreen Static.section-title {
        text-style: bold;
        margin-top: 1;
    }
    ItemDetailScreen Button {
        margin: 1 0;
    }
    """

    def __init__(self, item_id: int) -> None:
        super().__init__()
        self._item_id = item_id
        self._details: ShowDetails | MovieDetails | None = None
        self._watched_keys: set[tuple[int, int]] = set()
        self._current_season: int | None = None
        self._episodes: list[EpisodeInfo] = []
        self._seasons_data: list[tuple[int, int, str | None]] = []

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(id="header")
            yield Static(id="meta")
            yield Static(id="overview")
            yield Static(id="status-line")
            yield Button("Mark Next Episode Watched", id="next-btn", variant="success")
            yield Static("Seasons", classes="section-title")
            yield DataTable(id="seasons-table", cursor_type="row")
            yield Static("Episodes", classes="section-title")
            yield DataTable(id="episodes-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        seasons_table = self.query_one("#seasons-table", DataTable)
        seasons_table.add_columns("Season", "Episodes", "Name")
        episodes_table = self.query_one("#episodes-table", DataTable)
        episodes_table.add_columns("#", "Title", "Air Date", "Watched")
        self._load_data()

    @work(thread=True)
    def _load_data(self) -> None:
        # Find the tracked item
        items = list_tracked_items()
        item = next((i for i in items if i.id == self._item_id), None)
        if item is None:
            self.app.call_from_thread(
                self.app.notify, f"[red]No tracked item with ID {self._item_id}[/red]", timeout=5
            )
            self.app.call_from_thread(self.app.pop_screen)
            return

        self._watched_keys = get_watched_episode_keys(self._item_id)

        try:
            details = fetch_details(item.source.value, item.external_id, item.media_type.value)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("fetch details", exc), timeout=5
            )
            # Still show basic info from the tracked item
            self.app.call_from_thread(self._render_basic, item)
            return

        self._details = details
        self.app.call_from_thread(self._render_details, item, details)

    def _render_basic(self, item) -> None:
        """Render minimal info when API fetch fails."""
        self.query_one("#header", Static).update(item.title)
        self.query_one("#meta", Static).update(
            f"Source: [bold]{item.source.value}[/bold]  "
            f"Type: [bold]{item.media_type.value}[/bold]  "
            f"ID: [dim]{item.id}[/dim]"
        )
        self.query_one("#status-line", Static).update(
            f"Status: {status_badge(item.status)}  "
            "[dim]Press [/dim][bold]s[/bold][dim] to change[/dim]"
        )
        next_btn = self.query_one("#next-btn", Button)
        if item.media_type == MediaType.MOVIE:
            next_btn.display = False

    def _render_details(self, item, details: ShowDetails | MovieDetails) -> None:
        """Render full details."""
        self.query_one("#header", Static).update(details.title or "Untitled")

        if isinstance(details, MovieDetails):
            meta_parts = [
                f"Source: [bold]{item.source.value}[/bold]",
                "Type: [bold]movie[/bold]",
                f"ID: [dim]{item.id}[/dim]",
            ]
            if details.release_date:
                meta_parts.append(f"Released: {details.release_date}")
            if details.runtime:
                meta_parts.append(f"Runtime: {details.runtime}")
            self.query_one("#meta", Static).update("  |  ".join(meta_parts))

            # Hide seasons/episodes tables and next button for movies
            self.query_one("#seasons-table", DataTable).display = False
            self.query_one("#episodes-table", DataTable).display = False
            self.query_one("#next-btn", Button).display = False
            # Show watched status
            is_watched = len(self._watched_keys) > 0
            watched_text = "[green]watched[/green]" if is_watched else "[dim]not watched[/dim]"
            self.query_one("#overview", Static).update(details.overview or "")
            self.query_one("#status-line", Static).update(
                f"Status: {status_badge(item.status)}  |  {watched_text}  "
                "[dim]Press [/dim][bold]w[/bold][dim] to toggle, "
                "[/dim][bold]W[/bold][dim] to mark watched & completed[/dim]"
            )
        else:
            meta_parts = [
                f"Source: [bold]{item.source.value}[/bold]",
                "Type: [bold]show[/bold]",
                f"ID: [dim]{item.id}[/dim]",
                f"Seasons: [bold]{details.number_of_seasons}[/bold]",
                f"Episodes: [bold]{details.number_of_episodes}[/bold]",
            ]
            if details.release_date:
                meta_parts.append(f"First Aired: {details.release_date}")
            self.query_one("#meta", Static).update("  |  ".join(meta_parts))
            self.query_one("#overview", Static).update(details.overview or "")
            self.query_one("#status-line", Static).update(
                f"Status: {status_badge(item.status)}  "
                "[dim]Press [/dim][bold]s[/bold][dim] to change, "
                "[/dim][bold]n[/bold][dim] for next episode, "
                "[/dim][bold]w/u[/bold][dim] to toggle episode, "
                "[/dim][bold]W[/bold][dim] to mark whole show watched[/dim]"
            )

            # Populate seasons table
            seasons_table = self.query_one("#seasons-table", DataTable)
            seasons_table.clear()
            self._seasons_data = []
            for s in sorted(details.seasons, key=lambda x: x.season_number):
                self._seasons_data.append((s.season_number, s.episode_count, s.name))
                seasons_table.add_row(
                    str(s.season_number),
                    str(s.episode_count),
                    s.name or "[dim]—[/dim]",
                )

            # Auto-load first non-special season
            first_season = next(
                (
                    s
                    for s in sorted(details.seasons, key=lambda x: x.season_number)
                    if s.season_number > 0 and s.episode_count > 0
                ),
                None,
            )
            if first_season:
                self._load_season(first_season.season_number)

    @work(thread=True)
    def _load_season(self, season_number: int) -> None:
        items = list_tracked_items()
        item = next((i for i in items if i.id == self._item_id), None)
        if item is None:
            return

        self._current_season = season_number
        try:
            episodes = fetch_season_episodes(item.source.value, item.external_id, season_number)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify,
                format_api_error(f"fetch season {season_number} episodes", exc),
                timeout=5,
            )
            return

        self._episodes = episodes
        self._watched_keys = get_watched_episode_keys(self._item_id)

        def populate() -> None:
            table = self.query_one("#episodes-table", DataTable)
            table.clear()
            for ep in episodes:
                is_watched = (season_number, ep.episode_number) in self._watched_keys
                watched_mark = "[green]yes[/green]" if is_watched else "[dim]no[/dim]"
                table.add_row(
                    str(ep.episode_number),
                    ep.name or "[dim]—[/dim]",
                    ep.air_date or "[dim]—[/dim]",
                    watched_mark,
                )

        self.app.call_from_thread(populate)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter on seasons table — load that season's episodes."""
        if event.data_table.id != "seasons-table":
            return
        row_index = event.row_key.value
        if row_index is None:
            return
        idx = int(row_index)
        if idx >= len(self._seasons_data):
            return
        season_num = self._seasons_data[idx][0]
        self._load_season(season_num)

    @work(thread=True)
    def action_mark_next(self) -> None:
        try:
            item, season, episode = mark_next_watched(self._item_id)
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
            f"[green]Marked watched:[/green] {item.title} S{season:02}E{episode:02}",
            timeout=4,
        )
        if item.status == WatchStatus.COMPLETED:
            self.app.call_from_thread(
                self.app.notify,
                f"[green]All episodes watched — marked completed:[/green] {item.title}",
                timeout=4,
            )
        self.app.call_from_thread(self._reload_after_change)

    def action_mark_all_watched(self) -> None:
        """Mark every episode of the current show (or the movie) as watched."""
        items = list_tracked_items()
        item = next((i for i in items if i.id == self._item_id), None)
        if item is None:
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
            updated, newly_marked = mark_all_watched(self._item_id)
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
        self.app.call_from_thread(self._reload_after_change)

    @work(thread=True)
    def action_toggle_watched(self) -> None:
        """Toggle watched status for the selected episode (or movie)."""
        items = list_tracked_items()
        item = next((i for i in items if i.id == self._item_id), None)
        if item is None:
            return

        if item.media_type == MediaType.MOVIE:
            # Toggle movie watched status
            if len(self._watched_keys) > 0:
                try:
                    unmark_watched(self._item_id)
                    self.app.call_from_thread(
                        self.app.notify,
                        f"[green]Unmarked:[/green] {item.title}",
                        timeout=3,
                    )
                except ValueError as exc:
                    self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            else:
                try:
                    mark_watched(self._item_id)
                    self.app.call_from_thread(
                        self.app.notify,
                        f"[green]Marked watched:[/green] {item.title}",
                        timeout=3,
                    )
                except ValueError as exc:
                    self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
            self.app.call_from_thread(self._reload_after_change)
            return

        # Show: toggle selected episode
        ep_table = self.query_one("#episodes-table", DataTable)
        if self._current_season is None or ep_table.cursor_row is None:
            self.app.call_from_thread(
                self.app.notify, "[yellow]Select an episode first.[/yellow]", timeout=3
            )
            return
        if ep_table.cursor_row >= len(self._episodes):
            return

        ep = self._episodes[ep_table.cursor_row]
        season = self._current_season
        key = (season, ep.episode_number)

        if key in self._watched_keys:
            try:
                unmark_watched(self._item_id, season, ep.episode_number)
                self.app.call_from_thread(
                    self.app.notify,
                    f"[green]Unmarked:[/green] {item.title} S{season:02}E{ep.episode_number:02}",
                    timeout=3,
                )
            except ValueError as exc:
                self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)
        else:
            try:
                updated = mark_watched(self._item_id, season, ep.episode_number)
                self.app.call_from_thread(
                    self.app.notify,
                    f"[green]Marked watched:[/green] {item.title} "
                    f"S{season:02}E{ep.episode_number:02}",
                    timeout=3,
                )
                if updated.status == WatchStatus.COMPLETED:
                    self.app.call_from_thread(
                        self.app.notify,
                        f"[green]All episodes watched — marked completed:[/green] {item.title}",
                        timeout=4,
                    )
            except ValueError as exc:
                self.app.call_from_thread(self.app.notify, f"[red]{exc}[/red]", timeout=5)

        self.app.call_from_thread(self._reload_after_change)

    def action_change_status(self) -> None:
        """Open a modal to pick a new watch status for the current item."""
        items = list_tracked_items()
        item = next((i for i in items if i.id == self._item_id), None)
        if item is None:
            return

        def on_result(result: WatchStatus | None) -> None:
            if result is not None and result != item.status:
                self._set_status(result)

        self.app.push_screen(StatusSelectScreen(item.title, item.status), on_result)

    @work(thread=True)
    def _set_status(self, status: WatchStatus) -> None:
        try:
            item = set_watch_status(self._item_id, status.value)
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
            f"[green]Updated:[/green] {item.title} -> {status_badge(item.status)}",
            timeout=3,
        )
        if item.media_type == MediaType.SHOW and item.status == WatchStatus.COMPLETED:
            self.app.call_from_thread(
                self.app.notify,
                "[dim]All episodes marked as watched.[/dim]",
                timeout=4,
            )
        self.app.call_from_thread(self._reload_after_change)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next-btn":
            self.action_mark_next()

    def _reload_after_change(self) -> None:
        """Reload the detail screen and notify parent tabs to refresh."""
        self._load_data()
        self.app.refresh_all_tabs()  # type: ignore[attr-defined]
