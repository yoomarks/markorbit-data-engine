from __future__ import annotations

from dataclasses import dataclass

from app.trademark_factory.profile import CountryProfile
from app.trademark_framework.contracts import CountryPack
from app.trademark_framework.registry import country_packs


FACTORY_REGISTRY_VERSION = "TRADEMARK_COUNTRY_FACTORY_REGISTRY_V1"


@dataclass(frozen=True, slots=True)
class FactoryRegistryAudit:
    version: str
    country_count: int
    source_count: int
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "country_count": self.country_count,
            "source_count": self.source_count,
            "ready": self.ready,
            "errors": list(self.errors),
        }


class FactoryRegistry:
    """Read-only registry used by the country factory.

    Production instances are projected from ``app.trademark_framework.registry``. The
    constructor also accepts explicit packs so deterministic virtual-country fixtures can
    exercise the factory without mutating the production registry.
    """

    def __init__(self, packs: tuple[CountryPack, ...]) -> None:
        self._packs = packs
        self._canonical: dict[str, CountryPack] = {}
        self._aliases: dict[str, str] = {}
        self._build_indexes()

    def _build_indexes(self) -> None:
        for pack in self._packs:
            canonical = pack.jurisdiction.strip().upper()
            if canonical in self._canonical:
                raise ValueError(f"duplicate jurisdiction in factory registry: {canonical}")
            self._canonical[canonical] = pack
            for alias in (canonical, *pack.aliases):
                key = alias.strip().upper()
                existing = self._aliases.get(key)
                if existing is not None and existing != canonical:
                    raise ValueError(
                        f"jurisdiction alias collision in factory registry: {key} -> "
                        f"{existing}/{canonical}"
                    )
                self._aliases[key] = canonical

    @classmethod
    def from_framework(cls) -> "FactoryRegistry":
        return cls(country_packs())

    @property
    def packs(self) -> tuple[CountryPack, ...]:
        return self._packs

    def country_pack(self, jurisdiction: str) -> CountryPack:
        key = jurisdiction.strip().upper()
        try:
            canonical = self._aliases[key]
            return self._canonical[canonical]
        except KeyError as exc:
            raise ValueError(f"unsupported trademark jurisdiction: {jurisdiction}") from exc

    def profile(self, jurisdiction: str) -> CountryProfile:
        return CountryProfile.from_pack(self.country_pack(jurisdiction))

    def profiles(self) -> tuple[CountryProfile, ...]:
        return tuple(CountryProfile.from_pack(pack) for pack in self._packs)

    def audit(self) -> FactoryRegistryAudit:
        errors: list[str] = []
        source_count = 0
        source_keys: set[tuple[str, str]] = set()
        for pack in self._packs:
            errors.extend(pack.validate())
            for source in pack.sources:
                source_count += 1
                key = (pack.jurisdiction, source.source_id)
                if key in source_keys:
                    errors.append(f"duplicate factory source key: {key[0]}:{key[1]}")
                source_keys.add(key)
        return FactoryRegistryAudit(
            version=FACTORY_REGISTRY_VERSION,
            country_count=len(self._packs),
            source_count=source_count,
            errors=tuple(errors),
        )


def factory_registry() -> FactoryRegistry:
    """Return a fresh read-only projection of the authoritative framework registry."""
    return FactoryRegistry.from_framework()
