"""Create a local SQLite database with realistic mock trips for testing the web UI.

Pre-fills the geocode cache, so place names show up without calling Nominatim.

Usage:
    uv run python scripts/seed_mock_db.py                 # writes ./mock.db
    uv run python scripts/seed_mock_db.py --path other.db --reset
    DATABASE_URL=sqlite:///./mock.db uv run uvicorn kilometrix.web:app --reload
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from kilometrix.db import Base, GeocodeCache, Tag, Trip, make_engine
from kilometrix.geocode import cache_key

# name -> (lat, lon, place label as the geocoder would return it)
PLACES = {
    "home": (50.8280, 3.2650, "Hoogstraat, Kortrijk"),
    "office": (50.8125, 3.2813, "Pottelberg, Kortrijk"),
    "gent": (51.0543, 3.7174, "Korenmarkt, Gent"),
    "brugge": (51.2093, 3.2247, "Markt, Brugge"),
    "antwerpen": (51.2194, 4.4025, "Meir, Antwerpen"),
    "brussel": (50.8503, 4.3517, "Grote Markt, Brussel"),
    "oostende": (51.2300, 2.9125, "Kapellestraat, Oostende"),
}

# (start place, end place, start odometer km, distance km, start date, tags, end_in_progress)
TRIPS = [
    ("home", "office", 12000.0, 6.4, datetime(2026, 10, 12, 7, 10), ["work"], False),
    ("office", "home", 12006.4, 6.6, datetime(2026, 10, 12, 17, 5), ["work"], False),
    ("home", "gent", 12013.0, 52.3, datetime(2026, 10, 13, 9, 0), ["family"], False),
    ("gent", "home", 12065.3, 53.1, datetime(2026, 10, 13, 18, 20), ["family"], False),
    ("home", "brugge", 12118.4, 58.7, datetime(2026, 10, 14, 10, 30), [], False),
    ("brugge", "home", 12177.1, 57.9, datetime(2026, 10, 14, 19, 45), [], False),
    ("home", "office", 12235.0, 6.5, datetime(2026, 10, 15, 7, 15), ["work"], False),
    ("office", "antwerpen", 12241.5, 84.2, datetime(2026, 10, 15, 12, 0), ["work", "client"], False),
    ("antwerpen", "brussel", 12325.7, 54.8, datetime(2026, 10, 16, 8, 40), ["work", "client"], False),
    ("brussel", "home", 12380.5, 81.0, datetime(2026, 10, 16, 17, 30), ["work"], False),
    ("home", "oostende", 12461.5, 66.2, datetime(2026, 10, 18, 11, 0), [], False),
    ("oostende", "home", 12527.7, 65.8, datetime(2026, 10, 18, 16, 50), [], False),
    # trip still running: no end yet, last known position mid-route
    ("home", "gent", 12593.5, 18.0, datetime(2026, 10, 20, 9, 30), ["family"], True),
]

# Trips with no GPS fix (API returned no coordinates), to test "no location" rendering.
NO_LOCATION_TRIP = (12610.0, 9.4, datetime(2026, 10, 21, 8, 5), ["work"])


def build(path: Path, reset: bool) -> None:
    if reset and path.exists():
        path.unlink()
    url = f"sqlite:///{path}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        if session.scalars(select(Trip.id)).first() is not None:
            raise SystemExit(f"{path} already contains trips. Use --reset to rebuild it.")

        tags: dict[str, Tag] = {}

        def tag_for(name: str) -> Tag:
            if name not in tags:
                tags[name] = Tag(name=name)
                session.add(tags[name])
            return tags[name]

        for start, end, odo, km, started, trip_tags, in_progress in TRIPS:
            s_lat, s_lon, _ = PLACES[start]
            e_lat, e_lon, _ = PLACES[end]
            ended = None if in_progress else started + timedelta(minutes=round(km * 1.1) + 5)
            trip = Trip(
                started_at=started.replace(tzinfo=UTC) - timedelta(hours=1),  # stored as UTC
                ended_at=None if ended is None else ended.replace(tzinfo=UTC) - timedelta(hours=1),
                start_odometer=Decimal(str(odo)),
                end_odometer=Decimal(str(round(odo + km, 3))),
                distance_km=Decimal(str(km)),
                start_lat=s_lat,
                start_lon=s_lon,
                end_lat=e_lat,
                end_lon=e_lon,
                position_at=(ended or started).replace(tzinfo=UTC) - timedelta(hours=1),
            )
            trip.tags = [tag_for(t) for t in trip_tags]
            session.add(trip)

        odo, km, started, trip_tags = NO_LOCATION_TRIP
        trip = Trip(
            started_at=started.replace(tzinfo=UTC) - timedelta(hours=1),
            ended_at=(started + timedelta(minutes=20)).replace(tzinfo=UTC) - timedelta(hours=1),
            start_odometer=Decimal(str(odo)),
            end_odometer=Decimal(str(round(odo + km, 3))),
            distance_km=Decimal(str(km)),
        )
        trip.tags = [tag_for(t) for t in trip_tags]
        session.add(trip)

        # Pre-fill the geocode cache so the UI shows place names without network access.
        for lat, lon, label in PLACES.values():
            session.merge(GeocodeCache(key=cache_key(lat, lon), place=label))

        session.commit()
        print(f"Wrote {len(TRIPS) + 1} trips, {len(tags)} tags and {len(PLACES)} places to {path}")
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=Path("mock.db"))
    parser.add_argument("--reset", action="store_true", help="delete the file first if it exists")
    args = parser.parse_args()
    build(args.path, args.reset)


if __name__ == "__main__":
    main()
