"""Trip detection as a pure state machine.

`step` takes the previous poll state and one new reading and returns the new
state plus the trip events to persist. It does no I/O, so it can be tested
without the Renault API or a database.

Rules (see PLAN.md, "Trip detection"):
- Start: odometer increases while parked.
- Ongoing: odometer keeps increasing; end odometer and position are updated.
- End: odometer unchanged for `end_after_polls` consecutive polls.
- Distance always comes from the odometer, never from GPS.
- Positions are only kept as "last known", never as a history.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Reading:
    """One poll. `at` is the poll time (UTC). Position may be missing."""

    at: datetime
    odometer: Decimal
    lat: float | None = None
    lon: float | None = None
    position_at: datetime | None = None


@dataclass(frozen=True)
class PollState:
    """Detection state, persisted in `poll_state` between polls."""

    last_odometer: Decimal | None = None
    last_seen_at: datetime | None = None
    last_moved_at: datetime | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    last_position_at: datetime | None = None
    in_trip: bool = False
    unchanged_polls: int = 0


@dataclass(frozen=True)
class StartTrip:
    started_at: datetime
    start_odometer: Decimal
    start_lat: float | None
    start_lon: float | None
    end_odometer: Decimal
    end_lat: float | None
    end_lon: float | None
    position_at: datetime | None


@dataclass(frozen=True)
class MoveTrip:
    end_odometer: Decimal
    end_lat: float | None
    end_lon: float | None
    position_at: datetime | None


@dataclass(frozen=True)
class EndTrip:
    ended_at: datetime
    end_lat: float | None
    end_lon: float | None
    position_at: datetime | None


Event = StartTrip | MoveTrip | EndTrip


def _position(state: PollState, reading: Reading) -> tuple[float | None, float | None, datetime | None]:
    """Use the new position when the API gave one, otherwise keep the last known."""
    if reading.lat is not None and reading.lon is not None:
        return reading.lat, reading.lon, reading.position_at or reading.at
    return state.last_lat, state.last_lon, state.last_position_at


def step(
    state: PollState, reading: Reading, end_after_polls: int = 2
) -> tuple[PollState, list[Event]]:
    lat, lon, pos_at = _position(state, reading)

    # First ever reading: just establish the baseline.
    if state.last_odometer is None:
        return (
            PollState(
                last_odometer=reading.odometer,
                last_seen_at=reading.at,
                last_lat=lat,
                last_lon=lon,
                last_position_at=pos_at,
            ),
            [],
        )

    # Odometer went backwards: bad data. Ignore the reading entirely.
    if reading.odometer < state.last_odometer:
        return state, []

    base = replace(
        state,
        last_odometer=reading.odometer,
        last_seen_at=reading.at,
        last_lat=lat,
        last_lon=lon,
        last_position_at=pos_at,
    )
    moved = reading.odometer > state.last_odometer

    if moved:
        if not state.in_trip:
            event = StartTrip(
                started_at=state.last_seen_at,  # type: ignore[arg-type]  # set once baseline exists
                start_odometer=state.last_odometer,
                start_lat=state.last_lat,
                start_lon=state.last_lon,
                end_odometer=reading.odometer,
                end_lat=lat,
                end_lon=lon,
                position_at=pos_at,
            )
            return replace(base, in_trip=True, unchanged_polls=0, last_moved_at=reading.at), [event]
        event = MoveTrip(
            end_odometer=reading.odometer, end_lat=lat, end_lon=lon, position_at=pos_at
        )
        return replace(base, unchanged_polls=0, last_moved_at=reading.at), [event]

    # Odometer unchanged.
    if not state.in_trip:
        return base, []

    unchanged = state.unchanged_polls + 1
    if unchanged < end_after_polls:
        return replace(base, unchanged_polls=unchanged), []

    # Trip ends at the last time the odometer actually moved.
    event = EndTrip(
        ended_at=state.last_moved_at,  # type: ignore[arg-type]  # set when the trip started
        end_lat=lat,
        end_lon=lon,
        position_at=pos_at,
    )
    return replace(base, in_trip=False, unchanged_polls=0), [event]
