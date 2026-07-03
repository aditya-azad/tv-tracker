"""SQLAlchemy ORM models for the TV tracker database."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Source(StrEnum):
    """Which API a tracked item's data originated from."""

    TMDB = "tmdb"
    JIKAN = "jikan"


class MediaType(StrEnum):
    """The kind of media being tracked."""

    MOVIE = "movie"
    SHOW = "show"


class WatchStatus(StrEnum):
    """A user's watch status for a tracked item."""

    PLANNING = "planning"
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"


class AlertType(StrEnum):
    """The kind of new-content alert raised by sync."""

    NEW_SEASON = "new_season"
    NEW_EPISODE = "new_episode"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TrackedItem(Base):
    """One row per movie or show the user is tracking."""

    __tablename__ = "tracked_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[Source] = mapped_column(nullable=False)
    media_type: Mapped[MediaType] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[WatchStatus] = mapped_column(nullable=False, default=WatchStatus.PLANNING)
    total_seasons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_episodes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    watched_episodes: Mapped[list[WatchedEpisode]] = relationship(
        back_populates="tracked_item",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[list[NewContentAlert]] = relationship(
        back_populates="tracked_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    def __repr__(self) -> str:
        return (
            f"TrackedItem(id={self.id}, source={self.source}, "
            f"media_type={self.media_type}, title={self.title!r})"
        )


class WatchedEpisode(Base):
    """One row per episode the user has marked as watched (shows only)."""

    __tablename__ = "watched_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracked_item_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_items.id", ondelete="CASCADE"), nullable=False
    )
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    watched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    tracked_item: Mapped[TrackedItem] = relationship(back_populates="watched_episodes")

    __table_args__ = (
        UniqueConstraint(
            "tracked_item_id",
            "season_number",
            "episode_number",
            name="uq_watched_episode",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"WatchedEpisode(id={self.id}, tracked_item_id={self.tracked_item_id}, "
            f"S{self.season_number:02}E{self.episode_number:02})"
        )


class NewContentAlert(Base):
    """An alert raised by sync when a new season or episode is detected."""

    __tablename__ = "new_content_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracked_item_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_items.id", ondelete="CASCADE"), nullable=False
    )
    alert_type: Mapped[AlertType] = mapped_column(nullable=False)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    tracked_item: Mapped[TrackedItem] = relationship(back_populates="alerts")

    __table_args__ = (
        UniqueConstraint(
            "tracked_item_id",
            "season_number",
            "alert_type",
            name="uq_alert_per_season",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"NewContentAlert(id={self.id}, tracked_item_id={self.tracked_item_id}, "
            f"alert_type={self.alert_type}, season_number={self.season_number})"
        )
