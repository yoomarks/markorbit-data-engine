from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "run-us-capacity-pilot-target-host.ps1"
PILOT = ROOT / "scripts" / "run-us-capacity-pilot.ps1"

EXPECTED_FILE = "apc18840407-20251231-01.zip"
EXPECTED_SHA = "9b65bdcb80c2bdd6efa6869432771c30613bed6dc8efd3d4589e2fd8b334b062"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_no_all_switch(text: str) -> None:
    assert re.search(r"(?i)(?<![A-Za-z0-9_-])-All(?![A-Za-z0-9_-])", text) is None


def test_target_host_operator_freezes_exact_main_and_exact_package() -> None:
    text = _text(TARGET)

    assert "[string]$ExpectedMainSha" in text
    assert "git fetch origin main" in text
    assert "origin/main" in text
    assert 'Assert-ExactMain "entry"' in text
    assert 'Assert-ExactMain "pre-package-mutation"' in text
    assert "EXACT_MAIN_BOUNDED_US_PILOT_OK" in text

    assert "ExpectedHistoryParts = 91" in text
    assert "ExpectedSequence = 1" in text
    assert f'ExpectedFileName = "{EXPECTED_FILE}"' in text
    assert f'ExpectedPackageSha256 = "{EXPECTED_SHA}"' in text
    assert "ExpectedRemainingBefore = 310" in text
    assert "ExpectedRemainingAfter = 309" in text
    assert "US_TARGET_HOST_EXACT_ONE_PACKAGE_PILOT_PASS" in text


def test_target_host_operator_orders_hot_schema_transition_and_pilot() -> None:
    text = _text(TARGET)

    pre_hot = text.index("===== ACTIVE HOT PRE-SCHEMA GATE =====")
    schema = text.index("===== APPLY US M1.4 SCHEMA ONLY =====")
    post_hot = text.index("===== ACTIVE HOT POST-SCHEMA GATE =====")
    transition = text.index("===== US APPLICATION TRANSITION READY GATE =====")
    final_gate = text.index("===== FINAL PRE-MUTATION EXACT-MAIN / IDLE GATE =====")
    pilot = text.index("===== EXACTLY ONE FROZEN US CAPACITY PILOT PACKAGE =====")

    assert pre_hot < schema < post_hot < transition < final_gate < pilot
    assert "diagnose-clickhouse-active-hot-permissions-v2.ps1" in text
    assert "apply-us-m1-schema.ps1" in text
    assert "check-us-application-transition.ps1" in text
    assert "run-us-capacity-pilot.ps1" in text
    assert "ACTIVE_HOT_POST_SCHEMA_OK" in text
    assert "US_APPLICATION_TRANSITION_READY_OK" in text
    assert "FINAL_PRE_MUTATION_GATE_OK" in text


def test_target_host_operator_requires_idle_zero_worker_and_healthy_clickhouse() -> None:
    text = _text(TARGET)

    assert "stop-idle-worker.ps1" in text
    assert "docker compose ps -a -q worker" in text
    assert "worker_container_count_all_states=" in text
    assert "function Wait-ClickHouseHealthy" in text
    assert ".State.Health.Status" in text
    assert 'Wait-ClickHouseHealthy "pre-schema"' in text
    assert 'Wait-ClickHouseHealthy "post-schema"' in text
    assert 'Wait-ClickHouseHealthy "pre-package-mutation"' in text
    assert "GLOBAL_IDLE_ZERO_WORKER_CLICKHOUSE_HEALTHY_OK" in text


def test_target_host_hot_gate_requires_zero_blockers_tmp_and_healthy_comparison() -> None:
    text = _text(TARGET)

    assert "@($hot.blockers).Count -ne 0" in text
    assert "hot.schema_version.rwx_for_server_identity" in text
    assert "@($hot.schema_version.tmp_insert_dirs).Count -ne 0" in text
    assert "hot.disposable_root_rename_probe.passed" in text
    assert "hot.cn_comparison.rwx_for_server_identity" in text
    assert "active_hot_blockers=0" in text
    assert "active_hot_tmp_insert_dirs=0" in text


def test_bounded_pilot_pins_identity_before_and_after_apply() -> None:
    text = _text(PILOT)

    assert "ExpectedSequence = 1" in text
    assert f'ExpectedFileName = "{EXPECTED_FILE}"' in text
    assert f'ExpectedPackageSha256 = "{EXPECTED_SHA}"' in text
    assert "ExpectedRemainingBefore = 310" in text
    assert "ExpectedRemainingAfter = 309" in text

    assert "FROZEN_US_PILOT_PACKAGE_IDENTITY_OK" in text
    assert "dryRun.processed_count" in text
    assert "dryRun.remaining_count" in text
    assert "dryRun.next_step.sequence" in text
    assert "dryRun.next_step.file_name" in text
    assert "dryRun.next_step.sha256" in text

    assert "FROZEN_US_PILOT_APPLY_IDENTITY_OK" in text
    assert "replaySummary.apply_one_package_ok" in text
    assert "replaySummary.processed_count" in text
    assert "replaySummary.source_preflight_runs" in text
    assert "replaySummary.first_processed.sequence" in text
    assert "replaySummary.first_processed.file_name" in text
    assert "replaySummary.first_processed.sha256" in text
    assert "replaySummary.remaining_count" in text


def test_target_host_operator_never_expands_to_full_corpus_or_unrelated_mutation() -> None:
    combined = _text(TARGET) + "\n" + _text(PILOT)
    lowered = combined.lower()

    _assert_no_all_switch(combined)
    assert "2023_5.zip" not in lowered
    assert "project-us-full-corpus-capacity.ps1" not in lowered
    assert "docker compose start worker" not in lowered
    assert "docker compose restart worker" not in lowered
    assert "chmod " not in lowered
    assert "chown " not in lowered
    assert "full_corpus_replay_performed=False" in combined
    assert "capacity_projection_performed=False" in combined
