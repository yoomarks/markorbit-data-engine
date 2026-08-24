"""Single-process authenticated operator controller for Singapore IPOS activation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .acquisition import DataGovSgSnapshotDownloader
from .ipos_sg import IPOS_SG_TRADEMARK_APPLICATIONS
from .ipos_sg_acceptance import IposSourceAcceptance, probe_ipos_live_source
from .ipos_sg_full_acceptance import (
    FullCorpusAcceptanceReport,
    report_payload as full_corpus_report_payload,
    run_ipos_full_corpus_acceptance,
    write_acceptance_report,
)
from .ipos_sg_resources import (
    IposStoragePreflight,
    build_ipos_storage_preflight,
    storage_preflight_payload,
)
from .ipos_sg_state import (
    IposStateAudit,
    assert_ipos_state_ready,
    audit_ipos_state,
    audit_payload,
)


IPOS_SG_OPERATOR_RUN_VERSION = "IPOS_SG_OPERATOR_RUN_V1"
_LOCK_FILE = ".operator.lock"
_DEFAULT_STALE_LOCK_AGE = timedelta(hours=12)


class IposOperatorBusyError(RuntimeError):
    """Raised when another Singapore operator owns the state directory."""


class IposOperatorStateError(RuntimeError):
    """Raised when an operator precondition is not safe to continue."""


@dataclass(frozen=True)
class IposOperatorReport:
    version: str
    run_id: str
    started_at: datetime
    completed_at: datetime
    status: str
    state_before: dict[str, Any]
    storage_preflight: dict[str, Any]
    live_source: dict[str, Any]
    full_corpus: dict[str, Any]
    state_after: dict[str, Any]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IposOperatorBusyError(
            f"Singapore operator lock exists but cannot be validated: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise IposOperatorBusyError(
            f"Singapore operator lock is not a JSON object: {path}"
        )
    return payload


def _lock_is_stale(payload: dict[str, Any], *, now: datetime, stale_after: timedelta) -> bool:
    raw = str(payload.get("started_at") or "")
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if started.tzinfo is None:
        return False
    return now - started.astimezone(timezone.utc) >= stale_after


@contextmanager
def ipos_operator_lease(
    state_directory: str | Path,
    *,
    recover_stale_lock: bool = False,
    stale_after: timedelta = _DEFAULT_STALE_LOCK_AGE,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Iterator[dict[str, str]]:
    """Hold one filesystem lease for the complete authenticated operator run.

    The lease is intentionally stored inside the mounted lifecycle state so separate
    one-shot containers cannot overlap. Stale lock recovery is explicit and is only
    allowed after the configured age; malformed locks fail closed.
    """
    if stale_after.total_seconds() < 3600:
        raise ValueError("stale operator lock age must be at least one hour")

    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / _LOCK_FILE
    run_id = uuid.uuid4().hex
    started_at = now().astimezone(timezone.utc)
    payload = {
        "version": IPOS_SG_OPERATOR_RUN_VERSION,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
    }

    def acquire() -> int:
        return os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    try:
        descriptor = acquire()
    except FileExistsError as exc:
        existing = _read_lock(lock_path)
        if not recover_stale_lock:
            raise IposOperatorBusyError(
                "Singapore operator state is already leased by "
                f"run_id={existing.get('run_id') or 'unknown'} "
                f"started_at={existing.get('started_at') or 'unknown'}"
            ) from exc
        if not _lock_is_stale(existing, now=started_at, stale_after=stale_after):
            raise IposOperatorBusyError(
                "Singapore operator lock is not old enough for explicit stale recovery: "
                f"run_id={existing.get('run_id') or 'unknown'} "
                f"started_at={existing.get('started_at') or 'unknown'}"
            ) from exc
        lock_path.unlink()
        try:
            descriptor = acquire()
        except FileExistsError as retry_exc:
            raise IposOperatorBusyError(
                "Singapore operator lock was reacquired by another process during stale recovery"
            ) from retry_exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, ensure_ascii=False, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        yield payload
    finally:
        lock_path.unlink(missing_ok=True)


def _live_source_payload(result: IposSourceAcceptance) -> dict[str, Any]:
    payload = asdict(result)
    payload["checked_at"] = result.checked_at.isoformat()
    payload["field_names"] = list(result.field_names)
    return payload


def operator_report_payload(report: IposOperatorReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["started_at"] = report.started_at.isoformat()
    payload["completed_at"] = report.completed_at.isoformat()
    return payload


def _redact_secret(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def run_ipos_operator(
    state_directory: str | Path,
    *,
    api_key: str,
    recover_stale_lock: bool = False,
    state_auditor: Callable[[str | Path], IposStateAudit] = audit_ipos_state,
    storage_builder: Callable[[str | Path], IposStoragePreflight] = build_ipos_storage_preflight,
    live_probe: Callable[..., IposSourceAcceptance] = probe_ipos_live_source,
    downloader_factory: Callable[..., Any] = DataGovSgSnapshotDownloader,
    full_runner: Callable[..., FullCorpusAcceptanceReport] = run_ipos_full_corpus_acceptance,
    acceptance_writer: Callable[[str | Path, FullCorpusAcceptanceReport], Path] = write_acceptance_report,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> IposOperatorReport:
    """Run state/resource preflight, source probe, full corpus, and strict postflight.

    The lightweight live probe deliberately does not resolve a whole-dataset download
    URL. The full-corpus downloader performs the single authenticated initiate/poll
    sequence, avoiding duplicate multi-GB materialization requests.
    """
    secret = api_key.strip()
    if not secret:
        raise ValueError("DATA_GOV_SG_API_KEY is required for authenticated Singapore runs")

    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    acceptance_dir = state / "acceptance"

    with ipos_operator_lease(
        state,
        recover_stale_lock=recover_stale_lock,
        now=now,
    ) as lease:
        run_id = lease["run_id"]
        started_at = datetime.fromisoformat(lease["started_at"])
        phase = "STATE_PREFLIGHT"
        try:
            before = state_auditor(state)
            if not before.safe_to_run:
                raise IposOperatorStateError(
                    "Singapore IPOS preflight state is blocked: "
                    + json.dumps(audit_payload(before), ensure_ascii=False, sort_keys=True)
                )

            phase = "RESOURCE_PREFLIGHT"
            storage = storage_builder(state)
            if not storage.safe_to_run:
                raise IposOperatorStateError(
                    "Singapore IPOS storage headroom is blocked: "
                    + json.dumps(
                        storage_preflight_payload(storage),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )

            phase = "LIVE_SOURCE_AUTHENTICATION"
            live = live_probe(
                api_key=secret,
                resolve_download_url=False,
            )
            if live.dataset_id != IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id:
                raise RuntimeError("Singapore live-source probe returned the wrong dataset identity")

            phase = "FULL_CORPUS_LIFECYCLE"
            downloader = downloader_factory(api_key=secret)
            corpus = full_runner(state, downloader=downloader)
            if corpus.dataset_id != IPOS_SG_TRADEMARK_APPLICATIONS.dataset_id:
                raise RuntimeError("Singapore full-corpus run returned the wrong dataset identity")
            acceptance_writer(acceptance_dir / "latest.json", corpus)

            phase = "STATE_POSTFLIGHT"
            after = state_auditor(state)
            assert_ipos_state_ready(after)

            completed_at = now().astimezone(timezone.utc)
            report = IposOperatorReport(
                version=IPOS_SG_OPERATOR_RUN_VERSION,
                run_id=run_id,
                started_at=started_at,
                completed_at=completed_at,
                status="PASS",
                state_before=audit_payload(before),
                storage_preflight=storage_preflight_payload(storage),
                live_source=_live_source_payload(live),
                full_corpus=full_corpus_report_payload(corpus),
                state_after=audit_payload(after),
            )
            _atomic_write_json(
                acceptance_dir / "operator_latest.json",
                operator_report_payload(report),
            )
            return report
        except Exception as exc:
            failure = {
                "version": IPOS_SG_OPERATOR_RUN_VERSION,
                "run_id": run_id,
                "status": "FAILED",
                "phase": phase,
                "failed_at": now().astimezone(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": _redact_secret(str(exc), secret),
            }
            _atomic_write_json(acceptance_dir / "operator_failure_latest.json", failure)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one authenticated Singapore IPOS operator cycle"
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--recover-stale-lock",
        action="store_true",
        help="recover an operator lock only when it is at least 12 hours old",
    )
    args = parser.parse_args()
    api_key = os.getenv("DATA_GOV_SG_API_KEY") or ""
    try:
        report = run_ipos_operator(
            args.state_dir,
            api_key=api_key,
            recover_stale_lock=args.recover_stale_lock,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": _redact_secret(str(exc), api_key),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4

    print(json.dumps(operator_report_payload(report), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
