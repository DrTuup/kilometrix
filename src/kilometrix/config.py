"""Configuration from environment variables (and a local .env file)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing environment variable {name} (set it in .env)")
    return value


@dataclass(frozen=True)
class Settings:
    renault_username: str
    renault_password: str
    renault_vin: str
    renault_locale: str
    renault_country: str
    database_url: str
    poll_interval_seconds: int
    end_after_polls: int
    display_timezone: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        return cls(
            renault_username=_required("RENAULT_USERNAME"),
            renault_password=_required("RENAULT_PASSWORD"),
            renault_vin=_required("RENAULT_VIN"),
            # renault-api expects underscore locales (nl_BE), not nl-BE.
            renault_locale=os.environ.get("RENAULT_LOCALE", "nl_BE"),
            renault_country=os.environ.get("RENAULT_COUNTRY", "BE"),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///./kilometrix.db"),
            poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "300")),
            end_after_polls=int(os.environ.get("TRIP_END_UNCHANGED_POLLS", "2")),
            display_timezone=os.environ.get("DISPLAY_TIMEZONE", "Europe/Brussels"),
        )
