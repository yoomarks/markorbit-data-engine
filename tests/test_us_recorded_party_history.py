from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.us.recorded_party_history import (
    READY_VERSION,
    RecordedPartyHistoryInvalid,
    RecordedPartyHistoryRequest,
    RecordedPartyHistoryUnavailable,
    execute_page,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    def query(self, sql, *, settings):
        self.queries.append((sql, settings))
        return next(self.responses)


def _result(columns, rows):
    return SimpleNamespace(column_names=columns, result_rows=rows)


def _ready():
    return _result(
        [
            "ready_version",
            "assignment_max_rank",
            "ttab_max_rank",
            "implementation_sha",
            "accepted_at",
        ],
        [(READY_VERSION, 100, 200, "a" * 40, datetime(2026, 9, 18))],
    )


def test_recorded_party_history_reads_exact_name_without_identity_claim():
    client = FakeClient(
        [
            _ready(),
            _result(
                [
                    "source_domain",
                    "relationship_type",
                    "party_name",
                    "party_side",
                    "source_role",
                    "serial_number",
                    "registration_number",
                    "resource_type",
                    "resource_id",
                    "event_date",
                    "first_observed_at",
                    "last_observed_at",
                    "latest_source_rank",
                    "party_key",
                ],
                [
                    (
                        "US_ASSIGNMENT",
                        "ASSIGNEE",
                        "Example Holdings LLC",
                        "",
                        "",
                        "90123456",
                        "7654321",
                        "ASSIGNMENT",
                        "1234-5678",
                        "2025-01-02",
                        datetime(2025, 1, 2),
                        datetime(2025, 1, 3),
                        99,
                        "b" * 64,
                    )
                ],
            ),
        ]
    )
    page = execute_page(
        RecordedPartyHistoryRequest(
            name="  Example   Holdings LLC  ",
            source_domain="US_ASSIGNMENT",
            page_size=10,
        ),
        client=client,
        runtime_engine_version="M1.7",
    )
    assert page["normalized_name"] == "example holdings llc"
    assert page["results"][0]["serial_number"] == "90123456"
    assert page["results"][0]["identity_resolution_claimed"] is False
    assert page["results"][0]["review_required"] is True
    sql = client.queries[1][0]
    assert "normalized_name = 'example holdings llc'" in sql
    assert "source_domain = 'US_ASSIGNMENT'" in sql
    assert "source_rank <= 100" in sql
    assert "source_rank <= 200" in sql


def test_recorded_party_history_fails_closed_until_ready():
    client = FakeClient(
        [
            _result(
                [
                    "ready_version",
                    "assignment_max_rank",
                    "ttab_max_rank",
                    "implementation_sha",
                    "accepted_at",
                ],
                [],
            )
        ]
    )
    with pytest.raises(RecordedPartyHistoryUnavailable, match="not backfilled"):
        execute_page(RecordedPartyHistoryRequest(name="Example"), client=client)


def test_recorded_party_history_validates_request():
    with pytest.raises(RecordedPartyHistoryInvalid):
        RecordedPartyHistoryRequest(name="")
    with pytest.raises(RecordedPartyHistoryInvalid):
        RecordedPartyHistoryRequest(name="Example", source_domain="OTHER")
    with pytest.raises(RecordedPartyHistoryInvalid):
        RecordedPartyHistoryRequest(name="Example", page_size=0)


def test_recorded_party_history_schema_is_name_first_and_incremental():
    sql = Path(
        "database/clickhouse/init/021_us_recorded_party_history.sql"
    ).read_text(encoding="utf-8")
    assert "us_recorded_party_relationship_event" in sql
    assert "normalized_name," in sql
    assert "us_assignment_recorded_party_relationship_mv" in sql
    assert "FROM markorbit_facts.us_assignment_property_history AS prop" in sql
    assert "us_ttab_recorded_party_relationship_mv" in sql
    assert "FROM markorbit_facts.us_ttab_property_history AS prop" in sql
    assert "US_RECORDED_PARTY_HISTORY_SCHEMA_V1" in sql
