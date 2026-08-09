from app.us.semantic_readiness import evaluate_semantic_readiness


def test_semantic_readiness_requires_accepted_corpus() -> None:
    result = evaluate_semantic_readiness(
        pipeline={"state": "REPLAY_READY", "ready": False},
        references={"status": "PASS"},
    )
    assert result["state"] == "DATA_CORPUS_NOT_ACCEPTED"
    assert result["legal_interpretation_produced"] is False


def test_semantic_readiness_requires_official_references() -> None:
    result = evaluate_semantic_readiness(
        pipeline={"state": "ACCEPTED", "ready": True},
        references={
            "status": "NOT_READY",
            "status_reference": {"reason_codes": ["unmapped"]},
            "event_reference": {"reason_codes": []},
        },
    )
    assert result["state"] == "OFFICIAL_REFERENCES_NOT_READY"


def test_semantic_readiness_only_opens_rule_research_gate() -> None:
    result = evaluate_semantic_readiness(
        pipeline={"state": "ACCEPTED", "ready": True},
        references={
            "status": "PASS",
            "status_reference": {"reason_codes": []},
            "event_reference": {"reason_codes": []},
        },
    )
    assert result["state"] == "READY_FOR_RULE_RESEARCH"
    assert result["ready_for_rule_research"] is True
    assert result["legal_interpretation_produced"] is False
