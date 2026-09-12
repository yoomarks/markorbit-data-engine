from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cn.applicant_name_lookup_backfill_control import CNApplicantServingEpoch
from app.cn.applicant_name_lookup_backfill_operator import (
    execute_backfill_plan,
    load_backfill_plan,
    prepare_backfill_plan,
)


class Result:
    def __init__(self, rows):
        self.result_rows = rows


class SchemaClient:
    def query(self, sql, *, settings=None):
        if "FROM system.tables" in sql:
            return Result(
                [
                    (
                        "cn_applicant_name_lookup_current",
                        "1" * 36,
                        "ReplacingMergeTree",
                        "normalized_name, entity_id, application_number, relation_key",
                    ),
                    (
                        "cn_applicant_name_lookup_from_case_party_mv",
                        "2" * 36,
                        "MaterializedView",
                        "",
                    ),
                    (
                        "cn_case_party_current",
                        "3" * 36,
                        "ReplacingMergeTree",
                        "application_number, role, relation_key",
                    ),
                ]
            )
        if "schema_version" in sql:
            return Result([("APPLICANT_NAME_LOOKUP_V1",)])
        raise AssertionError(sql)


def epoch():
    return CNApplicantServingEpoch("2026-09-01", 42, 40)


def plan(tmp_path: Path):
    path = tmp_path / "plan.json"
    envelope = prepare_backfill_plan(
        path, client=SchemaClient(), epoch_getter=epoch, main_sha_getter=lambda: "a" * 40
    )
    return path, envelope


def test_prepare_is_deterministic_and_load_rejects_wrong_sha(tmp_path):
    path, envelope = plan(tmp_path)
    assert load_backfill_plan(path, envelope["plan_sha256"])["source_epoch"] == epoch().to_dict()
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        load_backfill_plan(path, "0" * 64)


def test_execute_requires_explicit_authority_before_receipt(tmp_path):
    path, envelope = plan(tmp_path)
    with pytest.raises(PermissionError, match="explicit production mutation authorization"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=False,
        )


def test_execute_rejects_sha_or_epoch_drift_before_start(tmp_path):
    path, envelope = plan(tmp_path)
    started = []
    with pytest.raises(RuntimeError, match="main SHA drifted"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=tmp_path / "receipt.json",
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "b" * 40,
            start_fn=lambda **kwargs: started.append(kwargs) or "run",
        )
    assert started == []


def test_execute_writes_exact_success_receipt(tmp_path):
    path, envelope = plan(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    result = execute_backfill_plan(
        path,
        plan_sha=envelope["plan_sha256"],
        receipt_path=receipt_path,
        production_mutation_authorized=True,
        client=SchemaClient(),
        epoch_getter=epoch,
        main_sha_getter=lambda: "a" * 40,
        start_fn=lambda **kwargs: "run",
        execute_fn=lambda *args, **kwargs: {
            "source_epoch": epoch().to_dict(),
            "cursor": {"emitted": 2},
            "completeness": {"complete": True},
        },
    )
    assert result["status"] == "SUCCESS"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == result


def test_partial_completeness_writes_failure_receipt(tmp_path):
    path, envelope = plan(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError, match="incomplete evidence"):
        execute_backfill_plan(
            path,
            plan_sha=envelope["plan_sha256"],
            receipt_path=receipt_path,
            production_mutation_authorized=True,
            client=SchemaClient(),
            epoch_getter=epoch,
            main_sha_getter=lambda: "a" * 40,
            start_fn=lambda **kwargs: "run",
            execute_fn=lambda *args, **kwargs: {
                "source_epoch": epoch().to_dict(),
                "cursor": {"emitted": 1},
                "completeness": {"complete": False},
            },
        )
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["status"] == "FAILED"
