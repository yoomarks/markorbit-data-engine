from datetime import date

import pytest

from app.cn.audit_case_status_inference import (
    SAMPLE_STRATEGY,
    _case_batch_sql,
    row_to_evidence,
    summarize_rows,
    validate_as_of_date,
)
from app.cn.case_status_inference import CaseEvidence, evaluate_case_status


def _row(**overrides):
    row = {
        "application_number": "12345678",
        "filing_date": date(2020, 1, 1),
        "prelim_pub_date": None,
        "registration_pub_date": None,
        "valid_until": None,
        "known_item_count": 3,
        "final_inactive_item_count": 3,
        "inactive_high_confidence_item_count": 0,
        "unknown_item_count": 0,
        "dated_final_item_count": 3,
        "first_dated_final_inactive_date": date(2020, 2, 1),
        "last_dated_final_inactive_date": date(2020, 2, 10),
        "first_dated_high_confidence_inactive_date": None,
    }
    row.update(overrides)
    return row


def test_as_of_defaults_to_data_coverage_not_wall_clock() -> None:
    coverage = date(2023, 1, 31)
    assert validate_as_of_date(None, coverage) == coverage
    with pytest.raises(ValueError, match="exceeds CN data coverage"):
        validate_as_of_date(date(2026, 8, 9), coverage)


def test_total_loss_date_requires_complete_dated_status_change_lineage() -> None:
    evidence = row_to_evidence(
        _row(dated_final_item_count=2),
        as_of_date=date(2023, 1, 31),
    )
    assert evidence.first_final_inactive_date == date(2020, 2, 1)
    assert evidence.total_final_inactive_date is None


def test_summary_records_r1_hit_and_empirical_promotion_gate() -> None:
    result = summarize_rows(
        [_row()],
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
        sample_per_rule=2,
    )
    assert result["status"] == "PASS"
    assert result["rule_hits"] == {"R1": 1}
    assert result["data_clock"]["wall_clock_time_used"] is False
    assert result["sampling"] == {
        "strategy": SAMPLE_STRATEGY,
        "sample_per_rule": 2,
        "scan_order_independent": True,
    }
    assert result["promotion_decision"] == "NOT_ELIGIBLE_WITHOUT_MANUAL_GROUND_TRUTH"


def test_rule_samples_are_scan_order_independent() -> None:
    rows = [_row(application_number=f"APP-{index:03d}") for index in range(30)]
    forward = summarize_rows(
        rows,
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
        sample_per_rule=5,
    )
    reverse = summarize_rows(
        list(reversed(rows)),
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
        sample_per_rule=5,
    )
    assert forward["samples_by_rule"] == reverse["samples_by_rule"]
    assert len(forward["samples_by_rule"]["R1"]) == 5
    sampled = {row["application_number"] for row in forward["samples_by_rule"]["R1"]}
    assert sampled != {f"APP-{index:03d}" for index in range(5)}


def test_zero_sample_limit_keeps_counts_and_empty_rule_sample_bucket() -> None:
    result = summarize_rows(
        [_row(application_number="A"), _row(application_number="B")],
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
        sample_per_rule=0,
    )
    assert result["rule_hits"] == {"R1": 2}
    assert result["samples_by_rule"] == {"R1": []}


def test_summary_warns_when_final_loss_has_no_dated_status_change() -> None:
    result = summarize_rows(
        [
            _row(
                dated_final_item_count=0,
                first_dated_final_inactive_date=None,
                last_dated_final_inactive_date=None,
            )
        ],
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
    )
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["rule_hits"] == {}
    assert result["evidence_quality"]["final_loss_without_dated_status_change"] == 1
    assert (
        result["evidence_quality"]["total_final_scope_without_complete_dated_item_lineage"]
        == 1
    )


def test_registered_partial_case_no_longer_hits_broad_r2() -> None:
    evidence = CaseEvidence(
        application_number="87654321",
        as_of_date=date(2023, 1, 31),
        prelim_pub_date=date(2015, 8, 1),
        registration_pub_date=date(2016, 1, 1),
        known_item_count=4,
        final_inactive_item_count=1,
        first_final_inactive_date=date(2020, 2, 1),
    )
    rules = {candidate.rule_id for candidate in evaluate_case_status(evidence).candidates}
    assert "R2" not in rules
    assert rules == {"R4", "R6"}


def test_summary_surfaces_remaining_r4_r6_cause_overlap_for_validation() -> None:
    row = _row(
        application_number="87654321",
        filing_date=date(2015, 1, 1),
        prelim_pub_date=date(2015, 8, 1),
        registration_pub_date=date(2016, 1, 1),
        known_item_count=4,
        final_inactive_item_count=1,
        dated_final_item_count=1,
        first_dated_final_inactive_date=date(2020, 2, 1),
        last_dated_final_inactive_date=date(2020, 2, 1),
    )
    result = summarize_rows(
        [row],
        as_of_date=date(2023, 1, 31),
        coverage_date=date(2023, 1, 31),
    )
    assert result["rule_hits"] == {"R4": 1, "R6": 1}
    assert result["overlap"]["cases_with_multiple_distinct_causes"] == 1
    assert "multiple_heuristic_causes_for_same_case" in result["warnings"]


def test_batch_sql_uses_true_status_changes_not_first_observation_as_dates() -> None:
    sql = _case_batch_sql(after_application_number="G123'A", batch_size=321)
    assert "c.application_number > 'G123''A'" in sql
    assert "LIMIT 321" in sql
    assert "obs.transition_type = 'STATUS_CHANGED'" in sql
    assert "obs.new_operational_effect = 'INACTIVE_CONFIRMED'" in sql
    assert "FIRST_OBSERVED" not in sql
