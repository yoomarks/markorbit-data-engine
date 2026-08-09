import csv
from pathlib import Path

import pytest

from app.cn.case_status_ground_truth import (
    LABEL_CONFIRMED,
    LABEL_INSUFFICIENT,
    LABEL_NOT_REVIEWED,
    LABEL_REJECTED,
    REVIEW_PACKET_VERSION,
    build_review_rows,
    read_review_csv,
    score_review_rows,
    write_review_csv,
)


def _audit():
    return {
        "audit": "CN_CASE_STATUS_INFERENCE_HISTORICAL_VALIDATION",
        "audit_version": "AUDIT_V1",
        "model_version": "MODEL_V1",
        "data_clock": {
            "coverage_date": "2023-01-31",
            "as_of_date": "2023-01-31",
        },
        "samples_by_rule": {
            "R6": [
                {
                    "application_number": "B200",
                    "inferred_status": "POST_REGISTRATION",
                    "inferred_cause": "NON_USE_OR_OTHER_CANCELLATION",
                    "inferred_scope": "PARTIAL",
                    "confidence_score": 0.6,
                    "filing_date": "2015-01-01",
                    "registration_pub_date": "2016-01-01",
                    "first_final_inactive_date": "2020-02-01",
                    "known_item_count": 4,
                    "final_inactive_item_count": 1,
                }
            ],
            "R1": [
                {
                    "application_number": "A100",
                    "inferred_status": "EARLY_TOTAL_TERMINATION",
                    "inferred_cause": "VOLUNTARY_WITHDRAWAL",
                    "inferred_scope": "TOTAL",
                    "confidence_score": 0.85,
                    "filing_date": "2020-01-01",
                    "first_final_inactive_date": "2020-02-01",
                    "total_final_inactive_date": "2020-02-10",
                    "known_item_count": 3,
                    "final_inactive_item_count": 3,
                }
            ],
        },
    }


def test_review_rows_are_deterministic_and_rule_sorted():
    first = build_review_rows(_audit())
    second = build_review_rows(_audit())
    assert first == second
    assert [row["rule_id"] for row in first] == ["R1", "R6"]
    assert all(row["review_label"] == LABEL_NOT_REVIEWED for row in first)
    assert all(len(row["review_id"]) == 24 for row in first)


def test_duplicate_sample_is_deduplicated_by_review_id():
    audit = _audit()
    audit["samples_by_rule"]["R1"].append(dict(audit["samples_by_rule"]["R1"][0]))
    rows = build_review_rows(audit)
    assert len(rows) == 2


def test_review_csv_round_trip_is_utf8_excel_friendly(tmp_path: Path):
    rows = build_review_rows(_audit())
    rows[0]["notes"] = "官方公告已核对"
    path = tmp_path / "review.csv"
    write_review_csv(path, rows)

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    restored = read_review_csv(path)
    assert restored[0]["notes"] == "官方公告已核对"


def test_score_requires_official_source_for_decisive_labels():
    rows = build_review_rows(_audit())
    rows[0]["review_label"] = LABEL_CONFIRMED
    with pytest.raises(ValueError, match="requires official_source_ref"):
        score_review_rows(rows)


def test_score_precision_excludes_insufficient_and_unreviewed():
    rows = build_review_rows(_audit())
    rows[0]["review_label"] = LABEL_CONFIRMED
    rows[0]["official_source_ref"] = "CNIPA:notice-A"
    rows[1]["review_label"] = LABEL_INSUFFICIENT

    extra = dict(rows[1])
    extra["review_id"] = "f" * 24
    extra["rule_id"] = "R7"
    extra["review_label"] = LABEL_REJECTED
    extra["official_source_ref"] = "CNIPA:notice-B"
    rows.append(extra)

    unreviewed = dict(rows[1])
    unreviewed["review_id"] = "e" * 24
    unreviewed["rule_id"] = "R8"
    unreviewed["review_label"] = LABEL_NOT_REVIEWED
    rows.append(unreviewed)

    score = score_review_rows(rows)
    assert score["overall"]["reviewed"] == 3
    assert score["overall"]["decisive"] == 2
    assert score["overall"]["confirmed"] == 1
    assert score["overall"]["rejected"] == 1
    assert score["overall"]["precision"] == 0.5
    assert score["overall"]["insufficient_evidence"] == 1
    assert score["overall"]["unreviewed"] == 1
    assert score["ground_truth_readiness"]["decision"] == "MANUAL_MODEL_REVIEW_REQUIRED"


def test_invalid_label_is_rejected():
    rows = build_review_rows(_audit())
    rows[0]["review_label"] = "MAYBE"
    with pytest.raises(ValueError, match="invalid review_label"):
        score_review_rows(rows)


def test_duplicate_review_id_is_rejected():
    rows = build_review_rows(_audit())
    rows[1]["review_id"] = rows[0]["review_id"]
    with pytest.raises(ValueError, match="duplicate review_id"):
        score_review_rows(rows)


def test_mixed_model_versions_are_rejected():
    rows = build_review_rows(_audit())
    rows[1]["model_version"] = "MODEL_V2"
    with pytest.raises(ValueError, match="mixes multiple model versions"):
        score_review_rows(rows)


def test_review_csv_requires_full_schema(tmp_path: Path):
    path = tmp_path / "broken.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id"])
        writer.writeheader()
        writer.writerow({"review_id": "abc"})
    with pytest.raises(ValueError, match="missing columns"):
        read_review_csv(path)


def test_packet_version_is_frozen_in_generated_rows():
    rows = build_review_rows(_audit())
    assert {row["review_packet_version"] for row in rows} == {REVIEW_PACKET_VERSION}
