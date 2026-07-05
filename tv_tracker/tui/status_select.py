"""Modal screen for picking a watch status for a tracked item."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from tv_tracker.models import WatchStatus
from tv_tracker.tui.common import status_badge

_ALL_STATUSES: list[WatchStatus] = list(WatchStatus)

_DISPLAY_NAMES: dict[WatchStatus, str] = {
    WatchStatus.UPCOMING: "Upcoming",
    WatchStatus.PLANNING: "Planning",
    WatchStatus.WATCHING: "Watching",
    WatchStatus.COMPLETED: "Completed",
    WatchStatus.ON_HOLD: "On Hold",
    WatchStatus.DROPPED: "Dropped",
}


class StatusSelectScreen(ModalScreen[WatchStatus | None]):
    """A modal that lets the user choose a watch status for a tracked item.

    Dismisses with the selected :class:`WatchStatus`, or ``None`` when
    cancelled.
    """

    DEFAULT_CSS = """
    StatusSelectScreen Middle {
        align: center middle;
    }
    StatusSelectScreen Center {
        width: auto;
        height: auto;
        padding: 1 2;
    }
    StatusSelectScreen Vertical {
        width: auto;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    StatusSelectScreen Static {
        text-align: center;
        margin-bottom: 1;
    }
    StatusSelectScreen Button {
        width: 24;
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel", show=True),
        *[
            Binding(str(i + 1), f"select({i})", _DISPLAY_NAMES[status], show=True)
            for i, status in enumerate(_ALL_STATUSES)
        ],
    ]

    def __init__(self, title: str, current: WatchStatus) -> None:
        super().__init__()
        self._title = title
        self._current = current

    def compose(self) -> ComposeResult:
        with Middle(), Center(), Vertical():
            yield Static("[bold]Change status[/bold]")
            yield Static(f"[bold]{self._title}[/bold]")
            yield Static(f"Current: {status_badge(self._current)}")
            for status in _ALL_STATUSES:
                prefix = "\u2713 " if status == self._current else "  "
                yield Button(
                    f"{prefix}{_DISPLAY_NAMES[status]}",
                    id=f"status-{status.value}",
                )
            yield Button("Cancel", id="status-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "status-cancel":
            self.action_cancel()
            return
        for status in _ALL_STATUSES:
            if event.button.id == f"status-{status.value}":
                self.dismiss(status)
                return

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_select(self, index: int) -> None:
        index = int(index)
        if 0 <= index < len(_ALL_STATUSES):
            self.dismiss(_ALL_STATUSES[index])
