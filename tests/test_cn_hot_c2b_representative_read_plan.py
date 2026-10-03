"""#843 representative-read plan contract tests; no production access."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "freeze_cn_hot_c2b_representative_read_plan.py"
)
SPEC = importlib.util.spec_from_file_location("cn_hot_c2b_representative_read_plan", SCRIPT)
assert SPEC and SPEC.loader
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


def accepted_inputs():
    prior = {
        "issue": 843,
        "parent": 837,
        "status": "FROZEN_READ_ONLY_PLANS_NO_APPLY",
        "control_epoch": {
            "processing_packages": 0,
            "successful_packages": 86,
            "max_success_sequence": 2993,
            "coverage_date": "2026-08-31",
            "running_jobs": 0,
            "watermark": "2026-08-31:2993:86",
        },
        "read_canary": {
            "route": planner.ROUTE,
            "source_table": "cn_case_current",
            "serving_endpoint": "SOURCE_ONLY",
            "serving_route_activation_authorized": False,
        },
        "merge_capacity": {"start_merges_or_optimize_authorized": False},
        "production_mutation_performed": False,
    }
    receipt = {
        "version": "CN_HOT_C2B_EXACT_READ_CANARY_R1",
        "status": "READ_ONLY_EXACT_KEY_PARITY_ACCEPTED_NOT_SERVING",
        "plan_sha256": planner.PRIOR_PLAN_SHA,
        "route_activation_authorized": False,
        "api_started": False,
        "writer_started": False,
        "merge_started": False,
        "source_cleaned": False,
        "probes": [
            {
                "key": key,
                "result_count": 1,
                "route_output_equal": True,
                "serving_epoch_equal": True,
            }
            for key in ("10002014", '"10002014"')
        ],
    }
    return prior, receipt


def make_plan(*, prior=None, receipt=None):
    accepted_prior, accepted_receipt = accepted_inputs()
    return planner.build_plan(
        prior or accepted_prior,
        receipt or accepted_receipt,
        route_sha=planner.ROUTE_SHA,
        exact_canary_operator_sha=planner.EXACT_CANARY_OPERATOR_SHA,
    )


def test_plan_is_bounded_serial_and_never_grants_serving():
    result = make_plan()
    assert result["status"] == "FROZEN_REPRESENTATIVE_READ_PLAN_NO_APPLY"
    assert result["serving_endpoint_before"] == result["serving_endpoint_after"] == "SOURCE_ONLY"
    assert [item["stratum"] for item in result["ranges"]] == [
        "literal_quote_sentinel",
        "direct_numeric_1",
        "direct_numeric_2",
        "direct_numeric_9",
        "madrid_g_family",
    ]
    assert all(item["page_size"] == 50 and item["fetch_limit"] == 51 for item in result["ranges"])
    budget = result["execution_budget"]
    assert budget["serial_only"] is True
    assert budget["total_query_ceiling"] == 30
    assert budget["read_limits"] == planner.READ_LIMITS
    assert result["acceptance"]["route_activation_authorized_on_success"] is False
    assert result["rollback_contract"]["canary_rollback_action"] == "NONE_SOURCE_REMAINS_SERVING"
    assert not any(result["explicit_non_authority"].values())
    assert result["production_mutation_performed"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "PASS", "no-serving receipt"),
        ("plan_sha256", "0" * 64, "no-serving receipt"),
        ("route_activation_authorized", True, "no-serving receipt"),
        ("merge_started", True, "no-serving receipt"),
    ],
)
def test_exact_canary_evidence_must_remain_no_serving(field, value, message):
    prior, receipt = accepted_inputs()
    receipt[field] = value
    with pytest.raises(RuntimeError, match=message):
        make_plan(prior=prior, receipt=receipt)


def test_exact_canary_requires_both_distinct_keys_and_equal_results():
    prior, receipt = accepted_inputs()
    receipt["probes"][1]["key"] = "10002014"
    with pytest.raises(RuntimeError, match="no-serving receipt"):
        make_plan(prior=prior, receipt=receipt)
    prior, receipt = accepted_inputs()
    receipt["probes"][0]["route_output_equal"] = False
    with pytest.raises(RuntimeError, match="no-serving receipt"):
        make_plan(prior=prior, receipt=receipt)


def test_prior_plan_and_bound_code_hashes_fail_closed():
    prior, receipt = accepted_inputs()
    prior["read_canary"]["serving_endpoint"] = "TARGET"
    with pytest.raises(RuntimeError, match="source-only"):
        make_plan(prior=prior, receipt=receipt)
    prior, receipt = accepted_inputs()
    with pytest.raises(RuntimeError, match="route code SHA"):
        planner.build_plan(
            prior,
            receipt,
            route_sha="0" * 64,
            exact_canary_operator_sha=planner.EXACT_CANARY_OPERATOR_SHA,
        )
    with pytest.raises(RuntimeError, match="operator SHA"):
        planner.build_plan(
            prior,
            receipt,
            route_sha=planner.ROUTE_SHA,
            exact_canary_operator_sha="0" * 64,
        )
