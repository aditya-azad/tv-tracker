"""Config tab: view and manage TMDB credentials."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from tv_tracker.settings_store import (
    delete_setting,
    get_tmdb_access_token,
    get_tmdb_api_key,
    set_tmdb_access_token,
    set_tmdb_api_key,
)


class CredentialPrompt(ModalScreen[str | None]):
    """Modal screen for entering a TMDB credential (API key or token)."""

    DEFAULT_CSS = """
    CredentialPrompt {
        align: center middle;
    }
    CredentialPrompt Vertical {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    CredentialPrompt Input {
        margin-bottom: 1;
    }
    CredentialPrompt Horizontal {
        height: auto;
    }
    CredentialPrompt Button {
        margin-right: 1;
    }
    """

    def __init__(self, title: str, placeholder: str, password: bool = True) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._password = password

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"[bold]{self._title}[/bold]")
            yield Input(
                placeholder=self._placeholder,
                password=self._password,
                id="cred-input",
            )
            with Horizontal():
                yield Button("Save", id="save-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "cred-input":
            self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self._save()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def _save(self) -> None:
        value = self.query_one("#cred-input", Input).value.strip()
        self.dismiss(value if value else None)


class ConfigPane(Vertical):
    """Config tab — show and manage TMDB API credentials."""

    DEFAULT_CSS = """
    ConfigPane {
        padding: 1 2;
    }
    ConfigPane Horizontal {
        height: auto;
        margin-top: 1;
    }
    ConfigPane Button {
        margin-right: 1;
    }
    ConfigPane Label.title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("TMDB Credentials", classes="title")
        yield Static(id="cred-status")
        yield Label("API Key", classes="title")
        with Horizontal():
            yield Button("Set API Key", id="set-key-btn", variant="primary")
            yield Button("Clear API Key", id="clear-key-btn", variant="error")
        yield Label("Read Access Token", classes="title")
        with Horizontal():
            yield Button("Set Token", id="set-token-btn", variant="primary")
            yield Button("Clear Token", id="clear-token-btn", variant="error")

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        has_key = get_tmdb_api_key() is not None
        has_token = get_tmdb_access_token() is not None
        status = self.query_one("#cred-status", Static)
        key_status = "[green]set[/green]" if has_key else "[red]not set[/red]"
        token_status = "[green]set[/green]" if has_token else "[red]not set[/red]"
        lines = [
            f"TMDB API key:      {key_status}",
            f"TMDB access token: {token_status}",
        ]
        if not has_key and not has_token:
            lines.append(
                "\n[dim]Set your TMDB API key to search and track titles.[/dim]\n"
                "[dim]Get a free key at https://www.themoviedb.org/settings/api[/dim]"
            )
        status.update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "set-key-btn":
                self._prompt_credential(
                    "Enter TMDB API Key",
                    "API key…",
                    self._save_key,
                )
            case "set-token-btn":
                self._prompt_credential(
                    "Enter TMDB Read Access Token",
                    "Access token…",
                    self._save_token,
                )
            case "clear-key-btn":
                delete_setting("tmdb_api_key")
                self.app.notify("[green]TMDB API key removed.[/green]", timeout=3)
                self.refresh_status()
            case "clear-token-btn":
                delete_setting("tmdb_access_token")
                self.app.notify("[green]TMDB access token removed.[/green]", timeout=3)
                self.refresh_status()

    def _prompt_credential(
        self, title: str, placeholder: str, callback: Callable[[str], None]
    ) -> None:
        def on_result(result: str | None) -> None:
            if result is not None:
                callback(result)

        self.app.push_screen(
            CredentialPrompt(title, placeholder, password=True),
            on_result,
        )

    def _save_key(self, key: str) -> None:
        set_tmdb_api_key(key)
        self.app.notify("[green]TMDB API key saved.[/green]", timeout=3)
        self.refresh_status()

    def _save_token(self, token: str) -> None:
        set_tmdb_access_token(token)
        self.app.notify("[green]TMDB access token saved.[/green]", timeout=3)
        self.refresh_status()
