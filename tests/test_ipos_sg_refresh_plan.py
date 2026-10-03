from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.snapshot_delta import ipos_sg_refresh_plan as plan


def evidence():
    current_hash = "a" * 64
    state = {
        "state_dir": "F:/state",
        "current_content_hash": current_hash,
        "current_pointer_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "row_count": 878707,
        "snapshot_bytes": 3930339177,
        "schema_hash": "d" * 64,
        "operator_receipt_sha256": "e" * 64,
        "operator_completed_at": "2026-09-29T02:02:23+00:00",
        "audit_status": "READY",
        "retained_full_snapshot_count": 1,
        "orphan_full_snapshot_count": 0,
    }
    source = {
        "dataset_id": "d_6145acb2130bf781165258e76a584383",
        "metadata_checked_at": "2026-10-03T13:51:34+00:00",
        "source_updated_at": "2026-10-02T17:41:21+00:00",
        "dataset_size_bytes": 3936433088,
        "column_metadata_sha256": "f" * 64,
        "metadata_identity_sha256": "1" * 64,
        "source_revision_trusted": False,
        "live_checked_at": "2026-10-03T13:52:02+00:00",
        "live_total_rows": 879477,
        "live_field_names_sha256": "2" * 64,
        "download_url_resolved": False,
    }
    weekly = {"tier": "weekly", "content_hash": current_hash}
    monthly = {"tier": "monthly", "content_hash": current_hash}
    return state, source, weekly, monthly


def make_plan():
    state, source, weekly, monthly = evidence()
    return plan.build_plan(
        execution_main_sha="0" * 40,
        state=state,
        source=source,
        weekly=weekly,
        monthly=monthly,
    )


def test_plan_binds_candidate_and_grants_no_apply_or_schedule():
    result = make_plan()
    assert result["kind"] == plan.PLAN_KIND
    assert result["status"] == "FROZEN_NO_APPLY"
    assert result["source_row_delta_from_current"] == 770
    assert result["operator_dag"] == "IPOS_SG_OPERATOR_DAG_V1"
    assert result["one_shot_refresh_only"] is True
    assert result["cn_serving_pre_post_regression_required"] is True
    assert result["recurring_schedule_enabled"] is False
    assert result["additional_refresh_authorized"] is False
    assert result["source_cleanup_authorized"] is False
    assert result["archive_cleanup_authorized"] is False
    assert result["production_mutation_performed"] is False


def test_plan_rejects_live_row_regression_and_wrong_archive():
    state, source, weekly, monthly = evidence()
    source["live_total_rows"] = state["row_count"] - 1
    with pytest.raises(RuntimeError, match="row count regressed"):
        plan.build_plan(
            execution_main_sha="0" * 40,
            state=state,
            source=source,
            weekly=weekly,
            monthly=monthly,
        )
    state, source, weekly, monthly = evidence()
    monthly["content_hash"] = "9" * 64
    with pytest.raises(RuntimeError, match="archive drift"):
        plan.build_plan(
            execution_main_sha="0" * 40,
            state=state,
            source=source,
            weekly=weekly,
            monthly=monthly,
        )


def test_exact_token_is_required_and_does_not_enable_schedule():
    frozen = make_plan()
    plan_sha = "3" * 64
    token = plan.authority(plan_sha)
    plan.validate_apply_authority(frozen, plan_sha, token)
    assert token == f"GO #849 SG-CORPUS-REFRESH {plan_sha} ONE-SHOT-NO-SCHEDULE"
    with pytest.raises(RuntimeError, match="exact SG"):
        plan.validate_apply_authority(frozen, plan_sha, "GO #849")
    drift = deepcopy(frozen)
    drift["recurring_schedule_enabled"] = True
    with pytest.raises(RuntimeError, match="contract drift"):
        plan.validate_apply_authority(drift, plan_sha, token)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "APPLY"),
        ("additional_refresh_authorized", True),
        ("source_cleanup_authorized", True),
        ("archive_cleanup_authorized", True),
        ("production_mutation_performed", True),
    ],
)
def test_authorization_rejects_plan_contract_drift(field, value):
    frozen = make_plan()
    frozen[field] = value
    plan_sha = "4" * 64
    with pytest.raises(RuntimeError, match="contract drift"):
        plan.validate_apply_authority(frozen, plan_sha, plan.authority(plan_sha))


