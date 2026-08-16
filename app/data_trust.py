from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable


DATA_TRUST_VERSION = "MARKORBIT_DATA_TRUST_FRESHNESS_V1"
ACCEPTANCE_PASS_STATUSES = {"PASS", "PASS_WITH_WARNINGS", "ACCEPTED"}
SILENCE_SEMANTICS = (
    "NO_SOURCE_OBSERVATION_WITHIN_VERIFIED_COVERAGE_NOT_LEGAL_NONEXISTENCE"
)


def _as_utc(value: date | datetime | str | None, *, label: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be ISO-8601 date/datetime") from exc
        return datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            tzinfo=timezone.utc,
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DataTrustEvidence:
    domain: str
    query_plane_ready: bool
    source_identity_complete: bool
    registered_corpus_complete: bool
    source_verification_passed: bool
    acceptance_status: str
    coverage_through: date | datetime | str | None
    required_coverage_through: date | datetime | str | None
    source_supports_silence: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataTrustResult:
    domain: str
    queryable: bool
    complete: bool
    fresh: bool
    accepted: bool
    trusted_for_silence: bool
    coverage_through: str | None
    required_coverage_through: str | None
    acceptance_status: str
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trust_version": DATA_TRUST_VERSION,
            "domain": self.domain,
            "queryable": self.queryable,
            "complete": self.complete,
            "fresh": self.fresh,
            "accepted": self.accepted,
            "trusted_for_silence": self.trusted_for_silence,
            "coverage_through": self.coverage_through,
            "required_coverage_through": self.required_coverage_through,
            "acceptance_status": self.acceptance_status,
            "reason_codes": list(self.reason_codes),
            "warnings": list(self.warnings),
            "silence_semantics": SILENCE_SEMANTICS,
            "legal_conclusion": False,
        }


def evaluate_data_trust(evidence: DataTrustEvidence) -> DataTrustResult:
    domain = evidence.domain.strip().upper()
    if not domain:
        raise ValueError("domain is required")

    acceptance_status = evidence.acceptance_status.strip().upper() or "NOT_EVALUATED"
    coverage = _as_utc(evidence.coverage_through, label="coverage_through")
    required = _as_utc(
        evidence.required_coverage_through,
        label="required_coverage_through",
    )

    queryable = bool(evidence.query_plane_ready)
    complete = bool(
        evidence.source_identity_complete
        and evidence.registered_corpus_complete
        and evidence.source_verification_passed
    )
    fresh = bool(coverage is not None and required is not None and coverage >= required)
    accepted = acceptance_status in ACCEPTANCE_PASS_STATUSES

    reasons: list[str] = []
    if not queryable:
        reasons.append("QUERY_PLANE_NOT_READY")
    if not evidence.source_identity_complete:
        reasons.append("SOURCE_IDENTITY_INCOMPLETE")
    if not evidence.registered_corpus_complete:
        reasons.append("REGISTERED_CORPUS_INCOMPLETE")
    if not evidence.source_verification_passed:
        reasons.append("SOURCE_VERIFICATION_NOT_PASSED")
    if coverage is None:
        reasons.append("COVERAGE_THROUGH_UNKNOWN")
    if required is None:
        reasons.append("REQUIRED_COVERAGE_THROUGH_UNKNOWN")
    if coverage is not None and required is not None and coverage < required:
        reasons.append("SOURCE_COVERAGE_STALE")
    if not accepted:
        reasons.append("DOMAIN_NOT_ACCEPTED")
    if not evidence.source_supports_silence:
        reasons.append("SOURCE_DOES_NOT_SUPPORT_SILENCE_INFERENCE")

    trusted_for_silence = bool(
        queryable
        and complete
        and fresh
        and accepted
        and evidence.source_supports_silence
    )

    return DataTrustResult(
        domain=domain,
        queryable=queryable,
        complete=complete,
        fresh=fresh,
        accepted=accepted,
        trusted_for_silence=trusted_for_silence,
        coverage_through=coverage.isoformat() if coverage else None,
        required_coverage_through=required.isoformat() if required else None,
        acceptance_status=acceptance_status,
        reason_codes=tuple(reasons),
        warnings=tuple(str(value) for value in evidence.warnings),
    )


def aggregate_data_trust(results: Iterable[DataTrustResult]) -> dict[str, Any]:
    rows = tuple(results)
    domains = {row.domain: row.as_dict() for row in rows}
    if len(domains) != len(rows):
        raise ValueError("data trust aggregate contains duplicate domain")
    return {
        "trust_version": DATA_TRUST_VERSION,
        "domain_count": len(rows),
        "all_queryable": bool(rows) and all(row.queryable for row in rows),
        "all_complete": bool(rows) and all(row.complete for row in rows),
        "all_fresh": bool(rows) and all(row.fresh for row in rows),
        "all_accepted": bool(rows) and all(row.accepted for row in rows),
        "all_trusted_for_silence": bool(rows)
        and all(row.trusted_for_silence for row in rows),
        "domains": domains,
        "silence_semantics": SILENCE_SEMANTICS,
        "legal_conclusion": False,
    }


def data_trust_contract() -> dict[str, Any]:
    return {
        "version": DATA_TRUST_VERSION,
        "dimensions": [
            "queryable",
            "complete",
            "fresh",
            "accepted",
            "trusted_for_silence",
        ],
        "freshness_policy": {
            "engine_does_not_guess_source_cadence": True,
            "domain_supplies_required_coverage_through": True,
            "fresh_when_coverage_meets_or_exceeds_required_boundary": True,
        },
        "completeness_policy": {
            "source_identity_complete_required": True,
            "registered_corpus_complete_required": True,
            "source_verification_passed_required": True,
        },
        "acceptance_policy": {
            "accepted_statuses": sorted(ACCEPTANCE_PASS_STATUSES),
            "execution_success_alone_is_not_acceptance": True,
        },
        "silence_policy": {
            "semantics": SILENCE_SEMANTICS,
            "requires_queryable": True,
            "requires_complete": True,
            "requires_fresh": True,
            "requires_accepted": True,
            "requires_source_supports_silence": True,
            "absence_is_not_legal_nonexistence": True,
            "absence_does_not_authorize_action": True,
        },
        "legal_conclusion": False,
    }
