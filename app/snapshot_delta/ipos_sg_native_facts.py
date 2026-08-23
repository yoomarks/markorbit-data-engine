"""Source-native Singapore IPOS fact extraction.

This module parses authoritative IPOS fields without deriving legal meaning. Dates remain
source strings and nested JSON payloads retain the source-native structure.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Mapping

from app.jurisdictions.singapore.source import JURISDICTION

from .detector import Observation


FieldAliases = tuple[str, ...]

# Official data.gov.sg/IPOS source fields, excluding provider-only datastore row id `_id`.
IPOS_NATIVE_SOURCE_FIELDS: tuple[str, ...] = (
    "applicationNumber",
    "filingDate",
    "internationalRegDate",
    "singaporeProtectionDate",
    "seriesMarkNum",
    "applicationType",
    "tradeMarkType",
    "descriptionParticularFeatureOfMark",
    "applicationDate",
    "markStatus",
    "markStatusDate",
    "statusUpdateDate",
    "registrationProcedureCompletionDate",
    "expiryDate",
    "publicationDate",
    "lastModifiedDate",
    "journalData_json",
    "irDetails_json",
    "iaDetails_json",
    "transformationData_json",
    "transformationIntoData_json",
    "replacementData_json",
    "priorityData_json",
    "replacementReplacesData_json",
    "markClausesData_json",
    "markData_json",
    "hmgCases_json",
    "otherEntriesData_json",
    "logogramData_json",
    "licenseData_json",
    "grantorData_json",
    "granteeData_json",
    "securityInterestData_json",
    "transferData_json",
    "documents_json",
    "goodsAndServicesSpecifications_json",
    "priorityClaimsDetails_json",
    "currentApplicantProprietorDetails_json",
    "agentCorrespondenceDetails_json",
)
_PROVIDER_METADATA_FIELDS = frozenset({"_id"})

_APPLICATION_NUMBER: FieldAliases = ("applicationNumber", "Application Number")
_FILING_DATE: FieldAliases = ("filingDate", "Filing Date")
_INTERNATIONAL_REGISTRATION_DATE: FieldAliases = (
    "internationalRegDate",
    "International Registration Date",
)
_SINGAPORE_PROTECTION_DATE: FieldAliases = (
    "singaporeProtectionDate",
    "Singapore Protection Date",
)
_SERIES_MARK_NUMBER: FieldAliases = ("seriesMarkNum", "Series Mark Number")
_APPLICATION_TYPE: FieldAliases = ("applicationType", "Application Type")
_TRADE_MARK_TYPE: FieldAliases = ("tradeMarkType", "Trade Mark Type")
_DESCRIPTION_PARTICULAR_FEATURE: FieldAliases = (
    "descriptionParticularFeatureOfMark",
    "Description Particular Feature Of Mark",
)
_APPLICATION_DATE: FieldAliases = ("applicationDate", "Application Date")
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

_JOURNAL_DATA: FieldAliases = ("journalData_json", "Journal Data")
_IR_DETAILS: FieldAliases = ("irDetails_json", "IR Details")
_IA_DETAILS: FieldAliases = ("iaDetails_json", "IA Details")
_TRANSFORMATION_DATA: FieldAliases = ("transformationData_json", "Transformation Data")
_TRANSFORMATION_INTO_DATA: FieldAliases = (
    "transformationIntoData_json",
    "Transformation Into Data",
)
_REPLACEMENT_DATA: FieldAliases = ("replacementData_json", "Replacement Data")
_PRIORITY_DATA: FieldAliases = ("priorityData_json", "Priority Data")
_REPLACEMENT_REPLACES_DATA: FieldAliases = (
    "replacementReplacesData_json",
    "Replacement Replaces Data",
)
_MARK_CLAUSES_DATA: FieldAliases = ("markClausesData_json", "Mark Clauses Data")
_MARK_DATA: FieldAliases = ("markData_json", "Mark Data")
_HMG_CASES: FieldAliases = ("hmgCases_json", "HMG Cases")
_OTHER_ENTRIES_DATA: FieldAliases = ("otherEntriesData_json", "Other Entries Data")
_LOGOGRAM_DATA: FieldAliases = ("logogramData_json", "Logogram Data")
_LICENSE_DATA: FieldAliases = ("licenseData_json", "License Data")
_GRANTOR_DATA: FieldAliases = ("grantorData_json", "Grantor Data")
_GRANTEE_DATA: FieldAliases = ("granteeData_json", "Grantee Data")
_SECURITY_INTEREST_DATA: FieldAliases = (
    "securityInterestData_json",
    "Security Interest Data",
)
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
    """Source-native facts for one authoritative IPOS application row."""

    application_number: str
    filing_date: str | None
    international_registration_date: str | None
    singapore_protection_date: str | None
    series_mark_number: str | None
    application_type: str | None
    trade_mark_type: str | None
    description_particular_feature_of_mark: str | None
    application_date: str | None
    mark_status: str
    mark_status_date: str | None
    status_update_date: str | None
    registration_completion_date: str | None
    expiry_date: str | None
    publication_date: str | None
    last_modified_date: str | None
    journal_data: tuple[dict[str, Any], ...]
    international_registration_details: tuple[dict[str, Any], ...]
    ia_details: tuple[dict[str, Any], ...]
    transformation_data: tuple[dict[str, Any], ...]
    transformation_into_data: tuple[dict[str, Any], ...]
    replacement_data: tuple[dict[str, Any], ...]
    priority_data: tuple[dict[str, Any], ...]
    replacement_replaces_data: tuple[dict[str, Any], ...]
    mark_clauses_data: tuple[dict[str, Any], ...]
    mark_data: tuple[dict[str, Any], ...]
    hmg_cases: tuple[dict[str, Any], ...]
    other_entries_data: tuple[dict[str, Any], ...]
    logogram_data: tuple[dict[str, Any], ...]
    license_data: tuple[dict[str, Any], ...]
    grantor_data: tuple[dict[str, Any], ...]
    grantee_data: tuple[dict[str, Any], ...]
    security_interest_data: tuple[dict[str, Any], ...]
    transfer_data: tuple[dict[str, Any], ...]
    documents: tuple[dict[str, Any], ...]
    goods_services: tuple[dict[str, Any], ...]
    priority_claims: tuple[dict[str, Any], ...]
    applicants: tuple[dict[str, Any], ...]
    agents: tuple[dict[str, Any], ...]


def ipos_native_schema_drift(
    fieldnames: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return missing and unknown source fields for an API/schema field list.

    The data.gov.sg datastore row id is provider metadata rather than an IPOS native fact.
    """

    observed = set(fieldnames) - _PROVIDER_METADATA_FIELDS
    expected = set(IPOS_NATIVE_SOURCE_FIELDS)
    missing = tuple(sorted(expected - observed))
    unknown = tuple(sorted(observed - expected))
    return missing, unknown


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
    """Extract authoritative facts without semantic/legal interpretation."""

    return IposNativeApplicationFacts(
        application_number=_required_text(
            row, _APPLICATION_NUMBER, label="Application Number"
        ),
        filing_date=_optional_text(row, _FILING_DATE),
        international_registration_date=_optional_text(
            row, _INTERNATIONAL_REGISTRATION_DATE
        ),
        singapore_protection_date=_optional_text(row, _SINGAPORE_PROTECTION_DATE),
        series_mark_number=_optional_text(row, _SERIES_MARK_NUMBER),
        application_type=_optional_text(row, _APPLICATION_TYPE),
        trade_mark_type=_optional_text(row, _TRADE_MARK_TYPE),
        description_particular_feature_of_mark=_optional_text(
            row, _DESCRIPTION_PARTICULAR_FEATURE
        ),
        application_date=_optional_text(row, _APPLICATION_DATE),
        mark_status=_required_text(row, _MARK_STATUS, label="Mark Status"),
        mark_status_date=_optional_text(row, _MARK_STATUS_DATE),
        status_update_date=_optional_text(row, _STATUS_UPDATE_DATE),
        registration_completion_date=_optional_text(
            row, _REGISTRATION_COMPLETION_DATE
        ),
        expiry_date=_optional_text(row, _EXPIRY_DATE),
        publication_date=_optional_text(row, _PUBLICATION_DATE),
        last_modified_date=_optional_text(row, _LAST_MODIFIED_DATE),
        journal_data=_json_object_array(row, _JOURNAL_DATA, label="Journal Data"),
        international_registration_details=_json_object_array(
            row, _IR_DETAILS, label="IR Details"
        ),
        ia_details=_json_object_array(row, _IA_DETAILS, label="IA Details"),
        transformation_data=_json_object_array(
            row, _TRANSFORMATION_DATA, label="Transformation Data"
        ),
        transformation_into_data=_json_object_array(
            row, _TRANSFORMATION_INTO_DATA, label="Transformation Into Data"
        ),
        replacement_data=_json_object_array(
            row, _REPLACEMENT_DATA, label="Replacement Data"
        ),
        priority_data=_json_object_array(row, _PRIORITY_DATA, label="Priority Data"),
        replacement_replaces_data=_json_object_array(
            row, _REPLACEMENT_REPLACES_DATA, label="Replacement Replaces Data"
        ),
        mark_clauses_data=_json_object_array(
            row, _MARK_CLAUSES_DATA, label="Mark Clauses Data"
        ),
        mark_data=_json_object_array(row, _MARK_DATA, label="Mark Data"),
        hmg_cases=_json_object_array(row, _HMG_CASES, label="HMG Cases"),
        other_entries_data=_json_object_array(
            row, _OTHER_ENTRIES_DATA, label="Other Entries Data"
        ),
        logogram_data=_json_object_array(row, _LOGOGRAM_DATA, label="Logogram Data"),
        license_data=_json_object_array(row, _LICENSE_DATA, label="License Data"),
        grantor_data=_json_object_array(row, _GRANTOR_DATA, label="Grantor Data"),
        grantee_data=_json_object_array(row, _GRANTEE_DATA, label="Grantee Data"),
        security_interest_data=_json_object_array(
            row, _SECURITY_INTEREST_DATA, label="Security Interest Data"
        ),
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
