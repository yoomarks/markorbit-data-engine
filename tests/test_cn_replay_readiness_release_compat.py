from app.cn.replay_readiness import SUPPORTED_ENGINE_VERSIONS, evaluate_readiness


def _package_state():
    return {
        "status_counts": {"SUCCESS": 85},
        "registered_package_count": 85,
        "next_pending": None,
        "next_retry": None,
        "latest_success": {"file_name": "2023_5.zip"},
    }


def _storage_state():
    return {
        "missing_m16_schema": [],
        "goods_baseline_history_rows": 0,
        "reconstructible_event_baseline_rows": 0,
        "legacy_party_history_rows": 0,
        "active_stage_rows": 0,
        "storage_v2_shadow_tables": [],
        "pending_mutations": [],
        "active_bytes": 1,
        "active_rows": 1,
        "disks": [],
    }


def test_m17_is_explicitly_supported_for_retained_cn_m16_readiness_contract():
    assert SUPPORTED_ENGINE_VERSIONS == frozenset({"M1.6", "M1.7"})
    report = evaluate_readiness(
        package_state=_package_state(),
        storage_state=_storage_state(),
        persistent_worker_running=False,
        current_engine_version="M1.7",
    )

    assert report["status"] == "COMPLETE"
    assert report["hard_issues"] == []


def test_future_engine_version_remains_fail_closed_until_reviewed():
    report = evaluate_readiness(
        package_state=_package_state(),
        storage_state=_storage_state(),
        persistent_worker_running=False,
        current_engine_version="M1.8",
    )

    assert report["status"] == "BLOCKED"
    assert report["hard_issues"] == [
        {
            "code": "UNEXPECTED_ENGINE_VERSION",
            "engine_version": "M1.8",
            "supported_engine_versions": ["M1.6", "M1.7"],
        }
    ]
