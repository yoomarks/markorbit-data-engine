from pathlib import Path

import app.us.jobs as us_jobs
from app.scanner import sha256_file


def test_us_input_policy_accepts_one_known_daily_source(tmp_path: Path, monkeypatch) -> None:
    incoming = tmp_path / "incoming" / "us"
    incoming.mkdir(parents=True)
    (incoming / "apc260809.xml").write_text("<root />", encoding="utf-8")
    monkeypatch.setattr(us_jobs, "list_us_packages", lambda: [])
    assert us_jobs.us_input_policy_issues(incoming) == []


def test_us_input_policy_rejects_unknown_precedence(tmp_path: Path, monkeypatch) -> None:
    incoming = tmp_path / "incoming" / "us"
    incoming.mkdir(parents=True)
    (incoming / "mystery.xml").write_text("<root />", encoding="utf-8")
    monkeypatch.setattr(us_jobs, "list_us_packages", lambda: [])
    issues = us_jobs.us_input_policy_issues(incoming)
    assert [item["type"] for item in issues] == ["UNKNOWN_US_PACKAGE_PRECEDENCE"]


def test_us_input_policy_rejects_two_sources_for_one_update_date(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming" / "us"
    incoming.mkdir(parents=True)
    (incoming / "apc260809.xml").write_text("<root />", encoding="utf-8")
    (incoming / "apc260809.zip").write_bytes(b"not-a-real-zip-yet")
    monkeypatch.setattr(us_jobs, "list_us_packages", lambda: [])
    issues = us_jobs.us_input_policy_issues(incoming)
    assert any(item["type"] == "AMBIGUOUS_US_UPDATE_DATE" for item in issues)


def test_us_input_policy_allows_exact_registered_source_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming" / "us"
    incoming.mkdir(parents=True)
    source = incoming / "apc260809.xml"
    source.write_text("<root />", encoding="utf-8")
    digest = sha256_file(source)
    monkeypatch.setattr(
        us_jobs,
        "list_us_packages",
        lambda: [{"partition_value": "2026-08-09", "sha256": digest}],
    )
    assert us_jobs.us_input_policy_issues(incoming) == []


def test_us_input_policy_rejects_unmodeled_same_day_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    incoming = tmp_path / "incoming" / "us"
    incoming.mkdir(parents=True)
    source = incoming / "apc260809.xml"
    source.write_text("new revision", encoding="utf-8")
    monkeypatch.setattr(
        us_jobs,
        "list_us_packages",
        lambda: [{"partition_value": "2026-08-09", "sha256": "0" * 64}],
    )
    issues = us_jobs.us_input_policy_issues(incoming)
    assert any(item["type"] == "US_DAILY_REVISION_POLICY_REQUIRED" for item in issues)
