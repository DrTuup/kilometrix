"""Renault (MyRenault / Kamereon) reader for one VIN.

Login can expose several Kamereon accounts. Only the account whose
`vehicleLinks` contain the VIN is used; never assume the first one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp
from renault_api.renault_client import RenaultClient


@dataclass(frozen=True)
class RawReading:
    odometer: float
    lat: float | None
    lon: float | None
    position_at: datetime | None


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class RenaultReader:
    def __init__(self, username: str, password: str, vin: str, locale: str, country: str):
        self._username = username
        self._password = password
        self._vin = vin
        self._locale = locale
        self._country = country
        self._websession: aiohttp.ClientSession | None = None
        self._vehicle: Any = None

    async def _connect(self) -> None:
        self._websession = aiohttp.ClientSession()
        client = RenaultClient(
            websession=self._websession, locale=self._locale, country=self._country
        )
        await client.session.login(self._username, self._password)
        for account in await client.get_api_accounts():
            links = (await account.get_vehicles()).vehicleLinks or []
            if any(link.vin == self._vin for link in links):
                self._vehicle = await account.get_api_vehicle(self._vin)
                return
        raise RuntimeError("VIN not found in any Renault account")

    async def read(self) -> RawReading:
        if self._vehicle is None:
            await self._connect()
        cockpit = await self._vehicle.get_cockpit()
        location = await self._vehicle.get_location()
        return RawReading(
            odometer=float(cockpit.totalMileage),
            lat=location.gpsLatitude,
            lon=location.gpsLongitude,
            position_at=_parse_time(location.lastUpdateTime),
        )

    async def close(self) -> None:
        """Drop the session so the next read logs in again."""
        if self._websession is not None:
            await self._websession.close()
        self._websession = None
        self._vehicle = None
