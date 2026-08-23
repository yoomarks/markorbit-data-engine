from datetime import datetime, timezone
import hashlib

from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from app.snapshot_delta.loader import SnapshotCsvLoader
from app.snapshot_delta.manifest import build_snapshot_manifest


def test_build_snapshot_manifest_from_csv(tmp_path):
    snapshot = tmp_path / "IPOSTradeMarkApplications.csv"
    payload = "\ufeffApplication No,Mark Name,Status\nSG1,ALPHA,Pending\nSG2,BETA,Registered\n"
    snapshot.write_text(payload, encoding="utf-8")
    loader = SnapshotCsvLoader(snapshot)
    retrieved_at = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)

    manifest = build_snapshot_manifest(
        loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=retrieved_at,
        source_uri="https://data.gov.sg/ipos/trademarks",
        storage_reference="snapshot://sg/2026-08-23",
    )

    assert loader.fieldnames() == ("Application No", "Mark Name", "Status")
    assert manifest.jurisdiction == "SG"
    assert manifest.source_id == "IPOS_SG_TRADEMARK_APPLICATIONS"
    assert manifest.dataset_id == "d_6145acb2130bf781165258e76a584383"
    assert manifest.row_count == 2
    assert manifest.retrieved_at == retrieved_at
    assert manifest.content_hash == hashlib.sha256(snapshot.read_bytes()).hexdigest()


def test_manifest_hashes_are_stable_for_identical_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot.csv"
    snapshot.write_text("id,status\n1,PENDING\n", encoding="utf-8")
    loader = SnapshotCsvLoader(snapshot)
    retrieved_at = datetime(2026, 8, 23, tzinfo=timezone.utc)

    first = build_snapshot_manifest(
        loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=retrieved_at,
        source_uri="source",
        storage_reference="storage",
    )
    second = build_snapshot_manifest(
        loader,
        IPOS_SG_TRADEMARK_APPLICATIONS,
        jurisdiction="SG",
        retrieved_at=retrieved_at,
        source_uri="source",
        storage_reference="storage",
    )

    assert first.schema_hash == second.schema_hash
    assert first.content_hash == second.content_hash
    assert first.row_count == second.row_count
