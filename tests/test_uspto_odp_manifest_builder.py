from app.uspto_odp_manifest_builder import build_manifest


def test_assignment_manifest_is_built_from_explicit_kind_and_authoritative_dates() -> None:
    result = build_manifest(
        domain="assignment",
        metadata={
            "productIdentifier": "EIP-5903T-OL",
            "files": [
                {"fileName": "snapshot.zip", "fileDate": "2026-08-01"},
                {"fileName": "daily-a.zip", "fileDate": "2026-08-08"},
                {"fileName": "daily-b.zip", "fileDate": "2026-08-09"},
            ],
        },
        source_specs=[
            {
                "path": "incoming/us_assignment/snapshot.zip",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
            },
            {
                "path": "incoming/us_assignment/daily-b.zip",
                "source_kind": "DAILY_ASSIGNMENT_XML",
            },
            {
                "path": "incoming/us_assignment/daily-a.zip",
                "source_kind": "DAILY_ASSIGNMENT_XML",
            },
        ],
    )
    assert result["status"] == "READY"
    assert result["source_kind_inferred_from_filename"] is False
    assert result["source_time_inferred_from_filename"] is False
    manifest = result["manifest"]
    assert manifest["manifest_version"] == "US_ASSIGNMENT_CORPUS_MANIFEST_V1"
    assert manifest["expected_snapshot_packages"] == 1
    assert manifest["expected_daily_packages"] == 2
    assert manifest["daily_through"] == "2026-08-09"
    assert [row["effective_date"] for row in manifest["sources"]] == [
        "2026-08-01",
        "2026-08-08",
        "2026-08-09",
    ]


def test_assignment_manifest_rejects_duplicate_effective_date() -> None:
    result = build_manifest(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [
                {"fileName": "snapshot.zip", "fileDate": "2026-08-01"},
                {"fileName": "daily.zip", "fileDate": "2026-08-01"},
            ],
        },
        source_specs=[
            {
                "path": "incoming/us_assignment/snapshot.zip",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
            },
            {
                "path": "incoming/us_assignment/daily.zip",
                "source_kind": "DAILY_ASSIGNMENT_XML",
            },
        ],
    )
    assert result["status"] == "NOT_READY"
    assert result["manifest"] is None
    assert any(row["type"] == "DUPLICATE_EFFECTIVE_DATE_NOT_MODELED" for row in result["issues"])


def test_assignment_manifest_requires_exactly_one_explicit_historical_source() -> None:
    result = build_manifest(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [{"fileName": "daily.zip", "fileDate": "2026-08-09"}],
        },
        source_specs=[
            {
                "path": "incoming/us_assignment/daily.zip",
                "source_kind": "DAILY_ASSIGNMENT_XML",
            }
        ],
    )
    assert result["status"] == "NOT_READY"
    assert any(row["type"] == "HISTORICAL_SOURCE_COUNT_MISMATCH" for row in result["issues"])


def test_ttab_manifest_normalizes_explicit_timestamps_to_utc() -> None:
    result = build_manifest(
        domain="ttab",
        metadata={
            "productIdentifier": "EIP-5904T-OL",
            "files": [
                {
                    "fileName": "historical.zip",
                    "releaseDateTime": "2026-08-01T10:00:00-04:00",
                },
                {
                    "fileName": "daily.zip",
                    "releaseDateTime": "2026-08-09T20:15:30-04:00",
                },
            ],
        },
        source_specs=[
            {
                "path": "incoming/us_ttab/historical.zip",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
            },
            {
                "path": "incoming/us_ttab/daily.zip",
                "source_kind": "TTAB_BULK_DAILY_XML",
            },
        ],
    )
    assert result["status"] == "READY"
    manifest = result["manifest"]
    assert manifest["manifest_version"] == "US_TTAB_CORPUS_MANIFEST_V1"
    assert manifest["expected_historical_packages"] == 1
    assert manifest["expected_daily_packages"] == 1
    assert manifest["daily_through"] == "2026-08-10"
    assert manifest["sources"][0]["snapshot_at"] == "2026-08-01T14:00:00.000Z"
    assert manifest["sources"][1]["snapshot_at"] == "2026-08-10T00:15:30.000Z"


def test_ttab_manifest_remains_not_ready_for_date_only_metadata() -> None:
    result = build_manifest(
        domain="ttab",
        metadata={
            "productIdentifier": "ttabtdxf",
            "files": [
                {"fileName": "historical.zip", "fileDate": "2026-08-01"},
            ],
        },
        source_specs=[
            {
                "path": "incoming/us_ttab/historical.zip",
                "source_kind": "TTAB_BULK_HISTORICAL_XML",
            }
        ],
    )
    assert result["status"] == "NOT_READY"
    assert result["manifest"] is None
    assert any(row["type"] == "AUTHORITATIVE_TIMESTAMP_PRECISION_MISSING" for row in result["issues"])


def test_manifest_builder_rejects_source_path_outside_domain_raw_tree() -> None:
    result = build_manifest(
        domain="assignment",
        metadata={"productIdentifier": "trtdxfag", "files": []},
        source_specs=[
            {
                "path": "../assignment.zip",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
            }
        ],
    )
    assert result["status"] == "NOT_READY"
    assert any(row["type"] == "SOURCE_PATH_INVALID" for row in result["issues"])


def test_manifest_builder_rejects_duplicate_basename_identity() -> None:
    result = build_manifest(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [{"fileName": "same.zip", "fileDate": "2026-08-01"}],
        },
        source_specs=[
            {
                "path": "incoming/us_assignment/same.zip",
                "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
            },
            {
                "path": "archive/us_assignment/same.zip",
                "source_kind": "DAILY_ASSIGNMENT_XML",
            },
        ],
    )
    assert result["status"] == "NOT_READY"
    assert any(row["type"] == "DUPLICATE_SOURCE_BASENAME" for row in result["issues"])
