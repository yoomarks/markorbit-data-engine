"""Source-native Singapore IPOS fact extraction.

This module parses authoritative IPOS fields without deriving legal meaning. Dates remain
source strings and nested JSON payloads retain the source-native structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.jurisdictions.singapore.source import JURISDICTION

from .detector import Observation


FieldAliases = tuple[str, ...]

_APPLICATION_NUMBER: FieldAliases = ("applicationNumber", "Application Number")
_FILING_DATE: FieldAliases = ("filingDate", "Filing Date")
_APPLICATION_TYPE: FieldAliases = ("applicationType", "Application Type")
_TRADE_MARK_TYPE: FieldAliases = ("tradeMarkType", "Trade Mark Type")
_MARK_STATUS: FieldAliases = ("markStatus", "Mark Status")
_MARK_STATUS_DATE: FieldAliases = ("markStatusDate", "Mark Status Date")
_STATUS_UPDATE_DATE: FieldAliases = ("statusUpdateDate", "Status Update Date")
_REGISTRATION_COMPLETION_DATE: FieldAliases = (
    "registrationProcedureCompletionDate",
    "Registration Procedure Completion Date",
)
_EXPIRY_DATE: FieldAliases = ("expiryDate", "Expiry Date")
_PUBLICATION_DATE: FieldAliases = ("publicationDate", "Publication Date")
_LAST_MODIFIED_DATE: FieldAliases = ("lastModifiedDate", "Last Modified Date")

_IR_DETAILS: FieldAliases = ("irDetails_json", "IR Details")
_MARK_DATA: FieldAliases = ("markData_json", "Mark Data")
_LICENSE_DATA: FieldAliases = ("licenseData_json", "License Data")
_TRANSFER_DATA: FieldAliases = ("transferData_json", "Transfer Data")
_DOCUMENTS: FieldAliases = ("documents_json", "Documents")
_GOODS_SERVICES: FieldAliases = (
    "goodsAndServicesSpecifications_json",
    "Goods And Services Specifications",
)
_PRIORITY_CLAIMS: FieldAliases = (
    "priorityClaimsDetails_json",
    "Priority Claims Details",
)
_APPLICANTS: FieldAliases = (
    "currentApplicantProprietorDetails_json",
    "Current Applicant Proprietor Details",
)
_AGENTS: FieldAliases = (
    "agentCorrespondenceDetails_json",
    "Agent Correspondence Details",
)


@dataclass(frozen=True)
class IposNativeApplicationFacts:
    """Selected source-native facts for one authoritative IPOS application row."""

    application_number: str
    mark_status: str
    filing_date: str | None
    application_type: str | None
    trade_mark_type: str | None
    mark_status_date: str | None
    status_update_date: str | None
    registration_completion_date: str | None
    expiry_date: str | None
    publication_date: str | None
    last_modified_date: str | None
    international_registration_details: tuple[dict[str, Any], ...]
    mark_data: tuple[dict[str, Any], ...]
    license_data: tuple[dict[str, Any], ...]
    transfer_data: tuple[dict[str, Any], ...]
    documents: tuple[dict[str, Any], ...]
    goods_services: tuple[dict[str, Any], ...]
    priority_claims: tuple[dict[str, Any], ...]
    applicants: tuple[dict[str, Any], ...]
    agents: tuple[dict[str, Any], ...]


def _first_value(row: Mapping[str, Any], aliases: FieldAliases) -> Any:
    for field in aliases:
        if field in row:
            return row[field]
    return None


def _optional_text(row: Mapping[str, Any], aliases: FieldAliases) -> str | None:
    value = _first_value(row, aliases)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(
    row: Mapping[str, Any], aliases: FieldAliases, *, label: str
) -> str:
    value = _optional_text(row, aliases)
    if value is None:
        raise ValueError(f"IPOS trademark row is missing {label}")
    return value


def _json_object_array(
    row: Mapping[str, Any], aliases: FieldAliases, *, label: str
) -> tuple[dict[str, Any], ...]:
    value = _first_value(row, aliases)
    if value is None:
        return ()

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return ()
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"IPOS {label} is not valid JSON") from exc
    else:
        decoded = value

    if not isinstance(decoded, list):
        raise ValueError(f"IPOS {label} must be a JSON array")
    if any(not isinstance(item, dict) for item in decoded):
        raise ValueError(f"IPOS {label} must contain JSON objects")

    return tuple(dict(item) for item in decoded)


def native_facts_from_ipos_row(row: Mapping[str, Any]) -> IposNativeApplicationFacts:
    """Extract selected authoritative facts without semantic/legal interpretation."""

    return IposNativeApplicationFacts(
        application_number=_required_text(
            row, _APPLICATION_NUMBER, label="Application Number"
        ),
        mark_status=_required_text(row, _MARK_STATUS, label="Mark Status"),
        filing_date=_optional_text(row, _FILING_DATE),
        application_type=_optional_text(row, _APPLICATION_TYPE),
        trade_mark_type=_optional_text(row, _TRADE_MARK_TYPE),
        mark_status_date=_optional_text(row, _MARK_STATUS_DATE),
        status_update_date=_optional_text(row, _STATUS_UPDATE_DATE),
        registration_completion_date=_optional_text(
            row, _REGISTRATION_COMPLETION_DATE
        ),
        expiry_date=_optional_text(row, _EXPIRY_DATE),
        publication_date=_optional_text(row, _PUBLICATION_DATE),
        last_modified_date=_optional_text(row, _LAST_MODIFIED_DATE),
        international_registration_details=_json_object_array(
            row, _IR_DETAILS, label="IR Details"
        ),
        mark_data=_json_object_array(row, _MARK_DATA, label="Mark Data"),
        license_data=_json_object_array(row, _LICENSE_DATA, label="License Data"),
        transfer_data=_json_object_array(
            row, _TRANSFER_DATA, label="Transfer Data"
        ),
        documents=_json_object_array(row, _DOCUMENTS, label="Documents"),
        goods_services=_json_object_array(
            row, _GOODS_SERVICES, label="Goods And Services Specifications"
        ),
        priority_claims=_json_object_array(
            row, _PRIORITY_CLAIMS, label="Priority Claims Details"
        ),
        applicants=_json_object_array(
            row, _APPLICANTS, label="Current Applicant Proprietor Details"
        ),
        agents=_json_object_array(
            row, _AGENTS, label="Agent Correspondence Details"
        ),
    )


def native_facts_from_ipos_observation(
    observation: Observation,
) -> IposNativeApplicationFacts:
    """Extract facts from an SG application observation and enforce identity integrity."""

    if observation.jurisdiction != JURISDICTION:
        raise ValueError("IPOS native facts require an SG observation")
    if observation.entity_type != "application":
        raise ValueError("IPOS native facts require an application observation")

    facts = native_facts_from_ipos_row(observation.payload)
    if facts.application_number != observation.entity_id:
        raise ValueError("IPOS observation identity does not match Application Number")
    return facts
