from pathlib import Path

from app.storage_audit import (
    PARTY_RELATION_ACTIONS_SQL,
    READ_ONLY_QUERIES,
    build_storage_audit,
)


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self):
        self.queries: list[str] = []

    def query(self, sql: str):
        self.queries.append(sql)
        if "FROM system.parts" in sql:
            return _Result(
                [
                    ("cn_goods_item_current", 1, 4, 100, 1024),
                    ("cn_goods_item_current", 0, 2, 20, 512),
                    ("cn_stage_goods", 1, 1, 10, 256),
                ]
            )
        if "cn_goods_item_observation" in sql:
            return _Result(
                [
                    ("FIRST_OBSERVED", 100),
                    ("REOBSERVED", 80),
                    ("STATUS_CHANGED", 5),
                ]
            )
        if "cn_observed_event" in sql:
            return _Result(
                [
                    ("APPLICATION_OBSERVED", 50, 50, 0),
                    ("PRELIMINARY_PUBLICATION_OBSERVED", 20, 18, 2),
                    ("OWNER_RELATION_OBSERVED", 10, 10, 0),
                ]
            )
        if "cn_case_party_relation_history" in sql:
            return _Result(
                [
                    ("OBSERVED_CURRENT", 120),
                    ("SUPERSEDED", 7),
                ]
            )
        raise AssertionError(sql)


def test_storage_audit_query_set_is_select_only():
    for sql in READ_ONLY_QUERIES:
        normalized = sql.lstrip().upper()
        assert normalized.startswith("SELECT")
        assert "ALTER TABLE" not in normalized
        assert "DELETE WHERE" not in normalized
        assert "OPTIMIZE TABLE" not in normalized
        assert "TRUNCATE TABLE" not in normalized
        assert "INSERT INTO" not in normalized


def test_party_history_audit_uses_physical_action_column():
    schema = Path("database/clickhouse/init/001_fact_schema.sql").read_text(
        encoding="utf-8"
    )

    assert "    action LowCardinality(String)," in schema
    assert "SELECT\n    action," in PARTY_RELATION_ACTIONS_SQL
    assert "GROUP BY action" in PARTY_RELATION_ACTIONS_SQL
    assert "relation_action" not in PARTY_RELATION_ACTIONS_SQL


def test_physical_audit_does_not_scan_fact_history():
    client = _Client()
    report = build_storage_audit(client=client)

    assert report["read_only"] is True
    assert report["mode"] == "physical"
    assert report["physical"]["active_bytes"] == 1280
    assert report["physical"]["inactive_bytes"] == 512
    assert report["physical"]["active_stage_bytes"] == 256
    assert len(client.queries) == 1
    assert "FROM system.parts" in client.queries[0]


def test_deep_audit_quantifies_legacy_noop_and_baseline_history():
    client = _Client()
    report = build_storage_audit(deep=True, client=client)

    goods = report["cn_goods_item_observation"]
    assert goods["first_observed_rows"] == 100
    assert goods["reobserved_rows"] == 80
    assert goods["policy"] == "FIRST_SOURCE_ON_CURRENT_PLUS_TRUE_DELTA_HISTORY"

    events = report["cn_observed_event"]
    assert events["reconstructible_baseline_candidate_rows"] == 68
    prelim = next(
        row
        for row in events["event_profile"]
        if row["event_type"] == "PRELIMINARY_PUBLICATION_OBSERVED"
    )
    assert prelim["empty_old_value_rows"] == 18
    assert prelim["prior_value_rows"] == 2

    party = report["cn_case_party_relation_history"]
    assert party["observed_current_rows"] == 120
    assert party["policy"] == "UNCHANGED_OBSERVED_CURRENT_IS_NO_OP_IN_STORAGE_V2"
    assert len(client.queries) == 4


def test_powershell_wrapper_does_not_start_persistent_worker():
    source = Path("scripts/audit-storage.ps1").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "docker compose run --rm --no-deps" in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose up -d worker" not in lowered
    assert "app.storage_audit" in source
