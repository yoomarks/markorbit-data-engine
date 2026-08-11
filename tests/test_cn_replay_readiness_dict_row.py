from app.cn import replay_readiness as core
from app.cn import replay_readiness_cli as compat_cli


def test_status_counts_accepts_psycopg_dict_rows():
    rows = [
        {"status": "SUCCESS", "count": 84},
        {"status": "REGISTERED", "count": 1},
    ]

    assert core._status_counts(rows) == {"SUCCESS": 84, "REGISTERED": 1}


def test_status_counts_keeps_tuple_compatibility():
    assert core._status_counts([("SUCCESS", 84), ("REGISTERED", 1)]) == {
        "SUCCESS": 84,
        "REGISTERED": 1,
    }


def test_row_to_dict_preserves_psycopg_dict_values():
    row = {
        "package_id": "pkg-1",
        "file_name": "2019_2.zip",
        "package_kind": "MONTHLY_PATCH",
        "partition_value": "2019-02",
        "source_rank": 123,
        "status": "REGISTERED",
    }
    columns = (
        "package_id",
        "file_name",
        "package_kind",
        "partition_value",
        "source_rank",
        "status",
    )

    assert core._row_to_dict(row, columns) == row


def test_row_to_dict_keeps_tuple_compatibility():
    columns = ("package_id", "file_name", "status")
    assert core._row_to_dict(("pkg-1", "2019_2.zip", "REGISTERED"), columns) == {
        "package_id": "pkg-1",
        "file_name": "2019_2.zip",
        "status": "REGISTERED",
    }


def test_compatibility_cli_uses_core_implementation():
    assert compat_cli.build_readiness is core.build_readiness
    assert compat_cli.main is core.main