def test_archive_evidence_reuses_content_verification(tmp_path: Path):
    archive = tmp_path / "weekly"
    objects = archive / "objects"
    points = archive / "points" / "weekly"
    objects.mkdir(parents=True)
    points.mkdir(parents=True)
    body = b"accepted archive bytes"
    content_hash = hashlib.sha256(body).hexdigest()
    (objects / f"{content_hash}.csv").write_bytes(body)
    point_path = points / "2026-W40.json"
    point_path.write_text(
        json.dumps(
            {
                "contract": "IPOS_SG_ACCEPTED_ARCHIVE_V1",
                "tier": "weekly",
                "slot": "2026-W40",
                "content_hash": content_hash,
                "object_reference": f"objects/{content_hash}.csv",
                "source_manifest": {
                    "content_hash": content_hash,
                    "dataset_id": "d_6145acb2130bf781165258e76a584383",
                },
            }
        ),
        encoding="utf-8",
    )

    result = plan.archive_evidence(
        point_path,
        tier="weekly",
        current_hash=content_hash,
        current_bytes=len(body),
    )
    assert result["content_hash"] == content_hash

    (objects / f"{content_hash}.csv").write_bytes(b"x" * len(body))
    with pytest.raises(RuntimeError, match="missing or corrupt"):
        plan.archive_evidence(
            point_path,
            tier="weekly",
            current_hash=content_hash,
            current_bytes=len(body),
        )


def test_accepted_state_rejects_snapshot_hash_drift(tmp_path: Path):
    body = b"accepted current bytes"
    content_hash = hashlib.sha256(body).hexdigest()
    snapshots = tmp_path / "snapshots"
    acceptance = tmp_path / "acceptance"
    snapshots.mkdir()
    acceptance.mkdir()
    reference = f"snapshots/{content_hash}.csv"
    snapshot = tmp_path / reference
    snapshot.write_bytes(body)
    (tmp_path / "current.json").write_text(
        json.dumps({"content_hash": content_hash, "storage_reference": reference}),
        encoding="utf-8",
    )
    (snapshots / f"{content_hash}.manifest.json").write_text(
        json.dumps(
            {
                "content_hash": content_hash,
                "storage_reference": reference,
                "jurisdiction": "SG",
                "source_id": "IPOS_SG_TRADEMARK_APPLICATIONS",
                "dataset_id": "d_6145acb2130bf781165258e76a584383",
                "row_count": 1,
                "schema_hash": "8" * 64,
            }
        ),
        encoding="utf-8",
    )
    (acceptance / "operator_latest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "completed_at": "2026-10-03T13:00:00+00:00",
                "full_corpus": {"content_hash": content_hash, "row_count": 1},
            }
        ),
        encoding="utf-8",
    )

    assert plan.accepted_state(tmp_path)["current_content_hash"] == content_hash
    snapshot.write_bytes(b"x" * len(body))
    with pytest.raises(RuntimeError, match="snapshot hash drift"):
        plan.accepted_state(tmp_path)


def test_live_comparison_rejects_state_source_and_archive_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    frozen = make_plan()
    state, source, _, _ = evidence()
    archives = {
        "weekly": {
            "tier": "weekly",
            "point_path": "F:/weekly/points/weekly/2026-W40.json",
            "point_sha256": "5" * 64,
            "slot": "2026-W40",
            "content_hash": state["current_content_hash"],
            "object_path": "F:/weekly/objects/current.csv",
            "object_bytes": state["snapshot_bytes"],
        },
        "monthly": {
            "tier": "monthly",
            "point_path": "G:/monthly/points/monthly/2026-09.json",
            "point_sha256": "6" * 64,
            "slot": "2026-09",
            "content_hash": state["current_content_hash"],
            "object_path": "G:/monthly/objects/current.csv",
            "object_bytes": state["snapshot_bytes"],
            "restore_receipt_path": "G:/monthly/acceptance/restore.json",
            "restore_receipt_sha256": "7" * 64,
        },
    }
    frozen["pre_refresh_archives"] = deepcopy(archives)
    live_state = deepcopy(state)
    live_source = deepcopy(source)
    live_archives = deepcopy(archives)
    monkeypatch.setattr(plan, "accepted_state", lambda _state_dir: deepcopy(live_state))
    monkeypatch.setattr(plan, "source_observation", lambda: deepcopy(live_source))
    monkeypatch.setattr(
        plan,
        "archive_evidence",
        lambda _point, *, tier, **_kwargs: deepcopy(live_archives[tier]),
    )

    plan._compare_live(frozen, tmp_path)
    live_state["row_count"] += 1
    with pytest.raises(RuntimeError, match="accepted SG current changed"):
        plan._compare_live(frozen, tmp_path)
    live_state["row_count"] -= 1
    live_source["live_total_rows"] += 1
    with pytest.raises(RuntimeError, match="official SG source changed"):
        plan._compare_live(frozen, tmp_path)
    live_source["live_total_rows"] -= 1
    live_archives["weekly"]["object_bytes"] += 1
    with pytest.raises(RuntimeError, match="weekly archive evidence changed"):
        plan._compare_live(frozen, tmp_path)
