"""SQLAlchemy models and the write path for one poll.

Works with SQLite (local) and PostgreSQL (k8s), selected via DATABASE_URL.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from kilometrix.detection import EndTrip, MoveTrip, PollState, Reading, StartTrip, step

BigIntPK = BigInteger().with_variant(Integer, "sqlite")
KM = Numeric(12, 3)
TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


trip_tags = Table(
    "trip_tags",
    Base.metadata,
    Column("trip_id", BigIntPK, ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", BigIntPK, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(TS)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    start_odometer: Mapped[Decimal] = mapped_column(KM)
    end_odometer: Mapped[Decimal] = mapped_column(KM)
    distance_km: Mapped[Decimal] = mapped_column(KM)
    start_lat: Mapped[float | None] = mapped_column(Float)
    start_lon: Mapped[float | None] = mapped_column(Float)
    end_lat: Mapped[float | None] = mapped_column(Float)
    end_lon: Mapped[float | None] = mapped_column(Float)
    position_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())

    tags: Mapped[list[Tag]] = relationship(secondary=trip_tags, back_populates="trips")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)

    trips: Mapped[list[Trip]] = relationship(secondary=trip_tags, back_populates="tags")


class PollStateRow(Base):
    __tablename__ = "poll_state"
    __table_args__ = (CheckConstraint("id = 1", name="poll_state_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_odometer: Mapped[Decimal | None] = mapped_column(KM)
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    last_moved_at: Mapped[datetime | None] = mapped_column(TS)
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lon: Mapped[float | None] = mapped_column(Float)
    last_position_at: Mapped[datetime | None] = mapped_column(TS)
    in_trip: Mapped[bool] = mapped_column(Boolean, default=False)
    trip_id: Mapped[int | None] = mapped_column(BigIntPK, ForeignKey("trips.id"))
    unchanged_polls: Mapped[int] = mapped_column(Integer, default=0)


class GeocodeCache(Base):
    """Reverse-geocoding results, keyed by coordinates rounded to 4 decimals (~11 m)."""

    __tablename__ = "geocode_cache"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    place: Mapped[str] = mapped_column(String(255))
    looked_up_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


def normalise_tag(name: str) -> str:
    """Tags are free-form, stored lowercase and trimmed."""
    cleaned = name.strip().lower()
    if not cleaned:
        raise ValueError("tag name must not be empty")
    if len(cleaned) > 64:
        raise ValueError("tag name must be 64 characters or fewer")
    return cleaned


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url)


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; they are always stored as UTC."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _load_state(row: PollStateRow | None) -> PollState:
    if row is None:
        return PollState()
    return PollState(
        last_odometer=row.last_odometer,
        last_seen_at=_utc(row.last_seen_at),
        last_moved_at=_utc(row.last_moved_at),
        last_lat=row.last_lat,
        last_lon=row.last_lon,
        last_position_at=_utc(row.last_position_at),
        in_trip=bool(row.in_trip),
        unchanged_polls=row.unchanged_polls or 0,
    )


def _save_state(row: PollStateRow, state: PollState, trip_id: int | None) -> None:
    row.last_odometer = state.last_odometer
    row.last_seen_at = state.last_seen_at
    row.last_moved_at = state.last_moved_at
    row.last_lat = state.last_lat
    row.last_lon = state.last_lon
    row.last_position_at = state.last_position_at
    row.in_trip = state.in_trip
    row.unchanged_polls = state.unchanged_polls
    row.trip_id = trip_id if state.in_trip else None


def apply_reading(session: Session, reading: Reading, end_after_polls: int = 2) -> list:
    """Run one poll through detection and write the result in the caller's transaction.

    The caller commits. Poll state and trip rows are written together, so a crash
    mid-poll leaves the database as it was before the poll.
    """
    row = session.get(PollStateRow, 1)
    if row is None:
        row = PollStateRow(id=1)
        session.add(row)

    state = _load_state(row)
    trip_id = row.trip_id
    new_state, events = step(state, reading, end_after_polls)

    for event in events:
        if isinstance(event, StartTrip):
            trip = Trip(
                started_at=event.started_at,
                ended_at=None,
                start_odometer=event.start_odometer,
                end_odometer=event.end_odometer,
                distance_km=event.end_odometer - event.start_odometer,
                start_lat=event.start_lat,
                start_lon=event.start_lon,
                end_lat=event.end_lat,
                end_lon=event.end_lon,
                position_at=event.position_at,
            )
            session.add(trip)
            session.flush()
            trip_id = trip.id
        elif isinstance(event, MoveTrip):
            trip = session.get(Trip, trip_id)
            trip.distance_km = event.end_odometer - trip.start_odometer
            trip.end_odometer = event.end_odometer
            trip.end_lat = event.end_lat
            trip.end_lon = event.end_lon
            trip.position_at = event.position_at
        elif isinstance(event, EndTrip):
            trip = session.get(Trip, trip_id)
            trip.ended_at = event.ended_at
            trip.end_lat = event.end_lat
            trip.end_lon = event.end_lon
            trip.position_at = event.position_at
            trip_id = None

    _save_state(row, new_state, trip_id)
    return events
