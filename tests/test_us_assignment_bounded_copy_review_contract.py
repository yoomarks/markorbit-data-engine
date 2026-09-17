from pathlib import Path

SCRIPT = Path("scripts/review-us-assignment-bounded-copy.ps1")


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_review_is_non_mutating_and_assignment_only() -> None:
    source = text()
    for marker in (
        "review_only=True",
        "copy_authorized=False",
        "mutation_performed=False",
        "ttab_copy_authorized=False",
        "serving_cutover_authorized=False",
        "source_cleanup_authorized=False",
    ):
        assert marker in source
    assert "BOUNDED_ASSIGNMENT_TABLE_COPY_EXECUTOR_IMPLEMENTATION" in source


def test_checksum_contract_is_null_safe_v2() -> None:
    source = text()
    assert "NULL_SAFE_JSON_TUPLE_CITYHASH64_V2" in source
    assert "cityHash64(toJSONString(tuple(*)))" in source
    assert "cityHash64(tuple(*))" not in source.split("function Get-LogicalChecksum", 1)[1].split("function Get-TargetBaseline", 1)[0]


def test_assignment_copy_order_is_frozen() -> None:
    source = text()
    expected = [
        "us_assignment_record_history",
        "us_assignment_assignor_history",
        "us_assignment_assignee_history",
        "us_assignment_property_history",
    ]
    positions = [source.index(name) for name in expected]
    assert positions == sorted(positions)


def test_future_copy_primitive_is_plan_only() -> None:
    source = text()
    assert "TARGET_WSL_DUAL_CLICKHOUSE_CLIENT_NATIVE_PIPE_V1" in source
    assert "copy_primitive_execution_performed=$false" in source
    assert "future_copy_authorized=$false" in source
    # Review code may describe future INSERT FORMAT Native, but must not invoke a copy executor.
    assert "Invoke-AssignmentCopy" not in source
    assert "-Apply" not in source


def test_secret_values_are_not_persisted_or_printed() -> None:
    source = text().lower()
    assert "credential_values_persisted=false" in source
    assert "write-host $password" not in source
    assert "password=$password" not in source


def test_ps51_ordered_plan_totals_do_not_use_measure_object_properties() -> None:
    source = text()
    assert "function Get-AssignmentPlanTotals" in source
    assert "foreach($plan in @($Plans))" in source
    assert "$plan.source_rows" in source
    assert "$plan.source_bytes" in source
    assert "Measure-Object source_rows" not in source
    assert "Measure-Object source_bytes" not in source
    assert "PS5 ordered-plan aggregation contract failed." in source
