from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceCapability(StrEnum):
    BULK_SNAPSHOT = "BULK_SNAPSHOT"
    DELTA_UPDATE = "DELTA_UPDATE"
    DELETE_EVENT = "DELETE_EVENT"
    API_PAGINATION = "API_PAGINATION"
    IMAGES = "IMAGES"
    GOODS_SERVICES = "GOODS_SERVICES"
    OWNER_HISTORY = "OWNER_HISTORY"
    AGENT_HISTORY = "AGENT_HISTORY"
    RELATIONSHIPS = "RELATIONSHIPS"


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    jurisdiction: str
    source_id: str
    capabilities: frozenset[SourceCapability]

    def supports(self, capability: SourceCapability) -> bool:
        return capability in self.capabilities
