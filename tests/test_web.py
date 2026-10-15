from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from kilometrix import web
from kilometrix.config import Settings
from kilometrix.db import Base, apply_reading, make_engine
from kilometrix.detection import Reading

T0 = datetime(2026, 10, 15, 6, 30, tzinfo=UTC)  # 08:30 in Europe/Brussels


def rd(minutes, km, lat=50.0, lon=6.0):
    t = T0 + timedelta(minutes=minutes)
    return Reading(at=t, odometer=Decimal(km), lat=lat, lon=lon, position_at=t)


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'web.db'}"
    monkeypatch.setenv("RENAULT_USERNAME", "x")
    monkeypatch.setenv("RENAULT_PASSWORD", "x")
    monkeypatch.setenv("RENAULT_VIN", "x")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Europe/Brussels")
    web.get_settings.cache_clear()
    web.get_engine.cache_clear()

    engine = make_engine(url)
    Base.metadata.create_all(engine)
    # Trip 1: 100 -> 120 km, ends at minute 20 (two unchanged polls).
    for step in [rd(0, "100"), rd(5, "110"), rd(10, "120"), rd(15, "120"), rd(20, "120")]:
        with Session(engine) as s:
            apply_reading(s, step)
            s.commit()
    # Trip 2: 120 -> 125, still in progress.
    for step in [rd(60 * 24, "125", lat=None, lon=None)]:
        with Session(engine) as s:
            apply_reading(s, step)
            s.commit()
    engine.dispose()

    app = web.create_app()
    with TestClient(app) as c:
        yield c
    web.get_engine.cache_clear()
    web.get_settings.cache_clear()


def test_index_and_static_served(client):
    assert client.get("/").status_code == 200
    assert "Kilometrix" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_list_trips_in_display_timezone(client):
    trips = client.get("/trips").json()
    assert len(trips) == 2
    first = [t for t in trips if t["distance_km"] == 20.0][0]
    assert first["started_at"].endswith("+02:00")  # CEST in October
    assert first["started_at"].startswith("2026-10-15T08:30")
    assert first["ended_at"] is not None


def test_in_progress_trip_has_no_end(client):
    trips = client.get("/trips").json()
    open_trip = [t for t in trips if t["ended_at"] is None][0]
    assert open_trip["distance_km"] == 5.0
    assert open_trip["end_lat"] is None or open_trip["end_lat"] == 50.0


def test_date_filter_uses_local_day(client):
    # 2026-10-15 local; the trip starts 08:30 local.
    assert len(client.get("/trips", params={"from": "2026-10-15", "to": "2026-10-15"}).json()) == 2
    assert len(client.get("/trips", params={"from": "2026-10-16"}).json()) == 0


def test_tag_set_and_filter_and_totals(client):
    trip = [t for t in client.get("/trips").json() if t["distance_km"] == 20.0][0]
    r = client.patch(f"/trips/{trip['id']}/tags", json={"tags": ["Work", " work ", "Personal"]})
    assert r.status_code == 200
    assert r.json()["tags"] == ["personal", "work"]  # normalised and de-duplicated

    work = client.get("/trips", params={"tag": "WORK"}).json()
    assert [t["id"] for t in work] == [trip["id"]]

    totals = client.get("/totals", params={"period": "day"}).json()
    assert totals["total_km"] == 25.0
    assert totals["by_tag"] == {"personal": 20.0, "work": 20.0}
    assert totals["untagged_km"] == 5.0
    assert totals["by_period"] == {"2026-10-15": 25.0}  # open trip starts at last parked poll (15 Oct); keys by local date


def test_totals_by_month_and_week(client):
    m = client.get("/totals", params={"period": "month"}).json()
    assert m["by_period"] == {"2026-10": 25.0}
    w = client.get("/totals", params={"period": "week"}).json()
    assert w["by_period"] == {"2026-10-12": 25.0}  # Monday of that week


def test_clear_tags(client):
    trip = client.get("/trips").json()[0]
    r = client.patch(f"/trips/{trip['id']}/tags", json={"tags": []})
    assert r.status_code == 200 and r.json()["tags"] == []


def test_bad_tag_rejected(client):
    trip = client.get("/trips").json()[0]
    r = client.patch(f"/trips/{trip['id']}/tags", json={"tags": ["   "]})
    assert r.status_code == 422


def test_unknown_trip_404(client):
    assert client.patch("/trips/99999/tags", json={"tags": ["x"]}).status_code == 404


def test_bad_period_rejected(client):
    assert client.get("/totals", params={"period": "year"}).status_code == 422
