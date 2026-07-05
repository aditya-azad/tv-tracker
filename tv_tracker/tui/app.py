"""Main TUI application for TV Tracker."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, TabbedContent, TabPane

from tv_tracker import __version__
from tv_tracker.db import init_db
from tv_tracker.settings_store import get_tmdb_access_token, get_tmdb_api_key
from tv_tracker.tui.config_screen import ConfigPane
from tv_tracker.tui.dashboard import MoviesDashboardPane, ShowsDashboardPane
from tv_tracker.tui.item_detail import ItemDetailScreen
from tv_tracker.tui.search import SearchPane
from tv_tracker.tui.tracked import TrackedPane


class TVTrackerApp(App):
    """TV Tracker — a TUI for tracking movies and shows."""

    TITLE = f"TV Tracker v{__version__}"
    CSS = """
    TabbedContent {
        height: 100%;
    }
    TabPane {
        padding: 0;
    }
    """

    BINDINGS: ClassVar = [
        Binding("1", "switch_tab('shows')", "Shows", show=True),
        Binding("2", "switch_tab('movies')", "Movies", show=True),
        Binding("3", "switch_tab('search')", "Search", show=True),
        Binding("4", "switch_tab('tracked')", "Tracked", show=True),
        Binding("5", "switch_tab('config')", "Config", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        with TabbedContent(id="main-tabs"):
            yield TabPane("Shows", ShowsDashboardPane(), id="shows")
            yield TabPane("Movies", MoviesDashboardPane(), id="movies")
            yield TabPane("Search", SearchPane(), id="search")
            yield TabPane("Tracked", TrackedPane(), id="tracked")
            yield TabPane("Config", ConfigPane(), id="config")
        yield Footer()

    def on_mount(self) -> None:
        init_db()
        if not (get_tmdb_api_key() or get_tmdb_access_token()):
            self.notify(
                "[yellow]TMDB API key not set.[/yellow] "
                "Open the [bold]Config[/bold] tab (press [bold]4[/bold]) to add it.",
                timeout=8,
            )

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to a tab by its pane ID."""
        self.query_one("#main-tabs", TabbedContent).active = tab_id

    def push_item_detail(self, item_id: int) -> None:
        """Push the item detail screen for a tracked item."""
        self.push_screen(ItemDetailScreen(item_id))

    def refresh_all_tabs(self) -> None:
        """Refresh data in all tabs that support it."""
        for tab in [ShowsDashboardPane, MoviesDashboardPane, SearchPane, TrackedPane, ConfigPane]:
            pane = self.query_one(tab)
            if hasattr(pane, "refresh_data"):
                pane.refresh_data()
            elif hasattr(pane, "refresh_status"):
                pane.refresh_status()


def run() -> None:
    """Entry point for the TUI application."""
    app = TVTrackerApp()
    app.run()
