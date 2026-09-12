from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from app.cn.text import normalized_match_text
from app.us.applicant_candidate_index import applicant_candidate_key, canonical_identity_text

APPLICANT_NAME_LOOKUP_SCHEMA_VERSION = "APPLICANT_NAME_LOOKUP_V1"
CN_APPLICANT_NAME_LOOKUP_TABLE = "markorbit_facts.cn_applicant_name_lookup_current"
US_APPLICANT_NAME_LOOKUP_TABLE = "markorbit_facts.us_applicant_name_lookup_current"
CN_APPLICANT_ROLES = frozenset({"OWNER", "CO_OWNER"})

CN_APPLICANT_NAME_LOOKUP_COLUMNS: tuple[str, ...] = (
    "normalized_name",
    "entity_id",
    "application_number",
    "relation_key",
    "source_row_hash",
    "record_hash",
    "source_rank",
    "ingested_at",
    "is_deleted",
)

US_APPLICANT_NAME_LOOKUP_COLUMNS: tuple[str, ...] = (
    "normalized_name",
    "candidate_key",
    "serial_number",
    "owner_key",
    "source_row_hash",
    "record_hash",
    "source_rank",
    "ingested_at",
    "is_deleted",
)


def cn_applicant_name_lookup_row(source: Mapping[str, Any]) -> list[Any] | None:
    """Build one CN lookup binding, excluding rows without a stable entity anchor."""
    if str(source.get("role") or "").strip().upper() not in CN_APPLICANT_ROLES:
        return None
    normalized_name = normalized_match_text(source.get("normalized_name") or source.get("raw_name"))
    entity_id = source.get("entity_id")
    if not normalized_name or entity_id in (None, ""):
        return None
    try:
        stable_entity_id = uuid.UUID(str(entity_id))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("CN Applicant lookup requires a valid entity_id") from error
    return [
        normalized_name,
        stable_entity_id,
        source.get("application_number"),
        source.get("relation_key"),
        source.get("source_row_hash"),
        source.get("record_hash"),
        source.get("source_rank"),
        source.get("ingested_at"),
        source.get("is_deleted", 0),
    ]


def us_applicant_name_lookup_row(source: Mapping[str, Any]) -> list[Any]:
    """Build one US lookup binding without collapsing distinct frozen identities."""
    normalized_name = canonical_identity_text(source.get("party_name_norm"))
    if not normalized_name:
        raise ValueError("US Applicant lookup requires party_name_norm")
    return [
        normalized_name,
        applicant_candidate_key(source),
        source.get("serial_number"),
        source.get("owner_key"),
        source.get("source_row_hash"),
        source.get("record_hash"),
        source.get("source_rank"),
        source.get("ingested_at"),
        source.get("is_deleted", 0),
    ]
