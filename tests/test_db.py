from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kilometrix.db import PollStateRow, Trip, apply_reading, make_engine, normalise_tag, Base, Tag
from kilometrix.detection import Reading

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def rd(minutes, km, lat=50.0, lon=6.0):
    t = T0 + timedelta(minutes=minutes)
    return Reading(at=t, odometer=Decimal(km), lat=lat, lon=lon, position_at=t)


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path/'t.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def poll(engine, reading):
    with Session(engine) as s:
        events = apply_reading(s, reading)
        s.commit()
    return events


def test_full_trip_is_persisted(engine):
    poll(engine, rd(0, "100"))
    poll(engine, rd(5, "110", lat=50.1, lon=6.1))
    poll(engine, rd(10, "120"))
    poll(engine, rd(15, "120"))  # first unchanged poll
    poll(engine, rd(20, "120"))  # second: trip ends
    with Session(engine) as s:
        trips = s.scalars(select(Trip)).all()
        assert len(trips) == 1
        t = trips[0]
        assert t.distance_km == Decimal("20")
        assert t.start_odometer == Decimal("100")
        assert t.end_odometer == Decimal("120")
        assert t.ended_at is not None
        st = s.get(PollStateRow, 1)
        assert st.in_trip is False and st.trip_id is None


def test_open_trip_survives_restart(engine):
    poll(engine, rd(0, "100"))
    poll(engine, rd(5, "110"))
    # new session = process restart
    poll(engine, rd(10, "115"))
    with Session(engine) as s:
        trips = s.scalars(select(Trip)).all()
        assert len(trips) == 1  # resumed, not duplicated
        assert trips[0].ended_at is None
        assert trips[0].distance_km == Decimal("15")
        assert s.get(PollStateRow, 1).trip_id == trips[0].id


def test_no_intermediate_positions_stored(engine):
    poll(engine, rd(0, "100"))
    poll(engine, rd(5, "110", lat=50.1, lon=6.1))
    poll(engine, rd(10, "115", lat=50.2, lon=6.2))
    with Session(engine) as s:
        t = s.scalars(select(Trip)).one()
        # only start and latest end positions exist, on the trip row
        assert (t.start_lat, t.start_lon) == (50.0, 6.0)
        assert (t.end_lat, t.end_lon) == (50.2, 6.2)


def test_missing_location_stored_as_null(engine):
    poll(engine, Reading(at=T0, odometer=Decimal("100")))
    poll(engine, Reading(at=T0 + timedelta(minutes=5), odometer=Decimal("105")))
    with Session(engine) as s:
        t = s.scalars(select(Trip)).one()
        assert t.start_lat is None and t.end_lat is None


def test_tags_normalised():
    assert normalise_tag("  Work ") == "work"
    with pytest.raises(ValueError):
        normalise_tag("   ")


def test_tag_name_unique(engine):
    with Session(engine) as s:
        s.add(Tag(name="work"))
        s.commit()
        s.add(Tag(name="work"))
        with pytest.raises(Exception):
            s.commit()
