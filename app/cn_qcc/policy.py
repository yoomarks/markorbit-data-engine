from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

from app.cn_qcc import POLICY_VERSION


# This is an acquisition-eligibility signal only. It does not assert a legal
# entity classification. QCC is queried only when the official applicant name
# itself contains a conventional company / organization form signal.
_CN_COMPANY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "集团有限公司",
    "集团股份有限公司",
    "集团公司",
    "公司",
    "合伙企业",
    "合作社",
    "事务所",
    "研究院",
    "研究所",
    "设计院",
    "医院",
    "学校",
    "大学",
    "学院",
    "中心",
    "工厂",
    "厂",
)
_ENGLISH_COMPANY_RE = re.compile(
    r"(?:\bCO\.?\s*,?\s*LTD\.?\b|\bLTD\.?\b|\bLIMITED\b|\bINC\.?\b|\bCORP(?:ORATION)?\.?\b)$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class QccCandidate:
    entity_id: str
    applicant_name: str
    normalized_name: str
    applicant_address: str
    country_code: str
    region_code: str
    city: str
    trademark_count: int
    latest_application_number: str
    source_rank: int
    source_fingerprint: str
    lane_reason: str
    last_result_status: str = "NEVER_FETCHED"
    last_source_fingerprint: str = ""
    refresh_due_at: datetime | None = None


@dataclass(frozen=True)
class PlannedCandidate:
    candidate: QccCandidate
    task_type: str
    priority_score: int
    reason_codes: tuple[str, ...]


def has_company_name_signal(name: str) -> bool:
    compact = "".join(str(name or "").split()).strip("，,。.;；")
    if not compact:
        return False
    if any(compact.endswith(suffix) for suffix in _CN_COMPANY_SUFFIXES):
        return True
    return bool(_ENGLISH_COMPANY_RE.search(compact))


def is_qcc_eligible(candidate: QccCandidate) -> bool:
    # QCC is a China-company enrichment source. A blank country is tolerated
    # only when the applicant name itself carries a company-form signal.
    country = (candidate.country_code or "").upper()
    if country not in {"", "CN"}:
        return False
    return has_company_name_signal(candidate.applicant_name)


def _is_due(candidate: QccCandidate, *, now: datetime) -> bool:
    due = candidate.refresh_due_at
    if due is None:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= now


def score_candidate(candidate: QccCandidate, *, now: datetime | None = None) -> PlannedCandidate | None:
    if not is_qcc_eligible(candidate):
        return None
    now = now or datetime.now(timezone.utc)
    status = (candidate.last_result_status or "NEVER_FETCHED").upper()
    reasons: list[str] = []
    score = 0
    task_type = "INITIAL_FETCH" if status == "NEVER_FETCHED" else "REFRESH"

    # Acquisition triggers are intentionally distinct from priority modifiers.
    # Historical/backfill lane membership or trademark holdings may rank work,
    # but they must never schedule a successful/not-found entity before its
    # refresh contract is due.
    if status == "NEVER_FETCHED":
        score += 1_000_000
        reasons.append("NEVER_FETCHED")

    if (
        candidate.last_source_fingerprint
        and candidate.last_source_fingerprint != candidate.source_fingerprint
    ):
        score += 900_000
        reasons.append("SOURCE_IDENTITY_CHANGED")

    if status in {"FAILED", "UNATTEMPTED"}:
        score += 800_000
        reasons.append("RETRY_PREVIOUS_FAILURE")

    if _is_due(candidate, now=now):
        score += 600_000
        reasons.append("REFRESH_DUE")

    if score <= 0:
        return None

    if candidate.lane_reason:
        reasons.append(candidate.lane_reason)
        if candidate.lane_reason == "RECENT_SOURCE_CHANGE":
            score += 200_000
        elif candidate.lane_reason == "HISTORICAL_BACKFILL":
            score += 50_000

    # Trademark holdings are evidence for acquisition priority, not a business
    # lead score. Keep this small so coverage/freshness always dominates.
    holdings = max(0, int(candidate.trademark_count))
    if holdings:
        score += min(holdings, 1000) * 100
        reasons.append("TRADEMARK_HOLDER")

    return PlannedCandidate(
        candidate=candidate,
        task_type=task_type,
        priority_score=score,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def select_candidates(
    candidates: Iterable[QccCandidate],
    *,
    capacity: int,
    now: datetime | None = None,
) -> list[PlannedCandidate]:
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    scored = [planned for item in candidates if (planned := score_candidate(item, now=now))]
    scored.sort(
        key=lambda item: (
            -item.priority_score,
            -item.candidate.source_rank,
            item.candidate.entity_id,
        )
    )
    return scored[:capacity]


__all__ = [
    "POLICY_VERSION",
    "QccCandidate",
    "PlannedCandidate",
    "has_company_name_signal",
    "is_qcc_eligible",
    "score_candidate",
    "select_candidates",
]
