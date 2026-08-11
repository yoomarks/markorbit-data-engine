from app.cn.audit_acceptance import apply_package_registry_gate, evaluate_acceptance


def _base_audit() -> dict:
    return {
        "status": "FAIL",
        "package_contract": {
            "total_parsed_to_stage_dropped": 2,
            "total_failed_rows": 0,
            "total_replacement_chars": 3,
        },
        "quality": {
            "occurrences_by_type": {"INVALID_TEXT_BYTES_REPLACED": 3},
            "unmapped_goods_status_codes": {},
        },
        "clickhouse": {
            "replacement_character_rows": {
                "case_mark_name": 0,
                "case_design_description": 0,
                "case_color_description": 0,
                "case_disclaimer": 0,
                "party_name": 0,
                "party_address": 0,
                "goods_scope": 0,
            },
            "duplicates_after_final": {"cases": 0, "scopes": 0, "current_parties": 0},
            "orphans": {"scope_without_case": 0, "party_without_case": 1},
        },
        "deep_raw_scan": {
            "packages": [
                {
                    "file_name": "fixture.zip",
                    "party_drop_reasons": {"applicant:EMPTY_PARTY_NAME": 1},
                    "rows_with_replacement_after_parse": 0,
                }
            ]
        },
    }


def _source_backed_orphan() -> dict:
    return {
        "application_number": "G123456A",
        "package_file": "fixture.zip",
        "unregistered_source_package": False,
        "source_file": "商标注册人信息.csv",
        "source_first_line": 10,
    }


def _passing_integrity_result() -> dict:
    return evaluate_acceptance(
        _base_audit(),
        [_source_backed_orphan()],
        [{"file_name": "fixture.zip", "expected_drop": 1, "explained_drop": 1}],
    )


def test_source_backed_incomplete_records_are_warnings_not_failures() -> None:
    result = _passing_integrity_result()
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["hard_fail_reasons"] == []
    assert result["reconciliation"]["unexplained_dropped"] == 0
    assert result["orphan_policy"]["source_backed_party_without_case"] == 1


def test_untraceable_party_orphan_is_hard_failure() -> None:
    orphan = _source_backed_orphan()
    orphan["unregistered_source_package"] = True
    orphan["package_file"] = None
    result = evaluate_acceptance(
        _base_audit(),
        [orphan],
        [{"file_name": "fixture.zip", "expected_drop": 1, "explained_drop": 1}],
    )
    assert result["status"] == "FAIL"
    assert "untraceable_party_without_case" in result["hard_fail_reasons"]


def test_unreconciled_parse_to_stage_drop_is_hard_failure() -> None:
    result = evaluate_acceptance(
        _base_audit(),
        [_source_backed_orphan()],
        [{"file_name": "fixture.zip", "expected_drop": 0, "explained_drop": 0}],
    )
    assert result["status"] == "FAIL"
    assert "unreconciled_parse_to_stage_drops" in result["hard_fail_reasons"]


def test_final_replacement_character_is_hard_failure() -> None:
    audit = _base_audit()
    audit["clickhouse"]["replacement_character_rows"]["party_name"] = 1
    result = evaluate_acceptance(
        audit,
        [_source_backed_orphan()],
        [{"file_name": "fixture.zip", "expected_drop": 1, "explained_drop": 1}],
    )
    assert result["status"] == "FAIL"
    assert "replacement_characters_in_final_tables" in result["hard_fail_reasons"]


def test_cn_corpus_acceptance_requires_registered_packages() -> None:
    result = apply_package_registry_gate(_passing_integrity_result(), [])
    assert result["status"] == "NOT_READY"
    assert result["not_ready_reasons"] == ["no_cn_packages_registered"]
    assert result["package_registry"]["all_success"] is False


def test_cn_corpus_acceptance_requires_all_packages_successful() -> None:
    packages = [
        {"file_name": "2023_4.zip", "status": "SUCCESS", "source_rank": 1},
        {"file_name": "2023_5.zip", "status": "REGISTERED", "source_rank": 2},
    ]
    result = apply_package_registry_gate(_passing_integrity_result(), packages)
    assert result["status"] == "NOT_READY"
    assert result["not_ready_reasons"] == ["cn_corpus_replay_not_complete"]
    assert result["package_registry"]["pending_count"] == 1


def test_cn_corpus_acceptance_failed_package_is_hard_failure() -> None:
    packages = [
        {"file_name": "2023_4.zip", "status": "FAILED", "source_rank": 1, "error_message": "boom"},
    ]
    result = apply_package_registry_gate(_passing_integrity_result(), packages)
    assert result["status"] == "FAIL"
    assert "cn_package_failure_or_interruption_present" in result["hard_fail_reasons"]
    assert result["package_registry"]["failed_or_interrupted_count"] == 1


def test_cn_corpus_acceptance_preserves_integrity_warnings_when_all_success() -> None:
    packages = [{"file_name": "2023_4.zip", "status": "SUCCESS", "source_rank": 1}]
    result = apply_package_registry_gate(_passing_integrity_result(), packages)
    assert result["status"] == "PASS_WITH_WARNINGS"
    assert result["package_registry"]["all_success"] is True
