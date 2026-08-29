from __future__ import annotations

from datetime import date

import pytest

from app.cn.discovery_preliminary_publication import (
    CANDIDATE_TYPE,
    MAX_INTERVAL_DAYS,
    MAX_PAGE_SIZE,
    PreliminaryPublicationDiscoveryRequest,
    build_page_sql,
    execute_page,
)
from app.cn.research_filing_to_prelim_duration import ServingEpoch
from app.discovery_contract import DiscoveryContractError, DiscoveryCursorError


COLUMNS = [
    "case_id",
    "application_number",
    "mark_name_raw",
    "classes",
    "filing_date",
    "prelim_pub_date",
    "prelim_pub_issue",
    "source_effective_date",
    "source_package_id",
    "source_row_hash",
    "record_hash",
    "source_rank",
]


class FakeResult:
    def __init__(self, rows):
        self.column_names = COLUMNS
        self.result_rows = rows


class ScriptedClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.sql: list[str] = []

    def query(self, sql: str):
        self.sql.append(sql)
        if not self.batches:
            raise AssertionError("unexpected query")
        return FakeResult(self.batches.pop(0))


def epoch(sequence: int = 85) -> ServingEpoch:
    return ServingEpoch(
        coverage_date=date(2026, 7, 31),
        max_success_sequence=sequence,
        success_count=85,
    )


def stable_epoch_getter():
    return epoch()


def row(case_id: str, app: str, prelim: str, *, rank: int = 85):
    return (
        case_id,
        app,
        f"MARK-{app}",
        [9, 35],
        date(2025, 1, 2),
        date.fromisoformat(prelim),
        "1900",
        date(2026, 7, 31),
        "11111111-1111-1111-1111-111111111111",
        f"source-{app}",
        f"record-{app}",
        rank,
    )


def request(*, page_size: int = 2, cursor: str | None = None):
    return PreliminaryPublicationDiscoveryRequest(
        start_date=date(2026, 7, 1),
        end_date=date(2026, 8, 1),
        page_size=page_size,
        cursor=cursor,
    )


def test_request_freezes_exact_fact_only_scope():
    value = request()
    identity = value.query_identity

    assert identity["candidate_type"] == CANDIDATE_TYPE
    assert identity["scope"]["jurisdiction"] == "CN"
    assert identity["scope"]["ranking"] == "NONE"
    assert identity["scope"]["joins"] == "NONE"
    assert identity["scope"]["prelim_pub_date"] == {
        "start_inclusive": "2026-07-01",
        "end_exclusive": "2026-08-01",
    }
    assert identity["limits"] == {
        "page_size": 2,
        "max_pages": 10,
        "max_results": 1000,
    }


def test_request_rejects_invalid_interval_and_page_size():
    with pytest.raises(DiscoveryContractError, match="positive"):
        PreliminaryPublicationDiscoveryRequest(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        )

    with pytest.raises(DiscoveryContractError, match=str(MAX_INTERVAL_DAYS)):
        PreliminaryPublicationDiscoveryRequest(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 2),
        )

    with pytest.raises(DiscoveryContractError, match=str(MAX_PAGE_SIZE)):
        PreliminaryPublicationDiscoveryRequest(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            page_size=101,
        )


def test_sql_is_keyset_only_and_bounded():
    sql = build_page_sql(
        request(),
        position=["2026-07-10", "A002", "00000000-0000-0000-0000-000000000002"],
    )

    assert "markorbit_facts.cn_case_current FINAL" in sql
    assert "prelim_pub_date >= toDate32('2026-07-01')" in sql
    assert "prelim_pub_date < toDate32('2026-08-01')" in sql
    assert "prelim_pub_date > toDate32('2026-07-10')" in sql
    assert "application_number > 'A002'" in sql
    assert "toString(case_id) > '00000000-0000-0000-0000-000000000002'" in sql
    assert "ORDER BY prelim_pub_date ASC, application_number ASC, toString(case_id) ASC" in sql
    assert "LIMIT 3" in sql
    assert "OFFSET" not in sql.upper()
    assert "JOIN" not in sql.upper()


