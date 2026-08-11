from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.us_ttab import TTAB_SCHEMA_VERSION
from app.us_ttab.corpus_manifest import MANIFEST_VERSION, preflight_manifest
from app.us_ttab import corpus_replay


def _xml(number: str) -> str:
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<ttab-proceedings><proceeding-information><proceeding-entry>
<number>{number}</number><type-code>EXA</type-code><filing-date>20260414</filing-date>
<status-update-date>20260610</status-update-date><status-code>2</status-code>
<party-information><party><identifier>1</identifier><role-code>P</role-code><name>Alpha</name>
<property-information><property><identifier>1</identifier><serial-number>{number}</serial-number></property></property-information>
</party></party-information><prosecution-history><prosecution-entry><identifier>1</identifier>
<code>158</code><type-code>X</type-code><date>20260414</date><history-text>APPEAL TO BOARD</history-text>
</prosecution-entry></prosecution-history></proceeding-entry></proceeding-information></ttab-proceedings>"""


def _write_manifest(raw_root: Path, sources: list[dict], *, daily_count: int, daily_through: str | None) -> Path:
    path = raw_root / "manifests" / "us_ttab" / "corpus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "expected_historical_packages": 1,
                "expected_daily_packages": daily_count,
                "daily_through": daily_through,
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_ttab_manifest_preflight_survives_incoming_to_archive_move(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_ttab"
    archive = tmp_path / "archive" / "us_ttab"
    incoming.mkdir(parents=True)
    archive.mkdir(parents=True)
    historical = incoming / "historical.xml"
    daily = incoming / "daily.xml"
    historical.write_text(_xml("97658985"), encoding="utf-8")
    daily.write_text(_xml("79412016"), encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_ttab/historical.xml",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
                "snapshot_at": "2026-05-13T12:00:00Z",
            },
            {
                "path": "incoming/us_ttab/daily.xml",
                "source_kind": "TTAB_BULK_DAILY_XML",
                "snapshot_at": "2026-05-14T12:00:00Z",
            },
        ],
        daily_count=1,
        daily_through="2026-05-14",
    )
    first = preflight_manifest(manifest, tmp_path)
    assert first["status"] == "READY"
    assert first["snapshot_at_inferred_from_filename"] is False
    assert first["calendar_gap_inference"] is False

    historical.rename(archive / historical.name)
    second = preflight_manifest(manifest, tmp_path)
    assert second["status"] == "READY"
    assert second["plan"][0]["path"].endswith("archive/us_ttab/historical.xml")


def test_ttab_manifest_requires_timezone_aware_snapshot(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_ttab/tt260809.zip",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
                "snapshot_at": "2026-08-09T12:00:00",
            }
        ],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert report["issues"][0]["type"] == "MANIFEST_INVALID"
    assert "timezone" in report["issues"][0]["error"].lower()


def test_ttab_manifest_rejects_per_proceeding_rawxml_source(tmp_path: Path):
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_ttab/raw.xml",
                "source_kind": "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
                "snapshot_at": "2026-08-09T12:00:00Z",
            }
        ],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert "not full-corpus replay sources" in report["issues"][0]["error"]


def test_ttab_manifest_rejects_daily_at_or_before_historical(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    (incoming / "historical.xml").write_text(_xml("97658985"), encoding="utf-8")
    (incoming / "daily.xml").write_text(_xml("79412016"), encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_ttab/historical.xml",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
                "snapshot_at": "2026-05-13T12:00:00Z",
            },
            {
                "path": "incoming/us_ttab/daily.xml",
                "source_kind": "TTAB_BULK_DAILY_XML",
                "snapshot_at": "2026-05-13T11:00:00Z",
            },
        ],
        daily_count=1,
        daily_through="2026-05-13",
    )
    report = preflight_manifest(manifest, tmp_path)
    assert "DAILY_NOT_AFTER_HISTORICAL_SNAPSHOT" in {
        item["type"] for item in report["issues"]
    }


def _plan_item(name: str, digest: str, snapshot: str, kind: str) -> dict:
    return {
        "path": f"/data/raw/incoming/us_ttab/{name}",
        "manifest_path": f"incoming/us_ttab/{name}",
        "file_name": name,
        "source_kind": kind,
        "snapshot_at": snapshot,
        "sha256": digest,
        "size_bytes": 1,
        "xml_members": [name.replace(".zip", ".xml")],
    }


def test_ttab_replay_blocks_success_after_unfinished_prefix(monkeypatch: pytest.MonkeyPatch):
    preflight = {
        "safe": True,
        "plan": [
            _plan_item("historical.zip", "a" * 64, "2026-05-13T12:00:00.000Z", "TTAB_BULK_HISTORICAL_XML"),
            _plan_item("daily.zip", "b" * 64, "2026-05-14T12:00:00.000Z", "TTAB_BULK_DAILY_XML"),
        ],
    }
    monkeypatch.setattr(
        corpus_replay,
        "list_ttab_packages",
        lambda: [
            {
                "package_id": "00000000-0000-0000-0000-000000000001",
                "file_name": "historical.zip",
                "sha256": "a" * 64,
                "package_kind": "TTAB_BULK_HISTORICAL_XML",
                "partition_value": "2026-05-13T12:00:00.000Z",
                "status": "REGISTERED",
                "schema_version": TTAB_SCHEMA_VERSION,
                "profile": {},
            },
            {
                "package_id": "00000000-0000-0000-0000-000000000002",
                "file_name": "daily.zip",
                "sha256": "b" * 64,
                "package_kind": "TTAB_BULK_DAILY_XML",
                "partition_value": "2026-05-14T12:00:00.000Z",
                "status": "SUCCESS",
                "schema_version": TTAB_SCHEMA_VERSION,
                "profile": {
                    "source_sha256": "b" * 64,
                    "snapshot_at": "2026-05-14T12:00:00+00:00",
                },
            },
        ],
    )
    state = corpus_replay._registry_state(preflight)
    blocker_types = {item["type"] for item in state["blockers"]}
    assert "OUT_OF_ORDER_SUCCESS_PACKAGE" in blocker_types


def test_ttab_replay_blocks_registry_source_outside_manifest(monkeypatch: pytest.MonkeyPatch):
    preflight = {
        "safe": True,
        "plan": [
            _plan_item("historical.zip", "a" * 64, "2026-05-13T12:00:00.000Z", "TTAB_BULK_HISTORICAL_XML")
        ],
    }
    monkeypatch.setattr(
        corpus_replay,
        "list_ttab_packages",
        lambda: [
            {
                "package_id": "00000000-0000-0000-0000-000000000003",
                "file_name": "old-rawxml.xml",
                "sha256": "c" * 64,
                "package_kind": "TTABVUE_PROCEEDING_RAWXML_SNAPSHOT",
                "partition_value": "2026-04-01T12:00:00.000Z",
                "status": "SUCCESS",
                "schema_version": TTAB_SCHEMA_VERSION,
                "profile": {"source_sha256": "c" * 64},
            }
        ],
    )
    state = corpus_replay._registry_state(preflight)
    assert state["blockers"][0]["type"] == "REGISTRY_PACKAGE_OUTSIDE_MANIFEST"


def test_ttab_corpus_scripts_make_apply_retry_and_current_build_explicit():
    replay = Path("scripts/replay-us-ttab-deterministic.ps1").read_text(encoding="utf-8")
    preflight = Path("scripts/preflight-us-ttab-corpus.ps1").read_text(encoding="utf-8")
    audit = Path("scripts/audit-us-ttab-corpus.ps1").read_text(encoding="utf-8")
    assert "[switch]$Apply" in replay
    assert "[switch]$ResumeFailed" in replay
    assert "--resume-failed" in replay
    assert '"--build"' in replay
    assert "Persistent worker is running" in replay
    assert "corpus_preflight" in preflight
    assert "corpus_audit" in audit
