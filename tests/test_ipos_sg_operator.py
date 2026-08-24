import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.snapshot_delta.ipos_sg_acceptance import IposSourceAcceptance
from app.snapshot_delta.ipos_sg_full_acceptance import FullCorpusAcceptanceReport
from app.snapshot_delta.ipos_sg_operator import (
    IposOperatorBusyError,
    IposOperatorStateError,
    ipos_operator_lease,
    operator_report_payload,
    run_ipos_operator,
)
from app.snapshot_delta.ipos_sg_state import IposStateAudit, IposStateIssue


def state_audit(status: str, *, safe: bool) -> IposStateAudit:
    return IposStateAudit(
        version="IPOS_SG_STATE_AUDIT_V1",
        checked_at=datetime(2026, 8, 24, 5, 0, tzinfo=timezone.utc),
        status=status,
        safe_to_run=safe,
        current_content_hash="a" * 64 if status == "READY" else None,
        retained_full_snapshot_count=1 if status == "READY" else 0,
        orphan_full_snapshot_count=0,
        transient_part_paths=(),
        issues=(
            ()
            if safe
            else (
                IposStateIssue(
                    code="BROKEN_STATE",
                    detail="synthetic blocked state",
                    blocking=True,
                ),
            )
        ),
    )


def live_acceptance() -> IposSourceAcceptance:
    return IposSourceAcceptance(
        dataset_id="d_6145acb2130bf781165258e76a584383",
        checked_at=datetime(2026, 8, 24, 5, 1, tzinfo=timezone.utc),
        total_rows=875000,
        field_names=("applicationNumber", "markStatus"),
        sample_application_number="40202600001A",
        sample_mark_status="Registered",
        download_url_resolved=False,
    )


def corpus_report() -> FullCorpusAcceptanceReport:
    return FullCorpusAcceptanceReport(
        dataset_id="d_6145acb2130bf781165258e76a584383",
        completed_at=datetime(2026, 8, 24, 5, 2, tzinfo=timezone.utc),
        status="BOOTSTRAPPED",
        content_hash="a" * 64,
        schema_hash="b" * 64,
        row_count=875000,
        bytes_downloaded=3900000000,
        current_snapshot_bytes=3900000000,
        event_count=0,
        native_change_count=0,
        retained_full_snapshot_count=1,
        elapsed_seconds=120.0,
        storage_reference=f"snapshots/{'a' * 64}.csv",
        events_path=None,
        native_changes_path=None,
    )


def test_operator_uses_one_authenticated_materialization_path_and_never_reports_secret(
    tmp_path: Path,
):
    secret = "operator-secret-value"
    audits = iter([state_audit("EMPTY", safe=True), state_audit("READY", safe=True)])
    calls = {}

    def audit(_state):
        return next(audits)

    def live_probe(*, api_key, resolve_download_url):
        calls["live_api_key"] = api_key
        calls["resolve_download_url"] = resolve_download_url
        return live_acceptance()

    class Downloader:
        pass

    downloader = Downloader()

    def downloader_factory(*, api_key):
        calls["downloader_api_key"] = api_key
        return downloader

    def full_runner(state, *, downloader):
        calls["full_state"] = Path(state)
        calls["full_downloader"] = downloader
        return corpus_report()

    def acceptance_writer(path, report):
        calls["acceptance_path"] = Path(path)
        calls["acceptance_report"] = report
        return Path(path)

    times = iter(
        [
            datetime(2026, 8, 24, 5, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 24, 5, 3, tzinfo=timezone.utc),
        ]
    )
    report = run_ipos_operator(
        tmp_path,
        api_key=secret,
        state_auditor=audit,
        live_probe=live_probe,
        downloader_factory=downloader_factory,
        full_runner=full_runner,
        acceptance_writer=acceptance_writer,
        now=lambda: next(times),
    )

    assert calls["live_api_key"] == secret
    assert calls["resolve_download_url"] is False
    assert calls["downloader_api_key"] == secret
    assert calls["full_downloader"] is downloader
    assert calls["full_state"] == tmp_path
    assert calls["acceptance_path"] == tmp_path / "acceptance" / "latest.json"
    assert report.status == "PASS"
    serialized = json.dumps(operator_report_payload(report), sort_keys=True)
    assert secret not in serialized
    assert not (tmp_path / ".operator.lock").exists()
    assert (tmp_path / "acceptance" / "operator_latest.json").exists()


def test_operator_lease_rejects_overlap_and_releases_after_exit(tmp_path: Path):
    with ipos_operator_lease(tmp_path):
        with pytest.raises(IposOperatorBusyError, match="already leased"):
            with ipos_operator_lease(tmp_path):
                pass
        assert (tmp_path / ".operator.lock").exists()

    assert not (tmp_path / ".operator.lock").exists()


def test_operator_failure_redacts_secret_and_releases_lease(tmp_path: Path):
    secret = "do-not-write-this-secret"

    def live_probe(**_kwargs):
        raise RuntimeError(f"synthetic provider failure {secret}")

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        run_ipos_operator(
            tmp_path,
            api_key=secret,
            state_auditor=lambda _state: state_audit("EMPTY", safe=True),
            live_probe=live_probe,
        )

    failure_path = tmp_path / "acceptance" / "operator_failure_latest.json"
    failure_text = failure_path.read_text(encoding="utf-8")
    failure = json.loads(failure_text)
    assert failure["phase"] == "LIVE_SOURCE_AUTHENTICATION"
    assert failure["status"] == "FAILED"
    assert "[REDACTED]" in failure["error"]
    assert secret not in failure_text
    assert not (tmp_path / ".operator.lock").exists()


def test_blocked_preflight_fails_before_any_network_or_corpus_work(tmp_path: Path):
    called = {"live": False, "full": False}

    def live_probe(**_kwargs):
        called["live"] = True
        return live_acceptance()

    def full_runner(*_args, **_kwargs):
        called["full"] = True
        return corpus_report()

    with pytest.raises(IposOperatorStateError, match="preflight state is blocked"):
        run_ipos_operator(
            tmp_path,
            api_key="secret",
            state_auditor=lambda _state: state_audit("BLOCKED", safe=False),
            live_probe=live_probe,
            full_runner=full_runner,
        )

    assert called == {"live": False, "full": False}
    assert not (tmp_path / ".operator.lock").exists()
