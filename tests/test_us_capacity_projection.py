from app.us_capacity_projection import evaluate_projection


GIB = 1024**3


def _capacity() -> dict[str, int]:
    return {
        "hot_free_bytes": 1200 * GIB,
        "hot_total_bytes": 1900 * GIB,
        "cold_free_bytes": 2700 * GIB,
        "cold_required_free_bytes": 300 * GIB,
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


def test_measured_pilot_can_authorize_when_hot_and_cold_budgets_fit() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "policy": {"hot_floor_percent": 30},
            "pilot": {
                "receipt_identity": "pilot:001",
                "raw_bytes": 10 * GIB,
                "hot_bytes": 8 * GIB,
                "rows": 1_000_000,
            },
        }
    )
    assert result["status"] == "GO"
    assert result["full_corpus_import_authorized"] is True
    assert result["projection"]["projected_hot_bytes"] == 400 * GIB
    assert result["projection"]["projected_rows"] == 50_000_000
    assert result["projection"]["estimated_without_pilot"] is False
    assert result["capacity"]["hot_budget_bytes"] == 630 * GIB


def test_projection_blocks_when_measured_hot_amplification_exceeds_floor_budget() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "pilot": {
                "receipt_identity": "pilot:002",
                "raw_bytes": 10 * GIB,
                "hot_bytes": 15 * GIB,
                "rows": 1_000_000,
            },
        }
    )
    assert result["status"] == "NO_GO"
    assert result["full_corpus_import_authorized"] is False
    assert result["projection"]["projected_hot_bytes"] == 750 * GIB
    assert result["issues"][0]["type"] == "PROJECTED_HOT_EXCEEDS_BUDGET"


def test_projection_blocks_when_raw_corpus_does_not_fit_cold_budget() -> None:
    capacity = _capacity()
    capacity["cold_free_bytes"] = 450 * GIB
    capacity["cold_required_free_bytes"] = 100 * GIB
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": capacity,
            "pilot": {
                "receipt_identity": "pilot:003",
                "raw_bytes": 10 * GIB,
                "hot_bytes": 2 * GIB,
                "rows": 1_000_000,
            },
        }
    )
    assert result["status"] == "NO_GO"
    assert result["projection"]["cold_safe"] is False
    assert any(issue["type"] == "RAW_CORPUS_EXCEEDS_COLD_BUDGET" for issue in result["issues"])


def test_projection_rejects_invalid_or_unbound_pilot_inputs() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 500 * GIB},
            "capacity": _capacity(),
            "pilot": {"raw_bytes": 10 * GIB, "hot_bytes": 8 * GIB, "rows": 1_000_000},
        }
    )
    assert result["status"] == "BLOCKED"
    assert result["safe"] is False
    assert result["issues"][0]["type"] == "PILOT_INVALID"


def test_projection_rejects_pilot_larger_than_declared_corpus() -> None:
    result = evaluate_projection(
        {
            "corpus": {"source_identity": "odp-manifest:abc", "raw_bytes": 5 * GIB},
            "capacity": _capacity(),
            "pilot": {
                "receipt_identity": "pilot:004",
                "raw_bytes": 10 * GIB,
                "hot_bytes": 8 * GIB,
                "rows": 1_000_000,
            },
        }
    )
    assert result["status"] == "BLOCKED"
    assert result["issues"] == [{"type": "PILOT_RAW_BYTES_EXCEED_CORPUS_RAW_BYTES"}]
