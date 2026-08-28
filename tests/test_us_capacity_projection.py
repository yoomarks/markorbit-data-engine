from app.us_capacity_projection import evaluate_projection


GIB = 1024**3


def _capacity() -> dict[str, int]:
    return {
        "hot_free_bytes": 1200 * GIB,
        "hot_total_bytes": 1900 * GIB,
        "cold_free_bytes": 2700 * GIB,
        "cold_required_free_bytes": 300 * GIB,
    }


def _pilot(*, hot_gib: int = 8, warm_gib: int = 2) -> dict[str, object]:
    first = hot_gib // 2
    second = hot_gib - first
    return {
        "receipt_identity": "pilot:001",
        "raw_bytes": 10 * GIB,
        "warm_bytes": warm_gib * GIB,
        "hot_bytes": hot_gib * GIB,
        "hot_bytes_by_table_family": {
            "case": first * GIB,
            "party_and_scope": second * GIB,
        },
        "rows": 1_000_000,
    }


def test_projection_requires_real_bounded_pilot_before_authorizing_full_corpus() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
        }
    )
    assert result["status"] == "PILOT_REQUIRED"
    assert result["safe"] is False
    assert result["full_corpus_import_authorized"] is False
    assert result["issues"] == [{"type": "BOUNDED_PILOT_RECEIPT_REQUIRED"}]


def test_measured_pilot_can_authorize_when_hot_warm_and_cold_budgets_fit() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "policy": {"hot_floor_percent": 30},
            "pilot": _pilot(),
        }
    )
    assert result["status"] == "GO"
    assert result["full_corpus_import_authorized"] is True
    assert result["projection"]["projected_hot_bytes"] == 400 * GIB
    assert result["projection"]["projected_warm_bytes"] == 100 * GIB
    assert result["projection"]["required_cold_and_warm_bytes"] == 600 * GIB
    assert result["projection"]["projected_hot_bytes_by_table_family"] == {
        "case": 200 * GIB,
        "party_and_scope": 200 * GIB,
    }
    assert result["projection"]["projected_rows"] == 50_000_000
    assert result["projection"]["estimated_without_pilot"] is False
    assert result["capacity"]["hot_budget_bytes"] == 630 * GIB


def test_projection_blocks_when_measured_hot_amplification_exceeds_floor_budget() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "pilot": _pilot(hot_gib=16),
        }
    )
    assert result["status"] == "NO_GO"
    assert result["full_corpus_import_authorized"] is False
    assert result["projection"]["projected_hot_bytes"] == 800 * GIB
    assert result["issues"][0]["type"] == "PROJECTED_HOT_EXCEEDS_BUDGET"


def test_projection_blocks_when_raw_plus_warm_does_not_fit_cold_budget() -> None:
    capacity = _capacity()
    capacity["cold_free_bytes"] = 650 * GIB
    capacity["cold_required_free_bytes"] = 100 * GIB
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": capacity,
            "pilot": _pilot(hot_gib=2, warm_gib=2),
        }
    )
    assert result["status"] == "NO_GO"
    assert result["projection"]["cold_safe"] is False
    assert any(
        issue["type"] == "PROJECTED_COLD_AND_WARM_EXCEED_BUDGET"
        for issue in result["issues"]
    )


def test_projection_rejects_invalid_or_unbound_pilot_inputs() -> None:
    pilot = _pilot()
    pilot.pop("receipt_identity")
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "pilot": pilot,
        }
    )
    assert result["status"] == "BLOCKED"
    assert result["safe"] is False
    assert result["issues"][0]["type"] == "PILOT_INVALID"


def test_projection_rejects_table_family_bytes_that_do_not_match_hot_total() -> None:
    pilot = _pilot()
    pilot["hot_bytes_by_table_family"] = {"case": 1 * GIB}
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "pilot": pilot,
        }
    )
    assert result["status"] == "BLOCKED"
    assert "sum exactly" in result["issues"][0]["error"]


def test_projection_rejects_pilot_larger_than_declared_corpus() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 5 * GIB},
            "capacity": _capacity(),
            "pilot": _pilot(),
        }
    )
    assert result["status"] == "BLOCKED"
    assert result["issues"] == [{"type": "PILOT_RAW_BYTES_EXCEED_CORPUS_RAW_BYTES"}]