def test_page1_page2_continuation_is_stable_without_duplicates():
    first_client = ScriptedClient(
        [[
            row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000002", "A002", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000003", "A003", "2026-07-11"),
        ]]
    )
    first = execute_page(
        request(),
        client=first_client,
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    assert [item["application_number"] for item in first["results"]] == ["A001", "A002"]
    assert first["next_cursor"] is not None
    assert first["provenance"]["page_number"] == 1
    assert first["provenance"]["emitted_count"] == 2
    assert first["bounded_truncation"] is False

    second_client = ScriptedClient(
        [[
            row("00000000-0000-0000-0000-000000000003", "A003", "2026-07-11"),
            row("00000000-0000-0000-0000-000000000004", "A004", "2026-07-12"),
        ]]
    )
    second = execute_page(
        request(cursor=first["next_cursor"]),
        client=second_client,
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    assert [item["application_number"] for item in second["results"]] == ["A003", "A004"]
    assert second["next_cursor"] is None
    assert second["provenance"]["page_number"] == 2
    assert second["provenance"]["emitted_count"] == 4
    assert "A002" in second_client.sql[0]
    assert "00000000-0000-0000-0000-000000000002" in second_client.sql[0]


def test_same_page_replay_is_deterministic():
    batch = [
        row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
        row("00000000-0000-0000-0000-000000000002", "A002", "2026-07-10"),
        row("00000000-0000-0000-0000-000000000003", "A003", "2026-07-11"),
    ]
    left = execute_page(
        request(),
        client=ScriptedClient([batch]),
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )
    right = execute_page(
        request(),
        client=ScriptedClient([batch]),
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    assert left == right


def test_cursor_rejects_changed_query_scope():
    first = execute_page(
        request(),
        client=ScriptedClient([[
            row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000002", "A002", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000003", "A003", "2026-07-11"),
        ]]),
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    changed = PreliminaryPublicationDiscoveryRequest(
        start_date=date(2026, 7, 2),
        end_date=date(2026, 8, 1),
        page_size=2,
        cursor=first["next_cursor"],
    )
    with pytest.raises(DiscoveryCursorError, match="cursor/query mismatch"):
        execute_page(
            changed,
            client=ScriptedClient([[]]),
            serving_epoch_getter=stable_epoch_getter,
            engine_version="git:test",
        )


def test_cursor_rejects_changed_snapshot_before_query():
    first = execute_page(
        request(),
        client=ScriptedClient([[
            row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000002", "A002", "2026-07-10"),
            row("00000000-0000-0000-0000-000000000003", "A003", "2026-07-11"),
        ]]),
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    client = ScriptedClient([[]])
    with pytest.raises(DiscoveryCursorError, match="cursor/snapshot mismatch"):
        execute_page(
            request(cursor=first["next_cursor"]),
            client=client,
            serving_epoch_getter=lambda: epoch(86),
            engine_version="git:test",
        )
    assert client.sql == []


def test_epoch_drift_during_page_fails_closed():
    epochs = iter([epoch(85), epoch(86)])
    client = ScriptedClient([[
        row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
    ]])

    with pytest.raises(DiscoveryContractError, match="epoch changed"):
        execute_page(
            request(),
            client=client,
            serving_epoch_getter=lambda: next(epochs),
            engine_version="git:test",
        )


def test_candidate_output_carries_exact_row_lineage():
    result = execute_page(
        request(),
        client=ScriptedClient([[
            row("00000000-0000-0000-0000-000000000001", "A001", "2026-07-10"),
        ]]),
        serving_epoch_getter=stable_epoch_getter,
        engine_version="git:test",
    )

    candidate = result["results"][0]
    assert candidate["candidate_type"] == CANDIDATE_TYPE
    assert candidate["source_package_id"] == "11111111-1111-1111-1111-111111111111"
    assert candidate["source_row_hash"] == "source-A001"
    assert candidate["record_hash"] == "record-A001"
    assert candidate["source_rank"] == 85
    assert result["snapshot"]["snapshot_id"].startswith("cn-serving-epoch:")
