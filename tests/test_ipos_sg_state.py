import json
from pathlib import Path

from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.ipos_sg_state import audit_ipos_state


def write_ready_state(state: Path, content_hash: str = "a" * 64) -> Path:
    snapshots = state / "snapshots"
    snapshots.mkdir(parents=True)
    snapshot = snapshots / f"{content_hash}.csv"
    snapshot.write_text("Application Number,Mark Status\nSG1,Pending\n", encoding="utf-8")
    storage_reference = f"snapshots/{content_hash}.csv"
    (snapshots / f"{content_hash}.manifest.json").write_text(
        json.dumps(
            {
                "jurisdiction": "SG",
                "source_id": IPOS_SG_TRADEMARK_APPLICATIONS.source_id,
                "dataset_id": IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id,
                "content_hash": content_hash,
                "storage_reference": storage_reference,
                "row_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (state / "current.json").write_text(
        json.dumps(
            {
                "content_hash": content_hash,
                "storage_reference": storage_reference,
            }
        ),
        encoding="utf-8",
    )
    return snapshot


def test_empty_state_is_safe_for_bootstrap(tmp_path: Path):
    audit = audit_ipos_state(tmp_path)

    assert audit.status == "EMPTY"
    assert audit.safe_to_run is True
    assert audit.current_content_hash is None
    assert audit.retained_full_snapshot_count == 0
    assert audit.issues == ()


def test_ready_state_requires_canonical_pointer_manifest_and_snapshot(tmp_path: Path):
    write_ready_state(tmp_path)

    audit = audit_ipos_state(tmp_path)

    assert audit.status == "READY"
    assert audit.safe_to_run is True
    assert audit.current_content_hash == "a" * 64
    assert audit.retained_full_snapshot_count == 1
    assert audit.orphan_full_snapshot_count == 0


def test_superseded_snapshot_is_recoverable_not_silently_clean(tmp_path: Path):
    write_ready_state(tmp_path)
    orphan = tmp_path / "snapshots" / f"{'b' * 64}.csv"
    orphan.write_text("orphan", encoding="utf-8")

    audit = audit_ipos_state(tmp_path)

    assert audit.status == "RECOVERABLE"
    assert audit.safe_to_run is True
    assert audit.orphan_full_snapshot_count == 1
    assert [issue.code for issue in audit.issues] == [
        "SUPERSEDED_FULL_SNAPSHOT_CLEANUP_PENDING"
    ]


def test_transient_part_is_recoverable_and_observable(tmp_path: Path):
    write_ready_state(tmp_path)
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / ".IPOSTradeMarkApplications.csv.part").write_text("partial", encoding="utf-8")

    audit = audit_ipos_state(tmp_path)

    assert audit.status == "RECOVERABLE"
    assert audit.safe_to_run is True
    assert audit.transient_part_paths == (
        "incoming/.IPOSTradeMarkApplications.csv.part",
    )
    assert [issue.code for issue in audit.issues] == ["TRANSIENT_PART_FILES_PRESENT"]


def test_invalid_current_pointer_blocks_operator_run(tmp_path: Path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "current.json").write_text(
        json.dumps(
            {
                "content_hash": "not-a-hash",
                "storage_reference": "../../outside.csv",
            }
        ),
        encoding="utf-8",
    )

    audit = audit_ipos_state(tmp_path)

    assert audit.status == "BLOCKED"
    assert audit.safe_to_run is False
    assert [issue.code for issue in audit.issues] == ["CURRENT_STATE_INTEGRITY_FAILURE"]


def test_orphan_snapshot_without_pointer_is_explicitly_recoverable(tmp_path: Path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / f"{'c' * 64}.csv").write_text("orphan", encoding="utf-8")

    audit = audit_ipos_state(tmp_path)

    assert audit.status == "RECOVERABLE"
    assert audit.safe_to_run is True
    assert audit.current_content_hash is None
    assert audit.orphan_full_snapshot_count == 1
    assert [issue.code for issue in audit.issues] == [
        "ORPHAN_SNAPSHOT_WITHOUT_CURRENT_POINTER"
    ]
