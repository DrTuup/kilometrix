"""FastAPI web app: trip list, totals, tagging. Serves the static HTML page.

Run: uv run uvicorn kilometrix.web:app --reload
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from kilometrix.db import Base, Tag, Trip, make_engine, normalise_tag, trip_tags
from kilometrix.geocode import cache_key, resolve_places

STATIC = Path(__file__).parent / "static"


@dataclass(frozen=True)
class WebSettings:
    """What the web app needs. No Renault credentials: the web pod never talks to Renault."""

    database_url: str
    display_timezone: str
    user_agent: str


@lru_cache
def get_settings() -> WebSettings:
    load_dotenv()
    return WebSettings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./kilometrix.db"),
        display_timezone=os.environ.get("DISPLAY_TIMEZONE", "Europe/Brussels"),
        user_agent=os.environ.get(
            "GEOCODE_USER_AGENT", "kilometrix/1.0 (https://kilometrix.local.rubenclaessens.nl)"
        ),
    )


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


def get_session():
    with Session(get_engine()) as session:
        yield session


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().display_timezone)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Local calendar day as a UTC range, so a trip is filtered by its local date."""
    zone = tz()
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def to_local_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz()).isoformat()


class TripOut(BaseModel):
    id: int
    started_at: str
    ended_at: str | None
    start_odometer: float
    end_odometer: float
    distance_km: float
    start_lat: float | None
    start_lon: float | None
    end_lat: float | None
    end_lon: float | None
    position_at: str | None
    tags: list[str]
    start_place: str | None = None
    end_place: str | None = None


def trip_out(trip: Trip, places: dict[str, str] | None = None) -> TripOut:
    places = places or {}

    def place(lat: float | None, lon: float | None) -> str | None:
        if lat is None or lon is None:
            return None
        return places.get(cache_key(lat, lon))

    return TripOut(
        id=trip.id,
        started_at=to_local_iso(trip.started_at),
        ended_at=to_local_iso(trip.ended_at),
        start_odometer=float(trip.start_odometer),
        end_odometer=float(trip.end_odometer),
        distance_km=float(trip.distance_km),
        start_lat=trip.start_lat,
        start_lon=trip.start_lon,
        end_lat=trip.end_lat,
        end_lon=trip.end_lon,
        position_at=to_local_iso(trip.position_at),
        tags=sorted(t.name for t in trip.tags),
        start_place=place(trip.start_lat, trip.start_lon),
        end_place=place(trip.end_lat, trip.end_lon),
    )


class TagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=50)


def create_app() -> FastAPI:
    app = FastAPI(title="Kilometrix")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/trips", response_model=list[TripOut])
    def list_trips(
        session: Session = Depends(get_session),
        day_from: date | None = Query(None, alias="from"),
        day_to: date | None = Query(None, alias="to"),
        tag: str | None = None,
    ) -> list[TripOut]:
        stmt = select(Trip).options(selectinload(Trip.tags)).order_by(Trip.started_at.desc())
        if day_from:
            stmt = stmt.where(Trip.started_at >= local_day_bounds(day_from)[0])
        if day_to:
            stmt = stmt.where(Trip.started_at < local_day_bounds(day_to)[1])
        if tag:
            stmt = stmt.where(Trip.tags.any(Tag.name == normalise_tag(tag)))
        trips = session.scalars(stmt).all()
        points = [
            (lat, lon)
            for t in trips
            for lat, lon in ((t.start_lat, t.start_lon), (t.end_lat, t.end_lon))
            if lat is not None and lon is not None
        ]
        places = resolve_places(session, points, user_agent=get_settings().user_agent)
        return [trip_out(t, places) for t in trips]

    @app.get("/totals")
    def totals(
        session: Session = Depends(get_session),
        day_from: date | None = Query(None, alias="from"),
        day_to: date | None = Query(None, alias="to"),
        period: str = Query("month", pattern="^(day|week|month)$"),
    ) -> dict:
        """km per tag and per period, plus untagged totals. Periods are local calendar buckets."""
        stmt = select(Trip).options(selectinload(Trip.tags))
        if day_from:
            stmt = stmt.where(Trip.started_at >= local_day_bounds(day_from)[0])
        if day_to:
            stmt = stmt.where(Trip.started_at < local_day_bounds(day_to)[1])
        trips = session.scalars(stmt).all()

        by_tag: dict[str, Decimal] = {}
        by_period: dict[str, Decimal] = {}
        untagged = Decimal("0")
        total = Decimal("0")
        for trip in trips:
            local = trip.started_at.replace(tzinfo=UTC) if trip.started_at.tzinfo is None else trip.started_at
            local = local.astimezone(tz()).date()
            key = _period_key(local, period)
            by_period[key] = by_period.get(key, Decimal("0")) + trip.distance_km
            total += trip.distance_km
            if not trip.tags:
                untagged += trip.distance_km
            for t in trip.tags:
                by_tag[t.name] = by_tag.get(t.name, Decimal("0")) + trip.distance_km

        return {
            "period": period,
            "total_km": float(total),
            "untagged_km": float(untagged),
            "by_tag": {k: float(v) for k, v in sorted(by_tag.items())},
            "by_period": {k: float(v) for k, v in sorted(by_period.items())},
        }

    @app.patch("/trips/{trip_id}/tags", response_model=TripOut)
    def set_tags(
        trip_id: int, body: TagsIn, session: Session = Depends(get_session)
    ) -> TripOut:
        trip = session.get(Trip, trip_id, options=[selectinload(Trip.tags)])
        if trip is None:
            raise HTTPException(status_code=404, detail="trip not found")
        wanted = set()
        for raw in body.tags:
            try:
                wanted.add(normalise_tag(raw))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        existing = {t.name: t for t in session.scalars(select(Tag).where(Tag.name.in_(wanted))).all()}
        new_tags = []
        for name in wanted:
            if name not in existing:
                tag = Tag(name=name)
                session.add(tag)
                existing[name] = tag
            new_tags.append(existing[name])
        trip.tags = new_tags
        session.commit()
        session.refresh(trip)
        return trip_out(trip)

    @app.get("/tags", response_model=list[str])
    def list_tags(session: Session = Depends(get_session)) -> list[str]:
        return sorted(session.scalars(select(Tag.name)).all())

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _period_key(day: date, period: str) -> str:
    if period == "day":
        return day.isoformat()
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    return day.strftime("%Y-%m")


app = create_app()
