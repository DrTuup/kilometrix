"""Re-run the DB write-path tests against PostgreSQL when KILOMETRIX_TEST_PG_URL is set."""

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from kilometrix.db import Base, PollStateRow, Trip, apply_reading, make_engine
from kilometrix.detection import Reading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

PG = os.environ.get("KILOMETRIX_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set KILOMETRIX_TEST_PG_URL to run")

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def rd(minutes, km, lat=50.0, lon=6.0):
    t = T0 + timedelta(minutes=minutes)
    return Reading(at=t, odometer=Decimal(km), lat=lat, lon=lon, position_at=t)


@pytest.fixture
def engine():
    eng = make_engine(PG)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


def poll(engine, reading):
    with Session(engine) as s:
        ev = apply_reading(s, reading)
        s.commit()
    return ev


def test_pg_trip_lifecycle_and_restart(engine):
    poll(engine, rd(0, "100"))
    poll(engine, rd(5, "110", lat=50.1, lon=6.1))
    poll(engine, rd(10, "115"))  # restart happens between polls (new session each time)
    with Session(engine) as s:
        t = s.scalars(select(Trip)).one()
        assert t.ended_at is None and t.distance_km == Decimal("15")
        assert (t.start_lat, t.start_lon) == (50.0, 6.0)
        assert (t.end_lat, t.end_lon) == (50.0, 6.0)  # poll at 10 carries the default position
    poll(engine, rd(15, "115"))
    poll(engine, rd(20, "115"))
    with Session(engine) as s:
        t = s.scalars(select(Trip)).one()
        assert t.ended_at is not None
        assert t.ended_at == T0 + timedelta(minutes=10)
        st = s.get(PollStateRow, 1)
        assert st.in_trip is False and st.trip_id is None


def test_pg_numeric_precision(engine):
    poll(engine, rd(0, "69881.123"))
    poll(engine, rd(5, "69900.456"))
    with Session(engine) as s:
        t = s.scalars(select(Trip)).one()
        assert t.distance_km == Decimal("19.333")


def test_pg_timestamps_are_timestamptz(engine):
    poll(engine, rd(0, "100"))
    poll(engine, rd(5, "105"))
    with engine.connect() as c:
        typ = c.execute(text(
            "select data_type from information_schema.columns where table_name='trips' and column_name='started_at'"
        )).scalar()
    assert typ == "timestamp with time zone"
