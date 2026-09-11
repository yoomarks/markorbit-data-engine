from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

APPLICANT_INDEX_TABLE = "markorbit_facts.us_applicant_candidate_current"
APPLICANT_INDEX_SCHEMA_VERSION = "US_OWNER_READ_V1"
APPLICANT_SOURCE_VERSION = "US_APPLICANT_OWNER_READ_V1"
APPLICANT_SOURCE_PREFIX = "US_APPLICANT:"
APPLICANT_CANDIDATE_PREFIX = "us:applicant:"

IDENTITY_FIELDS: tuple[str, ...] = (
    "party_name_norm",
    "party_type",
    "legal_entity_type_code",
    "entity_statement",
    "nationality_country",
    "nationality_state",
    "nationality_other",
    "address_1",
    "address_2",
    "city",
    "state",
    "country",
    "postcode",
    "dba_aka_text",
    "composed_of_statement",
)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value or "")


def canonical_identity_text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", _text(value)).split()).casefold()


def canonical_owner_identity(owner: Mapping[str, Any]) -> dict[str, str]:
    return {field: canonical_identity_text(owner.get(field)) for field in IDENTITY_FIELDS}


def applicant_candidate_key(owner: Mapping[str, Any]) -> str:
    material = {
        "schema": APPLICANT_INDEX_SCHEMA_VERSION,
        "jurisdiction": "US",
        "identity": canonical_owner_identity(owner),
    }
    payload = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def applicant_candidate_id(candidate_key: str) -> str:
    return f"{APPLICANT_CANDIDATE_PREFIX}{candidate_key}"


def applicant_source_id(candidate_key: str) -> str:
    return f"{APPLICANT_SOURCE_PREFIX}{candidate_key}"


def candidate_key_from_source_id(source_id: str) -> str | None:
    if not source_id.startswith(APPLICANT_SOURCE_PREFIX):
        return None
    key = source_id[len(APPLICANT_SOURCE_PREFIX):]
    if len(key) != 64 or any(ch not in "0123456789abcdef" for ch in key):
        return None
    return key


def owner_mapping(row: Sequence[Any], columns: Sequence[str]) -> dict[str, Any]:
    if len(row) != len(columns):
        raise ValueError("owner row/column length mismatch")
    return dict(zip(columns, row, strict=True))


def applicant_index_row(row: Sequence[Any], owner_columns: Sequence[str]) -> list[Any]:
    owner = owner_mapping(row, owner_columns)
    return [applicant_candidate_key(owner), *row]
