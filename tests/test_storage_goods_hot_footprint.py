from pathlib import Path

from app.storage_goods_hot_footprint import (
    COLUMNS_SQL,
    METADATA_QUERIES,
    TABLE_SQL,
    _assert_metadata_only_queries,
    detect_select_star_goods_contract,
    evaluate_goods_hot_footprint,
)


def _column(
    name: str,
    *,
    position: int,
    compressed: int,
    uncompressed: int,
    primary: bool = False,
    sorting: bool = False,
) -> dict[str, object]:
    return {
        "name": name,
        "type": "String",
        "position": position,
        "default_kind": "",
        "default_expression": "",
        "data_compressed_bytes": compressed,
        "data_uncompressed_bytes": uncompressed,
        "marks_bytes": 10,
        "is_in_partition_key": False,
        "is_in_sorting_key": sorting,
        "is_in_primary_key": primary,
        "is_in_sampling_key": False,
    }


def test_metadata_queries_are_select_only_and_system_scoped() -> None:
    _assert_metadata_only_queries()
    assert "FROM system.columns" in COLUMNS_SQL
    assert "FROM system.tables" in TABLE_SQL
    for sql in METADATA_QUERIES:
        upper = f" {' '.join(sql.upper().split())} "
        assert " FINAL " not in upper
        assert " OPTIMIZE " not in upper
        assert " ALTER " not in upper
        assert " INSERT " not in upper
        assert " DELETE " not in upper


def test_profile_reports_column_shares_and_blocks_removal() -> None:
    report = evaluate_goods_hot_footprint(
        columns=[
            _column(
                "application_number",
                position=1,
                compressed=100,
                uncompressed=500,
                primary=True,
                sorting=True,
            ),
            _column("goods_name", position=2, compressed=300, uncompressed=1200),
            _column("first_source_package_id", position=3, compressed=100, uncompressed=200),
        ],
        table_metadata={
            "engine": "ReplacingMergeTree",
            "sorting_key": "application_number",
            "primary_key": "application_number",
            "partition_key": "",
        },
        api_contract={"status": "SELECT_STAR_ALL_COLUMNS_EXPOSED", "select_star": True},
    )

    assert report["status"] == "PASS"
    assert report["totals"]["data_compressed_bytes"] == 500
    assert report["totals"]["data_uncompressed_bytes"] == 1900
    assert report["largest_columns"][0]["name"] == "goods_name"
    assert report["largest_columns"][0]["compressed_share"] == 0.6
    assert report["compatibility_preserving_removable_bytes"] == 0
    assert report["migration_authorized"] is False
    assert all(
        row["api_exposed_under_current_contract"] is True
        and row["removal_allowed_under_current_contract"] is False
        for row in report["columns"]
    )


def test_profile_classifies_key_and_provenance_roles() -> None:
    report = evaluate_goods_hot_footprint(
        columns=[
            _column(
                "application_number",
                position=1,
                compressed=10,
                uncompressed=20,
                primary=True,
            ),
            _column(
                "first_source_package_id",
                position=2,
                compressed=10,
                uncompressed=20,
            ),
        ],
        table_metadata={"engine": "MergeTree"},
        api_contract={"select_star": True},
    )
    by_name = {row["name"]: row for row in report["columns"]}
    assert by_name["application_number"]["role"] == "key"
    assert by_name["first_source_package_id"]["role"] == "provenance"


def test_missing_metadata_fails_closed() -> None:
    report = evaluate_goods_hot_footprint(
        columns=[],
        table_metadata=None,
        api_contract={"status": "SELECT_STAR_ALL_COLUMNS_EXPOSED", "select_star": True},
    )
    assert report["status"] == "REVIEW_REQUIRED"
    assert "DEPLOYED_COLUMN_METADATA_MISSING" in report["reason_codes"]
    assert "DEPLOYED_TABLE_METADATA_MISSING" in report["reason_codes"]
    assert report["migration_authorized"] is False


def test_unconfirmed_api_contract_fails_closed() -> None:
    report = evaluate_goods_hot_footprint(
        columns=[_column("goods_name", position=1, compressed=1, uncompressed=2)],
        table_metadata={"engine": "MergeTree"},
        api_contract={"status": "REVIEW_REQUIRED", "select_star": False},
    )
    assert report["status"] == "REVIEW_REQUIRED"
    assert "CURRENT_API_SELECT_STAR_CONTRACT_NOT_CONFIRMED" in report["reason_codes"]
    assert report["migration_authorized"] is False


def test_select_star_detector_matches_current_case_api(tmp_path: Path) -> None:
    source = tmp_path / "app" / "main_core.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''query = """\nSELECT *\nFROM markorbit_facts.cn_goods_item_current FINAL\nWHERE application_number = %(application_number)s\n"""\n''',
        encoding="utf-8",
    )
    contract = detect_select_star_goods_contract(tmp_path)
    assert contract["select_star"] is True
    assert contract["status"] == "SELECT_STAR_ALL_COLUMNS_EXPOSED"


def test_repository_current_case_api_still_exposes_all_goods_columns() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    contract = detect_select_star_goods_contract(repo_root)
    assert contract["select_star"] is True
    assert contract["status"] == "SELECT_STAR_ALL_COLUMNS_EXPOSED"
