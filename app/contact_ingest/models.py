from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


OwnerScope = Literal["ENTITY", "PERSON", "UNRESOLVED"]


@dataclass(frozen=True)
class TableData:
    source_member: str
    sheet_name: str
    rows: list[list[str]]


@dataclass(frozen=True)
class FieldMapping:
    source_column: str
    source_index: int
    canonical_field: str
    confidence: float
    owner_hint: OwnerScope | None = None


@dataclass
class ChannelPlan:
    owner_scope: OwnerScope
    channel_type: str
    raw_value: str
    normalized_value: str
    source_column: str
    source_row: int


@dataclass
class PersonPlan:
    full_name: str
    normalized_name: str
    relation_type: str
    title: str = ""
    department: str = ""
    channels: list[ChannelPlan] = field(default_factory=list)


@dataclass
class EntityPlan:
    canonical_name: str
    normalized_name: str
    normalized_address: str
    country_code: str = ""
    region_code: str = ""
    city: str = ""
    external_status: str = ""
    entity_type_hint: str = ""
    identifiers: dict[str, str] = field(default_factory=dict)
    aliases: list[tuple[str, str]] = field(default_factory=list)
    channels: list[ChannelPlan] = field(default_factory=list)
    people: list[PersonPlan] = field(default_factory=list)
    source_row: int = 0
    raw_record: dict[str, str] = field(default_factory=dict)


@dataclass
class CaseContactPlan:
    """A source-backed contact channel tied to a trademark case, not an owner.

    Some historical exports contain an application/registration number plus an
    email/phone but omit the attorney/applicant name. Keeping these observations
    unresolved prevents the importer from inventing a person/entity owner while
    still preserving useful source evidence for later resolution.
    """

    application_number: str = ""
    registration_number: str = ""
    jurisdiction: str = ""
    channels: list[ChannelPlan] = field(default_factory=list)
    source_row: int = 0
    raw_record: dict[str, str] = field(default_factory=dict)


@dataclass
class TablePlan:
    source_member: str
    sheet_name: str
    header_row: int
    profile: str
    profile_confidence: float
    mappings: list[FieldMapping]
    entities: list[EntityPlan]
    source_rows: int
    skipped_rows: int
    case_contacts: list[CaseContactPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    input_path: Path
    source_name: str
    source_sha256: str
    file_type: str
    version: str
    tables: list[TablePlan]
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        entities = sum(len(table.entities) for table in self.tables)
        case_contacts = sum(len(table.case_contacts) for table in self.tables)
        people = sum(
            len(entity.people)
            for table in self.tables
            for entity in table.entities
        )
        owned_channels = sum(
            len(entity.channels)
            + sum(len(person.channels) for person in entity.people)
            for table in self.tables
            for entity in table.entities
        )
        unresolved_channels = sum(
            len(record.channels)
            for table in self.tables
            for record in table.case_contacts
        )
        channels = owned_channels + unresolved_channels
        observations = channels
        return {
            "version": self.version,
            "input": str(self.input_path),
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "file_type": self.file_type,
            "tables_detected": len(self.tables),
            "source_rows": sum(table.source_rows for table in self.tables),
            "skipped_rows": sum(table.skipped_rows for table in self.tables),
            "entities_planned": entities,
            "people_planned": people,
            "case_contacts_planned": case_contacts,
            "channels_planned": channels,
            "owned_channels_planned": owned_channels,
            "unresolved_channels_planned": unresolved_channels,
            "channel_observations_planned": observations,
            "tables": [
                {
                    "source_member": table.source_member,
                    "sheet_name": table.sheet_name,
                    "header_row": table.header_row,
                    "profile": table.profile,
                    "profile_confidence": table.profile_confidence,
                    "source_rows": table.source_rows,
                    "entities_planned": len(table.entities),
                    "case_contacts_planned": len(table.case_contacts),
                    "skipped_rows": table.skipped_rows,
                    "field_mappings": [asdict(mapping) for mapping in table.mappings],
                    "warnings": table.warnings,
                }
                for table in self.tables
            ],
            "warnings": self.warnings,
        }
