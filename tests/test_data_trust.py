from datetime import date, datetime, timezone

import pytest

from app.data_trust import (
    DATA_TRUST_VERSION,
    SILENCE_SEMANTICS,
    DataTrustEvidence,
    aggregate_data_trust,
    data_trust_contract,
    evaluate_data_trust,
)


def _healthy(**overrides):
    values = {
        "domain": "US_APPLICATION",
        "query_plane_ready": True,
        "source_identity_complete": True,
        "registered_corpus_complete": True,
        "source_verification_passed": True,
        "acceptance_status": "PASS",
        "coverage_through": date(2026, 8, 15),
        "required_coverage_through": date(2026, 8, 14),
        "source_supports_silence": True,
    }
    values.update(overrides)
    return DataTrustEvidence(**values)


def test_healthy_domain_is_trusted_for_silence_only_with_all_gates() -> None:
    result = evaluate_data_trust(_healthy())

    assert result.queryable is True
    assert result.complete is True
    assert result.fresh is True
    assert result.accepted is True
    assert result.trusted_for_silence is True
    payload = result.as_dict()
    assert payload["silence_semantics"] == SILENCE_SEMANTICS
    assert payload["legal_conclusion"] is False


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"query_plane_ready": False}, "QUERY_PLANE_NOT_READY"),
        ({"source_identity_complete": False}, "SOURCE_IDENTITY_INCOMPLETE"),
        ({"registered_corpus_complete": False}, "REGISTERED_CORPUS_INCOMPLETE"),
        ({"source_verification_passed": False}, "SOURCE_VERIFICATION_NOT_PASSED"),
        ({"acceptance_status": "FAIL"}, "DOMAIN_NOT_ACCEPTED"),
        ({"source_supports_silence": False}, "SOURCE_DOES_NOT_SUPPORT_SILENCE_INFERENCE"),
    ],
)
def test_any_required_gate_disables_trusted_silence(override, reason) -> None:
    result = evaluate_data_trust(_healthy(**override))

    assert result.trusted_for_silence is False
    assert reason in result.reason_codes


def test_stale_or_unknown_coverage_is_not_fresh() -> None:
    stale = evaluate_data_trust(
        _healthy(
            coverage_through="2026-08-10T00:00:00Z",
            required_coverage_through="2026-08-14T00:00:00+00:00",
        )
    )
    assert stale.fresh is False
    assert stale.trusted_for_silence is False
    assert "SOURCE_COVERAGE_STALE" in stale.reason_codes

    unknown = evaluate_data_trust(_healthy(coverage_through=None))
    assert unknown.fresh is False
    assert "COVERAGE_THROUGH_UNKNOWN" in unknown.reason_codes


def test_engine_compares_explicit_boundaries_instead_of_guessing_cadence() -> None:
    result = evaluate_data_trust(
        _healthy(
            coverage_through=datetime(2026, 8, 14, 12, tzinfo=timezone.utc),
            required_coverage_through=datetime(2026, 8, 14, 12, tzinfo=timezone.utc),
        )
    )

    assert result.fresh is True
    assert result.trusted_for_silence is True


def test_bad_freshness_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        evaluate_data_trust(_healthy(coverage_through="not-a-date"))


def test_pass_with_warnings_can_be_accepted_but_warnings_are_preserved() -> None:
    result = evaluate_data_trust(
        _healthy(
            acceptance_status="PASS_WITH_WARNINGS",
            warnings=("known_source_coverage_warning",),
        )
    )

    assert result.accepted is True
    assert result.warnings == ("known_source_coverage_warning",)


def test_multi_domain_aggregate_does_not_hide_one_untrusted_domain() -> None:
    cn = evaluate_data_trust(_healthy(domain="CN"))
    us = evaluate_data_trust(
        _healthy(domain="US_APPLICATION", source_supports_silence=False)
    )
    aggregate = aggregate_data_trust((cn, us))

    assert aggregate["domain_count"] == 2
    assert aggregate["all_queryable"] is True
    assert aggregate["all_complete"] is True
    assert aggregate["all_fresh"] is True
    assert aggregate["all_accepted"] is True
    assert aggregate["all_trusted_for_silence"] is False
    assert aggregate["legal_conclusion"] is False


def test_duplicate_domain_aggregate_is_rejected() -> None:
    row = evaluate_data_trust(_healthy(domain="CN"))
    with pytest.raises(ValueError, match="duplicate domain"):
        aggregate_data_trust((row, row))


def test_contract_freezes_silence_as_source_observation_not_legal_absence() -> None:
    contract = data_trust_contract()

    assert contract["version"] == DATA_TRUST_VERSION
    assert contract["freshness_policy"]["engine_does_not_guess_source_cadence"] is True
    assert contract["acceptance_policy"]["execution_success_alone_is_not_acceptance"] is True
    assert contract["silence_policy"]["semantics"] == SILENCE_SEMANTICS
    assert contract["silence_policy"]["absence_is_not_legal_nonexistence"] is True
    assert contract["silence_policy"]["absence_does_not_authorize_action"] is True
    assert contract["legal_conclusion"] is False
