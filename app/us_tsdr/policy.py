from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


POLICY_VERSION = "US_TSDR_WEEKLY_V1"
DEFAULT_WEEKLY_CAPACITY = 300_000

CN_COUNTRY_CODES = {"CN", "CHN", "CHINA", "PRC"}


@dataclass(frozen=True)
class Candidate:
    serial_number: str
    source_rank: int
    filing_date: date | None = None
    applicant_country: str = ""
    current_attorney_present: bool = False
    source_attorney_fingerprint: str = ""
    representation_changed: bool = False
    attorney_removed: bool = False
    lifecycle_state: str = "UNKNOWN"
    never_fetched: bool = True
    terminal_complete: bool = False
    refresh_due_at: datetime | None = None
    last_fetched_at: datetime | None = None
    demand_priority: int = 0
    retry_required: bool = False
    is_new_application: bool = False


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    task_type: str
    priority_score: int
    reason_codes: tuple[str, ...]
    hard_new_application: bool


def _country(value: str) -> str:
    return (value or "").strip().upper()


def _is_refresh_due(candidate: Candidate, now: datetime) -> bool:
    return bool(candidate.refresh_due_at and candidate.refresh_due_at <= now)


def classify_task(candidate: Candidate) -> str | None:
    if candidate.terminal_complete:
        return None
    if candidate.lifecycle_state == "TERMINAL_INVALID":
        return "TERMINAL_INITIAL_FETCH" if candidate.never_fetched else "FINAL_FETCH"
    if candidate.never_fetched:
        return "INITIAL_FETCH"
    return "REFRESH"


def rank_candidate(
    candidate: Candidate,
    *,
    now: datetime | None = None,
) -> RankedCandidate | None:
    """Rank a TSDR candidate without making business/legal conclusions.

    New US application observations are a hard first lane. Terminal-invalid cases
    are one-shot only: once ``terminal_complete`` is true they leave scheduling.
    Other reasons only influence ordering; they never permanently suppress a case.
    """
    now = now or datetime.now(timezone.utc)
    task_type = classify_task(candidate)
    if task_type is None:
        return None

    is_new = bool(candidate.is_new_application and candidate.never_fetched)
    reasons: list[str] = []
    score = 0

    if is_new:
        reasons.append("NEW_APPLICATION")
        score += 1_000_000

    if candidate.attorney_removed:
        reasons.append("ATTORNEY_REMOVED")
        score += 900_000
    elif candidate.representation_changed:
        reasons.append("REPRESENTATION_CHANGED")
        score += 850_000

    if candidate.retry_required:
        reasons.append("RETRY_PREVIOUS_FAILURE")
        score += 700_000

    country = _country(candidate.applicant_country)
    if country in CN_COUNTRY_CODES:
        reasons.append("CN_APPLICANT")
        score += 600_000
        if not candidate.current_attorney_present:
            reasons.append("CN_NO_CURRENT_ATTORNEY")
            score += 180_000
    elif not candidate.current_attorney_present:
        reasons.append("NO_CURRENT_ATTORNEY")
        score += 120_000

    if candidate.lifecycle_state == "TERMINAL_INVALID":
        reasons.append("TERMINAL_INVALID_ONE_SHOT")
        score += 50_000

    if candidate.never_fetched:
        reasons.append("NEVER_FETCHED")
        score += 100_000
    elif _is_refresh_due(candidate, now):
        reasons.append("REFRESH_DUE")
        score += 80_000

    if candidate.demand_priority > 0:
        bounded = min(int(candidate.demand_priority), 100_000)
        reasons.append("EXTERNAL_DEMAND")
        score += bounded

    if (
        not is_new
        and not candidate.never_fetched
        and candidate.lifecycle_state != "TERMINAL_INVALID"
        and not candidate.attorney_removed
        and not candidate.representation_changed
        and not candidate.retry_required
        and not _is_refresh_due(candidate, now)
        and candidate.demand_priority <= 0
    ):
        return None

    if candidate.never_fetched and candidate.filing_date:
        age_days = max((now.date() - candidate.filing_date).days, 0)
        aging_boost = min(age_days // 30, 50_000)
        if aging_boost:
            reasons.append("COVERAGE_AGING")
            score += aging_boost

    if not reasons:
        reasons.append("BACKGROUND_REFRESH")

    return RankedCandidate(
        candidate=candidate,
        task_type=task_type,
        priority_score=score,
        reason_codes=tuple(reasons),
        hard_new_application=is_new,
    )


def select_weekly_batch(
    candidates: list[Candidate],
    *,
    capacity: int = DEFAULT_WEEKLY_CAPACITY,
    now: datetime | None = None,
) -> list[RankedCandidate]:
    if capacity < 1:
        raise ValueError("capacity must be positive")
    now = now or datetime.now(timezone.utc)

    ranked: list[RankedCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        serial = candidate.serial_number.strip()
        if not serial or serial in seen:
            continue
        seen.add(serial)
        item = rank_candidate(candidate, now=now)
        if item is not None:
            ranked.append(item)

    ranked.sort(
        key=lambda item: (
            0 if item.hard_new_application else 1,
            int(item.candidate.source_rank) if item.hard_new_application else -item.priority_score,
            item.candidate.serial_number if item.hard_new_application else "",
            -item.priority_score,
            item.candidate.serial_number,
        )
    )
    return ranked[:capacity]
