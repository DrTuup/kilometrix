"""Poller worker: poll the car, run trip detection, write to the database.

Run: uv run python -m kilometrix.poller
Migrations are not run here; run `uv run alembic upgrade head` first.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from kilometrix.config import Settings
from kilometrix.db import apply_reading, make_engine
from kilometrix.detection import Reading
from kilometrix.renault import RenaultReader

log = logging.getLogger("kilometrix.poller")

MAX_BACKOFF_SECONDS = 3600


async def run(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    reader = RenaultReader(
        username=settings.renault_username,
        password=settings.renault_password,
        vin=settings.renault_vin,
        locale=settings.renault_locale,
        country=settings.renault_country,
    )
    failures = 0
    log.info("poller started, interval=%ss", settings.poll_interval_seconds)

    while True:
        delay = settings.poll_interval_seconds
        try:
            raw = await reader.read()
            reading = Reading(
                at=datetime.now(UTC),
                odometer=Decimal(str(raw.odometer)),
                lat=raw.lat,
                lon=raw.lon,
                position_at=raw.position_at,
            )
            with Session(engine) as session:
                events = apply_reading(session, reading, settings.end_after_polls)
                session.commit()

            for event in events:
                log.info("detection: %s", type(event).__name__)
            if reading.lat is None or reading.lon is None:
                log.warning("no location in this poll; last known position kept")
            log.info("odometer=%s km", reading.odometer)
            failures = 0
        except Exception:
            failures += 1
            delay = min(settings.poll_interval_seconds * 2**failures, MAX_BACKOFF_SECONDS)
            log.exception("poll failed (%d in a row), retrying in %ss", failures, delay)
            try:
                await reader.close()
            except Exception:
                log.exception("closing Renault session failed")
        await asyncio.sleep(delay)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings.from_env()))


if __name__ == "__main__":
    main()
