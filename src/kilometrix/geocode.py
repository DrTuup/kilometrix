"""Reverse geocoding (coordinates -> short place name) via OpenStreetMap Nominatim.

Policy: at most one request per second, identifying User-Agent, results cached.
Failures are not cached, so they are retried on a later request.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from kilometrix.db import GeocodeCache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
MIN_INTERVAL_SECONDS = 1.0

_lock = threading.Lock()
_last_call = 0.0


def cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 4):.4f},{round(lon, 4):.4f}"


def _format(address: dict) -> str | None:
    road = address.get("road") or address.get("pedestrian") or address.get("footway")
    town = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
    )
    parts = [p for p in (road, town) if p]
    return ", ".join(parts) or None


def _fetch(lat: float, lon: float, user_agent: str, timeout: float = 5.0) -> str | None:
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()

    query = urllib.parse.urlencode(
        {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1, "accept-language": "nl"}
    )
    request = urllib.request.Request(f"{NOMINATIM_URL}?{query}", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    return _format(data.get("address", {}))


def resolve_places(
    session: Session,
    points: list[tuple[float, float]],
    user_agent: str,
    max_lookups: int = 10,
) -> dict[str, str]:
    """Return {cache_key: place} for the given points.

    Uses the cache first, then performs at most `max_lookups` new lookups per call so a
    page load never blocks for long. Remaining points fill in on later loads.
    """
    wanted = {cache_key(lat, lon): (round(lat, 4), round(lon, 4)) for lat, lon in points}
    if not wanted:
        return {}

    found = {
        row.key: row.place
        for row in session.scalars(select(GeocodeCache).where(GeocodeCache.key.in_(wanted))).all()
    }
    budget = max_lookups
    for key, (lat, lon) in wanted.items():
        if key in found or budget <= 0:
            continue
        budget -= 1
        place = _fetch(lat, lon, user_agent)
        if place:
            session.add(GeocodeCache(key=key, place=place))
            found[key] = place
    session.commit()
    return found
