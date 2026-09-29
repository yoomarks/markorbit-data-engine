"""SG weekly restore points and separately located monthly backup tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.snapshot_delta import ipos_sg_snapshot_archive as archive
from app.snapshot_delta.ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def accepted(state: Path, content: bytes = b"SG,verified corpus\n") -> str:
    content_hash = hashlib.sha256(content).hexdigest()
    ref = f"snapshots/{content_hash}.csv"
    snapshot = state / ref
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    for prior in snapshot.parent.glob("*.csv"):
        prior.unlink()
    snapshot.write_bytes(content)
    manifest = {
        "jurisdiction": "SG",
        "dataset_id": IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id,
        "source_id": IPOS_SG_TRADEMARK_APPLICATIONS.source_id,
        "source_uri": IPOS_SG_TRADEMARK_APPLICATIONS.dataset_url,
        "content_hash": content_hash,
        "schema_hash": "b" * 64,
        "row_count": 1,
        "retrieved_at": NOW.isoformat(),
        "storage_reference": ref,
    }
    _json(state / "current.json", {"content_hash": content_hash, "storage_reference": ref})
    _json(state / "snapshots" / f"{content_hash}.manifest.json", manifest)
    _json(
        state / "acceptance" / "operator_latest.json",
        {
            "status": "PASS",
            "full_corpus": {
                "status": "CHANGED",
                "content_hash": content_hash,
                "schema_hash": manifest["schema_hash"],
                "row_count": manifest["row_count"],
            },
            "state_after": {
                "status": "READY",
                "current_content_hash": content_hash,
                "retained_full_snapshot_count": 1,
            },
        },
    )
    return content_hash


def test_weekly_creates_real_restore_object_without_altering_current(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    digest = accepted(state)
    original_pointer = (state / "current.json").read_bytes()

    point = archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    assert point["slot"] == "2026-W40"
    assert point["content_hash"] == digest
    assert archive.verify_archive_point(weekly, "weekly", "2026-W40") == point
    assert (weekly / "objects" / f"{digest}.csv").read_bytes() == b"SG,verified corpus\n"
    assert (state / "current.json").read_bytes() == original_pointer
    assert not (state / ".operator.lock").exists()


def test_same_week_idempotent_and_next_week_reuses_one_physical_object(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    accepted(state)
    before = archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    repeated = archive.archive_accepted_current(
        state, weekly, tier="weekly", now=NOW + timedelta(hours=1)
    )
    assert repeated == before
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW + timedelta(days=7))
    assert len(list((weekly / "points" / "weekly").glob("*.json"))) == 2
    assert len(list((weekly / "objects").glob("*.csv"))) == 1


def test_weekly_retention_four_points_and_unreferenced_content_gc(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    observed = []
    for week in range(6):
        digest = accepted(state, f"SG version {week}\n".encode())
        point = archive.archive_accepted_current(
            state, weekly, tier="weekly", now=NOW + timedelta(days=week * 7)
        )
        observed.append((point["slot"], digest))
    assert sorted(p.stem for p in (weekly / "points" / "weekly").glob("*.json")) == [
        item[0] for item in observed[-4:]
    ]
    assert len(list((weekly / "objects").glob("*.csv"))) == 4
    assert not (weekly / "objects" / f"{observed[0][1]}.csv").exists()
    assert (state / "snapshots" / f"{observed[-1][1]}.csv").exists()


def test_monthly_requires_independent_volume_even_if_different_path(tmp_path: Path):
    state, monthly = tmp_path / "active", tmp_path / "backup"
    state.mkdir()
    monthly.mkdir()
    accepted(state)
    with pytest.raises(archive.IposArchiveError, match="independent filesystem"):
        archive.archive_accepted_current(state, monthly, tier="monthly", now=NOW)
    assert not list(monthly.iterdir())


def test_monthly_copy_and_three_month_retention_on_simulated_independent_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, monthly = tmp_path / "active", tmp_path / "separate-approved-volume"
    state.mkdir()
    monthly.mkdir()
    # Test substitutes ONLY filesystem topology; production guard always checks real st_dev.
    monkeypatch.setattr(archive, "_guard_roots", lambda *_args: None)
    observed = []
    for month in range(1, 6):
        digest = accepted(state, f"SG monthly version {month}".encode())
        point = archive.archive_accepted_current(
            state,
            monthly,
            tier="monthly",
            now=datetime(2026, month, 27, 14, tzinfo=timezone.utc),
        )
        observed.append((point["slot"], digest))
    assert len(list((monthly / "points" / "monthly").glob("*.json"))) == 3
    assert len(list((monthly / "objects").glob("*.csv"))) == 3
    assert not (monthly / "objects" / f"{observed[0][1]}.csv").exists()
    assert (
        archive.verify_archive_point(monthly, "monthly", observed[-1][0])["content_hash"]
        == observed[-1][1]
    )


def test_corrupt_source_and_receipt_cannot_create_restore_point(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    digest = accepted(state)
    (state / "snapshots" / f"{digest}.csv").write_bytes(b"CORRUPT")
    with pytest.raises(archive.IposArchiveError, match="SHA-256"):
        archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    assert not list(weekly.iterdir())
    accepted(state)
    report_path = state / "acceptance" / "operator_latest.json"
    receipt = json.loads(report_path.read_text(encoding="utf-8"))
    receipt["status"] = "FAILED"
    _json(report_path, receipt)
    with pytest.raises(archive.IposArchiveError, match="operator receipt"):
        archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    assert not (state / ".operator.lock").exists()


def test_same_slot_new_version_is_immutable_revision_with_verified_restore(tmp_path: Path):
    state, weekly, scratch = tmp_path / "active", tmp_path / "weekly", tmp_path / "scratch"
    for directory in (state, weekly, scratch):
        directory.mkdir()
    first = accepted(state)
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    second = accepted(state, b"changed corpus")
    revision = archive.archive_accepted_current(
        state, weekly, tier="weekly", now=NOW + timedelta(hours=2)
    )
    assert revision["content_hash"] == second
    assert archive.verify_archive_point(weekly, "weekly", "2026-W40")["content_hash"] == first
    assert (
        archive.verify_archive_point(weekly, "weekly", "2026-W40", revision_sha256=second)
        == revision
    )
    assert len(list((weekly / "points" / "weekly").glob("*.json"))) == 2
    assert (
        archive.restore_archive_point(
            weekly, "weekly", "2026-W40", scratch / "old.csv"
        ).read_bytes()
        == b"SG,verified corpus\n"
    )
    assert (
        archive.restore_archive_point(
            weekly, "weekly", "2026-W40", scratch / "new.csv", revision_sha256=second
        ).read_bytes()
        == b"changed corpus"
    )
    assert (
        archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW + timedelta(hours=3))
        == revision
    )


def test_archive_object_tampering_fails_closed_without_replacing_evidence(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    digest = accepted(state)
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    object_path = weekly / "objects" / f"{digest}.csv"
    object_path.write_bytes(b"tamper")
    with pytest.raises(archive.IposArchiveError, match="corrupt"):
        archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW + timedelta(hours=1))
    assert object_path.read_bytes() == b"tamper"


def test_archive_never_runs_over_active_lifecycle_lock(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    accepted(state)
    with archive.ipos_operator_lease(state):
        with pytest.raises(Exception, match="leased|lock|already"):
            archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    assert not list(weekly.iterdir())


def test_archive_root_must_not_be_nested_inside_active_state(tmp_path: Path):
    state = tmp_path / "active"
    archive_root = state / "archive"
    archive_root.mkdir(parents=True)
    accepted(state)
    with pytest.raises(archive.IposArchiveError, match="outside active"):
        archive.archive_accepted_current(state, archive_root, tier="weekly", now=NOW)


def test_monthly_archive_can_restore_verified_bytes_to_new_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, monthly, scratch = (
        tmp_path / "active",
        tmp_path / "operator-approved-other-volume",
        tmp_path / "scratch",
    )
    for path in (state, monthly, scratch):
        path.mkdir()
    # Simulate approved independent filesystem only in a test fixture.
    monkeypatch.setattr(archive, "_guard_roots", lambda *_args: None)
    digest = accepted(state, b"monthly-restore-test-corpus")
    point = archive.archive_accepted_current(state, monthly, tier="monthly", now=NOW)
    restored = archive.restore_archive_point(
        monthly, "monthly", point["slot"], scratch / "restored.csv"
    )
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == digest
    assert restored.read_bytes() == b"monthly-restore-test-corpus"
    assert (state / "current.json").is_file()
    with pytest.raises(archive.IposArchiveError, match="new file"):
        archive.restore_archive_point(monthly, "monthly", point["slot"], restored)


def test_monthly_reused_content_keeps_three_logical_points_and_one_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, monthly = tmp_path / "active", tmp_path / "monthly"
    state.mkdir()
    monthly.mkdir()
    monkeypatch.setattr(archive, "_guard_roots", lambda *_args: None)
    accepted(state)
    for month in range(1, 6):
        archive.archive_accepted_current(
            state,
            monthly,
            tier="monthly",
            now=datetime(2026, month, 27, 12, tzinfo=timezone.utc),
        )
    assert len(list((monthly / "points" / "monthly").glob("*.json"))) == 3
    assert len(list((monthly / "objects").glob("*.csv"))) == 1


def test_tampered_source_or_archive_does_not_overwrite_active_current(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    original = accepted(state)
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    pointer = (state / "current.json").read_bytes()
    point = (weekly / "points" / "weekly" / "2026-W40.json").read_bytes()
    (weekly / "objects" / f"{original}.csv").write_bytes(b"unexpected")
    with pytest.raises(archive.IposArchiveError, match="corrupt"):
        archive.verify_archive_point(weekly, "weekly", "2026-W40")
    assert (state / "current.json").read_bytes() == pointer
    assert (weekly / "points" / "weekly" / "2026-W40.json").read_bytes() == point


def test_revision_retention_counts_calendar_slots_and_only_deletes_unreferenced(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    first = accepted(state, b"week-zero-old")
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    revised = accepted(state, b"week-zero-revised")
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    kept = {}
    for week in range(1, 5):
        original = accepted(state, f"week-{week}-first".encode())
        point = archive.archive_accepted_current(
            state, weekly, tier="weekly", now=NOW + timedelta(weeks=week)
        )
        kept[point["slot"]] = original
        if week == 2:
            second = accepted(state, b"week-two-revised")
            archive.archive_accepted_current(
                state, weekly, tier="weekly", now=NOW + timedelta(weeks=week, hours=1)
            )
            kept["week-two-revised"] = second
    points = list((weekly / "points" / "weekly").glob("*.json"))
    assert len(points) == 5  # Four calendar slots, two versions in week 2.
    assert not (weekly / "objects" / f"{first}.csv").exists()
    assert not (weekly / "objects" / f"{revised}.csv").exists()
    assert not (weekly / "points" / "weekly" / "2026-W40.json").exists()
    assert (
        archive.verify_archive_point(
            weekly, "weekly", "2026-W42", revision_sha256=kept["week-two-revised"]
        )["content_hash"]
        == kept["week-two-revised"]
    )
    assert len(list((weekly / "objects").glob("*.csv"))) == 5


def test_same_month_revision_is_independent_and_restorable(tmp_path: Path, monkeypatch):
    state, monthly, scratch = tmp_path / "active", tmp_path / "monthly", tmp_path / "scratch"
    for directory in (state, monthly, scratch):
        directory.mkdir()
    monkeypatch.setattr(archive, "_guard_roots", lambda *_args: None)
    first = accepted(state, b"first-in-month")
    archive.archive_accepted_current(state, monthly, tier="monthly", now=NOW)
    second = accepted(state, b"second-in-month")
    archive.archive_accepted_current(state, monthly, tier="monthly", now=NOW)
    assert archive.verify_archive_point(monthly, "monthly", "2026-09")["content_hash"] == first
    assert (
        archive.restore_archive_point(
            monthly, "monthly", "2026-09", scratch / "new.csv", revision_sha256=second
        ).read_bytes()
        == b"second-in-month"
    )
    assert (
        archive.restore_archive_point(
            monthly, "monthly", "2026-09", scratch / "old.csv"
        ).read_bytes()
        == b"first-in-month"
    )
    with pytest.raises(archive.IposArchiveError, match="Revision"):
        archive.verify_archive_point(monthly, "monthly", "2026-09", revision_sha256="../")


def test_tampered_revision_manifest_blocks_retention_and_preserves_current(tmp_path: Path):
    state, weekly = tmp_path / "active", tmp_path / "weekly"
    state.mkdir()
    weekly.mkdir()
    accepted(state, b"first-version")
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    revised = accepted(state, b"revised-version")
    archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW)
    broken = weekly / "points" / "weekly" / f"2026-W40--{revised}.json"
    point = json.loads(broken.read_text())
    point["content_hash"] = "0" * 64
    _json(broken, point)
    accepted(state, b"following-week")
    pointer = (state / "current.json").read_bytes()
    with pytest.raises(archive.IposArchiveError, match="Invalid existing archive point"):
        archive.archive_accepted_current(state, weekly, tier="weekly", now=NOW + timedelta(days=7))
    assert (state / "current.json").read_bytes() == pointer
    assert broken.is_file()
