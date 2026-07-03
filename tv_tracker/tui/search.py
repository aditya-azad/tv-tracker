"""Search tab: search TMDB/Jikan and add titles to tracking list."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from tv_tracker.api import SearchResult
from tv_tracker.services import add_tracked_item
from tv_tracker.services import search as search_titles
from tv_tracker.settings_store import get_tmdb_access_token, get_tmdb_api_key
from tv_tracker.tui.common import format_api_error, truncate, year_str


class SearchPane(Vertical):
    """Search tab: type a query, scroll results, press Enter to add."""

    DEFAULT_CSS = """
    SearchPane {
        padding: 1 2;
    }
    SearchPane Horizontal {
        height: auto;
        margin-bottom: 1;
    }
    SearchPane Input {
        width: 2fr;
        margin-right: 1;
    }
    SearchPane Select {
        width: 1fr;
        margin-right: 1;
    }
    SearchPane Button {
        width: auto;
    }
    SearchPane #search-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._results: list[SearchResult] = []

    def compose(self) -> ComposeResult:
        yield Label("[bold]Search for titles to track[/bold]")
        with Horizontal():
            yield Input(placeholder="Search query…", id="search-input")
            yield Select(
                [
                    ("All", None),
                    ("Movies", "movie"),
                    ("Shows", "show"),
                ],
                value=None,
                id="type-filter",
            )
            yield Button("Search", id="search-btn", variant="primary")
        yield DataTable(id="search-results", cursor_type="row")
        yield Static(
            "[dim]Press [/dim][bold]Enter[/bold]"
            "[dim] on a result to add it to your tracking list.[/dim]",
            id="search-hint",
        )

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.add_columns("Source", "Type", "ID", "Title", "Year", "Overview")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self._do_search()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search-btn":
            self._do_search()

    @work(thread=True)
    def _do_search(self) -> None:
        query_input = self.query_one("#search-input", Input)
        query = query_input.value.strip()
        if not query:
            self.app.notify("[yellow]Enter a search query first.[/yellow]", timeout=3)
            return

        if not (get_tmdb_api_key() or get_tmdb_access_token()):
            self.app.notify(
                "[red]TMDB API key required.[/red] Set it in the Config tab.",
                timeout=5,
            )
            return

        type_filter = self.query_one("#type-filter", Select).value
        media_type: str | None = None
        if type_filter in ("movie", "show"):
            media_type = type_filter

        table = self.query_one("#search-results", DataTable)
        self.app.call_from_thread(table.clear)
        self._results = []
        self.app.notify(f"[cyan]Searching for '{query}'…[/cyan]", timeout=2)

        try:
            response = search_titles(query, media_type)
        except Exception as exc:
            self.app.call_from_thread(self.app.notify, format_api_error("search", exc), timeout=5)
            return

        for err in response.errors:
            self.app.call_from_thread(
                self.app.notify, f"[yellow]Warning: {err}[/yellow]", timeout=5
            )

        if not response.results:
            self.app.call_from_thread(
                self.app.notify, "[yellow]No results found.[/yellow]", timeout=3
            )
            return

        self._results = response.results

        def populate() -> None:
            table.clear()
            for r in response.results:
                table.add_row(
                    r.source.value,
                    r.media_type.value,
                    r.external_id,
                    r.title or "[dim]Untitled[/dim]",
                    year_str(r.release_date),
                    truncate(r.overview, 50),
                )

        self.app.call_from_thread(populate)
        self.app.call_from_thread(
            self.app.notify,
            f"[green]Found {len(response.results)} result(s).[/green]"
            " [dim]Press Enter to add.[/dim]",
            timeout=3,
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter press on a search result — add it to tracking."""
        if event.data_table.id != "search-results":
            return
        table = event.data_table
        if table.cursor_row is None or table.cursor_row < 0:
            return
        if table.cursor_row >= len(self._results):
            return
        result = self._results[table.cursor_row]
        self._add_result(result)

    @work(thread=True)
    def _add_result(self, result: SearchResult) -> None:
        self.app.notify(f"[cyan]Adding '{result.title}' to tracking…[/cyan]", timeout=2)
        try:
            item = add_tracked_item(
                result.source.value, result.external_id, result.media_type.value
            )
        except ValueError as exc:
            self.app.call_from_thread(self.app.notify, f"[yellow]{exc}[/yellow]", timeout=5)
            return
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, format_api_error("add item", exc), timeout=5
            )
            return

        self.app.call_from_thread(
            self.app.notify,
            f"[green]Added:[/green] {item.title} "
            f"([dim]{item.source} {item.media_type}, ID {item.id}[/dim])",
            timeout=4,
        )
        # Refresh the dashboard and tracked list
        self.app.call_from_thread(self.app.refresh_all_tabs)  # type: ignore[attr-defined]
