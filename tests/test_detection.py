from datetime import UTC, datetime, timedelta
from decimal import Decimal

from kilometrix.detection import EndTrip, MoveTrip, PollState, Reading, StartTrip, step

T0 = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def rd(minutes: int, km: str, lat=50.0, lon=6.0) -> Reading:
    return Reading(at=at(minutes), odometer=Decimal(km), lat=lat, lon=lon, position_at=at(minutes))


def run(readings, end_after=2):
    state, events = PollState(), []
    for r in readings:
        state, ev = step(state, r, end_after)
        events += ev
    return state, events


def test_first_reading_only_sets_baseline():
    state, events = step(PollState(), rd(0, "100"))
    assert events == []
    assert state.last_odometer == Decimal("100")
    assert state.in_trip is False


def test_parked_no_change_produces_nothing():
    _, events = run([rd(0, "100"), rd(5, "100"), rd(10, "100")])
    assert events == []


def test_start_uses_previous_reading_and_parked_position():
    _, events = run([rd(0, "100", lat=50.1, lon=6.1), rd(5, "103", lat=50.2, lon=6.2)])
    assert len(events) == 1
    start = events[0]
    assert isinstance(start, StartTrip)
    assert start.start_odometer == Decimal("100")
    assert start.end_odometer == Decimal("103")
    assert (start.start_lat, start.start_lon) == (50.1, 6.1)
    assert start.started_at == at(0)  # last poll before the odometer moved


def test_ongoing_updates_end_odometer_and_position():
    _, events = run([
        rd(0, "100"), rd(5, "103"), rd(10, "110", lat=50.5, lon=6.5),
    ])
    assert isinstance(events[1], MoveTrip)
    assert events[1].end_odometer == Decimal("110")
    assert (events[1].end_lat, events[1].end_lon) == (50.5, 6.5)


def test_end_after_two_unchanged_polls():
    state, events = run([rd(0, "100"), rd(5, "110"), rd(10, "110")])
    assert state.in_trip is True  # one unchanged poll is not enough
    state, ev = step(state, rd(15, "110"))
    events += ev
    end = events[-1]
    assert isinstance(end, EndTrip)
    assert end.ended_at == at(5)  # last time the odometer actually moved
    assert state.in_trip is False


def test_single_unchanged_poll_does_not_end_trip_when_moving_resumes():
    state, events = run([rd(0, "100"), rd(5, "110"), rd(10, "110"), rd(15, "120")])
    assert not any(isinstance(e, EndTrip) for e in events)
    assert state.in_trip is True


def test_odometer_going_backwards_is_ignored():
    state, events = run([rd(0, "100"), rd(5, "110")])
    before = state
    state2, ev = step(state, rd(10, "90"))
    assert ev == [] and state2 == before


def test_missing_location_keeps_last_known_position():
    readings = [rd(0, "100", lat=50.1, lon=6.1), Reading(at=at(5), odometer=Decimal("105"))]
    _, events = run(readings)
    assert events[0].start_lat == 50.1
    assert events[0].end_lat == 50.1  # carried forward, not None
    assert events[0].position_at == at(0)


def test_location_missing_throughout_is_none():
    _, events = run([Reading(at=at(0), odometer=Decimal("100")),
                     Reading(at=at(5), odometer=Decimal("105"))])
    assert events[0].start_lat is None and events[0].end_lat is None


def test_restart_mid_trip_resumes_without_new_start():
    state, _ = run([rd(0, "100"), rd(5, "110")])
    # simulate a restart: state round-trips through persistence unchanged
    restored = PollState(**state.__dict__)
    new_state, ev = step(restored, rd(10, "120"))
    assert all(not isinstance(e, StartTrip) for e in ev)
    assert isinstance(ev[0], MoveTrip)
