from app.domain_adapter import DOMAIN_LIFECYCLE, domain_adapter_contract


def test_domain_adapter_lifecycle_is_frozen_in_safe_order() -> None:
    assert DOMAIN_LIFECYCLE == (
        "DISCOVER",
        "REGISTER_SOURCE",
        "VERIFY_SOURCE",
        "PARSE",
        "STAGE",
        "NORMALIZE",
        "PUBLISH",
        "EMIT_EVENTS",
        "AUDIT",
        "ACCEPT",
    )


def test_domain_adapter_contract_keeps_engine_and_jurisdiction_boundaries_separate() -> None:
    contract = domain_adapter_contract()

    assert contract["version"] == "MARKORBIT_DOMAIN_ADAPTER_V1"
    assert contract["required_invariants"]["source_verification_before_parse"] is True
    assert contract["required_invariants"]["deterministic_replay"] is True
    assert contract["required_invariants"]["acceptance_fails_closed"] is True
    assert contract["required_invariants"]["legal_conclusion"] is False
    assert "source_specific_parser" in contract["adapter_owned"]
    assert "jurisdiction_semantics_guards" in contract["adapter_owned"]
    assert "durable_work_units" in contract["engine_owned"]
    assert "checkpoint_and_resume" in contract["engine_owned"]
