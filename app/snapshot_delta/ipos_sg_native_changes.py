"""Neutral source-family change detection for Singapore IPOS native facts.

This layer decomposes an already-detected application update into exact source-backed
families. It does not infer legal meaning or emit legal/semantic event types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ipos_sg_native_facts import IposNativeApplicationFacts


IPOS_NATIVE_FAMILY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "application",
        ("filing_date", "application_type", "application_date"),
    ),
    (
        "status",
        (
            "mark_status",
            "mark_status_date",
            "status_update_date",
            "registration_completion_date",
            "expiry_date",
            "publication_date",
        ),
    ),
    ("source_metadata", ("last_modified_date",)),
    ("journal", ("journal_data",)),
    (
        "international",
        (
            "international_registration_date",
            "singapore_protection_date",
            "international_registration_details",
            "ia_details",
        ),
    ),
    (
        "mark",
        (
            "series_mark_number",
            "trade_mark_type",
            "description_particular_feature_of_mark",
            "mark_clauses_data",
            "mark_data",
            "logogram_data",
        ),
    ),
    (
        "transformation",
        ("transformation_data", "transformation_into_data"),
    ),
    (
        "replacement",
        ("replacement_data", "replacement_replaces_data"),
    ),
    ("priority", ("priority_data", "priority_claims")),
    ("cases", ("hmg_cases",)),
    ("other_entries", ("other_entries_data",)),
    ("license", ("license_data",)),
    (
        "security_interest",
        ("grantor_data", "grantee_data", "security_interest_data"),
    ),
    ("transfer", ("transfer_data",)),
    ("documents", ("documents",)),
    ("goods_services", ("goods_services",)),
    ("applicants", ("applicants",)),
    ("agents", ("agents",)),
)


@dataclass(frozen=True)
class IposNativeFamilyChange:
    """One exact source-family difference for a single IPOS application."""

    application_number: str
    family: str
    changed_fields: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]


def native_family_payloads(
    facts: IposNativeApplicationFacts,
) -> dict[str, dict[str, Any]]:
    """Project native facts into deterministic, interpretation-free source families."""

    return {
        family: {field: getattr(facts, field) for field in fields}
        for family, fields in IPOS_NATIVE_FAMILY_FIELDS
    }


def diff_ipos_native_families(
    previous: IposNativeApplicationFacts,
    current: IposNativeApplicationFacts,
) -> tuple[IposNativeFamilyChange, ...]:
    """Return deterministic source-family changes for the same application identity."""

    if previous.application_number != current.application_number:
        raise ValueError("cannot compare IPOS native facts with different application numbers")

    previous_payloads = native_family_payloads(previous)
    current_payloads = native_family_payloads(current)
    changes: list[IposNativeFamilyChange] = []

    for family, fields in IPOS_NATIVE_FAMILY_FIELDS:
        before = previous_payloads[family]
        after = current_payloads[family]
        changed_fields = tuple(field for field in fields if before[field] != after[field])
        if changed_fields:
            changes.append(
                IposNativeFamilyChange(
                    application_number=current.application_number,
                    family=family,
                    changed_fields=changed_fields,
                    before=before,
                    after=after,
                )
            )

    return tuple(changes)
