from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from app.applicant_name_lookup import (
    APPLICANT_NAME_LOOKUP_SCHEMA_VERSION,
    CN_APPLICANT_NAME_LOOKUP_COLUMNS,
    US_APPLICANT_NAME_LOOKUP_COLUMNS,
    cn_applicant_name_lookup_row,
    us_applicant_name_lookup_row,
)


def _us_owner(**overrides: object) -> dict[str, object]:
    owner: dict[str, object] = {
        "party_name_norm": "Acme Holdings LLC",
        "party_type": "16",
        "legal_entity_type_code": "03",
        "entity_statement": "",
        "nationality_country": "US",
        "nationality_state": "DE",
        "nationality_other": "",
        "address_1": "1 Main St",
        "address_2": "",
        "city": "Wilmington",
        "state": "DE",
        "country": "US",
        "postcode": "19801",
        "dba_aka_text": "",
        "composed_of_statement": "",
        "serial_number": "90000001",
        "owner_key": "a" * 64,
        "source_row_hash": "b" * 64,
        "record_hash": "c" * 64,
        "source_rank": 1,
        "ingested_at": "2026-09-13 00:00:00.000",
        "is_deleted": 0,
    }
    owner.update(overrides)
    return owner


def test_lookup_schema_is_physically_keyed_by_normalized_name() -> None:
    sql = (
        Path(__file__).parents[1]
        / "database"
        / "clickhouse"
        / "init"
        / "014_applicant_name_lookup.sql"
    ).read_text(encoding="utf-8")

    assert "ORDER BY (normalized_name, entity_id, application_number, relation_key)" in sql
    assert "ORDER BY (normalized_name, candidate_key, serial_number, owner_key)" in sql
    assert "ingested_at DateTime64(3, 'UTC') DEFAULT now64(3)" in sql
    assert APPLICANT_NAME_LOOKUP_SCHEMA_VERSION in sql


def test_cn_lookup_reuses_canonical_normalization_and_stable_entity_anchor() -> None:
    entity_id = uuid.UUID("8f50ed78-7666-4cab-8a0c-f91c89de0ca7")
    row = cn_applicant_name_lookup_row(
        {
            "raw_name": " 北京 示例科技（有限）公司 ",
            "role": "OWNER",
            "entity_id": entity_id,
            "application_number": "CN-1",
            "relation_key": "a" * 64,
            "source_row_hash": "b" * 64,
            "record_hash": "c" * 64,
            "source_rank": 7,
            "ingested_at": "2026-09-13 00:00:00.000",
        }
    )

    assert row is not None
    assert dict(zip(CN_APPLICANT_NAME_LOOKUP_COLUMNS, row, strict=True))["normalized_name"] == (
        "北京示例科技有限公司"
    )
    assert row[1] == entity_id


def test_cn_lookup_never_fabricates_a_candidate_without_entity_id() -> None:
    assert (
        cn_applicant_name_lookup_row(
            {"normalized_name": "示例公司", "entity_id": None, "role": "OWNER"}
        )
        is None
    )
    assert (
        cn_applicant_name_lookup_row(
            {
                "normalized_name": "示例代理所",
                "entity_id": "8f50ed78-7666-4cab-8a0c-f91c89de0ca7",
                "role": "AGENT",
            }
        )
        is None
    )
    with pytest.raises(ValueError, match="valid entity_id"):
        cn_applicant_name_lookup_row(
            {
                "normalized_name": "示例公司",
                "entity_id": "not-an-entity-id",
                "role": "OWNER",
            }
        )


def test_us_same_name_keeps_distinct_frozen_identity_candidates() -> None:
    first = us_applicant_name_lookup_row(_us_owner())
    second = us_applicant_name_lookup_row(_us_owner(address_1="2 Main St", owner_key="d" * 64))
    first_by_name = dict(zip(US_APPLICANT_NAME_LOOKUP_COLUMNS, first, strict=True))
    second_by_name = dict(zip(US_APPLICANT_NAME_LOOKUP_COLUMNS, second, strict=True))

    assert first_by_name["normalized_name"] == second_by_name["normalized_name"]
    assert first_by_name["candidate_key"] != second_by_name["candidate_key"]


def test_us_lookup_rejects_an_empty_normalized_name() -> None:
    with pytest.raises(ValueError, match="party_name_norm"):
        us_applicant_name_lookup_row(_us_owner(party_name_norm="  "))
