"""Source-native to trademark-fact mapping contracts.

Mappings remain jurisdiction specific. This module only defines the reusable
shape of a mapping contract.
"""

from dataclasses import dataclass, field
from enum import Enum


class FactType(str, Enum):
    RECORD = "record"
    PARTY = "party"
    GOODS_SERVICES = "goods_services"
    EVENT = "event"
    RELATIONSHIP = "relationship"
    ASSET = "asset"


@dataclass(frozen=True)
class FieldMapping:
    source_path: str
    target_fact: str
    fact_type: FactType
    required: bool = False


@dataclass(frozen=True)
class MappingContract:
    jurisdiction: str
    source_id: str
    fields: tuple[FieldMapping, ...] = field(default_factory=tuple)

    def required_fields(self) -> tuple[FieldMapping, ...]:
        return tuple(field for field in self.fields if field.required)

    def facts(self) -> tuple[FactType, ...]:
        return tuple(sorted({field.fact_type for field in self.fields}, key=str))
