"""Authoritative Singapore IPOS snapshot schema contract.

The downloaded CSV may expose display headings while the public datastore API exposes
field ids. Both representations are normalized to the same 39 source-native fields.
Provider metadata such as datastore `_id` is ignored rather than treated as an IPOS fact.
"""

from __future__ import annotations

from collections.abc import Iterable

from .ipos_sg_native_facts import IPOS_NATIVE_SOURCE_FIELDS


IPOS_NATIVE_CSV_SOURCE_FIELDS: tuple[str, ...] = (
    "Application Number",
    "Filing Date",
    "International Registration Date",
    "Singapore Protection Date",
    "Series Mark Number",
    "Application Type",
    "Trade Mark Type",
    "Description Particular Feature Of Mark",
    "Application Date",
    "Mark Status",
    "Mark Status Date",
    "Status Update Date",
    "Registration Procedure Completion Date",
    "Expiry Date",
    "Publication Date",
    "Last Modified Date",
    "Journal Data",
    "IR Details",
    "IA Details",
    "Transformation Data",
    "Transformation Into Data",
    "Replacement Data",
    "Priority Data",
    "Replacement Replaces Data",
    "Mark Clauses Data",
    "Mark Data",
    "HMG Cases",
    "Other Entries Data",
    "Logogram Data",
    "License Data",
    "Grantor Data",
    "Grantee Data",
    "Security Interest Data",
    "Transfer Data",
    "Documents",
    "Goods And Services Specifications",
    "Priority Claims Details",
    "Current Applicant Proprietor Details",
    "Agent Correspondence Details",
)

_PROVIDER_METADATA_FIELDS = frozenset({"_id"})

_API_TO_CSV = dict(zip(IPOS_NATIVE_SOURCE_FIELDS, IPOS_NATIVE_CSV_SOURCE_FIELDS, strict=True))
_CANONICAL_FIELD_BY_ALIAS = {
    **{field: field for field in IPOS_NATIVE_SOURCE_FIELDS},
    **{csv_field: api_field for api_field, csv_field in _API_TO_CSV.items()},
}


def ipos_snapshot_schema_drift(
    fieldnames: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return missing canonical fields and unknown observed fields for a snapshot schema."""

    observed_canonical: set[str] = set()
    unknown: set[str] = set()

    for fieldname in fieldnames:
        if fieldname in _PROVIDER_METADATA_FIELDS:
            continue
        canonical = _CANONICAL_FIELD_BY_ALIAS.get(fieldname)
        if canonical is None:
            unknown.add(fieldname)
        else:
            observed_canonical.add(canonical)

    missing = set(IPOS_NATIVE_SOURCE_FIELDS) - observed_canonical
    return tuple(sorted(missing)), tuple(sorted(unknown))


def validate_ipos_native_snapshot_schema(fieldnames: Iterable[str]) -> None:
    """Fail closed when the authoritative source schema no longer matches the contract."""

    missing, unknown = ipos_snapshot_schema_drift(fieldnames)
    if not missing and not unknown:
        return

    details: list[str] = []
    if missing:
        details.append(f"missing={','.join(missing)}")
    if unknown:
        details.append(f"unknown={','.join(unknown)}")
    raise ValueError(f"IPOS snapshot schema drift: {'; '.join(details)}")
