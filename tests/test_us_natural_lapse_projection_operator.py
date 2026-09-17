from pathlib import Path

import pytest

from app.us import natural_lapse_projection_operator as operator
from app.us.applicant_candidate_backfill_control import USApplicantServingEpoch


class Result:
    def __init__(self, rows):
        self.result_rows = rows


EPOCH = USApplicantServingEpoch(
    bulk_run_id="run-1",
    plan_sha256="a" * 64,
    checkpoint_sequence=310,
    final_audit_version="AUDIT_V1",
)
MANIFEST = "c" * 64
MANIFEST_PAYLOAD = {
    "version": operator.SOURCE_MANIFEST_VERSION,
    "entry_count": 4,
    "package_count": 2,
    "kind_counts": {"CASE": 1, "CLASS": 1, "EVENT": 1, "OWNER": 1},
    "entries": [
        {
            "lineage_kind": "EVENT",
            "source_package_id": "00000000-0000-0000-0000-000000000001",
            "source_package_kind": "DAILY_APPLICATIONS",
            "source_effective_date": "2026-09-11",
            "source_file": "apc260911.xml",
            "min_source_rank": 30,
            "max_source_rank": 30,
            "fact_row_count": 100,
        }
    ],
    "fingerprint": MANIFEST,
}


class EmptyTargetClient:
    def query(self, sql):
        if "system.tables" in sql:
            return Result([])
        if "uniqExact(event_key)" in sql and "us_event_history" in sql:
            return Result([[200, 150, 149, 123, 456, "1983-03-31", "2026-09-11"]])
        raise AssertionError(sql)


def freeze_dependencies(monkeypatch):
    monkeypatch.setattr(
        operator,
        "current_source_manifest",
        lambda epoch, **_kwargs: MANIFEST_PAYLOAD,
    )
    monkeypatch.setattr(operator, "accepted_projection_state", lambda: None)


def test_projection_contract_is_exact_caex_hot_us_and_minimal_table():
    ddl = operator._ddl_path().read_text(encoding="utf-8")
    assert "us_natural_lapse_discovery_current" in ddl
    assert "ORDER BY (\n    lapse_reason,\n    lapse_event_date,\n    nice_class" in ddl
    assert ddl.count("storage_policy = 'hot_us_only'") == 1
    assert "us_natural_lapse_discovery_state" not in ddl
    assert not any(
        token in ddl.upper() for token in ("TRUNCATE", "DROP TABLE", "DELETE FROM", "ALTER TABLE")
    )
    sql = operator.projection_insert_sql(snapshot_id="b" * 64, source_manifest_fingerprint=MANIFEST)
    assert "e.event_code = 'CAEX'" in sql
    assert "e.event_type_code = 'O'" in sql
    assert "CANCELLED SEC. 8 (10-YR)/EXPIRED SECTION 9" in sql
    assert "ARRAY JOIN cls.nice_classes AS nice_class" in sql
    assert "class_lineage_json" in sql and "owner_lineage_json" in sql
    assert "status_code = '900'" not in sql
    assert "TTAB" not in sql


def test_source_identity_is_event_class_serial_bounded():
    sql = operator.source_identity_sql()
    assert "tuple(event_key, nice_class, serial_number)" in sql
    assert "event_code = 'CAEX'" in sql
    assert "event_type_code = 'O'" in sql
    assert "status_code = '900'" not in sql


def test_snapshot_identity_binds_epoch_manifest_and_source_identity():
    source = {
        "rows": 200,
        "event_count": 150,
        "serial_count": 149,
        "checksum_sum": 123,
        "checksum_xor": 456,
        "min_event_date": "1983-03-31",
        "max_event_date": "2026-09-11",
        "checksum_definition": operator.CHECKSUM_DEFINITION,
    }
    first = operator.projection_snapshot_id(
        epoch=EPOCH,
        source_manifest_fingerprint=MANIFEST,
        source=source,
    )
    second = operator.projection_snapshot_id(
        epoch=EPOCH,
        source_manifest_fingerprint="d" * 64,
        source=source,
    )
    assert len(first) == 64
    assert first != second


def test_prepare_plan_freezes_manifest_snapshot_source_and_mutation_scope(
    tmp_path: Path, monkeypatch
):
    freeze_dependencies(monkeypatch)
    output = tmp_path / "plan.json"
    envelope = operator.prepare_plan(
        output,
        client=EmptyTargetClient(),
        epoch_getter=lambda: EPOCH,
        main_sha_getter=lambda: "b" * 40,
    )
    plan = envelope["plan"]
    assert output.exists()
    assert plan["source_epoch"]["token"] == EPOCH.token
    assert plan["source_manifest"] == MANIFEST_PAYLOAD
    assert plan["source_manifest_fingerprint"] == MANIFEST
    assert plan["source_identity"]["rows"] == 200
    assert len(plan["projection_snapshot_id"]) == 64
    assert plan["mutation_scope"]["clickhouse_tables"] == [
        operator.PROJECTION_TABLE,
        "markorbit_facts.schema_version",
    ]
    assert plan["mutation_scope"]["forbidden"] == [
        "ALTER",
        "DELETE",
        "DROP",
        "TRUNCATE",
        "OPTIMIZE",
        "MOVE",
    ]
    assert len(envelope["plan_sha256"]) == 64


def test_execute_requires_exact_go_token(tmp_path: Path, monkeypatch):
    freeze_dependencies(monkeypatch)
    plan_path = tmp_path / "plan.json"
    envelope = operator.prepare_plan(
        plan_path,
        client=EmptyTargetClient(),
        epoch_getter=lambda: EPOCH,
        main_sha_getter=lambda: "b" * 40,
    )
    with pytest.raises(PermissionError, match="exact #700 production authority token"):
        operator.execute_plan(
            plan_path,
            plan_sha=envelope["plan_sha256"],
            authority_token="GO #700 something else",
            receipt_path=tmp_path / "receipt.json",
            client=EmptyTargetClient(),
            epoch_getter=lambda: EPOCH,
            main_sha_getter=lambda: "b" * 40,
        )
