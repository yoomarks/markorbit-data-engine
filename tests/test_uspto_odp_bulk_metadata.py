from app.uspto_odp_bulk_metadata import evaluate_metadata


def test_assignment_uses_explicit_odp_date_without_filename_inference() -> None:
    result = evaluate_metadata(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [{"fileName": "asb260809.zip", "fileDate": "2026-08-09"}],
        },
        expected_file_names=["asb260809.zip"],
    )
    assert result["status"] == "READY"
    assert result["plan"] == [
        {
            "file_name": "asb260809.zip",
            "effective_date": "2026-08-09",
            "metadata_field": "fileDate",
        }
    ]
    assert result["effective_date_inferred_from_filename"] is False


def test_assignment_does_not_parse_date_from_filename_when_metadata_date_missing() -> None:
    result = evaluate_metadata(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [{"fileName": "asb260809.zip"}],
        },
        expected_file_names=["asb260809.zip"],
    )
    assert result["status"] == "NOT_READY"
    assert result["issues"] == [
        {"type": "AUTHORITATIVE_EFFECTIVE_DATE_MISSING", "file_name": "asb260809.zip"}
    ]


def test_ttab_date_only_metadata_is_not_promoted_to_midnight_timestamp() -> None:
    result = evaluate_metadata(
        domain="ttab",
        metadata={
            "productIdentifier": "ttabtdxf",
            "files": [{"fileName": "tt260809.zip", "fileDate": "2026-08-09"}],
        },
        expected_file_names=["tt260809.zip"],
    )
    assert result["status"] == "NOT_READY"
    assert result["issues"] == [
        {"type": "AUTHORITATIVE_TIMESTAMP_PRECISION_MISSING", "file_name": "tt260809.zip"}
    ]
    assert result["snapshot_at_inferred_from_filename"] is False
    assert result["timestamp_midnight_manufactured_from_date"] is False


def test_ttab_accepts_explicit_timezone_aware_authoritative_timestamp() -> None:
    result = evaluate_metadata(
        domain="ttab",
        metadata={
            "productIdentifier": "ttabtdxf",
            "files": [
                {
                    "fileName": "tt260809.zip",
                    "releaseDateTime": "2026-08-09T14:22:31-04:00",
                }
            ],
        },
        expected_file_names=["tt260809.zip"],
    )
    assert result["status"] == "READY"
    assert result["plan"][0]["snapshot_at"] == "2026-08-09T14:22:31.000-04:00"
    assert result["plan"][0]["metadata_field"] == "releaseDateTime"


def test_ttab_rejects_naive_timestamp() -> None:
    result = evaluate_metadata(
        domain="ttab",
        metadata={
            "productIdentifier": "ttabtdxf",
            "files": [
                {"fileName": "tt260809.zip", "releaseDateTime": "2026-08-09T14:22:31"}
            ],
        },
        expected_file_names=["tt260809.zip"],
    )
    assert result["status"] == "NOT_READY"
    assert result["issues"][0]["type"] == "AUTHORITATIVE_TIMESTAMP_NOT_TIMEZONE_AWARE"


def test_metadata_preflight_fails_closed_on_product_mismatch() -> None:
    result = evaluate_metadata(
        domain="assignment",
        metadata={
            "productIdentifier": "ttabtdxf",
            "files": [{"fileName": "asb260809.zip", "fileDate": "2026-08-09"}],
        },
        expected_file_names=["asb260809.zip"],
    )
    assert result["status"] == "NOT_READY"
    assert result["issues"][0]["type"] == "ODP_PRODUCT_IDENTIFIER_MISMATCH"


def test_metadata_preflight_rejects_ambiguous_duplicate_file_records() -> None:
    result = evaluate_metadata(
        domain="assignment",
        metadata={
            "productIdentifier": "trtdxfag",
            "files": [
                {"fileName": "asb260809.zip", "fileDate": "2026-08-09"},
                {"fileName": "asb260809.zip", "fileDate": "2026-08-10"},
            ],
        },
        expected_file_names=["asb260809.zip"],
    )
    assert result["status"] == "NOT_READY"
    assert result["issues"][0] == {
        "type": "ODP_FILE_METADATA_AMBIGUOUS",
        "file_name": "asb260809.zip",
        "match_count": 2,
    }
