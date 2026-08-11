from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.us_assignment import ASSIGNMENT_SCHEMA_VERSION
from app.us_assignment.corpus_manifest import MANIFEST_VERSION, preflight_manifest
from app.us_assignment import corpus_replay


def _xml(reel: str) -> str:
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<trademark-assignments><assignment-information><assignment-entry>
<assignment><reel-no>{reel}</reel-no><frame-no>0001</frame-no><date-recorded>20260801</date-recorded></assignment>
<assignors><assignor><name>Alpha LLC</name></assignor></assignors>
<assignees><assignee><name>Beta Inc.</name></assignee></assignees>
<properties><property><serial-number>88990001</serial-number></property></properties>
</assignment-entry></assignment-information></trademark-assignments>"""


def _write_manifest(raw_root: Path, sources: list[dict], *, daily_count: int, daily_through: str | None) -> Path:
    path = raw_root / "manifests" / "us_assignment" / "corpus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "expected_snapshot_packages": 1,
                "expected_daily_packages": daily_count,
                "daily_through": daily_through,
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_manifest_preflight_survives_incoming_to_archive_move(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_assignment"
    archive = tmp_path / "archive" / "us_assignment"
    incoming.mkdir(parents=True)
    archive.mkdir(parents=True)
    snapshot = incoming / "snapshot.xml"
    daily = incoming / "daily.xml"
    snapshot.write_text(_xml("1001"), encoding="utf-8")
    daily.write_text(_xml("1002"), encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_assignment/snapshot.xml",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
                "effective_date": "2026-05-13",
            },
            {
                "path": "incoming/us_assignment/daily.xml",
                "source_kind": "DAILY_ASSIGNMENT_XML",
                "effective_date": "2026-05-14",
            },
        ],
        daily_count=1,
        daily_through="2026-05-14",
    )
    first = preflight_manifest(manifest, tmp_path)
    assert first["status"] == "READY"
    assert first["effective_date_inferred_from_filename"] is False
    assert first["calendar_gap_inference"] is False

    snapshot.rename(archive / snapshot.name)
    second = preflight_manifest(manifest, tmp_path)
    assert second["status"] == "READY"
    assert second["plan"][0]["path"].endswith("archive/us_assignment/snapshot.xml")


def test_manifest_rejects_daily_at_or_before_snapshot(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_assignment"
    incoming.mkdir(parents=True)
    (incoming / "snapshot.xml").write_text(_xml("2001"), encoding="utf-8")
    (incoming / "daily.xml").write_text(_xml("2002"), encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_assignment/snapshot.xml",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
                "effective_date": "2026-05-13",
            },
            {
                "path": "incoming/us_assignment/daily.xml",
                "source_kind": "DAILY_ASSIGNMENT_XML",
                "effective_date": "2026-05-12",
            },
        ],
        daily_count=1,
        daily_through="2026-05-12",
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert "DAILY_NOT_AFTER_HISTORICAL_SNAPSHOT" in {
        item["type"] for item in report["issues"]
    }


def test_manifest_requires_explicit_effective_date(tmp_path: Path):
    path = _write_manifest(
        tmp_path,
        [{"path": "incoming/us_assignment/asb260809.zip", "source_kind": "ASSIGNMENT_SNAPSHOT_XML"}],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(path, tmp_path)
    assert report["status"] == "NOT_READY"
    assert report["issues"][0]["type"] == "MANIFEST_INVALID"
    assert "never inferred from the filename" in report["issues"][0]["error"]


def test_manifest_path_cannot_escape_raw_root(tmp_path: Path):
    outside = tmp_path.parent / "outside.xml"
    outside.write_text(_xml("3001"), encoding="utf-8")
    path = _write_manifest(
        tmp_path,
        [
            {
                "path": "../outside.xml",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
                "effective_date": "2026-05-13",
            }
        ],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(path, tmp_path)
    assert report["status"] == "NOT_READY"
    assert any("RAW_DATA_PATH" in item.get("error", "") for item in report["issues"])


def _plan_item(name: str, digest: str, effective: str, kind: str = "DAILY_ASSIGNMENT_XML") -> dict:
    return {
        "path": f"/data/raw/incoming/us_assignment/{name}",
        "manifest_path": f"incoming/us_assignment/{name}",
        "file_name": name,
        "source_kind": kind,
        "effective_date": effective,
        "sha256": digest,
        "size_bytes": 1,
        "xml_members": [name.replace(".zip", ".xml")],
    }


def test_replay_blocks_success_after_unfinished_prefix(monkeypatch: pytest.MonkeyPatch):
    preflight = {
        "safe": True,
        "plan": [
            _plan_item("snapshot.zip", "a" * 64, "2026-05-13", "ASSIGNMENT_SNAPSHOT_XML"),
            _plan_item("daily.zip", "b" * 64, "2026-05-14"),
        ],
    }
    monkeypatch.setattr(
        corpus_replay,
        "list_assignment_packages",
        lambda: [
            {
                "package_id": "00000000-0000-0000-0000-000000000001",
                "file_name": "snapshot.zip",
                "sha256": "a" * 64,
                "package_kind": "ASSIGNMENT_SNAPSHOT_XML",
                "partition_value": "2026-05-13",
                "status": "REGISTERED",
                "profile": {},
            },
            {
                "package_id": "00000000-0000-0000-0000-000000000002",
                "file_name": "daily.zip",
                "sha256": "b" * 64,
                "package_kind": "DAILY_ASSIGNMENT_XML",
                "partition_value": "2026-05-14",
                "status": "SUCCESS",
                "profile": {
                    "schema_version": ASSIGNMENT_SCHEMA_VERSION,
                    "source_sha256": "b" * 64,
                },
            },
        ],
    )
    state = corpus_replay._registry_state(preflight)
    assert "OUT_OF_ORDER_SUCCESS_PACKAGE" in {item["type"] for item in state["blockers"]}


def test_replay_blocks_registry_source_outside_manifest(monkeypatch: pytest.MonkeyPatch):
    preflight = {"safe": True, "plan": [_plan_item("snapshot.zip", "a" * 64, "2026-05-13", "ASSIGNMENT_SNAPSHOT_XML")]}
    monkeypatch.setattr(
        corpus_replay,
        "list_assignment_packages",
        lambda: [
            {
                "package_id": "00000000-0000-0000-0000-000000000003",
                "file_name": "old-sample.xml",
                "sha256": "c" * 64,
                "package_kind": "ASSIGNMENT_SNAPSHOT_XML",
                "partition_value": "2026-04-01",
                "status": "SUCCESS",
                "profile": {
                    "schema_version": ASSIGNMENT_SCHEMA_VERSION,
                    "source_sha256": "c" * 64,
                },
            }
        ],
    )
    state = corpus_replay._registry_state(preflight)
    assert state["blockers"][0]["type"] == "REGISTRY_PACKAGE_OUTSIDE_MANIFEST"


def test_assignment_corpus_scripts_keep_apply_and_retry_explicit():
    replay = Path("scripts/replay-us-assignment-deterministic.ps1").read_text(encoding="utf-8")
    preflight = Path("scripts/preflight-us-assignment-corpus.ps1").read_text(encoding="utf-8")
    audit = Path("scripts/audit-us-assignment-corpus.ps1").read_text(encoding="utf-8")
    assert "[switch]$Apply" in replay
    assert "[switch]$ResumeFailed" in replay
    assert "--resume-failed" in replay
    assert "Persistent worker is running" in replay
    assert "corpus_preflight" in preflight
    assert "corpus_audit" in audit
