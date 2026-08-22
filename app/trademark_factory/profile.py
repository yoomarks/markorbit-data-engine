from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceTransport(StrEnum):
    FILE = "FILE"
    ZIP = "ZIP"
    XML = "XML"
    CSV = "CSV"
    JSON = "JSON"
    HTTP_API = "HTTP_API"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """A declarative description of an external trademark source.

    This intentionally describes capability and provenance only. It does not
    claim that a source is production-ready or legally complete.
    """

    source_id: str
    authority: str
    transport: SourceTransport
    authoritative: bool
    update_mode: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CountryProfile:
    """Country pack metadata consumed by the factory layer."""

    jurisdiction: str
    schema_name: str
    sources: tuple[SourceProfile, ...] = field(default_factory=tuple)
    pipeline_ready: bool = False

    def source(self, source_id: str) -> SourceProfile:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise KeyError(f"unknown trademark source: {source_id}")
