from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.us_ttab import TTAB_SCHEMA_VERSION
from app.us_ttab.corpus_manifest import MANIFEST_VERSION, preflight_manifest
from app.us_ttab import corpus_replay


def _xml(number: str, transaction_date: str = "20260513") -> str:
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<ttab-proceedings><transaction-date>{transaction_date}</transaction-date><proceeding-information><proceeding-entry>
<number>{number}</number><type-code>EXA</type-code><filing-date>20260414</filing-date>
<status-update-date>20260610</status-update-date><status-code>2</status-code>
<party-information><party><identifier>1</identifier><role-code>P</role-code><name>Alpha</name>
<property-information><property><identifier>1</identifier><serial-number>{number}</serial-number></property></property-information>
</party></party-information><prosecution-history><prosecution-entry><identifier>1</identifier>
<code>158</code><type-code>X</type-code><date>20260414</date><history-text>APPEAL TO BOARD</history-text>
</prosecution-entry></prosecution-history></proceeding-entry></proceeding-information></ttab-proceedings>"""


def _write_manifest(
    raw_root: Path,
    sources: list[dict],
    *,
    daily_count: int,
    daily_through: str | None,
    historical_count: int = 1,
) -> Path:
    path = raw_root / "manifests" / "us_ttab" / "corpus.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "expected_historical_packages": historical_count,
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
    historical.write_text(_xml("97658985", "20260513"), encoding="utf-8")
    daily.write_text(_xml("79412016", "20260514"), encoding="utf-8")
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
    assert Path(second["plan"][0]["path"]).as_posix().endswith(
        "archive/us_ttab/historical.xml"
    )



def test_ttab_manifest_accepts_split_historical_snapshot_parts(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    for index in range(1, 6):
        (incoming / f"historical-{index}.xml").write_text(
            _xml(str(90000000 + index), "20260902"), encoding="utf-8"
        )
    (incoming / "daily.xml").write_text(_xml("97658985", "20260904"), encoding="utf-8")
    publication_times = [f"2026-09-03T17:00:{53 + index:02d}Z" for index in range(5)]
    sources = [
        {
            "path": f"incoming/us_ttab/historical-{index}.xml",
            "source_kind": "TTAB_BULK_HISTORICAL_XML",
            "snapshot_at": publication_times[index - 1],
        }
        for index in range(1, 6)
    ]
    sources.append(
        {
            "path": "incoming/us_ttab/daily.xml",
            "source_kind": "TTAB_BULK_DAILY_XML",
            "snapshot_at": "2026-09-04T17:00:00Z",
        }
    )
    manifest = _write_manifest(
        tmp_path,
        sources,
        historical_count=5,
        daily_count=1,
        daily_through="2026-09-04",
    )

    report = preflight_manifest(manifest, tmp_path)

    assert report["status"] == "READY"
    assert report["safe"] is True
    assert report["expected_historical_packages"] == 5
    assert report["historical_batch_transaction_date"] == "2026-09-02"
    assert [row["file_name"] for row in report["plan"][:5]] == [
        f"historical-{index}.xml" for index in range(1, 6)
    ]


def test_ttab_manifest_rejects_split_historical_parts_with_mixed_transaction_date(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    for index in (1, 2):
        (incoming / f"historical-{index}.xml").write_text(
            _xml(str(91000000 + index), f"2026090{index}"), encoding="utf-8"
        )
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "path": "incoming/us_ttab/historical-1.xml",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
                "snapshot_at": "2026-09-03T17:00:00Z",
            },
            {
                "path": "incoming/us_ttab/historical-2.xml",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
                "snapshot_at": "2026-09-03T17:01:00Z",
            },
        ],
        historical_count=2,
        daily_count=0,
        daily_through=None,
    )

    report = preflight_manifest(manifest, tmp_path)

    assert report["status"] == "NOT_READY"
    assert "HISTORICAL_PART_TRANSACTION_DATE_MISMATCH" in {
        item["type"] for item in report["issues"]
    }

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


def test_ttab_manifest_allows_daily_publication_before_reissued_historical(tmp_path: Path):
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    (incoming / "historical.xml").write_text(_xml("97658985", "20260902"), encoding="utf-8")
    (incoming / "daily.xml").write_text(_xml("79412016", "20260513"), encoding="utf-8")
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
    assert report["status"] == "READY"
    assert [row["source_kind"] for row in report["plan"]] == [
        "TTAB_BULK_HISTORICAL_XML", "TTAB_BULK_DAILY_XML"
    ]


def _plan_item(name: str, digest: str, snapshot: str, kind: str) -> dict:
    return {
        "path": f"/data/raw/incoming/us_ttab/{name}",
        "manifest_path": f"incoming/us_ttab/{name}",
        "file_name": name,
        "source_kind": kind,
        "snapshot_at": snapshot,
        "transaction_date": snapshot[:10],
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
                "source_period_start": "2026-05-13",
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
                "source_period_start": "2026-05-14",
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


def test_ttab_manifest_rejects_missing_xml_transaction_date(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    source = incoming / "historical.xml"
    source.write_text("<ttab-proceedings></ttab-proceedings>", encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [{
            "path": "incoming/us_ttab/historical.xml",
            "source_kind": "TTAB_BULK_HISTORICAL_XML",
            "snapshot_at": "2026-09-03T17:01:00Z",
        }],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert any("transaction-date" in row.get("error", "") for row in report["issues"])


def test_ttab_manifest_rejects_duplicate_daily_transaction_date(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    (incoming / "historical.xml").write_text(_xml("90000001", "20260902"), encoding="utf-8")
    (incoming / "daily-a.xml").write_text(_xml("90000002", "20260911"), encoding="utf-8")
    (incoming / "daily-b.xml").write_text(_xml("90000003", "20260911"), encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            {"path": "incoming/us_ttab/historical.xml", "source_kind": "TTAB_BULK_HISTORICAL_XML", "snapshot_at": "2026-09-03T17:01:00Z"},
            {"path": "incoming/us_ttab/daily-a.xml", "source_kind": "TTAB_BULK_DAILY_XML", "snapshot_at": "2026-09-12T04:10:00Z"},
            {"path": "incoming/us_ttab/daily-b.xml", "source_kind": "TTAB_BULK_DAILY_XML", "snapshot_at": "2026-09-12T04:11:00Z"},
        ],
        daily_count=2,
        daily_through="2026-09-11",
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert "DUPLICATE_DAILY_TRANSACTION_DATE_NOT_MODELED" in {
        row["type"] for row in report["issues"]
    }


def test_ttab_manifest_rejects_invalid_xml_transaction_date(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming" / "us_ttab"
    incoming.mkdir(parents=True)
    source = incoming / "historical.xml"
    source.write_text(
        "<ttab-proceedings><transaction-date>20261340</transaction-date></ttab-proceedings>",
        encoding="utf-8",
    )
    manifest = _write_manifest(
        tmp_path,
        [{
            "path": "incoming/us_ttab/historical.xml",
            "source_kind": "TTAB_BULK_HISTORICAL_XML",
            "snapshot_at": "2026-09-03T17:01:00Z",
        }],
        daily_count=0,
        daily_through=None,
    )
    report = preflight_manifest(manifest, tmp_path)
    assert report["status"] == "NOT_READY"
    assert any("invalid XML transaction-date" in row.get("error", "") for row in report["issues"])
