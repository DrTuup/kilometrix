"""Fill a database with mock trips so the UI can be tried without the car.

Usage (use a separate database, not the live one):
    DATABASE_URL=sqlite:///./kilometrix_demo.db uv run alembic upgrade head
    DATABASE_URL=sqlite:///./kilometrix_demo.db uv run python -m kilometrix.seed

Refuses to run if the target database already contains trips.
Does not touch poll_state, so a live poller pointed at the same DB stays consistent.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kilometrix.config import Settings
from kilometrix.db import Tag, Trip, make_engine

# Mock places (rough coordinates, Belgium). Not the owner's real locations.
PLACES = {
    "office": (50.8503, 4.3517),     # Brussels
    "home": (50.7846, 5.9941),       # Liège area, matches spike-era position
    "client-a": (51.2194, 4.4025),   # Antwerp
    "client-b": (50.9413, 4.0322),   # Leuven
    "airport": (50.9010, 4.4844),
    "shops": (50.6326, 5.5797),      # Liège centre
}

# (start place, end place, typical km, weight, tags)
ROUTES = [
    ("home", "office", 105, 5, ["work"]),
    ("office", "home", 105, 5, ["work"]),
    ("home", "client-a", 95, 2, ["work"]),
    ("client-a", "home", 95, 2, ["work"]),
    ("home", "client-b", 75, 1, ["work"]),
    ("home", "shops", 12, 3, ["personal"]),
    ("shops", "home", 12, 3, ["personal"]),
    ("home", "airport", 115, 1, ["personal", "travel"]),
    ("airport", "home", 115, 1, ["personal", "travel"]),
    ("home", None, 30, 2, []),  # untagged, unknown destination
]

HOURS = [7, 8, 9, 12, 13, 17, 18, 19, 20]


def make_trips(rng: random.Random, days: int, start_odometer: Decimal) -> list[tuple]:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    first_day = now - timedelta(days=days)
    odometer = start_odometer
    rows = []
    weights = [r[3] for r in ROUTES]

    for offset in range(days):
        day = first_day + timedelta(days=offset)
        if day.weekday() >= 5 and rng.random() < 0.7:
            continue  # most weekends have no driving
        for _ in range(rng.choice([1, 2, 2, 3]) if day.weekday() < 5 else rng.choice([0, 1, 2])):
            origin, dest, km, _, tags = rng.choices(ROUTES, weights=weights)[0]
            hour = rng.choice(HOURS)
            started = day.replace(hour=hour, minute=rng.randrange(0, 60))
            distance = Decimal(str(round(km * rng.uniform(0.85, 1.15), 1)))
            ended = started + timedelta(minutes=int(float(distance) * rng.uniform(1.2, 1.8)))

            start_pos = PLACES.get(origin)
            end_pos = PLACES.get(dest) if dest else None
            # Occasionally drop the location to exercise "no location".
            if rng.random() < 0.1:
                start_pos = None
            if dest is None or rng.random() < 0.08:
                end_pos = None

            rows.append(
                dict(
                    started_at=started,
                    ended_at=ended,
                    start_odometer=odometer,
                    end_odometer=odometer + distance,
                    distance_km=distance,
                    start_lat=start_pos[0] if start_pos else None,
                    start_lon=start_pos[1] if start_pos else None,
                    end_lat=end_pos[0] if end_pos else None,
                    end_lon=end_pos[1] if end_pos else None,
                    position_at=ended if end_pos else None,
                    tags=tags,
                )
            )
            odometer += distance

    # One trip still in progress, as the poller would leave it.
    rows.append(
        dict(
            started_at=now - timedelta(minutes=12),
            ended_at=None,
            start_odometer=odometer,
            end_odometer=odometer + Decimal("3.4"),
            distance_km=Decimal("3.4"),
            start_lat=PLACES["home"][0],
            start_lon=PLACES["home"][1],
            end_lat=None,
            end_lon=None,
            position_at=None,
            tags=[],
        )
    )
    return rows


def main() -> None:
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)

    with Session(engine) as session:
        existing = session.scalar(select(func.count()).select_from(Trip))
        if existing:
            sys.exit(f"Refusing: {settings.database_url} already has {existing} trips. Use an empty demo database.")

        rng = random.Random(42)  # reproducible
        rows = make_trips(rng, days=60, start_odometer=Decimal("62000"))
        tag_cache: dict[str, Tag] = {}

        for row in rows:
            tags = row.pop("tags")
            trip = Trip(**row)
            for name in tags:
                if name not in tag_cache:
                    tag_cache[name] = Tag(name=name)
                trip.tags.append(tag_cache[name])
            session.add(trip)
        session.commit()

        total = sum(float(r["distance_km"]) for r in rows)
        print(f"seeded {len(rows)} trips, {total:.1f} km, tags: {sorted(tag_cache)}")


if __name__ == "__main__":
    main()
