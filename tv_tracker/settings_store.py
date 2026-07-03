"""Database-backed application settings (API keys, tokens, etc.)."""

from __future__ import annotations

from tv_tracker.db import session_scope
from tv_tracker.models import Setting

TMDB_API_KEY = "tmdb_api_key"
TMDB_ACCESS_TOKEN = "tmdb_access_token"


def get_setting(key: str) -> str | None:
    """Return the value for *key*, or ``None`` if not stored."""
    with session_scope() as session:
        row = session.get(Setting, key)
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    """Insert or update the setting *key* with *value*."""
    with session_scope() as session:
        row = session.get(Setting, key)
        if row is not None:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))


def delete_setting(key: str) -> None:
    """Remove *key* from the settings table (no-op if not present)."""
    with session_scope() as session:
        row = session.get(Setting, key)
        if row is not None:
            session.delete(row)


def get_tmdb_api_key() -> str | None:
    """Return the stored TMDB API key, or ``None``."""
    return get_setting(TMDB_API_KEY)


def get_tmdb_access_token() -> str | None:
    """Return the stored TMDB read access token, or ``None``."""
    return get_setting(TMDB_ACCESS_TOKEN)


def set_tmdb_api_key(value: str) -> None:
    """Store the TMDB API key."""
    set_setting(TMDB_API_KEY, value)


def set_tmdb_access_token(value: str) -> None:
    """Store the TMDB read access token."""
    set_setting(TMDB_ACCESS_TOKEN, value)
