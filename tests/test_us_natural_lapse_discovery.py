from datetime import date
import json

import pytest

from app.discovery_contract import DiscoveryContractError, DiscoveryCursorError
from app.us import natural_lapse_discovery as discovery
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch


class Result:
    def __init__(self, names, rows):
        self.column_names = list(names)
        self.result_rows = list(rows)


EPOCH = USApplicantServingEpoch(
    bulk_run_id="run-1",
    plan_sha256="a" * 64,
    checkpoint_sequence=310,
    final_audit_version="US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V1",
)
SNAPSHOT = "b" * 64
MANIFEST = "c" * 64
STATE = {
    "run_id": "projection-run-1",
    "implementation_sha": "d" * 40,
    "plan_sha256": "e" * 64,
    "projection_snapshot_id": SNAPSHOT,
    "source_manifest_fingerprint": MANIFEST,
    "source_epoch": EPOCH.to_dict(),
    "source_identity": {
        "rows": 100,
        "min_event_date": "1983-03-31",
        "max_event_date": "2026-09-11",
    },
    "projection_identity": {"rows": 100},
    "completed_at": "2026-09-17T00:00:00Z",
}

ROW_NAMES = [
    "lapse_event_key",
    "serial_number",
    "registration_number",
    "mark_identification",
    "mark_drawing_code",
    "nice_class",
    "owner_names",
    "observed_status_code",
    "observed_status_date",
    "cancellation_date",
    "renewal_date",
    "lapse_event_date",
    "event_code",
    "event_type_code",
    "description_text",
    "event_source_package_kind",
    "event_source_effective_date",
    "event_source_file",
    "event_source_row_hash",
    "event_source_package_id",
    "event_source_rank",
    "event_observed_at",
    "case_source_package_kind",
    "case_source_effective_date",
    "case_source_file",
    "case_source_row_hash",
    "case_source_package_id",
    "case_record_hash",
    "case_source_rank",
    "class_lineage_json",
    "owner_lineage_json",
    "source_manifest_fingerprint",
    "projection_snapshot_id",
    "legal_conclusion",
]


def row(event_key: str, event_date: date, nice_class: int = 9):
    return [
        event_key,
        "90000001",
        "1234567",
        "EXAMPLE",
        "4",
        nice_class,
        ["Example Owner LLC"],
        "900",
        event_date,
        event_date,
        None,
        event_date,
        "CAEX",
        "O",
        "CANCELLED SEC. 8 (10-YR)/EXPIRED SECTION 9",
        "DAILY_APPLICATIONS",
        date(2026, 9, 11),
        "event-source.xml",
        "f" * 64,
        "00000000-0000-0000-0000-000000000001",
        30,
        "2026-09-11T00:00:00Z",
        "DAILY_APPLICATIONS",
        date(2026, 9, 11),
        "case-source.xml",
        "1" * 64,
        "00000000-0000-0000-0000-000000000002",
        "2" * 64,
        31,
        json.dumps([["class-key", "DAILY_APPLICATIONS", "2026-09-11", "class.xml", "3" * 64]]),
        json.dumps([["owner-key", "DAILY_APPLICATIONS", "2026-09-11", "owner.xml", "4" * 64]]),
        MANIFEST,
        SNAPSHOT,
        0,
    ]


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.sql = []

    def query(self, sql, *, settings=None):
        self.sql.append((sql, settings))
        return Result(ROW_NAMES, self.rows)


def request(**overrides):
    values = {
        "lapse_reason": discovery.ADMITTED_REASON,
        "event_date_start": date(2026, 1, 1),
        "event_date_end": date(2026, 2, 1),
        "nice_class": None,
        "serial_number_start": None,
        "serial_number_end": None,
        "page_size": 1,
        "cursor": None,
    }
    values.update(overrides)
    return discovery.NaturalLapseDiscoveryRequest(**values)


def execute(req, client, **kwargs):
    return discovery.execute_page(
        req,
        client=client,
        serving_epoch_getter=kwargs.pop("serving_epoch_getter", lambda: EPOCH),
        state_getter=kwargs.pop("state_getter", lambda: dict(STATE)),
        engine_version_value="M1.7",
        **kwargs,
    )


def test_request_rejects_unsupported_reason_unbounded_window_and_partial_serial_range():
    with pytest.raises(DiscoveryContractError, match="unsupported natural-lapse reason"):
        request(lapse_reason="GENERIC_DEAD")
    with pytest.raises(DiscoveryContractError, match="exceeds 366 days"):
        request(event_date_end=date(2027, 1, 3))
    with pytest.raises(DiscoveryContractError, match="provided together"):
        request(serial_number_start="90000000")


