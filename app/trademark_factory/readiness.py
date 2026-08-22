from __future__ import annotations

from enum import IntEnum


class CountryReadiness(IntEnum):
    """Engineering maturity only, never legal/data completeness."""

    SOURCE_FOUND = 10
    SOURCE_PROFILED = 20
    PREFLIGHT_READY = 30
    PARSER_READY = 40
    COUNTRY_STORE_READY = 50
    CURRENT_PROJECTION_READY = 60
    ASSET_READY = 70
    PILOT_VALIDATED = 80
    RELEASE_ACCEPTED = 90
    PRODUCTION_CURRENT = 100


def can_reach(current: CountryReadiness, target: CountryReadiness) -> bool:
    return target >= current
