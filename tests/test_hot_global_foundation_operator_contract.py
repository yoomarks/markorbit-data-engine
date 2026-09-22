from pathlib import Path

PREPARE = Path("scripts/prepare-hot-global-foundation.ps1")
APPLY = Path("scripts/apply-hot-global-foundation.ps1")


def _prepare() -> str:
    return PREPARE.read_text(encoding="utf-8")


def _apply() -> str:
    return APPLY.read_text(encoding="utf-8")


def test_prepare_is_exact_main_read_only_and_capacity_bound() -> None:
    text = _prepare()
    for marker in (
        "Assert-ExactMain",
        "git rev-parse HEAD",
        "git rev-parse origin/main",
        "git status --porcelain=v1",
        "Assert-CapacityPlan",
        "ExpectedCapacityPlanSha256",
        "HOT_GLOBAL_INITIAL_CAPACITY_PLAN_V1",
        "HOT_GLOBAL_FOUNDATION_APPLY_PLAN_V1",
    ):
        assert marker in text
    assert "create vdisk" not in text.lower()
    assert "@('--mount','--vhd'" not in text
    assert "Invoke-TargetSql 'SYSTEM RELOAD CONFIG'" not in text


def test_prepare_freezes_exact_hot_global_identity_and_proposed_config() -> None:
    text = _prepare()
    for marker in (
        r"E:\MarkOrbitData\production\clickhouse\hot_global.vhdx",
        "17179869184",
        "16384",
        "mo_hot_global_prod",
        "markorbit_prod_hot_global",
        "/mnt/wsl/markorbit_prod_hot_global/clickhouse-data/",
        "hot_global_only",
        "target-config-before.xml",
        "target-config-proposed.xml",
        "before_sha256",
        "proposed_sha256",
    ):
        assert marker in text
    assert "hot_global disk already exists." in text
    assert "hot_global_only policy already exists." in text


def test_apply_requires_exact_plan_and_exact_go_token() -> None:
    text = _apply()
    for marker in (
        "ExpectedApplyPlanSha256",
        "Read-ApplyPlan",
        "GO #823 HOT-GLOBAL",
        "PROVISION-RELOAD-VERIFY",
        "AuthorityToken does not exactly match the frozen apply-plan SHA.",
        "HOT_GLOBAL_FOUNDATION_APPLY_PLAN_V1",
    ):
        assert marker in text


def test_apply_is_fail_closed_for_partial_state_and_idempotent_for_accepted() -> None:
    text = _apply()
    assert "'ACCEPTED'" in text
    assert "'ABSENT'" in text
    assert "'PARTIAL'" in text
    assert "hot_global is in partial state; refusing automatic mutation." in text
    assert "ALREADY_ACCEPTED_NOOP" in text
    assert "active_part_count" in text


def test_apply_provisions_only_hot_global_with_exact_vhdx_and_ext4() -> None:
    text = _apply()
    for marker in (
        'create vdisk file="{0}" maximum={1} type=expandable',
        "'--mount','--vhd',$script:VhdxPath,'--bare'",
        "'mkfs.ext4','-F','-L',$script:Ext4Label",
        "'--unmount',$script:VhdxPath",
        "'--mount','--vhd',$script:VhdxPath,'--name',$script:MountName",
        "New hot_global block device size mismatch",
        "chmod 0770",
        "hot_global clickhouse-data directory is not freshly empty.",
    ):
        assert marker in text
    lowered = text.lower()
    assert "wsl.exe' @('--shutdown" not in lowered
    assert "--unregister" not in lowered
    assert "docker " not in lowered
    assert "resize-vhd" not in lowered


def test_apply_uses_hot_reload_and_has_config_rollback() -> None:
    text = _apply()
    assert "SYSTEM RELOAD CONFIG" in text
    assert "config_rollback_attempted" in text
    assert "config_rollback_succeeded" in text
    assert "target_restart_performed=$false" in text
    assert "clickhouse_restart_performed=$false" in text
    assert "hot_global disk/policy did not appear after config reload." in text


def test_apply_preserves_accepted_baseline_and_forbids_data_work() -> None:
    text = _apply()
    for marker in (
        "Accepted hot_us disk baseline drifted.",
        "Accepted warm_cn disk baseline drifted.",
        "Accepted hot_us_only policy drifted.",
        "Accepted warm_cn_only policy drifted.",
        "hot_us_or_warm_cn_mutation_performed=$false",
        "table_schema_or_data_mutation_performed=$false",
        "data_copy_or_replay_performed=$false",
        "serving_cutover_performed=$false",
    ):
        assert marker in text
    assert "INSERT INTO" not in text
    assert "ALTER TABLE" not in text
    assert "CREATE TABLE" not in text


def test_both_scripts_have_windows_powershell_contract_mode() -> None:
    assert "HOT_GLOBAL_FOUNDATION_PREPARE_CONTRACT_PASS" in _prepare()
    assert "HOT_GLOBAL_FOUNDATION_APPLY_CONTRACT_PASS" in _apply()