def test_page_sql_uses_projection_primary_scope_and_optional_bounds():
    sql = discovery.build_page_sql(
        request(nice_class=9, serial_number_start="90000000", serial_number_end="91000000"),
        snapshot_id=SNAPSHOT,
    )
    assert discovery.PROJECTION_TABLE in sql
    assert "us_event_history" not in sql
    assert "lapse_reason = 'REGISTRATION_MAINTENANCE_LAPSE'" in sql
    assert "nice_class = 9" in sql
    assert "serial_number >= '90000000'" in sql
    assert "lapse_event_date >=" in sql and "lapse_event_date <" in sql
    assert f"projection_snapshot_id = '{SNAPSHOT}'" in sql


def test_page_sql_does_not_require_class_when_omitted():
    sql = discovery.build_page_sql(request(), snapshot_id=SNAPSHOT)
    assert "nice_class =" not in sql
    assert "ORDER BY lapse_event_date ASC, nice_class ASC" in sql


def test_execute_page_returns_source_fact_lineage_snapshot_and_cursor():
    client = FakeClient(rows=[row("1" * 64, date(2026, 1, 2)), row("2" * 64, date(2026, 1, 3), 42)])
    page = execute(request(), client)
    record = page["results"][0]
    assert page["result_state"] == "RESULTS"
    assert page["next_cursor"]
    assert record["lapse_reason"] == discovery.ADMITTED_REASON
    assert record["lifecycle_event"]["event_code"] == "CAEX"
    assert record["legal_conclusion"] is False
    assert record["source"]["source_fingerprint_sha256"] == "sha256:" + "f" * 64
    assert record["serving"]["source_manifest_fingerprint"] == MANIFEST
    assert record["serving"]["projection_snapshot_id"] == SNAPSHOT
    assert record["class_lineage"] and record["owner_lineage"]


def test_projection_unavailable_is_not_empty():
    with pytest.raises(discovery.NaturalLapseUnavailable, match="no accepted state"):
        execute(request(), FakeClient(), state_getter=lambda: None)


def test_cursor_query_mismatch_fails_closed():
    first = execute(
        request(),
        FakeClient(rows=[row("1" * 64, date(2026, 1, 2)), row("2" * 64, date(2026, 1, 3))]),
    )
    with pytest.raises(DiscoveryCursorError, match="cursor/query mismatch"):
        execute(
            request(nice_class=42, cursor=first["next_cursor"]),
            FakeClient(),
        )


def test_serving_epoch_drift_fails_closed():
    newer = USApplicantServingEpoch(
        bulk_run_id="run-2",
        plan_sha256="9" * 64,
        checkpoint_sequence=311,
        final_audit_version="US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V1",
    )
    epochs = iter([EPOCH, newer])
    with pytest.raises(discovery.NaturalLapseUnavailable, match="changed during"):
        execute(
            request(),
            FakeClient(rows=[row("1" * 64, date(2026, 1, 2))]),
            serving_epoch_getter=lambda: next(epochs),
        )


def test_projection_snapshot_drift_fails_closed():
    states = iter([dict(STATE), {**STATE, "projection_snapshot_id": "9" * 64}])
    with pytest.raises(discovery.NaturalLapseUnavailable, match="snapshot changed"):
        execute(
            request(),
            FakeClient(rows=[row("1" * 64, date(2026, 1, 2))]),
            state_getter=lambda: next(states),
        )


def test_wrong_source_event_mapping_and_legal_conclusion_are_rejected():
    bad = row("1" * 64, date(2026, 1, 2))
    bad[12] = "TTAB"
    with pytest.raises(DiscoveryContractError, match="outside the admitted source-event mapping"):
        execute(request(), FakeClient(rows=[bad]))
    bad_legal = row("2" * 64, date(2026, 1, 2))
    bad_legal[-1] = 1
    with pytest.raises(DiscoveryContractError, match="legal conclusion"):
        execute(request(), FakeClient(rows=[bad_legal]))


def test_empty_and_not_covered_states_are_distinct():
    empty = execute(request(), FakeClient(rows=[]))
    assert empty["result_state"] == "EMPTY"
    not_covered = execute(
        request(event_date_start=date(1970, 1, 1), event_date_end=date(1970, 2, 1)),
        FakeClient(rows=[]),
    )
    assert not_covered["result_state"] == "NOT_COVERED"
