from datetime import date

import pytest

from app.cn.case_status_inference import (
    MODEL_STAGE,
    MODEL_VERSION,
    CaseEvidence,
    InferredScope,
    evaluate_case_status,
)


def _rule_ids(evidence: CaseEvidence) -> set[str]:
    return {item.rule_id for item in evaluate_case_status(evidence).candidates}


def test_r1_early_total_loss_requires_no_preliminary_publication() -> None:
    evidence = CaseEvidence(
        application_number="12345678",
        as_of_date=date(2026, 8, 9),
        filing_date=date(2025, 1, 1),
        known_item_count=3,
        final_inactive_item_count=3,
        total_final_inactive_date=date(2025, 2, 15),
        first_final_inactive_date=date(2025, 2, 15),
    )

    evaluation = evaluate_case_status(evidence)

    assert "R1" in {item.rule_id for item in evaluation.candidates}
    r1 = next(item for item in evaluation.candidates if item.rule_id == "R1")
    assert r1.inferred_scope is InferredScope.TOTAL
    assert r1.inferred_cause == "LIKELY_VOLUNTARY_WITHDRAWAL"
    assert r1.model_version == MODEL_VERSION
    assert r1.model_stage == MODEL_STAGE == "EMPIRICAL"


def test_r1_does_not_fire_when_preliminary_publication_exists() -> None:
    evidence = CaseEvidence(
        application_number="12345678",
        as_of_date=date(2026, 8, 9),
        filing_date=date(2025, 1, 1),
        prelim_pub_date=date(2025, 2, 1),
        known_item_count=2,
        final_inactive_item_count=2,
        total_final_inactive_date=date(2025, 2, 15),
        first_final_inactive_date=date(2025, 2, 15),
    )

    assert "R1" not in _rule_ids(evidence)


def test_r2_partial_loss_after_preliminary_publication() -> None:
    evidence = CaseEvidence(
        application_number="23456789",
        as_of_date=date(2026, 8, 9),
        prelim_pub_date=date(2024, 6, 1),
        known_item_count=10,
        final_inactive_item_count=3,
        first_final_inactive_date=date(2024, 7, 1),
    )

    assert "R2" in _rule_ids(evidence)


def test_r3_total_loss_after_long_post_publication_delay_without_registration() -> None:
    evidence = CaseEvidence(
        application_number="34567890",
        as_of_date=date(2026, 8, 9),
        prelim_pub_date=date(2024, 1, 1),
        known_item_count=4,
        final_inactive_item_count=4,
        first_final_inactive_date=date(2025, 3, 1),
        total_final_inactive_date=date(2025, 3, 1),
    )

    assert "R3" in _rule_ids(evidence)


def test_r3_is_contradicted_by_registration_publication() -> None:
    evidence = CaseEvidence(
        application_number="34567890",
        as_of_date=date(2026, 8, 9),
        prelim_pub_date=date(2024, 1, 1),
        registration_pub_date=date(2025, 4, 1),
        known_item_count=4,
        final_inactive_item_count=4,
        first_final_inactive_date=date(2025, 3, 1),
        total_final_inactive_date=date(2025, 3, 1),
    )

    assert "R3" not in _rule_ids(evidence)


def test_r4_partial_loss_with_registration_publication() -> None:
    evidence = CaseEvidence(
        application_number="45678901",
        as_of_date=date(2026, 8, 9),
        prelim_pub_date=date(2023, 4, 1),
        registration_pub_date=date(2023, 8, 1),
        known_item_count=8,
        final_inactive_item_count=2,
        first_final_inactive_date=date(2023, 7, 1),
    )

    assert "R4" in _rule_ids(evidence)


def test_r5_before_three_year_mark_and_r6_after_three_year_mark() -> None:
    before = CaseEvidence(
        application_number="56789012",
        as_of_date=date(2026, 8, 9),
        registration_pub_date=date(2020, 6, 30),
        known_item_count=5,
        final_inactive_item_count=5,
        first_final_inactive_date=date(2023, 6, 29),
        total_final_inactive_date=date(2023, 6, 29),
    )
    after = CaseEvidence(
        application_number="67890123",
        as_of_date=date(2026, 8, 9),
        registration_pub_date=date(2020, 6, 30),
        known_item_count=5,
        final_inactive_item_count=5,
        first_final_inactive_date=date(2023, 6, 30),
        total_final_inactive_date=date(2023, 6, 30),
    )

    assert "R5" in _rule_ids(before)
    assert "R6" not in _rule_ids(before)
    assert "R6" in _rule_ids(after)
    assert "R5" not in _rule_ids(after)


def test_r7_requires_explicit_renewal_grace_deadline_and_no_restoration() -> None:
    evidence = CaseEvidence(
        application_number="78901234",
        as_of_date=date(2026, 8, 9),
        registration_pub_date=date(2015, 1, 1),
        renewal_grace_end=date(2025, 7, 1),
        known_item_count=2,
        final_inactive_item_count=2,
        first_final_inactive_date=date(2025, 8, 1),
        first_high_confidence_inactive_date=date(2025, 8, 1),
        total_final_inactive_date=date(2025, 8, 1),
    )

    assert "R7" in _rule_ids(evidence)

    restored = CaseEvidence(
        **{
            **evidence.__dict__,
            "renewal_or_restoration_observed": True,
        }
    )
    assert "R7" not in _rule_ids(restored)


def test_official_cause_supersedes_all_heuristics() -> None:
    evidence = CaseEvidence(
        application_number="89012345",
        as_of_date=date(2026, 8, 9),
        filing_date=date(2025, 1, 1),
        known_item_count=1,
        final_inactive_item_count=1,
        first_final_inactive_date=date(2025, 2, 1),
        total_final_inactive_date=date(2025, 2, 1),
        official_cause="OFFICIAL_WITHDRAWAL_NOTICE",
        evidence_refs=("document:sha256:abc",),
    )

    result = evaluate_case_status(evidence)

    assert result.candidates == ()
    assert result.official_evidence_supersedes is True
    assert result.superseding_official_cause == "OFFICIAL_WITHDRAWAL_NOTICE"


def test_unknown_goods_prevent_total_scope_claim() -> None:
    evidence = CaseEvidence(
        application_number="90123456",
        as_of_date=date(2026, 8, 9),
        known_item_count=4,
        final_inactive_item_count=4,
        unknown_item_count=1,
    )

    assert evidence.inferred_scope is InferredScope.PARTIAL
    assert not evidence.all_known_goods_final_inactive


def test_invalid_total_loss_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="total_final_inactive_date"):
        CaseEvidence(
            application_number="01234567",
            as_of_date=date(2026, 8, 9),
            known_item_count=3,
            final_inactive_item_count=2,
            total_final_inactive_date=date(2026, 1, 1),
        )
