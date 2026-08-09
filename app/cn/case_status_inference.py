from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


MODEL_VERSION = "CN_CASE_STATUS_INFERENCE_V1_EMPIRICAL"
MODEL_STAGE = "EMPIRICAL"
EARLY_TOTAL_LOSS_WINDOW_DAYS = 93
POST_PRELIM_LONG_DELAY_DAYS = 365


class InferredScope(StrEnum):
    PARTIAL = "PARTIAL"
    TOTAL = "TOTAL"
    UNKNOWN = "UNKNOWN"


class ConfidenceBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class CaseEvidence:
    """Evidence available to the heuristic case-status layer.

    This object intentionally contains reconstructed goods counts rather than
    raw source status codes. Item-level code 0/1/2 is never itself a case
    status or a legal cause.
    """

    application_number: str
    as_of_date: date
    filing_date: date | None = None
    prelim_pub_date: date | None = None
    registration_pub_date: date | None = None
    valid_until: date | None = None
    renewal_grace_end: date | None = None

    known_item_count: int = 0
    final_inactive_item_count: int = 0
    inactive_high_confidence_item_count: int = 0
    unknown_item_count: int = 0

    first_final_inactive_date: date | None = None
    total_final_inactive_date: date | None = None
    first_high_confidence_inactive_date: date | None = None

    renewal_or_restoration_observed: bool = False
    official_cause: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.known_item_count,
            self.final_inactive_item_count,
            self.inactive_high_confidence_item_count,
            self.unknown_item_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("goods counts must be non-negative")
        if self.final_inactive_item_count > self.known_item_count:
            raise ValueError("final inactive goods cannot exceed known goods")
        if self.inactive_high_confidence_item_count > self.known_item_count:
            raise ValueError("high-confidence inactive goods cannot exceed known goods")
        if self.unknown_item_count > self.known_item_count:
            raise ValueError("unknown goods cannot exceed known goods")
        if self.total_final_inactive_date and not self.all_known_goods_final_inactive:
            raise ValueError(
                "total_final_inactive_date requires all known goods to be final inactive"
            )

    @property
    def all_known_goods_final_inactive(self) -> bool:
        return (
            self.known_item_count > 0
            and self.final_inactive_item_count == self.known_item_count
            and self.unknown_item_count == 0
        )

    @property
    def some_goods_final_inactive(self) -> bool:
        return self.final_inactive_item_count > 0

    @property
    def inferred_scope(self) -> InferredScope:
        if self.all_known_goods_final_inactive:
            return InferredScope.TOTAL
        if self.some_goods_final_inactive:
            return InferredScope.PARTIAL
        return InferredScope.UNKNOWN


@dataclass(frozen=True)
class InferenceCandidate:
    rule_id: str
    inferred_status: str
    inferred_cause: str
    inferred_scope: InferredScope
    confidence_score: float
    confidence_band: ConfidenceBand
    evidence_summary: str
    evidence_refs: tuple[str, ...]
    model_version: str = MODEL_VERSION
    model_stage: str = MODEL_STAGE


@dataclass(frozen=True)
class InferenceEvaluation:
    application_number: str
    candidates: tuple[InferenceCandidate, ...]
    official_evidence_supersedes: bool
    superseding_official_cause: str | None
    model_version: str = MODEL_VERSION


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Feb 29 -> Feb 28 for a non-leap target year.
        return value.replace(month=2, day=28, year=value.year + years)


def _candidate(
    *,
    evidence: CaseEvidence,
    rule_id: str,
    status: str,
    cause: str,
    scope: InferredScope,
    confidence: float,
    band: ConfidenceBand,
    summary: str,
) -> InferenceCandidate:
    return InferenceCandidate(
        rule_id=rule_id,
        inferred_status=status,
        inferred_cause=cause,
        inferred_scope=scope,
        confidence_score=confidence,
        confidence_band=band,
        evidence_summary=summary,
        evidence_refs=evidence.evidence_refs,
    )


def _rule_r1(evidence: CaseEvidence) -> InferenceCandidate | None:
    if (
        evidence.filing_date is None
        or evidence.prelim_pub_date is not None
        or evidence.total_final_inactive_date is None
        or not evidence.all_known_goods_final_inactive
    ):
        return None
    if evidence.total_final_inactive_date < evidence.filing_date:
        return None
    if (
        evidence.total_final_inactive_date - evidence.filing_date
        > timedelta(days=EARLY_TOTAL_LOSS_WINDOW_DAYS)
    ):
        return None
    return _candidate(
        evidence=evidence,
        rule_id="R1",
        status="LIKELY_EARLY_TOTAL_TERMINATION",
        cause="LIKELY_VOLUNTARY_WITHDRAWAL",
        scope=InferredScope.TOTAL,
        confidence=0.85,
        band=ConfidenceBand.HIGH,
        summary=(
            "All reconstructed known goods became final inactive within the empirical "
            "early-filing window and no preliminary publication is observed."
        ),
    )


def _rule_r2(evidence: CaseEvidence) -> InferenceCandidate | None:
    if evidence.prelim_pub_date is None:
        return None
    if evidence.inferred_scope is not InferredScope.PARTIAL:
        return None
    return _candidate(
        evidence=evidence,
        rule_id="R2",
        status="LIKELY_PARTIAL_ADVERSE_OUTCOME",
        cause="LIKELY_PARTIAL_REFUSAL_OR_PARTIAL_ADVERSE_DECISION",
        scope=InferredScope.PARTIAL,
        confidence=0.65,
        band=ConfidenceBand.MEDIUM,
        summary=(
            "Preliminary publication exists and only part of the reconstructed durable "
            "goods universe is final inactive."
        ),
    )


