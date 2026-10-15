"""Spike: log in to MyRenault and print odometer and location for one VIN.

Usage:
    uv run python -m kilometrix.spike

Reads RENAULT_USERNAME, RENAULT_PASSWORD and RENAULT_VIN from the environment
or from a local .env file.
"""

import asyncio
import json
import os
import sys

import aiohttp
from dotenv import load_dotenv
from renault_api.renault_client import RenaultClient


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing environment variable {name} (set it in .env)")
    return value


async def main() -> None:
    load_dotenv()
    username = _require("RENAULT_USERNAME")
    password = _require("RENAULT_PASSWORD")
    vin = _require("RENAULT_VIN")
    # renault-api expects underscore locales (nl_BE), not nl-BE.
    locale = os.environ.get("RENAULT_LOCALE", "nl_BE")
    country = os.environ.get("RENAULT_COUNTRY", "BE")

    async with aiohttp.ClientSession() as websession:
        client = RenaultClient(websession=websession, locale=locale, country=country)
        await client.session.login(username, password)
        print("login: ok")

        # One login can expose several Kamereon accounts; only one holds the car.
        vehicle = None
        for account in await client.get_api_accounts():
            links = (await account.get_vehicles()).vehicleLinks or []
            if any(link.vin == vin for link in links):
                vehicle = await account.get_api_vehicle(vin)
                break
        if vehicle is None:
            sys.exit("VIN not found in any Renault account")

        cockpit = await vehicle.get_cockpit()
        location = await vehicle.get_location()

        print("odometer (totalMileage):", cockpit.totalMileage, "km")
        print("location lastUpdateTime:", location.lastUpdateTime)
        print("location lat/lon:", location.gpsLatitude, location.gpsLongitude)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # spike: show everything, don't hide API errors
        print(f"spike failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
