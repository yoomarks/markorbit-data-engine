from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.us.ttab_correspondent_history import (
    READY_VERSION,
    TTABCorrespondentHistoryInvalid,
    TTABCorrespondentHistoryRequest,
    TTABCorrespondentHistoryUnavailable,
    TARGET_TABLE,
    execute_page,
)
from app.us_ttab.model import (
    TTABPartyRecord,
    TTABProceedingBundle,
    TTABProceedingRecord,
    TTABPropertyRecord,
)
from app.us_ttab.publisher import TTABBatchPublisher, correspondent_mark_rows


class FakeQueryClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    def query(self, sql, *, settings):
        self.queries.append((sql, settings))
        return next(self.responses)


class FakePublisherClient:
    def __init__(self):
        self.inserts = []

    def insert(self, table, rows, *, column_names):
        self.inserts.append((table, list(rows), list(column_names)))


def _result(columns, rows):
    return SimpleNamespace(column_names=columns, result_rows=rows)


def _ready():
    return _result(
        [
            "ready_version",
            "accepted_source_max_rank",
            "accepted_serving_generation",
            "source_joined_rows",
            "target_rows",
            "target_relationship_count",
            "implementation_sha",
            "accepted_at",
        ],
        [
            (
                READY_VERSION,
                4070700000000002992,
                1,
                2_070_969,
                2_070_969,
                1_577_684,
                "a" * 40,
                datetime(2026, 9, 21, tzinfo=timezone.utc),
            )
        ],
    )


def _bundle():
    return TTABProceedingBundle(
        proceeding=TTABProceedingRecord(
            proceeding_number="91234567",
            interlocutory_attorney="USPTO STAFF ATTORNEY",
        ),
        parties=(
            TTABPartyRecord(
                proceeding_number="91234567",
                side="P",
                ordinal=1,
                party_name="Example Party LLC",
                party_id="P1",
                correspondent_name="  Jane   Q. Counsel ",
                correspondent_organization="Example Firm LLP",
            ),
        ),
        properties=(
            TTABPropertyRecord(
                proceeding_number="91234567",
                party_side="P",
                party_ordinal=1,
                ordinal=1,
                serial_number="90123456",
                registration_number="7654321",
                mark_text="EXAMPLE",
                source_property_id="PROP1",
            ),
        ),
    )


