"""Confirmation modal screen used before destructive actions."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """A modal that asks the user to confirm an action.

    Dismisses with ``True`` when confirmed and ``False`` when cancelled.
    """

    DEFAULT_CSS = """
    ConfirmScreen Middle {
        align: center middle;
    }
    ConfirmScreen Center {
        width: auto;
        height: auto;
        padding: 1 2;
    }
    ConfirmScreen Vertical {
        width: auto;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    ConfirmScreen Static {
        text-align: center;
        margin-bottom: 1;
    }
    ConfirmScreen Horizontal {
        height: auto;
        align-horizontal: center;
    }
    ConfirmScreen Button {
        margin: 0 1;
    }
    """

    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes", show=True),
        Binding("n", "cancel", "No", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, message: str, title: str = "Confirm") -> None:
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        with Middle(), Center(), Vertical():
            yield Static(f"[bold]{self._title}[/bold]", classes="confirm-title")
            yield Static(self._message, classes="confirm-message")
            with Horizontal():
                yield Button("Yes", id="confirm-yes", variant="error")
                yield Button("No", id="confirm-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.action_confirm()
        elif event.button.id == "confirm-no":
            self.action_cancel()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
