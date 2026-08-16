from datetime import date, datetime, timezone

import pytest

from app.fact_event_envelope import (
    FACT_EVENT_ENVELOPE_VERSION,
    NORMALIZATION_SEMANTICS,
    ProvenanceRef,
    SubjectRef,
    build_fact_event_envelope,
    fact_event_envelope_contract,
)


def test_cn_and_us_can_share_outer_envelope_without_legal_equivalence() -> None:
    cn = build_fact_event_envelope(
        jurisdiction="CN",
        resource_kind="EVENT",
        semantic_family="PUBLICATION",
        subject=SubjectRef("TRADEMARK_CASE", "CN-123"),
        provenance=ProvenanceRef(
            source_authority="CNIPA",
            source_domain="CN",
            source_package_id="pkg-cn",
            source_rank=100,
            source_effective_at=date(2026, 8, 1),
            source_file="cn.xml",
            source_start_line=10,
            source_end_line=12,
            source_row_hash="abc",
        ),
        source_type="PRELIMINARY_PUBLICATION_OBSERVED",
        normalized_type="PUBLICATION_OBSERVED",
        normalization_confidence=1.0,
        observed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        payload={"prelim_pub_issue": "2026-08"},
    )
    us = build_fact_event_envelope(
        jurisdiction="US",
        resource_kind="EVENT",
        semantic_family="PUBLICATION",
        subject=SubjectRef("TRADEMARK_CASE", "US-987"),
        provenance=ProvenanceRef(
            source_authority="USPTO",
            source_domain="US_APPLICATION",
            source_package_id="pkg-us",
            source_rank=200,
        ),
        source_code="CPC",
        source_text="Published for opposition",
        normalized_type="PUBLICATION_OBSERVED",
        normalization_confidence=0.95,
        payload={"status_code": "700"},
    )

    assert cn["envelope_version"] == FACT_EVENT_ENVELOPE_VERSION
    assert us["envelope_version"] == FACT_EVENT_ENVELOPE_VERSION
    assert cn["semantic_family"] == us["semantic_family"] == "PUBLICATION"
    assert cn["payload"] != us["payload"]
    for envelope in (cn, us):
        assert envelope["normalization"]["semantics"] == NORMALIZATION_SEMANTICS
        assert envelope["normalization"]["cross_jurisdiction_legal_equivalence"] is False
        assert envelope["legal_conclusion"] is False
        assert envelope["actionability"] == "SOURCE_FACT_ONLY"


def test_source_provenance_is_preserved_alongside_normalization() -> None:
    envelope = build_fact_event_envelope(
        jurisdiction="US",
        resource_kind="FACT",
        semantic_family="OWNERSHIP_RECORDATION",
        subject=SubjectRef("TRADEMARK_CASE", "12345678"),
        provenance=ProvenanceRef(
            source_authority="USPTO Assignment",
            source_domain="US_ASSIGNMENT",
            source_package_id="pkg-1",
            source_rank=42,
            source_file="assignment.xml",
            source_start_line=8,
            source_end_line=9,
            source_row_hash="rowhash",
        ),
        source_type="RECORDED_ASSIGNMENT_FACT",
        normalized_type="OWNERSHIP_RECORDATION_FACT",
        normalization_confidence=1.0,
        payload={"reel_frame": "1234/5678"},
    )

    assert envelope["observation"]["source_type"] == "RECORDED_ASSIGNMENT_FACT"
    assert envelope["normalization"]["normalized_type"] == "OWNERSHIP_RECORDATION_FACT"
    assert envelope["provenance"]["source_file"] == "assignment.xml"
    assert envelope["provenance"]["source_start_line"] == 8
    assert envelope["payload"]["reel_frame"] == "1234/5678"
    assert envelope["legal_conclusion"] is False


def test_normalized_type_cannot_discard_all_source_meaning() -> None:
    with pytest.raises(ValueError, match="requires retained source_type"):
        build_fact_event_envelope(
            jurisdiction="US",
            resource_kind="EVENT",
            semantic_family="EXAMINATION",
            subject=SubjectRef("TRADEMARK_CASE", "123"),
            provenance=ProvenanceRef("USPTO", "US_APPLICATION"),
            normalized_type="OFFICE_ACTION",
        )


def test_envelope_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="subject_key is required"):
        SubjectRef("TRADEMARK_CASE", "").as_dict()
    with pytest.raises(ValueError, match="source_authority is required"):
        ProvenanceRef("", "CN").as_dict()
    with pytest.raises(ValueError, match="source_end_line must not precede"):
        ProvenanceRef("CNIPA", "CN", source_start_line=20, source_end_line=10).as_dict()
    with pytest.raises(ValueError, match="unsupported resource_kind"):
        build_fact_event_envelope(
            jurisdiction="CN",
            resource_kind="LEGAL_OPINION",
            semantic_family="OTHER",
            subject=SubjectRef("TRADEMARK_CASE", "1"),
            provenance=ProvenanceRef("CNIPA", "CN"),
        )
    with pytest.raises(ValueError, match="unsupported semantic_family"):
        build_fact_event_envelope(
            jurisdiction="CN",
            resource_kind="FACT",
            semantic_family="ACTIVE_DEAD_LEGAL_STATUS",
            subject=SubjectRef("TRADEMARK_CASE", "1"),
            provenance=ProvenanceRef("CNIPA", "CN"),
        )
    with pytest.raises(ValueError, match="between 0 and 1"):
        build_fact_event_envelope(
            jurisdiction="CN",
            resource_kind="EVENT",
            semantic_family="OTHER",
            subject=SubjectRef("TRADEMARK_CASE", "1"),
            provenance=ProvenanceRef("CNIPA", "CN"),
            source_type="RAW_EVENT",
            normalized_type="OTHER_EVENT",
            normalization_confidence=1.1,
        )


def test_contract_keeps_alert_brain_and_workflow_outside_source_envelope() -> None:
    contract = fact_event_envelope_contract()

    assert contract["version"] == FACT_EVENT_ENVELOPE_VERSION
    assert contract["normalization"]["cross_jurisdiction_legal_equivalence"] is False
    assert contract["normalization"]["automatic_legal_status_equivalence"] is False
    assert contract["normalization"]["source_specific_payload_remains_authoritative"] is True
    assert contract["layer_boundary"]["alert_or_signal_is_not_implicitly_source_fact"] is True
    assert contract["layer_boundary"]["brain_reasoning_is_outside_envelope"] is True
    assert contract["layer_boundary"]["business_workflow_is_outside_envelope"] is True
    assert contract["legal_conclusion"] is False
    assert contract["consumer_writeback"] is False