def test_correspondent_projection_uses_party_correspondent_not_staff_attorney():
    package_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    rows = correspondent_mark_rows(
        _bundle(),
        package_id=package_id,
        source_kind="TTAB_DAILY",
        source_snapshot_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source_file="ttab.xml",
        source_rank=123,
        serving_generation=1,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row[2] == "jane q. counsel"
    assert row[3].strip() == "Jane   Q. Counsel"
    assert row[4] == "Example Firm LLP"
    assert row[8] == "Example Party LLC"
    assert row[9] == ""
    assert row[12] == "SERIAL:90123456"
    assert row[13] == "90123456"
    assert "USPTO STAFF ATTORNEY" not in [str(value) for value in row]


def test_publisher_only_dual_writes_correspondent_history_when_enabled():
    client = FakePublisherClient()
    publisher = TTABBatchPublisher(
        client,
        package_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        source_kind="TTAB_DAILY",
        source_snapshot_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source_rank=456,
        include_correspondent_history=True,
        correspondent_serving_generation=2,
        batch_size=1,
    )
    publisher.add(_bundle(), "ttab.xml")
    counts = publisher.close()
    assert counts[TARGET_TABLE] == 1
    target_inserts = [item for item in client.inserts if item[0] == TARGET_TABLE]
    assert len(target_inserts) == 1
    assert len(target_inserts[0][1]) == 1


def test_correspondent_history_exact_name_page_preserves_provenance():
    client = FakeQueryClient(
        [
            _ready(),
            _result(["count()"], [(1,)]),
            _result(
                [
                    "serving_generation",
                    "source_max_rank",
                    "source_package_id",
                    "updated_at",
                ],
                [
                    (
                        1,
                        4070700000000002992,
                        "00000000-0000-0000-0000-000000000000",
                        datetime(2026, 9, 21, tzinfo=timezone.utc),
                    )
                ],
            ),
            _result(
                [
                    "relationship_key",
                    "mark_identity",
                    "correspondent_name",
                    "correspondent_organization",
                    "proceeding_number",
                    "party_side",
                    "party_ordinal",
                    "party_name",
                    "party_role",
                    "serial_number",
                    "registration_number",
                    "mark_text",
                    "first_observed_at",
                    "last_observed_at",
                    "first_source_rank",
                    "latest_source_rank",
                    "latest_serving_generation",
                    "observation_count",
                    "latest_source_kind",
                    "latest_source_file",
                    "latest_source_package_id",
                ],
                [
                    (
                        "b" * 64,
                        "SERIAL:90123456",
                        "Jane Q. Counsel",
                        "Example Firm LLP",
                        "91234567",
                        "P",
                        1,
                        "Example Party LLC",
                        "PLAINTIFF",
                        "90123456",
                        "7654321",
                        "EXAMPLE",
                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                        datetime(2026, 1, 1, tzinfo=timezone.utc),
                        10,
                        99,
                        1,
                        4,
                        "TTAB_DAILY",
                        "ttab.xml",
                        "33333333-3333-3333-3333-333333333333",
                    )
                ],
            ),
        ]
    )
    page = execute_page(
        TTABCorrespondentHistoryRequest(
            name="  Jane   Q. Counsel ",
            page_size=10,
        ),
        client=client,
        runtime_engine_version="M1.7",
    )
    assert page["normalized_name"] == "jane q. counsel"
    result = page["results"][0]
    assert result["party_name"] == "Example Party LLC"
    assert result["party_role"] == "PLAINTIFF"
    assert result["serial_number"] == "90123456"
    assert result["latest_serving_generation"] == 1
    assert result["latest_source_package_id"].startswith("33333333")
    assert result["identity_resolution_claimed"] is False
    assert result["continuing_representation_claimed"] is False
    assert result["legal_conclusion"] is False
    assert "EXCLUDES_TTAB_INTERLOCUTORY_STAFF_ATTORNEY" in page["semantics"]
    sql = client.queries[-1][0]
    assert "normalized_name = 'jane q. counsel'" in sql
    assert "serving_generation <= 1" in sql
    assert "ORDER BY proceeding_number, mark_identity, relationship_key" in sql


def test_correspondent_history_fails_closed_until_ready():
    client = FakeQueryClient(
        [
            _result(
                [
                    "ready_version",
                    "accepted_source_max_rank",
                    "accepted_serving_generation",
                    "source_joined_rows",
                    "target_rows",
                    "target_relationship_count",
                    "implementation_sha",
                    "accepted_at",
                ],
                [],
            )
        ]
    )
    with pytest.raises(TTABCorrespondentHistoryUnavailable, match="not backfilled"):
        execute_page(TTABCorrespondentHistoryRequest(name="Example"), client=client)


def test_correspondent_history_request_and_schema_contract():
    with pytest.raises(TTABCorrespondentHistoryInvalid):
        TTABCorrespondentHistoryRequest(name="")
    with pytest.raises(TTABCorrespondentHistoryInvalid):
        TTABCorrespondentHistoryRequest(name="Example", page_size=0)

    sql = Path(
        "database/clickhouse/init/025_us_ttab_correspondent_mark_history.sql"
    ).read_text(encoding="utf-8")
    assert "us_ttab_correspondent_mark_history" in sql
    assert "ReplacingMergeTree(source_rank)" in sql
    assert "ORDER BY (normalized_name, serving_generation, observation_key)" in sql
    assert "storage_policy = 'hot_us_only'" in sql
    assert "us_ttab_correspondent_mark_history_watermark" in sql
    assert "serving_generation UInt64" in sql
    assert "US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA_V1" in sql
    assert READY_VERSION not in sql
