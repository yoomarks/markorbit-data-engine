"""Registry primitives for reusable trademark jurisdiction packs.

The registry intentionally stores country capability metadata only. It does not
replace jurisdiction-native schemas or ingestion implementations.
"""

from dataclasses import dataclass, field
from enum import Enum


class SourceRole(str, Enum):
    PRIMARY = "PRIMARY"
    ENRICHMENT = "ENRICHMENT"
    HISTORICAL_SEED = "HISTORICAL_SEED"


@dataclass(frozen=True)
class RegisteredSource:
    source_id: str
    authority: str
    role: SourceRole
    transport: str
    authoritative: bool = False


@dataclass(frozen=True)
class RegisteredJurisdiction:
    jurisdiction: str
    store_schema: str
    sources: tuple[RegisteredSource, ...] = field(default_factory=tuple)


_REGISTRY: dict[str, RegisteredJurisdiction] = {}


def register_jurisdiction(profile: RegisteredJurisdiction) -> None:
    existing = _REGISTRY.get(profile.jurisdiction)
    if existing is not None and existing != profile:
        raise ValueError(f"jurisdiction already registered: {profile.jurisdiction}")
    _REGISTRY[profile.jurisdiction] = profile


def get_jurisdiction(code: str) -> RegisteredJurisdiction | None:
    return _REGISTRY.get(code)


def list_jurisdictions() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
