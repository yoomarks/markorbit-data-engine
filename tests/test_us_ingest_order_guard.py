import pytest

from app.us.ingest import _assert_monotonic_package_order, _later_successful_packages


def _package(
    package_id: str,
    file_name: str,
    source_rank: int,
    status: str,
) -> dict[str, object]:
    return {
        "package_id": package_id,
        "file_name": file_name,
        "source_rank": source_rank,
        "status": status,
    }


def test_later_successful_package_blocks_reverse_order_ingest() -> None:
    historical = _package(
        "00000000-0000-0000-0000-000000000006",
        "apc18840407-20251231-06.zip",
        1_020_251_231_006_078,
        "REGISTERED",
    )
    daily = _package(
        "00000000-0000-0000-0000-000000000109",
        "apc260109.zip",
        3_020_260_109_000_091,
        "SUCCESS",
    )

    assert _later_successful_packages(historical, [historical, daily]) == [daily]
    with pytest.raises(RuntimeError, match="Out-of-order US Application ingestion blocked"):
        _assert_monotonic_package_order(historical, [historical, daily])


def test_later_non_success_package_does_not_block_ordered_ingest() -> None:
    historical = _package("history", "history.zip", 100, "REGISTERED")
    later_registered = _package("daily", "daily.zip", 200, "REGISTERED")

    assert _later_successful_packages(historical, [historical, later_registered]) == []
    _assert_monotonic_package_order(historical, [historical, later_registered])


def test_same_package_success_row_is_not_its_own_order_blocker() -> None:
    current = _package("same", "apc260109.zip", 200, "SUCCESS")

    assert _later_successful_packages(current, [current]) == []
    _assert_monotonic_package_order(current, [current])