def _rule_r3(evidence: CaseEvidence) -> InferenceCandidate | None:
    if (
        evidence.prelim_pub_date is None
        or evidence.registration_pub_date is not None
        or evidence.total_final_inactive_date is None
        or not evidence.all_known_goods_final_inactive
    ):
        return None
    long_delay_anchor = evidence.prelim_pub_date + timedelta(days=POST_PRELIM_LONG_DELAY_DAYS)
    if evidence.as_of_date < long_delay_anchor:
        return None
    if evidence.total_final_inactive_date < evidence.prelim_pub_date:
        return None
    return _candidate(
        evidence=evidence,
        rule_id="R3",
        status="LIKELY_POST_PUBLICATION_TOTAL_ADVERSE_OUTCOME",
        cause="LIKELY_OPPOSITION_LOSS_OR_OTHER_POST_PUBLICATION_TOTAL_ADVERSE_OUTCOME",
        scope=InferredScope.TOTAL,
        confidence=0.75,
        band=ConfidenceBand.MEDIUM,
        summary=(
            "Preliminary publication is observed, registration publication remains absent "
            "after the empirical long-delay window, and all reconstructed known goods are "
            "final inactive."
        ),
    )


def _rule_r4(evidence: CaseEvidence) -> InferenceCandidate | None:
    if evidence.prelim_pub_date is None or evidence.registration_pub_date is None:
        return None
    if evidence.inferred_scope is not InferredScope.PARTIAL:
        return None
    return _candidate(
        evidence=evidence,
        rule_id="R4",
        status="LIKELY_PARTIAL_REGISTRATION_OUTCOME",
        cause="LIKELY_PARTIAL_REGISTRATION_AFTER_ADVERSE_PROCEEDING",
        scope=InferredScope.PARTIAL,
        confidence=0.65,
        band=ConfidenceBand.MEDIUM,
        summary=(
            "Both preliminary and registration publication are observed while only part of "
            "the reconstructed durable goods universe is final inactive."
        ),
    )


def _rule_r5_or_r6(evidence: CaseEvidence) -> InferenceCandidate | None:
    loss_date = evidence.first_final_inactive_date
    registration_date = evidence.registration_pub_date
    if registration_date is None or loss_date is None or not evidence.some_goods_final_inactive:
        return None
    if loss_date < registration_date:
        return None
    three_year_mark = _add_years(registration_date, 3)
    if loss_date < three_year_mark:
        return _candidate(
            evidence=evidence,
            rule_id="R5",
            status="LIKELY_POST_REGISTRATION_ADVERSE_OUTCOME",
            cause="LIKELY_INVALIDATION_OR_VOLUNTARY_CANCELLATION",
            scope=evidence.inferred_scope,
            confidence=0.60,
            band=ConfidenceBand.MEDIUM,
            summary=(
                "Final goods loss is first observed after registration but before the "
                "three-year mark; non-use cancellation is therefore a weaker candidate."
            ),
        )
    return _candidate(
        evidence=evidence,
        rule_id="R6",
        status="LIKELY_POST_REGISTRATION_CANCELLATION_OUTCOME",
        cause="LIKELY_NON_USE_CANCELLATION_OR_OTHER_CANCELLATION",
        scope=evidence.inferred_scope,
        confidence=0.60,
        band=ConfidenceBand.MEDIUM,
        summary=(
            "Final goods loss is first observed at least three years after registration; "
            "non-use cancellation becomes a materially stronger candidate but is not proven."
        ),
    )


def _rule_r7(evidence: CaseEvidence) -> InferenceCandidate | None:
    inactive_date = evidence.first_high_confidence_inactive_date or evidence.first_final_inactive_date
    if (
        evidence.registration_pub_date is None
        or evidence.renewal_grace_end is None
        or inactive_date is None
        or evidence.renewal_or_restoration_observed
    ):
        return None
    if inactive_date <= evidence.renewal_grace_end:
        return None
    if (
        evidence.final_inactive_item_count == 0
        and evidence.inactive_high_confidence_item_count == 0
    ):
        return None
    return _candidate(
        evidence=evidence,
        rule_id="R7",
        status="LIKELY_EXPIRED_AFTER_RENEWAL_WINDOW",
        cause="LIKELY_NON_RENEWAL_EXPIRATION",
        scope=evidence.inferred_scope,
        confidence=0.80,
        band=ConfidenceBand.HIGH,
        summary=(
            "High-confidence or final goods inactivity is first observed after an externally "
            "supplied renewal/grace deadline and no renewal/restoration evidence is observed."
        ),
    )


def evaluate_case_status(evidence: CaseEvidence) -> InferenceEvaluation:
    """Evaluate empirical case-status candidates without mutating official facts.

    Official cause evidence supersedes heuristics. The function therefore returns
    no heuristic candidates when ``official_cause`` is supplied, while preserving
    that supersession explicitly in the result.
    """

    if evidence.official_cause:
        return InferenceEvaluation(
            application_number=evidence.application_number,
            candidates=(),
            official_evidence_supersedes=True,
            superseding_official_cause=evidence.official_cause,
        )

    candidates: list[InferenceCandidate] = []
    for rule in (_rule_r1, _rule_r2, _rule_r3, _rule_r4, _rule_r5_or_r6, _rule_r7):
        candidate = rule(evidence)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-item.confidence_score, item.rule_id))
    return InferenceEvaluation(
        application_number=evidence.application_number,
        candidates=tuple(candidates),
        official_evidence_supersedes=False,
        superseding_official_cause=None,
    )
